"""A class's own cinit runs before its global class slot is installed."""
import pytest

from py_swf.avm2 import resolve_multiname
from py_swf.bytecode_validation import decode_avm2
from py_swf.compiler.class_compiler import compile_class_program
from tests.native_control_vm import run_bytes


def cinit(abc, class_index=0):
    return next(body for body in abc.method_bodies if body.method == abc.classes[class_index].cinit)


def test_current_class_cinit_uses_its_class_receiver_before_global_installation():
    abc = compile_class_program('public class State {public static var prior:int;public static var seen:int;{(State).public::seen=(State).public::prior;(State).public::prior=4;}}').abc
    body = cinit(abc); own_name = abc.instances[0].name
    assert not any(ins.name in ("findpropstrict", "getproperty", "getlex") and ins.args[0] == own_name for ins in decode_avm2(body.code))
    receiver = {"prior": 0, "seen": -1}
    result = run_bytes(body.code, "as3", abc=abc, body=body, variables={"State": None}, _registers={0: receiver})
    assert not result.raised and receiver == {"prior": 4, "seen": 0}


@pytest.mark.parametrize("method_name", ["read", "$cinit"])
def test_ordinary_static_method_preserves_global_current_class_lookup(method_name):
    abc = compile_class_program('public class State {public static var value:int;public static function ' + method_name + '():int{return State.value;}}').abc
    trait = next(trait for trait in abc.classes[0].traits if trait.kind_flags & 15 == 1)
    body = next(body for body in abc.method_bodies if body.method == trait.method)
    assert body.method != abc.classes[0].cinit
    assert any(ins.name == "getproperty" and ins.args[0] == abc.instances[0].name for ins in decode_avm2(body.code))


def test_class_named_local_in_cinit_keeps_its_local_receiver():
    abc = compile_class_program('public class State {public static var value:int;{var State:Object={value:9};State.value=12;trace(State.value);}}').abc
    body = cinit(abc)
    result = run_bytes(body.code, "as3", abc=abc, body=body, variables={"State": None}, _registers={0: {"value": 0}})
    assert not result.raised and result.traces == [12]
    assert any(ins.name == "getlocal" and ins.args == (1,) for ins in decode_avm2(body.code))


def test_class_named_member_in_cinit_keeps_the_member_receiver():
    abc = compile_class_program('public class State {public static var State:Object;public static var value:int;{State.value=11;}}').abc
    body = cinit(abc)
    receiver = {"State": {"value": 9}, "value": 0}
    result = run_bytes(body.code, "as3", abc=abc, body=body, variables={"State": None}, _registers={0: receiver})
    assert not result.raised and receiver["State"]["value"] == 11 and receiver["value"] == 0


def test_another_class_in_cinit_is_not_replaced_with_the_current_receiver():
    abc = compile_class_program('public class Other {public static var value:int;}public class State {public static var seen:int;{State.seen=Other.value;}}').abc
    body = cinit(abc, 1)
    own_name, other_name = abc.instances[1].name, abc.instances[0].name
    assert resolve_multiname(abc.constant_pool, other_name) == "Other"
    assert any(ins.name == "getproperty" and ins.args[0] == other_name for ins in decode_avm2(body.code))
    assert not any(ins.name == "getproperty" and ins.args[0] == own_name for ins in decode_avm2(body.code))
