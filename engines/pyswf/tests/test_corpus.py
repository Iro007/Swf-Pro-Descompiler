"""Round-trip and disassembly checks over the local JPEXS corpus (skipped in CI)."""
import pytest

from py_swf.avm2 import ABCFile, disassemble_instructions
from py_swf.swf_parser import SWFFile
from tests.conftest import JPEXS_TESTDATA

# Deliberately malformed/exotic samples that even JPEXS special-cases
KNOWN_BAD = {"AmfTest.swf", "harman_encrypted.swf", "inside_xored.swf"}

def _swf_files():
    if not JPEXS_TESTDATA.is_dir():
        return []
    return sorted(
        p for p in JPEXS_TESTDATA.rglob("*.swf") if p.name not in KNOWN_BAD
    )

FILES = _swf_files()

@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_structural_roundtrip(path):
    swf = SWFFile()
    swf.read_bytes(path.read_bytes())
    swf2 = SWFFile()
    swf2.read_bytes(swf.save_bytes())
    assert len(swf2.tags) == len(swf.tags)
    assert all(a.data == b.data for a, b in zip(swf.tags, swf2.tags))

def test_corpus_present_or_skip(jpexs_testdata):
    assert (jpexs_testdata / "as3").is_dir()

@pytest.mark.skipif(not FILES, reason="JPEXS testdata not available")
def test_as3_disassembles_without_unknown_opcodes():
    path = JPEXS_TESTDATA / "as3" / "as3.swf"
    if not path.is_file():
        pytest.skip("as3.swf not in corpus")
    swf = SWFFile()
    swf.read_bytes(path.read_bytes())
    seen_methods = 0
    for tag in swf.tags:
        if tag.is_doabc:
            _, _, abc_bytes = tag.parse_doabc()
            abc = ABCFile()
            abc.parse(abc_bytes)
            for mb in abc.method_bodies:
                asm = disassemble_instructions(abc.constant_pool, mb.code)
                assert "raw_0x" not in asm, f"unknown opcode in:\n{asm}"
                seen_methods += 1
    assert seen_methods > 0
