"""Quality campaign provenance, conservative denominators and resumability."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_semantic_benchmarks as runner


def pins():
    return {"implementation_sha256": "source", "runtime": {"sha256": "runtime"},
            "compilers": {"independent_flex": {"sha256": "compiler"}},
            "adapter_files": {"harness": "adapter"}, "limits": {"ticks": 1}}


@pytest.mark.parametrize("dimension", ["source", "tool", "compiler", "runtime", "harness", "input", "scenario", "engine", "manifest"])
def test_cache_key_changes_for_every_semantic_dependency(dimension):
    before = pins()
    before["tool"] = {"sha256": "tool"}
    after = copy.deepcopy(before)
    case, scenario = {"sha256": "input"}, {"ticks": 1, "expected_trace": ["ok"]}
    changed_case, changed_scenario = copy.deepcopy(case), copy.deepcopy(scenario)
    engine, manifest = "native", "corpus"
    if dimension == "source": after["implementation_sha256"] = "changed"
    if dimension == "tool": after["tool"]["sha256"] = "changed"
    if dimension == "compiler": after["compilers"]["independent_flex"]["sha256"] = "changed"
    if dimension == "runtime": after["runtime"]["sha256"] = "changed"
    if dimension == "harness": after["adapter_files"]["harness"] = "changed"
    if dimension == "input": changed_case["sha256"] = "changed"
    if dimension == "scenario": changed_scenario["ticks"] = 2
    if dimension == "engine": engine = "rust"
    if dimension == "manifest": manifest = "changed"
    assert runner.cache_key(before, "corpus", case, scenario, "native") != runner.cache_key(after, manifest, changed_case, changed_scenario, engine)


def test_cache_reuses_failed_evidence_but_rejects_changed_or_missing_bytes(tmp_path):
    key = "k" * 64
    folder = tmp_path / key / "attempt"
    folder.mkdir(parents=True)
    evidence = folder / "generated.as"
    evidence.write_bytes(b'broken generated source\r\n')
    result = {"status": "compilation_failed", "cache": {"key": key, "hit": False}}
    runner.seal_attempt(folder, result)
    hit = runner.cached_result(tmp_path, key)
    assert hit["status"] == "compilation_failed" and hit["cache"]["hit"]
    evidence.write_bytes(b'repaired later')
    assert runner.cached_result(tmp_path, key) is None
    evidence.unlink()
    assert runner.cached_result(tmp_path, key) is None
    assert (folder / "receipt.json").is_file()  # failed evidence retained


def test_selection_does_not_mix_variants_or_expose_other_splits_by_default():
    manifest = {"families": [{"family_id": "development"}, {"family_id": "holdout"}], "cases": []}
    for split in ("development", "holdout"):
        for debug in (False, True):
            for compression in ("FWS", "CWS", "ZWS"):
                manifest["cases"].append({"family_id": split, "split": split, "debug": debug, "compression": compression})
    assert len(runner.select_cases(manifest)) == 1
    assert len(runner.select_cases(manifest, all_variants=True)) == 6
    with pytest.raises(ValueError, match="Unknown"):
        runner.select_cases(manifest, families=["absent"])


def quality_job(runtime, status="compiled"):
    return {"engine": "native", "role": "source_decompiler", "cache": {"key": "key"},
            "input_sha256": "input", "language": "as3", "source": {"status": "complete"},
            "compilations": [{"compiler": "independent_flex", "scope": "complete_generated_project",
                              "complete_project": True, "status": status, "runtime": runtime}]}


def test_compiler_pass_does_not_imply_runtime_pass_or_known_denominator():
    job = quality_job({"status": "runtime_reference_unsupported", "records": [
        {"label": "original", "exact_expected_trace_passed": False},
        {"label": "reconstructed", "exact_expected_trace_passed": True}]})
    runner.enrich_quality(job, pins(), {"ticks": 1})
    compilation = job["compilations"][0]
    assert compilation["projects_total"] == compilation["projects_passed"] == 1
    assert compilation["runtime"]["scenarios_total"] is None
    assert compilation["runtime"]["scenarios_passed"] is None
    assert job["scenarios_scheduled"] == 1
    missing = quality_job({"status": "not_run"}, "not_configured")
    runner.enrich_quality(missing, pins(), {"ticks": 1})
    assert missing["compilations"][0]["projects_passed"] is None


def test_runtime_pass_requires_both_exact_reference_and_candidate():
    runtime = {"status": "passed_bounded_trace", "records": [
        {"label": "original", "exact_expected_trace_passed": True},
        {"label": "reconstructed", "exact_expected_trace_passed": True}]}
    job = quality_job(runtime)
    runner.enrich_quality(job, pins(), {"ticks": 1})
    assert job["compilations"][0]["runtime"]["scenarios_passed"] == 1
    runtime["records"][0]["exact_expected_trace_passed"] = False
    runner.enrich_quality(job, pins(), {"ticks": 1})
    assert job["compilations"][0]["runtime"]["scenarios_passed"] is None


def test_compact_transport_keeps_source_paths_and_runtime_identity_not_large_payloads():
    job = quality_job({"records": [{"label": "original", "input_sha256": "exact", "run": {"stdout": "large", "stderr": "large", "returncode": 1}}]})
    job["source"]["documents"] = [{"source": "engine emitted class", "source_file": "generated.as", "source_sha256": "hash"}]
    compact = runner.compact_quality(job)
    assert compact["source"]["documents"][0] == {"source_file": "generated.as", "source_sha256": "hash"}
    assert compact["compilations"][0]["runtime"]["records"][0]["input_sha256"] == "exact"
    assert compact["compilations"][0]["runtime"]["records"][0]["run"] == {"returncode": 1}
    assert job["source"]["documents"][0]["source"] == "engine emitted class"


def test_source_bytes_preserve_newlines_and_hashes(tmp_path):
    text = "class Emitted {\r\n}\r\n"
    document = {"source": text}
    runner.save_documents([document], tmp_path)
    assert Path(document["source_file"]).read_bytes() == text.encode("utf-8")
    assert document["source_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_missing_class_and_partial_documents_never_admit_project(monkeypatch):
    monkeypatch.setattr(runner, "build_inventory", lambda data: {"units": [{"scope": "class", "document_id": "tag:0/class:0"}, {"scope": "class", "document_id": "tag:0/class:1"}]})
    docs = [{"document_id": "tag:0/class:0", "scope": "class", "source": "generated", "completeness": "complete"}]
    with pytest.raises(ValueError, match="missing"):
        runner.assert_complete_class_set(docs, b'original bytes')
    docs.append({"document_id": "tag:0/class:1", "scope": "class", "source": None, "completeness": "partial"})
    with pytest.raises(ValueError, match="partial"):
        runner.assert_complete_class_set(docs, b'original bytes')


def test_pin_change_rejected_before_execution(tmp_path, monkeypatch):
    binary = tmp_path / "compiler.exe"
    binary.write_bytes(b'pinned')
    record = {"implementation_sha256": "source", "compiler": runner.file_pin(binary)}
    monkeypatch.setattr(runner, "implementation_digest", lambda: "source")
    runner.verify_artifact_pins(record)
    binary.write_bytes(b'changed')
    with pytest.raises(ValueError, match="artifact changed"):
        runner.verify_artifact_pins(record)


def test_resume_does_not_spawn_again_and_source_change_invalidates_cache(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.json").write_text('{}')
    (corpus / "input.swf").write_bytes(b'immutable input')
    case = {"relative": "input.swf", "family_id": "dev", "split": "development", "debug": False,
            "compression": "FWS", "language": "as3", "sha256": runner.fingerprint(corpus / "input.swf"), "expected_trace": ["ok"]}
    monkeypatch.setattr(runner, "read_immutable_manifest", lambda *a, **k: {"families": [{"family_id": "dev"}], "cases": [case]})
    current = pins()
    current["suite_config"] = {"path": str(tmp_path / "tools.json")}
    current["source_tools"] = {"native": {"implementation_sha256": "source"}}
    monkeypatch.setattr(runner, "discover_pins", lambda args: copy.deepcopy(current))
    monkeypatch.setattr(runner, "implementation_digest", lambda: current["implementation_sha256"])
    calls = []
    def execute(command, *args, **kwargs):
        calls.append(command)
        request = json.loads(Path(command[-1]).read_text())
        job = quality_job({"status": "not_run"}, "compilation_failed")
        job.update(case_id="input.swf", family_id="dev", status="evaluated", input_sha256=case["sha256"])
        runner.write_json(Path(request["folder"]) / "worker-result.json", job)
        return {"stdout": "retained", "stderr": "", "returncode": 0, "timed_out": False, "wall_seconds": 0.1,
                "memory_limit_bytes": 2 * 1024 ** 3, "timeout_seconds": 10}
    monkeypatch.setattr(runner, "bounded_run", execute)
    args = SimpleNamespace(corpus=corpus, output=tmp_path / "report", splits=["development"], families=[],
                           all_variants=False, limit=None, engines=["native"], ticks=1, suite_config=None, job_timeout=10, force=False)
    first = runner.run(args)
    second = runner.run(args)
    assert len(calls) == 1 and first["cache_hits"] == 0 and second["cache_hits"] == 1
    assert second["jobs"][0]["compilations"][0]["status"] == "compilation_failed"
    current["implementation_sha256"] = "new-source"
    third = runner.run(args)
    assert len(calls) == 2 and third["cache_hits"] == 0
    transport = json.loads((args.output / "benchmark.json").read_text())
    assert transport["cases"][0]["sha256"] == case["sha256"]
    assert transport["cases"][0]["engines"][0]["quality_evaluation"]["evidence_status"] == "verified"


def test_atomic_report_update_retries_brief_windows_sharing_violation(tmp_path, monkeypatch):
    target = tmp_path / "index.json"
    original = runner.os.replace
    calls = []
    def replace(source, destination):
        calls.append((source, destination))
        if len(calls) == 1:
            raise PermissionError("File is being read")
        return original(source, destination)
    monkeypatch.setattr(runner.os, "replace", replace)
    monkeypatch.setattr(runner.time, "sleep", lambda value: None)
    runner.write_json(target, {"retained": True})
    assert json.loads(target.read_text()) == {"retained": True}
    assert len(calls) == 2


def test_runtime_uses_short_execution_path_and_retains_exact_harness_bytes(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.exe"
    runtime.write_bytes(b'not executed')
    folder = tmp_path / ("cache" * 30) / "runtime-check"
    executions = []
    def probe(fixture, executable, execution):
        executions.append(execution)
        assert not execution.is_relative_to(folder)
        execution.mkdir(parents=True)
        (execution / "test.swf").write_bytes(b'exact runtime input')
        (execution / "output.txt").write_bytes(b'original trace\r\n')
        return {"status": "passed_bounded_trace"}
    monkeypatch.setattr(runner, "probe", probe)
    result = runner.runtime_check(tmp_path / "original.swf", tmp_path / "candidate.swf", {"ticks": 1}, runtime, folder)
    assert result["status"] == "passed_bounded_trace"
    assert (folder / "runtime/test.swf").read_bytes() == b'exact runtime input'
    assert (folder / "runtime/output.txt").read_bytes() == b'original trace\r\n'
    assert not executions[0].exists()


def test_new_program_bootstrap_admission_keeps_all_opcode_guards_and_original_scope(tmp_path, monkeypatch):
    import py_swf.decompile.script_initializers as bootstrap
    body = SimpleNamespace(method=7, init_scope_depth=1)
    script = SimpleNamespace(init=7, traits=[SimpleNamespace(kind_flags=4)])
    abc = SimpleNamespace(scripts=[script], method_bodies=[body])
    unit = SimpleNamespace(abc=abc, tag_path=(6,))
    suite = SimpleNamespace(units=lambda: [unit])
    calls = []
    def proof(received_abc, received_body):
        assert received_abc is abc and received_body is not body
        assert received_body.init_scope_depth == 0
        calls.append(received_body)
        return (0,)
    monkeypatch.setattr(bootstrap, "declaration_order", proof)
    result = runner.prove_declaration_bootstraps(suite, [])
    assert body.init_scope_depth == 1
    assert len(calls) == 1 and result[0]["ambient_scope_equivalence"] == "unverified"
    def executable_call(*args):
        raise ValueError("Script contains executable operation call beyond class declarations")
    monkeypatch.setattr(bootstrap, "declaration_order", executable_call)
    with pytest.raises(ValueError, match="executable operation"):
        runner.prove_declaration_bootstraps(suite, [])
    body.init_scope_depth = 2
    with pytest.raises(ValueError, match="multiple ambient"):
        runner.prove_declaration_bootstraps(suite, [])
    body.init_scope_depth = 1
    script.traits.append(SimpleNamespace(kind_flags=0))
    with pytest.raises(ValueError, match="non-class package"):
        runner.prove_declaration_bootstraps(suite, [])
