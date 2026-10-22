"""Runtime double operands cannot use the reserved pool entry or lose IEEE bits."""
import math
import struct

import pytest

from py_swf.avm2 import ABCFile, MethodBodyInfo, MethodInfo, assemble_instructions
from py_swf.bytecode_validation import analyze_method, decode_avm2
from py_swf.compiler.class_compiler import compile_class_program


def bits(value):
    return struct.pack("<d", value)


def test_double_literals_skip_reserved_entry_preserve_zero_sign_and_reuse_exact_bits():
    abc = ABCFile()
    literals = ("0.0", "-0.0", "nan", "-nan", "inf", "-inf", "0.0", "-0.0", "nan")
    assembly = "\n".join("pushdouble " + value + "\npop" for value in literals) + "\nreturnvoid"
    body = MethodBodyInfo(); body.method = 0; body.local_count = 1
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, assembly)
    indexes = [ins.args[0] for ins in decode_avm2(body.code) if ins.name == "pushdouble"]
    assert all(index > 0 for index in indexes)
    assert indexes[0] != indexes[1] and indexes[2] != indexes[3]
    assert indexes[:3] == [indexes[6], indexes[7], indexes[8]]
    assert [bits(abc.constant_pool.doubles[index]) for index in indexes] == [bits(float(value)) for value in literals]
    limits = analyze_method(abc, body)
    body.max_stack, body.max_scope_depth = limits.max_stack, limits.max_scope_depth
    abc.method_bodies = [body]
    parsed = ABCFile(); parsed.parse(abc.serialize(), strict=True)
    assert [bits(parsed.constant_pool.doubles[index]) for index in indexes] == [bits(float(value)) for value in literals]


def test_double_interning_does_not_merge_existing_nan_payloads_or_signed_zeros():
    abc = ABCFile()
    payload = struct.unpack("<d", bytes.fromhex("010000000000f87f"))[0]
    abc.constant_pool.doubles.extend([payload, 0.0, -0.0])
    original = [bits(value) for value in abc.constant_pool.doubles]
    code = assemble_instructions(abc.constant_pool, "pushdouble 0.0\npushdouble -0.0\npushdouble nan\nreturnvoid")
    indexes = [ins.args[0] for ins in decode_avm2(code) if ins.name == "pushdouble"]
    assert indexes[:2] == [2, 3]
    assert indexes[2] == 4
    assert [bits(value) for value in abc.constant_pool.doubles[:4]] == original
    assert math.isnan(abc.constant_pool.doubles[indexes[2]])
    assert bits(abc.constant_pool.doubles[indexes[2]]) == bits(float("nan"))


def test_pushdouble_explicit_reserved_index_is_rejected_but_real_index_stays_exact():
    abc = ABCFile(); abc.constant_pool.doubles.append(-0.0)
    with pytest.raises(ValueError, match="reserved"):
        assemble_instructions(abc.constant_pool, "pushdouble #0\nreturnvalue")
    code = assemble_instructions(abc.constant_pool, "pushdouble #1\nreturnvalue")
    assert decode_avm2(code)[0].args == (1,)
    assert math.copysign(1, abc.constant_pool.doubles[1]) == -1


def test_native_negative_zero_expression_uses_a_real_double_entry_before_negate():
    abc = compile_class_program('public class A {public static var zero:Number; {A.zero=-0.0;} public function f():Number{return -0.0;}}').abc
    indexes = [ins.args[0] for body in abc.method_bodies for ins in decode_avm2(body.code) if ins.name == "pushdouble"]
    assert indexes and all(index > 0 for index in indexes)
    assert all(bits(abc.constant_pool.doubles[index]) == bits(0.0) for index in indexes)
    assert any(ins.name == "negate" for body in abc.method_bodies for ins in decode_avm2(body.code))


def test_empty_string_literal_uses_a_real_entry_and_reuses_it_without_changing_namespace_identity():
    abc = ABCFile()
    code = assemble_instructions(abc.constant_pool, 'pushstring ""\npop\npushstring ""\nreturnvalue')
    indexes = [ins.args[0] for ins in decode_avm2(code) if ins.name == "pushstring"]
    assert indexes == [1, 1]
    assert abc.constant_pool.strings == ["", ""]
    compiled = compile_class_program('public class A {public function empty():String{return "";}}').abc
    assert all(ins.args[0] > 0 for body in compiled.method_bodies for ins in decode_avm2(body.code) if ins.name == "pushstring")
    assert any(namespace["name"] == 0 and namespace["kind"] == 0x16 for namespace in compiled.constant_pool.namespaces[1:])
    parsed = ABCFile(); parsed.parse(compiled.serialize(), strict=True)
    assert any(ins.name == "pushstring" and parsed.constant_pool.strings[ins.args[0]] == "" for body in parsed.method_bodies for ins in decode_avm2(body.code))


def test_pushstring_reserved_reference_is_rejected_without_rewriting_other_strings():
    abc = ABCFile(); abc.constant_pool.strings.extend(["", "unchanged"])
    with pytest.raises(ValueError, match="reserved"):
        assemble_instructions(abc.constant_pool, "pushstring #0\nreturnvalue")
    code = assemble_instructions(abc.constant_pool, "pushstring #1\npushstring #2\nreturnvalue")
    assert [ins.args[0] for ins in decode_avm2(code) if ins.name == "pushstring"] == [1, 2]
    assert abc.constant_pool.strings == ["", "", "unchanged"]
