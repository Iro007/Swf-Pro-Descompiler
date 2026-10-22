"""Focused tests for array/object literals and computed member access."""

import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.avm2 import ABCFile, InstanceInfo, assemble_instructions, search_or_add_string
from py_swf.bytecode_validation import analyze_method, decode_avm2
from py_swf.compiler import compile_actionscript
from py_swf.editing import edit_script
from py_swf.symbols import MethodBindings
from py_swf.swf_parser import SWFFile, SWFTag
from tests.native_control_vm import run_bytes, run_source
from tests.native_fixtures import as3_tag, swf_with


@pytest.mark.parametrize("target", ["as2", "as3"])
def test_array_and_object_literals_compile_as_expression_values(target):
    source = 'var values = [1, 2, 3]; var record = {name: "Ada", score: values.length};'
    assembly = compile_actionscript(source, target)

    if target == "as2":
        bytecode = assemble_avm1(assembly)
        assert b"" != bytecode
    else:
        abc = ABCFile()
        bytecode = assemble_instructions(abc.constant_pool, assembly)
        assert {ins.name for ins in decode_avm2(bytecode)} >= {"newarray", "newobject"}


def test_as2_computed_read_and_write_evaluate_receiver_key_and_rhs_once_in_source_order():
    assembly = compile_actionscript("getObject()[getKey()] = getValue();", "as2")
    bytecode = assemble_avm1(assembly)

    assert assembly.count('ActionCallFunction') == 3
    assert assembly.index('"getObject"') < assembly.index('"getKey"') < assembly.index('"getValue"')
    assert "ActionSetMember" in assembly
    assert bytecode


def test_as2_computed_prefix_and_postfix_updates_are_supported():
    assembly = compile_actionscript("trace(items[nextIndex()]++); ++items[nextIndex()];", "as2")
    assert assembly.count("ActionGetMember") >= 2
    assert assembly.count("ActionSetMember") >= 2
    assert assembly.count('"nextIndex"') == 2
    assert assemble_avm1(assembly)


def test_as2_computed_compound_assignment_evaluates_reference_before_rhs_once():
    assembly = compile_actionscript("getObject()[getKey()] += getValue();", "as2")
    assert assembly.index('"getObject"') < assembly.index('"getKey"') < assembly.index('"getValue"')
    assert assembly.count('"getObject"') == 1
    assert assembly.count('"getKey"') == 1
    assert assembly.count('"getValue"') == 1
    assert "ActionGetMember" in assembly and "ActionSetMember" in assembly
    assert assemble_avm1(assembly)


def test_object_literal_values_keep_source_evaluation_order_for_both_targets():
    for target in ("as2", "as3"):
        assembly = compile_actionscript("var record = {first: markFirst(), second: markSecond()};", target)
        assert assembly.index("markFirst") < assembly.index("markSecond")
        if target == "as2":
            assert "ActionInitObject" in assembly
        else:
            abc = ABCFile()
            data = assemble_instructions(abc.constant_pool, assembly)
            assert "newobject" in {ins.name for ins in decode_avm2(data)}


@pytest.mark.parametrize("target", ["as2", "as3"])
def test_array_values_and_duplicate_object_keys_keep_source_order(target):
    array_result = run_source("var n = 0; return [n++, n++, n++];", target)
    object_result = run_source("var n = 0; return {x:n++, y:n++, x:n++};", target)

    assert array_result.value == [0, 1, 2]
    assert object_result.value == {"x": 2, "y": 1}


def test_as3_rejects_runtime_member_name_with_actionable_error():
    with pytest.raises((SyntaxError, ValueError), match="runtime|computed|P-code|bracket"):
        compile_actionscript("return values[index];", "as3")


@pytest.mark.parametrize("source", ["var x = [1,,2];", "var x = {name};", "var x = [1]; x[0] += 1;"])
def test_unsupported_literal_or_as3_compound_computed_assignment_is_rejected(source):
    with pytest.raises((SyntaxError, ValueError)):
        compile_actionscript(source, "as3")


def test_public_jpexs_as2_corpus_array_index_and_object_assignment_recompile():
    from pathlib import Path
    import re

    from py_swf.corpus import corpus_root
    fixture = corpus_root() / "as2/as2/DOMDocument.xml"
    if not fixture.exists():
        pytest.skip("Pinned public JPEXS source corpus is not present in this checkout")
    xml = fixture.read_text(encoding="utf-8")
    for frame_name, expected in (
        ("indicesTest", 'var k=[1,2,3]; var b=k[1];'),
        ("deleteTest", 'var obj = {a:1,b:2}; obj["salam likum"] = 58;'),
    ):
        frame = re.search(rf'<DOMFrame\b[^>]*name="{frame_name}"[^>]*>(.*?)</DOMFrame>', xml, re.S)
        assert frame, f"Missing public fixture frame {frame_name}"
        script = re.search(r"<!\[CDATA\[(.*?)\]\]>", frame.group(1), re.S)
        assert script and expected in " ".join(script.group(1).split())
        assert assemble_avm1(compile_actionscript(expected, "as2"))


def test_as2_dynamic_source_roundtrips_and_failed_edit_preserves_original():
    original_tag = SWFTag(12, assemble_avm1('ActionPush "before"\nActionPop\nend'))
    original = swf_with([original_tag])
    edited = edit_script(original_tag, 'var items = [1, 2]; items[nextIndex()] = 7;', source=True)[0]
    candidate = swf_with([edited])
    reopened = SWFFile()
    reopened.read_bytes(candidate)
    assert reopened.tags[0].tag_type == 12
    from py_swf.avm1 import disassemble_avm1
    listing = disassemble_avm1(reopened.tags[0].data)
    assert "init_array" in listing and "set_member" in listing

    with pytest.raises((SyntaxError, ValueError)):
        edit_script(edited, "items[broken = ;", source=True)
    assert original_tag.data == assemble_avm1('ActionPush "before"\nActionPop\nend')
    assert original != candidate


def test_as3_array_and_object_literals_survive_abc_save_and_reopen():
    tag, _ = edit_script(as3_tag(), 'var values = [1, 2, 3]; var record = {name: "Ada", size: values.length}; return record;', 1, source=True)
    movie = SWFFile()
    movie.read_bytes(swf_with([tag]))
    reopened_abc = ABCFile()
    reopened_abc.parse(movie.tags[0].parse_doabc()[2])
    instructions = decode_avm2(reopened_abc.method_bodies[1].code)
    names = [ins.name for ins in instructions]
    assert "newarray" in names and "newobject" in names


def test_as3_runtime_index_read_write_uses_public_multiname_l_and_roundtrips():
    tag, _ = edit_script(
        as3_tag(),
        'var values:Object = [10, 20]; var index:int = 0; values[index] = 7; return values[index];',
        1,
        source=True,
    )
    movie = SWFFile()
    movie.read_bytes(swf_with([tag]))
    reopened_abc = ABCFile()
    reopened_abc.parse(movie.tags[0].parse_doabc()[2])
    body = reopened_abc.method_bodies[1]
    instructions = decode_avm2(body.code)
    indexed = [ins for ins in instructions if ins.name in ("getproperty", "setproperty")]
    assert [ins.name for ins in indexed].count("getproperty") >= 1
    assert [ins.name for ins in indexed].count("setproperty") >= 1
    pool = reopened_abc.constant_pool
    for ins in indexed:
        multiname = pool.multinames[ins.args[0]]
        assert multiname["kind"] == 0x1B
        namespaces = pool.ns_sets[multiname["ns_set"]]
        assert len(namespaces) == 1
        public_namespace = pool.namespaces[namespaces[0]]
        assert public_namespace["kind"] == 0x16
        assert pool.strings[public_namespace["name"]] == ""
    limits = analyze_method(reopened_abc, body)
    assert body.max_stack == limits.max_stack


def test_as3_indexed_update_and_compound_assignment_evaluate_each_part_once():
    source = 'var values = [1, 2]; var index = 0; var old = values[index++]++; values[index++] += 4; return values[0] * 100 + old * 10 + index;'
    tag, _ = edit_script(as3_tag(), source, 1, source=True)
    movie = SWFFile()
    movie.read_bytes(swf_with([tag]))
    abc = ABCFile()
    abc.parse(movie.tags[0].parse_doabc()[2])
    body = abc.method_bodies[1]
    assert run_bytes(body.code, "as3", abc=abc, body=body).value == 212


def test_as3_indexed_assignment_evaluates_receiver_index_and_value_once_in_order():
    events, obj = [], {}
    def receiver():
        events.append("receiver")
        return obj
    def key():
        events.append("index")
        return "chosen"
    def value():
        events.append("value")
        return 23

    tag, _ = edit_script(as3_tag(), 'getObject()[getKey()] = getValue(); return 0;', 1, source=True)
    movie = SWFFile()
    movie.read_bytes(swf_with([tag]))
    abc = ABCFile()
    abc.parse(movie.tags[0].parse_doabc()[2])
    body = abc.method_bodies[1]
    result = run_bytes(body.code, "as3", abc=abc, body=body,
                       variables={"getObject": receiver, "getKey": key, "getValue": value})

    assert result.value == 0
    assert events == ["receiver", "index", "value"]
    assert obj == {"chosen": 23}


@pytest.mark.parametrize("target", ["as2", "as3"])
def test_calls_through_computed_member_names_remain_explicitly_unsupported(target):
    with pytest.raises(SyntaxError, match="Calls through computed member names"):
        if target == "as2":
            compile_actionscript("items[methodName]();", target)
        else:
            edit_script(as3_tag(), "items[methodName]();", 1, source=True)


def test_as3_computed_access_is_rejected_for_class_owned_method_bindings():
    abc = ABCFile()
    abc.parse(as3_tag().parse_doabc()[2])
    instance = InstanceInfo()
    instance.iinit = 1
    abc.instances = [instance]
    abc.scripts[0].traits = []
    bindings = MethodBindings(abc, abc.method_bodies[1])

    assert bindings.owner == ("instance", 0)
    with pytest.raises(SyntaxError, match="class namespace binding"):
        compile_actionscript("return values[index];", "as3", bindings=bindings)


def test_as3_computed_access_is_rejected_when_pool_contains_open_namespace():
    abc = ABCFile()
    abc.parse(as3_tag().parse_doabc()[2])
    pool = abc.constant_pool
    pool.namespaces.append({"kind": 0x08, "name": search_or_add_string(pool, "example:open")})
    bindings = MethodBindings(abc, abc.method_bodies[1])

    assert bindings.owner is None
    with pytest.raises(SyntaxError, match="private/open namespace lookup"):
        compile_actionscript("return values[index];", "as3", bindings=bindings)


def test_as3_ambiguous_class_script_owner_with_private_namespace_is_rejected():
    abc = ABCFile()
    abc.parse(as3_tag().parse_doabc()[2])
    instance = InstanceInfo()
    instance.iinit = 1
    abc.instances = [instance]
    pool = abc.constant_pool
    pool.namespaces.append({"kind": 0x05, "name": search_or_add_string(pool, "PrivateOwner")})
    bindings = MethodBindings(abc, abc.method_bodies[1])

    # MethodBindings cannot select one owner when the body is both a script
    # function and an instance initializer; the private pool fact is still
    # enough to reject the public-only MultinameL route.
    assert bindings.owner is None
    with pytest.raises(SyntaxError, match="private/open namespace lookup"):
        compile_actionscript("return values[index];", "as3", bindings=bindings)

