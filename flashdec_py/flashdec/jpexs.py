"""JPEXS Free Flash Decompiler (ffdec) as an out-of-process engine."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .workspace import find_workspace

SCRIPT_FORMATS = "script:as"
RESOURCE_FORMATS = "shape:svg,morphshape:svg,image:png_gif_jpeg,text:plain,sound:mp3_wav,font:ttf"
RESOURCE_TYPES = "image,shape,morphshape,sound,font,binaryData,text,symbolClass"


@dataclass
class JpexsTool:
    java: str
    jar: str

    def describe(self) -> str:
        return f"{self.jar} (java: {self.java})"


@dataclass
class JpexsResult:
    scripts: dict[str, str] = field(default_factory=dict)
    resource_dir: Path | None = None
    seconds: float = 0.0
    error: str | None = None
    log_tail: str = ""


def _java_candidates(root: Path | None):
    if os.environ.get("FLASHDEC_JAVA"):
        yield os.environ["FLASHDEC_JAVA"]
    exe = "java.exe" if os.name == "nt" else "java"
    if root is not None:
        yield from sorted(glob.glob(str(root / "py_swf_editor/.dev-references/jdk/*/bin" / exe)), reverse=True)
    if os.environ.get("JAVA_HOME"):
        yield str(Path(os.environ["JAVA_HOME"]) / "bin" / exe)
    found = shutil.which("java")
    if found:
        yield found


def _jar_candidates(root: Path | None):
    if os.environ.get("FLASHDEC_JPEXS_JAR"):
        yield os.environ["FLASHDEC_JPEXS_JAR"]
    if root is not None:
        yield str(root / "py_swf_editor/.dev-references/ffdec/ffdec-cli.jar")
        yield str(root / "py_swf_editor/.dev-references/ffdec/ffdec.jar")
        yield str(root / "jpexs-decompiler-master/dist/ffdec-cli.jar")
        yield str(root / "jpexs-decompiler-master/dist/ffdec.jar")
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        if base:
            yield str(Path(base) / "FFDec/ffdec-cli.jar")
            yield str(Path(base) / "FFDec/ffdec.jar")


def locate(java: str | None = None, jar: str | None = None) -> JpexsTool | None:
    root = find_workspace()
    java = java or next((c for c in _java_candidates(root) if Path(c).is_file()), None)
    jar = jar or next((c for c in _jar_candidates(root) if Path(c).is_file()), None)
    if not java or not jar:
        return None
    return JpexsTool(java, jar)


def run(tool: JpexsTool, swf_path: Path, out_dir: Path, *, resources: bool, timeout: int,
        deobfuscate: bool, memory: str = "2g") -> JpexsResult:
    result = JpexsResult()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    config = [
        "parallelSpeedUp=1",
        f"decompilationTimeoutFile={max(30, timeout // 2)}",
        f"exportTimeout={timeout}",
        f"autoDeobfuscate={1 if deobfuscate else 0}",
    ]
    formats = SCRIPT_FORMATS + ("," + RESOURCE_FORMATS if resources else "")
    types = "script" + ("," + RESOURCE_TYPES if resources else "")
    command = [tool.java, f"-Xmx{memory}", "-Djava.awt.headless=true", "-Dfile.encoding=UTF-8",
               "-jar", tool.jar, "-config", ",".join(config), "-format", formats,
               "-export", types, str(out_dir), str(swf_path)]
    started = time.perf_counter()
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout + 30)
        result.log_tail = (completed.stdout + completed.stderr)[-4000:]
        if completed.returncode != 0:
            result.error = f"ffdec exit code {completed.returncode}"
    except subprocess.TimeoutExpired:
        result.error = f"ffdec timed out after {timeout + 30}s"
    except OSError as error:
        result.error = f"could not start ffdec: {error}"
    result.seconds = time.perf_counter() - started
    scripts_dir = out_dir / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.rglob("*.as")):
            relative = path.relative_to(scripts_dir).as_posix()
            result.scripts[relative] = path.read_text(encoding="utf-8", errors="replace")
    result.resource_dir = out_dir
    return result
