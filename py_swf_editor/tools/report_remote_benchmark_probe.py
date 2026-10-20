"""Convert an observed, explicitly approved service probe to display-only evidence."""
import json
from datetime import datetime,timezone
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from py_swf.benchmark_engines import describe_engines
from py_swf.benchmark_report import render_dashboard
from py_swf.benchmarks import aggregate,quality
from py_swf.script_inventory import enumerate_scripts


def build():
    evidence=json.loads((ROOT / "reports/decompiler-com-evidence.json").read_text(encoding="utf-8"))
    assert evidence.get("user_authorized") and evidence.get("public_corpus_only")
    import hashlib
    report={"schema_version":1,"created_at":datetime.now(timezone.utc).isoformat(),"profile":"remote_probe","operation":"source",
            "measurement_context":{"kind":"remote_probe","suitable_for_comparison":False,"reason":"One browser observation per public SWF; includes network and service queue; not a local-engine speed benchmark"},
            "configuration":{"warmups":0,"hot_runs":0,"cold_runs":0,"seed":None,"public_corpus_only":True},
            "environment":{"measurement":"browser client; remote hardware and memory unknown"},
            "provenance":{"observations_sha256":hashlib.sha256((ROOT / "reports/decompiler-com-evidence.json").read_bytes()).hexdigest(),"engine_version":"not_published"},
            "engine_catalog":describe_engines(),"cases":[],"claims":{"parity":False,"superiority":False,"performance_winner":None}}
    for entry in evidence["results"]:
        path=ROOT / "third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata" / entry["file"]
        data=path.read_bytes()
        assert hashlib.sha256(data).hexdigest()==entry["sha256"]
        units=list(enumerate_scripts(data))
        observed=entry.get("output_inspection",{})
        engine={"engine":"decompiler_com","category":"remote_service","status":"output_unmapped" if observed else "failed",
                "cold":aggregate([],hot=False),"hot":aggregate([]),"cold_samples":[],"hot_samples":[],
                "quality":quality({"status":"output_unmapped","eligible_units":len(units),"units":[]}),
                "compilation_sample":[],"failures":[] if observed else [{"phase":"remote_probe","diagnostic":entry.get("download_error",entry.get("error"))}],
                "request_latency_ms":entry.get("elapsed_ms"),"artifacts":[{k:f[k] for k in ("file","bytes","sha256")} for f in observed.get("files",[])],
                "remote_observation":{"actionscript_files":observed.get("actionscript_files"),"source_compilation":"not_run","behavior":"not_run",
                                      "unit_mapping":"not_verified","remote_memory":"not_measured","remote_artifact_delete_requested":entry.get("delete_requested",False),
                                      "remote_artifact_delete_verified":entry.get("deletion_verified",False)},
                "diagnostic":"Source files observed, but canonical method mapping, compilation and behavior have not been verified. Engine implementation/version unknown."}
        report["cases"].append({"relative":entry["file"],"sha256":entry["sha256"],"bytes":len(data),"signature":data[:3].decode(),"engines":[engine],
                                "comparison":{"status":"remote_observation_only","quality_equivalent":False},"original_preserved":True})
    folder=ROOT / "reports/benchmark-remote-probe"
    folder.mkdir(exist_ok=True)
    report.update(scheduled_files=len(report["cases"]),completed_files=len(report["cases"]),uncompleted_files=[])
    (folder / "benchmark.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (folder / "benchmark.html").write_text(render_dashboard(report),encoding="utf-8")
    return {"report":str(folder),"files":len(report["cases"]),"engine_version":"unknown","semantic_parity":False}


if __name__=="__main__":
    print(json.dumps(build(),indent=2))
