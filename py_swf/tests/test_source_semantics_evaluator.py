import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import verify_parity_source_semantics as evaluator


def test_default_selection_never_exposes_holdout_and_variants_not_counted_as_families():
    cases=[]
    for split in ('development','validation','holdout'):
        for debug in (False,True):
            for compression in ('FWS','CWS','ZWS'):
                cases.append({'family_id':split,'split':split,'debug':debug,'compression':compression})
    manifest={'cases':cases,'families':[{'family_id':s} for s in ('development','validation','holdout')]}
    selected=evaluator.select_cases(manifest)
    assert len(selected)==2 and {c['split'] for c in selected}=={'development','validation'}
    assert evaluator.select_cases(manifest,['holdout'])==[]
    with pytest.raises(ValueError,match='Unknown'):evaluator.select_cases(manifest,['absent'])


def test_holdout_requires_matching_implementation_corpus_and_binary_pins(tmp_path,monkeypatch):
    corpus=tmp_path/'corpus';corpus.mkdir();(corpus/'manifest.sha256').write_text('corpus')
    monkeypatch.setattr(evaluator,'implementation_digest',lambda:'source')
    with pytest.raises(ValueError,match='requires'):evaluator.verify_freeze(None,corpus)
    freeze=tmp_path/'freeze.json';binary=tmp_path/'runtime.exe';binary.write_bytes(b'not executed pinned bytes')
    record={'implementation_sha256':'source','corpus_manifest_sha256':'corpus','binaries':{'ruffle':{'path':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}}}
    freeze.write_text(json.dumps(record));assert evaluator.verify_freeze(freeze,corpus)==record
    binary.write_bytes(b'changed')
    with pytest.raises(ValueError,match='binary hash mismatch'):evaluator.verify_freeze(freeze,corpus)


def test_independent_compiler_input_is_exact_generated_source_and_not_original(tmp_path,monkeypatch):
    generated='package newname { public class Exact extends flash.display.Sprite { public function Exact(){trace("engine source");} } }'
    calls=[]
    class Registry:
        def executable(self,name):return str(tmp_path/('mxmlc.jar' if name=='flex' else 'java.exe'))
        def environment(self):return {}
    (tmp_path/'mxmlc.jar').write_bytes(b'pinned compiler bytes')
    def compile(command,**kwargs):
        source=Path(command[-1]);assert source.read_text()==generated
        calls.append(source)
        output=Path(next(a.split('=',1)[1] for a in command if a.startswith('-output=')))
        output.write_bytes(b'candidate from generated text');return 'compiler returned'
    monkeypatch.setattr(evaluator,'run_tool',compile)
    monkeypatch.setattr(evaluator,'validate_swf_bytes',lambda *a:{'valid':True})
    result=evaluator.compile_flex_source(generated,tmp_path/'job',Registry())
    assert result['status']=='compiled' and calls[0].name=='Exact.as'
    assert calls[0].parts[-2:] == ('newname','Exact.as')


def test_missing_dependency_diagnostics_are_retained_without_repairing_source(tmp_path,monkeypatch):
    class Registry:
        def executable(self,name):return str(tmp_path/(name+'.jar'))
        def environment(self):return {}
    def fail(*args,**kwargs):raise ValueError('No se encontró el tipo: app.Missing')
    monkeypatch.setattr(evaluator,'run_tool',fail)
    result=evaluator.compile_flex_source('package { public class Main extends flash.display.Sprite {} }',tmp_path/'job',Registry())
    assert result['status']=='dependency_missing' and 'app.Missing' in result['diagnostic']
    assert not list((tmp_path/'job/source').glob('app/*'))


def test_new_shared_candidate_does_not_require_original_abc_edit_admission(tmp_path):
    generated='package { public class Main extends flash.display.Sprite { public function Main(){super();trace("generated only");} } }'
    result=evaluator.compile_new_shared_candidate(generated,tmp_path,'rust')
    assert result['status']=='compiled' and result['compiler']=='shared_native' and result['compiler_delegated']
    assert Path(result['path']).is_file() and result['validation']['valid']
    assert 'original ABC edit admission' in result['mode']


def test_generated_project_uses_original_symbol_root_and_requires_all_generated_dependencies(tmp_path):
    from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
    main='package { public class Main extends flash.display.Sprite { public function Main(){super();var h:Helper=new Helper();trace(h.value());} } }'
    helper='package { public class Helper { public function Helper(){super();} public function value():int{return 9;} } }'
    compiled=compile_class_program(main,sources={'Helper.as':helper})
    original=tmp_path/'original.swf';original.write_bytes(class_program_swf(compiled,document_class='Main'))
    docs=[{'document_id':'tag:0/class:0','scope':'class','completeness':'complete','source':main},
          {'document_id':'tag:0/class:1','scope':'class','completeness':'complete','source':helper}]
    project=evaluator.generated_project(docs,original)
    assert project['root_class']=='Main' and project['entry']=='Main.as'
    assert project['sources']=={'Main.as':main,'Helper.as':helper}
    docs[1]['completeness']='partial'
    with pytest.raises(ValueError,match='incomplete class dependencies'):evaluator.generated_project(docs,original)
    assert not list(tmp_path.glob('**/Helper.as'))  # no authored fallback file opened/created
