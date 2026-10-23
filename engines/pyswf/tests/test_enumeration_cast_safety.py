"""Independent regression for conversions that enumeration headers cannot drop."""
import math

import pytest

from py_swf.avm2 import assemble_instructions, disassemble_instructions
from py_swf.compiler import compile_actionscript
from py_swf.decompile.avm2_dec import decompile_method
from tests.native_control_vm import run_bytes
from tests.test_native_reconstruction_improvements import abrupt_enumeration


@pytest.mark.parametrize('conversion', ['convert_i', 'convert_i\nconvert_d', 'convert_u', 'convert_i\ncoerce_a'])
@pytest.mark.parametrize('value', [11.9, -11.9, 4294967297.9])
def test_enum_binding_keeps_cast_semantics_or_preserves_pcode(conversion, value):
    abc, body = abrupt_enumeration()
    code = ('pushbyte 0\nsetlocal_2\ngetlex data\ncoerce_a\nsetlocal_1\njump Check\n'
            'Body:\ngetlocal_1\ngetlocal_2\nnextvalue\n' + conversion + '\nsetlocal_3\ngetlocal_3\nreturnvalue\n'
            'Check:\nhasnext2 1 2\niftrue Body\nkill 1\nkill 2\npushnull\nreturnvalue')
    body.code = assemble_instructions(abc.constant_pool, code)
    expected = run_bytes(body.code, 'as3', abc=abc, body=body, variables={'data': {'first': value}})
    source, error = decompile_method(abc, body)
    if error:
        assert source is None and 'conversion' in error.lower()
        assert assemble_instructions(abc.constant_pool, disassemble_instructions(abc.constant_pool, body.code, exact=True)) == body.code
    else:
        rebuilt = assemble_instructions(abc.constant_pool, compile_actionscript(source, 'as3'))
        actual = run_bytes(rebuilt, 'as3', abc=abc, body=body, variables={'data': {'first': value}})
        assert (actual.value, actual.error, actual.raised) == (expected.value, expected.error, expected.raised)
