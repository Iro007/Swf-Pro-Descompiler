"""Spec-conformance tests for the AVM2 opcode table."""
from py_swf.avm2 import ConstantPool, assemble_instructions, disassemble_instructions
from py_swf.avm2_opcodes import OPCODES, MNEMONICS

# Opcodes whose historical misdefinition desynchronized the byte stream
SPEC = {
    0x1F: ("hasnext", 0),
    0x23: ("nextvalue", 0),
    0x26: ("pushtrue", 0),
    0x27: ("pushfalse", 0),
    0x32: ("hasnext2", 2),
    0x47: ("returnvoid", 0),
    0x48: ("returnvalue", 0),
    0x49: ("constructsuper", 1),
    0x4C: ("callproplex", 2),
    0x4E: ("callsupervoid", 2),
    0x53: ("applytype", 1),
    0x5A: ("newcatch", 1),
    0x77: ("convert_o", 0),
    0x78: ("checkfilter", 0),
    0xAB: ("equals", 0),
    0xAC: ("strictequals", 0),
    0xB2: ("istype", 1),
    0xC2: ("inclocal_i", 1),
    0xC5: ("add_i", 0),
}

def test_spec_conformance():
    for opcode, (mnemonic, argc) in SPEC.items():
        assert opcode in OPCODES, f"missing opcode 0x{opcode:02X} ({mnemonic})"
        name, args = OPCODES[opcode]
        assert name == mnemonic, f"0x{opcode:02X} is {name}, spec says {mnemonic}"
        assert len(args) == argc, f"{mnemonic} has {len(args)} operands, spec says {argc}"

def test_no_mnemonic_collisions():
    assert len(MNEMONICS) == len(OPCODES)

def test_constructor_body_roundtrip():
    """constructsuper (u30) used to desync every constructor body."""
    pool = ConstantPool()
    asm = (
        "    getlocal_0\n"
        "    pushscope\n"
        "    getlocal_0\n"
        "    constructsuper 0\n"
        "    returnvoid"
    )
    code = assemble_instructions(pool, asm)
    assert code == bytes([0xD0, 0x30, 0xD0, 0x49, 0x00, 0x47])
    assert assemble_instructions(pool, disassemble_instructions(pool, code)) == code

def test_forin_body_roundtrip():
    """hasnext2 (u30, u30) used to desync every for-in loop."""
    pool = ConstantPool()
    asm = "    hasnext2 1 2\n    iftrue L_0\nL_0:\n    returnvoid"
    code = assemble_instructions(pool, asm)
    assert code[0] == 0x32 and code[1] == 1 and code[2] == 2
    assert assemble_instructions(pool, disassemble_instructions(pool, code)) == code
