"""Register flashdec-py and flashdec-rs as hash-pinned benchmark adapters.

Run again after changing either tool: the harness refuses to run an adapter
whose recorded artifact hashes no longer match.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def setup():
    manifest_path = ROOT / ".dev-references/extra-tools.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.is_file() else {"schema_version": 1, "development_only": True, "tools": {}}
    if manifest.get("development_only") is not True:
        raise ValueError("Existing manifest must be marked development_only")
    configured = []

    module_root = (WORKSPACE / "flashdec_py").resolve()
    python = (ROOT / ".venv/Scripts/python.exe").resolve()
    if python.is_file() and (module_root / "flashdec/cli.py").is_file():
        files = [python, *sorted((module_root / "flashdec").glob("*.py")),
                 (WORKSPACE / "shared/scoring-rules.json").resolve()]
        version = (module_root / "flashdec/__init__.py").read_text(encoding="utf-8").split('"')[1]
        manifest["tools"]["flashdec_py"] = {
            "version": version, "license": "GPL-3.0",
            "executable": str(python), "module_root": str(module_root),
            "artifact_sha256": {str(path.resolve()): sha256(path) for path in files},
            "hash_authority": "Local source files and project interpreter hashed at registration",
            "capabilities": {"source": ["as2", "as3"]},
            "process_mode": "Python -I process per operation; runs PySWF in-process and JPEXS as a child process",
            "jpexs_timeout": 600, "development_only": True,
        }
        configured.append("flashdec_py")

    rust = (WORKSPACE / "rust_swf/target/x86_64-pc-windows-gnu/release/flashdec-rs.exe").resolve()
    if rust.is_file():
        manifest["tools"]["flashdec_rs"] = {
            "version": "1.0.0", "license": "GPL-3.0",
            "executable": str(rust),
            "artifact_sha256": {str(rust): sha256(rust)},
            "hash_authority": "Local release build hashed at registration",
            "capabilities": {"source": ["as2", "as3"]},
            "process_mode": "Native process per operation; runs RustSWF in-process and JPEXS as a child process",
            "jpexs_timeout": 600, "development_only": True,
        }
        configured.append("flashdec_rs")

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"configured": configured, "config": str(manifest_path)}


if __name__ == "__main__":
    result = setup()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["configured"] else 1)
