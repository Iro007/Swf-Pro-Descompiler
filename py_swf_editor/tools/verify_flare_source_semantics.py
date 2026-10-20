"""Compile exact Flare frame source and retain bounded trace evidence.

Only declared development/validation corpus cases are selected by default.
Generated .flr and extracted .as are separate artifacts; no authored source is
opened or substituted. This script does not claim global semantic equivalence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
sys.path.insert(0,str(PROJECT/'tools'))
from py_swf.benchmark_engines import checked_tool
from py_swf.benchmark_external_worker import ExternalToolError,perform
from py_swf.corpus import read_immutable_manifest
from py_swf.flare_source import flare_complete_action_source
from py_swf.suite_tools import ToolRegistry,run_tool,fingerprint
from py_swf.validation import validate_swf_bytes
from verify_parity_source_semantics import select_cases,runtime_result


def evaluate(corpus,output,*,families=None,rascal=None,runtime=None):
    corpus,output=Path(corpus).resolve(),Path(output).resolve()
    manifest=read_immutable_manifest(corpus)
    cases=select_cases(manifest,families)
    output.mkdir(parents=True,exist_ok=False)
    config=checked_tool('flare')
    report={'schema_version':1,'engine':'flare','version':config['version'],
        'executable_sha256':config['artifact_sha256'][config['executable']],
        'corpus_manifest_sha256':(corpus/'manifest.sha256').read_text().strip(),
        'holdout_included':False,'cases':[],
        'scope':'Exact generated source; single root frame-1 action placement; independent compilation; exact declared traces',
        'claims':{'semantic_parity':False,'superiority':False}}
    for case in cases:
        original=corpus/case['relative'];data=original.read_bytes()
        if fingerprint(original)!=case['sha256']:raise ValueError('Corpus input hash differs')
        folder=output/case['family_id'];folder.mkdir()
        row={key:case.get(key) for key in ('relative','family_id','split','sha256','expected_trace')}
        report['cases'].append(row)
        try:
            result=perform('flare',{**config,'evidence_root':str(folder/'raw')},data,'source',True)
        except (OSError,ValueError,SyntaxError) as error:
            result={'ok':False,'status':'failed','diagnostic':str(error),
                    'failure_origin':'external_process' if isinstance(error,ExternalToolError) else 'adapter'}
            if isinstance(error,ExternalToolError):
                result.update(native_returncode=error.returncode,stdout_tail=error.stdout,stderr_tail=error.stderr)
        row['generation']=result
        if result.get('ok') is not True:
            row['status']='generation_failed'
        elif result['status']=='not_applicable':
            row['status']='not_applicable'
        else:
            source=flare_complete_action_source(data,result);row['source_placement']=source
            row['status']=source['status']
            if source['status']=='complete_generated_actions':
                generated=folder/'generated.as';generated.write_bytes(source['source'].encode('utf-8'))
                row['source_file']=str(generated);row['source_sha256']=fingerprint(generated)
                row['compilations']=[]
                if not rascal:
                    row['compilations'].append({'status':'dependency_missing','compiler':'independent_rascal'})
                else:
                    compiler=Path(rascal).resolve();candidate=folder/'independent-rascal.swf'
                    command=[str(compiler),'--swf-version','15','--frame-rate','30','--output',str(candidate),str(generated)]
                    compiled={'compiler':'independent_rascal','compiler_sha256':fingerprint(compiler),'command':command,
                        'source_origin':'Exact Flare body; wrapper removal only; authored sources not used'}
                    row['compilations'].append(compiled)
                    try:
                        registry=ToolRegistry(folder/'tools.json')
                        compiled['log']=run_tool(command,cwd=folder,env=registry.environment(),timeout=110)
                        compiled['validation']=validate_swf_bytes(candidate.read_bytes())
                        compiled['status']='compiled' if compiled['validation']['valid'] else 'invalid_swf'
                        compiled['path']=str(candidate);compiled['sha256']=fingerprint(candidate)
                        if compiled['status']=='compiled':
                            compiled['runtime']=runtime_result(original,candidate,case,folder/'runtime-check',runtime)
                    except (OSError,ValueError,SyntaxError) as error:
                        compiled.update(status='compilation_failed',diagnostic=str(error))
        if hashlib.sha256(original.read_bytes()).hexdigest()!=case['sha256']:raise ValueError('Original input changed')
        (output/'flare-source-semantics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--families',nargs='*');parser.add_argument('--rascal',type=Path);parser.add_argument('--runtime',type=Path)
    args=parser.parse_args()
    report=evaluate(args.corpus,args.output,families=args.families,rascal=args.rascal,runtime=args.runtime)
    print(json.dumps({'cases':len(report['cases']),'output':str(args.output)},indent=2))


if __name__=='__main__':main()
