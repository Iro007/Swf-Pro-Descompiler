"""Inspect authorized remote ZIPs without extracting or executing their contents."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def inspect():
    probe=json.loads((ROOT / "reports/decompiler-com-probe.json").read_text(encoding="utf-8"))
    for row in probe["results"]:
        if not row.get("download"):
            continue
        archive=(ROOT / row["download"]["file"]).resolve()
        assert archive.is_relative_to(ROOT / ".dev-references/remote-results")
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==row["download"]["sha256"]
        records=[]
        with zipfile.ZipFile(archive) as handle:
            assert sum(e.file_size for e in handle.infolist())<20*1024**2
            for entry in handle.infolist():
                if entry.is_dir():
                    continue
                record={"file":entry.filename,"bytes":entry.file_size}
                data=handle.read(entry)
                record["sha256"]=hashlib.sha256(data).hexdigest()
                if entry.filename.endswith((".as",".log",".txt")):
                    text=data.decode("utf-8",errors="replace")
                    record["text_preview"]=text[:4000] if entry.filename.endswith((".log",".txt")) else text[:250]
                records.append(record)
        row["output_inspection"]={"files":records,"actionscript_files":sum(r["file"].endswith(".as") for r in records),
                                  "source_compilation":"not_run","unit_mapping":"not_verified","behavior":"not_run"}
    output=ROOT / "reports/decompiler-com-evidence.json"
    # Source and service paths are local evidence; the public dashboard gets
    # counts/status only, not the temporary artifact access URLs.
    output.write_text(json.dumps(probe,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps([{"case":r["file"],"latency_ms":r.get("elapsed_ms"),"source_files":r.get("output_inspection",{}).get("actionscript_files"),
                      "logs":[f for f in r.get("output_inspection",{}).get("files",[]) if f["file"].endswith((".log",".txt"))]} for r in probe["results"]],ensure_ascii=False,indent=2))
    return probe


if __name__=="__main__":
    inspect()
