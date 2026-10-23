"""Compile engine-generated source and compare exact bounded runtime traces.

No original authored source is used to repair generated source. Partial or
unsupported documents remain evidence. Holdout requires verified frozen pins.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT));sys.path.insert(0,str(PROJECT/'tools'))
from py_swf.benchmarks import implementation_digest
from py_swf.corpus import read_immutable_manifest
from py_swf.suite_tools import ToolRegistry,run_tool,fingerprint
from py_swf.universal import UniversalSuite
from py_swf.validation import validate_swf_bytes
from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
from py_swf.swf_parser import SWFFile,collect_symbol_names
from run_ruffle_trace_probe import probe


def verify_freeze(path,corpus):
    if path is None:raise ValueError('Holdout requires --frozen-build with exact source and binary hashes')
    record=json.loads(Path(path).read_text(encoding='utf-8'))
    if record.get('implementation_sha256')!=implementation_digest():raise ValueError('Frozen implementation hash does not match current source')
    if record.get('corpus_manifest_sha256',record.get('manifest_sha256'))!=(Path(corpus)/'manifest.sha256').read_text().strip():raise ValueError('Frozen corpus hash differs')
    binaries=record.get('binaries',{})
    if not binaries:raise ValueError('Frozen build has no binary hash pins')
    for name,binary in binaries.items():
        if not isinstance(binary,dict) or not binary.get('path') or not binary.get('sha256') or fingerprint(binary['path'])!=binary['sha256']:
            raise ValueError('Frozen binary hash mismatch: '+name)
    return record


def select_cases(manifest,families=None,include_holdout=False):
    selected=[]
    requested=set(families or [])
    known={f['family_id'] for f in manifest['families']}
    if requested-known:raise ValueError('Unknown source family: '+','.join(sorted(requested-known)))
    for case in manifest['cases']:
        if case['debug'] or case['compression']!='FWS':continue
        if requested and case['family_id'] not in requested:continue
        if case['split']=='holdout' and not include_holdout:continue
        selected.append(case)
    return selected


def source_filename(text):
    # Derive the output path from actual generated declarations, never the
    # authored source manifest or a replacement stub.
    package=re.search(r'\bpackage\s+([\w.]+)\s*\{',text)
    classes=re.findall(r'\bpublic\s+(?:(?:final|dynamic)\s+)?(?:class|interface)\s+([A-Za-z_$][\w$]*)',text)
    if len(classes)!=1:raise ValueError('Independent Flex requires one public class source per file; generated document is not a single class')
    name=classes[0]
    return Path(*(package.group(1).split('.') if package else []))/(name+'.as'),name


def generated_project(documents,original):
    movie=SWFFile();movie.read_bytes(Path(original).read_bytes())
    root=collect_symbol_names([tag for tag in movie.tags if tag.tag_type==76]).get(0)
    if not root:raise ValueError('Original has no SymbolClass 0; do not guess an entry class')
    sources={};class_documents=[d for d in documents if d.get('scope')=='class']
    if not class_documents:raise ValueError('No generated class documents are available')
    partial=[d['document_id'] for d in class_documents if d.get('completeness')!='complete' or not d.get('source')]
    if partial:raise ValueError('Generated project has incomplete class dependencies: '+','.join(partial))
    for document in class_documents:
        path,name=source_filename(document['source']);key=path.as_posix()
        if key in sources and sources[key]!=document['source']:raise ValueError('Generated class names collide across ABC blocks: '+key)
        sources[key]=document['source']
    root_file=root.replace('.','/')+'.as'
    if root_file not in sources:raise ValueError('Original SymbolClass 0 is absent from generated project: '+root)
    return {'root_class':root,'entry':root_file,'sources':sources,'class_documents':len(class_documents)}


def compile_shared_project(project,folder,engine):
    entry=project['entry'];sources=dict(project['sources']);source=sources.pop(entry)
    compiled=compile_class_program(source,sources=sources)
    candidate=folder/'candidate.swf';candidate.write_bytes(class_program_swf(compiled,document_class=project['root_class']))
    validation=validate_swf_bytes(candidate.read_bytes())
    if not validation['valid']:raise ValueError('Whole generated project produces invalid SWF')
    return {'status':'compiled','compiler':'shared_native','compiler_delegated':engine=='rust','source_engine':engine,
            'mode':'whole generated project; original SymbolClass 0; no exact edit admission',
            'path':str(candidate),'sha256':fingerprint(candidate),'validation':validation,'diagnostics':compiled.diagnostics}


def flex_source_files(sources):
    """Split generated package namespaces into their own Flex source modules.

    Flex's source-path resolver accepts one externally visible definition per
    file. Preserve all declarations and literals; do not repair source bodies.
    """
    from py_swf.compiler.class_parser import parse_class_program, _lex
    output={};namespaces={}
    for relative,source in sources.items():
        program=parse_class_program(source);tokens=_lex(source);spans=[];depth=0
        expected={(d.package,d.name):d for d in program.namespaces}
        for index,token in enumerate(tokens):
            if token.value=='{':depth+=1
            elif token.value=='}':depth-=1
            elif token.value=='namespace' and depth==1:
                name=tokens[index+1].value
                declaration=next((d for (package,key),d in expected.items() if key==name),None)
                if declaration is None:raise ValueError('Generated namespace cannot be linked to its parsed declaration')
                end=index+1
                while end<len(tokens) and tokens[end].value!=';':end+=1
                if end==len(tokens):raise ValueError('Truncated generated namespace declaration')
                start=index-1 if index and tokens[index-1].value in ('public','internal') else index
                spans.append((tokens[start].start,tokens[end].end))
                path='/'.join(filter(None,[declaration.package.replace('.','/'),name+'.as']))
                text='package'+(' '+declaration.package if declaration.package else '')+'\n{\n    '+declaration.visibility+' namespace '+name+' = '+declaration.value_source+';\n}\n'
                if path in namespaces and namespaces[path]!=text:raise ValueError('Generated namespace identities collide: '+path)
                namespaces[path]=text
        for start,end in reversed(spans):source=source[:start]+'\n'*source[start:end].count('\n')+source[end:]
        output[relative]=source
    for path,text in namespaces.items():
        if path in output:raise ValueError('Generated namespace/class module path collision: '+path)
        output[path]=text
    return output


def compile_flex_project(project,folder,registry):
    generated_files=flex_source_files(project['sources'])
    for relative,source in generated_files.items():
        target=folder/'source'/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(source,encoding='utf-8')
    compiler=Path(registry.executable('flex'));frameworks=compiler.parent.parent/'frameworks';output=folder/'candidate.swf'
    command=[registry.executable('java'),'-Xmx1024m','-jar',str(compiler),'+flexlib='+str(frameworks),'+playerglobalHome='+str(frameworks/'libs/player'),
             '-target-player=32.0','-debug=false','-omit-trace-statements=false','-output='+str(output),'-source-path='+str(folder/'source'),str(folder/'source'/project['entry'])]
    env=registry.environment();env['PLAYERGLOBAL_HOME']=str(frameworks/'libs/player')
    try:log=run_tool(command,cwd=folder,env=env,timeout=110)
    except (ValueError,OSError) as error:return {'status':'compilation_failed','compiler':'independent_flex','command':command,'diagnostic':str(error),'dependency_origin':'engine_generated_source_resolution; authored sources not used'}
    validation=validate_swf_bytes(output.read_bytes())
    return {'status':'compiled' if validation['valid'] else 'invalid_swf','compiler':'independent_flex','command':command,'log':log,
            'path':str(output),'sha256':fingerprint(output),'validation':validation,'compiler_sha256':fingerprint(compiler),
            'source_partitioning':'Generated package namespaces have their own source-path modules; bodies unchanged',
            'generated_file_sha256':{name:hashlib.sha256(text.encode()).hexdigest() for name,text in generated_files.items()}}


def compile_flex_source(text,folder,registry):
    relative,name=source_filename(text);source=folder/'source'/relative;source.parent.mkdir(parents=True,exist_ok=True);source.write_text(text,encoding='utf-8')
    output=folder/'candidate.swf';compiler=Path(registry.executable('flex'));frameworks=compiler.parent.parent/'frameworks'
    command=[registry.executable('java'),'-Xmx1024m','-jar',str(compiler),'+flexlib='+str(frameworks),'+playerglobalHome='+str(frameworks/'libs/player'),
             '-target-player=32.0','-debug=false','-omit-trace-statements=false','-output='+str(output),'-source-path='+str(folder/'source'),str(source)]
    env=registry.environment();env['PLAYERGLOBAL_HOME']=str(frameworks/'libs/player')
    try:log=run_tool(command,cwd=folder,env=env,timeout=110)
    except (ValueError,OSError) as error:
        diagnostic=str(error);missing=any(s in diagnostic.lower() for s in ('not found','no se encontró','undefined','no definida','no disponible'))
        return {'status':'dependency_missing' if missing else 'compilation_failed','compiler':'independent_flex','command':command,'diagnostic':diagnostic}
    validation=validate_swf_bytes(output.read_bytes())
    return {'status':'compiled' if validation['valid'] else 'invalid_swf','compiler':'independent_flex','command':command,'log':log,
            'path':str(output),'sha256':fingerprint(output),'validation':validation,'compiler_sha256':fingerprint(compiler)}


def compile_new_shared_candidate(source,folder,engine):
    """New standalone generated program; exact-ABC replacement gates are separate."""
    compiled=compile_class_program(source)
    main=next((name for name in compiled.class_names if name.rsplit('.',1)[-1]=='Main'),None)
    if main is None:raise ValueError('Generated document has no Main entry class; a class fragment alone cannot drive the original runtime scenario')
    candidate=folder/'shared-native.swf';candidate.write_bytes(class_program_swf(compiled,document_class=main))
    validation=validate_swf_bytes(candidate.read_bytes())
    if not validation['valid']:raise ValueError('Generated-source shared candidate failed SWF validation')
    return {'status':'compiled','compiler':'shared_native','compiler_delegated':engine=='rust','source_engine':engine,
            'mode':'new_generated_program; independent of exact original ABC edit admission',
            'path':str(candidate),'sha256':fingerprint(candidate),'validation':validation,'diagnostics':compiled.diagnostics}


def runtime_result(original,candidate,case,folder,runtime):
    if runtime is None or not Path(runtime).is_file():return {'status':'dependency_missing','diagnostic':'Pinned headless Ruffle harness unavailable'}
    fixture=folder/'trace-fixture.json';fixture.parent.mkdir(parents=True,exist_ok=True)
    fixture.write_text(json.dumps({'original_swf':str(original.resolve()),'reconstructed_swf':str(Path(candidate).resolve()),'expected_trace':case['expected_trace'],'ticks':1},indent=2),encoding='utf-8')
    return probe(fixture,Path(runtime),folder/'runtime')


def evaluate_as2_actions(suite,engine,original,case,folder,runtime,rascal):
    records=[]
    for unit in suite.units():
        if unit.language!='as2' or unit.diagnostic:continue
        result=suite.decompile({'unit_id':unit.document_id,'engine':'python' if engine=='native' else engine})
        source=result.get('source');record={'document_id':unit.document_id,'scope':'action','source':source,
            'source_engine':engine,'diagnostics':result.get('diagnostics',[]),'compilations':[]}
        records.append(record)
        if not source:record['status']='not_supported';continue
        job=folder/unit.document_id.replace(':','-').replace('/','_');job.mkdir()
        source_path=job/'generated.as';source_path.write_text(source,encoding='utf-8');record['source_file']=str(source_path);record['source_sha256']=fingerprint(source_path)
        # Compile the generated parent action through PySWF while preserving
        # the original timeline and unrelated tags. A nested function body
        # alone cannot replace its complete enclosing action record.
        try:
            from py_swf.unit_editing import edit_unit
            if unit.kind not in ('frame','init','button','button2','clip'):
                raise ValueError('Nested function compilation requires its generated enclosing action')
            candidate=job/'shared-native.swf'
            candidate.write_bytes(edit_unit(original.read_bytes(), {
                'document_id':unit.document_id,'bytecode_sha256':hashlib.sha256(unit.code).hexdigest()},source,source=True))
            shared={'status':'compiled','compiler':'shared_native','compiler_delegated':engine=='rust',
                    'source_engine':engine,'path':str(candidate),'sha256':fingerprint(candidate),
                    'validation':validate_swf_bytes(candidate.read_bytes()),'mode':'generated action; preserve original timeline and unrelated tags'}
            shared['runtime']=runtime_result(original,candidate,case,job/'shared-native-check',runtime)
        except (OSError,ValueError,SyntaxError) as error:
            shared={'status':'not_supported','compiler':'shared_native','compiler_delegated':engine=='rust','diagnostic':str(error)}
        record['compilations'].append(shared)
        if not rascal or not Path(rascal).is_file():
            record.update(status='dependency_missing',diagnostic='Pinned independent Rascal executable not supplied');continue
        output=job/'independent-rascal.swf';command=[str(rascal),'--swf-version','15','--frame-rate','30','--output',str(output),str(source_path)]
        try:
            log=run_tool(command,cwd=job,env=suite.registry.environment(),timeout=110)
            validation=validate_swf_bytes(output.read_bytes())
            compilation={'status':'compiled' if validation['valid'] else 'invalid_swf','compiler':'independent_rascal','command':command,'log':log,
                         'compiler_sha256':fingerprint(rascal),'path':str(output),'sha256':fingerprint(output),'validation':validation}
            if validation['valid']:compilation['runtime']=runtime_result(original,output,case,job/'independent-rascal-check',runtime)
        except (OSError,ValueError,SyntaxError) as error:compilation={'status':'compilation_failed','compiler':'independent_rascal','diagnostic':str(error),'command':command}
        record['compilations'].append(compilation);record['status']='evaluated'
    return records


def evaluate(corpus,output,*,engines=('native','rust'),families=None,rust_executable=None,runtime=None,rascal=None,frozen_build=None,include_holdout=False):
    corpus,output=Path(corpus).resolve(),Path(output).resolve();manifest=read_immutable_manifest(corpus)
    rust_executable=Path(rust_executable).resolve() if rust_executable else None
    runtime=Path(runtime).resolve() if runtime else None
    rascal=Path(rascal).resolve() if rascal else None
    freeze=verify_freeze(frozen_build,corpus) if include_holdout or frozen_build is not None else None
    if freeze:
        rust_executable=rust_executable or freeze.get('binaries',{}).get('rust',{}).get('path')
        runtime=runtime or freeze.get('binaries',{}).get('ruffle',{}).get('path')
        rascal=rascal or freeze.get('binaries',{}).get('rascal',{}).get('path')
        for name,path in (('rust',rust_executable),('ruffle',runtime),('rascal',rascal)):
            if path:
                pinned=freeze.get('binaries',{}).get(name)
                if not pinned or fingerprint(path)!=pinned['sha256']:raise ValueError('Execution binary differs from frozen pin: '+name)
    cases=select_cases(manifest,families,include_holdout);output.mkdir(parents=True,exist_ok=False)
    before=implementation_digest();report={'schema_version':1,'corpus_manifest_sha256':(corpus/'manifest.sha256').read_text().strip(),
        'implementation_sha256_before':before,'frozen_build':freeze,'holdout_included':include_holdout,'cases':[],
        'claims':{'semantic_parity':False,'superiority':False},'scope':'Engine-generated source only; exact expected trace at one declared tick'}
    for case in cases:
        original=corpus/case['relative'];family_folder=output/case['family_id'];family_folder.mkdir()
        row={key:case.get(key) for key in ('relative','family_id','split','sha256','expected_trace')};row['engines']=[]
        report['cases'].append(row)
        for engine in engines:
            folder=family_folder/engine;folder.mkdir();suite=UniversalSuite(folder/'state',folder/'tools.json')
            engine_row={'engine':engine,'documents':[],'status':'not_run'};row['engines'].append(engine_row)
            try:
                if engine=='rust':
                    if not rust_executable:raise ValueError('Pinned Rust executable not supplied; source fallback is forbidden')
                    suite.registry.configure('rust',rust_executable)
                suite.open(original)
                abc_paths=sorted({u.tag_path for u in suite.units() if u.abc is not None and not u.diagnostic})
                if not abc_paths:
                    engine_row['documents']=evaluate_as2_actions(suite,engine,original,case,folder,runtime,rascal)
                    engine_row['status']='observed' if engine_row['documents'] else 'not_supported'
                else:
                    for path in abc_paths:
                        documents=[];offset=0
                        while True:
                            page=suite.code_documents({'tag_path':list(path),'engine':engine,'include_source':True,'limit':200,'offset':offset})
                            documents.extend(page['items']);offset+=len(page['items'])
                            if offset>=page['total']:break
                            if not page['items']:raise ValueError('Source document pagination stalled; missing documents cannot be omitted')
                        for document in documents:
                            source=document.get('source');identifier=document['document_id'];document_folder=folder/identifier.replace(':','-').replace('/','_');document_folder.mkdir()
                            record={**document,'source_file':None,'compilations':[]};engine_row['documents'].append(record)
                            if source is not None:
                                source_path=document_folder/'generated.as';source_path.write_text(source,encoding='utf-8')
                                record['source_file']=str(source_path);record['source_sha256']=fingerprint(source_path)
                            record['status']='not_supported' if not source or document.get('completeness')!='complete' else 'retained_for_project'
                            record['compilation']='not_run'
                            record['standalone_eligibility']='not_eligible_standalone; only original document root drives runtime'
                    try:
                        project=generated_project(engine_row['documents'],original)
                        engine_row['project']={k:v for k,v in project.items() if k!='sources'}
                        engine_row['project']['source_hashes']={name:hashlib.sha256(text.encode('utf-8')).hexdigest() for name,text in project['sources'].items()}
                        engine_row['project_compilations']=[]
                        for compiler in ('shared_native','independent_flex'):
                            job=folder/compiler;job.mkdir()
                            try:
                                candidate=compile_shared_project(project,job,engine) if compiler=='shared_native' else compile_flex_project(project,job,suite.registry)
                                if candidate['status']=='compiled':candidate['runtime']=runtime_result(original,candidate['path'],case,job/'runtime-check',runtime)
                            except (OSError,ValueError,SyntaxError) as error:candidate={'status':'not_supported','compiler':compiler,'compiler_delegated':engine=='rust' and compiler=='shared_native','diagnostic':str(error)}
                            engine_row['project_compilations'].append(candidate)
                    except (OSError,ValueError) as error:engine_row['project_status']='not_supported_partial_project';engine_row['project_diagnostic']=str(error)
                    engine_row['status']='observed'
            except Exception as error:engine_row.update(status='dependency_missing' if engine=='rust' and not rust_executable else 'failed',diagnostic=str(error))
            finally:suite.close()
            (output/'source-semantics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if fingerprint(original)!=case['sha256']:raise ValueError('Original input changed during semantic checks')
    after=implementation_digest();report.update(implementation_sha256_after=after,implementation_unchanged=before==after,
        scheduled_cases=len(cases),completed_cases=len(report['cases']))
    (output/'source-semantics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--corpus',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--families',nargs='*');parser.add_argument('--engines',nargs='+',default=['native','rust']);parser.add_argument('--rust-executable',type=Path)
    parser.add_argument('--runtime',type=Path);parser.add_argument('--frozen-build',type=Path);parser.add_argument('--include-holdout',action='store_true')
    parser.add_argument('--rascal',type=Path)
    args=parser.parse_args();report=evaluate(args.corpus,args.output,engines=args.engines,families=args.families,rust_executable=args.rust_executable,
        runtime=args.runtime,rascal=args.rascal,frozen_build=args.frozen_build,include_holdout=args.include_holdout)
    print(json.dumps({'completed_cases':report['completed_cases'],'implementation_unchanged':report['implementation_unchanged'],'output':str(args.output)},indent=2))


if __name__=='__main__':main()
