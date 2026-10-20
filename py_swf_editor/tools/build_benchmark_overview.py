"""Export one offline comparison page and a read-only tool inventory snapshot."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from py_swf.benchmark_engines import describe_engines
from py_swf.benchmark_overview import render_overview
from server.routers.benchmarks import _overview


def build():
    (ROOT / "reports/tool-catalog.json").write_text(json.dumps({"schema_version":1,"tools":describe_engines()},ensure_ascii=False,indent=2),encoding="utf-8")
    report=_overview()
    folder=ROOT / "reports/benchmark-comparison"
    folder.mkdir(exist_ok=True)
    (folder / "comparison.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (folder / "comparison.html").write_text(render_overview(report),encoding="utf-8")
    return {"folder":str(folder),"tools":len(report["engine_catalog"]),"campaigns":len(report["campaigns"]),"selected":report["selected"]}


if __name__=="__main__":
    print(json.dumps(build(),indent=2))
