"""Read-only diagnostic for the pinned JPEXS checkout and local toolchain."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
JPEXS_DIR = ROOT / "third_party" / "jpexs-decompiler"
DEFAULT_COMMIT = "792d2c2fc886ddac030c601d286cf179b11b59c4"


def _run(*args: str, cwd: Path | None = None) -> str | None:
    try:
        return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def inspect() -> dict:
    result = {
        "checkout": str(JPEXS_DIR),
        "present": JPEXS_DIR.is_dir(),
        "git_checkout": (JPEXS_DIR / ".git").exists(),
        "git_commit": None,
        "expected_commit": os.environ.get("PY_SWF_JPEXS_COMMIT", DEFAULT_COMMIT),
        "java": shutil.which("java"),
        "ant": shutil.which("ant") or shutil.which("ant.bat"),
        "ffdec": os.environ.get("PY_SWF_FFDEC"),
        "jar": os.environ.get("PY_SWF_FFDEC_JAR"),
        "ready": False,
        "actions": [],
    }
    if result["present"] and result["git_checkout"]:
        result["git_commit"] = _run("git", "rev-parse", "HEAD", cwd=JPEXS_DIR)
        if not result["git_commit"]:
            result["actions"].append("El checkout no parece ser un repositorio Git válido.")
    elif result["present"]:
        result["actions"].append(
            "El directorio existe pero no es un submódulo/checkout Git independiente; ejecuta tools/setup_jpexs.ps1."
        )
    else:
        result["actions"].append(
            "Clona jindrapetrik/jpexs-decompiler en third_party/jpexs-decompiler y fija PY_SWF_JPEXS_COMMIT."
        )
    if not result["java"]:
        result["actions"].append("Instala una JRE/JDK compatible y deja java en PATH.")
    if not result["ant"]:
        result["actions"].append("Instala Apache Ant para compilar FFDec desde las fuentes.")
    if not result["ffdec"] and not result["jar"]:
        result["actions"].append("Configura PY_SWF_FFDEC o PY_SWF_FFDEC_JAR para activar el motor JPEXS.")
    result["ready"] = bool(result["java"] and (result["ffdec"] or result["jar"]))
    if result["expected_commit"] and result["git_checkout"] and result["git_commit"] != result["expected_commit"]:
        result["actions"].append("El commit local no coincide con PY_SWF_JPEXS_COMMIT.")
        result["ready"] = False
    return result


def main() -> int:
    print(json.dumps(inspect(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
