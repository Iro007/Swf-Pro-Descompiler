import copy
import json

from py_swf import bench_results
from py_swf.bench_results import (BASE_COMPONENTS, INDEX_COMPONENTS, comparable_engines, component_shares, flash_index, index_components,
                                  median_seconds, read_bench, robustness_score, unit_fidelity, upgrade)


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
    assert kept["tables"]["decompilers"]["engines"][0] == {**line, "unit_fidelity": None, "robustness": None}, "only the two terms it never measured are added"
    assert kept["tables"]["decompilers"]["corpus_cases"] == 9 and kept["schema_version"] == 2
    assert kept["tables"]["decompilers"]["index_components"] == list(BASE_COMPONENTS), "a schema 2 index averaged the four base terms"


def test_a_run_from_before_the_unit_count_says_it_has_no_index_terms():
    assert upgrade(copy.deepcopy(OLD))["tables"]["decompilers"]["index_components"] == []


def test_a_run_written_with_schema_3_keeps_its_own_terms_and_tables():
    new = copy.deepcopy(OLD)
    new["schema_version"] = 3
    table = new["tables"]["decompilers"]
    table["index_components"] = ["source", "recompiles", "same_trace", "units", "robustness"]
    table["engines"][0].update(flash_index=70.0, unit_coverage=0.5, unit_fidelity=None, unit_fidelity_cases=0, robustness=55.0, robustness_cases=51)
    new["tables"]["robustness"] = {"unit": "u", "winner": None, "note": "n", "engines": [
        {"engine": "native", "name": "PySWF", "cases": 51, "robustness": 55.0, "outcomes": {"recovered": 1}, "reasons": []},
        {"engine": "jpexs", "name": "JPEXS", "cases": 51, "robustness": 40.0, "outcomes": {"clean_error": 1}, "reasons": []}]}
    new["rows"]["robustness"] = [{"engine": e, "family": "f", "case": "c%d" % i, "measured": True, "outcome": "recovered"} for e in ("native", "jpexs") for i in range(51)]
    kept = upgrade(copy.deepcopy(new))
    assert kept["tables"]["decompilers"]["index_components"] == table["index_components"]
    assert kept["tables"]["decompilers"]["engines"][0]["flash_index"] == 70.0
    robust = kept["tables"]["robustness"]
    assert robust["corpus_cases"] == 51 and robust["engines"][0]["comparable"] is True and robust["engines"][0]["median_seconds"] is None
    assert "flash_index" not in robust["engines"][0], "only decompilers have the index"


def decompiler(engine, cases=6, same=3, units=0.5, fidelity=None, robustness=None, **more):
    return {"engine": engine, "cases": cases, "source": same, "recompiles": same, "same_trace": same, "unit_coverage": units, "comparable": True,
            **({} if fidelity is None else {"unit_fidelity": fidelity, "unit_fidelity_cases": cases}),
            **({} if robustness is None else {"robustness": robustness, "robustness_cases": 51}), **more}


def test_the_unit_fidelity_of_a_set_of_units_leaves_out_the_ones_the_trace_never_runs():
    assert unit_fidelity({"same": 3, "different": 1, "no_compile": 0, "not_run": 6}) == 0.75, "same / (evaluated - not_run)"
    assert unit_fidelity({"same": 2, "different": 0, "no_compile": 2, "not_run": 0}) == 0.5, "a unit that does not compile stays in the denominator"
    assert unit_fidelity({"same": 0, "different": 0, "no_compile": 0, "not_run": 5}) is None, "nothing judged, nothing to be faithful about"
    assert unit_fidelity({}) is None and unit_fidelity({"same": 4}) == 1.0


def test_the_six_terms_of_the_index_and_who_has_which():
    assert INDEX_COMPONENTS == ("source", "recompiles", "same_trace", "units", "unit_fidelity", "robustness")
    shares = component_shares(decompiler("native", fidelity=0.8, robustness=60.0))
    assert shares == {"source": 0.5, "recompiles": 0.5, "same_trace": 0.5, "units": 0.5, "unit_fidelity": 0.8, "robustness": 0.6}
    nothing_judged = component_shares({**decompiler("rust"), "unit_fidelity": None, "unit_fidelity_cases": 6})
    assert nothing_judged["unit_fidelity"] == 0.0, "measured but no unit judged: no source whose behaviour is known"
    assert component_shares(decompiler("rust"))["unit_fidelity"] is None and component_shares(decompiler("rust"))["robustness"] is None


def test_the_index_takes_the_extra_terms_only_when_every_ranked_engine_has_them():
    both = [decompiler("native", fidelity=0.8, robustness=60.0), decompiler("jpexs", fidelity=0.4, robustness=50.0)]
    assert index_components(both) == list(INDEX_COMPONENTS)
    one_missing = [decompiler("native", fidelity=0.8, robustness=60.0), decompiler("jpexs", fidelity=0.4)]
    assert index_components(one_missing) == ["source", "recompiles", "same_trace", "units", "unit_fidelity"], "robustness was not measured for jpexs"
    assert index_components([decompiler("native"), decompiler("jpexs")]) == list(BASE_COMPONENTS)
    # an engine that is not ranked (measured on other cases) does not decide what the index contains
    apart = decompiler("flare", fidelity=None, comparable=False)
    assert index_components([*both, apart]) == list(INDEX_COMPONENTS)
    # with nobody ranked, every measured engine counts
    alone = decompiler("native", fidelity=0.8, comparable=False)
    assert index_components([alone]) == ["source", "recompiles", "same_trace", "units", "unit_fidelity"]
    assert index_components([]) == list(BASE_COMPONENTS)


def test_the_index_is_the_mean_of_the_terms_the_run_includes_and_none_when_one_is_missing():
    line = decompiler("native", same=6, units=1.0, fidelity=0.5, robustness=50.0)
    assert flash_index(line) == 100.0, "the base terms alone, as before schema 3"
    assert flash_index(line, INDEX_COMPONENTS) == round(100 * (1 + 1 + 1 + 1 + 0.5 + 0.5) / 6, 1)
    assert flash_index(decompiler("rust", same=0, units=0.0), INDEX_COMPONENTS) is None, "a term the run did not measure"
    assert flash_index({**line, "robustness": None}, INDEX_COMPONENTS) is None
    assert flash_index({**line, "jpexs_closeness": 0.1, "jpexs_coverage": 0.1}, INDEX_COMPONENTS) == flash_index(line, INDEX_COMPONENTS), "JPEXS closeness never enters"


def test_the_robustness_score_lives_beside_the_reader():
    assert robustness_score({"recovered": 1, "crash": 1}) == 50.0 and robustness_score({}) is None


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
    assert bench_results.SCHEMA_VERSION == 3
