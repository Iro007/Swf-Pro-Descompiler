"""Frozen correctness observation: one release/FWS per family, per-engine files.

One operation is collected per applicable engine; this is not a full performance
profile. All units/docs and partial failures remain accessible as full JSON.
"""
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT));sys.path.insert(0,str(PROJECT/'tools'))
from py_swf.benchmarks import run_engine,implementation_digest,match_units
from py_swf.canonical_inventory import build_inventory,attach_observations
from py_swf.corpus import read_immutable_manifest,classify_application_units
from py_swf.benchmark_engines import checked_tool
from verify_parity_source_semantics import verify_freeze,select_cases


def collect(corpus,output,freeze_file,*,engines=('native','rust','jpexs','pyflashkit'),include_holdout=False,tool_config=None,config_path=None,timeout=180):
    corpus,output=Path(corpus).resolve(),Path(output).resolve();manifest=read_immutable_manifest(corpus)
    freeze=verify_freeze(freeze_file,corpus);digest=implementation_digest();cases=select_cases(manifest,include_holdout=include_holdout)
    output.mkdir(parents=True,exist_ok=False)
    index={'schema_version':1,'purpose':'one frozen correctness observation per family/engine; not performance','freeze':freeze,
           'manifest_sha256':(corpus/'manifest.sha256').read_text().strip(),'scheduled_cases':len(cases),'engines':list(engines),'entries':[],
           'claims':{'semantic_parity':False,'performance_winner':None}}
    for case in cases:
        (output/case['family_id']).mkdir(parents=True,exist_ok=True)
        thin=[];refs=[]
        inventory=classify_application_units(build_inventory((corpus/case['relative']).read_bytes()),case.get('application_classes',[]))
        for engine in engines:
            entry={'family_id':case['family_id'],'split':case['split'],'relative':case['relative'],'engine':engine,'status':'not_run'}
            index['entries'].append(entry)
            if implementation_digest()!=digest:entry.update(status='aborted',diagnostic='Frozen source changed');continue
            folder=output/case['family_id']/engine;folder.mkdir(parents=True)
            if engine=='rust':
                try:
                    configured=checked_tool('rust',tool_config)
                    pinned=freeze.get('binaries',{}).get('rust',{})
                    if hashlib.sha256(Path(configured['executable']).read_bytes()).hexdigest()!=pinned.get('sha256'):
                        raise ValueError('Rust benchmark configuration differs from frozen binary pin')
                except (OSError,ValueError) as error:
                    result={'engine':engine,'status':'not_configured','units':[],'diagnostic':str(error),'quality':{'source_available':None}}
                else:result=run_engine(engine,(corpus/case['relative']).read_bytes(),{'warmups':0,'hot_runs':1,'cold_runs':0},config_path=config_path,tool_config=tool_config,timeout=timeout)
            else:result=run_engine(engine,(corpus/case['relative']).read_bytes(),{'warmups':0,'hot_runs':1,'cold_runs':0},config_path=config_path,tool_config=tool_config,timeout=timeout)
            result['units']=attach_observations(inventory,result.get('units',[]),engine=engine,adapter_mode=result.get('adapter_mode'))
            result.pop('unit_observations',None);result.pop('canonical_inventory',None)
            result.update(unit_observations_ref='units',family_id=case['family_id'],split=case['split'],canonical_inventory_sha256=inventory['sha256'])
            report={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'profile':'correctness_one_operation','operation':'source',
                    'configuration':{'warmups':0,'hot_runs':1,'cold_runs':0,'purpose':'correctness_only'},'environment':{'platform':platform.platform()},
                    'provenance':{'implementation_sha256_before':digest,'implementation_sha256_after':implementation_digest(),'implementation_unchanged':implementation_digest()==digest,'freeze':freeze},
                    'cases':[{**case,'file':str((corpus/case['relative']).resolve()),'canonical_inventory':inventory,'engines':[result]}],
                    'claims':{'semantic_parity':False,'performance_winner':None}}
            data=json.dumps(report,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode();path=folder/'benchmark.json';path.write_bytes(data)
            entry.update(status=result.get('status','unknown'),path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),
                         import_status='ready' if len(data)<=64*1024**2 else 'not_imported_oversize')
            refs.append(dict(entry));thin.append({**{key:result.get(key) for key in ('engine','status','inventory_status','adapter_mode')},
                'units':[{key:unit.get(key) for key in ('unit_id','document_id','scope','bytecode_sha256','identity_verified','generation','source_kind','represented_by_document')} for unit in result['units']]})
            (output/'correctness-index.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
        pairs={'family_id':case['family_id'],'split':case['split'],'relative':case['relative'],'reports':refs,'comparison':match_units(thin,inventory)}
        (output/case['family_id']/'pairs.json').write_text(json.dumps(pairs,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    index['implementation_unchanged']=implementation_digest()==digest
    index['scheduled_entries']=len(cases)*len(engines)
    index['completed_entries']=sum('path' in entry for entry in index['entries'])
    index['aborted_entries']=sum(entry['status']=='aborted' for entry in index['entries'])
    (output/'correctness-index.json').write_text(json.dumps(index,indent=2),encoding='utf-8');return index


def main():
    p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--frozen-build',type=Path,required=True)
    p.add_argument('--include-holdout',action='store_true');p.add_argument('--tool-config',type=Path);p.add_argument('--config-path',type=Path);p.add_argument('--timeout',type=int,default=180)
    args=p.parse_args();report=collect(args.corpus,args.output,args.frozen_build,include_holdout=args.include_holdout,tool_config=args.tool_config,config_path=args.config_path,timeout=args.timeout)
    print(json.dumps({'scheduled_cases':report['scheduled_cases'],'completed_entries':report['completed_entries'],'implementation_unchanged':report['implementation_unchanged']},indent=2))


if __name__=='__main__':main()
