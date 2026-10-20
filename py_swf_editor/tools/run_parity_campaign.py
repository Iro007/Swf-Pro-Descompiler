"""Run three serialized full-profile sessions only after an explicit freeze.

Correctness and performance evidence are independent. CPU/RAM samples and
observed build/test processes produce contamination flags, never fake timings.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import uuid
import threading

PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from py_swf.benchmarks import run_benchmark, implementation_digest, match_units
from py_swf.corpus import read_immutable_manifest


def environment_gate(*, cpu_limit=10, minimum_available_ram=2*1024**3):
    import psutil
    samples=[]
    for _ in range(3):
        samples.append({'cpu_percent':psutil.cpu_percent(interval=1), 'available_ram_bytes':psutil.virtual_memory().available})
    interfering=[]
    for process in psutil.process_iter(['pid','name','cmdline']):
        try:
            if process.pid == __import__('os').getpid(): continue
            argv=process.info.get('cmdline') or [];tokens=' '.join(argv).lower()
            if any(term in tokens for term in (' -m pytest','cargo test','cargo build','rustc.exe','mxmlc.jar','lime build','build_parity_corpus.py')):
                interfering.append({'pid':process.pid,'name':process.info.get('name'),'classification':'build_or_test'})
        except (psutil.NoSuchProcess,psutil.AccessDenied):
            continue
    passed=all(s['cpu_percent']<=cpu_limit and s['available_ram_bytes']>=minimum_available_ram for s in samples) and not interfering
    return {'status':'ready' if passed else 'contaminated','samples':samples,'interfering_processes':interfering,
            'cpu_limit_percent':cpu_limit,'minimum_available_ram_bytes':minimum_available_ram}


class ContaminationMonitor:
    """Measure background CPU excluding the owned parent/worker process tree."""
    def __init__(self, cpu_limit=10, minimum_available_ram=2*1024**3):
        import psutil
        self.psutil=psutil;self.root=psutil.Process();self.cpu_limit=cpu_limit
        self.minimum_ram=minimum_available_ram;self.stop_event=threading.Event();self.samples=[]
        self.thread=threading.Thread(target=self._run,daemon=True)
    def start(self):
        self.thread.start();return self
    def _run(self):
        psutil=self.psutil;psutil.cpu_percent(None)
        previous={}
        try: initial=[self.root,*self.root.children(recursive=True)]
        except psutil.NoSuchProcess: initial=[]
        for process in initial:
            try:
                times=process.cpu_times();previous[(process.pid,process.create_time())]=times.user+times.system
            except (psutil.NoSuchProcess,psutil.AccessDenied):continue
        previous_time=time.monotonic()
        while not self.stop_event.wait(1):
            now=time.monotonic();elapsed=now-previous_time
            try:owned=[self.root,*self.root.children(recursive=True)]
            except psutil.NoSuchProcess:owned=[]
            current={}
            for process in owned:
                try:
                    times=process.cpu_times();current[(process.pid,process.create_time())]=times.user+times.system
                except (psutil.NoSuchProcess,psutil.AccessDenied):continue
            owned_cpu=sum(max(0,value-previous.get(key,0)) for key,value in current.items())/max(.001,elapsed)/max(1,psutil.cpu_count())*100
            cpu=psutil.cpu_percent(None);available=psutil.virtual_memory().available
            sample={'at':datetime.now(timezone.utc).isoformat(),'system_cpu_percent':cpu,'owned_tree_cpu_percent':owned_cpu,
                    'background_cpu_percent':max(0,cpu-owned_cpu),'available_ram_bytes':available}
            sample['contaminated']=sample['background_cpu_percent']>self.cpu_limit or available<self.minimum_ram
            self.samples.append(sample);previous=current;previous_time=now
    def finish(self):
        self.stop_event.set();self.thread.join(timeout=3)
        return {'status':'contaminated' if any(s['contaminated'] for s in self.samples) else 'observed_clean' if self.samples else 'not_observed',
                'sample_interval_seconds':1,'samples':self.samples,
                'limitation':'Short-lived child CPU can escape a one-second process-tree sample; pre/post build/test process gates remain required'}


def run_campaign(corpus, output, freeze_file, *, engines=('native','rust','jpexs','pyflashkit'), tool_config=None, config_path=None):
    corpus,output,freeze_file=Path(corpus),Path(output),Path(freeze_file)
    manifest=read_immutable_manifest(corpus)
    freeze=json.loads(freeze_file.read_text(encoding='utf-8'))
    if freeze.get('agents_quiescent') is not True or freeze.get('builds_and_tests_finished') is not True:
        raise ValueError('Performance requires an explicit completed-build and quiescent-agent freeze record')
    start_digest=implementation_digest()
    if freeze.get('implementation_sha256') != start_digest:
        raise ValueError('Implementation changed since the performance freeze')
    output.mkdir(parents=True,exist_ok=False)
    matrix=[{key:case.get(key) for key in ('relative','sha256','family_id','split','debug','compression')}|{'engine':engine,'status':'not_run','reason':'session not started'} for case in manifest['cases'] for engine in engines]
    campaign={'schema_version':1,'campaign_id':uuid.uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat(),
              'profile':{'warmups':3,'hot_runs':30,'cold_runs':5},'scheduled_sessions':3,'sessions':[],
              'manifest_sha256':(corpus/'manifest.sha256').read_text().strip(),'freeze':freeze,
              'claims':{'performance_winner':None,'parity':False},'status':'started'}
    campaign['sessions']=[{'ordinal':number+1,'status':'not_run','entries':[dict(row) for row in matrix]} for number in range(3)]
    aborted=None
    for number,session in enumerate(campaign['sessions']):
        if aborted:
            session.update(status='not_run',reason=aborted)
            for entry in session['entries']:entry['reason']=aborted
            continue
        gate=environment_gate()
        session['preflight']=gate
        if gate['status']!='ready':
            session.update(status='blocked_by_environment',reason='CPU, RAM or observed build/test gate failed')
            for entry in session['entries']:entry.update(status='blocked_by_environment',reason=session['reason'])
            campaign['status']='environment_not_ready'
            continue
        monitor=ContaminationMonitor().start()
        thin_results={}
        session['pair_reports']=[]
        try:
            for ordinal,entry in enumerate(session['entries']):
                if implementation_digest()!=start_digest:
                    aborted='implementation_changed_after_freeze';session['status']='aborted';campaign['status']='aborted'
                    for remaining in session['entries'][ordinal:]:remaining['reason']=aborted
                    break
                folder=output/('session-%d'%(number+1))/entry['family_id']/(Path(entry['relative']).stem+'-'+entry['engine'])
                try:
                    report=run_benchmark(corpus,profile='full',engines=(entry['engine'],),output=folder,
                                         seed=1729+number,tool_config=tool_config,config_path=config_path,
                                         case_relatives=[entry['relative']])
                    # One engine/variant per full report. Keep every unit and
                    # document; compact formatting avoids redundant whitespace.
                    encoded=json.dumps(report,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
                    report_path=folder/'benchmark.json';report_path.write_bytes(encoded)
                    entry.update(status='completed',report_path=str(report_path.resolve()),report_sha256=__import__('hashlib').sha256(encoded).hexdigest(),report_bytes=len(encoded),reason=None,
                                 import_status='ready' if len(encoded)<=64*1024**2 else 'not_imported_oversize')
                    case=report['cases'][0];engine=case['engines'][0]
                    entry['execution_status']=engine.get('status')
                    entry['profile_complete']=engine.get('status')=='not_applicable' or all(
                        len(engine.get(phase+'_samples',[]))==expected and all(s.get('ok') is True for s in engine.get(phase+'_samples',[]))
                        for phase,expected in (('cold',5),('hot',30),('warmup',3)))
                    thin={**{k:engine.get(k) for k in ('engine','status','inventory_status','adapter_mode')},
                          'units':[{k:u.get(k) for k in ('unit_id','document_id','scope','bytecode_sha256','identity_verified','generation','source_kind','represented_by_document')} for u in engine.get('units',[])]}
                    group=thin_results.setdefault(entry['relative'],{'results':[],'denominators':case['canonical_inventory']['scope_denominators'],'refs':[]})
                    group['results'].append(thin);group['refs'].append({k:entry[k] for k in ('engine','report_path','report_sha256','report_bytes','import_status')})
                    if len(group['results'])==len(engines):
                        pair={'relative':entry['relative'],'sha256':entry['sha256'],'family_id':entry['family_id'],'split':entry['split'],
                              'comparison':match_units(group['results'],{'scope_denominators':group['denominators']}),'reports':group['refs']}
                        path=folder.parent/(Path(entry['relative']).stem+'-pairs.json');path.write_text(json.dumps(pair,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
                        session['pair_reports'].append({'path':str(path.resolve()),'sha256':__import__('hashlib').sha256(path.read_bytes()).hexdigest(),'relative':entry['relative']})
                        del thin_results[entry['relative']]
                    del report,encoded
                except Exception as error:
                    entry.update(status='failed',reason=str(error))
                (output/'campaign.json').write_text(json.dumps(campaign,indent=2),encoding='utf-8')
        finally:
            session['during_run']=monitor.finish()
        session.update(completed_entries=sum(e['status']=='completed' for e in session['entries']),postflight=environment_gate())
        if session.get('status')!='aborted':session['status']='completed' if session['completed_entries']==len(matrix) and all(e.get('profile_complete') for e in session['entries']) else 'partial'
        if session['postflight']['status']!='ready' or session['during_run']['status']!='observed_clean' or implementation_digest()!=start_digest:
            if session.get('status')!='aborted':session['status']='contaminated';campaign['status']='contaminated'
            aborted=aborted or 'environment_or_implementation_contaminated'
        (output/'campaign.json').write_text(json.dumps(campaign,indent=2),encoding='utf-8')
    campaign['completed_sessions']=sum(s['status']=='completed' for s in campaign['sessions'])
    if campaign['completed_sessions']==3:campaign['status']='completed'
    campaign['corpus_complete']=manifest['complete']
    (output/'campaign.json').write_text(json.dumps(campaign,indent=2),encoding='utf-8')
    return campaign


def main():
    p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--freeze-file',type=Path,required=True);p.add_argument('--tool-config',type=Path);p.add_argument('--config-path',type=Path)
    args=p.parse_args();print(json.dumps(run_campaign(args.corpus,args.output,args.freeze_file,tool_config=args.tool_config,config_path=args.config_path),indent=2))


if __name__=='__main__':main()
