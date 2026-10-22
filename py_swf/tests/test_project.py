from pathlib import Path

from py_swf.project import SwfProject
from tests.conftest import make_synthetic_swf


def test_project_roundtrip_and_original_is_preserved(tmp_path: Path):
    original = make_synthetic_swf()
    project = SwfProject("movie.swf", original, original)
    project.snapshot("original")
    project.apply(make_synthetic_swf(signature="CWS"), "test_change", {"tag": 0})
    target = tmp_path / "movie.pyswfproj"
    project.to_archive(target)

    loaded = SwfProject.from_archive(target)
    assert loaded.filename == "movie.swf"
    assert loaded.original == original
    assert loaded.validate()["valid"]
    assert "original" in loaded.snapshots
    assert loaded.history[0]["operation"] == "test_change"


def test_project_rejects_invalid_working_bytes():
    original = make_synthetic_swf()
    project = SwfProject("movie.swf", original, original)
    try:
        project.apply(b"not swf", "bad")
    except ValueError as exc:
        assert "invalid SWF" in str(exc)
    else:
        raise AssertionError("invalid working bytes must be rejected")


def test_project_archive_rejects_invalid_working_bytes(tmp_path: Path):
    project = SwfProject("bad.swf", b"FWS", b"not-a-swf")
    try:
        project.to_archive(tmp_path / "bad.pyswfproj")
    except ValueError as exc:
        assert "invalid working SWF" in str(exc)
    else:
        raise AssertionError("invalid project must not be archived")


def test_identical_snapshots_keep_their_own_names(tmp_path: Path):
    data = make_synthetic_swf()
    project = SwfProject("movie.swf", data, data)
    project.snapshot("before")
    project.snapshot("after-sources-only")  # same bytes, different snapshot
    target = tmp_path / "movie.pyswfproj"
    project.to_archive(target)

    loaded = SwfProject.from_archive(target)
    assert set(loaded.snapshots) == {"before", "after-sources-only"}
    assert loaded.snapshots["after-sources-only"].data == data
