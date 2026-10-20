"""Common decompilation backends.

The native backend is always available.  The optional JPEXS backend talks to
``tools/jpexs_bridge.py`` over JSON Lines and is deliberately isolated behind
this module so the HTTP API and the web UI do not depend on Java.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import base64
from copy import deepcopy
import json
import os
import subprocess
import sys
import tempfile
import threading
import queue
import uuid

from . import as3_outline, avm1_dec, avm2_dec
from ..avm2 import ABCFile, build_method_mapping
from ..swf_parser import SWFTag


@dataclass
class BackendResult:
    kind: str
    sections: list[dict[str, Any]]
    engine: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "sections": self.sections,
            "engine": self.engine,
            "error": self.error,
        }


def _native_avm1(tag: SWFTag) -> BackendResult:
    offset = 2 if tag.tag_type == 59 else 0
    source, error = avm1_dec.decompile_avm1(tag.data[offset:])
    compiler_reason = None
    if not error:
        from ..compiler.as_compiler import Parser, tokenize
        try:
            Parser(tokenize(source),"as2").parse_program()
        except (SyntaxError, ValueError) as unsupported:
            compiler_reason = f"Native compiler syntax is incomplete: {unsupported}"
    return BackendResult(
        kind="avm1",
        engine="native",
        sections=[{"name": tag.name, "source": source, "error": error,
                   "body_index": 0, "editable_source": not (error or compiler_reason),
                   "replaceable_source": True,
                   "reconstruction": "structured" if not error else "pcode",
                   "source_edit_reason": error or compiler_reason}],
    )


def _native_avm2(tag: SWFTag) -> BackendResult:
    from ..editing import source_edit_reason
    parsed = tag.parse_doabc()
    if parsed is None:
        raise ValueError("Malformed DoABC tag")
    _, _, abc_bytes = parsed
    abc = ABCFile()
    abc.parse(abc_bytes)
    from ..abc_symbols import AbcSymbolIndex
    abc._native_symbol_index = AbcSymbolIndex([abc])
    mapping = build_method_mapping(abc)
    sections = [{
        "name": "// Class outline",
        "source": as3_outline.outline_abc(abc),
        "error": None,
        "editable_source": False,
        "source_edit_reason": "Class outlines are read-only; select a method body",
    }]
    for i, method_body in enumerate(abc.method_bodies):
        from .script_initializers import script_declaration_owner
        represented_script = script_declaration_owner(abc, method_body)
        method_name = mapping.get(method_body.method, f"method_{method_body.method}")
        source, error = avm2_dec.decompile_method(abc, method_body, method_name, include_script_declarations=represented_script is not None)
        if represented_script is not None and error is None:
            error = "Script declarations are represented by a separate source document; exact method P-code retained"
        if source is None:
            source = f"// no decompilable ({error}); ver disassembly\n"
        method = abc.methods[method_body.method]
        reason = source_edit_reason(abc, method_body, symbol_index=abc._native_symbol_index)
        unknown = any("// UNKNOWN:" in line and not any(name in line for name in ("debugfile", "debugline", "debug ")) for line in source.splitlines())
        compiler_reason = None
        if not (reason or error):
            from ..compiler.as_compiler import Parser, tokenize
            from ..symbols import MethodBindings
            try:
                Parser(tokenize(source),"as3",MethodBindings(abc,method_body,symbol_index=abc._native_symbol_index)).parse_program()
            except (SyntaxError, ValueError) as unsupported:
                compiler_reason = f"The native compiler does not yet accept this reconstructed syntax: {unsupported}"
        editable = not (reason or unknown or error or compiler_reason)
        sections.append({
            "name": method_name,
            "source": source,
            "error": error,
            "body_index": i,
            "method_index": method_body.method,
            "source_kind": "script_declarations" if represented_script is not None else "method_body",
            "represented_by_document": f"script:{represented_script}" if represented_script is not None else None,
            "scope": "method",
            "completeness": "complete" if not error else "partial",
            "unit_ids": [f"method:{method_body.method}"],
            "diagnostics": [message for message in (error, reason, compiler_reason) if message],
            "compiler_eligibility": {"eligible": editable, "reason": reason or compiler_reason},
            "editable_source": editable,
            "replaceable_source": reason is None,
            "reconstruction": "structured" if not error else "pcode",
            "source_edit_reason": reason or compiler_reason or (None if editable else "Source reconstruction is incomplete; use exact P-code"),
        })
        if represented_script is not None:
            sections[-1]["pcode"] = avm2_dec.disassemble_method(abc, method_body)
    from ..class_documents import reconstruct_abc_documents
    documents = reconstruct_abc_documents(abc, method_sections=sections[1:])
    # The legacy overview remains addressable, but cannot disguise failed
    # members as empty implementation bodies.
    if documents:
        sections[0]["source"] = "\n".join(section["source"] for section in documents if section["scope"] == "class")
    sections[0].update(scope="outline", completeness="outline", unit_ids=[], diagnostics=[], compiler_eligibility={"eligible": False, "reason": "Overview is read-only"})
    sections.extend(documents)
    return BackendResult(kind="avm2", sections=sections, engine="native")


class NativeBackend:
    name = "native"

    def decompile(self, tag: SWFTag, swf_bytes: bytes, filename: str) -> BackendResult:
        if tag.tag_type in (12, 59):
            return _native_avm1(tag)
        if tag.is_doabc:
            return _native_avm2(tag)
        raise ValueError("Not a supported script tag")


class JPEXSBackend:
    """Optional adapter for a locally installed/build JPEXS CLI.

    The bridge is a separate process and communicates only JSONL.  This keeps
    Java out of the Python import graph and makes failures degradable.
    """

    name = "jpexs"

    def __init__(self, bridge: str | Path | None = None, timeout: float = 120.0):
        self.bridge = Path(bridge or os.environ.get("PY_SWF_JPEXS_BRIDGE", "tools/jpexs_bridge.py"))
        if not self.bridge.is_absolute():
            self.bridge = Path(__file__).resolve().parents[2] / self.bridge
        self.timeout = timeout
        self._process = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.bridge.is_file() and bool(
            os.environ.get("PY_SWF_FFDEC")
            or os.environ.get("PY_SWF_FFDEC_JAR")
            or (os.environ.get("PY_SWF_JPEXS_JAVA_BRIDGE") and os.environ.get("PY_SWF_FFDEC_LIB_JAR"))
        )

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "bridge": str(self.bridge),
            "configured_executable": os.environ.get("PY_SWF_FFDEC"),
            "configured_jar": os.environ.get("PY_SWF_FFDEC_JAR"),
            "configured_java_bridge": os.environ.get("PY_SWF_JPEXS_JAVA_BRIDGE"),
            "configured_library_jar": os.environ.get("PY_SWF_FFDEC_LIB_JAR"),
        }

    def decompile(self, tag: SWFTag, swf_bytes: bytes, filename: str) -> BackendResult:
        if not self.available:
            raise RuntimeError("JPEXS backend is not configured")
        request = {
            "request_id": uuid.uuid4().hex,
            "operation": "decompile",
            "filename": filename,
            "tag_type": tag.tag_type,
            "swf_base64": base64.b64encode(swf_bytes).decode("ascii"),
        }
        response = self._request(request)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "JPEXS decompilation failed"))
        return BackendResult(
            kind=response.get("kind", "unknown"),
            sections=response.get("sections", []),
            engine="jpexs",
            error=response.get("error"),
        )

    def _start(self):
        self._process = subprocess.Popen(
            [sys.executable, str(self.bridge)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def _request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send one JSONL request to a persistent bridge process.

        The current Python bridge may invoke the FFDec CLI per request.  The
        persistent boundary is intentional: replacing that implementation by
        a long-lived Java/JPEXS worker later does not change this API.
        """
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._start()
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()
            result_queue = queue.Queue(maxsize=1)

            def read_response():
                try:
                    result_queue.put((self._process.stdout.readline(), None))
                except Exception as exc:
                    result_queue.put((None, exc))

            threading.Thread(target=read_response, daemon=True).start()
            try:
                line, read_error = result_queue.get(timeout=self.timeout)
            except queue.Empty as exc:
                self._process.kill()
                self._process = None
                raise RuntimeError(f"JPEXS bridge timed out after {self.timeout:g}s") from exc
            if read_error is not None:
                raise RuntimeError(str(read_error))
            if not line:
                stderr = self._process.stderr.read() if self._process.stderr else ""
                self._process = None
                raise RuntimeError(stderr.strip() or "JPEXS bridge exited unexpectedly")
            try:
                return json.loads(line)
            except ValueError as exc:
                raise RuntimeError(f"Invalid JPEXS bridge response: {line[-500:]}") from exc

    def close(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            self._process = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def backend_status() -> dict[str, Any]:
    native = {"name": "native", "available": True}
    jpexs = JPEXSBackend()
    return {"default": "native", "backends": [native, jpexs.status()]}


def decompile_with_fallback(
    tag: SWFTag,
    swf_bytes: bytes,
    filename: str,
    engine: str = "native",
) -> BackendResult:
    """Run the requested engine, falling back to Python in ``auto`` mode."""
    native = NativeBackend()
    jpexs = JPEXSBackend()
    if engine not in {"auto", "native", "jpexs"}:
        raise ValueError("engine must be auto, native or jpexs")
    if engine == "native":
        return native.decompile(tag, swf_bytes, filename)
    if engine == "jpexs":
        return jpexs.decompile(tag, swf_bytes, filename)
    if jpexs.available:
        try:
            return jpexs.decompile(tag, swf_bytes, filename)
        except Exception:
            # Auto mode must never make the app unusable because Java/JPEXS
            # is missing, broken, or cannot decode one particular SWF.
            pass
    return native.decompile(tag, swf_bytes, filename)


def compare_decompilation(tag: SWFTag, swf_bytes: bytes, filename: str) -> dict[str, Any]:
    """Run both engines and return comparable output, never hiding failures."""
    native = NativeBackend().decompile(tag, swf_bytes, filename).as_dict()
    jpexs = JPEXSBackend()
    if jpexs.available:
        try:
            jpexs_result = jpexs.decompile(tag, swf_bytes, filename).as_dict()
            jpexs_result["ok"] = True
        except Exception as exc:
            jpexs_result = {"engine": "jpexs", "ok": False, "error": str(exc), "sections": []}
    else:
        jpexs_result = {
            "engine": "jpexs",
            "ok": False,
            "error": "JPEXS backend is not configured",
            "sections": [],
        }

    native_text = "\n".join(section.get("source", "") for section in native["sections"])
    jpexs_text = "\n".join(section.get("source", "") for section in jpexs_result.get("sections", []))
    native_lines = native_text.splitlines()
    jpexs_lines = jpexs_text.splitlines()
    matching_lines = sum(1 for left, right in zip(native_lines, jpexs_lines) if left.strip() == right.strip())
    denominator = max(len(native_lines), len(jpexs_lines), 1)
    return {
        "kind": native["kind"],
        "engine": "compare",
        "native": {**native, "ok": True},
        "jpexs": jpexs_result,
        "metrics": {
            "native_lines": len(native_lines),
            "jpexs_lines": len(jpexs_lines),
            "matching_line_ratio": matching_lines / denominator,
        },
    }
