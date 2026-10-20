"""Check the frozen archive includes the dashboard but no development Java tools."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(path):
    from PyInstaller.archive.readers import CArchiveReader
    path = Path(path).resolve()
    archive = CArchiveReader(str(path))
    names = {name.replace("\\", "/"): name for name in archive.toc}
    forbidden = [name for name in names if name.lower().endswith((".jar", "/java.exe", "/javac.exe", "/jvm.dll")) or ".dev-references" in name.lower()]
    assert not forbidden, forbidden
    assert "py_swf/benchmark_dashboard.html" in names
    assert "py_swf/benchmark_overview.html" in names
    assert "benchmark_data/benchmark.json" in names
    assert "benchmark_data/disassembly/benchmark.json" in names
    assert "benchmark_data/remote/benchmark.json" in names
    assert "benchmark_data/audit/benchmark.json" in names
    assert "benchmark_data/tool-catalog.json" in names
    snapshot = json.loads(archive.extract(names["benchmark_data/benchmark.json"]))
    assert snapshot["claims"]["parity"] is False
    assert all("file" not in case for case in snapshot["cases"])
    audit = json.loads(archive.extract(names["benchmark_data/audit/benchmark.json"]))
    assert audit["profile"]=="source_audit" and len(audit["cases"])==96
    assert not audit["claims"]["parity"] and audit["audit_summary"]["behavior_verified"] is False
    assert all("file" not in case for case in audit["cases"])
    tools = {tool["id"]:tool for tool in json.loads(archive.extract(names["benchmark_data/tool-catalog.json"]))["tools"]}
    assert len(tools)==13
    for engine,version in (("sothink","7.4"),("trillix","5.3.1400"),("as3_sorcerer","6.78")):
        assert tools[engine]["local_availability"]=="installed_pending_verification"
        assert tools[engine]["installer_version"]==version and not tools[engine]["configured"]
    assert tools["asv"]["local_availability"]=="blocked_by_security"
    assert "La descarga no se completó" in tools["asv"]["setup_diagnostic"]
    return {"passed":True,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "java_artifacts":forbidden,"dashboard_embedded":True,"snapshot_files":len(snapshot["cases"]),
            "embedded_operations":["source","disassemble","remote_probe"],
            "audit_files":len(audit["cases"]),"tools":len(tools),"installers_not_bundled":True,
            "bytes":path.stat().st_size}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("exe",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    result=verify(args.exe)
    if args.output:
        args.output.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
