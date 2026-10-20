import hashlib
from pathlib import Path

import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.flare_source import flare_complete_action_source
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf


def movie(extra=()):
    return make_synthetic_swf([SWFTag(12,assemble_avm1('push 1\ntrace\nend')),*extra,SWFTag(1,b'')])


def output(data,source):
    payload=source.encode('latin-1')
    return {'ok':True,'warnings':[],
        'canonical_inventory':{'input_sha256':hashlib.sha256(data).hexdigest()},
        'raw_sources':[{'file':'input.flr','source':source,'bytes':len(payload),
                        'encoding':'latin-1','sha256':hashlib.sha256(payload).hexdigest()}]}


def test_wrapper_removal_preserves_exact_generated_body_and_never_claims_correctness():
    data=movie()
    body='\r\n  // comment with } {\r\n  trace("}\\\"{");\r\n  /* } */ if (true) { trace(1); }\r\n '
    raw="movie 'C:/temp/input.swf' {\n// flash 15\nframe 1 {"+body+'}\n}\n'
    result=flare_complete_action_source(data,output(data,raw))
    assert result['status']=='complete_generated_actions'
    assert result['source']==body and result['source_origin']=='Flare raw output; wrapper removal only'
    assert raw[slice(*result['source_span_characters'])]==body
    assert result['source_fidelity']=='not_verified'
    assert result['compilation']==result['behavior']=='not_run'


@pytest.mark.parametrize('raw',[
    'nonempty is not correct source',
    "movie 'input.swf' { frame 2 {trace(1);} }",
    "movie 'input.swf' { frame 1 {trace(1);} frame 2 {trace(2);} }",
    "movie 'input.swf' { frame 1 {trace(1);} } trace(99);",
    "movie 'input.swf' { frame 1 {trace(1);}",
    "movie 'input.swf' { frame 1 {trace('unfinished);} }",
    "movie 'input.swf' { frame 1 {/* unfinished } }",
    "movie 'input.swf' { frame 1 {} }",
])
def test_unsupported_or_incomplete_raw_output_is_preserved_without_admission(raw):
    data=movie();result=flare_complete_action_source(data,output(data,raw))
    assert result['status']=='unverified' and result['diagnostic']
    assert 'source' not in result


@pytest.mark.parametrize('extra',[
    [SWFTag(12,assemble_avm1('push 2\ntrace\nend'))],
    [SWFTag(39,b'\x01\x00\x01\x00\x00\x00')],
    [SWFTag(59,b'\x01\x00'+assemble_avm1('push 2\ntrace\nend'))],
])
def test_multiple_actions_sprites_and_init_scripts_cannot_be_flattened(extra):
    data=movie(extra);raw="movie 'input.swf' { frame 1 {trace(1);} }"
    result=flare_complete_action_source(data,output(data,raw))
    assert result['status']=='not_supported' and 'source' not in result


@pytest.mark.parametrize('mutation', ['warning','changed_source','other_input','failed'])
def test_warnings_tampering_and_cross_input_evidence_cannot_be_admitted(mutation):
    data=movie();result=output(data,"movie 'input.swf' { frame 1 {trace(1);} }")
    if mutation=='warning':result['warnings']=['Flare failed to decompile a pattern']
    elif mutation=='changed_source':result['raw_sources'][0]['source']+=' '
    elif mutation=='other_input':result['canonical_inventory']['input_sha256']='0'*64
    else:result['ok']=False
    checked=flare_complete_action_source(data,result)
    assert checked['status']=='unverified' and 'source' not in checked


def test_flare_adapter_retains_raw_latin1_and_stdout_warnings_as_separate_evidence(monkeypatch,tmp_path):
    import py_swf.benchmark_external_worker as adapter
    raw=b"movie 'input.swf' { frame 1 {trace('caf\xe9');} }"
    def invoke(command,cwd):
        (Path(cwd)/'input.flr').write_bytes(raw)
        return b'warning: unsupported pattern', '', 1.0
    monkeypatch.setattr(adapter,'invoke',invoke)
    result=adapter.perform('flare',{'executable':'pinned-flare.exe','evidence_root':str(tmp_path)},movie(),'source',True)
    assert result['status']=='output_unmapped' and result['units']==[]
    assert result['raw_sources'][0]['source'].encode('latin-1')==raw
    assert result['warnings']==['warning: unsupported pattern']
    assert Path(result['artifacts'][0]['artifact_path']).read_bytes()==raw
    assert result['compilation']==result['behavior']=='not_run'
    assert flare_complete_action_source(movie(),result)['status']=='unverified'


def test_flare_as3_is_not_applicable_and_never_invokes(monkeypatch):
    import py_swf.benchmark_external_worker as adapter
    from tests.native_fixtures import as3_tag
    def forbidden(*args):raise AssertionError('AS3 must not invoke Flare')
    monkeypatch.setattr(adapter,'invoke',forbidden)
    result=adapter.perform('flare',{},make_synthetic_swf([as3_tag()]),'source',True)
    assert result['status']=='not_applicable' and result['eligible_units']==0


def test_zero_exit_and_empty_flare_output_cannot_count_as_source(monkeypatch):
    import py_swf.benchmark_external_worker as adapter
    monkeypatch.setattr(adapter,'invoke',lambda *args:(b'', '', 1.0))
    with pytest.raises(ValueError,match='no nonempty output'):
        adapter.perform('flare',{'executable':'pinned-flare.exe'},movie(),'source',True)


def test_saved_compiler_input_keeps_exact_generated_crlf_characters(tmp_path,monkeypatch):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
    import verify_flare_source_semantics as evaluator
    data=movie();original=tmp_path/'original.swf';original.write_bytes(data)
    (tmp_path/'manifest.sha256').write_text('manifest-digest')
    case={'relative':'original.swf','family_id':'generated','split':'development',
          'sha256':hashlib.sha256(data).hexdigest(),'expected_trace':['1']}
    body='\r\n  trace(1);\r\n '
    generated=output(data,"movie 'input.swf' { frame 1 {"+body+'} }')
    generated['status']='output_unmapped'
    monkeypatch.setattr(evaluator,'read_immutable_manifest',lambda *a:{})
    monkeypatch.setattr(evaluator,'select_cases',lambda *a:[case])
    monkeypatch.setattr(evaluator,'checked_tool',lambda *a:{'executable':'flare.exe','version':'0.6','artifact_sha256':{'flare.exe':'pin'}})
    monkeypatch.setattr(evaluator,'perform',lambda *a:generated)
    result=evaluator.evaluate(tmp_path,tmp_path/'report')
    row=result['cases'][0]
    assert Path(row['source_file']).read_bytes()==body.encode('utf-8')
    assert row['source_sha256']==row['source_placement']['source_sha256']
