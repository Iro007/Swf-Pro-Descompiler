"""Read what tools/bench.py wrote: reports/bench/index.json and one results.json per run."""
import json
import os
from pathlib import Path

from .suite_tools import ROOT


def bench_directory():
    return Path(os.environ.get("FLASH_BENCH_DIR") or ROOT / "reports" / "bench")


def read_bench(run=None):
    folder = bench_directory()
    index_path = folder / "index.json"
    runs = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else []
    if not runs:
        return {"runs": [], "results": None, "folder": str(folder)}
    wanted = run or runs[-1]["run_id"]
    if wanted not in {entry["run_id"] for entry in runs}:
        raise ValueError("Unknown benchmark run")
    results = json.loads((folder / wanted / "results.json").read_text(encoding="utf-8"))
    return {"runs": runs[::-1], "results": results, "folder": str(folder)}
