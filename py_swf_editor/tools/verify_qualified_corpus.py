"""Verify qualified-name reconstruction separately from fixed historical coverage.

The fixed historical cohort comes from native-source-coverage.json (423 rows).
The optional QName slice is always named and reported as a sample of the 2,360
QName failures; it is never treated as that historical cohort. No input report
or SWF is modified. Reconstruction success does not establish semantic parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from py_swf.decompile.avm2_dec import decompile_method
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.avm2 import ABCFile
from py_swf.script_inventory import enumerate_scripts
from py_swf.swf_parser import SWFFile
from tools.native_source_coverage import survey


DEFAULT_QNAME_REPORT = ROOT / "reports/corpus-source-audit-fixed/benchmark.json"
DEFAULT_HISTORICAL_BASELINES = [
    ROOT / "reports/native-source-coverage.json",
    ROOT / "reports/native-source-coverage-postdom.json",
    ROOT / "reports/native-private-corpus.json",
]
QNAME_DIAGNOSTIC = "Unrepresentable qualified name"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def qname_candidates(report: dict) -> list[tuple[dict, dict]]:
    result = []
    for case in report.get("cases", []):
        for engine in case.get("engines", []):
            if engine.get("engine") != "native":
                continue
            for unit in engine.get("units", []):
                if unit.get("language") == "as3" and QNAME_DIAGNOSTIC in (unit.get("diagnostic") or ""):
                    result.append((case, unit))
    return result


def resolve_corpus_file(recorded: str | Path) -> Path:
    path = Path(recorded)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as error:
        raise ValueError(f"Historical corpus path escapes repository: {recorded}") from error
    if not path.is_file():
        raise FileNotFoundError(f"Historical corpus file is missing: {recorded}")
    return path


def validate_historical_baselines(paths: list[Path]) -> tuple[dict, dict[str, dict]]:
    """Prove fixed membership by path, SWF hash, and tag/body identity."""
    snapshots = []
    status_by_snapshot = {}
    canonical_members = None
    canonical_location = {}
    for report_path in paths:
        report_bytes = report_path.read_bytes()
        report = json.loads(report_bytes)
        if "files" not in report or "summary" not in report:
            raise ValueError(f"Not a native source coverage report: {report_path}")
        members = {}
        locations = {}
        file_summaries = []
        for entry in report["files"]:
            path = resolve_corpus_file(entry["file"])
            actual_hash = sha256(path.read_bytes())
            if actual_hash != entry["sha256"]:
                raise ValueError(f"Historical SWF hash mismatch: {entry['file']}")
            rel = path.relative_to(ROOT).as_posix()
            file_summaries.append({"file": rel, "sha256": actual_hash, "cases": len(entry["cases"])})
            for case in entry["cases"]:
                key = (rel, case.get("tag"), case.get("body"))
                if key in members:
                    raise ValueError(f"Duplicate historical case identity: {key}")
                members[key] = bool(case["structured"])
                locations[key] = {"file": rel, "sha256": actual_hash, "tag": case.get("tag"),
                                  "body": case.get("body"), "method": case.get("method")}
        calculated = {"total": len(members), "structured": sum(members.values()),
                      "pcode": sum(not value for value in members.values())}
        if calculated != report["summary"]:
            raise ValueError(f"Historical summary does not match rows in {report_path}: {calculated}")
        if canonical_members is None:
            canonical_members, canonical_location = members, locations
        elif members.keys() != canonical_members.keys():
            raise ValueError(f"Historical cohort membership differs in {report_path}")
        snapshot_name = report_path.relative_to(ROOT).as_posix()
        status_by_snapshot[snapshot_name] = members
        snapshots.append({"report": snapshot_name,
                          "report_sha256": sha256(report_bytes), "summary": calculated,
                          "file_count": len(file_summaries), "files": file_summaries})
    if canonical_members is None:
        raise ValueError("No historical baseline reports supplied")
    membership = []
    for key, location in canonical_location.items():
        membership.append({**location,
                           "structured_in_baselines": {name: statuses[key]
                                                       for name, statuses in status_by_snapshot.items()}})
    return {"denominator": len(canonical_members),
            "identity": "(repository-relative SWF path, SWF SHA-256, tag index, body index)",
            "snapshots": snapshots, "members": membership}, canonical_location


def compare_snapshot_to_current(snapshot_path: Path, historical_location: dict,
                                current: dict) -> dict:
    old = json.loads(snapshot_path.read_text(encoding="utf-8"))
    old_rows = {}
    for entry in old["files"]:
        path = resolve_corpus_file(entry["file"])
        rel = path.relative_to(ROOT).as_posix()
        for case in entry["cases"]:
            old_rows[(rel, case.get("tag"), case.get("body"))] = bool(case["structured"])
    transitions = {"gained": [], "regressions": [], "unchanged_pcode": 0, "still_structured": 0}
    for key, was_structured in old_rows.items():
        now = bool(current[key]["structured"])
        row = historical_location[key]
        if not was_structured and now:
            transitions["gained"].append(row)
        elif was_structured and not now:
            transitions["regressions"].append({**row, "diagnostic_now": current[key].get("diagnostic")})
        elif not was_structured:
            transitions["unchanged_pcode"] += 1
        else:
            transitions["still_structured"] += 1
    return {"baseline": snapshot_path.relative_to(ROOT).as_posix(),
            "baseline_sha256": sha256(snapshot_path.read_bytes()), "baseline_summary": old["summary"],
            "current_summary": {"total": len(current), "structured": sum(bool(c["structured"]) for c in current.values()),
                                "pcode": sum(not bool(c["structured"]) for c in current.values())},
            "transitions": transitions}


def run_gained_sources(gains: list[dict]) -> list[dict]:
    """Regenerate each gained historical case from its hash-verified real SWF."""
    evidence = []
    movies = {}
    abcs = {}
    for row in gains:
        relative = row["file"]
        path = resolve_corpus_file(relative)
        data = path.read_bytes()
        if sha256(data) != row["sha256"]:
            raise ValueError(f"Gain source SWF changed: {relative}")
        if relative not in movies:
            movie = SWFFile()
            movie.read_bytes(data)
            movies[relative] = movie
        movie = movies[relative]
        tag = movie.tags[row["tag"]]
        if row["body"] is None:
            source, error = decompile_avm1(tag.data)
        else:
            if relative not in abcs:
                abcs[relative] = {}
            if row["tag"] not in abcs[relative]:
                abc = ABCFile()
                abc.parse(tag.parse_doabc()[2])
                abcs[relative][row["tag"]] = abc
            source, error = decompile_method(abcs[relative][row["tag"]],
                                             abcs[relative][row["tag"]].method_bodies[row["body"]])
        if error:
            raise ValueError(f"Previously gained source no longer reconstructs: {relative} tag={row['tag']} body={row['body']}: {error}")
        evidence.append({**row, "source_sha256": sha256(source.encode("utf-8")),
                         "source_characters": len(source), "source_generation": "rerun_passed"})
    return evidence


def run_qname_sample(report_path: Path, sample_size: int) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    all_rows = qname_candidates(report)
    if sample_size < 1 or sample_size > len(all_rows):
        raise ValueError(f"QName sample size must be 1..{len(all_rows)}")
    selected = all_rows[:sample_size]  # deterministic report order, explicitly a sample
    files: dict[str, tuple[dict, dict]] = {}
    for case, unit in selected:
        files.setdefault(case["relative"], (case, {}))[1][unit["document_id"]] = unit
    outcomes = {"reconstructed": 0, "same_failure": 0, "different_failure": 0, "missing_unit": 0}
    details = []
    for relative, (case, expected) in files.items():
        path = resolve_corpus_file(case["file"])
        data = path.read_bytes()
        if sha256(data) != case["sha256"]:
            raise ValueError(f"QName corpus hash differs from source-audit report: {relative}")
        live = {unit.document_id: unit for unit in enumerate_scripts(data)}
        for document_id, before in expected.items():
            unit = live.get(document_id)
            if unit is None or unit.language != "as3" or unit.body is None:
                outcome, error = "missing_unit", None
            else:
                _, error = decompile_method(unit.abc, unit.body)
                outcome = ("reconstructed" if error is None else
                           "same_failure" if error == before["diagnostic"] else "different_failure")
            outcomes[outcome] += 1
            details.append({"file": relative, "swf_sha256": case["sha256"], "document_id": document_id,
                            "method": before.get("name"), "result": outcome,
                            "baseline_diagnostic": before.get("diagnostic"), "current_diagnostic": error})
    return {"definition": "Deterministic first N matching records in source-audit report order; sample only, not historical cohort",
            "qname_group_total": len(all_rows), "qname_sample_size": sample_size,
            "qname_sample_files": len(files), "outcomes": outcomes, "details": details,
            "verification_scope": "Source reconstruction result only; syntax, compile, and semantic equivalence not established"}


def verify(qname_report: Path, historical_baselines: list[Path], qname_sample_size: int) -> dict:
    historical, location = validate_historical_baselines(historical_baselines)
    # Re-run source generation on each file; retain one current result map for
    # independent transition comparisons against both historical snapshots.
    by_file = {row["file"]: row for row in location.values()}
    current_cases = {}
    current_files = []
    for relative, row in by_file.items():
        path = resolve_corpus_file(relative)
        data = path.read_bytes()
        if sha256(data) != row["sha256"]:
            raise ValueError(f"Historical SWF hash changed: {relative}")
        fresh = survey(path)
        current_files.append({"file": relative, "sha256": sha256(data), "cases": len(fresh["cases"])})
        for case in fresh["cases"]:
            current_cases[(relative, case.get("tag"), case.get("body"))] = case
    if current_cases.keys() != location.keys():
        raise ValueError("Current source run changed historical cohort membership or denominator")
    current_summary = {"total": len(current_cases), "structured": sum(bool(c["structured"]) for c in current_cases.values()),
                       "pcode": sum(not bool(c["structured"]) for c in current_cases.values())}
    historical["current"] = {"summary": current_summary, "files": current_files,
                              "source_generation_rerun": True, "semantic_parity": False,
                              "runtime_verification": "not_run"}
    historical["comparisons"] = [compare_snapshot_to_current(path, location, current_cases)
                                  for path in historical_baselines]
    for comparison in historical["comparisons"]:
        comparison["gained_source_outputs"] = run_gained_sources(comparison["transitions"]["gained"])
    historical["denominator_preserved"] = current_summary["total"] == historical["denominator"]
    historical["no_regressions_vs_any_baseline"] = all(not c["transitions"]["regressions"]
                                                        for c in historical["comparisons"])
    return {"schema_version": 1, "verification_kind": "qualified_name_and_historical_source_coverage",
            "historical_fixed_cohort": historical,
            "qualified_name_sample": run_qname_sample(qname_report, qname_sample_size),
            "inputs_read_only": True,
            "equivalence": {"semantic_parity": False, "runtime_verification": "not_run",
                            "note": "Source reconstruction/generation is measured separately; no behavior equivalence claim."}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qname-report", type=Path, default=DEFAULT_QNAME_REPORT)
    parser.add_argument("--historical-baselines", nargs="+", type=Path,
                        default=DEFAULT_HISTORICAL_BASELINES)
    parser.add_argument("--qname-sample-size", type=int, default=423,
                        help="Sample size from the QName group; never the fixed historical cohort")
    parser.add_argument("--output", type=Path,
                        help="Optional new JSON report path; existing reports are never overwritten by default")
    args = parser.parse_args(argv)
    result = verify(args.qname_report, args.historical_baselines, args.qname_sample_size)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output.resolve()
        if output.exists():
            parser.error(f"Refusing to overwrite existing report: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
        print(json.dumps({"output": str(output),
                          "historical": result["historical_fixed_cohort"]["current"]["summary"],
                          "comparisons": [{"baseline": c["baseline"],
                                           "baseline_summary": c["baseline_summary"],
                                           "gained": len(c["transitions"]["gained"]),
                                           "regressions": c["transitions"]["regressions"],
                                           "unchanged_pcode": c["transitions"]["unchanged_pcode"],
                                           "gained_source_reruns": len(c["gained_source_outputs"])}
                                          for c in result["historical_fixed_cohort"]["comparisons"]],
                          "qname_group_total": result["qualified_name_sample"]["qname_group_total"],
                          "qname_sample_size": result["qualified_name_sample"]["qname_sample_size"],
                          "qname_sample_outcomes": result["qualified_name_sample"]["outcomes"]},
                         ensure_ascii=False, indent=2))
    else:
        print(encoded)
    cohort = result["historical_fixed_cohort"]
    if not cohort["denominator_preserved"] or not cohort["no_regressions_vs_any_baseline"]:
        return 2
    if result["qualified_name_sample"]["outcomes"]["missing_unit"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
