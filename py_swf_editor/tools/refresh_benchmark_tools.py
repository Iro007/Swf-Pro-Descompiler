"""Audit local Flash roles and optionally run two small adapter smoke campaigns.

This is correctness/integration evidence under active development, not a quiet
performance ranking. Every observed input and configured artifact is hashed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
sys.path.insert(0, str(PROJECT))
from py_swf.benchmark_engines import checked_tool, describe_engines
from py_swf.benchmarks import run_benchmark
from py_swf.suite_tools import ToolRegistry
from py_swf.workspace_tools import discover_workspace_tools


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):
            value.update(chunk)
    return value.hexdigest()


def observation_rows(report,path):
    return [{'operation':report['operation'],'input_sha256':case['sha256'],'file':case['relative'],
             'engine':engine['engine'],'status':engine['status'],'hot_successful':engine['hot']['successful'],
             'cold_successful':engine['cold']['successful'],'failures':len(engine['failures']),
             'artifact_unit_scope':engine.get('artifact_unit_scope'),'artifacts':len(engine.get('artifacts',[])),
             'quality':engine['quality'],'report':str(path)}
            for case in report['cases'] for engine in case['engines']]


def refresh(output, *, measure=False):
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    if measure and any((output/operation).exists() for operation in ('source','disassemble')):
        raise ValueError('Preserve existing campaigns; choose a fresh output: '+str(output))
    manifest=PROJECT/'.dev-references/extra-tools.json'
    inputs=[ROOT/'benchmark_inputs/parity-20261014/swf'/name/'release.fws.swf'
            for name in ('dev-as2-timeline','dev-as3-control-handlers')]
    input_records=[{'path':str(path),'sha256':digest(path),'bytes':path.stat().st_size} for path in inputs]
    snapshots=output/'inputs'
    snapshots.mkdir(exist_ok=True)
    for source in inputs:
        target=snapshots/(source.parent.name+'.swf')
        if target.exists() and digest(target)!=digest(source):
            raise ValueError('Preserve existing different smoke input: '+str(target))
        if not target.exists():shutil.copyfile(source,target)
    provenance=json.loads(manifest.read_text(encoding='utf-8'))
    # Pin the current verified Parity binary for this probe instead of silently
    # measuring an older default executable. The live default is left intact.
    frozen=json.loads((ROOT/'reports/parity-implementation-20261014/freeze.json').read_text(encoding='utf-8'))
    rust=frozen['binaries']['rust']
    rust_path=ROOT/'rust_swf/dist/FlashUniversalStudio-Parity-20261014/rustswf.exe'
    if digest(rust_path)!=rust['sha256']:
        raise ValueError('Parity Rust executable changed; supply a newly audited frozen probe')
    provenance['tools']['rust']={'version':'Parity-20261014','executable':str(rust_path),
        'artifact_sha256':{str(rust_path):rust['sha256']},'scope':'Existing frozen Parity binary; active Python source, no performance ranking'}
    for engine in ('flasm','flex_swfdump'):
        provenance['tools'][engine]['evidence_root']=str(output/'external-artifacts')
    config=output/('smoke-tools.json' if measure else 'availability-tools.json')
    config.write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    observations=[]
    if measure:
        for operation,engines in [('source',('native','rust','jpexs','pyflashkit')),
                                  ('disassemble',('native','rust','jpexs','pyflashkit','rabcdasm','flasm','flex_swfdump'))]:
            destination=output/operation
            if destination.exists():
                raise ValueError('Preserve existing campaign; choose a fresh output: '+str(destination))
            report=run_benchmark(snapshots,profile='smoke',engines=engines,operation=operation,
                                 output=destination,tool_config=config,timeout=45)
            report['configuration']['purpose']='bounded adapter correctness and UI integration smoke; active development, no speed ranking'
            report['configuration']['agents_quiescent']=False
            report['configuration']['source_runtime']='current Python source and separately identified frozen Rust binary'
            (destination/'benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            observations.extend(observation_rows(report,destination/'benchmark.json'))
    else:
        for operation in ('source','disassemble'):
            path=output/operation/'benchmark.json'
            if path.is_file():
                observations.extend(observation_rows(json.loads(path.read_text(encoding='utf-8')),path))
    registry=describe_engines(config)
    for engine in registry:
        rows=[row for row in observations if row['engine']==engine['id']]
        engine['smoke_observations']=rows
        if engine['id'] in provenance['tools']:
            pin=checked_tool(engine['id'],config)
            engine['provenance']={key:pin.get(key) for key in ('version','archive','publisher_page','hash_authority','license','artifact_unit_scope')}
    # Compiler/runtime roles stay separate. Their prior corpus evidence and
    # current file availability do not turn into source-decompilation scores.
    suite=ToolRegistry(config_path=output/'unused-suite-config.json').catalog()
    corpus_manifest=ROOT/'benchmark_inputs/parity-20261014/manifest.json'
    corpus=json.loads(corpus_manifest.read_text(encoding='utf-8'))
    roles=[]
    for item in suite['engines']:
        if item['category'] not in ('compiler','runtime'):continue
        item['current_execution']='not_run_in_this_smoke'
        item['prior_corpus_families']=[family['family_id'] for family in corpus['families'] if family.get('compiler')==item['id']]
        if item['prior_corpus_families']:
            item['prior_corpus_manifest']={'path':str(corpus_manifest),'sha256':digest(corpus_manifest)}
        roles.append(item)
    rascal=Path(frozen['binaries']['rascal']['path'])
    roles.append({'id':'rascal','name':'Rascal 0.3.5','category':'compiler','operations':['compile_as2'],
        'available':rascal.is_file(),'path':str(rascal),'sha256':digest(rascal) if rascal.is_file() else None,
        'current_execution':'not_run_in_this_smoke','prior_corpus_manifest':{'path':str(corpus_manifest),'sha256':digest(corpus_manifest)},
        'prior_corpus_families':[family['family_id'] for family in corpus['families'] if family.get('compiler')=='rascal']})
    runtime=Path(frozen['binaries']['ruffle']['path'])
    roles.append({'id':'ruffle_headless','name':'Ruffle independent headless test harness','category':'runtime','operations':['execute_trace'],
        'available':runtime.is_file(),'path':str(runtime),'frozen_sha256':frozen['binaries']['ruffle']['sha256'],
        'current_execution':'not_run_in_this_smoke','prior_freeze':str(ROOT/'reports/parity-implementation-20261014/freeze.json')})
    if any(digest(Path(row['path']))!=row['sha256'] for row in input_records):
        raise ValueError('Original corpus input changed during smoke')
    available=[engine['id'] for engine in registry if engine['configured']]
    measured=sorted({row['engine'] for row in observations if row['status'] in ('measured','output_unmapped') and row['hot_successful']>0})
    result={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'purpose':'local role-aware availability and bounded adapter correctness',
        'inputs':input_records,'original_inputs_preserved':True,'registry':registry,'other_roles':roles,
        'discovery':discover_workspace_tools(ROOT),'configured_engines':available,'measured_engines':measured,
        'distinct_measured_engines':len(measured),'observations':observations,
        'unmeasured_notes':{'flare':'Restricted freeware; no configured adapter or authorized distribution installed',
            'swfmill':'XML converter; local executable unavailable',
            'swfextract':'SWFTools resource extractor; local executable unavailable',
            'swftools_swfdump':'Different project from Apache Flex swfdump; not installed',
            'commercial':'Installers, licensed GUI candidates and download portals are not independent measured engines'},
        'claims':{'parity':False,'superiority':False,'performance_winner':None}}
    (output/'available-engine-matrix.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'configured':available,'measured':measured,'distinct_measured_engines':len(measured),'output':str(output)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/benchmark-refresh-20261014')
    parser.add_argument('--measure',action='store_true')
    args=parser.parse_args()
    print(json.dumps(refresh(args.output,measure=args.measure),indent=2))
