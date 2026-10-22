import hashlib
import json
import sqlite3
import stat
import zipfile

import pytest

from py_swf.benchmark_catalog import (
    catalog_summary, export_catalog_html, import_cloud_bundle, import_local_report,
    render_catalog_html,
)


def report():
    return {"schema_version": 1, "created_at": "2026-10-13T00:00:00Z", "operation": "source",
            "environment": {"platform": "Windows", "python": "3.14"},
            "provenance": {"implementation_sha256_before": "a" * 64, "implementation_unchanged": True},
            "engine_catalog": [{"id": "native", "name": "Python nativo", "version": "0.3.0"}],
            "cases": [{"relative": "same.swf", "sha256": "b" * 64, "bytes": 5,
                       "engines": [{"engine": "native", "status": "measured",
                                    "quality": {"expected_units_reported": 2, "source_available": 1, "unit_mapping": "canonical"},
                                    "cold_samples": [{"ok": True, "wall_ms": 5, "total_with_startup_ms": 105}],
                                    "hot_samples": [{"ok": True, "wall_ms": 1, "peak_tree_rss_bytes": 1000, "memory_samples": 0},
                                                    {"ok": True, "wall_ms": 3, "peak_tree_rss_bytes": 2000, "memory_samples": 1},
                                                    {"ok": False, "wall_ms": 100},
                                                    {"ok": False, "timed_out": True, "wall_ms": 1000}],
                                    "warmup_samples": [{"ok": True, "wall_ms": 500}]}]}]}


def save_report(tmp_path, value=None, name="benchmark.json"):
    path = tmp_path / name
    path.write_text(json.dumps(value or report()), encoding="utf-8")
    return path


def bundle(tmp_path, members, name="cloud.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for key, value in members.items():
            archive.writestr(key, value if isinstance(value, bytes) else json.dumps(value))
    return path


def cloud_members():
    raw = {"tool": "pyflashkit", "file": "corpus/same.swf", "operation": "inspect",
           "exit_code": 0, "wall_ms": 20, "peak_tree_rss_bytes": 512, "coverage_verified": False}
    return {"results/runs.jsonl": (json.dumps(raw) + "\n" + json.dumps(raw) + "\n").encode(),
            "corpus/manifest.json": {"artifacts": [{"path": "corpus/same.swf", "sha256": "b" * 64, "avm": "AVM2"}]},
            "environment.json": {"platform": "Linux", "label": "Codex cloud"},
            "tools/pyflashkit/tool_manifest.json": {"id": "pyflashkit", "version": "1.0", "name": "pyflashkit"},
            "tools/rabcdasm/tool_manifest.json": {"id": "rabcdasm", "version": "1.19dev", "name": "RABCDAsm"},
            "results/internal_reimport.json": {"status": "accepted_internal_import", "independent_compilation_status": "not_measured"}}


def test_local_reimport_idempotent_retains_repetitions_and_boundaries(tmp_path):
    database, source = tmp_path / "catalog.db", save_report(tmp_path)
    first = import_local_report(database, source)
    copied = tmp_path / "copy.json"
    copied.write_text(json.dumps(report(), indent=4))
    second = import_local_report(database, copied)
    assert first["imported"] and not second["imported"]
    assert first["campaign_id"] == second["campaign_id"]
    assert first["run_count"] == 6
    summary = catalog_summary(database)
    assert summary["totals"] == {"campaigns": 1, "artifacts": 1, "runs": 6, "tools": 1}
    rows = {row["phase"]: row for row in summary["rows"]}
    assert rows["hot"]["median_ms"] == 2
    assert rows["cold"]["median_ms"] == 105
    assert rows["warmup"]["median_ms"] == 500
    assert rows["hot"]["median_error_ms"] == 100
    assert rows["hot"]["median_timeout_ms"] == 1000
    assert rows["hot"]["success_count"] == 2
    assert rows["hot"]["error_count"] == rows["hot"]["timeout_count"] == 1
    assert rows["hot"]["peak_tree_rss_bytes"] == 2000
    assert rows["hot"]["coverage_percent"] == 50
    assert rows["hot"]["engine_name"] == "PySWF"
    assert summary["claims"] == {"parity": False, "superiority": False, "performance_winner": None}


def test_distinct_campaigns_same_filename_different_sha_and_environment(tmp_path):
    database = tmp_path / "catalog.db"
    import_local_report(database, save_report(tmp_path))
    changed = report()
    changed["created_at"] = "2026-10-13T01:00:00Z"
    changed["cases"][0]["sha256"] = "c" * 64
    changed["environment"]["platform"] = "Linux"
    second = import_local_report(database, save_report(tmp_path, changed, "changed.json"))
    summary = catalog_summary(database, campaign=second["campaign_id"], limit=1)
    assert summary["totals"]["campaigns"] == summary["totals"]["artifacts"] == 2
    assert len(summary["rows"]) == 1 and summary["truncated"]
    assert summary["rows"][0]["artifact_sha256"] == "c" * 64
    assert summary["rows"][0]["platform"] == "Linux"


def test_unmapped_unconfigured_and_disassembly_do_not_claim_source_coverage(tmp_path):
    value = report()
    value["cases"][0]["engines"][0]["quality"]["unit_mapping"] = "not_verified"
    value["cases"][0]["engines"].append({"engine": "flare", "status": "not_configured"})
    value["cases"][0]["engines"].append({"engine": "flasm", "status": "output_unmapped", "operation": "disassemble", "quality": {"unit_mapping": "canonical", "source_available": 2, "expected_units_reported": 2}})
    database = tmp_path / "catalog.db"
    import_local_report(database, save_report(tmp_path, value))
    summary = catalog_summary(database)
    assert all(row["coverage_percent"] is None for row in summary["rows"])
    native = catalog_summary(database, engine="native")["rows"][0]
    assert native["source_available"] is None
    flare = catalog_summary(database, engine="flare")["rows"][0]
    assert flare["run_count"] == 0 and flare["status"] == "not_configured"


def test_cloud_preserves_duplicates_separate_tool_and_missing_quality(tmp_path):
    database = tmp_path / "catalog.db"
    import_local_report(database, save_report(tmp_path))
    archive = bundle(tmp_path, cloud_members())
    result = import_cloud_bundle(database, archive, hashlib.sha256(archive.read_bytes()).hexdigest())
    assert result["run_count"] == 2 and result["artifact_count"] == 1
    assert not import_cloud_bundle(database, archive)["imported"]
    summary = catalog_summary(database, engine="pyflashkit")
    row = summary["rows"][0]
    assert row["run_count"] == row["success_count"] == 2
    assert row["operation"] == "inspect" and row["phase"] == "cold"
    assert row["coverage_percent"] is None and row["source_available"] is None
    assert row["compilation"] == "not_measured" and row["behavior"] == "not_measured"
    assert row["language"] == "AVM2"
    assert {tool["id"] for tool in summary["tools"]} == {"native", "pyflashkit", "rabcdasm"}
    assert summary["campaigns"][0]["source_kind"] == "cloud_bundle"


def test_bundle_and_manifest_hashes_and_traversal_checked_before_database(tmp_path):
    database = tmp_path / "catalog.db"
    archive = bundle(tmp_path, cloud_members())
    with pytest.raises(ValueError, match="SHA-256"):
        import_cloud_bundle(database, archive, "0" * 64)
    assert not database.exists()
    bad = bundle(tmp_path, {"../outside.py": b"", **cloud_members()}, "traversal.zip")
    with pytest.raises(ValueError, match="Unsafe"):
        import_cloud_bundle(database, bad)
    members = cloud_members()
    members["corpus/same.swf"] = b"different"
    bad_hash = bundle(tmp_path, members, "bad-hash.zip")
    with pytest.raises(ValueError, match="Manifest SHA-256"):
        import_cloud_bundle(database, bad_hash)
    assert not database.exists()


def test_zip_symlink_rejected(tmp_path):
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(info, "somewhere")
    with pytest.raises(ValueError, match="Unsafe"):
        import_cloud_bundle(tmp_path / "catalog.db", archive)


def test_import_is_atomic_when_case_is_invalid(tmp_path):
    value = report()
    value["cases"].append("invalid")
    database = tmp_path / "catalog.db"
    with pytest.raises(ValueError, match="case"):
        import_local_report(database, save_report(tmp_path, value))
    assert catalog_summary(database)["totals"]["campaigns"] == 0
    assert catalog_summary(database)["totals"]["runs"] == 0


def test_summary_is_read_only_and_missing_database_is_not_created(tmp_path):
    database = tmp_path / "catalog.db"
    with pytest.raises(FileNotFoundError):
        catalog_summary(database)
    assert not database.exists()
    import_local_report(database, save_report(tmp_path))
    before = database.read_bytes()
    summary = catalog_summary(database, operation="source", environment="Windows", engine="native")
    assert summary["rows"]
    assert before == database.read_bytes()


def test_html_is_standalone_and_script_content_escaped(tmp_path):
    value = report()
    value["cases"][0]["relative"] = "</script><script>bad()</script>"
    database = tmp_path / "catalog.db"
    import_local_report(database, save_report(tmp_path, value))
    path = export_catalog_html(database, tmp_path / "catalog.html")
    page = __import__("pathlib").Path(path).read_text(encoding="utf-8")
    assert "<script>bad()</script>" not in page
    assert "\\u003c/script>" in page and "textContent" in page
    assert "<title>Catálogo de benchmarks Flash</title>" in page
    assert "https://" not in page


def test_cloud_heap_probe_never_deduplicates_or_mixes_with_main_runs(tmp_path):
    members = cloud_members()
    members["heap-probe/results/runs.jsonl"] = json.dumps({"tool": "pyflashkit", "file": "corpus/same.swf", "operation": "inspect", "exit_code": 0, "wall_ms": 1000}).encode()
    database = tmp_path / "catalog.db"
    result = import_cloud_bundle(database, bundle(tmp_path, members))
    assert result["run_count"] == 3
    rows = catalog_summary(database)["rows"]
    assert {row["phase"] for row in rows} == {"cold", "heap_probe"}
    assert sorted(row["median_ms"] for row in rows) == [20, 1000]


def test_confirmed_cloud_ids_operations_tools_and_wall_seconds(tmp_path):
    raw = {"tool_id": "ffdec", "artifact_id": "micro01", "input_sha256": "a" * 64,
           "input_bytes": 128, "operation": "decompile_as", "wall_s": 0.25, "exit_code": 0,
           "peak_tree_rss_bytes": 1000, "status": "completed", "as_error_marker_count": 2}
    members = {"results/runs.jsonl": json.dumps(raw).encode(),
               "corpus/manifest.json": {"artifacts": [{"id": "micro01", "path": "corpus/micro.swf", "sha256": "a" * 64, "avm": "AVM1"}]},
               "tools/ffdec/tool_manifest.json": {"id": "ffdec", "name": "JPEXS", "version": "26.3.0", "commands": {}},
               "tools/pyflashkit.json": {"id": "pyflashkit", "version": "0.2", "commands": {}},
               "tools/flasm.json": {"id": "flasm", "version": "1.62", "commands": {}},
               "tools/rabcdasm.json": {"id": "rabcdasm", "version": "1.19dev", "commands": {}}}
    database = tmp_path / "catalog.db"
    import_cloud_bundle(database, bundle(tmp_path, members))
    summary = catalog_summary(database)
    assert {tool["id"] for tool in summary["tools"]} == {"jpexs", "pyflashkit", "flasm", "rabcdasm"}
    row = summary["rows"][0]
    assert row["engine"] == "jpexs" and row["operation"] == "source"
    assert row["file"] == "corpus/micro.swf" and row["language"] == "AVM1"
    assert row["median_ms"] == 250 and row["phase"] == "cold"
    assert row["as_error_marker_count"] == 2 and row["exit_zero_count"] == 1
    assert any("error markers" in warning for warning in row["warnings"])
    assert row["source_available"] is row["coverage_percent"] is None
    assert row["compilation"] == row["behavior"] == "not_measured"
    with sqlite3.connect(database) as db:
        metadata = json.loads(db.execute("SELECT metadata_json FROM validations").fetchone()[0])
        assert metadata["raw_tool_id"] == "ffdec" and metadata["raw_operation"] == "decompile_as"
        assert json.loads(db.execute("SELECT metadata_json FROM runs").fetchone()[0])["as_error_marker_count"] == 2


def test_render_is_read_only_and_html_has_cohort_defaults_and_metric_guards(tmp_path):
    database = tmp_path / "catalog.db"
    import_local_report(database, save_report(tmp_path))
    before = database.read_bytes()
    page = render_catalog_html(database)
    assert database.read_bytes() == before
    assert "id=\"environment\"" in page and "id=\"metric\"" in page
    assert "c.source_kind==='local_report'" in page
    assert "['hot','summary','cold','process','observation']" in page
    assert "rows.slice(0,80)" in page
    assert "['campaign_id','environment_id','operation','phase']" in page
    assert "La selección mezcla" in page and "No medido" in page


@pytest.mark.parametrize("sample,status,success", [
    ({"timeout": "False", "timed_out": "0", "exit_code": "0"}, "success", 1),
    ({"timeout": "true", "exit_code": "-9"}, "timeout", 0),
    ({"ok": False, "exit_code": 0}, "indeterminate_conflicting_execution_flags", None),
    ({"ok": True, "exit_code": 1}, "indeterminate_conflicting_execution_flags", None),
    ({"ok": "false", "exit_code": "1"}, "error", 0),
    ({"ok": "true", "success": "false"}, "indeterminate_conflicting_execution_flags", None),
    ({"ok": "unknown", "timeout": "False"}, "indeterminate", None),
])
def test_execution_boolean_strings_and_conflicting_flags(sample, status, success):
    from py_swf.benchmark_catalog import _sample_status
    assert _sample_status(sample) == (status, success)


def test_real_cloud_memory_units_sampling_censoring_and_integrity(tmp_path):
    rows = [{"tool_id": "ffdec", "artifact_id": "micro01", "artifact_sha256": "a" * 64,
             "operation": "decompile_as", "wall_s": 2.896398033, "execution_status": "completed", "exit_code": 0,
             "sampled_peak_tree_rss_mib": 99.03125, "rss_samples": 273, "rss_sample_interval_s": 0.01,
             "rusage_maxrss_kib": 101820, "wall_is_censored": False, "semantic_status": "not_measured",
             "recompilation_status": "not_measured", "adapter_status": {"stage": "load_swf", "status": "completed"}},
            {"tool_id": "ffdec", "artifact_id": "micro01", "artifact_sha256": "a" * 64,
             "operation": "decompile_as", "wall_s": 30, "execution_status": "completed", "exit_code": 0,
             "sampled_peak_tree_rss_mib": 999, "rss_samples": 0, "rusage_maxrss_kib": 500000,
             "wall_is_censored": True}]
    journal = "\n".join(json.dumps(row) for row in rows).encode()
    members = {"results/runs.jsonl": journal,
               "corpus/manifest.json": {"artifacts": [{"id": "micro01", "path": "corpus/micro.swf", "sha256": "a" * 64}]},
               "transfer/integrity.json": {"original_files": [{"path": "results/runs.jsonl", "sha256": hashlib.sha256(journal).hexdigest()}]}}
    database = tmp_path / "catalog.db"
    import_cloud_bundle(database, bundle(tmp_path, members))
    row = catalog_summary(database)["rows"][0]
    assert row["peak_tree_rss_bytes"] == 99.03125 * 1048576
    assert row["rusage_maxrss_kib"] == 500000
    assert row["rss_sample_count"] == 273
    assert row["rss_sample_interval_s"] == [0.01]
    assert row["median_ms"] == 2896.398033
    assert row["censored_count"] == 1 and row["median_censored_ms"] == 30000
    assert row["compilation"] == row["behavior"] == "not_measured"
    assert row["adapter_statuses"] == [{"stage": "load_swf", "status": "completed"}]
    members["transfer/integrity.json"]["original_files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Manifest SHA-256"):
        import_cloud_bundle(tmp_path / "invalid.db", bundle(tmp_path, members, "invalid.zip"))


def test_batch_input_group_is_preserved_without_counting_as_a_swf(tmp_path):
    members = cloud_members()
    members["results/runs.jsonl"] += (json.dumps({"tool_id": "ffdec", "artifact_id": "batch", "artifact_sha256": None,
                                                  "input_artifact_ids": ["corpus/same.swf"], "operation": "decompile_batch",
                                                  "exit_code": 0, "wall_s": 10}) + "\n").encode()
    database = tmp_path / "catalog.db"
    result = import_cloud_bundle(database, bundle(tmp_path, members))
    assert result["run_count"] == 3
    assert result["artifact_count"] == 1 and result["unidentified_artifact_groups"] == 1
    summary = catalog_summary(database)
    assert summary["totals"]["artifacts"] == 1
    batch = next(row for row in summary["rows"] if row["operation"] == "decompile_batch")
    assert batch["artifact_sha256"] is None and batch["run_count"] == 1
