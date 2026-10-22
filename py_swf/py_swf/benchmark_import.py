"""Import local GUI exports as unverified observations, never timed benchmarks."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from .benchmark_engines import describe_engines
from .benchmarks import aggregate,quality,render_html
from .script_inventory import enumerate_scripts

GUI_ENGINES={"sothink","trillix","as3_sorcerer"}
MAX_EXPORT_BYTES=16*1024**2
PUBLIC_CORPUS_ROOT=Path(__file__).resolve().parents[1] / "third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata"
PUBLIC_REVISION="b2ef2d3d3f44291a4bdfa899c986bc39a89fa6aa"


def import_exports(engine,input_file,exports,output,manifest,*,edition="unknown"):
    if engine not in GUI_ENGINES:
        raise ValueError("Only installed GUI decompiler exports can be imported")
    if edition not in ("unknown","trial","licensed"):
        raise ValueError("Unknown edition declaration")
    input_file,exports,output,manifest=map(Path,(input_file,exports,output,manifest))
    data=input_file.read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    approved=json.loads(manifest.read_text(encoding="utf-8"))
    if approved.get("source_revision")!=PUBLIC_REVISION:
        raise ValueError("Manifest does not identify the approved public revision")
    case=next((case for case in approved["cases"] if case.get("public_corpus") is True and case["sha256"]==digest),None)
    if case is None:
        raise ValueError("Input is not in the approved public corpus manifest")
    source=(PUBLIC_CORPUS_ROOT / case.get("source","")).resolve(strict=True)
    if not source.is_relative_to(PUBLIC_CORPUS_ROOT.resolve(strict=True)) or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=digest:
        raise ValueError("Input does not match the pinned public corpus source")
    if not exports.is_dir():
        raise ValueError("Export folder does not exist")
    if (output / "benchmark.json").exists() or (output / "benchmark.html").exists():
        raise FileExistsError("Existing observation will not be overwritten")
    root=exports.resolve(strict=True)
    artifacts=[]
    for path in sorted(exports.rglob("*.as")):
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Export file escapes the selected folder")
        if not path.is_file() or path.stat().st_size>MAX_EXPORT_BYTES:
            raise ValueError("Source export exceeds the inspection limit")
        raw=path.read_bytes()
        artifacts.append({"relative":path.relative_to(exports).as_posix(),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"compilation":"not_run","behavior":"not_run"})
    q=quality({"status":"output_unmapped","eligible_units":len(list(enumerate_scripts(data))),"units":[]})
    result={"engine":engine,"operation":"source","status":"output_unmapped" if artifacts else "not_applicable",
            "quality":q,"units":[],"documents":[],"cold":aggregate([],hot=False),"hot":aggregate([]),
            "cold_samples":[],"hot_samples":[],"compilation_sample":[],"failures":[],
            "manual_export_observation":{"actionscript_files":len(artifacts),"artifacts":artifacts,"edition_user_declared":edition,
                 "producer":"user_asserted; execution was not observed","execution_observed":False,"latency_measured":False},
            "diagnostic":"Local files inspected; producer identity, unit correspondence, compilation and behavior not independently verified" if artifacts else "No .as exports provided; not counted as a decompiler failure. Trial restrictions may apply"}
    report={"schema_version":1,"created_at":datetime.now(timezone.utc).isoformat(),"profile":"manual_export","operation":"source",
            "measurement_context":{"kind":"manual_export","suitable_for_comparison":False,"reason":"Local user-supplied GUI exports; no observed execution, timings or behavior"},
            "configuration":{"warmups":0,"hot_runs":0,"cold_runs":0},"provenance":{"public_manifest_sha256":hashlib.sha256(manifest.read_bytes()).hexdigest(),"source_revision":approved.get("source_revision")},
            "engine_catalog":describe_engines(),"cases":[{"relative":case.get("source",case["file"]),"sha256":digest,"bytes":len(data),"engines":[result],"original_preserved":input_file.read_bytes()==data}],
            "scheduled_files":1,"completed_files":1,"claims":{"parity":False,"superiority":False,"performance_winner":None}}
    output.mkdir(parents=True,exist_ok=True)
    (output / "benchmark.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (output / "benchmark.html").write_text(render_html(report),encoding="utf-8")
    return report
