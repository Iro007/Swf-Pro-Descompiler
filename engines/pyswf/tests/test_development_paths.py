import hashlib
import json
from pathlib import Path

import pytest

from py_swf.development_paths import normalize_development_config, resolve_development_path


def test_relocation_prefers_current_copy_even_if_old_file_exists(tmp_path):
    old = tmp_path / "old" / ".dev-references" / "tool.exe"
    current_root = tmp_path / "moved"
    local = current_root / "vendor" / "references" / "tool.exe"
    old.parent.mkdir(parents=True)
    local.parent.mkdir(parents=True)
    old.write_bytes(b"old copy")
    local.write_bytes(b"current copy")
    assert resolve_development_path(str(old), current_root) == local


def test_windows_manifest_rebases_paths_classpath_and_hash_keys(tmp_path):
    root = tmp_path / "project"
    stale = r"C:\old\py_swf\.dev-references\tool.exe"
    digest = hashlib.sha256(b"artifact").hexdigest()
    config = {"executable": stale, "artifact_sha256": {stale: digest},
              "reference_classpath": r"C:\old\py_swf\.dev-references\bridge;C:\old\py_swf\.dev-references\library\lib.jar"}
    normalized = normalize_development_config(config, root)
    expected = str(root / "vendor" / "references" / "tool.exe")
    assert normalized["executable"] == expected
    assert normalized["artifact_sha256"] == {expected: digest}
    assert "old" not in normalized["reference_classpath"]
    assert config["executable"] == stale


@pytest.mark.parametrize("value", [r".dev-references\..\outside.exe", "../outside.exe"])
def test_parent_traversal_is_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="traversal"):
        resolve_development_path(value, tmp_path / "project")


def test_explicit_external_artifact_outside_workspace_and_manifest_is_rejected(tmp_path):
    root = tmp_path / "workspace"
    external = tmp_path / "external" / "tool.exe"
    with pytest.raises(ValueError, match="outside"):
        resolve_development_path(str(external), root, root / "vendor" / "references")


def test_relocated_tool_hash_is_not_replaced_or_silently_accepted(tmp_path, monkeypatch):
    import py_swf.benchmark_engines as registry
    root = tmp_path / "project"
    references = root / "vendor" / "references"
    references.mkdir(parents=True)
    local = references / "tool.exe"
    local.write_bytes(b"changed artifact")
    stale = r"C:\old\project\.dev-references\tool.exe"
    config = references / "extra-tools.json"
    config.write_text(json.dumps({"development_only": True, "tools": {
        "flare": {"executable": stale, "artifact_sha256": {
            stale: hashlib.sha256(b"original artifact").hexdigest()
        }}
    }}))
    monkeypatch.setattr(registry, "WORKSPACE", root)
    monkeypatch.setattr(registry, "REFERENCES", references)
    with pytest.raises(ValueError, match="hash mismatch"):
        registry.checked_tool("flare", config)


def test_manifest_owned_relative_paths_keep_hash_keys_consistent(tmp_path):
    root = tmp_path / "workspace"
    config_dir = tmp_path / "custom-manifest"
    config_dir.mkdir()
    normalized = normalize_development_config(
        {"executable": "./tool.exe", "artifact_sha256": {"./tool.exe": "a" * 64}},
        root, config_dir,
    )
    assert normalized["artifact_sha256"][normalized["executable"]] == "a" * 64
