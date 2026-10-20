"""Identity, transport and fallback checks for the Rust benchmark adapter."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import sys
import time

import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.benchmark_process import Worker
from py_swf.benchmark_rust_worker import (
    RustProtocolError, RustWorker, checked_executable, disassembly,
    map_inventory, perform, render_unit, scripts,
)
from py_swf.script_inventory import enumerate_scripts
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf


class FakeRust:
    timeout = 5
    reported_ms = 0
    fingerprint = {"executable": "test-only", "sha256": "0" * 64}

    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.calls = []

    def call(self, operation, args, deadline):
        assert time.perf_counter() < deadline
        self.calls.append((operation, args))
        return self.dispatch(operation, args)


def native_metadata(reference):
    return {"id": reference["document_id"], "name": reference["name"],
            "language": {"as2": "avm1", "as3": "avm2"}[reference["language"]], "kind": reference["kind"],
            "tag_path": reference["tag_path"], "byte_len": reference["bytecode_bytes"],
            "byte_start": reference.get("normalized_byte_start", reference["byte_start"]),
            "sha256": reference["bytecode_sha256"], "diagnostics": []}


def canonical_fixture():
    data = make_synthetic_swf([SWFTag(12, assemble_avm1("push 1\ntrace\nend"))])
    return data, next(enumerate_scripts(data, include_nested_avm1=True)).metadata()


def test_sha256_is_required_and_checked_before_process_launch(tmp_path, monkeypatch):
    binary = tmp_path / "rustswf.exe"
    binary.write_bytes(b"original")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert checked_executable(binary, digest)["sha256"] == digest
    binary.write_bytes(b"changed")
    monkeypatch.setattr("py_swf.benchmark_rust_worker.subprocess.Popen",
                        lambda *a, **k: pytest.fail("Hash-mismatched binary must not launch"))
    with pytest.raises(ValueError, match="changed after configuration"):
        RustWorker(binary, digest)
    with pytest.raises(ValueError, match="complete executable SHA-256"):
        checked_executable(binary, "123")


def test_identity_uses_location_language_length_and_hash():
    _, canonical = canonical_fixture()
    native = native_metadata(canonical)
    matched, extra, missing = map_inventory([canonical], [native])
    assert list(matched) == [canonical["document_id"]] and not extra and not missing
    for field, value in (("sha256", "bad"), ("byte_len", 999), ("language", "as3"), ("id", "tag:9/actions"), ("tag_path", [9]), ("byte_start", 999)):
        changed = {**native, field: value}
        matched, extra, missing = map_inventory([canonical], [changed])
        assert not matched and extra == [changed] and missing == [canonical]


def test_duplicate_identities_and_diagnostic_units_are_unmapped():
    _, canonical = canonical_fixture()
    native = native_metadata(canonical)
    assert not map_inventory([canonical], [native, native])[0]
    assert not map_inventory([canonical, canonical], [native])[0]
    assert not map_inventory([{**canonical, "diagnostic": "damaged"}], [native])[0]


def test_scripts_pages_beyond_500_and_rejects_stall():
    items = [{"id": str(n)} for n in range(501)]
    worker = FakeRust(lambda operation, args: {"total": 501, "offset": args["offset"],
                                              "items": items[args["offset"]:args["offset"] + 500]})
    assert scripts(worker, time.perf_counter() + 5) == items
    assert [args["offset"] for _, args in worker.calls] == [0, 500]
    worker = FakeRust(lambda operation, args: {"total": 1, "offset": 0, "items": []})
    with pytest.raises(RustProtocolError, match="no progress"):
        scripts(worker, time.perf_counter() + 5)


def test_disassembly_recovers_full_unicode_pcode_with_stable_pages():
    text = "🙂" * 65536 + "tail"
    worker = FakeRust(lambda operation, args: {
        "pcode": text[args["offset"]:args["offset"] + 65536],
        "pcode_total": len(text), "offset": args["offset"],
        "state": "decoded", "instruction_total": 1, "diagnostics": [],
    })
    recovered, first = disassembly(worker, "tag:0/actions", time.perf_counter() + 5)
    assert recovered == text and first["state"] == "decoded"
    assert [args["offset"] for _, args in worker.calls] == [0, 65536]


def test_disassembly_rejects_changed_total_and_truncated_pages():
    calls = [0]
    def dispatch(operation, args):
        calls[0] += 1
        return {"pcode": "a", "pcode_total": 2 if calls[0] == 1 else 3,
                "offset": args["offset"], "state": "decoded"}
    with pytest.raises(RustProtocolError, match="changed during pagination"):
        disassembly(FakeRust(dispatch), "u", time.perf_counter() + 5)
    with pytest.raises(RustProtocolError, match="no progress"):
        disassembly(FakeRust(lambda op, args: {"pcode": "", "pcode_total": 1, "offset": 0}),
                    "u", time.perf_counter() + 5)


def test_fallback_retains_complete_pcode_and_never_claims_source_equivalence():
    text = "a" * 65536 + "tail"
    def dispatch(operation, args):
        if operation == "decompile":
            return {"unit_id": "u", "state": "pcode_only", "source": None,
                    "pcode": text[:65536] + "\n; partial view", "pcode_total": len(text),
                    "diagnostics": [{"code": "unsupported", "message": "switch not supported"}]}
        return {"pcode": text[args["offset"]:args["offset"] + 65536],
                "pcode_total": len(text), "offset": args["offset"], "state": "decoded",
                "instruction_total": 10, "diagnostics": []}
    output = render_unit(FakeRust(dispatch), {"id": "u"}, "decompile", True, time.perf_counter() + 5)
    assert output["generation"] == "pcode" and output["source"] == output["pcode"] == text
    assert output["source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert output["syntax"] == "not_checked"
    assert output["behavior"] == output["compilation"] == output["swf_validation"] == "not_run"
    assert output["source_fidelity"] == "not_verified" and not output["compiler_eligible"]


def test_partial_decode_is_explicit_and_does_not_claim_full_disassembly():
    output = render_unit(FakeRust(lambda op, args: {
        "pcode": "; undecoded raw tail", "pcode_total": 20, "offset": 0,
        "state": "partial", "instruction_total": 1, "diagnostics": [],
    }), {"id": "u"}, "disassemble", True, time.perf_counter() + 5)
    assert output["generation"] == "disassembly" and output["disassembly"] == "partial"
    assert output["disassembly_complete_decode"] is False


def test_each_operation_opens_a_new_copy_and_records_verified_canonical_units():
    data, canonical = canonical_fixture()
    metadata = native_metadata(canonical)
    paths = []
    def dispatch(operation, args):
        if operation == "open":
            path = Path(args["path"])
            paths.append(path)
            assert path.read_bytes() == data
            return {"info": {"sha256": hashlib.sha256(data).hexdigest()}, "diagnostics": []}
        if operation == "scripts":
            return {"total": 1, "offset": args["offset"], "items": [metadata]}
        return {"unit_id": metadata["id"], "state": "structured", "source": "trace(1);",
                "pcode": "trace", "pcode_total": 5, "diagnostics": []}
    worker = FakeRust(dispatch)
    first = perform(worker, data, "source", True)
    second = perform(worker, data, "decompile", False)
    assert first["status"] == second["status"] == "measured"
    assert first["units"][0]["document_id"] == canonical["document_id"]
    assert first["units"][0]["identity_verified"] is True
    assert "source" not in second["units"][0]
    assert first["units"][0]["source"] == "trace(1);"
    assert paths[0] != paths[1] and all(not path.exists() for path in paths)
    assert first["identity_parse_ms"] >= 0 and first["rust_reported_operation_ms"] == 0


def test_extra_nested_function_preserves_outputs_and_marks_denominator_unmapped():
    data, canonical = canonical_fixture()
    original = native_metadata(canonical)
    nested = {**original, "id": original["id"] + "/function:0"}
    def dispatch(operation, args):
        if operation == "open":
            return {"info": {"sha256": hashlib.sha256(data).hexdigest()}}
        if operation == "scripts":
            return {"total": 2, "offset": 0, "items": [original, nested]}
        return {"unit_id": args["id"], "state": "pcode_only", "source": None,
                "pcode": "end", "pcode_total": 3, "diagnostics": []}
    output = perform(FakeRust(dispatch), data, "source", True)
    assert output["status"] == "output_unmapped" and output["matched_units"] == 1
    assert output["unmapped_units"][0]["rust_document_id"] == nested["id"]
    assert output["unmapped_units"][0]["source"] == "end"
    assert output["eligible_units"] == 1 and output["rust_unit_count"] == 2


def test_compile_remains_not_run_and_never_calls_rust():
    worker = FakeRust(lambda *a: pytest.fail("No Rust compiler exists"))
    result = perform(worker, b"", "compile")
    assert result["status"] == "not_applicable" and not result["ok"]
    assert result["compilation"] == result["behavior"] == "not_run"


def test_protocol_mismatch_and_deadline_discard_transport():
    worker = RustWorker.__new__(RustWorker)
    worker.process = type("Process", (), {"stdin": io.StringIO()})()
    worker.responses = queue.Queue()
    worker.reported_ms = 0
    closed = []
    worker.close = lambda: closed.append(True)
    worker.responses.put(json.dumps({"request_id": "wrong", "result": {}}) + "\n")
    with pytest.raises(RustProtocolError, match="request_id mismatch"):
        worker.exchange("info", {}, time.perf_counter() + 1)
    assert closed
    worker.responses = queue.Queue()
    with pytest.raises(RustProtocolError, match="deadline exceeded"):
        worker.exchange("info", {}, time.perf_counter() + .001)


ROOT = Path(__file__).resolve().parents[1]
RUST_EXE = ROOT.parent / "RUST SWF/dist/FlashUniversalStudio/rustswf.exe"


@pytest.mark.skipif(os.name != "nt" or not RUST_EXE.is_file(), reason="Packaged Rust CLI is unavailable")
def test_packaged_rust_real_jsonl_fresh_open_source_and_501_unit_inventory():
    pytest.importorskip("psutil")
    digest = hashlib.sha256(RUST_EXE.read_bytes()).hexdigest()
    command = [sys.executable, "-m", "py_swf.benchmark_rust_worker",
               "--executable", str(RUST_EXE), "--sha256", digest]
    data, canonical = canonical_fixture()
    with Worker(command, cwd=ROOT, timeout=30, memory_bytes=1024**3) as worker:
        source = worker.request({"operation": "source", "swf_base64": base64.b64encode(data).decode(),
                                 "include_source": True})
        assert source["ok"], source
        assert source["status"] == "measured" and source["units"][0]["identity_verified"]
        assert source["units"][0]["document_id"] == canonical["document_id"]
        assert source["units"][0]["generation"] == "available"
        assert source["units"][0]["behavior"] == source["units"][0]["compilation"] == "not_run"
        assert source["tool_fingerprint"]["sha256"] == digest
        assert worker.startup_ms > 0
        large = make_synthetic_swf([SWFTag(12, b"\x00") for _ in range(501)])
        listing = worker.request({"operation": "disassemble", "swf_base64": base64.b64encode(large).decode()})
        assert listing["ok"], listing
        assert listing["status"] == "measured" and len(listing["units"]) == 501
        assert listing["units"][-1]["document_id"] == "tag:500/actions"
        assert listing["units"][0]["source_sha256"] != source["units"][0]["source_sha256"]


@pytest.mark.skipif(os.name != "nt" or not RUST_EXE.is_file(), reason="Packaged Rust CLI is unavailable")
def test_native_and_rust_integrated_benchmark_matches_as3_hashes_and_preserves_input(tmp_path):
    pytest.importorskip("psutil")
    from py_swf.benchmarks import run_benchmark
    from tests.native_fixtures import as3_tag
    data = make_synthetic_swf([as3_tag()])
    movie = tmp_path / "fixture.swf"
    movie.write_bytes(data)
    executable = str(RUST_EXE.resolve())
    digest = hashlib.sha256(RUST_EXE.read_bytes()).hexdigest()
    config = tmp_path / "tools.json"
    config.write_text(json.dumps({"development_only": True, "tools": {
        "rust": {"executable": executable, "version": "0.1.0",
                 "artifact_sha256": {executable: digest}}
    }}), encoding="utf-8")
    report = run_benchmark(movie, engines=("native", "rust"), profile="smoke",
                           output=tmp_path / "report", tool_config=config, timeout=30,
                           memory_bytes=1024**3)
    assert movie.read_bytes() == data
    assert report["completed_files"] == 1 and report["claims"]["parity"] is False
    case = report["cases"][0]
    assert case["original_preserved"]
    rust = next(engine for engine in case["engines"] if engine["engine"] == "rust")
    assert rust["status"] == "measured", rust["failures"]
    assert rust["tool_fingerprint"]["sha256"] == digest
    assert rust["hot"]["successful"] == 3 and rust["cold"]["successful"] == 1
    assert rust["compilation_sample"] == []
    assert all(unit["language"] == "as3" and unit["identity_verified"] for unit in rust["units"])
    assert all(unit["behavior"] == unit["compilation"] == "not_run" for unit in rust["units"])
    pair = next(pair for pair in case["comparison"]["pairs"] if pair["engines"] == ["native", "rust"])
    assert pair["matched"] == 2 and pair["bytecode_mismatches"] == []
    assert pair["quality_equivalent"] is False


@pytest.mark.parametrize("path_form", ("forward_slashes", "relative_with_dot"))
def test_rust_command_accepts_literal_hashed_paths_before_resolving(tmp_path, monkeypatch, path_form):
    from py_swf.benchmarks import command_for
    folder = tmp_path / "tool"
    folder.mkdir()
    executable = folder / "rustswf.exe"
    executable.write_bytes(b"pinned test binary; command construction only")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    monkeypatch.chdir(tmp_path)
    configured_path = executable.as_posix() if path_form == "forward_slashes" else "./tool/rustswf.exe"
    config = tmp_path / "tools.json"
    config.write_text(json.dumps({"development_only": True, "tools": {
        "rust": {"executable": configured_path,
                 "artifact_sha256": {configured_path: digest}}
    }}), encoding="utf-8")
    command = command_for("rust", tool_config=config)
    assert command[command.index("--executable") + 1] == str(executable.resolve())
    assert command[command.index("--sha256") + 1] == digest
