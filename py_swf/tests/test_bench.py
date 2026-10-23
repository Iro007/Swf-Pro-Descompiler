"""The benchmark counts what happened and says why when nothing did."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bench  # noqa: E402


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
        {"engine": engine, "source": True, "recompiles": True, "same_trace": i < passed, "reason": ""} for i in range(total)]
    close = bench.table(rows("native", 4, 6) + rows("rust", 3, 6))
    assert close["winner"] is None and "sin ganador" in close["note"]
    clear = bench.table(rows("native", 6, 6) + rows("rust", 2, 6))
    assert clear["winner"] == "native"


def test_engines_measured_on_different_cases_never_get_a_winner():
    ok = lambda engine, n, passed: [{"engine": engine, "ok": i < passed, "detail": "", "reason": ""} for i in range(n)]  # noqa: E731
    mixed = bench.simple_table(ok("rabcdasm", 4, 4) + ok("flasm", 2, 0), "disassemblers")
    assert mixed["winner"] is None and "casos distintos" in mixed["note"]
    gap = {"engine": "x", "ok": None, "detail": "", "reason": "no lee AS2"}
    same = bench.simple_table(ok("a", 4, 4) + ok("b", 4, 1) + [gap], "compilers")
    assert same["winner"] == "a"
    skipped = [line for line in same["engines"] if line["engine"] == "x"][0]
    assert skipped["cases"] == 0 and skipped["reasons"] == ["no lee AS2"]
