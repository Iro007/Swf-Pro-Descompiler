"""Generate reproducible parse/decompile metrics for a directory of SWFs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import tracemalloc
from typing import Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from py_swf.engines import NativePythonEngine, compare_engines
from py_swf.swf_parser import SWFFile


def _paths(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
    else:
        yield from sorted(root.rglob("*.swf"))


def report_file(path: Path, compare: bool = False, memory: bool = False) -> dict:
    result = {"file": str(path), "valid": False, "errors": [], "warnings": []}
    start = time.perf_counter()
    if memory:
        tracemalloc.start()
    try:
        data = path.read_bytes()
        swf = SWFFile()
        swf.read_bytes(data)
        result.update({
            "valid": True,
            "bytes": len(data),
            "signature": swf.signature,
            "version": swf.version,
            "frames": swf.frame_count,
            "frame_rate": swf.frame_rate,
            "tags": len(swf.tags),
            "scripts": [],
        })
        engine = NativePythonEngine()
        for index, tag in enumerate(swf.tags):
            if tag.tag_type not in {12, 59, 82}:
                continue
            script = {"tag": index, "type": tag.name}
            try:
                decompiled = engine.decompile(data, index, path.name)
                script["engine"] = decompiled.get("engine", "native")
                script["sections"] = len(decompiled.get("sections", []))
                script["errors"] = [s.get("error") for s in decompiled.get("sections", []) if s.get("error")]
                if compare:
                    diff = compare_engines(data, index, path.name)
                    script["matching_line_ratio"] = diff.get("metrics", {}).get("matching_line_ratio")
                    script["jpexs_ok"] = diff.get("jpexs", {}).get("ok", False)
            except Exception as exc:
                script["errors"] = [str(exc)]
            result["scripts"].append(script)
    except Exception as exc:
        result["errors"].append(str(exc))
    finally:
        if memory:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            result["peak_memory_bytes"] = peak
        result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def summarize(files: list[dict]) -> dict:
    """Aggregate per-script metrics into a parity baseline."""
    scripts = [s for f in files for s in f.get("scripts", [])]
    failed = [s for s in scripts if s.get("errors")]
    ratios = [s["matching_line_ratio"] for s in scripts
              if isinstance(s.get("matching_line_ratio"), (int, float))]
    total = len(scripts)
    return {
        "scripts_total": total,
        "scripts_with_errors": len(failed),
        "decompile_success_rate": round(1 - len(failed) / total, 4) if total else None,
        "jpexs_compared": len(ratios),
        "mean_matching_line_ratio": round(sum(ratios) / len(ratios), 4) if ratios else None,
    }


def build_report(root: Path, compare: bool = False, limit: int | None = None, memory: bool = False) -> dict:
    paths = list(_paths(root))
    if limit is not None:
        paths = paths[:max(0, limit)]
    files = [report_file(path, compare=compare, memory=memory) for path in paths]
    return {
        "schema_version": 1,
        "corpus": str(root),
        "file_count": len(files),
        "valid_count": sum(1 for item in files if item["valid"]),
        "summary": summarize(files),
        "files": files,
    }



def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure PySWF parsing/decompilation over a SWF corpus")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", action="store_true", help="also run the optional JPEXS engine")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--memory", action="store_true", help="track peak memory (slow)")
    args = parser.parse_args(argv)
    payload = build_report(args.corpus, compare=args.compare, limit=args.limit, memory=args.memory)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(json.dumps(payload["summary"], indent=2))
    else:
        print(encoded)
    return 0 if payload["valid_count"] == payload["file_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
