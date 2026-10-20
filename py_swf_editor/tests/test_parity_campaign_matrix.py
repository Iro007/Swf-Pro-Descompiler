import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import run_parity_campaign as runner


def prepare(tmp_path,monkeypatch):
    corpus=tmp_path/'corpus';corpus.mkdir();(corpus/'manifest.sha256').write_text('c'*64)
    cases=[{'relative':f'swf/{name}/release.fws.swf','sha256':hashlib.sha256(name.encode()).hexdigest(),'family_id':name,'split':'development','debug':False,'compression':'FWS'} for name in ('alpha','beta')]
    monkeypatch.setattr(runner,'read_immutable_manifest',lambda *a:{'cases':cases,'complete':True})
    monkeypatch.setattr(runner,'implementation_digest',lambda:'frozen')
    freeze=tmp_path/'freeze.json';freeze.write_text(json.dumps({'agents_quiescent':True,'builds_and_tests_finished':True,'implementation_sha256':'frozen'}))
    class Monitor:
        def start(self):return self
        def finish(self):return {'status':'observed_clean','samples':[]}
    monkeypatch.setattr(runner,'ContaminationMonitor',Monitor)
    return corpus,freeze,cases


def fake_report(relative,engine):
    unit={'document_id':'tag:0/method:0','scope':'method','bytecode_sha256':'a'*64,'identity_verified':True,'generation':'available'}
    result={'engine':engine,'status':'measured','units':[unit]}
    for phase,count in (('cold',5),('hot',30),('warmup',3)):result[phase+'_samples']=[{'ok':True} for _ in range(count)]
    return {'schema_version':1,'cases':[{'relative':relative,'canonical_inventory':{'scope_denominators':{'method':1},'units':[]},'engines':[result]}]}


def test_blocked_environment_retains_every_case_engine_in_all_three_sessions(tmp_path,monkeypatch):
    corpus,freeze,cases=prepare(tmp_path,monkeypatch)
    monkeypatch.setattr(runner,'environment_gate',lambda:{'status':'contaminated'})
    monkeypatch.setattr(runner,'run_benchmark',lambda *a,**k:pytest.fail('Ineligible performance launched'))
    report=runner.run_campaign(corpus,tmp_path/'output',freeze,engines=('native','rust'))
    assert len(report['sessions'])==3 and report['completed_sessions']==0
    for session in report['sessions']:
        assert len(session['entries'])==len(cases)*2
        assert all(e['status']=='blocked_by_environment' and e['reason'] for e in session['entries'])


def test_complete_matrix_is_serial_and_full_reports_remain_accessible(tmp_path,monkeypatch):
    corpus,freeze,cases=prepare(tmp_path,monkeypatch);calls=[];running=[]
    monkeypatch.setattr(runner,'environment_gate',lambda:{'status':'ready'})
    def collect(*args,**kwargs):
        assert not running
        running.append(True);calls.append((kwargs['engines'],kwargs['case_relatives'],kwargs['profile']))
        folder=Path(kwargs['output']);folder.mkdir(parents=True)
        result=fake_report(kwargs['case_relatives'][0],kwargs['engines'][0]);running.clear();return result
    monkeypatch.setattr(runner,'run_benchmark',collect)
    report=runner.run_campaign(corpus,tmp_path/'output',freeze,engines=('native','rust'))
    assert len(calls)==3*len(cases)*2 and all(len(c[0])==len(c[1])==1 and c[2]=='full' for c in calls)
    assert report['completed_sessions']==3
    for session in report['sessions']:
        assert len(session['pair_reports'])==len(cases)
        for entry in session['entries']:
            data=Path(entry['report_path']).read_bytes()
            assert hashlib.sha256(data).hexdigest()==entry['report_sha256']
            assert json.loads(data)['cases'][0]['engines'][0]['units']


def test_code_change_aborts_and_preserves_completed_and_unrun_entries(tmp_path,monkeypatch):
    corpus,freeze,cases=prepare(tmp_path,monkeypatch);state={'digest':'frozen'}
    monkeypatch.setattr(runner,'implementation_digest',lambda:state['digest'])
    monkeypatch.setattr(runner,'environment_gate',lambda:{'status':'ready'})
    def collect(*args,**kwargs):
        Path(kwargs['output']).mkdir(parents=True);state['digest']='changed'
        return fake_report(kwargs['case_relatives'][0],kwargs['engines'][0])
    monkeypatch.setattr(runner,'run_benchmark',collect)
    report=runner.run_campaign(corpus,tmp_path/'output',freeze,engines=('native','rust'))
    assert report['status']=='aborted' and report['completed_sessions']==0
    assert report['sessions'][0]['entries'][0]['report_path']
    assert sum(e['status']=='completed' for s in report['sessions'] for e in s['entries'])==1
    assert len([e for s in report['sessions'] for e in s['entries']])==12
    assert all(e.get('reason') for s in report['sessions'] for e in s['entries'] if e['status']!='completed')
