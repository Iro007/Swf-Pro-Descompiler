"""Pin official RABCDAsm Windows tools locally. Never install system tools."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.bootstrap_references import verified_download, sha256

RABCDASM = {
    "url":"https://github.com/CyberShadow/RABCDAsm/releases/download/1.18/RABCDAsm_v1.18.7z",
    "sha256":"68f2a39b45bafab874bacae7ec7b809e27eb6a14a2ec45c52c4305165d2308bf",
    "commit":"68f6fa241cda4f67550978dc024b184397d91156",
}
SEVENZIP = {
    "url":"https://github.com/ip7z/7zip/releases/download/26.04/7zr.exe",
    "sha256":"256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282",
}


def setup():
    import py7zr
    archive=verified_download(RABCDASM,ROOT / ".dev-references/downloads/rabcdasm-1.18.7z")
    extractor=verified_download(SEVENZIP,ROOT / ".dev-references/downloads/7zr-26.04.exe")
    destination=(ROOT / ".dev-references/extra-tools/rabcdasm-1.18-complete").resolve()
    destination.mkdir(parents=True,exist_ok=True)
    marker=destination / ".archive-sha256"
    if not marker.exists():
        if any(destination.iterdir()):
            raise ValueError("Preserve existing tool directory; refusing to overwrite it")
        with py7zr.SevenZipFile(archive) as handle:
            for entry in handle.files:
                name=entry.filename.replace("\\","/")
                if ":" in name or not (destination / name).resolve().is_relative_to(destination) or getattr(entry,"is_symlink",False) or getattr(entry,"is_junction",False):
                    raise ValueError("Unsafe reference archive member")
        # py7zr validates the member list but cannot decode BCJ2. The official
        # standalone 7zr helper supports it; no global 7-Zip installation.
        subprocess.run([str(extractor),"x",str(archive),"-o"+str(destination),"-y"],check=True,
                       stdout=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        marker.write_text(RABCDASM["sha256"],encoding="ascii")
    elif marker.read_text().strip()!=RABCDASM["sha256"]:
        raise ValueError("Existing tool directory belongs to another archive")
    executable=next(destination.rglob("rabcdasm.exe"))
    assembler=next(destination.rglob("rabcasm.exe"))
    config_path=ROOT / ".dev-references/extra-tools.json"
    config=json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {"schema_version":1,"development_only":True,"tools":{}}
    config["tools"]["rabcdasm"]={"version":"1.18","commit":RABCDASM["commit"],"executable":str(executable),"assembler":str(assembler),
         "artifact_sha256":{str(p):sha256(p) for p in destination.rglob("*") if p.is_file()},
         "archive":RABCDASM,"hash_authority":"Recorded from official HTTPS release; publisher provides no independent digest"}
    config["extraction_tool"]={**SEVENZIP,"version":"26.04","license":"LGPL-2.1-or-later; dev-only","digest_authority":"Official GitHub release asset SHA256"}
    config_path.write_text(json.dumps(config,indent=2),encoding="utf-8")
    return {"configured":"rabcdasm","version":"1.18","executables_sha256":{p.name:sha256(p) for p in (executable,assembler)},
            "flasm":"Not downloaded: expired upstream TLS certificate","flare":"Not downloaded: expired upstream TLS certificate; restricted freeware is not bundled"}


if __name__=="__main__":
    print(json.dumps(setup(),indent=2))
