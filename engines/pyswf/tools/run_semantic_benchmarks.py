"""One resumable command for generated-source compilation and bounded behavior.

This is a local development campaign, not a scheduled automation or a timing
race. Only generated complete projects/actions reach a compiler. Every compiler
and runtime outcome remains separate, including failures and unavailable tools.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

from py_swf import paths
WORKSPACE = paths.WORKSPACE

from py_swf.benchmark_engines import ENGINES, checked_tool, default_peer_executable, default_rust_executable
from py_swf.benchmark_process import Worker
from py_swf.benchmarks import implementation_digest, reference_command
from py_swf.canonical_inventory import build_inventory
from py_swf.corpus import read_immutable_manifest
from py_swf.suite_tools import ToolRegistry, fingerprint, run_tool
from py_swf.swf_parser import SWFFile
from py_swf.universal import UniversalSuite
from py_swf.validation import validate_swf_bytes
from run_ruffle_trace_probe import bounded_run, probe
from verify_parity_source_semantics import compile_shared_project, generated_project

SOURCE_ENGINES = ("native", "rust", "js", "go", "asm", "jpexs", "pyflashkit", "flare")
REFERENCE_ENGINES = ("rabcdasm", "flasm", "flex_swfdump")
ALL_ENGINES = SOURCE_ENGINES + REFERENCE_ENGINES
SCHEMA_VERSION = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            # Windows readers may temporarily deny delete sharing. Keep the
            # old valid JSON visible and retry its atomic replacement briefly.
            time.sleep(0.04 * (attempt + 1))


def file_pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": fingerprint(path)} if path.is_file() else {
        "path": str(path), "status": "not_configured"}


def pin_tree(root, suffixes):
    root = Path(root)
    return {str(path.resolve()): fingerprint(path) for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix.lower() in suffixes}


def discover_pins(args):
    """Validate existing tool configuration; never install or replace it."""
    registry = ToolRegistry(args.suite_config)
    pins = {
        "schema_version": SCHEMA_VERSION,
        "implementation_sha256": implementation_digest(),
        "python": file_pin(sys.executable),
        "python_version": sys.version,
        "adapter_files": {str(path): fingerprint(path) for path in (
            Path(__file__), PROJECT / "tools/verify_parity_source_semantics.py",
            PROJECT / "tools/run_ruffle_trace_probe.py")},
        "tool_config": file_pin(args.tool_config),
        "reference_config": file_pin(args.reference_config),
        "suite_config": file_pin(registry.path),
        "suite_tools": {}, "source_tools": {}, "compilers": {},
        "runtime": file_pin(args.runtime),
        "limits": {"job_wall_seconds": args.job_timeout,
                   "job_memory_bytes": 2 * 1024 ** 3, "runtime_ticks_max": 120,
                   "runtime_execution_seconds": 15, "runtime_wall_seconds": 30},
    }
    for name in ("java", "flex"):
        try:
            pins["suite_tools"][name] = file_pin(registry.executable(name))
        except (OSError, ValueError) as error:
            pins["suite_tools"][name] = {"status": "not_configured", "diagnostic": str(error)}
    pins["compilers"]["shared_native"] = {"implementation_sha256": pins["implementation_sha256"]}
    flex = pins["suite_tools"].get("flex", {})
    if flex.get("sha256"):
        sdk = Path(flex["path"]).parent.parent
        pins["compilers"]["independent_flex"] = {
            "sdk_artifact_sha256": pin_tree(sdk / "lib", {".jar"}) |
            pin_tree(sdk / "frameworks", {".swc", ".xml", ".properties"}),
            "java": pins["suite_tools"].get("java"), "compiler": flex}
    else:
        pins["compilers"]["independent_flex"] = {"status": "not_configured"}
    pins["compilers"]["independent_rascal"] = file_pin(args.rascal)
    for name in args.engines:
        try:
            if name == "native":
                record = {"implementation_sha256": pins["implementation_sha256"]}
            elif name in ("rust", "js", "go", "asm"):
                binary = args.rust_executable if name == "rust" and args.rust_executable else default_peer_executable(name)
                record = file_pin(binary)
            elif name == "jpexs":
                command = reference_command(args.reference_config)
                from py_swf.development_paths import normalize_development_config
                raw = json.loads(Path(args.reference_config).read_text(encoding="utf-8"))
                config = normalize_development_config(raw, WORKSPACE, Path(args.reference_config).parent)
                record = {"command": command, "artifact_sha256": config["artifact_sha256"],
                          "java": file_pin(command[0]), "config_sha256": digest(config)}
            else:
                config = checked_tool(name, args.tool_config)
                record = {"config": config, "config_sha256": digest(config)}
                if name == "pyflashkit":
                    venv = Path(config["executable"]).parent.parent
                    record["library_sha256"] = pin_tree(venv / "Lib/site-packages/flashkit", {".py", ".pyd"})
            pins["source_tools"][name] = record
        except Exception as error:
            pins["source_tools"][name] = {"status": "not_configured", "diagnostic": str(error)}
    return pins


def select_cases(manifest, *, splits=("development",), families=(), all_variants=False, limit=None):
    known = {f["family_id"] for f in manifest["families"]}
    if set(families) - known:
        raise ValueError("Unknown families: " + ", ".join(sorted(set(families) - known)))
    selected = [case for case in manifest["cases"] if case["split"] in splits and
                (not families or case["family_id"] in families) and
                (all_variants or (not case["debug"] and case["compression"] == "FWS"))]
    return selected[:limit] if limit else selected


def cache_key(pins, manifest_sha256, case, scenario, engine):
    return digest({"pins": pins, "manifest_sha256": manifest_sha256, "case": case,
                   "scenario": scenario, "engine": engine})


def verify_artifact_pins(pins):
    """Check all explicit artifact pins without following arbitrary commands."""
    def walk(value):
        if isinstance(value, dict):
            if value.get("path") and value.get("sha256"):
                if fingerprint(value["path"]) != value["sha256"]:
                    raise ValueError("Pinned artifact changed: " + value["path"])
            for key, child in value.items():
                if isinstance(child, str) and len(child) == 64 and all(char in "0123456789abcdef" for char in child.lower()) and Path(key).is_absolute():
                    if fingerprint(key) != child:
                        raise ValueError("Pinned artifact changed: " + key)
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(pins)
    if implementation_digest() != pins["implementation_sha256"]:
        raise ValueError("Pinned implementation changed before job execution")


def seal_attempt(folder, result):
    """Keep an immutable receipt over every source, candidate, log and harness input."""
    result_path = folder / "result.json"
    write_json(result_path, result)
    artifacts = {path.relative_to(folder).as_posix(): fingerprint(path)
                 for path in sorted(folder.rglob("*")) if path.is_file() and
                 path.name != "receipt.json" and not path.name.endswith(".tmp")}
    write_json(folder / "receipt.json", {"schema_version": SCHEMA_VERSION,
               "cache_key": result["cache"]["key"], "artifacts": artifacts})


def cached_result(root, key):
    receipts = sorted((Path(root) / key).glob("*/receipt.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for receipt_path in receipts:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            folder = receipt_path.parent.resolve()
            if receipt.get("cache_key") != key or "result.json" not in receipt.get("artifacts", {}):
                continue
            for relative, expected in receipt["artifacts"].items():
                path = (folder / relative).resolve()
                if not path.is_relative_to(folder) or not path.is_file() or fingerprint(path) != expected:
                    raise ValueError("Cached evidence missing or changed")
            result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            result["cache"] = {"key": key, "hit": True, "receipt": str(receipt_path)}
            return result
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def save_documents(documents, folder):
    for index, document in enumerate(documents):
        text = document.get("source")
        if text is not None:
            target = folder / "source" / (str(index).zfill(4) + ".as")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
            document.update(source_file=str(target), source_sha256=fingerprint(target))
    return documents


def native_documents(suite, engine):
    documents = []
    for path in sorted({unit.tag_path for unit in suite.units() if unit.abc is not None and not unit.diagnostic}):
        offset = 0
        while True:
            page = suite.code_documents({"tag_path": list(path), "engine": engine,
                                         "include_source": True, "limit": 200, "offset": offset})
            documents.extend(page["items"])
            offset += len(page["items"])
            if offset >= page["total"]:
                break
            if not page["items"]:
                raise ValueError("Source pagination stalled; incomplete projects cannot be compiled")
    return documents


def reference_output(data, pins, folder):
    command = pins["command"]
    with Worker(command, cwd=PROJECT, timeout=110) as worker:
        response = worker.request({"operation": "decompile", "include_source": True,
                                   "swf_base64": base64.b64encode(data).decode("ascii")})
    write_json(folder / "raw-source-response.json", response)
    if not response.get("ok"):
        raise ValueError(response.get("diagnostic") or response.get("error") or "JPEXS source export failed")
    return response


def external_output(engine, data, pins, folder):
    from py_swf.benchmark_external_worker import perform
    config = dict(pins["config"])
    config["evidence_root"] = str(folder / "raw")
    result = perform(engine, config, data, "source" if engine in SOURCE_ENGINES else "disassemble", True)
    write_json(folder / "raw-source-response.json", result)
    return result


def peer_output(engine, data, pins, folder):
    from py_swf.benchmark_rust_worker import RustWorker, perform
    worker = RustWorker(pins["path"], pins["sha256"], timeout=110, engine=engine)
    try:
        result = perform(worker, data, "source", include_source=True)
    finally:
        worker.close()
    write_json(folder / "raw-source-response.json", result)
    return result


def assert_complete_class_set(documents, data):
    inventory = build_inventory(data)
    expected = {unit["document_id"] for unit in inventory["units"] if unit["scope"] == "class"}
    emitted = {document["document_id"] for document in documents if document.get("scope") == "class"}
    if expected != emitted:
        raise ValueError("Generated project class identities differ from original ABC: missing=" +
                         repr(sorted(expected - emitted)) + "; extra=" + repr(sorted(emitted - expected)))
    partial = [document["document_id"] for document in documents if document.get("scope") == "class"
               and (document.get("completeness") != "complete" or document.get("source") is None)]
    if partial:
        raise ValueError("Generated project contains partial documents: " + ", ".join(partial))


def prove_declaration_bootstraps(suite, documents):
    """Class files are a whole project only for proven declaration-only scripts.

    This inspects original ABC structure to reject dropped executable script
    initialization. It never copies original authored text into the candidate.
    """
    from py_swf.decompile.script_initializers import declaration_order
    proofs, seen = [], set()
    for unit in suite.units():
        if unit.abc is None or unit.tag_path in seen:
            continue
        seen.add(unit.tag_path)
        prefix = "tag:" + "/".join(map(str, unit.tag_path))
        for index, script in enumerate(unit.abc.scripts):
            bodies = [body for body in unit.abc.method_bodies if body.method == script.init]
            if len(bodies) != 1:
                raise ValueError("Original script initializer is missing or ambiguous")
            identifier = prefix + "/script:" + str(index)
            if any(trait.kind_flags & 15 != 4 for trait in script.traits):
                raise ValueError("Whole-project adapter does not yet link non-class package declarations: " + identifier)
            body = bodies[0]
            if body.init_scope_depth not in (0, 1):
                raise ValueError("Whole-project adapter cannot classify multiple ambient initializer scopes: " + identifier)
            # Exact ABC edit admission requires initial scope depth zero. This
            # campaign builds a new program from all generated class files.
            # Recognize its declaration-only bootstrap independently of that
            # exact-edit restriction, retaining every opcode/stack/class-install
            # check and rejecting extra calls, writes, branches and handlers.
            # Ambient lexical binding equivalence remains explicitly unverified;
            # only the independent bounded runtime can demonstrate a scenario.
            source_project_body = copy.copy(body)
            source_project_body.init_scope_depth = 0
            order = declaration_order(unit.abc, source_project_body)
            proofs.append({"document_id": identifier, "class_initialization_order": list(order),
                           "original_init_scope_depth": body.init_scope_depth,
                           "ambient_scope_equivalence": "unverified" if body.init_scope_depth else "not_applicable",
                           "admission": "Whole class set with declaration-only bootstrap recreated by compiler; source program admission only, not exact ABC edit admission; no authored text used"})
    return proofs


def compile_flex(project, folder, registry):
    """Compile all emitted class files unchanged, with no authored fallback."""
    for relative, text in project["sources"].items():
        target = (folder / "source" / relative).resolve()
        if not target.is_relative_to((folder / "source").resolve()):
            raise ValueError("Generated source filename escapes project")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    try:
        compiler = Path(registry.executable("flex"))
        java = registry.executable("java")
    except (OSError, ValueError) as error:
        return {"compiler": "independent_flex", "status": "not_configured", "diagnostic": str(error)}
    frameworks = compiler.parent.parent / "frameworks"
    output = folder / "candidate.swf"
    command = [java, "-Djava.awt.headless=true", "-Xmx768m", "-jar", str(compiler),
               "+flexlib=" + str(frameworks), "+playerglobalHome=" + str(frameworks / "libs/player"),
               "-target-player=32.0", "-debug=false", "-omit-trace-statements=false",
               "-output=" + str(output), "-source-path=" + str(folder / "source"),
               str(folder / "source" / project["entry"])]
    record = {"compiler": "independent_flex", "command": command,
              "compiler_sha256": fingerprint(compiler), "source_treatment": "Exact generated class files; no source body repair"}
    try:
        env = registry.environment()
        env["PLAYERGLOBAL_HOME"] = str(frameworks / "libs/player")
        log = run_tool(command, cwd=folder, env=env, timeout=110)
        (folder / "compiler.log").write_text(log, encoding="utf-8")
        validation = validate_swf_bytes(output.read_bytes())
        record.update(status="compiled" if validation["valid"] else "invalid_swf",
                      path=str(output), sha256=fingerprint(output), validation=validation)
    except Exception as error:
        record.update(status="compilation_failed", diagnostic=str(error))
        (folder / "compiler.log").write_text(str(error), encoding="utf-8")
    return record


def compile_rascal(source, folder, binary, registry):
    record = {"compiler": "independent_rascal", "source_treatment": "Exact generated complete root frame source"}
    if not binary or not Path(binary).is_file():
        return {**record, "status": "not_configured", "diagnostic": "Independent Rascal executable unavailable"}
    source_path = folder / "generated.as"
    source_path.write_bytes(source.encode("utf-8"))
    output = folder / "candidate.swf"
    command = [str(binary), "--swf-version", "15", "--frame-rate", "30", "--output", str(output), str(source_path)]
    record.update(command=command, compiler_sha256=fingerprint(binary))
    try:
        log = run_tool(command, cwd=folder, env=registry.environment(), timeout=110)
        (folder / "compiler.log").write_text(log, encoding="utf-8")
        validation = validate_swf_bytes(output.read_bytes())
        record.update(status="compiled" if validation["valid"] else "invalid_swf",
                      path=str(output), sha256=fingerprint(output), validation=validation)
    except Exception as error:
        record.update(status="compilation_failed", diagnostic=str(error))
        (folder / "compiler.log").write_text(str(error), encoding="utf-8")
    return record


def runtime_check(original, candidate, scenario, runtime, folder):
    if not runtime or not Path(runtime).is_file():
        return {"status": "not_configured", "diagnostic": "Pinned headless Ruffle harness unavailable"}
    fixture = folder / "scenario.json"
    write_json(fixture, {**scenario, "original_swf": str(original), "reconstructed_swf": str(candidate)})
    try:
        # The Rust harness opens test assets relative to cwd. A full immutable
        # cache path exceeds Windows' process cwd limit, so execute in a short
        # disposable directory and retain all exact harness bytes afterwards.
        with tempfile.TemporaryDirectory(prefix="fs-trace-") as temporary:
            execution = Path(temporary) / "runtime"
            try:
                result = probe(fixture, Path(runtime), execution)
                result["execution_directory"] = str(execution)
                result["retained_evidence"] = str(folder / "runtime")
                return result
            finally:
                if execution.is_dir():
                    shutil.copytree(execution, folder / "runtime", dirs_exist_ok=True)
    except Exception as error:
        (folder / "runtime-exception.log").write_text(str(error), encoding="utf-8")
        return {"status": "runtime_error", "diagnostic": str(error), "fixture_sha256": fingerprint(fixture)}


def complete_as2_sources(suite, engine, response, data):
    units = list(suite.units())
    if any(unit.diagnostic for unit in units):
        raise ValueError("Original contains an unparsed action/code container; whole-project admission is unavailable")
    roots = [unit for unit in units if unit.language == "as2" and unit.kind in ("frame", "init", "button", "button2", "clip")]
    if not roots:
        raise ValueError("No complete AVM1 action container available")
    records = []
    for unit in roots:
        if unit.diagnostic:
            raise ValueError("Original contains an unparsed AVM1 action container")
        if response is not None:
            selected = [record for record in response.get("units", []) if record.get("document_id") == unit.document_id]
            result = selected[0] if len(selected) == 1 else {}
            source = result.get("source") if result.get("generation") == "available" and result.get("identity_verified") is True else None
            if result.get("bytecode_sha256") != hashlib.sha256(unit.code).hexdigest():
                source = None
        else:
            result = suite.decompile({"unit_id": unit.document_id, "engine": engine})
            source = result.get("source")
        record = {"document_id": unit.document_id, "scope": "action", "kind": unit.kind,
                  "tag_path": list(unit.tag_path), "bytecode_sha256": hashlib.sha256(unit.code).hexdigest(),
                  "source": source, "completeness": "complete" if source is not None else "partial",
                  "diagnostics": result.get("diagnostics", [])}
        records.append(record)
    if any(record["source"] is None for record in records):
        return records, False
    return records, True


def rascal_placement_supported(data, documents):
    movie = SWFFile()
    movie.read_bytes(data)
    if len(documents) != 1 or documents[0]["kind"] != "frame" or len(documents[0]["tag_path"]) != 1:
        return False
    action_index = documents[0]["tag_path"][0]
    return (movie.frame_count == 1 and sum(tag.tag_type == 1 for tag in movie.tags) == 1 and
            not any(tag.tag_type == 39 for tag in movie.tags) and
            not any(tag.tag_type == 1 for tag in movie.tags[:action_index]))


def compile_shared_actions(data, documents, folder, engine):
    from py_swf.unit_editing import edit_unit
    candidate = data
    for document in documents:
        candidate = edit_unit(candidate, {"document_id": document["document_id"],
                              "bytecode_sha256": document["bytecode_sha256"]}, document["source"], source=True)
    output = folder / "candidate.swf"
    output.write_bytes(candidate)
    validation = validate_swf_bytes(candidate)
    return {"compiler": "shared_native", "compiler_delegated": engine != "native", "source_engine": engine,
            "status": "compiled" if validation["valid"] else "invalid_swf", "path": str(output),
            "sha256": fingerprint(output), "validation": validation,
            "mode": "Every original AVM1 action container recompiled from generated source; original non-code tags retained"}


def evaluate_job(request):
    folder = Path(request["folder"])
    pins, engine, case, scenario = (request[key] for key in ("pins", "engine", "case", "scenario"))
    verify_artifact_pins(pins)
    original = folder / "input.swf"
    data = original.read_bytes()
    if fingerprint(original) != case["sha256"]:
        raise ValueError("Pinned job input bytes changed")
    record = {"engine": engine, "role": "source_decompiler" if engine in SOURCE_ENGINES else "disassembly_reference",
              "family_id": case["family_id"], "case_id": case["relative"], "split": case["split"],
              "language": case["language"], "input_sha256": case["sha256"], "scenario": scenario,
              "source": {"status": "not_run", "documents": [], "project_scope": None,
                         "source_fidelity": "not_verified",
                         "completeness_scope": "All generated project declarations/containers present; semantic fidelity is tested separately and bounded"},
              "compilations": [], "claims": {"semantic_parity": False, "superiority": False},
              "provenance_sha256": digest(pins), "status": "not_run"}
    source_pin = pins["source_tools"][engine]
    if source_pin.get("status") == "not_configured":
        return {**record, "status": "not_configured", "diagnostic": source_pin.get("diagnostic")}
    if case["language"] not in ENGINES[engine].languages:
        return {**record, "status": "not_applicable", "diagnostic": "Tool does not support the input language"}
    registry = ToolRegistry(request.get("suite_config"))
    # Use the exact binaries discovered and pinned by the controller.
    for name, pin in pins["suite_tools"].items():
        if pin.get("sha256"):
            registry.tools[name] = {"path": pin["path"], "sha256": pin["sha256"]}
    suite = UniversalSuite(folder / "state", folder / "tools.json")
    suite.registry = registry
    try:
        suite.open(original)
        response = None
        if engine == "rust":
            suite.registry.tools["rust"] = {"path": source_pin["path"], "sha256": source_pin["sha256"]}
        elif engine in ("js", "go", "asm"):
            response = peer_output(engine, data, source_pin, folder)
        elif engine == "jpexs":
            response = reference_output(data, source_pin, folder)
        elif engine not in ("native", "rust"):
            response = external_output(engine, data, source_pin, folder)
        if engine in REFERENCE_ENGINES:
            return {**record, "status": response.get("status", "observed"), "disassembly": response,
                    "diagnostic": "Instruction listings are retained separately; no source compilation quality rank"}
        if case["language"] == "as3":
            if engine in ("native", "rust"):
                documents = native_documents(suite, engine)
            else:
                documents = [dict(unit) for unit in response.get("units", []) if unit.get("scope") in ("class", "script")]
                for document in documents:
                    if "completeness" not in document:
                        document["completeness"] = "complete" if document.get("generation") == "available" and document.get("identity_verified") else "partial"
            record["source"]["documents"] = save_documents(documents, folder)
            try:
                assert_complete_class_set(documents, data)
                record["source"]["script_admission"] = prove_declaration_bootstraps(suite, documents)
                project = generated_project(documents, original)
            except (ValueError, OSError) as error:
                record["source"].update(status="not_supported", diagnostic=str(error))
                record.update(status="not_supported")
                return record
            scope = "complete_generated_project"
            record["source"].update(status="complete", project_scope=scope,
                                    root_class=project["root_class"], source_hashes={key: hashlib.sha256(text.encode("utf-8")).hexdigest() for key, text in project["sources"].items()})
            for compiler in ("shared_native", "independent_flex"):
                job = folder / compiler
                job.mkdir(parents=True)
                try:
                    result = compile_shared_project(project, job, engine) if compiler == "shared_native" else compile_flex(project, job, registry)
                except Exception as error:
                    result = {"compiler": compiler, "status": "compilation_failed", "diagnostic": str(error)}
                result.update(scope=scope, complete_project=True, source_engine=engine,
                              compiler_delegated=compiler == "shared_native" and engine != "native")
                if result["status"] == "compiled":
                    result["runtime"] = runtime_check(original, Path(result["path"]), scenario, pins["runtime"].get("path"), job / "runtime-check")
                else:
                    result["runtime"] = {"status": "not_run", "diagnostic": "Compilation did not produce a validated candidate"}
                record["compilations"].append(result)
        else:
            if engine == "flare":
                from py_swf.flare_source import flare_complete_action_source
                extracted = flare_complete_action_source(data, response)
                write_json(folder / "flare-source-admission.json", extracted)
                if extracted.get("status") != "complete_generated_actions":
                    record["source"].update(status=extracted.get("status", "unverified"), diagnostic=extracted.get("diagnostic"))
                    return {**record, "status": "not_supported"}
                roots = [unit for unit in suite.units() if unit.language == "as2" and unit.kind == "frame"]
                if len(roots) != 1:
                    raise ValueError("Flare admission did not prove exactly one root action")
                unit = roots[0]
                documents = [{"document_id": unit.document_id, "scope": "action", "kind": "frame", "tag_path": list(unit.tag_path),
                              "bytecode_sha256": hashlib.sha256(unit.code).hexdigest(), "source": extracted["source"], "completeness": "complete"}]
                complete = True
                record["source"]["admission"] = {key: value for key, value in extracted.items() if key != "source"}
            else:
                documents, complete = complete_as2_sources(suite, engine, response, data)
            record["source"]["documents"] = save_documents(documents, folder)
            if not complete:
                record["source"].update(status="not_supported", diagnostic="At least one enclosing AVM1 action has no complete generated source")
                return {**record, "status": "not_supported"}
            scope = "complete_generated_actions"
            record["source"].update(status="complete", project_scope=scope)
            for compiler in ("shared_native", "independent_rascal"):
                job = folder / compiler
                job.mkdir(parents=True)
                try:
                    if compiler == "shared_native":
                        result = compile_shared_actions(data, documents, job, engine)
                    elif rascal_placement_supported(data, documents):
                        result = compile_rascal(documents[0]["source"], job, pins["compilers"]["independent_rascal"].get("path"), registry)
                    else:
                        result = {"compiler": compiler, "status": "not_supported", "diagnostic": "Independent Rascal adapter requires one complete root timeline action; event/timeline rebuilding is not implemented"}
                except Exception as error:
                    result = {"compiler": compiler, "status": "compilation_failed", "diagnostic": str(error)}
                result.update(scope=scope, complete_project=True, source_engine=engine,
                              compiler_delegated=compiler == "shared_native" and engine != "native")
                if result["status"] == "compiled":
                    result["runtime"] = runtime_check(original, Path(result["path"]), scenario, pins["runtime"].get("path"), job / "runtime-check")
                else:
                    result["runtime"] = {"status": "not_run", "diagnostic": "Compilation did not produce a validated candidate"}
                record["compilations"].append(result)
        record["status"] = "evaluated"
        return record
    finally:
        suite.close()


def summarize(jobs):
    rows = []
    for engine in ALL_ENGINES:
        entries = [job for job in jobs if job["engine"] == engine]
        if not entries:
            continue
        row = {"engine": engine, "role": entries[0]["role"], "jobs": len(entries),
               "job_status_counts": dict(Counter(job["status"] for job in entries)), "compilers": []}
        for compiler in sorted({entry["compiler"] for job in entries for entry in job.get("compilations", [])}):
            attempts = [entry for job in entries for entry in job.get("compilations", []) if entry["compiler"] == compiler]
            row["compilers"].append({"compiler": compiler, "scheduled_projects": len(entries),
                "evaluated_projects": len(attempts), "compiled_projects": sum(entry["status"] == "compiled" for entry in attempts),
                "compilation_status_counts": dict(Counter(entry["status"] for entry in attempts)),
                "runtime_status_counts": dict(Counter(entry.get("runtime", {}).get("status", "not_run") for entry in attempts)),
                "bounded_trace_passed": sum(entry.get("runtime", {}).get("status") == "passed_bounded_trace" for entry in attempts)})
        rows.append(row)
    return rows


def enrich_quality(result, pins, scenario):
    """Unknown denominators stay null; only a passing reference enables a scenario."""
    result["schema_version"] = SCHEMA_VERSION
    result["evaluation_id"] = result["cache"]["key"]
    result["projects_scheduled"] = 1
    result["scenarios_scheduled"] = 1
    result["scenario_id"] = digest(scenario)
    result["source_eligible"] = result["role"] == "source_decompiler"
    result["scope_id"] = result["input_sha256"] + ":generated-project"
    result["scope"] = {"kind": "complete_project" if result.get("language") == "as3" else "complete_actions",
                       "id": result["scope_id"], "complete": result.get("source", {}).get("status") == "complete"}
    result["comparison_key"] = digest({"input_sha256": result["input_sha256"], "scenario": scenario,
                                      "compilers": pins["compilers"], "runtime": pins["runtime"],
                                      "harness": pins["adapter_files"], "limits": pins["limits"]})
    result["context_valid"] = True
    result["evidence_status"] = "verified"
    result["context"] = {"comparison_key": result["comparison_key"], "runtime_sha256": pins["runtime"].get("sha256"),
                         "scenario_sha256": digest(scenario), "provenance_sha256": digest(pins),
                         "performance_comparison_eligible": False}
    for compilation in result.get("compilations", []):
        complete = compilation.get("complete_project") is True
        compilation["projects_total"] = 1 if complete else None
        compilation["projects_passed"] = int(compilation["status"] == "compiled") if complete and compilation["status"] not in ("not_configured", "not_supported", "unverified") else None
        runtime = compilation.setdefault("runtime", {"status": "not_run"})
        original = [entry for entry in runtime.get("records", []) if entry.get("label") == "original"]
        rebuilt = [entry for entry in runtime.get("records", []) if entry.get("label") == "reconstructed"]
        reference_passed = len(original) == 1 and original[0].get("exact_expected_trace_passed") is True
        eligible = complete and compilation["status"] == "compiled" and reference_passed
        runtime["reference_status"] = "passed" if reference_passed else "failed" if original else "unverified"
        runtime["scenarios_total"] = 1 if eligible else None
        runtime["scenarios_passed"] = int(len(rebuilt) == 1 and rebuilt[0].get("exact_expected_trace_passed") is True and runtime.get("status") == "passed_bounded_trace") if eligible else None
    return result


def compact_quality(job):
    """Catalog metadata links large logs/source through sealed evidence files."""
    def compact(value):
        if isinstance(value, list):
            return [compact(child) for child in value]
        if isinstance(value, dict):
            return {key: compact(child) for key, child in value.items() if key not in
                    ("source", "pcode", "source_text", "stdout", "stderr", "raw_sources", "disassembly")}
        return value
    result = compact(job)
    # The source object is metadata; only its per-document source text is omitted.
    result["source"] = {key: compact(value) for key, value in job.get("source", {}).items()}
    return result


def export_benchmark(report, cases, output):
    """Schema-1 transport accepted by the existing read-only local catalog."""
    from dataclasses import asdict
    rows = []
    for case in cases:
        engines = []
        for job in report["jobs"]:
            if job["case_id"] != case["relative"]:
                continue
            quality = compact_quality(job)
            quality["context_valid"] = report.get("implementation_unchanged") is True and report.get("pins_unchanged") is True
            if not quality["context_valid"]:
                quality["evidence_status"] = "invalidated_context_changed"
            engines.append({"engine": job["engine"], "operation": "source" if job["role"] == "source_decompiler" else "disassemble",
                            "family_id": case["family_id"], "split": case["split"],
                            "adapter_mode": "generated_complete_project_v1", "valid_performance_summary": False,
                            "benchmark_role": job["role"],
                            "status": job["status"], "quality_evaluation": quality,
                            "cold_samples": [], "hot_samples": [], "warmup_samples": [],
                            "compilation": "see_quality_evaluation", "behavior": "see_quality_evaluation",
                            "tool_fingerprint": {"version": ENGINES[job["engine"]].name,
                                "executable_sha256": digest(report["provenance"]["source_tools"][job["engine"]])}})
        rows.append({**case, "source_family": case["family_id"], "engines": engines})
    transport = {"schema_version": 1, "created_at": report["created_utc"], "label": "Generated source compile and bounded behavior",
                 "profile": "source_semantics", "operation": "source", "measurement_context": report["performance"],
                 "provenance": {"implementation_sha256_before": report["provenance"]["implementation_sha256"],
                                "implementation_unchanged": report.get("implementation_unchanged"),
                                "source_semantics_index": str(output / "semantic-index.json"),
                                "source_semantics_index_sha256": fingerprint(output / "semantic-index.json")},
                 "engine_catalog": [asdict(ENGINES[engine]) for engine in ALL_ENGINES], "cases": rows,
                 "claims": report["claims"], "scheduled_files": len(cases), "completed_files": len(rows)}
    write_json(output / "benchmark.json", transport)
    return transport


def run(args):
    corpus, output = args.corpus.resolve(), args.output.resolve()
    manifest = read_immutable_manifest(corpus, verify_sources=False)
    cases = select_cases(manifest, splits=args.splits, families=args.families, all_variants=args.all_variants, limit=args.limit)
    if not cases:
        raise ValueError("No selected corpus cases")
    output.mkdir(parents=True, exist_ok=True)
    cache_root = output / "cache"
    pins = discover_pins(args)
    manifest_sha = fingerprint(corpus / "manifest.json")
    pin_id = digest(pins)
    write_json(output / "pins" / (pin_id + ".json"), pins)
    report = {"schema_version": SCHEMA_VERSION, "run_id": uuid.uuid4().hex,
              "created_utc": datetime.now(timezone.utc).isoformat(), "kind": "source_semantics_campaign",
              "corpus": str(corpus), "corpus_manifest_sha256": manifest_sha,
              "provenance_sha256": pin_id, "provenance": pins,
              "selection": {"splits": args.splits, "families": args.families, "all_variants": args.all_variants},
              "scheduled_jobs": len(cases) * len(args.engines), "jobs": [], "engine_summary": [],
              "claims": {"semantic_parity": False, "superiority": False, "blind_holdout": False},
              "scope": "Generated complete projects/actions only; exact expected traces under declared bounded scenarios; no rendering/audio/video/network or general equivalence claim",
              "holdout_caveat": "Corpus split labels retained for provenance; prior exposure prevents a blind holdout claim",
              "performance": {"status": "not_measured", "reason": "Serialized quality campaign; compiler/runtime durations are operational logs, not comparative speed measurements"}}
    write_json(output / "semantic-index.json", report)
    for case in cases:
        scenario = {"expected_trace": case["expected_trace"], "ticks": args.ticks}
        for engine in args.engines:
            key = cache_key(pins, manifest_sha, case, scenario, engine)
            result = None if args.force else cached_result(cache_root, key)
            if result is None:
                folder = cache_root / key / uuid.uuid4().hex
                folder.mkdir(parents=True)
                (folder / "input.swf").write_bytes((corpus / case["relative"]).read_bytes())
                request = {"folder": str(folder), "pins": pins, "case": case, "scenario": scenario,
                           "engine": engine, "suite_config": pins["suite_config"]["path"]}
                write_json(folder / "request.json", request)
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(folder / "request.json")]
                started = time.perf_counter()
                process = bounded_run(command, PROJECT, timeout=args.job_timeout)
                (folder / "worker.stdout.log").write_text(process["stdout"], encoding="utf-8")
                (folder / "worker.stderr.log").write_text(process["stderr"], encoding="utf-8")
                result_path = folder / "worker-result.json"
                result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {
                    "engine": engine, "family_id": case["family_id"], "case_id": case["relative"], "language": case["language"],
                    "role": "source_decompiler" if engine in SOURCE_ENGINES else "disassembly_reference",
                    "input_sha256": case["sha256"], "source": {"status": "unverified", "documents": [], "project_scope": None},
                    "compilations": [], "status": "timeout" if process["timed_out"] else "failed",
                    "diagnostic": process["stderr"][-8000:], "claims": {"semantic_parity": False, "superiority": False}}
                result.update(cache={"key": key, "hit": False}, evidence=str(folder),
                              worker={**{name: process[name] for name in ("returncode", "timed_out", "wall_seconds", "memory_limit_bytes", "timeout_seconds")},
                                      "command": command}, completed_utc=datetime.now(timezone.utc).isoformat())
                result["elapsed_seconds"] = time.perf_counter() - started
                enrich_quality(result, pins, scenario)
                seal_attempt(folder, result)
            report["jobs"].append(result)
            report["engine_summary"] = summarize(report["jobs"])
            report["completed_jobs"] = len(report["jobs"])
            report["cache_hits"] = sum(job["cache"]["hit"] for job in report["jobs"])
            write_json(output / "semantic-index.json", report)
            print(case["family_id"], engine, result["status"], "cache" if result["cache"]["hit"] else "executed", flush=True)
    report["status"] = "completed_bounded_campaign"
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    report["implementation_sha256_after"] = implementation_digest()
    report["implementation_unchanged"] = report["implementation_sha256_after"] == pins["implementation_sha256"]
    report["pins_unchanged"] = digest(discover_pins(args)) == pin_id
    if not report["implementation_unchanged"] or not report["pins_unchanged"]:
        report["status"] = "invalidated_source_changed"
    write_json(output / "runs" / (report["run_id"] + ".json"), report)
    write_json(output / "semantic-index.json", report)
    export_benchmark(report, cases, output)
    return report


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        request_path = Path(sys.argv[2]).resolve(strict=True)
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = evaluate_job(request)
        write_json(Path(request["folder"]) / "worker-result.json", result)
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=paths.CORPUS / "parity-20261014/optimized")
    parser.add_argument("--output", type=Path, default=WORKSPACE / "reports/semantic-benchmarks-20261014")
    parser.add_argument("--engines", nargs="+", choices=ALL_ENGINES, default=list(ALL_ENGINES))
    parser.add_argument("--splits", nargs="+", choices=("development", "validation", "holdout"), default=["development"])
    parser.add_argument("--families", nargs="*", default=[])
    parser.add_argument("--all-variants", action="store_true", help="Select debug/compression variants explicitly; never treat variants as independent families")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true", help="Create new immutable attempts; retain previous successful and failed evidence")
    parser.add_argument("--ticks", type=int, default=1)
    parser.add_argument("--job-timeout", type=int, default=240)
    parser.add_argument("--rust-executable", type=Path)
    parser.add_argument("--suite-config", type=Path)
    parser.add_argument("--tool-config", type=Path, default=paths.REFERENCES / "extra-tools.json")
    parser.add_argument("--reference-config", type=Path, default=paths.REFERENCES / "toolchain.json")
    parser.add_argument("--runtime", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home())) /
                        "FlashParityRuntime/ruffle-target/x86_64-pc-windows-gnu/debug/deps/tests-470feeee72990987.exe")
    parser.add_argument("--rascal", type=Path, default=paths.REFERENCES / "extra-tools/rascal-0.3.5/bin/rascal.exe")
    args = parser.parse_args()
    if not 1 <= args.ticks <= 120 or args.job_timeout < 1 or args.limit is not None and args.limit < 1:
        parser.error("ticks must be 1..120; timeout and limit must be positive")
    report = run(args)
    print(json.dumps({key: report.get(key) for key in ("status", "scheduled_jobs", "completed_jobs", "cache_hits", "implementation_unchanged")}, indent=2))
    return 0 if report["status"] == "completed_bounded_campaign" else 2


if __name__ == "__main__":
    raise SystemExit(main())
