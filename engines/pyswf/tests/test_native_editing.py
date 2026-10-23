import io
import json
from pathlib import Path
import wave

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from py_swf.avm1 import assemble_avm1, disassemble_avm1
from py_swf.avm2 import ABCFile, assemble_instructions, disassemble_instructions
from py_swf.bytecode_validation import analyze_method, BytecodeValidationError
from py_swf.editing import edit_script
from py_swf.engines import NativePythonEngine
from py_swf.project import SwfProject
from py_swf.resources import replace_image, export_image
from py_swf.swf_parser import SWFFile, SWFTag
from server.app import app
from tests.native_fixtures import as3_tag, swf_with

client = TestClient(app)


@pytest.mark.parametrize("tag_type", [72, 82])
@pytest.mark.parametrize("signature", ["FWS", "CWS", "ZWS"])
def test_as3_source_edits_survive_save_reopen_and_project(tag_type, signature, tmp_path):
    tag = as3_tag(tag_type)
    original = swf_with([tag], signature)
    engine = NativePythonEngine()
    source = 'var n:int = 0; while(n < 3) { n++; if(n == 2) { continue; } trace("España: \\\"hola\\\" 🌍"); } return score + n;'
    edited = engine.compile_source(original, 0, source, body_index=1)
    reopened = SWFFile()
    reopened.read_bytes(edited)
    assert reopened.signature == signature
    assert reopened.tags[0].tag_type == tag_type
    abc = ABCFile()
    abc.parse(reopened.tags[0].parse_doabc()[2])
    body = abc.method_bodies[1]
    limits = analyze_method(abc, body)
    assert body.max_stack == limits.max_stack >= 2
    assert body.local_count >= 3
    assert body.max_scope_depth == limits.max_scope_depth == 1
    assert any('España: "hola" 🌍' == value for value in abc.constant_pool.strings)
    assert abc.methods[1].param_names == [abc.constant_pool.strings.index("score")]
    exact = disassemble_instructions(abc.constant_pool, body.code, exact=True)
    assert assemble_instructions(abc.constant_pool, exact) == body.code
    project = SwfProject("movie.swf", original, edited)
    project.snapshot("edited")
    path = project.to_archive(tmp_path / "movie.pyswfproj")
    restored = SwfProject.from_archive(path)
    assert restored.original == original
    assert restored.working == edited
    assert restored.engine == "native"


def test_call_argument_count_is_not_a_multiname_index():
    tag = as3_tag()
    new_tag, validation = edit_script(tag, 'trace("one"); return score;', 1, source=True)
    abc = ABCFile()
    abc.parse(new_tag.parse_doabc()[2])
    from py_swf.bytecode_validation import decode_avm2
    calls = [ins for ins in decode_avm2(abc.method_bodies[1].code) if ins.name == "callproperty"]
    assert len(calls) == 1
    assert calls[0].args[1] == 1
    assert validation["max_stack"] == 2


@pytest.mark.parametrize("code", ["pop\nreturnvoid", "getlocal -1\nreturnvoid", "pushtrue\niffalse missing\nreturnvoid"])
def test_invalid_as3_edit_is_rejected_without_history(code):
    original = swf_with([as3_tag()])
    sid = client.post("/api/files", files={"file": ("native.swf", original)}).json()["session_id"]
    response = client.post(f"/api/files/{sid}/tags/0/assemble", json={"body_index": 1, "code": code})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/files/{sid}/download").content == original
    assert client.get(f"/api/files/{sid}/history").json()["undo"] == 0


def test_stack_join_mismatch_and_non_boundary_branch_rejected():
    tag = as3_tag()
    abc = ABCFile()
    abc.parse(tag.parse_doabc()[2])
    body = abc.method_bodies[1]
    body.code = assemble_instructions(abc.constant_pool, 'pushtrue\niffalse other\npushbyte 1\njump end\nother:\nnop\nend:\nreturnvoid')
    with pytest.raises(BytecodeValidationError, match="join"):
        analyze_method(abc, body)
    body.code = bytes.fromhex("10 ff ff ff 47")
    with pytest.raises(BytecodeValidationError, match="branch"):
        analyze_method(abc, body)


def test_native_default_does_not_launch_java_even_when_configured(monkeypatch):
    from py_swf.decompile.backends import JPEXSBackend
    monkeypatch.setenv("PY_SWF_FFDEC_JAR", "configured-but-unnecessary.jar")
    monkeypatch.setattr(JPEXSBackend, "decompile", lambda *args, **kwargs: pytest.fail("Default launched JPEXS"))
    original = swf_with([as3_tag()])
    sid = client.post("/api/files", files={"file": ("native.swf", original)}).json()["session_id"]
    result = client.get(f"/api/files/{sid}/tags/0/decompile")
    assert result.status_code == 200, result.text
    assert result.json()["engine"] == "native"
    assert client.get("/api/files/engines/status").json()["default"] == "native"
    assert client.post(f"/api/files/{sid}/tags/0/compile-source", json={"body_index": 1, "source": 'return score + 20;'}).status_code == 200
    saved = client.get(f"/api/files/{sid}/download").content
    reopened_sid = client.post("/api/files", files={"file": ("saved.swf", saved)}).json()["session_id"]
    listing = client.get(f"/api/files/{reopened_sid}/tags/0/disassemble").json()
    assert "pushbyte 20" in listing["scripts"][1]["code"]


def test_exact_pcode_preserves_private_namespace_reference():
    tag = as3_tag()
    abc = ABCFile()
    abc.parse(tag.parse_doabc()[2])
    from py_swf.avm2 import search_or_add_string
    name = search_or_add_string(abc.constant_pool, "privateMember")
    abc.constant_pool.namespaces.append({"kind": 5, "name": 0})
    abc.constant_pool.multinames.append({"kind": 7, "ns": len(abc.constant_pool.namespaces) - 1, "name": name})
    index = len(abc.constant_pool.multinames) - 1
    code = assemble_instructions(abc.constant_pool, f"getlocal_0\ngetproperty #{index}\nreturnvalue")
    exact = disassemble_instructions(abc.constant_pool, code, exact=True)
    assert f"getproperty #{index}" in exact
    assert assemble_instructions(abc.constant_pool, exact) == code


def test_avm1_unicode_and_escaped_strings_roundtrip():
    value = 'España 🌍\n"quote" \\path'
    data = assemble_avm1(f'push {json.dumps(value, ensure_ascii=False)}\ntrace\nend')
    assert assemble_avm1(disassemble_avm1(data)) == data


def test_resource_replacement_reopen_and_failure_rollback():
    def png(color):
        buffer = io.BytesIO()
        Image.new("RGBA", (3, 2), color).save(buffer, "PNG")
        return buffer.getvalue()
    seed = SWFTag(36, b"\x07\x00\x05" + b"\x00" * 5)
    image = replace_image(seed, png((255, 0, 0, 255)), "png")
    original = swf_with([image], "CWS")
    sid = client.post("/api/files", files={"file": ("image.swf", original)}).json()["session_id"]
    endpoint = f"/api/files/{sid}/tags/0/replace"
    assert client.post(endpoint, files={"file": ("bad.png", b"invalid")}).status_code == 422
    assert client.get(f"/api/files/{sid}/history").json()["undo"] == 0
    replacement = png((0, 128, 255, 160))
    assert client.post(endpoint, files={"file": ("new.png", replacement)}).status_code == 200
    saved = client.get(f"/api/files/{sid}/download").content
    reopened_sid = client.post("/api/files", files={"file": ("saved.swf", saved)}).json()["session_id"]
    result = client.get(f"/api/files/{reopened_sid}/tags/0/export/image")
    image = Image.open(io.BytesIO(result.content))
    pixel = image.getpixel((0, 0))
    assert pixel[3] == 160 and pixel[2] == 255 and abs(pixel[1] - 128) <= 1


def test_source_edits_preserve_parameter_coercions():
    tag, _ = edit_script(as3_tag(), 'score = 1.5; return score;', 1, source=True)
    abc = ABCFile()
    abc.parse(tag.parse_doabc()[2])
    code = disassemble_instructions(abc.constant_pool, abc.method_bodies[1].code, exact=True)
    assert 'coerce_i' in code
    assert 'setlocal 1' in code


def test_bad_resource_export_is_blocked_and_original_available():
    original = swf_with([as3_tag()])
    sid = client.post('/api/files', files={'file': ('native.swf', original)}).json()['session_id']
    response = client.put(f'/api/files/{sid}/tags/0/raw', content=b'bad abc', headers={'Content-Type': 'application/octet-stream'})
    assert response.status_code == 422
    assert client.get(f'/api/files/{sid}/download').content == original
    assert client.get(f'/api/files/{sid}/history').json()['undo'] == 0


def test_native_edit_of_real_corpus_method_preserves_other_methods():
    from tests.conftest import JPEXS_TESTDATA
    path = JPEXS_TESTDATA / 'as3' / 'as3.swf'
    if not path.is_file():
        pytest.skip('Local corpus is unavailable')
    swf = SWFFile()
    swf.read_file(path)
    for tag in swf.tags:
        if not tag.is_doabc:
            continue
        old = ABCFile()
        old.parse(tag.parse_doabc()[2])
        for index, body in enumerate(old.method_bodies):
            method = old.methods[body.method]
            from py_swf.avm2 import resolve_multiname
            from py_swf.editing import source_edit_reason
            kind = resolve_multiname(old.constant_pool, method.return_type)
            if source_edit_reason(old, body) or kind not in ('*', 'void', 'int', 'uint', 'Number'):
                continue
            source = 'trace("real-native-saved"); ' + ('return;' if kind == 'void' else 'return 7;')
            edited, _ = edit_script(tag, source, index, source=True)
            reopened = ABCFile()
            reopened.parse(edited.parse_doabc()[2])
            assert 'real-native-saved' in reopened.constant_pool.strings
            assert reopened.constant_pool.integers == old.constant_pool.integers
            assert reopened.constant_pool.uintegers == old.constant_pool.uintegers
            for offset, other in enumerate(old.method_bodies):
                if offset != index:
                    assert reopened.method_bodies[offset].code == other.code
            assert reopened.instances[0].name == old.instances[0].name
            return
    pytest.fail('No supported ordinary method found in the real corpus')


@pytest.mark.parametrize('value', [-2147483648, -20000, -128, -1, 0, 127, 2147483647])
def test_signed_constants_survive_abc_serialization(value):
    tag = as3_tag()
    edited, _ = edit_script(tag, f'pushint {value}\nreturnvalue', 1)
    abc = ABCFile()
    abc.parse(edited.parse_doabc()[2])
    assert value in abc.constant_pool.integers
    exact = disassemble_instructions(abc.constant_pool, abc.method_bodies[1].code, exact=True)
    assert assemble_instructions(abc.constant_pool, exact) == abc.method_bodies[1].code


def test_standard_abc_s32_positive_short_and_negative_word_encodings():
    from py_swf.avm2 import ByteReader, ByteWriter
    assert ByteReader(bytes.fromhex('7f')).read_s32() == 127
    assert ByteReader(bytes.fromhex('40')).read_s32() == 64
    assert ByteReader(bytes.fromhex('ff ff ff ff 0f')).read_s32() == -1
    assert ByteReader(bytes.fromhex('80 80 80 80 08')).read_s32() == -2147483648
    writer = ByteWriter()
    writer.write_s32(127)
    assert writer.data == b'\x7f'


def test_signed_pushbyte_disassembly_and_source_after_reopen():
    tag, _ = edit_script(as3_tag(), 'pushbyte -1\nreturnvalue', 1)
    abc = ABCFile()
    abc.parse(tag.parse_doabc()[2])
    assert 'pushbyte -1' in disassemble_instructions(abc.constant_pool, abc.method_bodies[1].code)
    from py_swf.decompile.avm2_dec import decompile_method
    source, error = decompile_method(abc, abc.method_bodies[1])
    assert error is None and 'return -1;' in source
