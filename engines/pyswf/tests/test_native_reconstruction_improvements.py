"""Real-format regressions for bounded EOF regions and abrupt enumeration."""
from pathlib import Path
import math

import pytest

from py_swf.avm1 import assemble_avm1, disassemble_avm1
from py_swf.avm2 import ABCFile, MethodBodyInfo, MethodInfo, assemble_instructions, disassemble_instructions
from py_swf.compiler import compile_actionscript
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.decompile.ir import Instruction, ReconstructionError
from py_swf.decompile.regions import as2_regions
from py_swf.script_inventory import enumerate_scripts
from tests.native_control_vm import run_bytes, run_source
from tests.native_source_subset import NODE, run_trusted_subset


@pytest.mark.parametrize("body,expected", [
    ('push "body"\ntrace', ["body", "finally"]),
    ('push "caught"\nthrow', ["caught", "finally"]),
    ('push 7\nreturn', ["finally"]),
])
def test_avm1_finally_boundary_at_exact_function_eof(body, expected):
    code = assemble_avm1('try 3 "e" Catch Finally After\n' + body +
        '\njump Finally\nCatch:\npush "e"\nget_variable\ntrace\n'
        'Finally:\npush "finally"\ntrace\nAfter:')
    source, error = decompile_avm1(code)
    assert error is None, error
    assert "finally {" in source
    # The reference VM needs an instruction at the endpoint to finish pending
    # completions. ActionEnd supplies precisely the inert EOF anchor recovered
    # by the source reconstructor; no additional user action is evaluated.
    before = run_bytes(code + b"\0", "as2")
    after = run_source(source, "as2")
    assert before.traces == expected
    assert (after.traces, after.value, after.raised, after.error) == (
        before.traces, before.value, before.raised, before.error)


@pytest.mark.parametrize("boundary", [2, 6])
def test_avm1_invalid_try_boundary_is_not_made_into_an_exit(boundary):
    instructions = [Instruction(0, 3, "try", (2, '""', 3, boundary, boundary, boundary)),
                    Instruction(3, 5, "push", (b"",))]
    with pytest.raises(ReconstructionError, match="action boundary"):
        as2_regions(instructions)


def test_avm1_constructor_integral_double_count_preserves_argument_order():
    code = assemble_avm1('push 2 1 2.0 "C"\nnew_object\nreturn')
    source, error = decompile_avm1(code)
    assert error is None and source == "return new C(1, 2);"
    # `new` is outside the native source compiler subset. P-code still saves
    # exactly, and the trusted V8 subset independently checks emitted order.
    assert assemble_avm1(disassemble_avm1(code)) == code
    with pytest.raises(SyntaxError, match="Unsupported expression"):
        compile_actionscript(source, "as2")
    if NODE:
        actual = run_trusted_subset('function C(a,b) {this.values=[a,b];}\n' + source)
        assert actual["value"] == {"values": [1, 2]}


@pytest.mark.parametrize("count", ["-1.0", "0.5", "nan", "inf", "true", "3.0"])
def test_avm1_unsafe_constructor_count_still_fails_closed(count):
    code = assemble_avm1('push 2 1 ' + count + ' "C"\nnew_object\nreturn')
    source, error = decompile_avm1(code)
    assert error and "argument count" in error
    assert source.endswith(disassemble_avm1(code))


def abrupt_enumeration(op="nextvalue", extra="", tail="kill 1\nkill 2\npushnull\nreturnvalue", conversions="", repeated=False):
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.local_count = 4
    body.code = assemble_instructions(abc.constant_pool,
        'pushbyte 0\nsetlocal_2\ngetlex data\ncoerce_a\nsetlocal_1\n'
        'jump Check\nBody:\ngetlocal_1\ngetlocal_2\n' + op +
        '\n' + conversions + 'setlocal_3\n' + extra +
        ('findpropstrict trace\ngetlocal_3\ncallpropvoid trace 1\njump Check\n' if repeated else 'getlocal_3\nreturnvalue\n') +
        'Check:\nhasnext2 1 2\niftrue Body\n' + tail)
    abc.method_bodies = [body]
    return abc, body


@pytest.mark.parametrize("op", ["nextname", "nextvalue"])
@pytest.mark.parametrize("data", [{}, {"first": 11, "second": 22}])
def test_abrupt_enumeration_without_backedge_recompiles_with_same_behavior(op, data):
    abc, body = abrupt_enumeration(op)
    source, error = decompile_method(abc, body)
    assert error is None, error
    assert ("for each (" if op == "nextvalue" else "for (") in source
    before = run_bytes(body.code, "as3", abc=abc, body=body, variables={"data": data})
    rebuilt_abc, rebuilt_body = ABCFile(), MethodBodyInfo()
    rebuilt_abc.methods = [MethodInfo()]
    rebuilt_body.code = assemble_instructions(rebuilt_abc.constant_pool, compile_actionscript(source, "as3"))
    after = run_bytes(rebuilt_body.code, "as3", abc=rebuilt_abc, body=rebuilt_body, variables={"data": data})
    assert (after.traces, after.value, after.raised, after.error) == (
        before.traces, before.value, before.raised, before.error)


@pytest.mark.parametrize("repeated", [False, True])
@pytest.mark.parametrize("conversions,value,expected", [
    ("convert_i\n", 11.9, 11),
    ("coerce_i\n", -11.9, -11),
    ("convert_i\nconvert_d\n", 11.9, 11.0),
    ("convert_u\nconvert_i\nconvert_d\n", 4294967295.9, -1.0),
    ("convert_i\nconvert_d\n", 2147483648.75, -2147483648.0),
    ("convert_d\nconvert_d\n", float("nan"), float("nan")),
])
def test_enumeration_conversions_are_never_dropped_from_header(conversions, value, expected, repeated):
    abc, body = abrupt_enumeration(conversions=conversions, repeated=repeated)
    original = run_bytes(body.code, "as3", abc=abc, body=body, variables={"data": {"first": value}})
    actual = original.traces[0] if repeated else original.value
    assert math.isnan(actual) if math.isnan(expected) else actual == expected
    source, error = decompile_method(abc, body)
    assert source is None and "binding conversion" in error
    # The fallback preserves every cast opcode rather than emitting a source
    # loop whose binding silently keeps an unconverted enumeration value.
    pcode = disassemble_instructions(abc.constant_pool, body.code, exact=True)
    rebuilt = assemble_instructions(abc.constant_pool, pcode)
    assert rebuilt == body.code
    saved = run_bytes(rebuilt, "as3", abc=abc, body=body, variables={"data": {"first": value}})
    saved_actual = saved.traces[0] if repeated else saved.value
    assert math.isnan(saved_actual) if math.isnan(expected) else saved_actual == expected


@pytest.mark.parametrize("extra", [
    'getlocal_1\npop\n',
    'pushbyte 9\nsetlocal_2\n',
])
def test_abrupt_enumeration_with_visible_internal_register_use_fails_closed(extra):
    # pop of a pure register is semantically inert, so make the first case an
    # actual source-visible read before return instead.
    if extra.startswith("getlocal"):
        extra = 'findpropstrict trace\ngetlocal_1\ncallpropvoid trace 1\n'
    abc, body = abrupt_enumeration(extra=extra)
    source, error = decompile_method(abc, body)
    assert source is None and "Enumeration registers" in error


def test_abrupt_enumeration_cursor_escaping_without_kill_fails_closed():
    abc, body = abrupt_enumeration(tail="getlocal_2\nreturnvalue")
    source, error = decompile_method(abc, body)
    assert source is None and "escape" in error


def test_later_enumeration_can_reuse_definitely_killed_registers():
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    parts = []
    for index, op in enumerate(("nextname", "nextvalue")):
        parts.append(f'pushbyte 0\nsetlocal_2\ngetlex data\nsetlocal_1\njump Check{index}\n'
            f'Body{index}:\ngetlocal_1\ngetlocal_2\n{op}\nsetlocal_3\n'
            f'findpropstrict trace\ngetlocal_3\ncallpropvoid trace 1\njump Check{index}\n'
            f'Check{index}:\nhasnext2 1 2\niftrue Body{index}\nkill 1\nkill 2')
    body.code = assemble_instructions(abc.constant_pool, '\n'.join(parts) + '\nreturnvoid')
    rebuilt, error = decompile_method(abc, body)
    assert error is None, error
    assert "for (" in rebuilt and "for each (" in rebuilt
    rebuilt_abc, rebuilt_body = ABCFile(), MethodBodyInfo()
    rebuilt_abc.methods = [MethodInfo()]
    rebuilt_body.code = assemble_instructions(rebuilt_abc.constant_pool, compile_actionscript(rebuilt, "as3"))
    data = {"first": 11, "second": 22}
    before = run_bytes(body.code, "as3", abc=abc, body=body, variables={"data": data})
    after = run_bytes(rebuilt_body.code, "as3", abc=rebuilt_abc, body=rebuilt_body, variables={"data": data})
    assert after.traces == before.traces == ["first", "second", 11, 22]


def test_real_avm1_constructor_and_as3_abrupt_foreach_fixtures():
    from py_swf import paths
    root = paths.CORPUS
    if not root.exists():
        pytest.skip("Shared CLI fixture corpus unavailable")
    unit = next(enumerate_scripts((root / "as2_definefunc_regs.swf").read_bytes()))
    source, error = decompile_avm1(unit.code)
    assert error is None and "new MyClass()" in source
    units = list(enumerate_scripts((root / "as3_new.flex.swf").read_bytes()))
    matches = [u for u in units if u.name.endswith("TestForEachReturn.run")]
    assert len(matches) == 1
    source, error = decompile_method(matches[0].abc, matches[0].body)
    assert error is None and "for each (" in source
