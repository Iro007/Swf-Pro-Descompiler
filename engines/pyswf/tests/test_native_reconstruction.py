"""Source structure AND bytecode behavior, independent of source fixtures."""
import math
from pathlib import Path

import pytest

from py_swf.avm1 import assemble_avm1, parse_push_values, serialize_push_value
from py_swf.avm2 import ABCFile, MethodInfo, MethodBodyInfo, assemble_instructions, build_method_mapping
from py_swf.compiler import compile_actionscript
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.swf_parser import SWFFile
from tests.conftest import JPEXS_TESTDATA
from tests.native_control_vm import run_bytes, run_source, FlashError, DefinitionError, Thrown
from tests.test_native_control_flow import CASES


def native_source(text, target):
    compiled = compile_actionscript(text, target)
    if target == "as2":
        return decompile_avm1(assemble_avm1(compiled))
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, compiled, exception_table=body.exceptions)
    return decompile_method(abc, body)


@pytest.mark.parametrize("target", ["as2", "as3"])
@pytest.mark.parametrize("text,feature", [
    ('for(var n=0; n<4; n++) {trace(n);} trace("end");', "for"),
    ('for(var n=0; n<4; n++) {if(n==1) continue; if(n==3) break; trace(n);} trace("end");', "for"),
    ('var n=2; switch(n) {case 1: trace("one"); break; case 2: trace("two"); case 3: trace("three"); break; default: trace("other");} trace("end");', "switch"),
    ('try {trace("body");} catch(e) {trace(e);} finally {trace("finally");} trace("end");', "try"),
    ('try {throw "caught";} catch(e) {trace(e);} finally {trace("finally");} trace("end");', "try"),
    ('try {return 7;} finally {trace("finally");} trace("end");', "try"),
    ('try {return 1;} finally {return 2;} trace("end");', "try"),
    ('for(var n=0; n<3; n++) {try {if(n==0) continue; if(n==1) break;} finally {trace(n);}} trace("end");', "try"),
])
def test_native_structures_recompile_with_same_behavior(target, text, feature):
    reconstructed, error = native_source(text, target)
    assert error is None, error
    assert feature + (" {" if feature == "try" else " (") in reconstructed
    before = run_source(text, target)
    after = run_source(reconstructed, target)
    assert (after.traces, after.value, after.error, after.raised) == (before.traces, before.value, before.error, before.raised)
    assert reconstructed.count('trace("end")') <= 1


def test_real_avm1_double_word_order_not_only_self_roundtrip():
    assert parse_push_values(bytes.fromhex("06 00 00 f0 3f 00 00 00 00")) == ["1.0"]
    assert serialize_push_value("1.0") == bytes.fromhex("06 00 00 f0 3f 00 00 00 00")
    assert math.isinf(float(parse_push_values(bytes.fromhex("06 00 00 f0 7f 00 00 00 00"))[0]))


def test_nullable_string_cast_does_not_become_String_null():
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, "pushnull\ncoerce_s\nreturnvalue")
    reconstructed, error = decompile_method(abc, body)
    assert error is None
    assert reconstructed == "return null;"


def test_unknown_call_receiver_fails_closed():
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, "getlocal_1\ngetlocal_0\ncall 0\nreturnvalue")
    reconstructed, error = decompile_method(abc, body)
    assert reconstructed is None and "receiver" in error


def test_avm2_construct_reconstructs_new_expression():
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(
        abc.constant_pool,
        'findpropstrict RegExp\ngetproperty RegExp\npushstring "pattern"\npushstring "i"\nconstruct 2\nreturnvalue'
    )
    reconstructed, error = decompile_method(abc, body)
    assert error is None, error
    assert 'return new RegExp("pattern", "i");' in reconstructed


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local SWF corpus unavailable")
def test_real_as3_construct_regexp():
    path = JPEXS_TESTDATA / "as3_new/bin/as3_new.flex.swf"
    abc, body = corpus_method(path, "TestRegExp.run")
    reconstructed, error = decompile_method(abc, body)
    assert error is None, error
    assert 'var a1 = new RegExp(' in reconstructed
    assert 'var b1 = new RegExp("[0-9AB]+");' in reconstructed


def test_avm2_construct_private_fails_closed():
    from py_swf.decompile.ir import Value, Block, Instruction, ReconstructionError
    from py_swf.decompile.lift import Lifter, State
    from py_swf.symbols import NameIdentity, NamespaceIdentity

    lifter = Lifter("as3")
    private_id = NameIdentity(7, "PrivateCls", (NamespaceIdentity(5, "private_ns"),), 1)
    block = Block(0, [Instruction(0, 1, "construct", (0,))])
    callee = Value("name", "PrivateCls", binding=private_id)
    with pytest.raises(ReconstructionError) as exc_info:
        lifter.lift(block, state=State(stack=(callee,)))
    assert "Private constructor call needs explicit receiver semantics" in str(exc_info.value)



@pytest.mark.parametrize("target", ["as2", "as3"])
@pytest.mark.parametrize("text,traces,value", CASES)
def test_all_supported_native_control_cases_survive_source_roundtrip(target, text, traces, value):
    reconstructed, error = native_source(text, target)
    assert error is None, error
    before, after = run_source(text, target), run_source(reconstructed, target)
    assert (after.traces, after.value, after.error, after.raised) == (before.traces, before.value, before.error, before.raised)


def corpus_method(path, match):
    movie = SWFFile()
    movie.read_bytes(path.read_bytes())
    for tag in movie.tags:
        if tag.is_doabc:
            abc = ABCFile()
            abc.parse(tag.parse_doabc()[2])
            names = build_method_mapping(abc)
            for body in abc.method_bodies:
                if names.get(body.method, "").endswith(match):
                    return abc, body
    raise AssertionError(f"Real method {match} not found in {path}")


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local SWF corpus unavailable")
@pytest.mark.parametrize("test,feature", [("TestFor", "for"), ("TestSwitch", "switch"), ("TestSwitchDefault", "switch")])
def test_real_as3_control_structure(test, feature):
    path = JPEXS_TESTDATA / "as3_new/bin/as3_new.flex.swf"
    abc, body = corpus_method(path, test + ".run")
    reconstructed, error = decompile_method(abc, body)
    assert error is None, error
    assert feature + " (" in reconstructed
    if test == "TestFor":
        assert 'trace(("a=" + a))' in reconstructed and "(a < 10)" in reconstructed
    else:
        assert 'case (57 * a):' in reconstructed
        assert 'case 13:' in reconstructed and 'case 14:' in reconstructed
        fall = reconstructed.split('case 13:', 1)[1].split('case 14:', 1)[0]
        assert 'break;' not in fall


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local SWF corpus unavailable")
@pytest.mark.parametrize("tag_index,feature", [(261, "for"), (271, "for"), (283, "switch"), (289, "switch")])
def test_real_as2_control_structure(tag_index, feature):
    movie = SWFFile()
    movie.read_bytes((JPEXS_TESTDATA / "as2/as2.swf").read_bytes())
    reconstructed, error = decompile_avm1(movie.tags[tag_index].data)
    assert error is None, error
    assert feature + " (" in reconstructed
    if tag_index == 283:
        assert reconstructed.index('case 2:') < reconstructed.index('case 3:')
        assert 'trace("scriptend")' in reconstructed
        assert reconstructed.count('trace("scriptend")') == 1


def compile_bytes(text, target):
    compiled = compile_actionscript(text, target)
    if target == "as2":
        return assemble_avm1(compiled), None, None
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, compiled, exception_table=body.exceptions)
    return body.code, abc, body


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local SWF corpus unavailable")
@pytest.mark.parametrize("variant", ["flex", "air"])
@pytest.mark.parametrize("test", ["TestFor", "TestForBreak", "TestForContinue", "TestSwitch", "TestSwitchDefault"])
def test_real_as3_bytecode_behavior_equals_recompiled_source(test, variant):
    path = JPEXS_TESTDATA / f"as3_new/bin/as3_new.{variant}.swf"
    abc, body = corpus_method(path, test + ".run")
    text, error = decompile_method(abc, body)
    assert error is None, error
    new_code, new_abc, new_body = compile_bytes(text, "as3")
    before = run_bytes(body.code, "as3", abc=abc, body=body)
    after = run_bytes(new_code, "as3", abc=new_abc, body=new_body)
    assert not before.raised and not after.raised
    assert (before.traces, before.value) == (after.traces, after.value)


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local SWF corpus unavailable")
@pytest.mark.parametrize("test", ["TestTryCatch", "TestTryFinally", "TestTryFinallyReturn", "TestTryFinallyNoCatch"])
@pytest.mark.parametrize("inject", [False, True])
def test_real_as3_exception_behavior_equals_recompiled_source(test, inject):
    path = JPEXS_TESTDATA / "as3_cross_compile/bin/as3_cross_compile.flex.swf"
    abc, body = corpus_method(path, test + ".run")
    text, error = decompile_method(abc, body)
    assert error is None, error
    assert "try {" in text
    if "Finally" in test:
        assert "finally {" in text
    new_code, new_abc, new_body = compile_bytes(text, "as3")
    injected = FlashError("injected")
    def variables(events):
        def trace(value):
            events.append(value)
            if inject and value == "in try":
                raise Thrown(injected)
        return {"trace": trace}
    before_events, after_events = [], []
    before = run_bytes(body.code, "as3", abc=abc, body=body, variables=variables(before_events))
    after = run_bytes(new_code, "as3", abc=new_abc, body=new_body, variables=variables(after_events))
    assert (before_events, before.value, before.error, before.raised) == (after_events, after.value, after.error, after.raised)


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local SWF corpus unavailable")
@pytest.mark.parametrize("tag_index", [261, 271, 283, 289, 317])
def test_real_as2_bytecode_behavior_equals_recompiled_source(tag_index):
    movie = SWFFile()
    movie.read_bytes((JPEXS_TESTDATA / "as2/as2.swf").read_bytes())
    code = movie.tags[tag_index].data
    text, error = decompile_avm1(code)
    assert error is None, error
    before = run_bytes(code, "as2")
    after = run_bytes(compile_bytes(text, "as2")[0], "as2")
    assert (before.traces, before.value, before.error, before.raised) == (after.traces, after.value, after.error, after.raised)
