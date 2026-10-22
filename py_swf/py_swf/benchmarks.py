"""Development-only decompilation benchmarks. Availability is NOT semantic parity."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
from itertools import combinations

from .benchmark_process import Worker, WorkerError
from .benchmark_engines import ENGINES, describe_engines, external_command

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {"smoke": {"warmups": 1, "hot_runs": 3, "cold_runs": 1}, "full": {"warmups": 3, "hot_runs": 30, "cold_runs": 5}}
# Smoke ids for the two supported corpus layouts. Ids absent from the chosen
# corpus are skipped; tests/test_benchmark_smoke.py keeps the repository list real.
JPEXS_TESTDATA_SMOKE_FILES = ("as2/as2.swf", "as3_new/bin/as3_new.flex.swf", "as3_new/bin/as3_new.air.swf", "as3_cross_compile/bin/as3_cross_compile.flex.swf", "as2_definefunc_regs/as2_definefunc_regs.swf")
REPOSITORY_SMOKE_FILES = ("as2_definefunc_regs.swf", "as3_cross_compile.flex.swf", "as3_new.flex.swf",
                          "parity-20261014/optimized/swf/dev-as2-timeline/release.fws.swf",
                          "parity-20261014/optimized/swf/dev-as3-classes-namespaces/release.fws.swf",
                          "parity-20261014/optimized/swf/dev-haxe-application/release.fws.swf")
SMOKE_FILES = JPEXS_TESTDATA_SMOKE_FILES + REPOSITORY_SMOKE_FILES


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def implementation_digest():
    if getattr(sys, "frozen", False):
        return sha256(Path(sys.executable).read_bytes())
    digest = hashlib.sha256()
    for folder in (ROOT / "py_swf", ROOT / "tools/jpexs_java_bridge"):
        for path in sorted(p for p in folder.rglob("*") if p.suffix in (".py", ".java")):
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def reference_command(config_path=None):
    reference_root = ROOT
    if getattr(sys, "frozen", False):
        from .suite_tools import PROJECT
        reference_root = PROJECT
    path = Path(config_path) if config_path else reference_root / ".dev-references/toolchain.json"
    if not path.exists():
        raise WorkerError("Development reference unavailable: run tools/bootstrap_references.py and tools/build_reference_bridge.py")
    from .development_paths import normalize_development_config
    config = normalize_development_config(json.loads(path.read_text(encoding="utf-8")), reference_root, path.resolve().parent)
    if config.get("development_only") is not True or config.get("version") != "26.3.0" or config.get("commit") != "b2ef2d3d3f44291a4bdfa899c986bc39a89fa6aa":
        raise WorkerError("Reference toolchain does not match the approved pinned version")
    if not config.get("reference_classpath"):
        raise WorkerError("Reference bridge not built: run tools/build_reference_bridge.py")
    if not config.get("artifact_sha256"):
        raise WorkerError("Reference artifact hashes unavailable; rebuild the development bridge")
    bridge_source = reference_root / "tools/jpexs_java_bridge/DevelopmentReferenceBridge.java"
    if sha256(bridge_source.read_bytes()) != config.get("bridge_source_sha256"):
        raise WorkerError("Reference bridge source changed; rebuild before measuring")
    for file, expected in config["artifact_sha256"].items():
        if sha256(Path(file).read_bytes()) != expected:
            raise WorkerError("Reference artifact changed after build: " + file)
    flashlib = Path(config["ffdec_jar"]).parent / "flashlib"
    return [config["java"], "-Xmx1024m", "-Djava.awt.headless=true", "-Duser.home={reference_home}", "-Dpyswf.playerglobal=" + str(flashlib / "playerglobal32_0.swc"), "-Dpyswf.airglobal=" + str(flashlib / "airglobal.swc"), "-cp", config["reference_classpath"], "DevelopmentReferenceBridge"]


def command_for(engine, config_path=None, tool_config=None):
    if getattr(sys, "frozen", False):
        raise WorkerError("Benchmarks are development tools; run them using the project .venv, not the desktop executable")
    if engine == "native":
        return [sys.executable, "-m", "py_swf.benchmark_worker"]
    if engine == "jpexs":
        return reference_command(config_path)
    if engine in ("rust", "js", "go", "asm"):
        from .benchmark_engines import checked_tool, tools_config, default_peer_executable, default_rust_executable
        if tool_config and tools_config(tool_config).get(engine):
            config = checked_tool(engine, tool_config)
            digest = config["artifact_sha256"][config["executable"]]
            executable = Path(config["executable"]).resolve()
        else:
            executable = default_rust_executable() if engine == "rust" else default_peer_executable(engine)
            digest = sha256(executable.read_bytes())
        cmd = [sys.executable, "-m", "py_swf.benchmark_rust_worker", "--executable", str(executable), "--sha256", digest]
        if engine != "rust":
            cmd.extend(["--engine", engine])
        return cmd
    if engine in ("flasm","flare","rabcdasm","pyflashkit","flex_swfdump","flashdec_py","flashdec_rs","swfmill","swfextract"):
        return external_command(engine,tool_config)
    if engine in ("sothink","trillix","as3_sorcerer","asv"):
        raise WorkerError("Licensed installation and reproducible GUI/CLI adapter not available; no result measured")
    if engine=="decompiler_com":
        raise WorkerError("Remote service is evaluated separately with explicit public-corpus approval; no automated upload from this CLI")
    raise ValueError("Unknown benchmark engine: " + engine)


def measure(response, startup_ms=0):
    sample = dict(response.get("measurement", {}))
    sample.update(startup_ms=startup_ms, ok=response.get("ok", False))
    for key in ("parse_ms", "generate_ms", "serialize_ms", "input_decode_ms", "external_cli_ms", "temporary_io_and_catalogue_ms", "identity_parse_ms", "rust_reported_operation_ms"):
        sample[key] = response.get(key)
    for key in ("time_scope", "native_returncode", "failure_origin", "failure_stage", "status"):
        if key in response:
            sample[key] = response[key]
    accounted = sum(response.get(key, 0) for key in ("parse_ms", "generate_ms", "serialize_ms", "input_decode_ms"))
    sample["transport_and_unattributed_ms"] = max(0, sample.get("wall_ms", 0) - accounted)
    sample["total_with_startup_ms"] = sample.get("wall_ms", 0) + startup_ms
    return sample


def aggregate(samples, *, hot=True):
    successful = [s for s in samples if s.get("ok")]
    times = sorted(s["wall_ms" if hot else "total_with_startup_ms"] for s in successful)
    peaks = [s["peak_tree_rss_bytes"] for s in successful if s.get("peak_tree_rss_bytes") is not None]
    result = {"attempted": len(samples), "successful": len(successful), "median_ms": statistics.median(times) if times else None, "peak_tree_rss_bytes": max(peaks) if peaks else None}
    if hot:
        result["p95_ms"] = times[math.ceil(.95 * len(times))-1] if times else None
    else:
        result["range_ms"] = [min(times), max(times)] if times else None
    return result


def source_available_in_scope(unit):
    if unit.get('generation') not in ('available','implicit'):
        return False
    if unit.get('scope','method') in ('method','action','function') and (
            unit.get('represented_by_document') or unit.get('source_kind') in ('script_declarations','class_declarations','document_reference')):
        return False
    return True


def disassembly_available_in_scope(unit):
    return unit.get('identity_verified') is True and unit.get('disassembly') == 'available'


def quality(response):
    if response.get("operation") in ("extract", "xml") and response.get("status") == "measured":
        # Tag-level operations: every unit is a resource/tag of the input.
        return {"expected_units_reported": response.get("eligible_units"), "source_available": response.get("available_units"),
                "disassembly_available": None, "pcode": None, "represented_by_document": None,
                "failed": response.get("eligible_units", 0) - response.get("available_units", 0),
                "syntax_native_subset_supported": 0, "compiled_verified": 0, "behavior_verified": 0,
                "semantic_parity": "not_established", "unit_mapping": "matched"}
    units = response.get("units", [])
    units = [u for u in units if u.get('scope', 'method') in ('method', 'action', 'function', 'diagnostic')]
    unmapped=not response or response.get("status") in ('output_unmapped','not_configured','failed','not_measured','not_applicable') or response.get('inventory_status') == 'disagreement' or (response.get("ok") is False and not units)
    disassembly=response.get('operation') == 'disassemble' or any(u.get("generation")=="disassembly" for u in units)
    return {"expected_units_reported": response.get("eligible_units",None if unmapped else len(units)),
            "source_available": None if unmapped or disassembly else sum(source_available_in_scope(u) for u in units),
            "disassembly_available": None if unmapped or not disassembly else sum(disassembly_available_in_scope(u) for u in units),
            "pcode": None if unmapped or disassembly else sum(u.get("generation") in ('pcode','represented_by_document') and u.get('source_kind','pcode')=='pcode' for u in units),
            "represented_by_document":None if unmapped else sum(bool(u.get('represented_by_document')) for u in units),
            "failed": None if unmapped else sum(u.get("generation")=="failed" for u in units),
            "syntax_native_subset_supported": sum(u.get("syntax") == "native_subset_supported" for u in units),
            "compiled_verified": sum(u.get("compilation") == "passed" for u in units),
            "behavior_verified": sum(u.get("behavior") == "passed" for u in units), "semantic_parity": "not_established",
            "unit_mapping": "not_verified" if unmapped else "canonical"}


def verify_compilation_sample(worker, engine, payload, *, limit=8):
    from .validation import validate_swf_bytes
    original = worker.request({**payload, "include_source": True})
    if not original.get("ok"):
        return [{"document_id": None, "compilation": "not_run", "diagnostic": original.get("diagnostic"), "behavior": "not_run"}]
    targets = [u for u in original.get("units", []) if u.get("compiler_eligible") and "source" in u] if engine == "native" else [d for d in original.get("documents", []) if "source" in d and d.get("generation") == "available"] + [u for u in original.get("units", []) if u.get("language") == "as2" and "source" in u and u.get("generation") == "available"]
    cases = []
    for unit in sorted(targets, key=lambda u:u["document_id"])[:limit]:
        try:
            response = worker.request({**payload, "operation": "compile", "document_id": unit["document_id"], "source": unit["source"]})
            case = {"document_id": unit["document_id"], "compilation": "passed" if response.get("ok") else "failed", "swf_validation": "not_run", "behavior": "not_run"}
            if response.get("ok"):
                candidate = base64.b64decode(response["swf_base64"], validate=True)
                validation = validate_swf_bytes(candidate)
                case["swf_validation"] = "passed" if validation["valid"] else "failed"
                case["diagnostic"] = validation.get("errors")
                case["candidate_sha256"] = sha256(candidate)
                case["saved_reopened_in_reference"] = response.get("swf_reopened", False)
            else:
                case["diagnostic"] = response.get("diagnostics") or response.get("diagnostic")
            cases.append(case)
        except Exception as error:
            cases.append({"document_id": unit["document_id"], "compilation": "failed", "diagnostic": str(error), "swf_validation": "not_run", "behavior": "not_run"})
            break
    return cases


def run_engine(engine, data, profile, *, config_path=None, tool_config=None, operation="source", timeout=120, memory_bytes=2*1024**3, command_factory=None):
    result = {"engine": engine, "cold_samples": [], "hot_samples": [], "warmup_samples": [], "failures": [], "units": [], "documents": []}
    result.update(operation=operation,category=ENGINES[engine].category,status="not_measured")
    payload = {"operation": "decompile" if operation=="source" else "disassemble", "swf_base64": base64.b64encode(data).decode("ascii"), "include_source": False}
    if engine not in ("native","jpexs"):
        payload["operation"]=operation
    if operation not in ENGINES[engine].operations:
        result.update(status="not_applicable",diagnostic="Tool does not support this benchmark operation",cold=aggregate([],hot=False),hot=aggregate([]),quality=quality({}),compilation_sample=[])
        result["quality"]["source_available"]=None
        result["quality"]["expected_units_reported"]=None
        return result
    try:
        command = command_factory(engine) if command_factory is not None else command_for(engine, config_path,tool_config)
    except (OSError, ValueError, WorkerError) as error:
        result["failures"].append({"phase": "setup", "diagnostic": str(error)})
        result["cold"] = aggregate([], hot=False)
        result["hot"] = aggregate([])
        result["quality"] = quality({})
        result.update(status="not_configured",diagnostic=str(error))
        result["quality"]["source_available"]=None
        result["quality"]["expected_units_reported"]=None
        return result
    latest, last_successful = {}, {}
    for ordinal in range(profile["cold_runs"]):
        try:
            with Worker(command, cwd=ROOT, timeout=timeout, memory_bytes=memory_bytes) as worker:
                latest = worker.request(payload)
                if latest.get("status")=="not_applicable":
                    result.update(status="not_applicable",diagnostic="No scripts in the tool's supported language",quality=quality(latest),cold=aggregate([],hot=False),hot=aggregate([]),compilation_sample=[],applicability_probe=measure(latest,worker.startup_ms))
                    result["quality"]["source_available"]=None
                    return result
                if latest.get("ok"):
                    last_successful = latest
                sample = measure(latest, worker.startup_ms)
                result["cold_samples"].append(sample)
                if not latest.get("ok"):
                    result["failures"].append({"phase": "cold", "run": ordinal, "diagnostic": latest.get("diagnostic")})
        except Exception as error:
            result["cold_samples"].append({"ok": False, "diagnostic": str(error)})
            result["failures"].append({"phase": "cold", "run": ordinal, "diagnostic": str(error)})
    try:
        with Worker(command, cwd=ROOT, timeout=timeout, memory_bytes=memory_bytes) as worker:
            result["hot_worker_startup_ms"] = worker.startup_ms
            for ordinal in range(profile["warmups"] + profile["hot_runs"]):
                try:
                    latest = worker.request(payload)
                    if latest.get("ok"):
                        last_successful = latest
                    sample = measure(latest)
                    target = "warmup_samples" if ordinal < profile["warmups"] else "hot_samples"
                    result[target].append(sample)
                    if not latest.get("ok"):
                        result["failures"].append({"phase": target, "run": ordinal, "diagnostic": latest.get("diagnostic")})
                except Exception as error:
                    target = "warmup_samples" if ordinal < profile["warmups"] else "hot_samples"
                    result[target].append({"ok": False, "diagnostic": str(error)})
                    result["failures"].append({"phase": target, "run": ordinal, "diagnostic": str(error)})
                    # Dead/uncorrelated worker cannot be reused. Count the
                    # remaining scheduled repetitions as not completed.
                    remaining_hot = profile["hot_runs"] - len(result["hot_samples"])
                    result["hot_samples"].extend({"ok": False, "diagnostic": "Worker unusable after previous failure"} for _ in range(remaining_hot))
                    break
    except Exception as error:
        result["failures"].append({"phase": "hot_startup", "diagnostic": str(error)})
        result["hot_samples"] = [{"ok": False, "diagnostic": str(error)} for _ in range(profile["hot_runs"])]
    observed = last_successful or latest
    result["units"], result["documents"] = observed.get("units", []), observed.get("documents", [])
    result["quality"] = quality(observed)
    result["quality_observation"] = "last successful operation" if last_successful else "no successful operation"
    result.update(status=observed.get("status","measured" if last_successful else "failed"),
                  diagnostic=observed.get("diagnostic"),artifacts=observed.get("artifacts",[]),warnings=observed.get("warnings",[]),
                  process_mode=observed.get("process_mode","persistent worker; fresh parse every operation"))
    for key in ("time_scope", "tool_fingerprint", "native_returncode", "failure_origin", "failure_stage", "output_summary",
                "canonical_inventory", "canonical_inventory_sha256", "inventory_status", "inventory_cross_validation", "adapter_mode", "include_scope", "artifact_unit_scope",
                "available_units"):
        if key in observed:
            result[key] = observed[key]
    result['unit_observations'] = result['units']
    result['eligible_units'] = observed.get('eligible_units',sum(u.get('scope','method') in ('method','action','function','diagnostic') for u in result['units']))
    if engine == "rust":
        for key in ("tool_fingerprint", "timing_scope", "identity_mapping", "identity_parse_ms",
                    "rust_reported_operation_ms", "rust_unit_count", "matched_units", "unmapped_units",
                    "missing_canonical_units", "canonical_inventory_diagnostic", "container_diagnostics"):
            result[key] = observed.get(key)
    # A separate worker and untimed requests ensure compilation does not
    # warm caches, change heap residency, or contaminate decompilation timing.
    if last_successful and operation=="source" and engine in ("native","jpexs"):
        try:
            with Worker(command, cwd=ROOT, timeout=timeout, memory_bytes=memory_bytes) as verifier:
                result["compilation_sample"] = verify_compilation_sample(verifier, engine, payload)
        except Exception as error:
            result["compilation_sample"] = [{"document_id": None, "compilation": "not_run", "diagnostic": str(error), "behavior": "not_run"}]
    else:
        result["compilation_sample"] = []
    result["quality"]["compiled_verified"] = sum(c["compilation"] == "passed" and c["swf_validation"] == "passed" for c in result["compilation_sample"])
    result["compilation_sample_policy"] = "At most eight deterministic eligible documents; native method bodies vs JPEXS script packs are NOT equal denominators"
    result["cold"], result["hot"] = aggregate(result["cold_samples"], hot=False), aggregate(result["hot_samples"])
    return result


def legacy_match_units(results):
    native = next((r for r in results if r["engine"] == "native"), None)
    comparators = [r for r in results if r["engine"] in ("jpexs", "rust")]
    if native is None or not comparators:
        return {"status": "comparison_not_requested", "quality_equivalent": False}
    from collections import Counter
    left = {u["document_id"]: u for u in native["units"]}
    pairs = []
    for comparator in comparators:
        engine = comparator["engine"]
        right = {u["document_id"]: u for u in comparator["units"] if u.get("identity_verified") is not False}
        common = left.keys() & right.keys()
        duplicates = {r["engine"]: sorted(key for key, count in Counter(u["document_id"] for u in r["units"]).items() if count > 1) for r in (native, comparator)}
        mismatch = [key for key in sorted(common) if left[key].get("bytecode_sha256") != right[key].get("bytecode_sha256")]
        unverified = [key for key in sorted(common) if not left[key].get("bytecode_sha256") or not right[key].get("bytecode_sha256")]
        ambiguous = set(duplicates["native"]) | set(duplicates[engine])
        verified = common - set(mismatch) - set(unverified) - ambiguous
        unavailable = comparator.get("status") in ("not_configured", "failed", "not_measured", "not_applicable") or native.get("status") in ("not_configured", "failed", "not_measured", "not_applicable")
        status = ("comparison_unavailable" if unavailable else
                  "ambiguous_duplicate_identities" if ambiguous else
                  "bytecode_mismatch" if mismatch else
                  "identity_unverified" if unverified else
                  "partial_identity_mapping" if comparator.get("status") == "output_unmapped" else
                  "matched_by_location_and_bytecode")
        pairs.append({"engines": ["native", engine], "status": status,
                      "matched": 0 if unavailable else len(verified), "matched_locations": len(common),
                      "unverified_bytecode": unverified, "duplicate_identities": duplicates,
                      "native_only": sorted(left.keys() - right.keys()), engine + "_only": sorted(right.keys() - left.keys()),
                      "bytecode_mismatches": mismatch, "quality_equivalent": False,
                      "identity_mapping": comparator.get("identity_mapping"),
                      "unmapped_external_units": len(comparator.get("unmapped_units") or []),
                      "reason": "Source availability and successful parsing are not behavior equivalence; no speed winner is declared"})
    primary = next((p for p in pairs if p["engines"][1] == "jpexs"), pairs[0])
    return {**primary, "pairs": pairs}


def match_units(results, canonical_inventory=None):
    """Every applicable source-engine pair, retaining the historical primary row."""
    from collections import Counter
    legacy = legacy_match_units(results)
    pairs = []
    source_results = [r for r in results if ENGINES.get(r.get('engine')) is not None
                      and 'source' in ENGINES[r['engine']].operations]
    for left_result, right_result in combinations(source_results, 2):
        left_name, right_name = left_result['engine'], right_result['engine']
        left_units, right_units = left_result.get('unit_observations', left_result.get('units', [])), right_result.get('unit_observations', right_result.get('units', []))
        def key(unit):
            # document_id remains the within-input alias used by historical
            # Java replies; full input-qualified unit_id is retained in storage.
            return unit.get('document_id', unit.get('unit_id'))
        duplicates = {name: sorted(k for k, count in Counter(key(u) for u in units).items() if count > 1)
                      for name, units in ((left_name, left_units), (right_name, right_units))}
        left, right = {key(u): u for u in left_units}, {key(u): u for u in right_units}
        common = set(left) & set(right)
        mismatch = sorted(k for k in common if left[k].get('bytecode_sha256') != right[k].get('bytecode_sha256'))
        unknown = sorted(k for k in common if not left[k].get('bytecode_sha256') or not right[k].get('bytecode_sha256')
                         or left[k].get('identity_verified') is False or right[k].get('identity_verified') is False)
        ambiguous = set(duplicates[left_name]) | set(duplicates[right_name])
        unavailable = any(r.get('status') in ('not_configured', 'failed', 'not_measured', 'not_applicable') for r in (left_result, right_result))
        disagreement = any(r.get('inventory_status') == 'disagreement' for r in (left_result, right_result))
        verified = common - set(mismatch) - set(unknown) - ambiguous
        if unavailable or disagreement:
            verified = set()
        scopes = {}
        available_scopes = {u.get('scope', 'method') for u in [*left_units, *right_units]}
        if canonical_inventory:
            available_scopes |= set(canonical_inventory.get('scope_denominators', {}))
        for scope in sorted(available_scopes):
            left_scope = {k for k, u in left.items() if u.get('scope', 'method') == scope}
            right_scope = {k for k, u in right.items() if u.get('scope', 'method') == scope}
            denominator = canonical_inventory.get('scope_denominators', {}).get(scope) if canonical_inventory else None
            scopes[scope] = {'canonical_denominator': denominator, 'left_reported': len(left_scope), 'right_reported': len(right_scope),
                             'matched': len(verified & left_scope & right_scope),
                             'left_source_available': sum(source_available_in_scope(left[k]) for k in verified & left_scope),
                             'right_source_available': sum(source_available_in_scope(right[k]) for k in verified & right_scope),
                             'source_coverage_denominator': 'same_scope_exact_identity_intersection',
                             'applicable': bool(left_scope and right_scope)}
        status = ('comparison_unavailable' if unavailable else 'inventory_disagreement' if disagreement else
                  'ambiguous_duplicate_identities' if ambiguous else 'bytecode_mismatch' if mismatch else
                  'partial_identity_mapping' if any(r.get('status') == 'output_unmapped' for r in (left_result, right_result)) else 'identity_unverified' if unknown else
                  'matched_by_location_and_bytecode')
        pairs.append({'engines': [left_name, right_name], 'status': status, 'matched': len(verified), 'matched_locations': len(common),
                      'applicability': 'not_applicable' if any(r.get('status') == 'not_applicable' for r in (left_result,right_result)) else 'applicable',
                      'duplicate_identities': duplicates, 'bytecode_mismatches': mismatch, 'unverified_bytecode': unknown,
                      left_name + '_only': sorted({k for k,u in left.items() if u.get('identity_verified') is not False} - {k for k,u in right.items() if u.get('identity_verified') is not False}),
                      right_name + '_only': sorted({k for k,u in right.items() if u.get('identity_verified') is not False} - {k for k,u in left.items() if u.get('identity_verified') is not False}),
                      'unmapped_external_units':sum(len(r.get('unmapped_units') or []) for r in (left_result,right_result)),
                      'scope_metrics': scopes, 'include_scope': sorted(available_scopes), 'quality_equivalent': False,
                      'reason': 'Byte identity alone does not establish recompilation, source fidelity, or behavior equivalence'})
    if not pairs:
        return {**legacy, 'pairs': [], 'pair_policy': 'all_applicable_source_engines'}
    primary = next((p for p in pairs if set(p['engines']) == {'native', 'jpexs'}), pairs[0])
    return {**primary, 'pairs': pairs, 'pair_policy': 'all_applicable_source_engines', 'pair_count': len(pairs),
            'applicable_pair_count': sum(p['applicability'] == 'applicable' for p in pairs)}


def readable_swf(data):
    """Container-level check only: unusual but legal bytecode stays eligible."""
    from .swf_parser import SWFFile
    try:
        SWFFile().read_bytes(data, strict=True)
        return True
    except Exception:
        return False


def collect_paths(corpus, profile, limit=None, family_ids=None, case_relatives=None):
    if limit is not None and limit < 1:
        raise ValueError("Benchmark --limit must be positive; cases cannot silently disappear")
    corpus = Path(corpus)
    if corpus.is_file():
        return [corpus]
    if not corpus.is_dir():
        raise ValueError("Corpus directory not found: " + str(corpus))
    source_manifest = corpus / 'manifest.json'
    authored = json.loads(source_manifest.read_text(encoding='utf-8')) if source_manifest.is_file() else {}
    if authored.get('immutable'):
        from .corpus import read_immutable_manifest
        authored = read_immutable_manifest(corpus)
        rows = authored.get('cases', [])
        if family_ids:
            requested=set(family_ids);available={r['family_id'] for r in rows}
            if requested - available:raise ValueError('Unknown corpus family: ' + ', '.join(sorted(requested-available)))
            rows=[r for r in rows if r['family_id'] in requested]
        if case_relatives:
            requested=set(case_relatives);available={r['relative'] for r in rows}
            if requested - available:raise ValueError('Unknown or excluded corpus case: ' + ', '.join(sorted(requested-available)))
            rows=[r for r in rows if r['relative'] in requested]
        paths = sorted(corpus / row['relative'] for row in rows)
    else:
        if family_ids or case_relatives:raise ValueError('Family/case selection requires a sealed source-family manifest')
        paths = sorted(corpus.rglob("*.swf"))
    if profile == "smoke":
        selected = [corpus / relative for relative in SMOKE_FILES if (corpus / relative).is_file()]
        if not selected:
            # A smoke run checks that engines work; damaged containers belong to full runs.
            for path in paths:
                if readable_swf(path.read_bytes()):
                    selected.append(path)
                    if len(selected) == 5:
                        break
        paths = selected
    return paths[:limit] if limit is not None else paths


def build_manifest(paths, corpus, seed):
    root = Path(corpus) if Path(corpus).is_dir() else Path(corpus).parent
    rows = []
    source_manifest = root / 'manifest.json'
    authored = json.loads(source_manifest.read_text(encoding='utf-8')) if source_manifest.is_file() else {}
    authored_rows = {row.get('relative', row.get('path')): row for row in authored.get('cases', [])}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        # Compiler variants stay together by source project. This does not
        # claim semantic deduplication of unrelated public projects.
        group = relative.split("/")[0] if "/" in relative else path.stem.split(".")[0]
        value = int(sha256(f"{seed}:{group}".encode())[:8], 16) % 10
        known = authored_rows.get(relative, {})
        if known.get('sha256') and sha256(path.read_bytes()) != known['sha256']:
            raise ValueError('Immutable corpus hash mismatch: ' + relative)
        rows.append({**known, "file": str(path.resolve()), "relative": relative, "source_family": known.get('family_id', group),
                     "family_id": known.get('family_id', group), "split": known.get('split', "validation" if value < 3 else "development"),
                     "sha256": sha256(path.read_bytes()), "bytes": path.stat().st_size})
    return {"schema_version": 1, "seed": seed, "grouping": "source project path; compiler variants never split; not semantic cross-project deduplication", "cases": rows}


def render_html(report):
    from .benchmark_report import render_dashboard
    return render_dashboard(report)


def summarize_family_quality(cases):
    """Macro coverage: variants average within families, families have equal weight."""
    cells = {}
    for case in cases:
        family = case.get('family_id', case.get('source_family', case['relative']))
        for engine in case['engines']:
            for origin, scopes in engine.get('quality', {}).get('origin_scope_metrics', {}).items():
                for scope, metric in scopes.items():
                    denominator = metric['canonical_denominator']
                    value = metric['source_available']/denominator if denominator and metric['source_available'] is not None else None
                    key = (engine['engine'], origin, scope)
                    cells.setdefault(key, {}).setdefault(family, []).append(value)
    output=[]
    for (engine,origin,scope),families in sorted(cells.items()):
        family_values={family:statistics.mean(v for v in values if v is not None) if any(v is not None for v in values) else None
                       for family,values in families.items()}
        verified=[v for v in family_values.values() if v is not None]
        output.append({'engine':engine,'code_origin':origin,'scope':scope,'families':family_values,
                       'measured_families':len(verified),'scheduled_families':len(family_values),
                       'mean_source_coverage':statistics.mean(verified) if verified else None,
                       'weighting':'equal family weights after averaging its debug/compression variants',
                       'missing_family_policy':'null and retained; never imputed as zero or full coverage'})
    return output


def run_benchmark(corpus, *, profile="smoke", engines=("native", "jpexs"), output=None, seed=1729, limit=None, config_path=None, tool_config=None, operation="source", timeout=120, memory_bytes=2*1024**3, command_factory=None, reference_provenance=None, family_ids=None, case_relatives=None):
    import psutil
    paths = collect_paths(corpus, profile, limit, family_ids, case_relatives)
    if not paths:
        raise ValueError("No SWF files in the selected corpus")
    if operation not in ("source","disassemble","extract","xml") or profile not in PROFILES or not engines or len(set(engines)) != len(engines) or any(e not in ENGINES for e in engines):
        raise ValueError("Invalid benchmark profile or engine list")
    output = Path(output or ROOT / "reports/decompiler-benchmark")
    output.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(paths, corpus, seed)
    order = list(manifest["cases"])
    random.Random(seed).shuffle(order)
    digest = implementation_digest()
    references = reference_provenance if reference_provenance is not None else json.loads((ROOT / "docs/reference-lock.json").read_text(encoding="utf-8"))
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "profile": profile, "configuration": {**PROFILES[profile], "seed": seed, "timeout_seconds": timeout, "worker_memory_limit_bytes": memory_bytes, "memory_sample_interval_ms": 20, "fresh_parse_each_iteration": True, "file_io": "Parent reads immutable bytes before timing; workers receive same base64 carrier", "memory_limit_authority": "Windows Job committed memory; sampled RSS watchdog on other hosts"}, "environment": {"platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(), "python": sys.version, "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False), "ram_bytes": psutil.virtual_memory().total, "available_ram_bytes": psutil.virtual_memory().available, "background_cpu_percent": psutil.cpu_percent(interval=.1), "psutil": psutil.__version__}, "provenance": {"implementation_sha256_before": digest, "references": references}, "cases": [], "claims": {"parity": False, "superiority": False, "performance_winner": None}}
    (output / "corpus-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report.update(operation=operation,engine_catalog=describe_engines(tool_config),
                  scope_note="External CLI output is measured at file level; unmapped unit coverage stays null, not zero or 100%. CLI startup remains in each hot operation.")
    report['configuration']['family_ids'] = list(family_ids) if family_ids else None
    report['configuration']['case_relatives'] = list(case_relatives) if case_relatives else None
    try:
        for ordinal, case in enumerate(order):
            data = Path(case["file"]).read_bytes()
            if sha256(data) != case["sha256"]:
                raise ValueError("Corpus changed after manifest capture: " + case["file"])
            selected = list(engines) if ordinal % 2 == 0 else list(reversed(engines))
            results = []
            for engine in selected:
                print(f'BENCH {ordinal+1}/{len(order)} {engine}: {case["relative"]}', flush=True)
                kwargs = {"command_factory": command_factory} if command_factory is not None else {}
                results.append(run_engine(engine, data, PROFILES[profile], config_path=config_path, tool_config=tool_config,operation=operation, timeout=timeout, memory_bytes=memory_bytes, **kwargs))
            from .canonical_inventory import build_inventory, attach_observations
            inventory = build_inventory(data)
            from .corpus import classify_application_units
            inventory = classify_application_units(inventory, case.get('application_classes', []), language=case.get('language'))
            rust = next((r for r in results if r['engine'] == 'rust'), None)
            inventory['cross_validation'] = rust.get('inventory_cross_validation', 'not_run') if rust else 'not_run'
            for result in results:
                if operation in ('extract', 'xml'):
                    # Units are resources/tags, not bytecode: no canonical method/class mapping.
                    result.pop('unit_observations', None)
                    result['include_scope'] = sorted({u['scope'] for u in result.get('units', []) if u.get('scope')})
                    result.update(canonical_inventory_sha256=inventory['sha256'], family_id=case['family_id'], split=case['split'])
                    result['quality'] = quality(result)
                    continue
                result['units'] = attach_observations(inventory, result.get('units', []), engine=result['engine'], adapter_mode=result.get('adapter_mode', 'legacy'))
                # Preserve the historical array without serializing a second
                # identical copy or four identical canonical inventories.
                result.pop('unit_observations',None)
                result['unit_observations_ref'] = 'units'
                result.pop('canonical_inventory',None)
                result['include_scope'] = sorted({u['scope'] for u in result['units'] if u.get('scope')})
                result.update(canonical_inventory_sha256=inventory['sha256'], family_id=case['family_id'], split=case['split'])
                # Preserve partial/missing inventories instead of treating an empty list as verified.
                if result['units'] and any(not u['identity_verified'] for u in result['units']):
                    result['inventory_status'] = 'disagreement'
                result['quality'] = quality(result)
                result['quality']['compiled_verified'] = sum(c.get('compilation') == 'passed' and c.get('swf_validation') == 'passed' for c in result.get('compilation_sample', []))
                blocked = result.get('status') in ('not_configured','not_applicable','failed','output_unmapped') or result.get('inventory_status') == 'disagreement'
                result['quality']['scope_metrics'] = {
                    scope: {'canonical_denominator': count,
                            'reported': sum(u.get('scope') == scope for u in result['units']),
                            'identity_verified': sum(u.get('scope') == scope and u['identity_verified'] for u in result['units']),
                            'source_available': None if blocked or operation != 'source' else sum(u.get('scope') == scope and u['identity_verified'] and source_available_in_scope(u) for u in result['units']),
                            'disassembly_available': None if blocked or operation != 'disassemble' else sum(u.get('scope') == scope and disassembly_available_in_scope(u) for u in result['units'])}
                    for scope, count in inventory['scope_denominators'].items()}
                result['quality']['origin_scope_metrics'] = {}
                for origin in ('application','dependency','mixed','unknown'):
                    metrics = {}
                    for scope in inventory['scope_denominators']:
                        ids = {u['document_id'] for u in inventory['units'] if u['scope'] == scope and u.get('code_origin') == origin}
                        mapped = [u for u in result['units'] if u.get('document_id') in ids and u['identity_verified']]
                        metrics[scope] = {'canonical_denominator':len(ids),'identity_verified':len(mapped),
                                          'source_available':None if blocked or operation != 'source' else sum(source_available_in_scope(u) for u in mapped),
                                          'disassembly_available':None if blocked or operation != 'disassemble' else sum(disassembly_available_in_scope(u) for u in mapped)}
                    result['quality']['origin_scope_metrics'][origin] = metrics
            entry = {**case, "canonical_inventory": inventory, "canonical_inventory_sha256": inventory['sha256'],
                     "include_scope": sorted(inventory['scope_denominators']),
                     "signature": data[:3].decode("ascii", errors="replace"), "engines": results,
                     "comparison": match_units(results, inventory), "original_preserved": sha256(Path(case["file"]).read_bytes()) == case["sha256"]}
            report["cases"].append(entry)
            (output / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except KeyboardInterrupt:
        report["cancelled"] = True
    finally:
        after = implementation_digest()
        report["provenance"]["implementation_sha256_after"] = after
        report["provenance"]["implementation_unchanged"] = after == digest
        report["scheduled_files"] = len(order)
        report["completed_files"] = len(report["cases"])
        report["uncompleted_files"] = [c["relative"] for c in order[len(report["cases"]):]]
        report['family_scope_quality'] = summarize_family_quality(report['cases'])
        (output / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "benchmark.html").write_text(render_html(report), encoding="utf-8")
    return report
