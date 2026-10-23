"""Round-trip tests for AVM1 function-body decoding (DefineFunction/2, With)."""
import struct

from py_swf.avm1 import assemble_avm1, disassemble_avm1

def _record(code, payload):
    return bytes([code]) + struct.pack("<H", len(payload)) + payload

def test_define_function2_roundtrip():
    body = bytes([0x96, 4, 0, 4, 1, 4, 2]) + bytes([0x0A, 0x3E])  # push r1 r2; add; return
    payload = b"suma\x00" + struct.pack("<H", 2) + bytes([3]) + struct.pack("<H", 0)
    payload += bytes([1]) + b"a\x00" + bytes([2]) + b"b\x00"
    payload += struct.pack("<H", len(body))
    code = _record(0x8E, payload) + body + bytes([0x26, 0x00])

    asm = disassemble_avm1(code)
    assert 'define_function2 "suma" 3 0 [r:1 "a", r:2 "b"]' in asm
    assert "return" in asm
    assert assemble_avm1(asm) == code

def test_define_function_and_with_roundtrip():
    fn_body = bytes([0x07])  # stop
    fn_payload = b"f\x00" + struct.pack("<H", 0) + struct.pack("<H", len(fn_body))
    with_body = bytes([0x06])  # play
    with_payload = struct.pack("<H", len(with_body))
    code = (
        _record(0x9B, fn_payload) + fn_body
        + _record(0x94, with_payload) + with_body
        + bytes([0x00])
    )

    asm = disassemble_avm1(code)
    assert 'define_function "f" []' in asm
    assert "with L_" in asm
    assert assemble_avm1(asm) == code

def test_block_end_at_stream_end():
    """A function body reaching the end of the stream still gets its end label."""
    fn_body = bytes([0x07])
    fn_payload = b"g\x00" + struct.pack("<H", 0) + struct.pack("<H", len(fn_body))
    code = _record(0x9B, fn_payload) + fn_body

    asm = disassemble_avm1(code)
    assert asm.rstrip().endswith(":")
    assert assemble_avm1(asm) == code
