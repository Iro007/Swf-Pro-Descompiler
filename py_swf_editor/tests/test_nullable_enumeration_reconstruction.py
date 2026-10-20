"""Nullable AVM2 String enumeration bindings retain their exact conversion."""
from pathlib import Path

import pytest

from py_swf.avm2 import ABCFile, MethodBodyInfo, MethodInfo, assemble_instructions, disassemble_instructions, search_or_add_multiname
from py_swf.bytecode_validation import analyze_method, decode_avm2
from py_swf.compiler import compile_actionscript
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.script_inventory import enumerate_scripts
from tests.native_control_vm import run_bytes
from tests.test_native_reconstruction_improvements import abrupt_enumeration


def rebuild(source):
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, compile_actionscript(source, "as3"))
    limits = analyze_method(abc, body)
    body.max_stack, body.local_count, body.max_scope_depth = limits.max_stack, limits.local_count, limits.max_scope_depth
    return abc, body


@pytest.mark.parametrize("op", ["nextname", "nextvalue"])
@pytest.mark.parametrize("repeated", [False, True])
@pytest.mark.parametrize("value", [None, "literal", 11.9, False])
def test_nullable_binding_roundtrip_preserves_null_and_conversion(op, repeated, value):
    abc, body = abrupt_enumeration(op=op, conversions="coerce_s\n", repeated=repeated)
    data = {value: value}
    source, error = decompile_method(abc, body)
    assert error is None, error
    assert ":String in " in source and "String(" not in source
    # The destination assignment stays in the body, after the typed header.
    assert "local3 = $value" in source
    new_abc, new_body = rebuild(source)
    original = run_bytes(body.code, "as3", abc=abc, body=body, variables={"data": data})
    actual = run_bytes(new_body.code, "as3", abc=new_abc, body=new_body, variables={"data": data})
    assert (actual.traces, actual.value, actual.error, actual.raised) == (
        original.traces, original.value, original.error, original.raised)
    assert "coerce_s" in disassemble_instructions(new_abc.constant_pool, new_body.code)
    if value is None:
        assert (actual.traces[0] if repeated else actual.value) is None


def test_empty_nullable_enumeration_preserves_existing_destination():
    abc, body = abrupt_enumeration(conversions="coerce_s\n", tail="kill 1\nkill 2\ngetlocal_3\nreturnvalue")
    prefix = assemble_instructions(abc.constant_pool, 'pushstring "keep"\nsetlocal_3')
    # Prepending a straight-line initialization preserves every relative jump.
    body.code = prefix + body.code
    source, error = decompile_method(abc, body)
    assert error is None, error
    new_abc, new_body = rebuild(source)
    for data, expected in [({}, "keep"), ({"one": None}, None)]:
        before = run_bytes(body.code, "as3", abc=abc, body=body, variables={"data": data})
        after = run_bytes(new_body.code, "as3", abc=new_abc, body=new_body, variables={"data": data})
        assert before.value == after.value == expected


def test_undefined_enumerated_value_retains_nullable_coerce_s_opcode():
    abc, body = abrupt_enumeration(conversions="coerce_s\n")
    body.code = assemble_instructions(abc.constant_pool,
        'pushbyte 0\nsetlocal_2\npushstring "first"\npushundefined\nnewobject 1\nsetlocal_1\n'
        'jump Check\nBody:\ngetlocal_1\ngetlocal_2\nnextvalue\ncoerce_s\nsetlocal_3\ngetlocal_3\nreturnvalue\n'
        'Check:\nhasnext2 1 2\niftrue Body\nkill 1\nkill 2\npushnull\nreturnvalue')
    source, error = decompile_method(abc, body)
    assert error is None, error
    assert '"first": undefined' in source and ":String in " in source and "String(" not in source
    new_abc, new_body = rebuild(source)
    rebuilt = disassemble_instructions(new_abc.constant_pool, new_body.code)
    # The bounded VM represents null and undefined by the same carrier. This
    # opcode check proves the actual undefined value reaches nullable coerce_s
    # when recompiled, rather than the different String(undefined) operation.
    assert "pushundefined" in rebuilt and "coerce_s" in rebuilt
    before = run_bytes(body.code, "as3", abc=abc, body=body)
    after = run_bytes(new_body.code, "as3", abc=new_abc, body=new_body)
    assert before.value is None and after.value is None


def test_nullable_enumeration_preserves_collection_getter_conversion_and_body_order():
    abc, body = abrupt_enumeration(conversions="coerce_s\n", repeated=True)
    body.code = assemble_instructions(abc.constant_pool,
        'pushbyte 0\nsetlocal_2\nfindpropstrict collection\ncallproperty collection 0\nsetlocal_1\n'
        'jump Check\nBody:\ngetlocal_1\ngetlocal_2\nnextvalue\ncoerce_s\nsetlocal_3\n'
        'findpropstrict seen\ngetlocal_3\ncallpropvoid seen 1\njump Check\n'
        'Check:\nhasnext2 1 2\niftrue Body\nkill 1\nkill 2\nreturnvoid')
    source, error = decompile_method(abc, body)
    assert error is None, error
    new_abc, new_body = rebuild(source)

    def evaluate(target_abc, target_body):
        events = []
        class Convert:
            def __str__(self):
                events.append("convert")
                return "converted"
        class Values(dict):
            def __getitem__(self, key):
                events.append("get:" + key)
                return super().__getitem__(key)
        def collection():
            events.append("collection")
            return Values(first=Convert(), second=None)
        result = run_bytes(target_body.code, "as3", abc=target_abc, body=target_body,
                           variables={"collection": collection, "seen": lambda value: events.append(("body", value))})
        assert not result.raised
        return events

    assert evaluate(abc, body) == evaluate(new_abc, new_body) == [
        "collection", "get:first", "convert", ("body", "converted"), "get:second", ("body", None)]


def test_nullable_enumeration_temporary_cannot_collide_with_embedded_identifier():
    abc, body = abrupt_enumeration(conversions="coerce_s\n")
    binding_pc = next(i.offset for i in decode_avm2(body.code) if i.name == "setlocal_3")
    collision = f"$value{binding_pc}_1"
    abc.constant_pool.strings.append(collision)
    source, error = decompile_method(abc, body)
    assert error is None, error
    assert f"var {collision}_1:String in " in source
    assert f"var {collision}:String in " not in source


@pytest.mark.parametrize("extra,tail", [
    ('findpropstrict trace\ngetlocal_1\ncallpropvoid trace 1\n', "kill 1\nkill 2\npushnull\nreturnvalue"),
    ("", "getlocal_2\nreturnvalue"),
])
def test_nullable_binding_still_rejects_hidden_register_consumers(extra, tail):
    abc, body = abrupt_enumeration(conversions="coerce_s\n", extra=extra, tail=tail)
    source, error = decompile_method(abc, body)
    assert source is None and "Enumeration registers" in error
    assert assemble_instructions(abc.constant_pool, disassemble_instructions(abc.constant_pool, body.code, exact=True)) == body.code


@pytest.mark.parametrize("consumer", ["returnvalue", "pop\nreturnvoid"])
def test_nullable_next_without_proven_enumeration_stays_in_pcode(consumer):
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, "getlocal_1\ngetlocal_2\nnextvalue\ncoerce_s\n" + consumer)
    source, error = decompile_method(abc, body)
    assert source is None and "enumeration_string" in error
    assert assemble_instructions(abc.constant_pool, disassemble_instructions(abc.constant_pool, body.code, exact=True)) == body.code


def test_nullable_enumeration_property_receiver_is_not_evaluated_after_conversion():
    abc, body = abrupt_enumeration(conversions="coerce_s\n")
    body.code = assemble_instructions(abc.constant_pool,
        'pushbyte 0\nsetlocal_2\ngetlex data\nsetlocal_1\njump Check\n'
        'Body:\ngetlex receiver\ngetproperty target\ngetlocal_1\ngetlocal_2\nnextvalue\n'
        'coerce_s\nsetproperty value\njump Check\n'
        'Check:\nhasnext2 1 2\niftrue Body\nkill 1\nkill 2\nreturnvoid')
    source, error = decompile_method(abc, body)
    assert source is None and "proven local binding" in error
    assert assemble_instructions(abc.constant_pool, disassemble_instructions(abc.constant_pool, body.code, exact=True)) == body.code


def test_nullable_string_parameter_does_not_prove_addition_produces_string():
    abc, body = ABCFile(), MethodBodyInfo()
    method = MethodInfo()
    method.param_types = [search_or_add_multiname(abc.constant_pool, "String")]
    abc.methods = [method]
    body.code = assemble_instructions(abc.constant_pool, "getlocal_1\npushbyte 1\nadd\ncoerce_s\nreturnvalue")
    source, error = decompile_method(abc, body)
    # For a null parameter the add result is numeric 1; coerce_s then makes
    # "1". Emitting just (local1 + 1) would silently change the return type.
    assert source is None and "Nullable string coercion" in error
    assert assemble_instructions(abc.constant_pool, disassemble_instructions(abc.constant_pool, body.code, exact=True)) == body.code


@pytest.mark.parametrize("value", [None, "text"])
def test_string_literal_proves_concatenation_despite_nullable_parameter(value):
    abc, body = ABCFile(), MethodBodyInfo()
    method = MethodInfo()
    method.param_types = [search_or_add_multiname(abc.constant_pool, "String")]
    abc.methods = [method]
    body.code = assemble_instructions(abc.constant_pool, 'pushstring "prefix"\ngetlocal_1\nadd\ncoerce_s\nreturnvalue')
    source, error = decompile_method(abc, body)
    assert error is None, error
    new_abc, new_body = rebuild(source)
    before = run_bytes(body.code, "as3", abc=abc, body=body, arguments=(value,))
    after = run_bytes(new_body.code, "as3", abc=new_abc, body=new_body, variables={"local1": value})
    assert before.value == after.value == ("prefixnull" if value is None else "prefixtext")


def test_real_nullable_string_enumeration_methods_now_have_typed_headers():
    path = Path(__file__).resolve().parents[2] / "benchmark_inputs/as3_new.flex.swf"
    if not path.exists():
        pytest.skip("Shared real SWF fixture unavailable")
    expected = {"TestForEachSwitch.run", "TestForInSwitch.run", "TestForEachTry.run", "TestForInIf.run"}
    found = set()
    for unit in enumerate_scripts(path.read_bytes()):
        match = next((name for name in expected if unit.name.endswith(name)), None)
        if match:
            source, error = decompile_method(unit.abc, unit.body)
            assert error is None, (unit.document_id, error)
            assert ":String in " in source
            found.add(match)
    assert found == expected
