"""The desktop benchmark RPC preserves all selected local comparator engines."""
import hashlib
import json
from pathlib import Path

import pytest

from py_swf import benchmark_engines, benchmarks, universal, workspace_tools
from py_swf.avm1 import assemble_avm1
from py_swf.benchmark_process import WorkerError
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf
from tests.native_fixtures import as3_tag
from tests.test_suite_benchmark_reports import unchanged_state


SEVEN_ENGINES = ("native", "rust", "jpexs", "rabcdasm", "flasm", "flare", "flex_swfdump")


@pytest.fixture
def isolated_suite(tmp_path, monkeypatch):
    source = tmp_path / "input.swf"
    source.write_bytes(make_synthetic_swf([as3_tag(), SWFTag(12, assemble_avm1("push 1\ntrace\nend"))]))
    suite = universal.UniversalSuite(tmp_path / "state", tmp_path / "tools.json")
    suite.open(source)
    monkeypatch.setenv("FLASH_BENCHMARK_CATALOG", str(tmp_path / "catalog.sqlite"))
    monkeypatch.setattr(benchmark_engines, "ROOT", tmp_path / "isolated-project")
    monkeypatch.setattr(workspace_tools, "discover_workspace_tools", lambda *args, **kwargs: {"tools": []})
    yield suite, source
    suite.close()


def pinned_flex_config(tmp_path):
    executable = tmp_path / "fixture-java.exe"
    library = tmp_path / "fixture-swfdump.jar"
    executable.write_bytes(b"isolated runtime fingerprint; never executed")
    library.write_bytes(b"isolated swfdump fingerprint; never executed")
    config = tmp_path / "benchmark-tools.json"
    config.write_text(json.dumps({"development_only": True, "tools": {"flex_swfdump": {
        "executable": str(executable), "library_jar": str(library), "version": "fixture-4.16.1",
        "artifact_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (executable, library)}
    }}}), encoding="utf-8")
    return config


def test_universal_capability_rpc_exposes_flex_as_disassembly_only(isolated_suite, tmp_path):
    suite, _ = isolated_suite
    before = unchanged_state(suite)
    config = pinned_flex_config(tmp_path)
    catalog = suite.dispatch("benchmark_tools", {"tool_config": str(config)})
    entries = {entry["id"]: entry for entry in catalog["engines"]}
    assert set(SEVEN_ENGINES) <= set(entries)
    flex = entries["flex_swfdump"]
    assert flex["configured"] and flex["category"] == "disassembler"
    assert tuple(flex["operations"]) == ("disassemble",)
    assert tuple(flex["languages"]) == ("as2", "as3")
    assert "coverage remains unknown" in flex["output_scope"]
    assert unchanged_state(suite) == before


def test_universal_dispatch_passes_all_seven_and_routes_flex_to_external_adapter(isolated_suite, tmp_path, monkeypatch):
    from py_swf import benchmark_catalog

    suite, source = isolated_suite
    before = unchanged_state(suite)
    config = pinned_flex_config(tmp_path)
    seen = {}

    def no_measurement(corpus, **options):
        seen.update(engines=options["engines"], operation=options["operation"], tool_config=options["tool_config"])
        command = options["command_factory"]("flex_swfdump")
        assert "py_swf.benchmark_external_worker" in command
        assert command[command.index("--engine") + 1] == "flex_swfdump"
        assert command[command.index("--config") + 1] == str(config.resolve())
        output = Path(options["output"]); output.mkdir()
        report = {"completed_files": 1, "scheduled_files": 1,
                  "claims": {"parity": False, "superiority": False, "performance_winner": None},
                  "cases": [{"engines": [{"engine": engine, "status": "not_measured"} for engine in options["engines"]]}]}
        (output / "benchmark.json").write_text(json.dumps(report), encoding="utf-8")
        return report

    monkeypatch.setattr(benchmarks, "run_benchmark", no_measurement)
    monkeypatch.setattr(benchmark_catalog, "import_local_report", lambda *args: {"imported": True})
    result = suite.dispatch("benchmark", {"corpus": str(source), "output": str(tmp_path / "dispatch-report"),
        "engines": ["python", *SEVEN_ENGINES[1:]], "operation": "disassemble", "tool_config": str(config)})
    assert seen == {"engines": SEVEN_ENGINES, "operation": "disassemble", "tool_config": str(config)}
    saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert tuple(row["engine"] for row in saved["cases"][0]["engines"]) == SEVEN_ENGINES
    assert result["completed_files"] == 1 and result["claims"]["parity"] is False
    assert unchanged_state(suite) == before


@pytest.mark.parametrize("operation,unsupported", [
    ("source", ("rabcdasm", "flasm", "flex_swfdump")),
    ("disassemble", ("jpexs", "flare")),
])
def test_real_campaign_keeps_seven_rows_and_inapplicability_explicit(isolated_suite, tmp_path, monkeypatch, operation, unsupported):
    """Exercise real campaign/report/catalog paths, replacing process startup only."""
    suite, source = isolated_suite
    before = unchanged_state(suite)
    worker_attempts = []

    def unavailable_worker(command, **kwargs):
        worker_attempts.append(command)
        raise WorkerError("Process startup disabled in dispatch regression; no measurement")

    def missing_tool(*args, **kwargs):
        raise ValueError("Isolated optional engine unavailable")

    def missing_reference(*args, **kwargs):
        raise WorkerError("Isolated optional reference unavailable")

    monkeypatch.setattr(benchmarks, "Worker", unavailable_worker)
    monkeypatch.setattr(benchmarks, "reference_command", missing_reference)
    monkeypatch.setattr(benchmark_engines, "default_rust_executable", missing_tool)
    monkeypatch.setattr(suite.registry, "executable", missing_tool)
    result = suite.dispatch("benchmark", {"corpus": str(source), "output": str(tmp_path / "source-report"),
        "engines": list(SEVEN_ENGINES), "operation": operation, "tool_config": str(tmp_path / "missing-tools.json")})
    saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    rows = {row["engine"]: row for row in saved["cases"][0]["engines"]}
    assert set(rows) == set(SEVEN_ENGINES) and len(rows) == 7
    for engine in unsupported:
        row = rows[engine]
        assert row["status"] == "not_applicable" and "does not support" in row["diagnostic"]
        assert row["hot"]["median_ms"] is None and row["cold"]["median_ms"] is None
        assert row["quality"]["source_available"] is None and row["failures"] == []
    assert worker_attempts and all("benchmark_external_worker" not in " ".join(command) for command in worker_attempts)
    assert result["catalog_import"]["imported"] is True and result["catalog_warning"] is None
    assert result["completed_files"] == result["scheduled_files"] == 1
    assert saved["claims"]["parity"] is False and saved["claims"]["performance_winner"] is None
    assert source.read_bytes() == before[-1] and unchanged_state(suite) == before
