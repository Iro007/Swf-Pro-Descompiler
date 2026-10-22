"""Install the pinned official pyflashkit wheel into an isolated dev venv.

Run using the project's existing .venv; no global package install or SWF upload.
The PyPI release digest is fixed below, not accepted from a mutable download.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.3.0"
COMMIT = "e50946b48d91d726aac2beec4e6252e3bd512f33"
WHEEL = "pyflashkit-1.3.0-py3-none-any.whl"
WHEEL_URL = "https://files.pythonhosted.org/packages/39/9e/1e66d54283ebffa871b5a2ffcc275638e39d7e3538901a0bcd549895f481/" + WHEEL
WHEEL_SHA256 = "a618f4055b8d9c6c0fa09b800d43840692581e0bc9414cca1ba569a4c0c94b6a"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def setup():
    if sys.prefix == sys.base_prefix:
        raise ValueError("Run this setup from the project's existing .venv, never global Python")
    folder = ROOT / ".dev-references/extra-tools" / f"pyflashkit-{VERSION}"
    folder.mkdir(parents=True, exist_ok=True)
    wheel = folder / WHEEL
    if not wheel.exists():
        with urllib.request.urlopen(WHEEL_URL, timeout=60) as response:
            wheel.write_bytes(response.read())
    if sha256(wheel) != WHEEL_SHA256:
        raise ValueError("Pinned official PyPI wheel SHA256 mismatch; preserve the file for review")
    venv = folder / ".venv"
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        subprocess.run([sys.executable,"-m","venv",str(venv)], check=True)
    subprocess.run([str(python),"-m","pip","install","--no-deps","--no-index",str(wheel)], check=True)
    version = subprocess.check_output([str(python),"-I","-c","from flashkit.cli import main; main()","--version"], text=True).strip()
    if version != f"flashkit {VERSION}":
        raise ValueError("Installed CLI version differs from the pinned release")
    runtime = json.loads(subprocess.check_output([str(python),"-I","-c",
        "import json,sys,sysconfig; print(json.dumps({'python':sys.version,'base_executable':sys._base_executable,'site_packages':sysconfig.get_paths()['purelib']}))"], text=True))
    packages = Path(runtime["site_packages"])
    metadata = packages / f"pyflashkit-{VERSION}.dist-info"
    files = [wheel, python, venv / "pyvenv.cfg"]
    files.extend(sorted((packages / "flashkit").rglob("*.py")))
    files.extend(sorted(path for path in metadata.rglob("*") if path.is_file()))
    manifest_path = ROOT / ".dev-references/extra-tools.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.is_file() else {"schema_version":1,"development_only":True,"tools":{}}
    if manifest.get("development_only") is not True:
        raise ValueError("Existing manifest must be marked development_only")
    manifest["tools"]["pyflashkit"] = {
        "version":VERSION,"commit":COMMIT,"license":"MIT",
        "website":"https://github.com/bitalizer/pyflashkit",
        "executable":str(python.resolve()),"python_version":runtime["python"],
        "runtime_base_executable":runtime["base_executable"],
        "runtime_base_executable_sha256":sha256(runtime["base_executable"]),
        "artifact_sha256":{str(path.resolve()):sha256(path) for path in files},
        "archive":{"url":WHEEL_URL,"sha256":WHEEL_SHA256},
        "hash_authority":"Official PyPI release digest and publishing attestation; installed package and isolated interpreter hashed locally",
        "capabilities":{"source":["as3"],"disassemble":["as3"]},
        "process_mode":"Isolated Python -I process starts on every operation; official CLI main for source, sequential official API for disassembly",
        "development_only":True,
    }
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return {"configured":"pyflashkit","version":VERSION,"python":runtime["python"],"config":str(manifest_path),"artifact_count":len(files),"operations":["source","disassemble"],"languages":["as3"],"release_packaged":False}


if __name__ == "__main__":
    print(json.dumps(setup(), indent=2))
