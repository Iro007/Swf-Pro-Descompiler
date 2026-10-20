"""Tests de sonidos (WAV/ADPCM/MP3) y fuentes/textos."""
import io
import struct
import wave

import pytest

from py_swf import sounds, text_fonts
from py_swf.swf_parser import SWFFile, SWFTag, collect_symbol_names
from tests.conftest import JPEXS_TESTDATA

def _define_sound(fmt, rate_idx, bits16, stereo, sample_count, payload):
    info = (fmt << 4) | (rate_idx << 2) | (int(bits16) << 1) | int(stereo)
    data = (7).to_bytes(2, "little") + bytes([info]) + sample_count.to_bytes(4, "little") + payload
    return SWFTag(14, data)

def test_pcm16_to_wav():
    samples = struct.pack("<4h", 0, 1000, -1000, 32767)
    tag = _define_sound(3, 3, True, False, 4, samples)
    data, ext = sounds.export_sound(tag)
    assert ext == "wav"
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 44100
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.readframes(4) == samples

def test_mp3_passthrough():
    frames = b"\xff\xfb\x90\x00" + b"\x00" * 16
    tag = _define_sound(2, 2, True, False, 100, b"\x00\x00" + frames)
    data, ext = sounds.export_sound(tag)
    assert ext == "mp3"
    assert data == frames

def test_adpcm_decodes_nonempty():
    # 2-bit codes (code_bits=2): header UB2=0, luego initial SB16 + index UB6 + codes
    bits = "00"                      # code size -> 2 bits
    bits += format(100 & 0xFFFF, "016b")  # initial sample = 100
    bits += format(0, "06b")         # initial index
    bits += "01" * 30                # 30 códigos "subir"
    padded = bits + "0" * ((8 - len(bits) % 8) % 8)
    payload = int(padded, 2).to_bytes(len(padded) // 8, "big")
    tag = _define_sound(1, 1, True, False, 31, payload)
    data, ext = sounds.export_sound(tag)
    assert ext == "wav"
    with wave.open(io.BytesIO(data)) as w:
        pcm = w.readframes(w.getnframes())
    values = struct.unpack(f"<{len(pcm)//2}h", pcm)
    assert values[0] == 100
    # los códigos de magnitud 1 sin signo deben incrementar monótonamente
    assert values[-1] > values[0]

def test_symbol_names():
    payload = (2).to_bytes(2, "little")
    payload += (5).to_bytes(2, "little") + b"com.Game\x00"
    payload += (9).to_bytes(2, "little") + b"Logo\x00"
    names = collect_symbol_names([SWFTag(76, payload)])
    assert names == {5: "com.Game", 9: "Logo"}

@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="no corpus")
def test_font_and_text_from_corpus():
    path = JPEXS_TESTDATA / "as3" / "as3.swf"
    if not path.is_file():
        pytest.skip("as3.swf not present")
    swf = SWFFile()
    swf.read_bytes(path.read_bytes())
    fonts = text_fonts.collect_fonts(swf.tags)
    assert fonts, "as3.swf should contain a DefineFont3"
    font = next(iter(fonts.values()))
    assert font["num_glyphs"] > 0
    assert any(font["glyphs"]), "glyphs should have path records"
    svg = text_fonts.font_to_svg(font)
    assert "<path" in svg

    text_tags = [t for t in swf.tags if t.tag_type in (11, 33)]
    assert text_tags
    parsed = text_fonts.parse_text_tag(text_tags[0])
    svg = text_fonts.text_to_svg(parsed, fonts)
    assert "<path" in svg or "<text" in svg
