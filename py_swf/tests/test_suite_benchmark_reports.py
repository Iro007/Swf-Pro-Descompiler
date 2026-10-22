"""Measured-report reads through the application API never alter a live project."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from py_swf import universal
from py_swf.avm1 import assemble_avm1
from py_swf.benchmark_report import public_report
from py_swf.benchmarks import run_benchmark
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf


@pytest.fixture
def suite(tmp_path):
    source = tmp_path / "original.swf"
    source.write_bytes(make_synthetic_swf([SWFTag(12, assemble_avm1("push 1\ntrace\nend"))]))
    instance = universal.UniversalSuite(tmp_path / "state", tmp_path / "tools.json")
    instance.open(source)
    instance.commit(make_synthetic_swf([SWFTag(12, assemble_avm1("push 2\ntrace\nend"))]), "test-edit")
    yield instance
    instance.close()


def test_catalog_empty_read_does_not_create_database_or_edit_project(suite, tmp_path, monkeypatch):
    monkeypatch.setattr(universal, "ROOT", tmp_path / "workspace")
    monkeypatch.setattr(universal, "__file__", str(tmp_path / "resources/py_swf/universal.py"))
    monkeypatch.delenv("FLASH_BENCHMARK_CATALOG", raising=False)
    before = unchanged_state(suite)
    result = suite.dispatch("benchmark_catalog", {})
    assert result["available"] is False and result["rows"] == []
    assert result["totals"] == {"campaigns": 0, "artifacts": 0, "runs": 0, "tools": 0}
    assert not Path(result["database"]).exists()
    assert unchanged_state(suite) == before


def test_catalog_rpc_filters_and_http_read_leave_database_unchanged(suite, tmp_path, monkeypatch):
    from py_swf.benchmark_catalog import import_local_report
    from server.routers.benchmarks import catalog_summary
    database = tmp_path / "catalog.sqlite"
    import_local_report(database, save(tmp_path / "benchmark.json"))
    monkeypatch.setenv("FLASH_BENCHMARK_CATALOG", str(database))
    before = unchanged_state(suite)
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    result = suite.dispatch("benchmark_catalog", {"engine": "external", "limit": 1})
    assert result["available"] is True and len(result["rows"]) == 1
    assert result["rows"][0]["coverage_percent"] is None
    assert suite.dispatch("benchmark_catalog", {"engine": "absent"})["rows"] == []
    monkeypatch.setattr(universal, "ToolRegistry", lambda *_: pytest.fail("Catalog HTTP must not discover or start tools"))
    response = catalog_summary()
    assert response.status_code == 200 and json.loads(response.body)["totals"]["campaigns"] == 1
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest
    assert unchanged_state(suite) == before


def test_catalog_rpc_rejects_missing_explicit_database(suite, tmp_path):
    before = unchanged_state(suite)
    with pytest.raises(ValueError, match="no existe"):
        suite.dispatch("benchmark_catalog", {"database": str(tmp_path / "missing.sqlite")})
    assert unchanged_state(suite) == before


def test_new_application_campaign_is_registered_without_editing_project(suite, tmp_path, monkeypatch):
    from py_swf import benchmarks
    monkeypatch.setattr(universal, "ROOT", tmp_path / "workspace")
    monkeypatch.setattr(universal, "__file__", str(tmp_path / "resources/py_swf/universal.py"))
    monkeypatch.delenv("FLASH_BENCHMARK_CATALOG", raising=False)
    def measured(corpus, **options):
        value = report()
        output = Path(options["output"])
        output.mkdir(parents=True)
        save(output / "benchmark.json", value)
        return value
    monkeypatch.setattr(benchmarks, "run_benchmark", measured)
    before = unchanged_state(suite)
    result = suite.benchmark({"corpus": str(tmp_path), "output": str(tmp_path / "campaign"), "engines": ["native"]})
    assert result["catalog_import"]["imported"] is True and result["catalog_warning"] is None
    assert suite.dispatch("benchmark_catalog", {})["totals"]["campaigns"] == 1
    assert unchanged_state(suite) == before


def test_catalog_import_failure_keeps_completed_report(suite, tmp_path, monkeypatch):
    from py_swf import benchmarks, benchmark_catalog
    def measured(corpus, **options):
        value = report()
        output = Path(options["output"])
        output.mkdir(parents=True)
        save(output / "benchmark.json", value)
        return value
    def unavailable(*args, **kwargs):
        raise OSError("database is read-only")
    monkeypatch.setattr(benchmarks, "run_benchmark", measured)
    monkeypatch.setattr(benchmark_catalog, "import_local_report", unavailable)
    result = suite.benchmark({"corpus": str(tmp_path), "output": str(tmp_path / "campaign"), "engines": ["native"]})
    assert Path(result["path"]).is_file() and result["catalog_import"] is None
    assert "read-only" in result["catalog_warning"]


def test_suite_accepts_local_external_adapter_without_claiming_unit_coverage(suite, tmp_path, monkeypatch):
    from py_swf import benchmarks, benchmark_engines
    config = tmp_path / "external-tools.json"
    seen = {}
    def external(engine, path):
        seen.update(engine=engine, config=path)
        return ["isolated-python", "adapter"]
    def measured(corpus, **options):
        assert options["engines"] == ("pyflashkit", "rabcdasm")
        assert options["operation"] == "disassemble"
        assert options["command_factory"]("pyflashkit") == ["isolated-python", "adapter"]
        value = report()
        output = Path(options["output"])
        output.mkdir(parents=True)
        save(output / "benchmark.json", value)
        return value
    monkeypatch.setattr(benchmark_engines, "external_command", external)
    monkeypatch.setattr(benchmarks, "run_benchmark", measured)
    monkeypatch.setenv("FLASH_BENCHMARK_CATALOG", str(tmp_path / "catalog.sqlite"))
    before = unchanged_state(suite)
    result = suite.benchmark({"corpus": str(tmp_path), "output": str(tmp_path / "external-campaign"),
                              "engines": ["pyflashkit", "rabcdasm"], "operation": "disassemble",
                              "tool_config": str(config)})
    assert seen == {"engine": "pyflashkit", "config": str(config)}
    assert result["claims"]["parity"] is False
    assert unchanged_state(suite) == before


def test_suite_requires_implemented_local_benchmark_adapter(suite, tmp_path):
    before = unchanged_state(suite)
    with pytest.raises(ValueError, match="adaptador"):
        suite.benchmark({"corpus": str(tmp_path), "output": str(tmp_path / "campaign"),
                         "engines": ["sothink"]})
    assert unchanged_state(suite) == before


def report():
    return {"schema_version": 1, "created_at": "2026-10-13T00:00:00+00:00", "profile": "manual",
            "operation": "disassemble", "completed_files": 1, "scheduled_files": 1,
            "claims": {"parity": False, "superiority": False, "performance_winner": None},
            "cases": [{"file": "private/input.swf", "relative": "sample.swf", "engines": [
                {"engine": "external", "status": "output_unmapped",
                 "cold": {"median_ms": None, "attempted": 0},
                 "hot": {"median_ms": None, "p95_ms": None, "peak_tree_rss_bytes": None},
                 "quality": {"expected_units_reported": None, "source_available": None,
                             "disassembly_available": None, "pcode": None, "unit_mapping": "unmapped"},
                 "units": [{"source": "hidden source", "bytecode": "hidden bytes"}],
                 "documents": [{"source": "hidden document"}],
                 "compilation_audit": {"summary": {"validated_reopened": 0}, "records": [{"source": "hidden audit"}]},
                 "compilation_sample": [{"source": "hidden sample", "compilation": "not_run"}]}],
                       "comparison": {"matched": None, "identity_mapping": None, "quality_equivalent": False}}]}


def save(path, data=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report() if data is None else data, ensure_ascii=False), encoding="utf-8")
    return path


def unchanged_state(suite):
    return (deepcopy(suite.project.__dict__), deepcopy(suite.undo_stack), deepcopy(suite.redo_stack),
            deepcopy(suite.cache), suite.origin, (suite.directory / "session.pyswfproj").read_bytes(),
            (suite.directory / "working.swf").read_bytes(), Path(suite.origin).read_bytes())


def test_compact_report_preserves_null_mapping_and_live_history(suite, tmp_path):
    data = report()
    data["extra"] = {"SOURCE": "hidden extension", "bytecode": [1, 2], "pcode": "push 1", "metric": None}
    path = save(tmp_path / "new-report.json", data)
    before = unchanged_state(suite)
    original = path.read_bytes()
    result = suite.dispatch("benchmark_report", {"path": str(path)})
    assert result["path"] == str(path.resolve())
    compact = result["report"]
    engine = compact["cases"][0]["engines"][0]
    assert engine["quality"] == data["cases"][0]["engines"][0]["quality"]
    assert engine["hot"]["median_ms"] is None
    assert compact["cases"][0]["comparison"] == data["cases"][0]["comparison"]
    assert compact["claims"] == data["claims"]
    assert compact["extra"] == {"metric": None}
    assert engine["compilation_sample"] == [{"compilation": "not_run"}]
    assert engine["compilation_audit"]["summary"] == {"validated_reopened": 0}
    serialized = json.dumps(result, allow_nan=False)
    assert "hidden" not in serialized and "private/input.swf" not in serialized
    assert "units" not in engine and "documents" not in engine
    assert "records" not in engine["compilation_audit"]
    assert unchanged_state(suite) == before
    assert path.read_bytes() == original


def test_reads_new_real_native_benchmark_without_recalculating_metrics(suite, tmp_path):
    corpus = Path(suite.origin)
    original = corpus.read_bytes()
    output = tmp_path / "measured"
    measured = run_benchmark(corpus, engines=("native",), output=output, timeout=20, memory_bytes=256 * 1024**2)
    before = unchanged_state(suite)
    result = suite.dispatch("benchmark_report", {"path": str(output / "benchmark.json")})
    assert result["report"]["schema_version"] == 1
    assert result["report"]["completed_files"] == 1
    expected = public_report(measured)["cases"][0]["engines"][0]
    actual = result["report"]["cases"][0]["engines"][0]
    assert actual["cold"] == expected["cold"]
    assert actual["hot"] == expected["hot"]
    assert actual["quality"] == expected["quality"]
    assert corpus.read_bytes() == original
    assert unchanged_state(suite) == before


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(schema_version=2), lambda r: r.update(schema_version=True),
    lambda r: r.update(cases={}), lambda r: r.update(cases=[None]),
    lambda r: r["cases"][0].update(engines=[{}]),
    lambda r: r["cases"][0]["engines"][0].update(hot=[]),
    lambda r: r["cases"][0]["engines"][0]["hot"].update(median_ms="1"),
    lambda r: r["cases"][0]["engines"][0]["quality"].update(source_available=True),
    lambda r: r.update(completed_files=-1), lambda r: r.update(profile=[]),
    lambda r: r.update(extra=float("nan")), lambda r: r.update(extra=float("inf")),
])
def test_rejects_malformed_report_shapes_and_nonfinite_numbers(suite, tmp_path, mutation):
    data = report()
    mutation(data)
    path = save(tmp_path / "invalid.json", data)
    before = unchanged_state(suite)
    with pytest.raises(ValueError):
        suite.dispatch("benchmark_report", {"path": str(path)})
    assert unchanged_state(suite) == before


def test_rejects_invalid_paths_json_and_size_limits(suite, tmp_path, monkeypatch):
    malformed = tmp_path / "bad.json"
    malformed.write_text("{broken", encoding="utf-8")
    wrong_extension = save(tmp_path / "report.txt")
    too_big = tmp_path / "too-big.json"
    assert universal.MAX_BENCHMARK_REPORT == 32 * 1024**2
    with too_big.open("wb") as stream:
        stream.seek(universal.MAX_BENCHMARK_REPORT)
        stream.write(b"0")
    before = unchanged_state(suite)
    for path in (None, "", str(tmp_path), str(tmp_path / "missing.json"), str(malformed), str(wrong_extension), str(too_big)):
        with pytest.raises(ValueError):
            suite.dispatch("benchmark_report", {"path": path})
    assert universal.MAX_BENCHMARK_SNAPSHOT == 4 * 1024**2
    monkeypatch.setattr(universal, "MAX_BENCHMARK_SNAPSHOT", 1024)
    data = report()
    data["cases"][0]["engines"][0]["units"][0]["source"] = "x" * 4096
    path = save(tmp_path / "raw-large.json", data)
    suite.dispatch("benchmark_report", {"path": str(path)})
    data["scope_note"] = "x" * 2048
    save(path, data)
    with pytest.raises(ValueError, match="4 MiB"):
        suite.dispatch("benchmark_report", {"path": str(path)})
    assert unchanged_state(suite) == before


def test_recent_discovery_is_bounded_stable_and_skips_invalid_files(suite, tmp_path, monkeypatch):
    root, project = tmp_path / "workspace", tmp_path / "python"
    monkeypatch.setattr(universal, "ROOT", root)
    monkeypatch.setattr(universal, "PROJECT", project)
    paths = []
    for index in range(25):
        parent = root if index % 2 else project
        path = save(parent / "reports" / f"run-{index:02}" / "benchmark.json")
        stamp = (index + 1) * 1_000_000_000
        os.utime(path, ns=(stamp, stamp))
        paths.append(path)
    for name in ("tie-a", "tie-b"):
        path = save(root / "reports" / f"{name}.json")
        os.utime(path, ns=(100_000_000_000, 100_000_000_000))
        paths.append(path)
    bad = save(root / "reports" / "invalid.json", {"schema_version": 2, "cases": []})
    save(root / "outside.json")
    save(root / "reports" / "one" / "two" / "three" / "four" / "too-deep.json")
    before = unchanged_state(suite)
    result = suite.dispatch("benchmark_reports", {})
    expected = sorted(paths, key=lambda path: (-path.stat().st_mtime_ns, str(path.resolve()).casefold(), str(path.resolve())))
    assert result["total"] == 27
    assert [row["path"] for row in result["reports"]] == [str(path.resolve()) for path in expected[:20]]
    assert result["reports"][0]["label"] == "tie-a"
    assert result["reports"][2]["label"] == "run-24"
    assert all(row["completed_files"] == 1 for row in result["reports"])
    assert str(bad) not in {row["path"] for row in result["reports"]}
    assert suite.dispatch("benchmark_reports", {}) == result
    assert unchanged_state(suite) == before
    assert universal.MAX_BENCHMARK_FILES == 500 and universal.MAX_BENCHMARK_DEPTH == 3
    monkeypatch.setattr(universal, "MAX_BENCHMARK_FILES", 4)
    assert suite.dispatch("benchmark_reports", {})["total"] <= 4


def test_jsonl_worker_returns_report_and_recovers_after_invalid_report(tmp_path):
    path = save(tmp_path / "local.json")
    requests = [{"id": 1, "op": "benchmark_report", "args": {"path": str(tmp_path / "missing.json")}},
                {"id": 2, "op": "benchmark_report", "args": {"path": str(path)}},
                {"id": 3, "op": "ping", "args": {}}]
    result = subprocess.run([sys.executable, "-m", "py_swf.universal_worker", "worker"],
                            input="".join(json.dumps(item) + "\n" for item in requests), capture_output=True,
                            text=True, encoding="utf-8", timeout=20,
                            env={**os.environ, "FLASH_SUITE_STATE_DIR": str(tmp_path / "worker"), "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, 3]
    assert not replies[0]["ok"] and replies[1]["ok"] and replies[2]["ok"]
    assert replies[1]["result"]["report"]["cases"][0]["comparison"]["matched"] is None
    assert "hidden source" not in result.stdout


def test_discovery_does_not_follow_a_report_path_that_changes_outside_reports(suite, tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setattr(universal, "ROOT", root)
    monkeypatch.setattr(universal, "PROJECT", root)
    save(root / "reports" / "candidate.json")
    outside = save(root / "outside.json").resolve()
    # Simulate a path resolving elsewhere between discovery and the bounded read.
    monkeypatch.setattr(universal, "_benchmark_read", lambda path: (outside, report()))
    assert suite.dispatch("benchmark_reports", {}) == {"reports": [], "total": 0}
