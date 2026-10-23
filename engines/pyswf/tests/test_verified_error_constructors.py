"""Error construction requires the pinned SDK and the original source binding."""
from types import MappingProxyType

import pytest

from py_swf.abc_symbols import inspect_qname
from py_swf.avm2 import ABCFile
from py_swf.bytecode_validation import decode_avm2
from py_swf.compiler.class_compiler import compile_class_program, class_program_swf
from py_swf.decompile import hierarchy_contract
from py_swf.validation import validate_swf_bytes
from tests.native_control_vm import run_bytes


ERROR_NAMES = ("Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError")


@pytest.fixture
def verified_sdk():
    contract = hierarchy_contract.external_error_constructors()
    if not contract:
        pytest.skip("Verified local playerglobal constructor metadata unavailable")
    return contract


@pytest.mark.parametrize("name", ERROR_NAMES)
@pytest.mark.parametrize("arguments,count", [("", 0), ('"message"', 1), ('"message", 41', 2)])
def test_verified_error_constructors_emit_exact_qname_and_sdk_arity(verified_sdk, name, arguments, count):
    compiled = compile_class_program(f'public class A {{public function make():* {{return new {name}({arguments});}}}}')
    parsed = ABCFile(); parsed.parse(compiled.abc_bytes(), strict=True)
    calls = [ins for body in parsed.method_bodies for ins in decode_avm2(body.code) if ins.name == "constructprop"]
    assert len(calls) == 1 and calls[0].args[1] == count
    identity = inspect_qname(parsed.constant_pool, calls[0].args[0])
    assert (identity.kind, identity.namespace_kind, identity.package, identity.name) == (7, 0x16, "", name)
    assert verified_sdk[(0x16, "", name)] == (0, 2)
    assert validate_swf_bytes(class_program_swf(compiled))["valid"]


def test_constructor_contract_is_immutable_and_keeps_existing_hierarchy_shape(verified_sdk):
    with pytest.raises(TypeError):
        verified_sdk[(0x16, "", "Error")] = (0, 3)
    hierarchy = hierarchy_contract.external_hierarchy()
    assert hierarchy[(0x16, "", "Error")] == ((0x16, "", "Object"), False)
    assert hierarchy[(0x16, "", "TypeError")] == ((0x16, "", "Error"), False)


@pytest.mark.parametrize("name", ERROR_NAMES)
def test_extra_error_constructor_argument_is_rejected(verified_sdk, name):
    with pytest.raises(ValueError, match="Constructor argument count"):
        compile_class_program(f'public class A {{public function f():* {{return new {name}("x", 1, 2);}}}}')


def test_error_constructor_arguments_keep_side_effect_order(verified_sdk):
    abc = compile_class_program('public class A {public function f():* {return new Error(trace("first"), trace("second"));}}').abc
    method = abc.instances[0].traits[0].method
    body = next(body for body in abc.method_bodies if body.method == method)
    result = run_bytes(body.code, "as3", abc=abc, body=body)
    assert result.traces == ["first", "second"] and not result.raised
    assert isinstance(result.value, Exception) and result.value.args == (None, None)


@pytest.mark.parametrize("n,expected", [(-1, 0), (0, 1), (1, 3), (3, 11)])
def test_new_error_typed_catch_and_finally_execute_bounded_paths(verified_sdk, n, expected):
    source = '''public class A {public function calculate(n:int):int {
        var sum:int=0;
        try {for(var i:int=0;i<n;i++){switch(i){case 0:sum+=2;break;case 1:sum+=3;break;default:sum+=5;}}
             if(n<0){throw new Error("negative");}}
        catch(error:Error){sum=0-1;} finally {sum+=1;} return sum;
    }}'''
    abc = compile_class_program(source).abc
    body = next(body for body in abc.method_bodies if body.method == abc.instances[0].traits[0].method)
    result = run_bytes(body.code, "as3", abc=abc, body=body, arguments=(n,))
    assert result.value == expected and not result.raised
    assert any(ins.name == "constructprop" for ins in decode_avm2(body.code))


def test_native_class_backward_branches_target_avm2_label_for_player_verification():
    abc = compile_class_program('public class A {public function f():int{var n:int=0;while(n<3){n++;}return n;}}').abc
    body = next(body for body in abc.method_bodies if body.method == abc.instances[0].traits[0].method)
    instructions = decode_avm2(body.code)
    by_offset = {ins.offset: ins for ins in instructions}
    backward = []
    for index, ins in enumerate(instructions):
        if ins.name == "jump" or ins.name.startswith("if"):
            next_offset = instructions[index + 1].offset if index + 1 < len(instructions) else len(body.code)
            target = next_offset + ins.args[0]
            if target < ins.offset:
                backward.append(target)
                assert by_offset[target].name == "label"
    assert backward


@pytest.mark.parametrize("source", [
    'public class A {public var Error:Class;public function f():*{return new Error();}}',
    'public class A {public function f(Error:Class):*{return new Error();}}',
    'public class A {public function f():*{var Error:Class;return new Error();}}',
    'public class A {public function f():*{return new Error();var Error:Class;}}',
    'public class A {public function f():*{try{throw 1;}catch(Error:*){return new Error();}}}',
    'public class A {public function f(Error:Class):Function{return function():*{return new Error();};}}',
    'public class A {public function f():Function{var Error:Class;return function():*{return new Error();};}}',
    'public class A {public function f():Function{return function(Error:Class):*{return new Error();};}}',
    'public class B {public var TypeError:Class;}public class A extends B {public function f():*{return new TypeError();}}',
])
def test_lexical_member_catch_and_closure_shadows_are_rejected(source):
    with pytest.raises(ValueError, match="constructor type is shadowed"):
        compile_class_program(source)


def test_error_catch_name_does_not_shadow_the_type_after_its_scope(verified_sdk):
    compiled = compile_class_program('public class A {public function f():*{try{throw 1;}catch(Error:*){trace(Error);}return new Error();}}')
    assert validate_swf_bytes(class_program_swf(compiled))["valid"]


def test_frozen_missing_and_changed_sdk_cannot_admit_error_construction(monkeypatch, tmp_path):
    monkeypatch.setattr(hierarchy_contract.sys, "frozen", True, raising=False)
    monkeypatch.delenv("FLASH_SUITE_WORKSPACE", raising=False)
    source = 'public class A {public function f():* {return new Error("x");}}'
    with pytest.raises(ValueError, match="verified class provider: Error"):
        compile_class_program(source)
    monkeypatch.setenv("FLASH_SUITE_WORKSPACE", str(tmp_path))
    sdk = tmp_path / "vendor/toolpacks/flex/frameworks/libs/player/32.0/playerglobal.swc"
    sdk.parent.mkdir(parents=True); sdk.write_bytes(b"altered untrusted SDK")
    assert not hierarchy_contract.external_error_constructors()
    with pytest.raises(ValueError, match="verified class provider: Error"):
        compile_class_program(source)


@pytest.mark.parametrize("metadata", [
    {(0x17, "", "Error"): ((0x16, "", "Object"), False, (0, 2))},
    {(0x16, "other", "Error"): ((0x16, "", "Object"), False, (0, 2))},
    {(0x16, "", "Error"): None},
    {(0x16, "", "Error"): ((0x16, "", "Object"), True, (0, 2))},
    {(0x16, "", "Error"): ((0x16, "", "Unknown"), False, (0, 2))},
    {(0x16, "", "Error"): ((0x16, "", "Object"), False, None)},
])
def test_homonyms_duplicates_interfaces_and_unproven_roots_cannot_supply_contract(monkeypatch, tmp_path, metadata):
    monkeypatch.setattr(hierarchy_contract, "_external_metadata", lambda path: MappingProxyType(metadata))
    assert not hierarchy_contract._external_error_constructors(tmp_path / "isolated-synthetic-contract.swc")
