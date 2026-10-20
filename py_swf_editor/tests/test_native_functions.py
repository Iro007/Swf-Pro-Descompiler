"""Closures, dynamic scopes and bounded reconstruction regressions."""
import pytest

from py_swf.avm1 import assemble_avm1, disassemble_avm1
from py_swf.bytecode_validation import validate_avm1, BytecodeValidationError
from py_swf.avm2 import ABCFile, MethodInfo, MethodBodyInfo, TraitsInfo, assemble_instructions, search_or_add_multiname
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.decompile.backends import NativeBackend
from py_swf.swf_parser import SWFFile
from tests.conftest import JPEXS_TESTDATA
from tests.native_control_vm import run_bytes, run_source
from tests.native_source_subset import NODE, run_trusted_subset
from tests.test_native_reconstruction import corpus_method


def closure_fixture():
    abc = ABCFile()
    abc.methods = [MethodInfo(), MethodInfo()]
    outer, inner = MethodBodyInfo(), MethodBodyInfo()
    inner.method, inner.init_scope_depth = 1, 2
    for slot, text, typ in [(1, "counter", "int"), (2, "readCounter", "Function")]:
        trait = TraitsInfo()
        trait.slot_id = slot
        trait.name = search_or_add_multiname(abc.constant_pool, text)
        trait.type_name = search_or_add_multiname(abc.constant_pool, typ)
        outer.traits.append(trait)
    outer.code = assemble_instructions(abc.constant_pool, "getlocal_0\npushscope\nnewactivation\ndup\nsetlocal_1\npushscope\ngetscopeobject 1\npushbyte 1\nsetslot 1\ngetscopeobject 1\nnewfunction 1\nsetslot 2\ngetscopeobject 1\npushbyte 7\nsetslot 1\ngetscopeobject 1\ngetslot 2\ngetglobalscope\ncall 0\nreturnvalue")
    inner.code = assemble_instructions(abc.constant_pool, "getouterscope 1\ngetslot 1\nreturnvalue")
    abc.method_bodies = [outer, inner]
    return abc, outer, inner


@pytest.mark.skipif(NODE is None, reason="Independent JS subset runtime unavailable")
def test_closure_reads_live_slot_after_mutation():
    abc, outer, _ = closure_fixture()
    text, error = decompile_method(abc, outer)
    assert error is None, error
    assert "function()" in text and "return counter;" in text
    assert run_bytes(outer.code, "as3", abc=abc, body=outer).value == 7
    assert run_trusted_subset(text)["value"] == 7


def test_recursive_newfunction_graph_is_bounded():
    abc, outer, inner = closure_fixture()
    inner.code = assemble_instructions(abc.constant_pool, "newfunction 0\nreturnvalue")
    text, error = decompile_method(abc, outer)
    assert text is None and "limit" in error


def test_shadowed_capture_fails_closed_instead_of_reading_parameter():
    abc, outer, inner = closure_fixture()
    abc.methods[1].param_types = [0]
    index = len(abc.constant_pool.strings)
    abc.constant_pool.strings.append("counter")
    abc.methods[1].param_names = [index]
    text, error = decompile_method(abc, outer)
    assert text is None and "Shadowed" in error


@pytest.mark.parametrize("variant", ["flex", "air"])
@pytest.mark.parametrize("method,args,expected", [("TestInnerFunctions.run", ["hello"], ["hello"]), ("TestInlineFunctions.run", [], ["value2cc", "hello,hello"]), ("TestInlineFunctions2.run", [], [])])
@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir() or NODE is None, reason="Local corpus/JS subset runtime unavailable")
def test_real_as3_nested_closures_keep_bytecode_behavior(variant, method, args, expected):
    path = JPEXS_TESTDATA / f"as3_new/bin/as3_new.{variant}.swf"
    abc, body = corpus_method(path, method)
    text, error = decompile_method(abc, body)
    assert error is None, error
    original = run_bytes(body.code, "as3", abc=abc, body=body, arguments=args)
    # The test function's source parameters come from debug metadata, so bind
    # the outer parameter separately when wrapping a method body for V8.
    reconstructed = run_trusted_subset(text, variables={"a": args[0]} if args else None)
    assert original.traces == reconstructed["traces"] == expected
    assert not original.raised


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir() or NODE is None, reason="Local corpus/JS subset runtime unavailable")
@pytest.mark.parametrize("tag", [292, 544])
def test_real_as2_functions_and_nested_with(tag):
    movie = SWFFile()
    data = (JPEXS_TESTDATA / "as2/as2.swf").read_bytes()
    movie.read_bytes(data)
    text, error = decompile_avm1(movie.tags[tag].data)
    assert error is None, error
    def env():
        return {"_root": {"something": {"somesub": 0, "subvar": {"somesub2": 0}}}}
    variables = env()
    before = run_bytes(movie.tags[tag].data, "as2", variables=variables)
    after = run_trusted_subset(text, variables=env())
    assert before.traces == after["traces"]
    if tag == 292:
        assert before.traces == ["functionTest", "hello friend! 7"]
    else:
        assert variables["_root"] == after["root"]
        assert variables["_root"]["something"]["subvar"]["somesub2"] == 4
    section = NativeBackend().decompile(movie.tags[tag], data, "as2.swf").sections[0]
    assert section["reconstruction"] == "structured"
    if tag == 292:
        assert section["editable_source"]
        recompiled=run_source(text,"as2")
        assert not recompiled.raised and recompiled.traces==before.traces
    else:
        assert not section["editable_source"]  # dynamic with scopes remain protected


def test_avm1_call_frame_opcode_roundtrip_and_empty_payload_are_exact():
    code=assemble_avm1('push 5\ncall\nend')
    assert code==bytes.fromhex('96050007050000009e000000')
    validate_avm1(code)
    assert '    call' in disassemble_avm1(code)
    assert assemble_avm1(disassemble_avm1(code))==code
    assert assemble_avm1('push 5\nActionCall\nend')==code
    with pytest.raises(ValueError,match='no record operands'):
        assemble_avm1('call 5\nend')
    with pytest.raises(BytecodeValidationError,match='empty payload'):
        validate_avm1(bytes.fromhex('9e01000400'))
    with pytest.raises(BytecodeValidationError,match='Truncated'):
        validate_avm1(bytes.fromhex('9e020004'))
    with pytest.raises(BytecodeValidationError,match='Unknown AVM1'):
        validate_avm1(bytes.fromhex('7f00'))


@pytest.mark.skipif(NODE is None, reason="Independent JS subset runtime unavailable")
def test_as3_with_lookup_remains_dynamic_and_object_is_created_once():
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, 'pushstring "x"\npushbyte 4\nnewobject 1\npushwith\nfindproperty x\nfindpropstrict x\ngetproperty x\npushbyte 1\nadd\nsetproperty x\nfindpropstrict trace\nfindpropstrict x\ngetproperty x\ncallpropvoid trace 1\npopscope\nreturnvoid')
    text, error = decompile_method(abc, body)
    assert error is None, error
    assert "with (" in text and "var x" not in text
    assert text.count('{"x": 4}') == 1
    assert run_bytes(body.code, "as3", abc=abc, body=body).traces == run_trusted_subset(text)["traces"] == [5]


@pytest.mark.parametrize("variant", ["flex", "air"])
@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local corpus unavailable")
def test_real_bottom_test_loop_recompiles_without_reordering(variant):
    abc, body = corpus_method(JPEXS_TESTDATA / f"as3_new/bin/as3_new.{variant}.swf", "TestDoWhile.run")
    text, error = decompile_method(abc, body)
    assert error is None, error
    before = run_bytes(body.code, "as3", abc=abc, body=body)
    after = run_source(text, "as3")
    assert before.traces == after.traces == [f"a={i}" for i in range(8, 20)]


def test_invalid_inline_function_body_is_not_flattened():
    text, error = decompile_avm1(bytes.fromhex("9b0500000000080000"))
    assert error is not None and "Truncated" in error


def test_typed_nullable_string_coercion_remains_null_not_text():
    abc, body = ABCFile(), MethodBodyInfo()
    method = MethodInfo()
    method.param_types = [search_or_add_multiname(abc.constant_pool, "String")]
    abc.methods = [method]
    body.code = assemble_instructions(abc.constant_pool, "getlocal_1\ncoerce_s\nreturnvalue")
    text, error = decompile_method(abc, body)
    assert error is None and text == "return local1;"


@pytest.mark.parametrize("target", ["as2", "as3"])
@pytest.mark.parametrize("expression", ["false && 7", "true && 7", "false || 9", "true || 9", "(false || 2) && 3"])
def test_pure_expression_merges_preserve_short_circuit_values(target, expression):
    from tests.test_native_reconstruction import native_source
    original = "var result = " + expression + "; trace(result); return result;"
    text, error = native_source(original, target)
    assert error is None, error
    before, after = run_source(original, target), run_source(text, target)
    assert (before.traces, before.value) == (after.traces, after.value)


@pytest.mark.skipif(NODE is None, reason="Independent JS subset runtime unavailable")
def test_avm1_array_evaluation_order_is_not_reversed():
    code = assemble_avm1('push 2 1 "event"\ncall_function\npush 1 1 "event"\ncall_function\npush 2\ninit_array\nreturn')
    text, error = decompile_avm1(code)
    assert error is None, error
    events = []
    def event(value):
        events.append(value)
        return value
    before = run_bytes(code, "as2", variables={"event": event})
    after = run_trusted_subset('function event(v) {trace(v); return v;}\n' + text)
    assert before.value == after["value"] == [1, 2]
    assert events == after["traces"] == [2, 1]


@pytest.mark.skipif(NODE is None, reason="Independent JS subset runtime unavailable")
def test_avm1_duplicate_object_key_preserves_overwrite_order():
    code = assemble_avm1('push "x" 1 "x" 2 2\ninit_object\nreturn')
    text, error = decompile_avm1(code)
    assert error is None, error
    assert run_bytes(code, "as2").value == run_trusted_subset(text)["value"] == {"x": 1}


@pytest.mark.parametrize("variant", ["flex", "air"])
@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local corpus unavailable")
def test_real_for_each_selects_values_not_keys(variant):
    abc, body = corpus_method(JPEXS_TESTDATA / f"as3_new/bin/as3_new.{variant}.swf", "TestForEach.run")
    text, error = decompile_method(abc, body)
    assert error is None, error
    assert "for each (" in text and "list[0]" in text
    original = run_bytes(body.code, "as3", abc=abc, body=body, variables={"Array": lambda *v: list(v), "undefined": None})
    assert original.traces == ["item #first", "item #second", "item #third"]


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir() or NODE is None, reason="Local corpus/JS subset runtime unavailable")
def test_real_pcode_edits_export_and_reopen_without_touching_originals(tmp_path):
    from tools.verify_native_functions import verify
    report = verify(JPEXS_TESTDATA, tmp_path)
    assert len(report["semantic_cases"]) == 6
    assert len(report["real_pcode_edits"]) == 2
    assert all(r["saved_reopened"] and r["original_preserved"] and r["other_tags_preserved"] for r in report["real_pcode_edits"])
    as2,as3=report['real_pcode_edits']
    assert as2['baseline_validation_valid'] and not as2['source_snapshot_isolated']
    assert as2['other_tags_scope']=='complete original SWF'
    assert len(as3['original_sha256'])==64
    if not as3['baseline_validation_valid']:
        assert as3['baseline_status']=='strict validator rejects current real SWF'
        assert as3['full_source_export_rejected'] and as3['source_snapshot_isolated']
        assert as3['other_tags_scope']=='isolated ABC library fixture'
        assert as3['baseline_errors'] and as3['full_source_export_error']
        assert as3['edit_input_sha256']!=as3['original_sha256']
