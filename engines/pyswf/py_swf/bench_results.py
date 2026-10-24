"""Read what tools/bench.py wrote (reports/bench/index.json and one results.json per run) and score it.

The scoring lives beside the reader so that the writer (tools/bench.py) and every reader agree on it:
which engines may be ranked together, the median time per case and the Índice Flash. The definitions
are in docs/BENCHMARKS_CODIGO.md. A run written before schema 2 is read the same way as a new one:
`upgrade` fills in what it lacks (None where the run never measured it).
"""
import json
import os
from pathlib import Path
from statistics import median

from .suite_tools import ROOT

SCHEMA_VERSION = 2


def bench_directory():
    return Path(os.environ.get("FLASH_BENCH_DIR") or ROOT / "reports" / "bench")


def is_measured(row):
    """The engine really ran on this case, rather than being skipped (not installed, does not read the language)."""
    return row["ok"] is not None if "ok" in row else row.get("measured", True)


def comparable_engines(rows):
    """Ids of the engines that may be ranked together: the largest group measured on exactly the same
    cases. Fewer than two engines is not a comparison, so then nobody is comparable."""
    cases = {}
    for row in rows:
        if is_measured(row):
            cases.setdefault(row["engine"], set()).add((row["family"], row["case"]))
    groups = {}
    for engine, found in cases.items():
        groups.setdefault(frozenset(found), []).append(engine)
    if not groups:
        return set()
    together = max(groups.items(), key=lambda group: (len(group[1]), len(group[0])))[1]
    return set(together) if len(together) > 1 else set()


def median_seconds(rows):
    """Median seconds per case over the measured rows that carry a time, or None when no row does."""
    values = [row["seconds"] for row in rows if isinstance(row.get("seconds"), (int, float))]
    return round(median(values), 3) if values else None


def timed_cases(rows):
    return sum(isinstance(row.get("seconds"), (int, float)) for row in rows)


def flash_index(line):
    """Índice Flash of a decompiler line, 0 to 100: the mean of four shares that every engine, JPEXS included, is
    judged on equally and that need no other decompiler to measure: the cases with complete source, the cases
    whose source recompiles, the cases where Ruffle prints the same trace as the original, and the units of the
    SWF's own inventory (classes, scripts, actions) the engine emitted source for. None when the engine was not
    measured or the run did not count the units (a run from before schema 2)."""
    cases, units = line.get("cases"), line.get("unit_coverage")
    if not cases or units is None:
        return None
    return round(100 * (line["source"] / cases + line["recompiles"] / cases + line["same_trace"] / cases + units) / 4, 1)


def upgrade(results):
    """Give a run written before schema 2 the fields the interface reads; what it never measured stays None."""
    results.setdefault("schema_version", 1)
    for category, table in results.get("tables", {}).items():
        rows = results.get("rows", {}).get(category, [])
        together = comparable_engines(rows)
        table.setdefault("corpus_cases", len({(row["family"], row["case"]) for row in rows}) or results.get("cases"))
        for line in table["engines"]:
            by_engine = [row for row in rows if row["engine"] == line["engine"] and is_measured(row)]
            line.setdefault("median_seconds", median_seconds(by_engine))
            line.setdefault("timed_cases", timed_cases(by_engine))
            line.setdefault("comparable", line["engine"] in together if rows else True)
            if category == "decompilers":
                for key in ("units_total", "units_with_source", "unit_coverage"):
                    line.setdefault(key, None)
                line.setdefault("flash_index", flash_index(line))
    return results


def read_bench(run=None):
    folder = bench_directory()
    index_path = folder / "index.json"
    runs = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else []
    if not runs:
        return {"runs": [], "results": None, "folder": str(folder)}
    wanted = run or runs[-1]["run_id"]
    if wanted not in {entry["run_id"] for entry in runs}:
        raise ValueError("Unknown benchmark run")
    results = upgrade(json.loads((folder / wanted / "results.json").read_text(encoding="utf-8")))
    return {"runs": runs[::-1], "results": results, "folder": str(folder)}
