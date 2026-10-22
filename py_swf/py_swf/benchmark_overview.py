"""A single comparison surface, retaining distinct cohorts and operations."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .benchmark_report import public_report

CHANNELS = ("source", "disassemble", "remote")


def channel(report):
    if report.get("measurement_context", {}).get("kind") == "remote_probe":
        return "remote"
    return report.get("operation", "source")


def build_overview(reports):
    campaigns, catalog, warnings = [], {}, []
    seen = set()
    for report_id, original in reports:
        report = public_report(original)
        kind = channel(report)
        if kind not in CHANNELS:
            continue
        # Release copies and their original report have identical observations.
        key = (kind, report.get("created_at"), report.get("profile"),
               tuple((c.get("sha256"), tuple(e.get("engine") for e in c.get("engines", []))) for c in report["cases"]))
        if key in seen:
            continue
        seen.add(key)
        if report.get("provenance", {}).get("implementation_unchanged") is False:
            warnings.append({"report_id": report_id, "reason": "Implementation changed during this campaign"})
        for engine in report.get("engine_catalog", []):
            catalog[engine["id"]] = engine
        campaigns.append({"id": report_id, "channel": kind, "report": report})
    campaigns.sort(key=lambda item: item["report"].get("created_at", ""), reverse=True)
    # Prefer current complete, non-mixed campaigns. Never combine measurements
    # from different versions, corpora or profiles into a fabricated median.
    selected = {}
    for kind in CHANNELS:
        eligible = [c for c in campaigns if c["channel"] == kind and (kind=="remote" or c["report"].get("provenance", {}).get("implementation_unchanged") is True)
                    and not c["report"].get("cancelled") and not c["report"].get("uncompleted_files")
                    and (kind == "remote" or c["report"].get("measurement_context", {}).get("kind") == "source_audit" or c["report"].get("measurement_context", {}).get("suitable_for_comparison") is not False)]
        if eligible:
            selected[kind] = eligible[0]["id"]
    # A packaged capability/inventory snapshot is read-only. Never inspect or
    # launch installations while serving a dashboard request.
    root=Path(__file__).resolve().parents[1]
    snapshot=root / "benchmark_data/tool-catalog.json"
    if not snapshot.is_file():
        snapshot=root / "reports/tool-catalog.json"
    if snapshot.is_file():
        try:
            for engine in json.loads(snapshot.read_text(encoding="utf-8"))["tools"]:
                catalog[engine["id"]]=engine
        except (OSError,ValueError,KeyError):
            pass
    if not catalog:
        from .benchmark_engines import ENGINES
        from dataclasses import asdict
        catalog = {k: {**asdict(v), "configured": False} for k, v in ENGINES.items()}
    return {"schema_version": 1, "kind": "comparison_overview", "created_at": datetime.now(timezone.utc).isoformat(),
            "campaigns": campaigns, "selected": selected, "engine_catalog": list(catalog.values()), "warnings": warnings,
            "claims": {"parity": False, "superiority": False, "performance_winner": None}}


def render_overview(overview):
    embedded = json.dumps(overview, ensure_ascii=False).replace("<", "\\u003c")
    page = Path(__file__).with_name("benchmark_overview.html").read_text(encoding="utf-8")
    original = Path(__file__).with_name("benchmark_dashboard.html").read_text(encoding="utf-8")
    css = original.split("<style>", 1)[1].split("</style>", 1)[0]
    return page.replace("__OVERVIEW_CSS__", css).replace("__OVERVIEW_JSON__", embedded)


def report_fingerprint(report):
    return hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
