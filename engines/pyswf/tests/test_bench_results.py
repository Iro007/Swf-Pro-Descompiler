import copy
import json

from py_swf import bench_results
from py_swf.bench_results import comparable_engines, flash_index, median_seconds, read_bench, upgrade


def test_reads_latest_run_and_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASH_BENCH_DIR", str(tmp_path))
    assert read_bench()["results"] is None
    for run in ("a", "b"):
        (tmp_path / run).mkdir()
        (tmp_path / run / "results.json").write_text(json.dumps({"run_id": run}), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps([{"run_id": "a"}, {"run_id": "b"}]), encoding="utf-8")
    assert read_bench()["results"]["run_id"] == "b" and read_bench("a")["results"]["run_id"] == "a"
    try:
        read_bench("../x")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must be rejected")


def row(engine, case, measured=True, seconds=None):
    """A decompiler row: `measured` says whether the engine ran on the case."""
    return {"engine": engine, "family": "f", "case": case, "measured": measured, "seconds": seconds}


def simple(engine, case, ok):
    """A row of any other category: ok is None when the task does not apply to the engine."""
    return {"engine": engine, "family": "f", "case": case, "ok": ok, "seconds": None}


def test_only_engines_measured_on_exactly_the_same_cases_are_comparable():
    rows = [row("a", "1"), row("a", "2"), row("b", "1"), row("b", "2"), row("c", "1"), row("c", "2", measured=False)]
    assert comparable_engines(rows) == {"a", "b"}
    assert comparable_engines([row("a", "1"), row("b", "2")]) == set(), "one engine per set is not a comparison"
    assert comparable_engines([row("a", "1", measured=False)]) == set()
    assert comparable_engines([simple("x", "1", True), simple("y", "1", False), simple("z", "1", None)]) == {"x", "y"}


def test_the_largest_group_is_the_one_that_is_ranked():
    rows = [row("a", "1"), row("a", "2"), row("a", "3"), row("b", "1"), row("b", "2"), row("c", "1"), row("c", "2"),
            row("d", "1"), row("d", "2")]
    assert comparable_engines(rows) == {"b", "c", "d"}, "three engines on two cases beat one engine on three"


def test_median_seconds_ignores_the_rows_without_a_time():
    assert median_seconds([row("a", "1", seconds=1.0), row("a", "2", seconds=0.2), row("a", "3"), row("a", "4", seconds=0.6)]) == 0.6
    assert median_seconds([row("a", "1")]) is None and median_seconds([]) is None


def test_flash_index_is_the_mean_of_source_recompile_trace_and_units():
    line = {"cases": 6, "source": 6, "recompiles": 3, "same_trace": 3, "unit_coverage": 0.5}
    assert flash_index(line) == 62.5, "(6/6 + 3/6 + 3/6 + 1/2) / 4"
    assert flash_index({**line, "source": 0, "recompiles": 0, "same_trace": 0, "unit_coverage": 0.0}) == 0.0
    assert flash_index({**line, "source": 6, "recompiles": 6, "same_trace": 6, "unit_coverage": 1.0}) == 100.0
    assert flash_index({**line, "reference": True}) == 62.5, "JPEXS is judged like every other engine"
    assert flash_index({**line, "jpexs_closeness": 0.1, "jpexs_coverage": 0.1}) == 62.5, "the closeness to JPEXS does not enter"
    assert flash_index({**line, "unit_coverage": None}) is None, "the run never counted the units of the SWF"
    assert flash_index({**line, "cases": 0}) is None


OLD = {
    "schema_version": 1, "run_id": "old", "cases": 2,
    "tables": {
        "decompilers": {"unit": "u", "winner": None, "note": "n", "engines": [
            {"engine": "native", "name": "PySWF", "cases": 2, "source": 2, "recompiles": 2, "same_trace": 1,
             "jpexs_closeness": 0.5, "jpexs_coverage": 1.0, "reasons": []},
            {"engine": "rust", "name": "RustSWF", "cases": 2, "source": 1, "recompiles": 1, "same_trace": 0,
             "jpexs_closeness": None, "jpexs_coverage": None, "reasons": []}]},
        "converters": {"unit": "u", "winner": None, "note": "n", "engines": [
            {"engine": "swfmill", "name": "swfmill", "cases": 2, "ok": 2, "details": [], "reasons": []}]},
    },
    "rows": {
        "decompilers": [{"engine": e, "family": "f", "case": c, "source": True, "recompiles": True, "same_trace": True, "reason": ""}
                        for e in ("native", "rust") for c in ("1", "2")],
        "converters": [{"engine": "swfmill", "family": "f", "case": c, "ok": True, "detail": "", "reason": ""} for c in ("1", "2")],
    },
}


def test_a_run_from_before_schema_2_is_filled_in_without_inventing_measurements():
    results = upgrade(copy.deepcopy(OLD))
    native, rust = results["tables"]["decompilers"]["engines"]
    assert results["schema_version"] == 1, "the file keeps saying which schema wrote it"
    assert (native["median_seconds"], native["timed_cases"], rust["median_seconds"]) == (None, 0, None), "old runs never timed anything"
    assert (native["comparable"], rust["comparable"]) == (True, True), "every engine saw the same cases"
    assert native["flash_index"] is None and rust["flash_index"] is None, "that run never counted the units of the SWF"
    assert (native["units_total"], native["units_with_source"], native["unit_coverage"]) == (None, None, None)
    converter = results["tables"]["converters"]["engines"][0]
    assert converter["median_seconds"] is None and "flash_index" not in converter, "only decompilers have the index"
    assert results["tables"]["decompilers"]["corpus_cases"] == 2


def test_a_run_that_counted_the_units_gets_its_index_when_the_file_lacks_it():
    run = copy.deepcopy(OLD)
    run["tables"]["decompilers"]["engines"][0]["unit_coverage"] = 0.5
    native = upgrade(run)["tables"]["decompilers"]["engines"][0]
    assert native["flash_index"] == 75.0, "(2/2 + 2/2 + 1/2 + 1/2) / 4"


def test_a_run_written_with_schema_2_is_left_as_it_is():
    new = copy.deepcopy(OLD)
    new["schema_version"] = 2
    line = new["tables"]["decompilers"]["engines"][0]
    line.update(median_seconds=0.25, timed_cases=2, comparable=False, flash_index=12.3, units_total=10, units_with_source=5, unit_coverage=0.5)
    new["tables"]["decompilers"]["corpus_cases"] = 9
    kept = upgrade(copy.deepcopy(new))
    assert kept["tables"]["decompilers"]["engines"][0] == line
    assert kept["tables"]["decompilers"]["corpus_cases"] == 9


def test_read_bench_upgrades_old_runs_and_a_run_without_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASH_BENCH_DIR", str(tmp_path))
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "results.json").write_text(json.dumps(OLD), encoding="utf-8")
    bare = {"run_id": "bare", "cases": 1, "tables": {"extractors": {"unit": "u", "winner": None, "note": "n", "engines": [
        {"engine": "swfextract", "name": "swfextract", "cases": 1, "ok": 1, "details": [], "reasons": []}]}}}
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "results.json").write_text(json.dumps(bare), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps([{"run_id": "old"}, {"run_id": "bare"}]), encoding="utf-8")
    assert read_bench("old")["results"]["tables"]["decompilers"]["engines"][0]["flash_index"] is None
    result = read_bench("bare")["results"]
    line = result["tables"]["extractors"]["engines"][0]
    assert result["schema_version"] == 1 and line["comparable"] is True and line["median_seconds"] is None
    assert result["tables"]["extractors"]["corpus_cases"] == 1, "no rows: the run's own case count"
    assert bench_results.SCHEMA_VERSION == 2
