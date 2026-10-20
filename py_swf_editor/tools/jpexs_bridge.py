#!/usr/bin/env python3
"""JSONL bridge to a locally configured JPEXS/FFDec CLI.

Input (one JSON object per line)::

    {"operation": "decompile", "filename": "x.swf",
     "swf_base64": "...", "tag_type": 82}

Output is one JSON object per line.  Configure either ``PY_SWF_FFDEC`` with
the ffdec executable/script, or ``PY_SWF_FFDEC_JAR`` with a JAR path.  The
bridge is intentionally not a library import: Java remains optional and can
be removed when a native Python backend reaches parity.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def _command(input_file: Path, output_dir: Path) -> list[str]:
    output_args = ["-format", "script:as", "-o", str(output_dir), str(input_file)]
    executable = os.environ.get("PY_SWF_FFDEC")
    if executable:
        # Windows batch launchers are not native executables. Explicitly use
        # cmd.exe so the bridge works with ffdec.bat and ffdec.cmd.
        if Path(executable).suffix.lower() in {".bat", ".cmd"}:
            return ["cmd", "/d", "/c", executable, *output_args]
        return [executable, *output_args]
    jar = os.environ.get("PY_SWF_FFDEC_JAR")
    if jar:
        java = os.environ.get("JAVA", "java")
        return [java, "-jar", jar, *output_args]
    raise RuntimeError("Set PY_SWF_FFDEC or PY_SWF_FFDEC_JAR")


def _java_bridge_command() -> list[str] | None:
    """Return the command for the persistent ffdec_lib JSONL bridge."""
    bridge = os.environ.get("PY_SWF_JPEXS_JAVA_BRIDGE")
    lib = os.environ.get("PY_SWF_FFDEC_LIB_JAR")
    if not bridge or not lib:
        return None
    java = os.environ.get("JAVA", "java")
    return [java, "-cp", f"{bridge}{os.pathsep}{lib}", "JPEXSJsonlBridge"]


def _collect_sections(output_dir: Path) -> list[dict[str, str | None]]:
    sections = []
    for path in sorted(output_dir.rglob("*.as")):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            sections.append({"name": path.name, "source": "", "error": str(exc)})
            continue
        sections.append({"name": str(path.relative_to(output_dir)), "source": source, "error": None})
    return sections


def handle(request: dict) -> dict:
    request_id = request.get("request_id")
    prefix = {"request_id": request_id} if request_id is not None else {}
    if request.get("operation") != "decompile":
        return {**prefix, "ok": False, "error": "Unsupported operation"}
    try:
        data = base64.b64decode(request["swf_base64"], validate=True)
        with tempfile.TemporaryDirectory(prefix="py-swf-jpexs-") as temp:
            root = Path(temp)
            input_file = root / (Path(request.get("filename", "input.swf")).name or "input.swf")
            output_dir = root / "out"
            output_dir.mkdir()
            input_file.write_bytes(data)
            timeout = float(os.environ.get("PY_SWF_JPEXS_TIMEOUT", "120"))
            try:
                proc = subprocess.run(
                    _command(input_file, output_dir),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {**prefix, "ok": False, "error": f"JPEXS timed out after {timeout} seconds"}
            if proc.returncode != 0:
                return {**prefix, "ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}
            sections = _collect_sections(output_dir)
            if not sections:
                return {**prefix, "ok": False, "error": "JPEXS produced no ActionScript files"}
            return {**prefix, "ok": True, "kind": "jpexs", "sections": sections}
    except Exception as exc:
        return {**prefix, "ok": False, "error": str(exc)}


def main() -> int:
    java_bridge = _java_bridge_command()
    if java_bridge:
        # Keep the Java process alive for the whole Python bridge lifetime.
        # Python remains the supervisor so timeout/cancellation semantics stay
        # identical for the HTTP server and CLI.
        child = subprocess.Popen(
            java_bridge,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert child.stdin is not None and child.stdout is not None
        try:
            for line in sys.stdin:
                child.stdin.write(line)
                child.stdin.flush()
                response = child.stdout.readline()
                if not response:
                    break
                print(response.rstrip("\r\n"), flush=True)
        finally:
            child.terminate()
        return child.wait(timeout=5)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
