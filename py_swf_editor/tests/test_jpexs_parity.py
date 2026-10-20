import io
import wave
import pytest

from py_swf.compiler import compile_actionscript
from py_swf.avm2 import ABCFile, assemble_instructions
from py_swf.sounds import parse_define_sound, replace_define_sound
from py_swf.text_fonts import parse_edit_text, replace_edit_text_string
from py_swf.decompile.deobfuscate import deobfuscate_script
from py_swf.swf_parser import SWFTag, make_rect


def test_as3_compiler_basic():
    code = 'trace("Hello World"); stop(); score = score + 10;'
    asm = compile_actionscript(code, target="as3")
    assert "findpropstrict trace" in asm
    assert 'pushstring "Hello World"' in asm
    assert "findpropstrict stop" in asm
    assert "add" in asm

    abc = ABCFile()
    bytecode = assemble_instructions(abc.constant_pool, asm)
    assert len(bytecode) > 0


def test_as3_compiler_if_else():
    code = 'if (score > 100) { win(); } else { lose(); }'
    asm = compile_actionscript(code, target="as3")
    assert "greaterthan" in asm
    assert "iffalse" in asm
    assert "win" in asm
    assert "lose" in asm


def test_as1_compiler():
    code = 'stop(); score = 10;'
    asm = compile_actionscript(code, target="as1")
    assert "ActionStop" in asm
    assert "ActionSetVariable" in asm


def test_replace_sound_wav():
    # Create valid synthetic WAV
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 100)
    wav_bytes = buf.getvalue()

    dummy_tag = SWFTag(14, b"\x05\x00\x00\x00\x00\x00\x00")
    new_tag = replace_define_sound(dummy_tag, wav_bytes, extension="wav")
    assert new_tag.tag_type == 14
    info = parse_define_sound(new_tag)
    assert info["sound_id"] == 5
    assert info["rate"] == 44100
    assert info["format"] == 3  # PCM LE
    assert info["channels"] == 1


def test_replace_sound_mp3():
    dummy_tag = SWFTag(14, b"\x0A\x00\x00\x00\x00\x00\x00")
    mp3_data = b"\xFF\xFB\x90\x64" + b"\x00" * 200
    new_tag = replace_define_sound(dummy_tag, mp3_data, extension="mp3")
    assert new_tag.tag_type == 14
    info = parse_define_sound(new_tag)
    assert info["sound_id"] == 10
    assert info["format"] == 2  # MP3


def test_replace_edit_text():
    stream_data = bytearray(b"\x01\x00")  # char_id = 1
    stream_data.extend(make_rect(0, 1000, 0, 500))
    stream_data.extend(b"\x80\x00")  # f1 has_text=1, f2=0
    stream_data.extend(b"myVar\x00Original text\x00")

    tag = SWFTag(37, bytes(stream_data))
    orig = parse_edit_text(tag)
    assert orig["initial_text"] == "Original text"

    new_tag = replace_edit_text_string(tag, "Texto Reemplazado!")
    replaced = parse_edit_text(new_tag)
    assert replaced["initial_text"] == "Texto Reemplazado!"
    assert replaced["variable_name"] == "myVar"
    assert replaced["char_id"] == 1


def test_deobfuscator():
    obf_code = 'var §_--_§:int = 5; if (false) { dead(); }'
    cleaned = deobfuscate_script(obf_code)
    assert "§_--_§" not in cleaned
    assert "var_1" in cleaned
    assert "dead()" not in cleaned
