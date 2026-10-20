"""Register/pool flow and independent bytecode trace checks for nested handlers."""
import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.avm2 import ABCFile, MethodBodyInfo, MethodInfo, assemble_instructions
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.decompile.avm2_dec import decompile_method
from tests.conftest import JPEXS_TESTDATA
from tests.native_control_vm import FlashError, Thrown, run_bytes, run_source
from tests.test_native_reconstruction import compile_bytes, corpus_method


def test_avm1_function_captures_definition_pool_before_outer_pool_change():
    code = assemble_avm1('constant_pool ["original"]\ndefine_function "read" [] E\npush c:0\nreturn\nE:\nconstant_pool ["changed"]\npush 0 "read"\ncall_function\ntrace\nend')
    text, error = decompile_avm1(code)
    assert error is None, error
    assert 'return "original"' in text
    assert run_source(text, "as2").traces == ["original"]


def test_avm1_pool_branches_do_not_capture_one_arbitrary_arm():
    code = assemble_avm1('constant_pool ["first"]\npush "flag"\nget_variable\nif B\njump J\nB:\nconstant_pool ["second"]\nJ:\npush c:0\ntrace\nend')
    text, error = decompile_avm1(code)
    assert "exact P-code follows" in text and "Constant pool mismatch" in error
    assert run_bytes(code, "as2", variables={"flag": False}).traces == ["first"]
    assert run_bytes(code, "as2", variables={"flag": True}).traces == ["second"]


def test_function2_preloaded_register_reassignment_uses_a_hygienic_local():
    code = assemble_avm1('define_function2 "$read" 1 1 [] E\npush 7\nstore_register 1\npop\npush r:1\nreturn\nE:\npush 0 "$read"\ncall_function\ntrace\nend')
    text, error = decompile_avm1(code)
    assert error is None, error
    assert "$preload1" in text and "this =" not in text
    assert run_source(text, "as2").traces == [7]


@pytest.mark.parametrize("header,body,diagnostic", [
    ('"bad" 0 1 [] E', "push undefined\nreturn", "preloads"),
    ('"bad" 1 512 [] E', "push undefined\nreturn", "Reserved"),
    ('"bad" 1 0 [] E', "push r:2\nreturn", "register count"),
    ('"bad" 0 2 [] E', 'push "this"\nget_variable\nreturn', "Suppressed"),
])
def test_invalid_or_unrepresentable_function2_context_stays_pcode(header, body, diagnostic):
    code = assemble_avm1("define_function2 " + header + "\n" + body + "\nE:\nend")
    text, error = decompile_avm1(code)
    assert "exact P-code follows" in text and diagnostic in error


def test_scope_objects_from_different_cfg_arms_are_not_merged_as_one_owner():
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, 'getlocal_1\niftrue B\ngetlocal_0\npushscope\njump J\nB:\npushnull\npushscope\nJ:\ngetscopeobject 0\nreturnvalue')
    text, error = decompile_method(abc, body)
    assert text is None and "scope mismatch" in error


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Original development corpus unavailable")
@pytest.mark.parametrize("random_value", [0, .6, 1.1])
@pytest.mark.parametrize("trigger", [None, "in try2", "in finally2", "in finally1"])
def test_real_nested_finally_has_equal_trace_return_and_exception(random_value, trigger):
    # 1.1 is a deliberately injected out-of-range random value to exercise the
    # early-return lowering. These are bounded VM-subset cases, not Flash parity.
    abc, body = corpus_method(JPEXS_TESTDATA / "as3_cross_compile/bin/as3_cross_compile.flex.swf", "TestTryFinallyReturnNested2.run")
    text, error = decompile_method(abc, body)
    assert error is None, error
    assert text.count("finally {") == 3
    code, rebuilt_abc, rebuilt_body = compile_bytes(text, "as3")
    injected = FlashError("injected")
    def execute(code, abc, body):
        events = []
        def trace(value):
            events.append(value)
            if value == trigger:
                raise Thrown(injected)
        result = run_bytes(code, "as3", abc=abc, body=body, variables={"Math": {"random": lambda: random_value}, "trace": trace})
        return events, result.value, result.error, result.raised
    assert execute(body.code, abc, body) == execute(code, rebuilt_abc, rebuilt_body)
