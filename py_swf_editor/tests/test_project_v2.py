import hashlib
import json
import zipfile

import pytest

from py_swf.project import SwfProject, FORMAT_VERSION
from tests.conftest import make_synthetic_swf


def test_v2_preserves_saved_sources_and_original(tmp_path):
    data=make_synthetic_swf()
    docs={"tag:0/action":{"document_id":"tag:0/action","source":"trace(\"España 🌍\");","working_swf_sha256":hashlib.sha256(data).hexdigest(),"kind":"action"}}
    project=SwfProject("movie.swf",data,data,source_documents=docs)
    path=project.to_archive(tmp_path / "movie.pyswfproj")
    loaded=SwfProject.from_archive(path)
    assert loaded.original==data and loaded.source_documents==docs
    with zipfile.ZipFile(path) as archive:
        manifest=json.loads(archive.read("manifest.json"))
        assert manifest["format_version"]==FORMAT_VERSION
        assert hashlib.sha256(archive.read("source-documents.json")).hexdigest()==manifest["source_documents_sha256"]


def test_v1_migrates_in_memory_without_writing_old_archive(tmp_path):
    data=make_synthetic_swf()
    path=tmp_path / "old.pyswfproj"
    manifest={"format_version":1,"filename":"old.swf","original_sha256":hashlib.sha256(data).hexdigest(),"working_sha256":hashlib.sha256(data).hexdigest()}
    with zipfile.ZipFile(path,"w") as archive:
        archive.writestr("manifest.json",json.dumps(manifest))
        archive.writestr("original.swf",data)
        archive.writestr("working.swf",data)
    before=path.read_bytes()
    project=SwfProject.from_archive(path)
    assert project.source_documents=={} and path.read_bytes()==before
    migrated=project.to_archive(tmp_path / "migrated.pyswfproj")
    with zipfile.ZipFile(migrated) as archive:
        assert json.loads(archive.read("manifest.json"))["format_version"]==FORMAT_VERSION
    assert path.read_bytes()==before


def test_tampered_source_cache_is_rejected(tmp_path):
    data=make_synthetic_swf()
    source=SwfProject("movie.swf",data,data).to_archive(tmp_path / "original.pyswfproj")
    broken=tmp_path / "tampered.pyswfproj"
    with zipfile.ZipFile(source) as original,zipfile.ZipFile(broken,"w") as archive:
        for item in original.infolist():
            archive.writestr(item,b'{"tampered":{}}' if item.filename=="source-documents.json" else original.read(item.filename))
    with pytest.raises(ValueError,match="document checksum"):
        SwfProject.from_archive(broken)
