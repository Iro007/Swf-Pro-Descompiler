"""Behavioral and persistence regressions for for/switch/exception compilation."""

import pytest
from fastapi.testclient import TestClient

from py_swf.avm1 import assemble_avm1, disassemble_avm1, parse_try_header
from py_swf.avm2 import ABCFile, assemble_instructions, resolve_multiname
from py_swf.bytecode_validation import analyze_method, validate_avm1, BytecodeValidationError
from py_swf.compiler import compile_actionscript
from py_swf.editing import edit_script
from py_swf.engines import NativePythonEngine
from py_swf.project import SwfProject
from py_swf.storage import write_swf
from py_swf.swf_parser import SWFFile, SWFTag
from server.app import app
from tests.native_control_vm import run_source, run_bytes, Thrown
from tests.native_fixtures import as3_tag, swf_with


CASES = [
    ('for(var i:int=0; i<4; i++) { if(i==1) continue; trace(i); }', [0, 2, 3], None),
    ('var n=0; for(;;) { trace(n++); if(n==2) break; }', [0, 1], None),
    ('for(var i=0, j=3; i<3; ++i, j-=1) trace(i+j);', [3, 3, 3], None),
    ('var n=1; switch(n++) {case 1: trace(n); case 2: trace("fall"); break; default: trace("wrong");}', [2, "fall"], None),
    ('switch(2) {default: trace("wrong"); case 1: trace("wrong"); break; case 2: trace("two");}', ["two"], None),
    ('switch(9) {case 1: trace("wrong"); break; default: trace("default"); case 2: trace("fall");}', ["default", "fall"], None),
    ('switch(9) {case 1: trace("wrong");} trace("end");', ["end"], None),
    ('var n=0; switch(0) {case n++: trace(n); break; case n++: trace("wrong");} trace(n);', [1, 1], None),
    ('switch(1) {case "1": trace("wrong"); break; default: trace("strict");}', ["strict"], None),
    ('for(var i=0; i<3; i++) {switch(i) {case 0: continue; case 1: break; default: trace(i);} trace("loop");}', ["loop", 2, "loop"], None),
    ('try {trace("try");} catch(e) {trace("wrong");} finally {trace("finally");}', ["try", "finally"], None),
    ('try {throw "caught";} catch(e) {trace(e);} finally {trace("finally");}', ["caught", "finally"], None),
    ('try {explode(); trace("wrong");} catch(e) {trace(e);} finally {trace("finally");}', ["boom", "finally"], None),
    ('var n=1; try {return n++;} finally {trace(n); n=99;}', [2], 1),
    ('try {return 1;} finally {return 2;}', [], 2),
    ('try {throw "old";} finally {return "replacement";}', [], "replacement"),
    ('try {try {return 1;} finally {throw "new";}} catch(e) {trace(e);} trace("end");', ["new", "end"], None),
    ('try {try {throw "old";} catch(e) {throw "new";} finally {trace("inner");}} catch(e) {trace(e);} finally {trace("outer");}', ["inner", "new", "outer"], None),
    ('try {try {return 7;} finally {trace("inner");}} finally {trace("outer");}', ["inner", "outer"], 7),
    ('for(var i=0; i<3; i++) {try {if(i==0) continue; if(i==1) break;} finally {trace(i);}} trace("end");', [0, 1, "end"], None),
    ('try {for(var i=0; i<3; i++) {if(i==0) continue; trace(i); break;} trace("body");} finally {trace("finally");}', [1, "body", "finally"], None),
    ('for(var i=0; i<2; i++) {try {return 1;} finally {continue;}} return 2;', [], 2),
    ('while(true) {try {return 1;} finally {break;}} return 2;', [], 2),
    ('var e="outer"; try {throw "inner";} catch(e) {trace(e);} trace(e);', ["inner", "outer"], None),
    ('try {throw "outer";} catch(e) {try {throw "inner";} catch(e) {trace(e);} trace(e);} finally {trace("end");}', ["inner", "outer", "end"], None),
    ('try {switch(1) {case 1: break;} trace("body");} finally {trace("end");}', ["body", "end"], None),
    ('try {return 1;} catch(e) {trace("wrong");} finally {trace("end");}', ["end"], 1),
    ('try {} finally {} trace("empty");', ["empty"], None),
    ('var e:String="outer"; try {throw 1;} catch(e) {trace(e++); trace(e);} trace(e);', [1, 2, "outer"], None),
    ('try {return explode();} catch(e) {trace(e); return 7;} finally {trace("end");}', ["boom", "end"], 7),
    ('for(var i=0; i<2; i++) {try {switch(i) {case 0: continue; default: break;} trace("body");} finally {trace(i);}}', [0, "body", 1], None),
]


@pytest.mark.parametrize("target", ["as2", "as3"])
@pytest.mark.parametrize("source,traces,value", CASES)
def test_structured_bytecode_semantics(target, source, traces, value):
    result = run_source(source, target)
    assert not result.raised, result.error
    assert result.traces == traces
    assert result.value == value


@pytest.mark.parametrize("target", ["as2", "as3"])
def test_uncaught_exception_runs_finally_once(target):
    result = run_source('try {throw "uncaught";} finally {trace("once");}', target)
    assert result.raised and result.error == "uncaught"
    assert result.traces == ["once"]


def test_ordered_as3_typed_catches_and_no_sibling_recapture():
    source = 'try {throw "s";} catch(e:Number) {trace("wrong");} catch(e:String) {trace(e); throw "second";} catch(e:*) {trace("wrong");} finally {trace("end");}'
    result = run_source(source, "as3")
    assert result.raised and result.error == "second"
    assert result.traces == ["s", "end"]


@pytest.mark.parametrize("target", ["as2", "as3"])
def test_error_class_catch(target):
    source = 'try {explode();} catch(e:TypeError) {trace("wrong");} catch(e:Error) {trace("caught");} finally {trace("end");}'
    compiled = compile_actionscript(source, target)
    def explode():
        raise Thrown(ValueError("real error object"))
    if target == "as2":
        result = run_bytes(assemble_avm1(compiled), target, variables={"explode": explode})
    else:
        tag, _ = edit_script(as3_tag(), source, 1, source=True)
        abc = ABCFile()
        abc.parse(tag.parse_doabc()[2])
        body = abc.method_bodies[1]
        result = run_bytes(body.code, target, abc=abc, body=body, variables={"explode": explode})
    assert not result.raised
    assert result.traces == ["caught", "end"]


@pytest.mark.parametrize("source", [
    'for(var i=0 i<3; i++) {}', 'for each(var x=0;x<3;x++) {}', 'for(var x,y in z) {}',
    'switch(1) {default: break; default: break;}', 'switch(1) {trace(1);}',
    'try {}', 'try trace(1); finally {}', 'catch(e) {}', 'finally {}',
    'try {} catch(e:*) {} catch(f:Error) {}', 'try {} catch() {}',
    'switch(1) {case 1: continue;}',
])
def test_invalid_control_flow_is_explicitly_rejected(source):
    with pytest.raises((ValueError, SyntaxError)):
        compile_actionscript(source)


def test_as3_assembly_cannot_silently_drop_exception_table():
    compiled = compile_actionscript('try {throw "s";} catch(e:String) {trace(e);} finally {}')
    with pytest.raises(ValueError, match="exception_table"):
        assemble_instructions(ABCFile().constant_pool, compiled)
    with pytest.raises(ValueError, match="exception_table"):
        assemble_instructions(ABCFile().constant_pool, str(compiled))


def test_old_swf_cannot_silently_receive_swf7_exception_actions():
    original = swf_with([SWFTag(12, assemble_avm1("end"))])
    original = original[:3] + bytes([6]) + original[4:]
    source = 'try {trace("ok");} finally {}'
    with pytest.raises(ValueError, match="SWF version 7"):
        NativePythonEngine().compile_source(original, 0, source)
    client = TestClient(app)
    sid = client.post("/api/files", files={"file": ("old.swf", original)}).json()["session_id"]
    response = client.post(f"/api/files/{sid}/tags/0/compile-source", json={"source": source})
    assert response.status_code == 422 and "version 7" in response.text
    assert client.get(f"/api/files/{sid}/download").content == original
    assert client.get(f"/api/files/{sid}/history").json()["undo"] == 0


def test_as3_exception_pcode_roundtrip_remaps_offsets_after_edit():
    tag, _ = edit_script(as3_tag(), 'try {throw "caught";} catch(e:String) {trace(e);} finally {trace("end");} return 9;', 1, source=True)
    abc = ABCFile()
    abc.parse(tag.parse_doabc()[2])
    body = abc.method_bodies[1]
    from py_swf.avm2 import disassemble_instructions
    code = disassemble_instructions(abc.constant_pool, body.code, exact=True, exceptions=body.exceptions)
    exceptions = []
    assert assemble_instructions(abc.constant_pool, code, exception_table=exceptions) == body.code
    assert [vars(e) for e in exceptions] == [vars(e) for e in body.exceptions]
    changed, _ = edit_script(tag, 'nop\n' + code, 1)
    reopened = ABCFile()
    reopened.parse(changed.parse_doabc()[2])
    new_body = reopened.method_bodies[1]
    assert [e.target_offset for e in new_body.exceptions] == [e.target_offset + 1 for e in body.exceptions]
    result = run_bytes(new_body.code, "as3", abc=reopened, body=new_body)
    assert result.traces == ["caught", "end"] and result.value == 9


def test_as3_exception_metadata_types_and_bounds_are_validated():
    tag, _ = edit_script(as3_tag(), 'try {} catch(e:flash.errors.IOError) {}', 1, source=True)
    abc = ABCFile()
    abc.parse(tag.parse_doabc()[2])
    body = abc.method_bodies[1]
    assert resolve_multiname(abc.constant_pool, body.exceptions[0].exc_type) == "flash.errors::IOError"
    body.exceptions[0].exc_type = len(abc.constant_pool.multinames)
    with pytest.raises(BytecodeValidationError, match="exception type"):
        analyze_method(abc, body)


def test_void_return_and_typed_return_capture_across_finally():
    result = run_source('try {return;} finally {trace("end");}', "as3", return_type="void")
    assert result.traces == ["end"] and result.value is None
    result = run_source('var n=1.8; try {return n;} finally {n=5;}', "as3", return_type="int")
    assert result.value == 1
    with pytest.raises(ValueError, match="void"):
        compile_actionscript('try {return 1;} finally {}', return_type="void")


def test_avm1_try_exact_disassembly_roundtrip_and_size_validation():
    compiled = compile_actionscript('try {try {throw "e";} finally {trace("f");}} catch(e) {trace(e);}', "as2")
    code = assemble_avm1(compiled)
    validate_avm1(code)
    assert "try 1" in disassemble_avm1(code)
    assert assemble_avm1(disassemble_avm1(code)) == code
    with pytest.raises(ValueError):
        parse_try_header(b"\x01\x00")
    with pytest.raises(BytecodeValidationError, match="Truncated"):
        validate_avm1(b"\x8f\x08\x00\x01\xff\x7f\x00\x00\x00\x00\x00")


SOURCE = 'var n=0; for(var i=0; i<3; i++) {try {switch(i) {case 0: continue; case 1: throw "caught"; default: n+=2;}} catch(e) {trace(e);} finally {n++;}} return n;'


@pytest.mark.parametrize("tag_type", [12, 59, 72, 82])
@pytest.mark.parametrize("signature", ["FWS", "CWS", "ZWS"])
def test_extended_edits_atomic_save_reopen_project_and_second_edit(tag_type, signature, tmp_path, monkeypatch):
    from py_swf.decompile.backends import JPEXSBackend
    monkeypatch.setattr(JPEXSBackend, "decompile", lambda *a, **kw: pytest.fail("Java/JPEXS must not run"))
    target, index = ("as2", 0) if tag_type in (12, 59) else ("as3", 1)
    tag = SWFTag(tag_type, (b"\x07\0" if tag_type == 59 else b"") + assemble_avm1("end")) if target == "as2" else as3_tag(tag_type)
    original = swf_with([tag], signature)
    edited = NativePythonEngine().compile_source(original, 0, SOURCE, body_index=index)
    path = tmp_path / "saved.swf"
    write_swf(path, edited)
    swf = SWFFile()
    swf.read_bytes(path.read_bytes())
    assert swf.signature == signature and swf.tags[0].tag_type == tag_type
    if target == "as2":
        code = swf.tags[0].data[2 if tag_type == 59 else 0:]
        validate_avm1(code)
        result = run_bytes(code, target)
    else:
        abc = ABCFile()
        abc.parse(swf.tags[0].parse_doabc()[2])
        body = abc.method_bodies[1]
        assert body.exceptions
        analyze_method(abc, body)
        result = run_bytes(body.code, target, abc=abc, body=body)
    assert result.traces == ["caught"] and result.value == 5 and not result.raised
    project = SwfProject("original.swf", original, edited)
    project.snapshot("extended syntax")
    reopened = SwfProject.from_archive(project.to_archive(tmp_path / "saved.pyswfproj"))
    assert reopened.original == original and reopened.working == edited
    # Generated exception tables must not make subsequent source edits impossible.
    again = NativePythonEngine().compile_source(edited, 0, 'return 42;', body_index=index)
    second = SWFFile()
    second.read_bytes(again)
    if target == "as3":
        abc = ABCFile()
        abc.parse(second.tags[0].parse_doabc()[2])
        assert not abc.method_bodies[1].exceptions


@pytest.mark.parametrize("target", ["as2", "as3"])
def test_http_edit_history_export_reopen_and_error_rollback(target):
    client = TestClient(app)
    tag = SWFTag(12, assemble_avm1("end")) if target == "as2" else as3_tag()
    original = swf_with([tag], "CWS")
    sid = client.post("/api/files", files={"file": ("input.swf", original)}).json()["session_id"]
    endpoint = f"/api/files/{sid}"
    index = 0 if target == "as2" else 1
    response = client.post(endpoint + "/tags/0/compile-source", json={"source": SOURCE, "body_index": index})
    assert response.status_code == 200, response.text
    assert response.json()["engine"] == "native"
    sections = client.get(endpoint + "/tags/0/decompile").json()["sections"]
    section = next(s for s in sections if s.get("body_index") == index)
    assert section["editable_source"] is True
    assert section["reconstruction"] == "structured"
    assert section["replaceable_source"] is True
    saved = client.get(endpoint + "/download").content
    reopened = client.post("/api/files", files={"file": ("saved.swf", saved)})
    assert reopened.status_code == 200, reopened.text
    assert client.post(endpoint + "/undo").status_code == 200
    assert client.get(endpoint + "/download").content == original
    assert client.post(endpoint + "/redo").status_code == 200
    assert client.get(endpoint + "/download").content == saved
    bad = client.post(endpoint + "/tags/0/compile-source", json={"source": 'try {}', "body_index": index})
    assert bad.status_code == 422
    assert client.get(endpoint + "/download").content == saved
