"""Robustness: which units survive a damage, how a run on a damaged file is classified, what the score is."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bench_robustness as robustness  # noqa: E402
from run_ruffle_trace_probe import bounded_run  # noqa: E402

from py_swf import paths  # noqa: E402
from py_swf.bench_results import OUTCOME_SCORES, OUTCOMES, robustness_score  # noqa: E402
from py_swf.canonical_inventory import build_inventory  # noqa: E402
from py_swf.closeness import canonical_units  # noqa: E402
from py_swf.corpus import read_damaged_manifest  # noqa: E402
from py_swf.robustness import abnormal_exit, classify, decompressed, intact_units, internal_error, tag_spans  # noqa: E402

CLASSES = paths.CORPUS / "parity-20261014/optimized/swf/dev-as3-classes-namespaces/release.fws.swf"
TIMELINE = paths.CORPUS / "parity-20261014/optimized/swf/dev-as2-timeline/release.fws.swf"
needs_corpus = pytest.mark.skipif(not CLASSES.is_file(), reason="sealed parity corpus unavailable")


# --- the score -------------------------------------------------------------------------------------------------------

def test_the_five_outcomes_are_worth_what_the_documentation_says():
    assert OUTCOMES == ("recovered", "partial", "clean_error", "crash", "hang")
    assert OUTCOME_SCORES == {"recovered": 100, "partial": 60, "clean_error": 40, "crash": 0, "hang": 0}


def test_the_robustness_score_is_the_mean_value_of_the_outcomes():
    assert robustness_score({"recovered": 2, "crash": 2}) == 50.0
    assert robustness_score({"recovered": 1, "partial": 1, "clean_error": 1, "crash": 1, "hang": 1}) == 40.0
    assert robustness_score({"clean_error": 51}) == 40.0 and robustness_score({"hang": 3}) == 0.0
    assert robustness_score({"recovered": 4}) == 100.0
    assert robustness_score({}) is None and robustness_score({"recovered": 0}) is None, "no file, no score"
    assert robustness_score({"partial": 1, "recovered": 2}) == round((60 + 200) / 3, 1)


# --- which units are still intact -------------------------------------------------------------------------------------

@needs_corpus
def test_a_unit_is_intact_when_no_damaged_range_touches_its_bytes_or_its_structure():
    data = CLASSES.read_bytes()
    inventory = build_inventory(data)
    methods = {u["document_id"]: u for u in inventory["units"] if u["scope"] == "method"}
    everything = intact_units(data, [])
    assert everything["total_documents"] == 5 and len(everything["documents"]) == 5
    assert everything["total_methods"] == 11 and len(everything["methods"]) == 11
    assert intact_units(data, [[0, len(data)]])["documents"] == set(), "a file lost to the end has nothing intact"

    # one byte inside a method body: that method, its class and its script are lost, the rest survive
    add = methods["tag:6/method:5"]
    hit = intact_units(data, [[add["bytecode_offset"], add["bytecode_offset"] + 1]])
    assert "tag:6/method:5" not in hit["methods"] and len(hit["methods"]) == 10
    assert ("tag:6/class:1", "class") not in hit["documents"] and ("tag:6/script:1", "script") not in hit["documents"]
    assert ("tag:6/class:0", "class") in hit["documents"] and ("tag:6/script:0", "script") in hit["documents"]

    # one byte in the constant pool, shared by every unit of the DoABC tag, takes all of them
    block = inventory["abc_blocks"][0]
    assert intact_units(data, [[block["byte_offset"] + 20, block["byte_offset"] + 21]])["documents"] == set()
    # a byte of the tag record header too
    spans = tag_spans(decompressed(data))[0]
    assert intact_units(data, [[spans[(6,)][0], spans[(6,)][0] + 1]])["documents"] == set()
    # but damage after the code (the SymbolClass tag, the end of the file) leaves every unit intact
    assert len(intact_units(data, [[spans[(7,)][1], len(data)]])["documents"]) == 5
    # and so does a wrong file length in the header: it is not in any unit
    assert intact_units(data, [[4, 8]])["documents"] == everything["documents"]


@needs_corpus
def test_an_action_is_lost_with_its_tag_and_found_otherwise():
    data = TIMELINE.read_bytes()
    spans = tag_spans(decompressed(data))[0]
    record, payload, end, code = spans[(2,)]
    assert code == 12
    assert intact_units(data, [])["documents"] == {("tag:2/actions", "action")}
    assert intact_units(data, [[end - 1, end]])["documents"] == set()
    assert intact_units(data, [[spans[(1,)][1], spans[(1,)][2]]])["documents"] == {("tag:2/actions", "action")}, "damage in another tag"


def test_the_corpus_manifest_ranges_leave_something_to_recover_in_most_cases():
    manifest = read_damaged_manifest(paths.CORPUS / "damaged")
    recoverable = 0
    for case in manifest["cases"]:
        found = intact_units((paths.CORPUS / case["source"]).read_bytes(), case["damage"]["ranges"])
        recoverable += bool(found["documents"])
    assert 20 <= recoverable < len(manifest["cases"]), "some cases leave nothing to recover (the header cut) and many leave a part"


# --- the five outcomes ------------------------------------------------------------------------------------------------

def document(identity, scope="class", has=True, verified=True):
    return {"document_id": identity, "scope": scope, "has_source": has, "verified": verified}


INTACT = {"documents": {("tag:6/class:0", "class")}, "methods": {"tag:6/method:1"}}
RAN = {"timed_out": False, "returncode": 0, "stderr": ""}


def watch(**fields):
    return {"documents": [], "methods": [], "diagnostics": [], "failure": None, **fields}


def test_a_timeout_is_a_hang_whatever_else_was_seen():
    assert classify({**RAN, "timed_out": True}, watch(documents=[document("tag:6/class:0")]), (), INTACT)[0] == "hang"
    assert classify(RAN, watch(failure={"kind": "timeout", "message": "deadline exceeded"}), (), INTACT)[0] == "hang"


def test_a_process_that_left_no_result_or_fell_over_is_a_crash():
    assert classify({**RAN, "returncode": 1, "stderr": "Traceback (most recent call last)\nIndexError"}, None, (), INTACT)[0] == "crash"
    assert classify(RAN, watch(failure={"kind": "crash", "message": "worker exited without JSON"}), (), INTACT)[0] == "crash"
    outcome, why = classify(RAN, watch(failure={"kind": "error", "message": "java.lang.NullPointerException at x"}), (), INTACT)
    assert outcome == "crash" and "NullPointer" in why, "an error text that is a stack trace is an unhandled exception"
    assert classify({**RAN, "returncode": 0xC0000005}, watch(failure={"kind": "error", "message": "boom"}), (), INTACT)[0] == "crash"
    assert classify(RAN, watch(diagnostics=["IndexError: list index out of range"]), (), INTACT)[0] == "crash", "no output and only an internal error to say"


def test_no_output_with_a_clear_message_is_a_clean_error():
    assert classify(RAN, watch(failure={"kind": "error", "message": "Invalid SWF file: header too short"}), (), INTACT) == ("clean_error", "Invalid SWF file: header too short")
    assert classify(RAN, watch(diagnostics=["tag 6: truncated"]), (), INTACT)[0] == "clean_error"
    outcome, why = classify(RAN, watch(), (), INTACT)
    assert (outcome, why) == ("clean_error", "sin salida ni mensaje"), "the closest of the five when it neither fails nor explains"


def test_output_for_the_intact_parts_and_a_new_message_about_the_damage_is_recovered():
    seen = watch(documents=[document("tag:6/class:0")], diagnostics=["tag 6: bad opcode"])
    assert classify(RAN, seen, (), INTACT) == ("recovered", "tag 6: bad opcode")
    by_method = watch(methods=[{"document_id": "tag:6/method:1", "scope": "method", "has_source": True, "verified": True}], diagnostics=["tag 6: bad opcode"])
    assert classify(RAN, by_method, (), INTACT)[0] == "recovered", "method-level output covers the intact parts too"
    mid_way = watch(documents=[document("tag:6/class:0")], failure={"kind": "error", "message": "unit 7 failed"})
    assert classify(RAN, mid_way, (), INTACT)[0] == "recovered", "an error after some output is a report"


def test_a_message_the_engine_also_gives_for_the_undamaged_file_is_not_a_report():
    routine = "tag:6/method:2: complex control flow"
    seen = watch(documents=[document("tag:6/class:0")], diagnostics=[routine])
    assert classify(RAN, seen, [routine], INTACT)[0] == "partial"
    assert classify(RAN, seen, [], INTACT)[0] == "recovered"
    assert classify(RAN, {**seen, "diagnostics": [routine, "new"]}, [routine], INTACT)[0] == "recovered"


def test_any_other_output_is_partial_whatever_the_reason():
    silent = watch(documents=[document("tag:6/class:0")])
    assert classify(RAN, silent, (), INTACT) == ("partial", "sin aviso del daño")
    elsewhere = watch(documents=[document("tag:6/class:9")], diagnostics=["tag 6: bad opcode"])
    assert classify(RAN, elsewhere, (), INTACT) == ("partial", "sin salida para lo intacto")
    unverified = watch(documents=[document("tag:6/class:0", verified=False)], diagnostics=["bad"])
    assert classify(RAN, unverified, (), INTACT)[0] == "partial", "output that is not the original's unit does not recover it"
    assert classify(RAN, watch(documents=[document("tag:6/class:0")], diagnostics=["bad"]), (), None)[0] == "partial", "nothing known about the intact units"
    empty = watch(documents=[document("tag:6/class:0", has=False)], diagnostics=["bad"])
    assert classify(RAN, empty, (), INTACT)[0] == "clean_error", "a document without source is no output"


def test_stack_traces_and_faults_are_told_from_ordinary_messages():
    for text in ("Traceback (most recent call last):\n  File x", "IndexError: list index out of range", "java.lang.ArrayIndexOutOfBoundsException: 4",
                 "Error\n\tat com.jpexs.decompiler.flash.SWF.parse(SWF.java:123)", "thread 'main' panicked at src/lib.rs:10", "panic: runtime error: index out of range",
                 "goroutine 1 [running]:", "TypeError: Cannot read properties of undefined (reading 'x')", "Error\n    at run (file:///x.mjs:10:5)",
                 "index out of bounds: the len is 3 but the index is 7", "Maximum call stack size exceeded"):
        assert internal_error(text), text
    for text in ("Invalid SWF file: header too short", "FWS length differs from declared length", "Tag 389 exceeds its container at offset 1233",
                 "Unknown trait kind 14", "Zlib decompression failed: zlib: invalid checksum", "com.jpexs.decompiler.flash.SwfOpenException: SWF header is too short.",
                 "abc_invalid: Unknown multiname kind 4", ""):
        assert not internal_error(text), text
    assert abnormal_exit(0xC0000005) and abnormal_exit(-1) and abnormal_exit(3221225477)
    assert not abnormal_exit(0) and not abnormal_exit(1) and not abnormal_exit(2) and not abnormal_exit(None)


# --- the worker side: what counts as a controlled error ----------------------------------------------------------------

def test_only_deliberate_errors_are_controlled_python_errors():
    import zlib
    assert robustness.controlled(ValueError("bad")) and robustness.controlled(EOFError("short"))
    assert not robustness.controlled(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")), "a decoding error is ValueError by inheritance only"
    assert not robustness.controlled(IndexError("x")) and not robustness.controlled(zlib.error("Error -3"))
    assert robustness.explain(ValueError("Invalid header")) == ("error", "Invalid header")
    kind, message = robustness.explain(zlib.error("Error -3 while decompressing"))
    assert kind == "crash" and message.startswith("zlib.error: "), "the module is part of the name"
    assert robustness.explain(IndexError("index out of range"))[0] == "crash"
    assert robustness.explain(subprocess.TimeoutExpired("x", 1))[0] == "timeout"
    from py_swf.benchmark_process import WorkerError
    from py_swf.benchmark_rust_worker import RustOperationError, RustProtocolError
    assert robustness.explain(RustOperationError("no such unit"))[0] == "error"
    assert robustness.explain(RustProtocolError("Rust worker exited without JSON (exit=101): panicked"))[0] == "crash"
    assert robustness.explain(RustProtocolError("Rust benchmark operation deadline exceeded"))[0] == "timeout"
    assert robustness.explain(WorkerError("Operation timed out after 30 seconds"))[0] == "timeout"
    assert robustness.last_exception("Traceback...\n  File x\nflashkit.errors.ABCParseError: Corrupted ABC data") == ("flashkit.errors.ABCParseError", "Corrupted ABC data")
    assert robustness.clean("Failed to parse 'C:\\Users\\x\\Temp\\fs-1\\input.swf'  now") == "Failed to parse 'input.swf' now"


# --- the harness itself: a crash or a hang is one more outcome ---------------------------------------------------------

def test_a_process_that_sleeps_past_its_timeout_is_killed_and_classified_as_a_hang():
    process = bounded_run([sys.executable, "-c", "import time; time.sleep(60)"], Path.cwd(), timeout=2)
    assert process["timed_out"] and process["wall_seconds"] < 30
    assert classify(process, None, (), INTACT)[0] == "hang"


def test_a_process_that_dies_without_a_result_is_a_crash_and_its_memory_is_measured():
    process = bounded_run([sys.executable, "-c", "x = bytearray(40 * 1024 * 1024); import sys; sys.exit(3)"], Path.cwd(), timeout=30)
    assert process["returncode"] == 3 and not process["timed_out"]
    assert classify(process, None, (), INTACT)[0] == "crash"
    assert process["peak_memory_bytes"] is None or process["peak_memory_bytes"] > 40 * 1024 ** 2, "the job reports the peak of the whole tree"


# --- verifying what an engine emitted against the original -------------------------------------------------------------

@needs_corpus
def test_documents_count_only_when_their_identity_and_bytecode_are_the_originals():
    data = CLASSES.read_bytes()
    canonical, inventory = canonical_units(data), build_inventory(data)
    sha = canonical[("tag:6/method:5", "method")]["sha"]
    class_sha = canonical[("tag:6/class:1", "class")]["sha"]
    methods = canonical[("tag:6/class:1", "class")]["methods"]
    seen = {"engine": "jpexs", "method_hashes": {}, "methods": [
        {"document_id": "tag:6/method:5", "scope": "method", "has_source": True, "bytecode_sha256": sha},
        {"document_id": "tag:6/method:7", "scope": "method", "has_source": True, "bytecode_sha256": "wrong"}],
        "documents": [
            {"document_id": "tag:6/class:1", "scope": "class", "has_source": True, "bytecode_sha256": class_sha, "unit_ids": None},
            {"document_id": "tag:6/class:0", "scope": "class", "has_source": True, "bytecode_sha256": "wrong", "unit_ids": None},
            {"document_id": "tag:6/class:9", "scope": "class", "has_source": True, "bytecode_sha256": class_sha, "unit_ids": None}]}
    checked = robustness.verify(seen, canonical, inventory)
    assert {m["document_id"]: m["verified"] for m in checked["methods"]} == {"tag:6/method:5": True, "tag:6/method:7": False}
    assert {d["document_id"]: d["verified"] for d in checked["documents"]} == {"tag:6/class:1": True, "tag:6/class:0": False, "tag:6/class:9": False}
    # PySWF names the methods of a class instead of hashing them
    native = {"engine": "native", "methods": [], "method_hashes": {}, "documents": [
        {"document_id": "tag:6/class:1", "scope": "class", "has_source": True, "bytecode_sha256": None, "unit_ids": sorted(methods)},
        {"document_id": "tag:6/class:0", "scope": "class", "has_source": True, "bytecode_sha256": None, "unit_ids": ["tag:6/method:99"]}]}
    assert [d["verified"] for d in robustness.verify(native, canonical, inventory)["documents"]] == [True, False]
    # RustSWF emits classes: they count when it listed every method of the class with the original's hashes, and its scripts are composed from them
    owned = set(canonical[("tag:6/class:1", "class")]["methods"]) | set(canonical[("tag:6/class:0", "class")]["methods"])
    rust = {"engine": "rust", "methods": [], "method_hashes": {name: canonical[(name, "method")]["sha"] for name in owned}, "documents": [
        {"document_id": "tag:6/class:1", "scope": "class", "has_source": True, "bytecode_sha256": None, "unit_ids": None},
        {"document_id": "tag:6/class:0", "scope": "class", "has_source": True, "bytecode_sha256": None, "unit_ids": None}]}
    documents = {d["document_id"]: d for d in robustness.verify(rust, canonical, inventory)["documents"]}
    assert documents["tag:6/class:1"]["verified"] and documents["tag:6/class:0"]["verified"]
    assert documents["tag:6/script:1"]["verified"] and documents["tag:6/script:0"]["verified"], "a script is the classes it holds"
    assert "tag:6/script:2" not in documents, "a script without classes is not composed"
    # Flare writes one text for the whole movie: it is the root action of a movie that has exactly one
    timeline = TIMELINE.read_bytes()
    flare = {"engine": "flare", "methods": [], "method_hashes": {}, "documents": [{"document_id": None, "scope": "consolidated", "has_source": True}]}
    assert robustness.verify(flare, canonical_units(timeline), build_inventory(timeline))["documents"][0]["document_id"] == "tag:2/actions"
    assert robustness.verify(flare, canonical, inventory)["documents"] == [], "a movie with no action has none to attribute it to"


# --- the table ----------------------------------------------------------------------------------------------------------

def row(engine, case, outcome, kind="flip_code", measured=True, seconds=0.1, recovered=0, intact=0, memory=50.0):
    return {"engine": engine, "family": "f", "case": case, "kind": kind, "language": "as3", "source": "s", "measured": measured, "outcome": outcome if measured else None,
            "reason": "" if measured else "no lee AS3", "seconds": seconds if measured else None, "peak_memory_mib": memory if measured else None,
            "intact_units": intact if measured else None, "recovered_units": recovered if measured else None, "reported": None}


def test_the_table_scores_each_engine_by_kind_and_ranks_only_engines_measured_on_the_same_files():
    rows = ([row("native", "a", "recovered", recovered=2, intact=2), row("native", "b", "crash", kind="bad_length"), row("native", "c", "partial", recovered=1, intact=2)]
            + [row("rust", "a", "clean_error"), row("rust", "b", "clean_error", kind="bad_length"), row("rust", "c", "clean_error")]
            + [row("flare", "a", "hang"), row("flare", "b", "clean_error", kind="bad_length"), row("flare", "c", None, measured=False)])
    result = robustness.table(rows)
    by = {line["engine"]: line for line in result["engines"]}
    assert by["native"]["robustness"] == round((100 + 0 + 60) / 3, 1) and by["rust"]["robustness"] == 40.0 and by["flare"]["robustness"] == 20.0
    assert by["native"]["outcomes"] == {"recovered": 1, "partial": 1, "clean_error": 0, "crash": 1, "hang": 0}
    assert by["native"]["by_kind"]["bad_length"]["score"] == 0.0 and by["native"]["by_kind"]["flip_code"]["cases"] == 2
    assert by["native"]["recoverable_cases"] == 2 and by["native"]["recovered_units"] == 3 and by["native"]["intact_units"] == 4
    assert by["native"]["unit_recovery"] == (1.0 + 0.5) / 2, "the mean over the files with something intact"
    assert by["rust"]["unit_recovery"] is None
    assert [by[e]["comparable"] for e in ("native", "rust", "flare")] == [True, True, False], "flare read two of the three files"
    assert by["flare"]["cases"] == 2 and by["flare"]["reasons"] == ["no lee AS3"]
    assert [line["engine"] for line in result["engines"]] == ["native", "rust", "flare"]
    assert result["winner"] is None and "sin ganador" in result["note"], "a lead of under one file is noise"
    assert result["corpus_cases"] == 3 and set(result["kinds"]) == set(robustness.KIND_LABELS)
    assert by["native"]["peak_memory_mib"] == 50.0 and by["native"]["median_seconds"] == 0.1


def test_a_clear_lead_names_the_most_robust_engine():
    rows = ([row("native", str(i), "recovered") for i in range(10)] + [row("rust", str(i), "clean_error") for i in range(10)])
    assert robustness.table(rows)["winner"] == "native"


# --- the whole thing on a real file ---------------------------------------------------------------------------------------

@needs_corpus
def test_pyswf_on_a_real_damaged_file_goes_through_its_own_process_and_comes_back_classified(tmp_path):
    manifest = read_damaged_manifest(paths.CORPUS / "damaged")
    case = dict(next(c for c in manifest["cases"] if c["id"] == "truncate-header-dev-as2-timeline"), path=str(paths.CORPUS / "damaged" / "truncate-header-dev-as2-timeline.swf"))
    done = robustness.evaluate("native", {}, case, paths.CORPUS / case["source"], tmp_path, 60, {}, threading.Lock())
    assert done["outcome"] == "clean_error" and "header" in done["reason"], done
    assert done["measured"] and done["intact_units"] == 0 and done["seconds"] is not None
    assert done["peak_memory_mib"] is None or done["peak_memory_mib"] > 1
    skipped = robustness.evaluate("flare", {}, dict(case, language="as3"), paths.CORPUS / case["source"], tmp_path, 60, {}, threading.Lock())
    assert skipped["measured"] is False and skipped["reason"] == "no lee AS3"
    missing = robustness.evaluate("rust", {"status": "not_configured", "diagnostic": "absent"}, case, paths.CORPUS / case["source"], tmp_path, 60, {}, threading.Lock())
    assert missing["measured"] is False and missing["reason"].startswith("no instalado")
    assert json.loads(json.dumps(done)) == done, "a row is plain JSON"


@needs_corpus
def test_an_unknown_engine_is_a_clean_failure_of_the_worker_not_a_dead_benchmark(tmp_path):
    process, observation = robustness.run_observation("bogus", {}, TIMELINE, tmp_path, 30)
    assert observation["failure"]["kind"] == "error" and "bogus" in observation["failure"]["message"]
