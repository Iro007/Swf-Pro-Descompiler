"""The benchmark: does what a decompiler emits recompile, and does it behave the same?

One question per category, one small results.json per run (reports/bench/<run>/),
one index.json listing the runs. Every cell that is not a number says why.

    python tools/bench.py --smoke      # one case per development family, ~minutes
    python tools/bench.py --full       # every development and validation case

Decompilers only for now: each engine decompiles the original SWF, the emitted
source is recompiled by an independent compiler (Flex for AS3, Rascal for AS2)
and Ruffle must print the same trace as the original. The per-job machinery is
shared with run_semantic_benchmarks.py (its --worker mode); this file only
chooses the jobs, keeps the verdicts and drops the heavy evidence.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

from py_swf.benchmark_engines import ENGINES
from py_swf.corpus import read_immutable_manifest
from py_swf.suite_tools import fingerprint
from run_ruffle_trace_probe import bounded_run
from run_semantic_benchmarks import SOURCE_ENGINES, discover_pins, select_cases, write_json

SCHEMA_VERSION = 1
OUT = WORKSPACE / "reports" / "bench"
CORPUS = WORKSPACE / "benchmark_inputs" / "parity-20261014" / "optimized"
RUNTIME = Path(os.environ.get("LOCALAPPDATA", Path.home())) / (
    "FlashParityRuntime/ruffle-target/x86_64-pc-windows-gnu/debug/deps/tests-470feeee72990987.exe")
# Differences of one case out of this many are noise, not a winner.
NOISE_CASES = 1


def settings(engines, job_timeout):
    return argparse.Namespace(
        suite_config=None, engines=engines, job_timeout=job_timeout, runtime=RUNTIME,
        rust_executable=None, tool_config=PROJECT / ".dev-references/extra-tools.json",
        reference_config=PROJECT / ".dev-references/toolchain.json",
        rascal=PROJECT / ".dev-references/extra-tools/rascal-0.3.5/bin/rascal.exe")


def verdict(job):
    """Collapse one finished job into the three facts the table counts, plus the reason."""
    row = {"engine": job["engine"], "family": job["family_id"], "case": job["case_id"],
           "language": job["language"], "source": False, "recompiles": None, "same_trace": None, "reason": ""}
    if job["status"] in ("not_configured", "not_applicable", "not_supported", "timeout", "failed"):
        text = job.get("diagnostic") or job.get("source", {}).get("diagnostic") or job["status"]
        row["reason"] = str(text).strip().splitlines()[-1][:240]
        return row
    row["source"] = job.get("source", {}).get("status") == "complete"
    if not row["source"]:
        row["reason"] = job.get("source", {}).get("diagnostic") or "no complete source emitted"
        return row
    independent = [c for c in job.get("compilations", []) if c["compiler"].startswith("independent_")]
    if not independent:
        row["reason"] = "no independent compiler ran"
        return row
    compiler = independent[0]
    row["compiler"] = compiler["compiler"]
    row["recompiles"] = compiler["status"] == "compiled"
    if not row["recompiles"]:
        row["reason"] = (compiler.get("diagnostic") or compiler["status"])[:240]
        return row
    runtime = compiler.get("runtime", {})
    row["same_trace"] = runtime.get("status") == "passed_bounded_trace"
    if not row["same_trace"]:
        row["reason"] = runtime.get("diagnostic") or runtime.get("status", "not run")
    return row


def run_job(pins, case, engine, work, timeout):
    work.mkdir(parents=True)
    (work / "input.swf").write_bytes((CORPUS / case["relative"]).read_bytes())
    request = {"folder": str(work), "pins": pins, "case": case, "engine": engine,
               "scenario": {"expected_trace": case["expected_trace"], "ticks": 1},
               "suite_config": pins["suite_config"]["path"]}
    write_json(work / "request.json", request)
    process = bounded_run([sys.executable, str(PROJECT / "tools/run_semantic_benchmarks.py"), "--worker",
                           str(work / "request.json")], PROJECT, timeout=timeout)
    result_path = work / "worker-result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        result = {"engine": engine, "family_id": case["family_id"], "case_id": case["relative"],
                  "language": case["language"], "status": "timeout" if process["timed_out"] else "failed",
                  "diagnostic": ("exceeded %d s" % timeout) if process["timed_out"] else process["stderr"]}
    return result


def table(rows):
    """One line per engine; the winner is named only when the lead exceeds the noise."""
    by_engine = defaultdict(list)
    for row in rows:
        by_engine[row["engine"]].append(row)
    lines = []
    for engine, items in by_engine.items():
        lines.append({"engine": engine, "name": ENGINES[engine].name, "cases": len(items),
                      "source": sum(r["source"] for r in items),
                      "recompiles": sum(r["recompiles"] is True for r in items),
                      "same_trace": sum(r["same_trace"] is True for r in items),
                      "reasons": sorted({r["reason"] for r in items if r["reason"]})[:3]})
    lines.sort(key=lambda l: (-l["same_trace"], -l["recompiles"], -l["source"]))
    if len(lines) > 1:
        lead = lines[0]["same_trace"] - lines[1]["same_trace"]
        winner = lines[0]["engine"] if lead > NOISE_CASES else None
    else:
        winner = None
    return {"unit": "casos en que el fuente generado recompila y Ruffle imprime la misma traza que el original",
            "winner": winner, "note": None if winner else "sin ganador claro: la ventaja es de %d caso(s) o menos" % NOISE_CASES,
            "engines": lines}


def show(tables):
    for category, data in tables.items():
        print("\n== %s ==  (%s)" % (category, data["unit"]))
        print("%-12s %5s %7s %10s %11s" % ("engine", "cases", "source", "recompiles", "same trace"))
        for line in data["engines"]:
            print("%-12s %5d %7d %10d %11d" % (line["engine"], line["cases"], line["source"], line["recompiles"], line["same_trace"]))
            for reason in line["reasons"]:
                print("    - " + reason)
        print("winner: " + (data["winner"] or data["note"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true", help="one case per development family (default)")
    mode.add_argument("--full", action="store_true", help="every development and validation case")
    parser.add_argument("--engines", nargs="+", choices=SOURCE_ENGINES, default=list(SOURCE_ENGINES))
    parser.add_argument("--job-timeout", type=int, default=240)
    parser.add_argument("--keep-evidence", action="store_true", help="keep the per-job folders (large)")
    args = parser.parse_args()

    manifest = read_immutable_manifest(CORPUS, verify_sources=False)
    splits = ("development", "validation") if args.full else ("development",)
    cases = select_cases(manifest, splits=splits)
    if not args.full:
        first = {}
        for case in cases:
            first.setdefault(case["family_id"], case)
        cases = list(first.values())

    pins = discover_pins(settings(args.engines, args.job_timeout))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = OUT / run_id
    rows, started = [], time.perf_counter()
    for case in cases:
        for engine in args.engines:
            if pins["source_tools"].get(engine, {}).get("status") == "not_configured":
                rows.append({"engine": engine, "family": case["family_id"], "case": case["relative"],
                             "language": case["language"], "source": False, "recompiles": None, "same_trace": None,
                             "reason": "not installed: " + str(pins["source_tools"][engine].get("diagnostic", ""))[:200]})
                continue
            if case["language"] not in ENGINES[engine].languages:
                rows.append({"engine": engine, "family": case["family_id"], "case": case["relative"],
                             "language": case["language"], "source": False, "recompiles": None, "same_trace": None,
                             "reason": "does not read " + case["language"].upper()})
                continue
            work = folder / "work" / (case["family_id"] + "-" + engine)
            job = run_job(pins, case, engine, work, args.job_timeout)
            rows.append(verdict(job))
            print(case["family_id"], engine, rows[-1]["same_trace"], flush=True)
    if not args.keep_evidence:
        shutil.rmtree(folder / "work", ignore_errors=True)

    tables = {"decompilers": table(rows)}
    results = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "mode": "full" if args.full else "smoke",
               "created_utc": datetime.now(timezone.utc).isoformat(), "seconds": round(time.perf_counter() - started),
               "corpus": manifest["corpus_id"], "cases": len(cases),
               "oracle": {"runtime": RUNTIME.name, "runtime_sha256": fingerprint(RUNTIME) if RUNTIME.is_file() else None},
               "tables": tables, "rows": rows}
    write_json(folder / "results.json", results)
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else []
    index.append({"run_id": run_id, "mode": results["mode"], "cases": len(cases), "seconds": results["seconds"],
                  "winner": tables["decompilers"]["winner"]})
    write_json(index_path, index)
    show(tables)
    print("\n" + str(folder / "results.json"), "%.1f KB" % ((folder / "results.json").stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
