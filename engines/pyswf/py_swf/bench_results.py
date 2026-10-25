"""Read what tools/bench.py wrote (reports/bench/index.json and one results.json per run) and score it.

The scoring lives beside the reader so that the writer (tools/bench.py) and every reader agree on it:
which engines may be ranked together, the median time per case, the robustness score, the fidelity of a unit and the
Índice Flash. The definitions are in docs/BENCHMARKS_CODIGO.md. A run written before schema 3 is read the same way as a
new one: `upgrade` fills in what it lacks (None where the run never measured it).
"""
import json
import os
from pathlib import Path
from statistics import median

from .suite_tools import ROOT

SCHEMA_VERSION = 3

# The five outcomes of an engine on a damaged file and what each is worth (docs/BENCHMARKS_CODIGO.md, Robustez).
OUTCOMES = ("recovered", "partial", "clean_error", "crash", "hang")
OUTCOME_SCORES = {"recovered": 100, "partial": 60, "clean_error": 40, "crash": 0, "hang": 0}
# The outcomes of a unit that is compiled alone and spliced into the original SWF (Fidelidad por unidad).
UNIT_OUTCOMES = ("same", "different", "no_compile", "not_run")
# The terms of the Índice Flash. The first four are the base of every run; the last two enter only when the run measured
# them for every engine that is ranked.
BASE_COMPONENTS = ("source", "recompiles", "same_trace", "units")
EXTRA_COMPONENTS = ("unit_fidelity", "robustness")
INDEX_COMPONENTS = BASE_COMPONENTS + EXTRA_COMPONENTS


def bench_directory():
    explicit = os.environ.get("FLASH_BENCH_DIR")
    if explicit:
        return Path(explicit)
    primary = ROOT / "reports" / "bench"
    if (primary / "index.json").is_file():
        return primary
    for parent in ROOT.parents:
        candidate = parent / "reports" / "bench"
        if (candidate / "index.json").is_file():
            return candidate
    cwd_candidate = Path.cwd() / "reports" / "bench"
    if (cwd_candidate / "index.json").is_file():
        return cwd_candidate
    return primary


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


def robustness_score(outcomes):
    """0 to 100: the mean value of the outcomes of a set of damaged files (recovered 100, partial 60, clean_error 40,
    crash 0, hang 0). `outcomes` maps each outcome to how many files ended that way; None if there are none."""
    total = sum(outcomes.get(name, 0) for name in OUTCOMES)
    if not total:
        return None
    return round(sum(OUTCOME_SCORES[name] * outcomes.get(name, 0) for name in OUTCOMES) / total, 1)


def unit_fidelity(counts):
    """The share of judged units whose trace is the original's: same / (evaluated - not_run). A unit that is never
    executed by the case's trace cannot be judged, so it is left out of the denominator; one that does not compile stays in.
    None when no unit was judged."""
    judged = sum(counts.get(name, 0) for name in ("same", "different", "no_compile"))
    return counts.get("same", 0) / judged if judged else None


def component_shares(line):
    """Each term of the Índice Flash for a decompiler line, as a share from 0 to 1; None where the run did not measure it.

    An engine that was measured for per-unit fidelity but had no unit to judge (nothing it emitted could be compiled
    alone and run) scores 0 there: there is no source whose behaviour is known."""
    cases = line.get("cases")
    shares = dict.fromkeys(INDEX_COMPONENTS)
    if cases:
        shares.update(source=line["source"] / cases, recompiles=line["recompiles"] / cases, same_trace=line["same_trace"] / cases)
    shares["units"] = line.get("unit_coverage")
    fidelity = line.get("unit_fidelity")
    shares["unit_fidelity"] = fidelity if fidelity is not None else (0.0 if line.get("unit_fidelity_cases") else None)
    robustness = line.get("robustness")
    shares["robustness"] = None if robustness is None else robustness / 100
    return shares


def index_components(lines):
    """The terms of the Índice Flash of a run: the four base ones, plus per-unit fidelity and robustness when every
    engine that is ranked has them. Only engines measured on the same cases are ranked; with none, every measured engine counts."""
    measured = [line for line in lines if line.get("cases")]
    ranked = [line for line in measured if line.get("comparable")] or measured
    extras = [name for name in EXTRA_COMPONENTS if ranked and all(component_shares(line)[name] is not None for line in ranked)]
    return list(BASE_COMPONENTS) + extras


def flash_index(line, components=BASE_COMPONENTS):
    """Índice Flash of a decompiler line, 0 to 100: the mean of the shares in `components`, which every engine, JPEXS
    included, is judged on equally and which need no other decompiler to measure: the cases with complete source, the
    cases whose source recompiles, the cases where Ruffle prints the same trace as the original, the units of the SWF's own
    inventory (classes, scripts, actions) the engine emitted source for and, when the run measured them, the share of units
    whose trace stays the same when compiled alone and the robustness score on damaged files. None when the engine was not
    measured or a term is missing (a run from before schema 2 did not count the units)."""
    shares = component_shares(line)
    if not line.get("cases") or any(shares[name] is None for name in components):
        return None
    return round(100 * sum(shares[name] for name in components) / len(components), 1)


def upgrade(results):
    """Give a run written before schema 3 the fields the interface reads; what it never measured stays None."""
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
                for key in ("units_total", "units_with_source", "unit_coverage", "unit_fidelity", "robustness"):
                    line.setdefault(key, None)
                line.setdefault("flash_index", flash_index(line))
        if category == "decompilers":
            table.setdefault("index_components", list(BASE_COMPONENTS) if any(line.get("flash_index") is not None for line in table["engines"]) else [])
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
