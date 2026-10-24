"""The benchmark counts what happened and says why when nothing did."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bench  # noqa: E402
import bench_tasks  # noqa: E402


def job(status="evaluated", source="complete", compile_status="compiled", runtime="passed_bounded_trace", **extra):
    return {"engine": "native", "family_id": "f", "case_id": "c.swf", "language": "as3", "status": status,
            "source": {"status": source, **extra.get("source", {})},
            "compilations": [{"compiler": "shared_native", "status": "compiled"},
                             {"compiler": "independent_flex", "status": compile_status, "runtime": {"status": runtime}}],
            **{k: v for k, v in extra.items() if k != "source"}}


def test_independent_compiler_and_same_trace_decide_the_verdict():
    row = bench.verdict(job())
    assert (row["source"], row["recompiles"], row["same_trace"], row["reason"]) == (True, True, True, "")
    assert bench.verdict(job(runtime="trace_mismatch"))["same_trace"] is False
    failed = bench.verdict(job(compile_status="compilation_failed"))
    assert failed["recompiles"] is False and failed["same_trace"] is None and failed["reason"]


def test_every_gap_carries_its_reason():
    partial = bench.verdict(job(status="not_supported", source="not_supported", source_extra=None,
                                diagnostic="partial documents"))
    assert partial["source"] is False and partial["reason"] == "partial documents"
    assert "no complete source" in bench.verdict(job(source="unverified"))["reason"]


def test_a_lead_within_noise_names_no_winner():
    rows = lambda engine, passed, total: [  # noqa: E731
        {"engine": engine, "family": "f%d" % i, "case": "c%d" % i, "source": True, "recompiles": True, "same_trace": i < passed, "reason": ""}
        for i in range(total)]
    close = bench.table(rows("native", 4, 6) + rows("rust", 3, 6))
    assert close["winner"] is None and "sin ganador" in close["note"]
    clear = bench.table(rows("native", 6, 6) + rows("rust", 2, 6))
    assert clear["winner"] == "native"


def test_engines_measured_on_different_cases_never_get_a_winner():
    ok = lambda engine, n, passed: [{"engine": engine, "family": "f%d" % i, "case": "c%d" % i, "ok": i < passed, "detail": "", "reason": ""}  # noqa: E731
                                    for i in range(n)]
    mixed = bench.simple_table(ok("rabcdasm", 4, 4) + ok("flasm", 2, 0), "disassemblers")
    assert mixed["winner"] is None and "casos distintos" in mixed["note"]
    gap = {"engine": "x", "ok": None, "detail": "", "reason": "no lee AS2"}
    same = bench.simple_table(ok("a", 4, 4) + ok("b", 4, 1) + [gap], "compilers")
    assert same["winner"] == "a"
    skipped = [line for line in same["engines"] if line["engine"] == "x"][0]
    assert skipped["cases"] == 0 and skipped["reasons"] == ["no lee AS2"]


def test_closeness_fields_are_means_and_jpexs_is_the_reference():
    row = lambda engine, case, close, cover: {"engine": engine, "family": "f", "case": case, "source": True,  # noqa: E731
                                              "recompiles": True, "same_trace": True, "reason": "",
                                              "jpexs_closeness": close, "jpexs_coverage": cover}
    result = bench.table([row("native", "a", 0.8, 1.0), row("native", "b", None, None), row("native", "c", 0.6, 0.5),
                          row("rust", "a", None, None), row("jpexs", "a", None, None)])
    by = {line["engine"]: line for line in result["engines"]}
    assert abs(by["native"]["jpexs_closeness"] - 0.7) < 1e-9 and by["native"]["jpexs_coverage"] == 0.75
    assert by["rust"]["jpexs_closeness"] is None and by["rust"]["jpexs_coverage"] is None
    assert by["jpexs"]["jpexs_closeness"] == 1.0 and by["jpexs"]["jpexs_coverage"] == 1.0 and by["jpexs"]["reference"] is True
    assert "reference" not in by["native"]


def test_closeness_row_says_why_when_jpexs_is_missing_or_failed():
    assert bench.closeness_row({}, None, b"")["jpexs_closeness"] is None
    failed = bench.closeness_row({}, {"status": "failed", "diagnostic": "boom"}, b"")
    assert failed["jpexs_closeness"] is None and "boom" in failed["jpexs_reason"]


def test_percent_prints_a_dash_for_missing_values():
    assert bench.percent(None) == "—" and bench.percent(0.756) == "76%"


def test_closeness_uses_documents_even_when_the_project_was_not_assembled(monkeypatch):
    reference = {"status": "not_supported", "source": {"status": "not_supported", "documents": [
        {"document_id": "d", "scope": "class", "bytecode_sha256": "h", "source": "class A {}"}]}}
    seen = {}
    monkeypatch.setattr(bench, "compare", lambda e, r, data, canonical=None: seen.update(engine=e, reference=r) or
                        {"closeness": 0.5, "coverage": 1.0, "weighted": 0.5, "reference_units": 1, "matched": 1, "excluded": {}})
    row = bench.closeness_row({"source": {"documents": []}}, reference, b"")
    assert row["jpexs_closeness"] == 0.5 and seen["reference"][0]["source"] == "class A {}"


def drow(engine, case, same=True, seconds=None, measured=True, close=None, cover=None, units=None):
    """A decompiler row as bench.verdict, bench.closeness_row and bench.units_row leave it (measured=False: the
    engine never ran). units is (units with source, units of the SWF)."""
    with_source, total = units if units and measured else (None, None)
    return {"engine": engine, "family": "f", "case": case, "source": measured and same, "recompiles": True if measured and same else None,
            "same_trace": same if measured else None, "reason": "" if measured else "no lee AS2", "measured": measured,
            "seconds": seconds, "jpexs_closeness": close, "jpexs_coverage": cover, "units_total": total, "units_with_source": with_source}


def test_schema_version_is_two_because_rows_now_carry_times():
    assert bench.SCHEMA_VERSION == 2


def test_verdict_keeps_the_time_the_engine_spent_and_whether_it_ran():
    ran = bench.verdict(job(timing={"source_seconds": 0.12345}))
    assert (ran["measured"], ran["seconds"]) == (True, 0.123)
    partial = bench.verdict(job(status="not_supported", source="not_supported", diagnostic="partial documents", timing={"source_seconds": 2.0}))
    assert (partial["measured"], partial["seconds"]) == (True, 2.0), "it ran and was timed even though its output was refused"
    assert bench.verdict(job())["seconds"] is None, "a job that recorded no time"
    for status in ("not_configured", "not_applicable"):
        skipped = bench.verdict(job(status=status, diagnostic="no instalado"))
        assert (skipped["measured"], skipped["seconds"]) == (False, None)
    timed_out = bench.verdict(job(status="timeout", diagnostic="exceeded 240 s"))
    assert (timed_out["measured"], timed_out["seconds"]) == (True, None)


def test_a_line_carries_the_median_seconds_per_case_and_how_many_cases_were_timed():
    rows = [drow("native", "c%d" % i, seconds=s) for i, s in enumerate([0.2, 0.4, None, 1.0])]
    line = bench.table(rows)["engines"][0]
    assert (line["median_seconds"], line["timed_cases"], line["cases"]) == (0.4, 3, 4)
    assert bench.table([drow("rust", "c")])["engines"][0]["median_seconds"] is None, "nothing was timed"


def test_flash_index_in_the_table_follows_its_definition_and_scores_jpexs_like_the_others():
    # native: complete source, recompiles and same trace in 3 of 6 cases, half of the units of the SWF with source:
    # (3/6 + 3/6 + 3/6 + 1/2) / 4. JPEXS gets everything right here and is scored the same way as the rest.
    rows = ([drow("native", "c%d" % i, same=i < 3, units=(1, 2)) for i in range(6)]
            + [drow("jpexs", "c%d" % i, close=1.0, cover=1.0, units=(2, 2)) for i in range(6)]
            + [drow("rust", "c%d" % i, same=False, units=(0, 2)) for i in range(6)])
    by = {line["engine"]: line for line in bench.table(rows)["engines"]}
    assert by["native"]["flash_index"] == 50.0
    assert by["jpexs"]["flash_index"] == 100.0 and by["jpexs"]["reference"] is True
    assert by["rust"]["flash_index"] == 0.0
    closer = [drow("native", "c%d" % i, same=i < 3, close=0.99, cover=0.99, units=(1, 2)) for i in range(6)]
    assert bench.table(closer)["engines"][0]["flash_index"] == 50.0, "closeness and coverage of JPEXS do not enter the index"
    assert bench.table([drow("native", "c")])["engines"][0]["flash_index"] is None, "no unit count, no index"


def test_a_line_sums_the_units_and_averages_their_coverage_per_case():
    rows = [drow("native", "c0", units=(1, 2)), drow("native", "c1", units=(3, 4)), drow("native", "c2", measured=False)]
    line = bench.table(rows)["engines"][0]
    assert (line["units_total"], line["units_with_source"]) == (6, 4)
    assert line["unit_coverage"] == (1 / 2 + 3 / 4) / 2, "every case weighs the same, a big SWF does not drown the small ones"
    nothing = bench.table([drow("rust", "c0")])["engines"][0]
    assert (nothing["units_total"], nothing["unit_coverage"]) == (None, None)


def test_a_row_counts_the_units_of_the_swf_the_engine_emitted_source_for():
    canonical = {("c0", "class"): {"sha": "h0", "methods": set()}, ("c1", "class"): {"sha": "h1", "methods": set()},
                 ("a0", "action"): {"sha": "ha", "methods": set()}, ("m0", "method"): {"sha": "hm", "methods": set()}}
    job = {"source": {"documents": [
        {"document_id": "c0", "scope": "class", "bytecode_sha256": "h0", "source": "class A {}"},
        {"document_id": "c1", "scope": "class", "bytecode_sha256": "WRONG", "source": "class B {}"}]}}
    assert bench.units_row(job, canonical) == {"units_total": 3, "units_with_source": 1}, "methods are not documents; a wrong hash is not verified"
    assert bench.units_row({}, canonical) == {"units_total": 3, "units_with_source": 0}, "an engine that died emitted nothing"


def test_engines_measured_on_other_cases_are_listed_but_never_ranked():
    four = lambda engine, passed: [drow(engine, "c%d" % i, same=i < passed) for i in range(4)]  # noqa: E731
    narrow = [drow("pyflashkit", "c%d" % i, measured=i < 2) for i in range(4)]  # reads two of the four
    absent = [drow("flare", "c%d" % i, measured=False) for i in range(4)]  # not installed
    result = bench.table(four("native", 4) + four("rust", 1) + narrow + absent)
    by = {line["engine"]: line for line in result["engines"]}
    assert [by[e]["comparable"] for e in ("native", "rust", "pyflashkit", "flare")] == [True, True, False, False]
    assert (by["pyflashkit"]["cases"], by["pyflashkit"]["same_trace"]) == (2, 2), "perfect on what it reads, but not on the same cases"
    assert by["flare"]["cases"] == 0 and by["flare"]["flash_index"] is None
    assert [line["engine"] for line in result["engines"]] == ["native", "rust", "pyflashkit", "flare"]
    assert result["winner"] == "native" and result["corpus_cases"] == 4


def test_jpexs_competes_for_the_winner_like_any_other_engine():
    rows = ([drow("jpexs", "c%d" % i) for i in range(5)] + [drow("native", "c%d" % i, same=i < 3) for i in range(5)]
            + [drow("rust", "c%d" % i, same=False) for i in range(5)])
    assert bench.table(rows)["winner"] == "jpexs"
    beaten = [drow("jpexs", "c%d" % i, same=i < 1) for i in range(5)] + [drow("native", "c%d" % i) for i in range(5)]
    assert bench.table(beaten)["winner"] == "native", "and it can lose"
    alone = bench.table([drow("jpexs", "c%d" % i) for i in range(5)])
    assert alone["winner"] is None and "ningún par" in alone["note"], "one engine is not a ranking"


def test_simple_tables_carry_median_seconds_and_rank_only_engines_on_the_same_cases():
    row = lambda engine, case, ok, seconds=None: {"engine": engine, "family": "f", "case": case, "ok": ok, "detail": "", "reason": "", "seconds": seconds}  # noqa: E731
    rows = [row("flex", "a", True, 2.0), row("flex", "b", True, 4.0), row("native", "a", True, 0.1), row("native", "b", False, 0.3),
            row("rascal", "c", True, 1.0), row("haxe", "c", None)]
    result = bench.simple_table(rows, "compilers")
    by = {line["engine"]: line for line in result["engines"]}
    assert (by["flex"]["median_seconds"], by["native"]["median_seconds"], by["haxe"]["median_seconds"]) == (3.0, 0.2, None)
    assert (by["flex"]["timed_cases"], by["haxe"]["cases"]) == (2, 0)
    assert [by[e]["comparable"] for e in ("flex", "native", "rascal", "haxe")] == [True, True, False, False]
    assert result["winner"] is None and "sin ganador claro" in result["note"], "one case apart, on the cases both were measured on"
    assert result["corpus_cases"] == 3


def test_the_console_table_shows_the_index_the_seconds_and_marks_engines_on_other_cases(capsys):
    four = lambda engine, passed: [drow(engine, "c%d" % i, same=i < passed, seconds=0.25, close=0.9, cover=0.6, units=(1, 2)) for i in range(4)]  # noqa: E731
    narrow = [drow("pyflashkit", "c%d" % i, measured=i < 2, seconds=0.5, close=0.5, cover=0.5, units=(1, 2)) for i in range(4)]
    bench.show({"decompilers": bench.table(four("native", 4) + four("rust", 1) + narrow)})
    lines = capsys.readouterr().out.splitlines()
    assert any("índice" in line and "s/caso" in line for line in lines)
    native = next(line for line in lines if line.startswith("native"))
    assert "87.5" in native and "0.25" in native and "*" not in native, "(4/4 + 4/4 + 4/4 + 1/2) / 4"
    assert next(line for line in lines if line.startswith("pyflashkit")).startswith("pyflashkit *")
    assert "* medido sobre otros casos: no se ordena con el resto" in lines


def test_task_rows_round_the_tool_time_to_milliseconds_and_keep_none_when_it_did_not_finish():
    assert bench_tasks.row("flex", "f", "c", True, seconds=0.12345)["seconds"] == 0.123
    assert bench_tasks.row("flex", "f", "c", False, reason="boom")["seconds"] is None
