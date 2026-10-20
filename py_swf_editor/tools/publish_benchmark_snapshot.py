"""Create a small, read-only distribution snapshot from completed measurements."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from py_swf.benchmark_report import public_report, render_dashboard


def publish(source, destination, *, include_context=False):
    source, destination = Path(source), Path(destination)
    raw = source.read_bytes()
    report = json.loads(raw)
    if report.get("measurement_context", {}).get("suitable_for_comparison") is False and not include_context:
        raise ValueError("Exploratory measurements must not be the release snapshot")
    if report.get("provenance", {}).get("implementation_unchanged") is not True and not (include_context and report.get("measurement_context",{}).get("kind")=="remote_probe"):
        raise ValueError("The release snapshot needs a single implementation revision")
    if report.get("completed_files") != report.get("scheduled_files") or report.get("cancelled"):
        raise ValueError("The release snapshot must retain a complete selected campaign")
    snapshot = public_report(report)
    snapshot["snapshot"] = {"full_report_sha256": hashlib.sha256(raw).hexdigest(),
                            "scope": "Display summary; full local report contains units and raw samples"}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "benchmark.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "benchmark.html").write_text(render_dashboard(snapshot), encoding="utf-8")
    return {"snapshot": str(destination.resolve()), "files": len(snapshot["cases"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/benchmark-release")
    parser.add_argument("--include-context",action="store_true",help="Include explicitly labeled observational evidence, not a speed comparison")
    args = parser.parse_args()
    print(json.dumps(publish(args.source, args.output,include_context=args.include_context), indent=2))
