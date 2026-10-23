"""An engine exists because engines/<folder>/engine.json says so, and nowhere else."""
import json

import pytest

from py_swf.benchmark_engines import ENGINES, engines_directory, load_engines

REQUIRED = {"id", "name", "category", "operations", "languages"}


def test_every_manifest_is_valid_and_ids_are_unique():
    folders = sorted(p for p in engines_directory().iterdir() if (p / "engine.json").is_file())
    assert folders, "engines/ has no manifests"
    ids = []
    for folder in folders:
        data = json.loads((folder / "engine.json").read_text(encoding="utf-8"))
        assert REQUIRED <= data.keys(), folder.name
        assert data["delivery"] in {"repo", "download", "user_install", "remote"}, folder.name
        ids.append(data["id"])
    assert len(ids) == len(set(ids))
    assert set(ids) == set(ENGINES)


def test_a_new_engine_needs_only_a_folder_and_a_manifest(tmp_path):
    (tmp_path / "toy").mkdir()
    (tmp_path / "toy/engine.json").write_text(json.dumps(
        {"id": "toy", "name": "Toy", "category": "decompiler", "operations": ["source"], "languages": ["as3"]}), encoding="utf-8")
    engines = load_engines(tmp_path)
    assert list(engines) == ["toy"] and engines["toy"].operations == ("source",)


def test_incomplete_or_duplicate_manifests_are_rejected(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad/engine.json").write_text(json.dumps({"id": "bad"}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load_engines(tmp_path)
    (tmp_path / "bad/engine.json").unlink()
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "engine.json").write_text(json.dumps(
            {"id": "same", "name": name, "category": "decompiler", "operations": [], "languages": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_engines(tmp_path)
