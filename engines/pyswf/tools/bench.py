"""The benchmark: does what a decompiler emits recompile, and does it behave the same?

One question per category, one small results.json per run (reports/bench/<run>/),
one index.json listing the runs. Every cell that is not a number says why.

    python tools/bench.py --smoke      # one case per development family, ~minutes
    python tools/bench.py --full       # every development and validation case

Decompilers: each engine decompiles the original SWF, the emitted source is
recompiled by an independent compiler (Flex for AS3, Rascal for AS2) and Ruffle
must print the same trace as the original. The per-job machinery is shared with
run_semantic_benchmarks.py (its --worker mode); this file only chooses the jobs,
keeps the verdicts and drops the heavy evidence. The other categories (compilers,
disassemblers, converters, extractors) are tasks in bench_tasks.py.

Schema 2 also records the time each engine spent per case (`seconds`, median per
engine), how many of the SWF's own units each decompiler emitted source for and
which engines were measured on the same cases (`comparable`); the decompilers get
the Índice Flash, JPEXS included. JPEXS runs once per case: that one job is its own
row and the reference the closeness of the others is measured against.
py_swf/bench_results.py holds the scoring and docs/BENCHMARKS_CODIGO.md defines it.
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
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

from py_swf import paths
WORKSPACE = paths.WORKSPACE

from py_swf.bench_results import SCHEMA_VERSION, comparable_engines, flash_index, median_seconds, timed_cases
from py_swf.benchmark_engines import ENGINES
from py_swf.closeness import canonical_units, compare, inventory_coverage
from py_swf.corpus import read_immutable_manifest
from py_swf.suite_tools import fingerprint
from run_ruffle_trace_probe import bounded_run
from py_swf.suite_tools import ToolRegistry
from run_semantic_benchmarks import SOURCE_ENGINES, discover_pins, select_cases, write_json
import bench_tasks

OUT = WORKSPACE / "reports" / "bench"
CORPUS = paths.CORPUS / "parity-20261014" / "optimized"
RUNTIME = Path(os.environ.get("LOCALAPPDATA", Path.home())) / (
    "FlashParityRuntime/ruffle-target/x86_64-pc-windows-gnu/debug/deps/tests-470feeee72990987.exe")
# Differences of one case out of this many are noise, not a winner.
NOISE_CASES = 1


def settings(engines, job_timeout):
    return argparse.Namespace(
        suite_config=None, engines=engines, job_timeout=job_timeout, runtime=RUNTIME,
        rust_executable=None, tool_config=paths.REFERENCES / "extra-tools.json",
        reference_config=paths.REFERENCES / "toolchain.json",
        rascal=paths.REFERENCES / "extra-tools/rascal-0.3.5/bin/rascal.exe")


def verdict(job):
    """Collapse one finished job into the three facts the table counts, plus the reason. `seconds` is what the engine
    spent producing its source (None when it never finished); `measured` is False when it never ran on the case."""
    row = {"engine": job["engine"], "family": job["family_id"], "case": job["case_id"],
           "language": job["language"], "source": False, "recompiles": None, "same_trace": None, "reason": "",
           "measured": job["status"] not in ("not_configured", "not_applicable"), "seconds": None}
    seconds = (job.get("timing") or {}).get("source_seconds")
    if seconds is not None:
        row["seconds"] = round(seconds, 3)
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


def documents_of(result):
    """The generated documents of a finished job, with their text read back from disk."""
    units = []
    for document in result.get("source", {}).get("documents", []):
        text = document.get("source")
        if text is None and document.get("source_file"):
            try:
                text = Path(document["source_file"]).read_text(encoding="utf-8")
            except OSError:
                text = None
        units.append({"document_id": document.get("document_id"), "scope": document.get("scope"),
                      "bytecode_sha256": document.get("bytecode_sha256"), "unit_ids": document.get("unit_ids"), "source": text})
    return units


def units_row(engine_job, canonical):
    """The units of the SWF (its own ABC/action inventory, no decompiler involved) and how many of them the
    engine emitted verified source for, even when its project was refused as a whole."""
    found = inventory_coverage(documents_of(engine_job), canonical)
    return {"units_total": found["units"], "units_with_source": found["with_source"]}


def closeness_row(engine_job, reference_job, data, canonical=None):
    """Fields the table reads: similarity to JPEXS and how much of JPEXS's output the engine covers."""
    if reference_job is None:
        return {"jpexs_closeness": None, "jpexs_coverage": None, "jpexs_reason": "JPEXS no se ejecutó para este caso"}
    reference = documents_of(reference_job)
    if not any(unit["source"] is not None for unit in reference):
        why = reference_job.get("diagnostic") or reference_job.get("source", {}).get("diagnostic") or "JPEXS no emitió fuente"
        return {"jpexs_closeness": None, "jpexs_coverage": None, "jpexs_reason": "JPEXS: " + str(why).strip().splitlines()[-1][:200]}
    engine = documents_of(engine_job)
    try:
        found = compare(engine, reference, data, canonical)
    except Exception as error:
        return {"jpexs_closeness": None, "jpexs_coverage": None, "jpexs_reason": "comparación fallida: " + str(error)[:160]}
    return {"jpexs_closeness": found["closeness"], "jpexs_coverage": found["coverage"],
            "jpexs_weighted": found["weighted"], "jpexs_detail": {k: found[k] for k in ("reference_units", "matched", "excluded")},
            "jpexs_reason": "" if found["closeness"] is not None else "ninguna unidad verificada coincide con JPEXS"}


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def total(values):
    values = [v for v in values if v is not None]
    return sum(values) if values else None


def share(count, cases):
    return count / cases if cases else 0.0


def table(rows):
    """One line per engine, counted over the cases it was measured on. Engines measured on the same cases are
    ranked together and the winner is named only when its lead exceeds the noise; JPEXS competes like the
    others (it is only the reference of the closeness columns); an engine measured on other cases is listed
    with `comparable: false` and is not ranked."""
    by_engine = defaultdict(list)
    for row in rows:
        by_engine[row["engine"]].append(row)
    together = comparable_engines(rows)
    lines = []
    for engine, items in by_engine.items():
        reference = engine == "jpexs"
        ran = [r for r in items if r.get("measured", True)]
        line = {"engine": engine, "name": ENGINES[engine].name, "cases": len(ran),
                "source": sum(r["source"] for r in ran),
                "recompiles": sum(r["recompiles"] is True for r in ran),
                "same_trace": sum(r["same_trace"] is True for r in ran),
                "jpexs_closeness": 1.0 if reference else mean(r.get("jpexs_closeness") for r in items),
                "jpexs_coverage": 1.0 if reference else mean(r.get("jpexs_coverage") for r in items),
                "units_total": total(r.get("units_total") for r in ran), "units_with_source": total(r.get("units_with_source") for r in ran),
                "unit_coverage": mean(r["units_with_source"] / r["units_total"] for r in ran if r.get("units_total")),
                "median_seconds": median_seconds(ran), "timed_cases": timed_cases(ran),
                "comparable": engine in together,
                **({"reference": True} if reference else {}),
                "reasons": sorted({r["reason"] for r in items if r["reason"]})[:3]}
        line["flash_index"] = flash_index(line)
        lines.append(line)
    lines.sort(key=lambda l: (not l["comparable"], -share(l["same_trace"], l["cases"]),
                              -share(l["recompiles"], l["cases"]), -share(l["source"], l["cases"])))
    rivals = [l for l in lines if l["comparable"]]
    winner = rivals[0]["engine"] if len(rivals) > 1 and rivals[0]["same_trace"] - rivals[1]["same_trace"] > NOISE_CASES else None
    note = None if winner else ("sin ganador claro: la ventaja es de %d caso(s) o menos" % NOISE_CASES if len(rivals) > 1
                                else "sin ganador: ningún par de motores se midió sobre los mismos casos")
    return {"unit": "casos en que el fuente generado recompila y Ruffle imprime la misma traza que el original",
            "winner": winner, "note": note, "corpus_cases": len({(r.get("family"), r.get("case")) for r in rows}),
            "engines": lines}


CATEGORIES = {
    "decompilers": "Descompiladores",
    "compilers": "Compiladores",
    "disassemblers": "Desensambladores",
    "converters": "Conversores XML",
    "extractors": "Extractores",
}
UNITS = {
    "compilers": "proyectos del corpus que compilan y Ruffle imprime la traza esperada",
    "disassemblers": "SWF que tras desensamblar y reensamblar imprimen la misma traza (solo los que reensamblan)",
    "converters": "SWF que tras SWF a XML a SWF imprimen la misma traza",
    "extractors": "SWF cuyos recursos se extraen todos y decodifican",
}


def simple_table(rows, category):
    """Same shape as table(): applicable cases only; the others keep their reason."""
    by_engine = defaultdict(list)
    for item in rows:
        by_engine[item["engine"]].append(item)
    together = comparable_engines(rows)
    lines = []
    for engine, items in by_engine.items():
        ran = [i for i in items if i["ok"] is not None]
        lines.append({"engine": engine, "name": ENGINES[engine].name if engine in ENGINES else engine,
                      "cases": len(ran), "ok": sum(i["ok"] is True for i in ran),
                      "median_seconds": median_seconds(ran), "timed_cases": timed_cases(ran),
                      "comparable": engine in together,
                      "details": sorted({i["detail"] for i in items if i["detail"]})[:3],
                      "reasons": sorted({i["reason"] for i in items if i["reason"]})[:3]})
    lines.sort(key=lambda l: (l["cases"] == 0, not l["comparable"], -share(l["ok"], l["cases"]), l["engine"]))
    measured = [l for l in lines if l["cases"]]
    rivals = [l for l in measured if l["comparable"]]
    winner = rivals[0]["engine"] if len(rivals) > 1 and rivals[0]["ok"] - rivals[1]["ok"] > NOISE_CASES else None
    if winner:
        note = None
    elif len(measured) < 2:
        note = "un solo motor medido: no hay con qué compararlo"
    elif len(rivals) < 2:
        note = "sin ganador: los motores se midieron sobre casos distintos (leen lenguajes distintos)"
    else:
        note = "sin ganador claro: la ventaja es de %d caso(s) o menos" % NOISE_CASES
    return {"unit": UNITS[category], "winner": winner, "note": note,
            "corpus_cases": len({(i.get("family"), i.get("case")) for i in rows}), "engines": lines}


def percent(value):
    return "—" if value is None else "%.0f%%" % (100 * value)


def number(value, template="%.1f"):
    return "—" if value is None else template % value


def show(tables):
    for category, data in tables.items():
        print()
        print("== %s ==  (%s)" % (CATEGORIES[category], data["unit"]))
        if category == "decompilers":
            print("%-12s %5s %8s %10s %11s %9s %11s %11s %7s %8s" % ("motor", "casos", "fuente", "recompila", "misma traza", "unidades", "cerca JPEXS", "cubre JPEXS", "índice", "s/caso"))
        else:
            print("%-12s %5s %9s %8s" % ("motor", "casos", "correctos", "s/caso"))
        ranked = any(line["comparable"] for line in data["engines"])
        for line in data["engines"]:
            name = line["engine"] + (" *" if ranked and line["cases"] and not line["comparable"] else "")
            seconds = number(line["median_seconds"], "%.2f")
            if category == "decompilers":
                print("%-12s %5d %8d %10d %11d %9s %11s %11s %7s %8s" % (name, line["cases"], line["source"], line["recompiles"], line["same_trace"],
                                                                         percent(line.get("unit_coverage")), percent(line.get("jpexs_closeness")),
                                                                         percent(line.get("jpexs_coverage")), number(line["flash_index"]), seconds))
            else:
                print("%-12s %5d %9d %8s" % (name, line["cases"], line["ok"], seconds))
            for reason in line["reasons"]:
                print("    - " + reason)
        if ranked and any(not line["comparable"] and line["cases"] for line in data["engines"]):
            print("* medido sobre otros casos: no se ordena con el resto")
        print("ganador: " + data["winner"] if data["winner"] else data["note"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true", help="un caso por familia de desarrollo (por defecto)")
    mode.add_argument("--full", action="store_true", help="todos los casos de desarrollo y validación")
    parser.add_argument("--only", nargs="+", choices=list(CATEGORIES), default=list(CATEGORIES), help="categorías a medir")
    parser.add_argument("--engines", nargs="+", choices=SOURCE_ENGINES, default=list(SOURCE_ENGINES),
                        help="descompiladores (JPEXS se mide siempre: es la referencia de cercanía y compite como los demás)")
    parser.add_argument("--job-timeout", type=int, default=240)
    parser.add_argument("--keep-evidence", action="store_true", help="conserva las carpetas de cada trabajo (grandes)")
    args = parser.parse_args()

    manifest = read_immutable_manifest(CORPUS, verify_sources=False)
    splits = ("development", "validation") if args.full else ("development",)
    cases = select_cases(manifest, splits=splits)
    if not args.full:
        first = {}
        for case in cases:
            first.setdefault(case["family_id"], case)
        cases = list(first.values())

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = OUT / run_id
    started = time.perf_counter()
    tables, all_rows = {}, {}

    if "decompilers" in args.only:
        runnable = list(args.engines) + ([] if "jpexs" in args.engines else ["jpexs"])
        pins = discover_pins(settings(runnable, args.job_timeout))
        rows = []
        for case in cases:
            results, case_rows = {}, {}
            for engine in runnable:
                base = {"engine": engine, "family": case["family_id"], "case": case["relative"], "language": case["language"],
                        "source": False, "recompiles": None, "same_trace": None, "measured": False, "seconds": None}
                if pins["source_tools"].get(engine, {}).get("status") == "not_configured":
                    row = {**base, "reason": "no instalado: " + str(pins["source_tools"][engine].get("diagnostic", ""))[:200]}
                elif case["language"] not in ENGINES[engine].languages:
                    row = {**base, "reason": "no lee " + case["language"].upper()}
                else:
                    work = folder / "work" / (case["family_id"] + "-" + engine)
                    results[engine] = run_job(pins, case, engine, work, args.job_timeout)
                    row = verdict(results[engine])
                    print("descompilador", case["family_id"], engine, row["same_trace"], flush=True)
                case_rows[engine] = row
            data = (CORPUS / case["relative"]).read_bytes()
            canonical = canonical_units(data)
            # JPEXS ran once for this case: that job is its own row and the reference for the closeness of the rest.
            for engine in runnable:
                row = case_rows[engine]
                if engine in results:
                    row.update(units_row(results[engine], canonical))
                else:
                    row.update(units_total=None, units_with_source=None)
                if engine == "jpexs":
                    row.update(jpexs_closeness=1.0 if "jpexs" in results else None, jpexs_coverage=1.0 if "jpexs" in results else None)
                elif engine in results:
                    row.update(closeness_row(results[engine], results.get("jpexs"), data, canonical))
                else:
                    row.update(jpexs_closeness=None, jpexs_coverage=None)
                rows.append(row)
        if not args.keep_evidence:
            shutil.rmtree(folder / "work", ignore_errors=True)
        tables["decompilers"], all_rows["decompilers"] = table(rows), rows

    others = [c for c in args.only if c != "decompilers"]
    if others:
        by_family = {case["family_id"]: case for case in cases}
        ctx = {"tool_config": paths.REFERENCES / "extra-tools.json", "runtime": RUNTIME, "registry": ToolRegistry(None),
               "rascal": paths.REFERENCES / "extra-tools/rascal-0.3.5/bin/rascal.exe",
               "original_of": lambda family: CORPUS / by_family[family]["relative"]}
        tasks = {"disassemblers": bench_tasks.disassemblers, "converters": bench_tasks.converters, "extractors": bench_tasks.extractors}
        for category in others:
            rows = []
            if category == "compilers":
                for family in manifest["families"]:
                    if family["family_id"] in by_family:
                        rows += guarded(bench_tasks.compilers, family, CORPUS.parent / "projects" / family["family_id"], ctx, family["family_id"], category)
            else:
                for case in (cases_with_resources(manifest) if category == "extractors" else cases):
                    rows += guarded(tasks[category], case, CORPUS / case["relative"], ctx, case["relative"], category)
            tables[category], all_rows[category] = simple_table(rows, category), rows

    results = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "mode": "full" if args.full else "smoke",
               "created_utc": datetime.now(timezone.utc).isoformat(), "seconds": round(time.perf_counter() - started),
               "corpus": manifest["corpus_id"], "cases": len(cases),
               "oracle": {"runtime": RUNTIME.name, "runtime_sha256": fingerprint(RUNTIME) if RUNTIME.is_file() else None},
               "tables": tables, "rows": all_rows}
    write_json(folder / "results.json", results)
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else []
    index.append({"run_id": run_id, "mode": results["mode"], "cases": len(cases), "seconds": results["seconds"],
                  "created_utc": results["created_utc"], "categories": list(tables),
                  "winner": tables.get("decompilers", {}).get("winner")})
    write_json(index_path, index)
    show(tables)
    print()
    print(folder / "results.json", "%.1f KB" % ((folder / "results.json").stat().st_size / 1024))
    return 0


def cases_with_resources(manifest):
    """Extraction needs SWFs that have resources: one per family, development and validation."""
    from py_swf.benchmark_resource_tools import EXTRACTABLE
    from py_swf.swf_parser import SWFFile
    chosen = {}
    for case in select_cases(manifest, splits=("development", "validation")):
        if case["family_id"] in chosen:
            continue
        movie = SWFFile()
        movie.read_bytes((CORPUS / case["relative"]).read_bytes())
        if any(tag.tag_type in EXTRACTABLE and tag.char_id is not None for tag in movie.tags):
            chosen[case["family_id"]] = case
    return list(chosen.values())


def guarded(task, subject, path, ctx, name, category):
    """A task that blows up is a failed cell with its reason, never a missing one."""
    try:
        rows = task(subject, path, ctx)
    except Exception as error:
        rows = [bench_tasks.row("?", name, name, False, reason=type(error).__name__ + ": " + str(error).splitlines()[0][:160])]
    for item in rows:
        print(category, name, item["engine"], item["ok"], flush=True)
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
