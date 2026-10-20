import hashlib
import json
import zipfile
import zlib

import pytest
from fastapi.testclient import TestClient

from py_swf.avm1 import assemble_avm1, disassemble_avm1
from py_swf.engines import NativePythonEngine
from py_swf.project import SwfProject
from py_swf.script_inventory import enumerate_scripts
from py_swf.swf_parser import SWFFile, SWFTag
from py_swf.validation import validate_swf_bytes
from server.app import app
from tests.native_fixtures import as3_tag, swf_with


def test_http_validation_rejects_same_bad_abc_as_core():
    data = swf_with([SWFTag(82, b"\0\0\0\0bad\0broken ABC")])
    response = TestClient(app).post("/api/files/validate", files={"file": ("bad.swf", data)})
    assert response.json() == validate_swf_bytes(data)
    assert not response.json()["valid"]
    import io
    from py_swf.robust_parser import validate_swf
    assert validate_swf(io.BytesIO(data))[0] is False


def test_nested_end_payload_and_opaque_abc_tail_are_rejected_for_editing():
    nested = SWFTag(39, b"\x01\0\x01\0" + SWFTag(0, b"not empty").pack())
    assert not validate_swf_bytes(swf_with([nested]))["valid"]
    tag = as3_tag()
    malformed = SWFTag(82, tag.data + b"opaque tail")
    assert not validate_swf_bytes(swf_with([malformed]))["valid"]
    with pytest.raises(ValueError, match="Trailing data"):
        NativePythonEngine().compile_source(swf_with([malformed]), 0, 'return 1;', 1)


@pytest.mark.parametrize("signature", ["FWS", "CWS", "ZWS"])
def test_strict_validation_checks_actual_decompressed_length(signature):
    original = swf_with([], signature)
    tampered = original[:4] + (9).to_bytes(4, "little") + original[8:]
    assert not validate_swf_bytes(tampered)["valid"]
    parsed = SWFFile()
    parsed.read_bytes(tampered)  # Inspection remains tolerant of a false length.


def test_limit_stops_cws_and_tolerant_recovery_before_large_allocation(monkeypatch):
    import py_swf.swf_parser as parser
    from py_swf.robust_parser import read_swf_bytes_robust
    monkeypatch.setattr(parser, "MAX_DECOMPRESSED_BYTES", 512)
    bomb = b"CWS\x0f" + (9).to_bytes(4, "little") + zlib.compress(b"A" * 4096)
    with pytest.raises(ValueError, match="limit"):
        SWFFile().read_bytes(bomb)
    with pytest.raises(Exception, match="limit"):
        read_swf_bytes_robust(bomb)
    fws = b"FWS\x0f" + (9).to_bytes(4, "little") + b"A" * 4096
    with pytest.raises(ValueError, match="limit"):
        SWFFile().read_bytes(fws)


@pytest.mark.parametrize("endpoint", ["", "/robust-parse", "/validate", "/repair", "/projects"])
def test_all_upload_entrypoints_enforce_streaming_limit(monkeypatch, endpoint):
    import server.config as config
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 32)
    response = TestClient(app).post("/api/files" + endpoint, files={"file": ("oversized.swf", b"x" * 33)})
    assert response.status_code == 413


def event_movie(signature="FWS"):
    first = assemble_avm1('push "first"\ntrace\nend')
    second = assemble_avm1('push "second"\ntrace\nend')
    # Keep a noncanonical long header for a tiny sibling; edits preserve its raw record.
    sibling = ((87 << 6) | 63).to_bytes(2, "little") + (7).to_bytes(4, "little") + b"\x09\0\0\0\0\0x"
    inner = SWFTag(39, b"\x07\0\x01\0" + SWFTag(12, first).pack() + sibling + SWFTag(0, b"").pack())
    sprite = SWFTag(39, b"\x08\0\x01\0" + inner.pack() + SWFTag(0, b"").pack())
    records = (len(first) + 4).to_bytes(2, "little") + b"\x01\0" + first + b"\0\0\x02\0" + second
    button2 = SWFTag(34, b"\x03\0\0\x03\0\0" + records)
    key, event = 0x20000, 1
    clip = SWFTag(26, b"\x80\x01\0\0\0" + (key | event).to_bytes(4, "little") + key.to_bytes(4, "little") + (len(first) + 1).to_bytes(4, "little") + b"A" + first + event.to_bytes(4, "little") + len(second).to_bytes(4, "little") + second + b"\0" * 4)
    return swf_with([sprite, button2, clip], signature), sibling


@pytest.mark.parametrize("signature", ["FWS", "CWS", "ZWS"])
@pytest.mark.parametrize("document", ["tag:0/0/0/actions", "tag:1/button2:0", "tag:1/button2:1", "tag:2/clip:0", "tag:2/clip:1"])
def test_nested_and_event_edits_preserve_other_units_and_metadata(signature, document):
    original, sibling = event_movie(signature)
    before = {unit.document_id: unit for unit in enumerate_scripts(original)}
    edited = NativePythonEngine().edit_unit(original, before[document].metadata(), 'trace("a substantially longer replacement");', source=True)
    after = {unit.document_id: unit for unit in enumerate_scripts(edited)}
    assert after.keys() == before.keys()
    assert b"replacement" in after[document].code
    for key, unit in before.items():
        assert after[key].event_flags == unit.event_flags
        if key != document:
            assert after[key].code == unit.code
    parsed = SWFFile()
    parsed.read_bytes(edited)
    assert sibling in parsed.tags[0].data
    assert b"A" in parsed.tags[2].data  # KeyCode is retained outside action bytes.
    assert validate_swf_bytes(edited)["valid"]
    assert list(enumerate_scripts(original))[0].code == before["tag:0/0/0/actions"].code


def test_stale_or_invalid_unit_edit_never_changes_input():
    original, _ = event_movie()
    locator = list(enumerate_scripts(original))[0].metadata()
    locator["bytecode_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="stale"):
        NativePythonEngine().edit_unit(original, locator, "end")
    with pytest.raises(ValueError):
        NativePythonEngine().edit_unit(original, "tag:1/button2:0", "jump missing_label")


def test_nested_abc_method_can_be_edited_by_method_identity():
    original = swf_with([SWFTag(39, b"\x01\0\x01\0" + as3_tag().pack() + SWFTag(0, b"").pack())])
    edited = NativePythonEngine().edit_unit(original, "tag:0/0/method:1", 'return "changed";', source=True)
    assert any(b"changed" in unit.abc.constant_pool.strings[-1].encode() for unit in enumerate_scripts(edited) if unit.language == "as3")


def test_v3_metadata_and_v2_compatibility_preserve_original_sources_history(tmp_path):
    data = swf_with([])
    project = SwfProject("movie.swf", data, data, history=[{"operation": "authored"}], source_documents={"doc": {"source": "trace(1);"}}, suite_metadata={"selected_engine": "python"}, toolchains={"flex": {"version": "test"}})
    path = project.to_archive(tmp_path / "new.pyswfproj")
    reopened = SwfProject.from_archive(path)
    assert reopened.suite_metadata == project.suite_metadata and reopened.toolchains == project.toolchains
    assert reopened.original == data and reopened.source_documents == project.source_documents and reopened.history == project.history
    old = tmp_path / "v2.pyswfproj"
    with zipfile.ZipFile(path) as archive, zipfile.ZipFile(old, "w") as writer:
        for name in archive.namelist():
            value = archive.read(name)
            if name == "manifest.json":
                manifest = json.loads(value)
                manifest["format_version"] = 2
                value = json.dumps(manifest).encode()
            writer.writestr(name, value)
    snapshot = old.read_bytes()
    legacy = SwfProject.from_archive(old)
    assert legacy.source_documents == project.source_documents and legacy.original == data
    assert legacy.suite_metadata == {} and old.read_bytes() == snapshot


def test_project_zip_budget_is_checked_before_reading_expanded_entries(tmp_path, monkeypatch):
    import py_swf.project as project
    path = tmp_path / "bomb.pyswfproj"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr("original.swf", b"x" * 1000)
        archive.writestr("working.swf", b"x" * 1000)
    monkeypatch.setattr(project, "MAX_PROJECT_BYTES", 1024)
    with pytest.raises(ValueError, match="expanded-size"):
        SwfProject.from_archive(path)
