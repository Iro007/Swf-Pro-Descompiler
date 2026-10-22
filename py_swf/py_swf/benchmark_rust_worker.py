"""Benchmark bridge for the pinned Rust SWF JSONL worker.

Every timed operation opens a new temporary SWF. Unit identities are accepted
only after comparison with Python's canonical location and bytecode inventory.
The parent benchmark_process.Worker bounds the complete process tree.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .script_inventory import enumerate_scripts
from .canonical_inventory import build_inventory, attach_observations, cross_validate

MAX_INPUT_BYTES = 100 * 1024**2
MAX_UNITS = 100_000
MAX_PCODE_CHARACTERS = 4 * 1024**2
MAX_REPLY_CHARACTERS = 8 * 1024**2
PROTOCOL = "rust-swf-jsonl-v1"


class RustProtocolError(RuntimeError):
    """Invalid transport, process exit or deadline; discard the worker."""


class RustOperationError(ValueError):
    """The Rust worker rejected this particular operation."""


def checked_executable(executable, expected_sha256):
    path = Path(executable).resolve(strict=True)
    expected = expected_sha256.lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("A complete executable SHA-256 is required")
    if not path.is_file():
        raise ValueError("Rust executable is not a file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError("Rust executable changed after configuration")
    return {"executable": str(path), "sha256": actual, "adapter_protocol": PROTOCOL}


class RustWorker:
    """Persistent native worker; all requests are correlated and bounded."""

    def __init__(self, executable, expected_sha256, timeout=105, engine="rust"):
        if timeout <= 0:
            raise ValueError("Rust worker timeout must be positive")
        self.engine = engine
        self.fingerprint = checked_executable(executable, expected_sha256)
        self.timeout = timeout
        self.responses = queue.Queue()
        self.logs = deque(maxlen=64)
        self.reported_ms = 0.0
        self.process = subprocess.Popen(
            [self.fingerprint["executable"], "worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="strict", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    def _read(self):
        output = self.process.stdout
        try:
            while True:
                line = output.readline(MAX_REPLY_CHARACTERS + 1)
                if not line:
                    break
                if len(line) > MAX_REPLY_CHARACTERS or not line.endswith("\n"):
                    self.responses.put(RustProtocolError("Rust response exceeds the JSONL bound"))
                    return
                self.responses.put(line)
        except (OSError, ValueError, UnicodeError) as error:
            self.responses.put(RustProtocolError("Rust response read failed: " + str(error)))
        finally:
            self.responses.put(None)

    def _stderr(self):
        error_output = self.process.stderr
        try:
            while chunk := error_output.read(2048):
                self.logs.append(chunk)
        except (OSError, ValueError, UnicodeError):
            pass

    def exchange(self, operation, args, deadline):
        request_id = uuid.uuid4().hex
        if time.perf_counter() >= deadline:
            raise RustProtocolError("Rust benchmark operation deadline exceeded")
        try:
            self.process.stdin.write(json.dumps(
                {"request_id": request_id, "operation": operation, "args": args},
                ensure_ascii=False,
            ) + "\n")
            self.process.stdin.flush()
            reply = self.responses.get(timeout=max(.001, deadline - time.perf_counter()))
        except queue.Empty:
            self.close()
            raise RustProtocolError("Rust benchmark operation deadline exceeded") from None
        except (OSError, ValueError) as error:
            self.close()
            raise RustProtocolError("Rust request write failed: " + str(error)) from error
        if isinstance(reply, Exception):
            self.close()
            raise reply
        if reply is None:
            code = self.process.poll()
            self.close()
            raise RustProtocolError(f"Rust worker exited without JSON (exit={code}): " + "".join(self.logs)[-4000:])
        try:
            result = json.loads(reply)
        except (ValueError, TypeError) as error:
            self.close()
            raise RustProtocolError("Rust worker returned invalid JSON") from error
        if not isinstance(result, dict) or result.get("request_id") != request_id:
            self.close()
            raise RustProtocolError("Rust JSONL request_id mismatch")
        elapsed = result.get("elapsed_ms", 0)
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed >= 0:
            self.reported_ms += elapsed
        return result

    def call(self, operation, args, deadline):
        reply = self.exchange(operation, args, deadline)
        if "error" in reply:
            raise RustOperationError(str(reply["error"]))
        if not isinstance(reply.get("result"), dict):
            raise RustProtocolError("Rust response has no result object")
        return reply["result"]

    def ping(self):
        # The native worker has no ping verb. Info returns either a correlated
        # no-session error or an info result; both prove its JSONL loop is alive.
        reply = self.exchange("info", {}, time.perf_counter() + self.timeout)
        if "error" not in reply and not isinstance(reply.get("result"), dict):
            raise RustProtocolError("Rust info handshake has no result or error")
        return {"ok": True, "engine": getattr(self, "engine", "rust"), "tool_fingerprint": self.fingerprint}

    def close(self):
        process = getattr(self, "process", None)
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        self.process = None


def _integer(value, label, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise RustProtocolError("Invalid Rust " + label)
    return value


def scripts(worker, deadline):
    output, offset, expected_total = [], 0, None
    while True:
        page = worker.call("scripts", {"offset": offset, "limit": 500}, deadline)
        total = _integer(page.get("total"), "script count", MAX_UNITS)
        if expected_total is not None and total != expected_total:
            raise RustProtocolError("Rust script inventory changed during pagination")
        expected_total = total
        if page.get("offset") != offset or not isinstance(page.get("items"), list):
            raise RustProtocolError("Invalid Rust script page")
        items = page["items"]
        if len(items) > 500 or offset + len(items) > total or any(not isinstance(i, dict) for i in items):
            raise RustProtocolError("Invalid Rust script page length")
        output.extend(items)
        offset += len(items)
        if offset == total:
            return output
        if not items:
            raise RustProtocolError("Rust script pagination made no progress")


def disassembly(worker, unit_id, deadline):
    chunks, offset, total, first = [], 0, None, None
    while True:
        page = worker.call("disassemble", {"id": unit_id, "offset": offset, "limit": 0}, deadline)
        count = _integer(page.get("pcode_total"), "P-code count", MAX_PCODE_CHARACTERS)
        if total is not None and count != total:
            raise RustProtocolError("Rust P-code changed during pagination")
        total = count
        if first is None:
            first = page
        chunk = page.get("pcode")
        if page.get("offset") != offset or not isinstance(chunk, str) or len(chunk) > 65536 or offset + len(chunk) > total:
            raise RustProtocolError("Invalid Rust P-code page")
        chunks.append(chunk)
        offset += len(chunk)
        if offset == total:
            return "".join(chunks), first
        if not chunk:
            raise RustProtocolError("Rust P-code pagination made no progress")


def map_inventory(canonical, native):
    """Require unique location, language, length and exact bytecode SHA-256."""
    canonical_counts = Counter(unit["document_id"] for unit in canonical)
    native_counts = Counter(unit.get("id") for unit in native)
    by_id = {unit["document_id"]: unit for unit in canonical}
    matched, unmapped = {}, []
    for unit in native:
        identity = unit.get("id")
        reference = by_id.get(identity)
        context = reference.get("avm1_context") if reference is not None else None
        context_verified = context is None or (
            unit.get("parameters") == context["parameters"]
            and unit.get("avm1_registers") == context["registers"]
            and unit.get("avm1_register_count") == context["register_count"]
            and unit.get("avm1_flags") == context["flags"]
            and unit.get("avm1_constants_sha256") == context["constants_sha256"]
        )
        verified = (
            isinstance(identity, str)
            and canonical_counts[identity] == native_counts[identity] == 1
            and reference is not None
            and unit.get("tag_path") == reference["tag_path"]
            and (reference.get("normalized_byte_start") is None
                 or unit.get("byte_start") == reference["normalized_byte_start"])
            and {"avm1": "as2", "avm2": "as3"}.get(unit.get("language"), unit.get("language")) == reference["language"]
            and unit.get("byte_len") == reference["bytecode_bytes"]
            and unit.get("sha256") == reference["bytecode_sha256"]
            and reference.get("diagnostic") is None
            and context_verified
        )
        if verified:
            matched[identity] = reference
        else:
            unmapped.append(unit)
    missing = [unit for unit in canonical if unit["document_id"] not in matched]
    return matched, unmapped, missing


def render_unit(worker, metadata, operation, include_source, deadline):
    identity = metadata["id"]
    diagnostics = list(metadata.get("diagnostics") or [])
    instruction_count, assembly_state = None, "not_separately_checked"
    if operation == "disassemble":
        pcode, result = disassembly(worker, identity, deadline)
        source = None
        state = result.get("state")
        assembly_state = state
        instruction_count = result.get("instruction_total")
        diagnostics.extend(result.get("diagnostics") or [])
    else:
        result = worker.call("decompile", {"id": identity}, deadline)
        if result.get("unit_id") != identity:
            raise RustProtocolError("Rust reconstructed a different unit")
        state, source = result.get("state"), result.get("source")
        if source is not None and (not isinstance(source, str) or state != "structured"):
            raise RustProtocolError("Rust source does not match its reconstruction state")
        total = _integer(result.get("pcode_total"), "P-code count", MAX_PCODE_CHARACTERS)
        pcode = result.get("pcode")
        if not isinstance(pcode, str):
            raise RustProtocolError("Rust reconstruction has no P-code text")
        if total > 65536:
            pcode, listing = disassembly(worker, identity, deadline)
            if len(pcode) != total:
                raise RustProtocolError("Rust reconstruction and listing disagree")
            instruction_count = listing.get("instruction_total")
            assembly_state = listing.get("state")
            diagnostics.extend(listing.get("diagnostics") or [])
        elif len(pcode) != total:
            raise RustProtocolError("Rust reconstruction P-code is truncated")
        diagnostics.extend(result.get("diagnostics") or [])
        if state == "structured" and source is None:
            raise RustProtocolError("Rust structured result contains no source")
    text = pcode if operation == "disassemble" or source is None else source
    if operation == "disassemble":
        generation = "disassembly" if assembly_state in ("decoded", "partial") else "failed"
    else:
        generation = "available" if source is not None else "pcode"
    record = {
        "generation": generation,
        "rust_state": state,
        "rust_disassembly_state": assembly_state,
        "rust_diagnostics": diagnostics,
        "diagnostic": "; ".join(str(d.get("message", d)) if isinstance(d, dict) else str(d) for d in diagnostics) or None,
        "syntax": "not_checked", "compilation": "not_run", "swf_validation": "not_run",
        "behavior": "not_run", "source_fidelity": "not_applicable" if operation == "disassemble" else "not_verified",
        "compiler_eligible": False, "source_edit_reason": "Rust SWF Studio has no ActionScript compiler",
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_characters": len(text), "source_utf8_bytes": len(text.encode("utf-8")),
        "pcode_sha256": hashlib.sha256(pcode.encode("utf-8")).hexdigest(),
        "pcode_characters": len(pcode), "pcode_complete_transport": True,
        "instruction_count": instruction_count,
    }
    if operation == "disassemble":
        record["disassembly"] = "available" if assembly_state == "decoded" else "partial" if assembly_state == "partial" else "unavailable"
        record["disassembly_complete_decode"] = assembly_state == "decoded"
    if include_source:
        record["source"] = text
        record["pcode"] = pcode
    return record


def perform(worker, data, operation, include_source=False):
    engine_id = getattr(worker, "engine", "rust")
    if operation == "source":
        operation = "decompile"
    if operation not in ("decompile", "disassemble"):
        return {"ok": False, "engine": engine_id, "status": "not_applicable", "units": [],
                "compilation": "not_run", "behavior": "not_run",
                "diagnostic": "Rust supports source reconstruction and disassembly; no ActionScript compiler"}
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("Rust benchmark input exceeds 100 MiB")
    deadline = time.perf_counter() + worker.timeout
    worker.reported_ms = 0.0
    started = time.perf_counter()
    byte_inventory = build_inventory(data)
    canonical_error = None
    try:
        canonical = [unit.metadata() for unit in enumerate_scripts(data, include_nested_avm1=True)]
    except (ValueError, EOFError, IndexError, KeyError, UnicodeError) as error:
        canonical, canonical_error = [], str(error)
    identity_parse_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pyswf-rust-benchmark-") as folder:
        path = Path(folder) / "input.swf"
        path.write_bytes(data)
        opened = worker.call("open", {"path": str(path)}, deadline)
        info = opened.get("info")
        if not isinstance(info, dict) or info.get("sha256") != hashlib.sha256(data).hexdigest():
            raise RustProtocolError("Rust fresh-open input SHA-256 mismatch")
        native = scripts(worker, deadline)
        parse_ms = identity_parse_ms + (time.perf_counter() - started) * 1000
        matched, unmapped, missing = map_inventory(canonical, native)
        generated, artifacts = [], []
        started = time.perf_counter()
        for unit in native:
            identity = unit.get("id")
            if not isinstance(identity, str):
                raise RustProtocolError("Rust inventory contains no unit identity")
            try:
                output = render_unit(worker, unit, operation, include_source, deadline)
            except RustOperationError as error:
                output = {"generation": "failed", "diagnostic": str(error), "syntax": "not_checked",
                          "compilation": "not_run", "swf_validation": "not_run", "behavior": "not_run",
                          "source_fidelity": "not_verified", "compiler_eligible": False}
            if identity in matched:
                generated.append({**matched[identity], **output, "identity_verified": True, "rust_metadata": unit})
            else:
                artifacts.append({"rust_document_id": identity, "rust_metadata": unit, **output})
        for unit in missing:
            generated.append({**unit, "generation": "failed", "identity_verified": False,
                              "diagnostic": "No unique Rust unit matched canonical location and bytecode hash",
                              "syntax": "not_checked", "compilation": "not_run", "swf_validation": "not_run",
                              "behavior": "not_run", "source_fidelity": "not_verified", "compiler_eligible": False})
        generate_ms = (time.perf_counter() - started) * 1000
    mapping_complete = not (unmapped or missing or canonical_error)
    independent_check = cross_validate(byte_inventory, native)
    byte_inventory['cross_validation'] = {'engine': engine_id, **independent_check}
    generated = attach_observations(byte_inventory, generated, engine=engine_id, adapter_mode='rust_jsonl_byte_units')
    mapping_complete = mapping_complete and independent_check['status'] == 'verified'
    return {
        "ok": True, "engine": engine_id, "status": "measured" if mapping_complete else "output_unmapped",
        "units": generated, "documents": [], "eligible_units": len(canonical),
        "unit_observations": generated, "canonical_inventory": byte_inventory,
        "canonical_inventory_sha256": byte_inventory['sha256'],
        "inventory_status": independent_check['status'], "inventory_cross_validation": independent_check,
        "adapter_mode": 'rust_jsonl_byte_units', "include_scope": sorted({u.get('scope') for u in generated}),
        "rust_unit_count": len(native), "matched_units": len(matched),
        "unmapped_units": artifacts, "missing_canonical_units": missing,
        "identity_mapping": "location_language_length_and_bytecode_sha256" if mapping_complete else "partial_or_unverified",
        "canonical_inventory_diagnostic": canonical_error,
        "parse_ms": parse_ms, "generate_ms": generate_ms, "identity_parse_ms": identity_parse_ms,
        "rust_reported_operation_ms": worker.reported_ms,
        "decoded_script_bytes": sum(unit.get("byte_len", 0) for unit in native),
        "container_diagnostics": opened.get("diagnostics", []),
        "tool_fingerprint": worker.fingerprint, "rust_document_info": info,
        "source_fidelity": "not_verified", "behavior": "not_run", "compilation": "not_run",
        "process_mode": "Persistent Rust JSONL process; every operation freshly opens a temporary SWF",
        "timing_scope": "Wall time includes Python identity inventory, temporary I/O, Rust parsing/generation and paged JSONL; Rust reported milliseconds are a separate internal observation",
    }


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--timeout", type=float, default=105)
    parser.add_argument("--engine", default="rust")
    args = parser.parse_args()
    checked_executable(args.executable, args.sha256)
    worker = None
    protocol = sys.stdout
    sys.stdout = sys.stderr
    try:
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("JSONL request must be an object")
                if worker is None:
                    worker = RustWorker(args.executable, args.sha256, args.timeout, engine=args.engine)
                if request.get("operation") == "ping":
                    result = worker.ping()
                else:
                    started = time.perf_counter()
                    data = base64.b64decode(request["swf_base64"], validate=True)
                    decode_ms = (time.perf_counter() - started) * 1000
                    result = perform(worker, data, request.get("operation"), request.get("include_source", False))
                    result["input_decode_ms"] = decode_ms
            except Exception as error:
                result = {"ok": False, "engine": args.engine, "units": [], "diagnostic": str(error)}
                if worker is not None:
                    worker.close()
                    worker = None
            result["request_id"] = request.get("request_id") if isinstance(request, dict) else None
            started = time.perf_counter()
            json.dumps(result, ensure_ascii=False)
            result["serialize_ms"] = (time.perf_counter() - started) * 1000
            protocol.write(json.dumps(result, ensure_ascii=False) + "\n")
            protocol.flush()
    finally:
        if worker is not None:
            worker.close()


if __name__ == "__main__":
    main()
