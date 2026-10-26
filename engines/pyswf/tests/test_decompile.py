"""Tests del decompilador v1 (AS3 outline, AVM2 method, AVM1)."""
import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.avm2 import ABCFile, ConstantPool, MethodBodyInfo, build_method_mapping
from py_swf.decompile import as3_outline, avm1_dec, avm2_dec
from py_swf.swf_parser import SWFFile
from tests.conftest import JPEXS_TESTDATA


def test_avm1_linear_code():
    asm = (
        'push "greeting" "hola"\n'
        "set_variable\n"
        'push "greeting"\n'
        "get_variable\n"
        "trace\n"
        "end"
    )
    src, err = avm1_dec.decompile_avm1(assemble_avm1(asm))
    assert err is None
    assert "greeting = \"hola\";" in src
    assert "trace(greeting);" in src


def test_avm1_arithmetic():
    asm = (
        'push "x" 3 4\n'
        "add\n"
        "set_variable\n"
        "end"
    )
    src, _ = avm1_dec.decompile_avm1(assemble_avm1(asm))
    assert "x = (3 + 4);" in src


def test_avm1_cfg_ignores_truncated_branch_target():
    # ActionIf with an incomplete payload must degrade without creating an
    # empty basic block or throwing from the CFG builder.
    actions, _ = avm1_dec._decode(bytes.fromhex("9d 01 00 00"))
    blocks, _ = avm1_dec.build_avm1_cfg(actions)
    assert all(block.actions for block in blocks.values())


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="no corpus")
def test_as3_outline_from_corpus():
    path = JPEXS_TESTDATA / "as3" / "as3.swf"
    if not path.is_file():
        pytest.skip("as3.swf not present")
    swf = SWFFile()
    swf.read_bytes(path.read_bytes())
    abc = None
    for tag in swf.tags:
        if tag.is_doabc:
            _, _, ab = tag.parse_doabc()
            abc = ABCFile()
            abc.parse(ab)
            break
    assert abc is not None
    outline = as3_outline.outline_abc(abc)
    assert "package" in outline
    assert "class TestClass2" in outline
    assert "function" in outline


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="no corpus")
def test_avm2_decompile_falls_back_gracefully():
    path = JPEXS_TESTDATA / "as3" / "as3.swf"
    if not path.is_file():
        pytest.skip("as3.swf not present")
    swf = SWFFile()
    swf.read_bytes(path.read_bytes())
    abc = None
    for tag in swf.tags:
        if tag.is_doabc:
            _, _, ab = tag.parse_doabc()
            abc = ABCFile()
            abc.parse(ab)
            break
    decompiled = 0
    for mb in abc.method_bodies:
        source, error = avm2_dec.decompile_method(abc, mb)
        # cada método o decompila o devuelve una razón de fallback, nunca crashea
        assert (source is None) == (error is not None)
        if source is not None:
            decompiled += 1
    assert decompiled > 0  # algunos métodos simples deben decompilar


def test_avm2_cfg_reconstructs_branch_condition():
    # pushtrue; iffalse L_else; pushbyte 1; pop; jump L_end;
    # L_else: pushbyte 2; pop; L_end: returnvoid
    code = bytes.fromhex("26 12 07 00 00 24 01 29 10 03 00 00 24 02 29 47")
    abc = ABCFile()
    abc.constant_pool = ConstantPool()
    body = MethodBodyInfo()
    body.code = code
    source, error = avm2_dec.decompile_method(abc, body)
    assert error is None
    assert "if (true)" in source
    assert "condition" not in source
    instructions, _ = avm2_dec._decode(abc.constant_pool, code)
    blocks, entry = avm2_dec.build_cfg(instructions)
    assert len(blocks) >= 3
    assert len(entry.successors) == 2


def test_avm2_cfg_reconstructs_simple_while_and_keeps_exit_outside():
    # pushtrue; iffalse L_exit; local1 = 1 + 2; jump L_header; L_exit: returnvoid
    code = bytes.fromhex("26 12 0a 00 00 24 01 24 02 a0 d5 10 f1 ff ff 47")
    abc = ABCFile()
    abc.constant_pool = ConstantPool()
    body = MethodBodyInfo()
    body.code = code
    source, error = avm2_dec.decompile_method(abc, body)
    assert error is None
    assert "while (true)" in source
    assert "local1 = (1 + 2);" in source
    assert source.index("while") < source.index("return;")


def test_avm2_cfg_ignores_truncated_or_out_of_range_branches():
    abc = ABCFile()
    abc.constant_pool = ConstantPool()
    body = MethodBodyInfo()
    body.code = bytes.fromhex("12 00")  # truncated iffalse operand
    source, error = avm2_dec.decompile_method(abc, body)
    assert source is not None or error is not None
    instructions, _ = avm2_dec._decode(abc.constant_pool, bytes.fromhex("10 ff ff ff 47"))
    blocks, _ = avm2_dec.build_cfg(instructions)
    assert all(block.instructions for block in blocks.values())


def test_avm2_reconstructs_lookup_switch_targets():
    # lookup switch with two explicit cases and a default target.
    # Offsets are relative to the lookupswitch opcode, not its end. The
    # previous fixture pointed into pushbyte operands, not instructions.
    code = bytes.fromhex("24 01 1b 17 00 00 01 0b 00 00 11 00 00 24 01 10 08 00 00 24 02 10 02 00 00 24 03 29 47")
    abc = ABCFile()
    abc.constant_pool = ConstantPool()
    body = MethodBodyInfo()
    body.code = code
    source, error = avm2_dec.decompile_method(abc, body)
    assert error is None
    assert "switch (1)" in source
    assert "case 0:" in source
    assert "case 1:" in source
    assert "default:" in source


def test_avm2_e4x_descendant_and_namespace():
    from py_swf.decompile.ir import render, Value, literal, name
    node = Value("descendant", "", (name("xml"), literal("item")))
    assert render(node) == "xml..item"
    node_wildcard = Value("descendant", "", (name("doc"), literal("*")))
    assert render(node_wildcard) == "doc..*"

