"""One complete fresh source observation per file/engine, NOT a speed benchmark."""
import base64
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

from .benchmark_process import Worker
from .benchmark_engines import describe_engines
from .benchmarks import ROOT, aggregate, build_manifest, collect_paths, command_for, implementation_digest, match_units, quality, sha256


def run_audit(corpus, *, output=None, engines=("native", "jpexs"), limit=None, seed=1729, config_path=None, timeout=120, compile_documents=False, compilation_budget_seconds=300):
    if getattr(sys, "frozen", False):
        raise ValueError("Corpus audits are development-only; use the project .venv")
    if not math.isfinite(compilation_budget_seconds) or compilation_budget_seconds<=0:
        raise ValueError("Compilation file budget must be positive and finite")
    if not engines or len(set(engines)) != len(engines) or any(e not in ("native", "jpexs") for e in engines):
        raise ValueError("Source audits currently compare native and pinned JPEXS")
    paths = collect_paths(corpus, "full", limit)
    if not paths:
        raise ValueError("Empty corpus")
    output = Path(output or ROOT / "reports/corpus-source-audit")
    output.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(paths, corpus, seed)
    digest = implementation_digest()
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "profile": "source_audit",
              "operation": "source", "measurement_context": {"kind": "source_audit", "suitable_for_comparison": False,
                   "reason": "One generation observation per input, no timing repetitions or semantic equivalence"},
              "configuration": {"warmups": 0, "hot_runs": 0, "cold_runs": 0, "seed": seed, "timeout_seconds": timeout,
                   "worker_memory_limit_bytes": 2*1024**3, "fresh_parse_each_iteration": True, "timing_measured": False,
                   "compile_documents":compile_documents,"compilation_file_budget_seconds":compilation_budget_seconds},
              "provenance": {"implementation_sha256_before": digest, "references": json.loads((ROOT / "docs/reference-lock.json").read_text(encoding="utf-8"))},
              "engine_catalog": describe_engines(), "cases": [], "claims": {"parity": False, "superiority": False, "performance_winner": None}}
    workers = {}
    def request(engine,payload):
        if engine not in workers:
            workers[engine] = Worker(command_for(engine, config_path), cwd=ROOT, timeout=timeout)
        try:
            return workers[engine].request(payload)
        except Exception:
            workers.pop(engine).close()
            raise
    (output / "corpus-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for ordinal, case in enumerate(manifest["cases"]):
            print(f'AUDIT {ordinal+1}/{len(paths)} {case["relative"]}', flush=True)
            data = Path(case["file"]).read_bytes()
            if sha256(data) != case["sha256"]:
                raise ValueError("Corpus changed after manifest creation")
            results = []
            for engine in (engines if ordinal % 2 == 0 else tuple(reversed(engines))):
                response = {}
                failure = None
                try:
                    payload={"operation":"decompile","swf_base64":base64.b64encode(data).decode(),"include_source":compile_documents}
                    response = request(engine,payload)
                    if not response.get("ok"):
                        failure = response.get("diagnostic", "Source generation failed")
                except Exception as error:
                    failure = str(error)
                    if engine in workers:
                        workers.pop(engine).close()
                result = {"engine": engine, "status": "measured" if response.get("ok") else "failed", "operation": "source",
                          "units": response.get("units", []), "documents": response.get("documents", []),
                          "quality": quality(response), "cold": aggregate([], hot=False), "hot": aggregate([]),
                          "cold_samples": [], "hot_samples": [], "compilation_sample": [],
                          "failures": [{"phase": "audit", "diagnostic": failure}] if failure else [],
                          "verification": {"parse_and_source": "observed" if response.get("ok") else "failed",
                                           "compilation": "not_run", "behavior": "not_run", "swf_validation": "not_run"}}
                if not response.get("ok"):
                    result["quality"].update(source_available=None, expected_units_reported=None)
                elif compile_documents:
                    from .compilation_audit import audit_compilation
                    result["compilation_audit"]=audit_compilation(response,engine,payload,lambda item:request(engine,item),file_budget_seconds=compilation_budget_seconds)
                    summary=result["compilation_audit"]["summary"]
                    result["quality"]["compiled_verified"]=summary["validated_reopened"]
                    result["verification"].update(compilation="audited",swf_validation="audited")
                    # Source text is needed only while compiling. Keep hashes,
                    # identities and diagnostics in the persisted evidence.
                    for unit in result["units"]+result["documents"]:
                        unit.pop("source",None)
                results.append(result)
            report["cases"].append({**case, "signature": data[:3].decode("ascii", errors="replace"), "engines": results,
                                    "comparison": match_units(results), "original_preserved": sha256(Path(case["file"]).read_bytes()) == case["sha256"]})
            (output / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except KeyboardInterrupt:
        report["cancelled"] = True
    finally:
        for worker in workers.values():
            worker.close()
        after = implementation_digest()
        report["provenance"].update(implementation_sha256_after=after, implementation_unchanged=after == digest)
        report.update(scheduled_files=len(paths), completed_files=len(report["cases"]),
                      uncompleted_files=[c["relative"] for c in manifest["cases"][len(report["cases"]):]])
        report["audit_summary"] = {
            "files": len(report["cases"]), "matched_units": sum(c["comparison"].get("matched", 0) for c in report["cases"]),
            "identity_mismatch_files": [c["relative"] for c in report["cases"] if c["comparison"].get("native_only") or c["comparison"].get("jpexs_only") or c["comparison"].get("bytecode_mismatches") or any(c["comparison"].get("duplicate_identities", {}).values())],
            "operation_failure_files": [c["relative"] for c in report["cases"] if any(e["failures"] for e in c["engines"])],
            "originals_preserved": all(c["original_preserved"] for c in report["cases"]), "behavior_verified": False}
        if compile_documents:
            from .compilation_audit import summarize
            report["audit_summary"]["compilation_by_engine"]={engine:summarize([r for c in report["cases"] for e in c["engines"] if e["engine"]==engine for r in e.get("compilation_audit",{}).get("records",[])]) for engine in engines}
        (output / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
