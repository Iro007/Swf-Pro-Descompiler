"""Robustness: how every decompiler copes with the damaged SWF files of corpus/damaged/ (tools/bench.py --only robustness).

Each (engine, damaged file) pair runs in its own process (`--worker`) under a timeout and a memory-limited Windows job
(tools/run_ruffle_trace_probe.bounded_run), so a crash or a hang of an engine is one more outcome, never the end of the
benchmark. The worker drives the engine through the same entry point the other benchmarks use (PySWF in process, the Rust
family through its JSONL worker, JPEXS through its bridge, pyflashkit and Flare through their command line) and writes what
it saw without judging it. The parent then decides, with py_swf/robustness.py:

- which units of the original are still intact (`intact_units`): the units no damaged byte range touches;
- what the engine emitted for them, checked against the original's inventory like the Índice Flash does (`closeness`);
- the outcome of the run (`classify`): recovered, partial, clean_error, crash or hang.

A message counts as a report of the damage only if the engine did not already give it for the undamaged source file, so the
routine diagnostics of a decompiler (a method it cannot rebuild, a class it keeps as P-code) never pass for a warning.
docs/BENCHMARKS_CODIGO.md defines the outcomes and the score.

    python tools/bench_robustness.py --engines native rust --limit 8     # try it without a full benchmark run
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import traceback

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

from py_swf import paths
from py_swf.bench_results import comparable_engines, median_seconds, robustness_score, timed_cases
from py_swf.benchmark_engines import ENGINES
from py_swf.canonical_inventory import build_inventory
from py_swf.closeness import canonical_units, _same_code
from py_swf.corpus import read_damaged_manifest
from py_swf.robustness import OUTCOMES, classify, intact_units
from run_ruffle_trace_probe import bounded_run
from run_semantic_benchmarks import write_json

DAMAGED = paths.CORPUS / "damaged"
# What the engines get to open and decompile a damaged file before the harness gives up on them.
DEFAULT_TIMEOUT = 30
MAX_DIAGNOSTICS = 60
SOURCE_ENGINES = ("native", "rust", "js", "go", "asm", "jpexs", "pyflashkit", "flare")

KIND_LABELS = {
    "truncate_header": "Truncado en la cabecera", "truncate_tag": "Truncado en un tag", "truncate_code": "Truncado en el código",
    "bad_length": "Longitud de archivo errónea", "bad_compression": "Flujo zlib roto", "bad_tag_length": "Longitud de tag errónea",
    "flip_code": "Bytes alterados en el código", "flip_noncode": "Bytes alterados fuera del código",
}


# --- the worker: drive one engine on one file and write down what happened -------------------------------------------

class Observation:
    """What an engine did with a file, as plain data. No judgement here: the parent classifies."""

    def __init__(self, engine):
        self.began = time.perf_counter()
        self.data = {"engine": engine, "opened": False, "stage": "open", "documents": [], "methods": [], "diagnostics": [],
                     "failure": None, "seconds": None, "method_hashes": {}}

    def start(self):
        """Start the clock the engine's time is read from; an adapter calls it once its own set-up is done."""
        self.began = time.perf_counter()

    def diagnose(self, text):
        text = clean(text)[:240]
        if text and text not in self.data["diagnostics"] and len(self.data["diagnostics"]) < MAX_DIAGNOSTICS:
            self.data["diagnostics"].append(text)

    def document(self, document_id, scope, has_source, bytecode_sha256=None, unit_ids=None):
        self.data["documents"].append({"document_id": document_id, "scope": scope, "has_source": bool(has_source),
                                       "bytecode_sha256": bytecode_sha256, "unit_ids": unit_ids})

    def method(self, document_id, has_source, bytecode_sha256=None):
        self.data["methods"].append({"document_id": document_id, "scope": "method", "has_source": bool(has_source),
                                     "bytecode_sha256": bytecode_sha256})

    def fail(self, kind, message, error_type=""):
        self.data["failure"] = {"kind": kind, "type": error_type, "message": clean(message)[:300]}


def clean(text):
    """One line without the folders of the temporary files an engine names in its messages."""
    return " ".join(re.sub(r"[A-Za-z]:[\\/](?:[^\s'\"]*[\\/])+", "", str(text)).split())


def tail(text, lines=3):
    return " | ".join(line.strip() for line in str(text).strip().splitlines()[-lines:] if line.strip())


def controlled(error):
    """The errors a Python engine raises on purpose for bad input: ValueError and EOFError, but not the decoding errors
    that are ValueError by inheritance only. Every other exception type is the engine falling over its input."""
    return isinstance(error, (ValueError, EOFError)) and not isinstance(error, UnicodeError)


def explain(error):
    """(kind, message) of an exception that reached an adapter: a deliberate error, a timeout or a crash of the engine."""
    from py_swf.benchmark_process import WorkerError
    from py_swf.benchmark_rust_worker import RustOperationError, RustProtocolError
    message = str(error)
    if isinstance(error, subprocess.TimeoutExpired):
        return "timeout", "superó el plazo"
    if isinstance(error, (RustProtocolError, WorkerError)):
        return ("timeout" if re.search(r"deadline|timed out", message, re.I) else "crash"), message
    if isinstance(error, RustOperationError):
        return "error", message
    if controlled(error):
        return "error", message
    return "crash", "%s: %s" % (qualified(error), message)


def qualified(error):
    """The class of an exception with its module, so that zlib.error does not read as just `error`."""
    kind = type(error)
    return kind.__name__ if kind.__module__ == "builtins" else "%s.%s" % (kind.__module__, kind.__name__)


def message_of(item):
    """A diagnostic from an engine, whatever shape it takes (text, or {code, message})."""
    if isinstance(item, dict):
        return "%s: %s" % (item.get("code"), item.get("message")) if item.get("code") else item.get("message") or json.dumps(item, sort_keys=True)
    return str(item)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def observe_native(obs, path, work, timeout):
    """PySWF in process: open the file, list its units, rebuild every class, script and action and every method."""
    from py_swf.decompile.avm2_dec import decompile_method
    from py_swf.universal import UniversalSuite
    suite = UniversalSuite(work / "state", work / "tools.json")  # building the suite is set-up, not decompiling
    obs.start()
    try:
        state = suite.open(path)
        obs.data["opened"], obs.data["stage"] = True, "list"
        for error in (state.get("validation") or {}).get("errors", []):
            obs.diagnose(error)
        units = suite.units()
        for unit in units:
            if unit.diagnostic:
                obs.diagnose("%s: %s" % (unit.document_id, unit.diagnostic))
        obs.data["stage"] = "decompile"
        for tag_path in sorted({unit.tag_path for unit in units if unit.abc is not None and not unit.diagnostic}):
            offset = 0
            try:
                while True:
                    page = suite.code_documents({"tag_path": list(tag_path), "engine": "native", "include_source": True, "limit": 200, "offset": offset})
                    for item in page["items"]:
                        if item["scope"] in ("class", "script"):
                            obs.document(item["document_id"], item["scope"], item.get("source"), unit_ids=item.get("unit_ids"))
                            for message in item.get("diagnostics", []):
                                obs.diagnose(message)
                    offset += len(page["items"])
                    if offset >= page["total"] or not page["items"]:
                        break
            except Exception as error:  # one ABC that cannot be rebuilt: the next one may still be
                obs.diagnose("%s: %s" % (type(error).__name__, error))
        for unit in units:
            if unit.diagnostic:
                continue
            try:
                if unit.language == "as3":
                    text, error = decompile_method(unit.abc, unit.body, unit.name)
                    obs.method(unit.document_id, text and not error, sha(unit.code))
                    if error:
                        obs.diagnose("%s: %s" % (unit.document_id, error))
                elif unit.kind in ("frame", "init", "button", "button2", "clip"):
                    result = suite.decompile({"unit_id": unit.document_id, "engine": "native"})
                    obs.document(unit.document_id, "action", result.get("source"), sha(unit.code))
                    for message in result.get("diagnostics", []):
                        obs.diagnose("%s: %s" % (unit.document_id, message))
            except Exception as error:
                obs.diagnose("%s: %s: %s" % (unit.document_id, type(error).__name__, error))
    finally:
        suite.close()


def observe_peer(obs, engine, pin, path, work, timeout):
    """RustSWF, JsSWF, GoSWF and AsmSWF: the same JSONL worker protocol, one process each."""
    from py_swf.benchmark_rust_worker import RustOperationError, RustWorker
    from py_swf.suite_tools import TOOLPACKS, fingerprint
    worker = RustWorker(pin["path"], pin["sha256"], timeout=timeout, engine=engine)
    deadline = time.perf_counter() + timeout
    try:
        opened = worker.call("open", {"path": str(path)}, deadline)
        obs.data["opened"], obs.data["stage"] = True, "list"
        for item in opened.get("diagnostics") or []:
            obs.diagnose(message_of(item))
        units, offset = [], 0
        while True:
            page = worker.call("scripts", {"offset": offset, "limit": 500}, deadline)
            units.extend(item for item in page.get("items", []) if isinstance(item, dict))
            offset += len(page.get("items", []))
            if not page.get("items") or offset >= page.get("total", 0):
                break
        for unit in units:
            for item in unit.get("diagnostics") or []:
                obs.diagnose(message_of(item))
        obs.data["stage"] = "decompile"
        for unit in units:
            identity = unit.get("id")
            try:
                result = worker.call("decompile", {"id": identity}, deadline)
            except RustOperationError as error:
                obs.diagnose("%s: %s" % (identity, error))
                continue
            for item in result.get("diagnostics") or []:
                obs.diagnose("%s: %s" % (identity, message_of(item)))
            source = result.get("source")
            has_source = isinstance(source, str) and bool(source.strip()) and result.get("state") == "structured"
            if unit.get("language") == "avm2":
                obs.method(identity, has_source, unit.get("sha256"))
                obs.data["method_hashes"][identity] = unit.get("sha256")
            elif "/function:" not in str(identity):
                obs.document(identity, "action", has_source, unit.get("sha256"))
        if engine == "rust":  # class documents: the only peer that rebuilds whole classes
            request_extra = {}
            sdk = TOOLPACKS / "flex/frameworks/libs/player/32.0/playerglobal.swc"
            from py_swf.decompile.hierarchy_contract import PLAYERGLOBAL_32_SHA256
            if sdk.is_file() and fingerprint(sdk) == PLAYERGLOBAL_32_SHA256:
                request_extra = {"hierarchy_swc": str(sdk), "hierarchy_sha256": PLAYERGLOBAL_32_SHA256}
            seen = set()
            for unit in units:
                tag_path = tuple(unit.get("tag_path") or ())
                if unit.get("language") != "avm2" or tag_path in seen:
                    continue
                seen.add(tag_path)
                try:
                    response = worker.call("decompile_document", {"id": unit["id"], **request_extra}, deadline)
                except RustOperationError as error:
                    obs.diagnose("%s: %s" % (unit["id"], error))
                    continue
                for item in response.get("diagnostics") or []:
                    obs.diagnose("tag:%s: %s" % ("/".join(map(str, tag_path)), message_of(item)))
                prefix = "tag:" + "/".join(map(str, tag_path))
                for document in response.get("documents", []):
                    if document.get("class_index") is not None:
                        obs.document("%s/class:%d" % (prefix, document["class_index"]), "class",
                                     response.get("state") == "structured" and document.get("source"))
    finally:
        worker.close()


def observe_jpexs(obs, pin, path, work, timeout):
    """JPEXS through the Java bridge the suite uses: classes, scripts, methods and actions in one answer."""
    from py_swf.benchmark_process import Worker
    data = Path(path).read_bytes()
    with Worker(pin["command"], cwd=PROJECT, timeout=timeout) as worker:
        response = worker.request({"operation": "decompile", "include_source": True, "swf_base64": base64.b64encode(data).decode("ascii")})
    if not response.get("ok"):
        obs.fail("error", response.get("diagnostic") or response.get("error") or "JPEXS rechazó el archivo")
        return
    obs.data["opened"], obs.data["stage"] = True, "decompile"
    for unit in response.get("units", []):
        scope = unit.get("scope")
        has_source = unit.get("generation") == "available" and bool((unit.get("source") or "").strip())
        if unit.get("diagnostic"):
            obs.diagnose("%s: %s" % (unit.get("document_id"), unit["diagnostic"]))
        if scope in ("class", "script", "action"):
            obs.document(unit.get("document_id"), scope, has_source, unit.get("bytecode_sha256"))
        elif scope == "method":
            obs.method(unit.get("document_id"), has_source, unit.get("bytecode_sha256"))


def last_exception(stderr):
    """(qualified name, message) of the exception that ended a Python script, from the last line of its traceback."""
    for line in reversed(str(stderr).strip().splitlines()):
        found = re.match(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)):?\s*(.*)$", line.strip())
        if found:
            return found.group(1), found.group(2)
    return "", ""


def observe_pyflashkit(obs, pin, path, work, timeout):
    """pyflashkit through the script the suite already runs in its isolated interpreter (every ABC block, class and method)."""
    import py_swf.benchmark_external_worker as external
    config = pin["config"]
    folder = Path(tempfile.mkdtemp(prefix="fs-flashkit-", dir=work))
    (folder / "input.swf").write_bytes(Path(path).read_bytes())
    command = [config["executable"], "-I", "-c", external.FLASHKIT_API_ALL_ABC, str(folder / "input.swf"), str(folder / "output"), "source"]
    try:
        external.invoke(command, folder, timeout=timeout)
    except external.ExternalToolError as error:
        name, message = last_exception(error.stderr)
        library = name.startswith("flashkit.") or name in ("ValueError", "EOFError")
        if abnormal(error.returncode) or not library:
            obs.fail("crash", "%s: %s" % (name or "exit %s" % error.returncode, message or tail(error.stderr)))
        else:
            obs.fail("error", "%s: %s" % (name, message))
        return
    obs.data["opened"], obs.data["stage"] = True, "decompile"
    metadata = folder / "output" / "observations.json"
    if not metadata.is_file():
        obs.diagnose("pyflashkit no escribió sus observaciones")
        return
    for row in json.loads(metadata.read_text(encoding="utf-8"))["rows"]:
        has_source = row.get("generation") == "available" and bool(row.get("output_file"))
        if row.get("diagnostic"):
            obs.diagnose("%s: %s" % (row.get("document_id"), row["diagnostic"]))
        if row.get("scope") == "class":
            obs.document(row["document_id"], "class", has_source, row.get("bytecode_sha256"))
        else:
            obs.method(row["document_id"], has_source, row.get("bytecode_sha256"))


def abnormal(returncode):
    from py_swf.robustness import abnormal_exit
    return abnormal_exit(returncode)


def observe_flare(obs, pin, path, work, timeout):
    """Flare, a command-line tool: one consolidated ActionScript 2 text for the whole movie, no unit identities."""
    import py_swf.benchmark_external_worker as external
    from py_swf.robustness import internal_error
    config = pin["config"]
    folder = Path(tempfile.mkdtemp(prefix="fs-flare-", dir=work))
    (folder / "input.swf").write_bytes(Path(path).read_bytes())
    try:
        stdout, stderr, _ = external.invoke([config["executable"], str(folder / "input.swf")], folder, timeout=timeout)
    except external.ExternalToolError as error:
        message = tail(error.stderr) or tail(error.stdout) or "código de salida %s" % error.returncode
        obs.fail("crash" if abnormal(error.returncode) or internal_error(message) else "error", message)
        return
    obs.data["opened"], obs.data["stage"] = True, "decompile"
    for line in (stdout.decode("latin-1") + "\n" + stderr).splitlines():
        obs.diagnose(line)
    produced = [file for file in folder.glob("*.flr") if file.read_bytes().strip()]
    obs.document(None, "consolidated", bool(produced))


def preload(engine):
    """Import what an adapter needs before the clock starts: loading Python modules is not the engine's time."""
    if engine == "native":
        from py_swf.universal import UniversalSuite  # noqa: F401
        from py_swf.decompile.avm2_dec import decompile_method  # noqa: F401
    elif engine in ("rust", "js", "go", "asm"):
        from py_swf.benchmark_rust_worker import RustOperationError, RustWorker  # noqa: F401
        from py_swf.decompile.hierarchy_contract import PLAYERGLOBAL_32_SHA256  # noqa: F401
        from py_swf.suite_tools import TOOLPACKS, fingerprint  # noqa: F401
    elif engine == "jpexs":
        from py_swf.benchmark_process import Worker  # noqa: F401
    else:
        import py_swf.benchmark_external_worker  # noqa: F401


def observe(request):
    engine, path = request["engine"], Path(request["path"])
    work = Path(request["folder"])
    timeout = request["engine_timeout"]
    obs = Observation(engine)
    preload(engine)
    obs.start()
    pin = request.get("pin") or {}
    try:
        if engine == "native":
            observe_native(obs, path, work, timeout)
        elif engine in ("rust", "js", "go", "asm"):
            observe_peer(obs, engine, pin, path, work, timeout)
        elif engine == "jpexs":
            observe_jpexs(obs, pin, path, work, timeout)
        elif engine == "pyflashkit":
            observe_pyflashkit(obs, pin, path, work, timeout)
        elif engine == "flare":
            observe_flare(obs, pin, path, work, timeout)
        else:
            raise ValueError("Motor desconocido: " + engine)
    except BaseException as error:  # the engine's answer, whatever it was; the process itself must report it
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        kind, message = explain(error)
        obs.fail(kind, message, type(error).__name__)
        if kind == "crash":
            obs.data["traceback"] = tail(traceback.format_exc(), 6)
    obs.data["seconds"] = round(time.perf_counter() - obs.began, 3)
    return obs.data


def worker_main(request_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    result = observe(request)
    write_json(Path(request["folder"]) / "worker-result.json", result)
    return 0


# --- the parent: run, verify against the original, classify, score -----------------------------------------------------

def manifest_cases(root=DAMAGED):
    manifest = read_damaged_manifest(root)
    return manifest, [dict(case, path=str(Path(root) / case["relative"])) for case in manifest["cases"]]


def run_observation(engine, pin, path, work, timeout):
    """Run one engine on one file in its own process. -> (process facts, observation or None)."""
    work.mkdir(parents=True, exist_ok=True)
    request = {"engine": engine, "path": str(path), "folder": str(work), "pin": pin, "engine_timeout": max(5, timeout - 5)}
    write_json(work / "request.json", request)
    process = bounded_run([sys.executable, str(Path(__file__).resolve()), "--worker", str(work / "request.json")], PROJECT, timeout=timeout)
    result_path = work / "worker-result.json"
    observation = None
    if result_path.is_file():
        try:
            observation = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError:
            observation = None
    return process, observation


def verify(observation, canonical, inventory):
    """Mark which documents and methods the engine emitted are the original's own units (identity and bytecode).

    Documents carry the hash of their code when the engine reports one; PySWF names the methods of a class instead, and
    RustSWF's classes are accepted when the engine listed every method of the class with the original's hashes.
    Scripts that RustSWF does not emit are composed from its classes, as the suite does; Flare's single text is the root action."""
    documents, methods = [], []
    reported = observation.get("method_hashes", {})
    for item in observation.get("methods", []):
        known = canonical.get((item["document_id"], "method"))
        methods.append({**item, "verified": bool(item["has_source"] and known and item.get("bytecode_sha256") == known["sha"])})
    for item in observation.get("documents", []):
        if item["scope"] == "consolidated":
            actions = [key for key in canonical if key[1] == "action"]
            if len(actions) == 1 and item["has_source"]:
                documents.append({"document_id": actions[0][0], "scope": "action", "has_source": True, "verified": True})
            continue
        known = canonical.get((item["document_id"], item["scope"]))
        if known is None or not item["has_source"]:
            verified = False
        elif item.get("bytecode_sha256") is not None or item.get("unit_ids") is not None:
            verified = _same_code(item, known)
        else:  # RustSWF class: every method of the class listed by the engine with the original's hash
            verified = bool(known["methods"]) and all(
                reported.get(name) is not None and canonical.get((name, "method"), {}).get("sha") == reported.get(name) for name in known["methods"])
        documents.append({**item, "verified": bool(verified)})
    if observation["engine"] == "rust":
        by_id = {(d["document_id"], d["scope"]): d for d in documents}
        for block in inventory["abc_blocks"]:
            prefix = "tag:" + "/".join(map(str, block["tag_path"]))
            for script, classes in block["script_class_indices"].items():
                key = ("%s/script:%s" % (prefix, script), "script")
                parts = [by_id.get(("%s/class:%d" % (prefix, index), "class")) for index in classes]
                if key in canonical and classes and all(part and part["has_source"] and part["verified"] for part in parts):
                    documents.append({"document_id": key[0], "scope": "script", "has_source": True, "verified": True})
    return {**observation, "documents": documents, "methods": methods}


def evaluate(engine, pin, case, source, work, timeout, cache, lock):
    """The row of one (engine, damaged file): outcome, reason, time, memory and the units recovered."""
    row = {"engine": engine, "family": case["family_id"], "case": case["id"], "kind": case["kind"], "language": case["language"],
           "source": case["source"], "measured": True, "outcome": None, "reason": "", "seconds": None, "peak_memory_mib": None,
           "intact_units": None, "recovered_units": None, "intact_methods": None, "recovered_methods": None, "reported": None}
    if case["language"] not in ENGINES[engine].languages:
        return {**row, "measured": False, "reason": "no lee " + case["language"].upper()}
    if pin.get("status") == "not_configured":
        return {**row, "measured": False, "reason": "no instalado: " + str(pin.get("diagnostic", ""))[:200]}
    key = (engine, case["source"])
    with lock:
        if key not in cache:
            cache[key] = None  # claimed below, outside the lock
            claimed = True
        else:
            claimed = False
    if claimed:
        started = run_observation(engine, pin, source, work / ("baseline-%s-%s" % (engine, hashlib.sha256(case["source"].encode()).hexdigest()[:8])), timeout)
        with lock:
            cache[key] = started[1] or {}
    else:  # another thread is running the undamaged file: wait for it
        while True:
            with lock:
                if cache[key] is not None:
                    break
            time.sleep(0.05)
    baseline = (cache[key] or {}).get("diagnostics", [])
    process, observation = run_observation(engine, pin, case["path"], work / ("%s-%s" % (engine, case["id"])), timeout)
    original = Path(paths.CORPUS) / case["source"]
    data = original.read_bytes()
    with lock:
        if ("intact", case["id"]) not in cache:
            cache[("intact", case["id"])] = intact_units(data, case["damage"]["ranges"])
        if ("canonical", case["source"]) not in cache:
            cache[("canonical", case["source"])] = (canonical_units(data), build_inventory(data))
    intact = cache[("intact", case["id"])]
    canonical, inventory = cache[("canonical", case["source"])]
    checked = verify(observation, canonical, inventory) if observation else None
    outcome, reason = classify(process, checked, baseline, intact)
    seconds = process["wall_seconds"] if process.get("timed_out") or not observation or observation.get("seconds") is None else observation["seconds"]
    recovered = [d for d in (checked or {}).get("documents", []) if d.get("has_source") and d.get("verified") and (d["document_id"], d["scope"]) in intact["documents"]]
    recovered_methods = [m for m in (checked or {}).get("methods", []) if m.get("has_source") and m.get("verified") and m["document_id"] in intact["methods"]]
    peak = process.get("peak_memory_bytes")
    new = [text for text in (observation or {}).get("diagnostics", []) if text not in set(baseline)]
    return {**row, "outcome": outcome, "reason": reason[:240], "seconds": round(seconds, 3),
            "peak_memory_mib": None if peak is None else round(peak / 1024 ** 2, 1),
            "intact_units": len(intact["documents"]), "recovered_units": len(recovered),
            "intact_methods": len(intact["methods"]), "recovered_methods": len(recovered_methods),
            "reported": bool(new or ((observation or {}).get("failure") or {}).get("message"))}


def run(pins, engines, cases, work, timeout=DEFAULT_TIMEOUT, workers=4, progress=print):
    """Every engine on every damaged file, `workers` processes at a time. -> rows."""
    lock, cache = __import__("threading").Lock(), {}
    jobs = [(engine, case) for case in cases for engine in engines]
    root = Path(paths.CORPUS)

    def one(job):
        engine, case = job
        pin = pins["source_tools"].get(engine, {})
        try:
            row = evaluate(engine, pin, case, root / case["source"], work, timeout, cache, lock)
        except Exception as error:  # the harness must not stop for one pair
            row = {"engine": engine, "family": case["family_id"], "case": case["id"], "kind": case["kind"], "language": case["language"],
                   "source": case["source"], "measured": False, "outcome": None, "reason": "fallo del banco: %s: %s" % (type(error).__name__, error)}
        progress("robustez", case["id"], engine, row.get("outcome"), flush=True) if progress is print else None
        return row

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, jobs))


def share(count, total):
    return count / total if total else 0.0


def table(rows):
    """One line per engine over the damaged files it was measured on, ranked only among engines measured on the same files."""
    by_engine = defaultdict(list)
    for row in rows:
        by_engine[row["engine"]].append(row)
    together = comparable_engines(rows)
    lines = []
    for engine, items in by_engine.items():
        ran = [r for r in items if r.get("measured", True) and r.get("outcome")]
        outcomes = Counter(r["outcome"] for r in ran)
        kinds = {}
        for kind in KIND_LABELS:
            part = [r for r in ran if r["kind"] == kind]
            if part:
                counted = Counter(r["outcome"] for r in part)
                kinds[kind] = {"cases": len(part), "outcomes": {o: counted.get(o, 0) for o in OUTCOMES}, "score": robustness_score(counted)}
        recoverable = [r for r in ran if r.get("intact_units")]
        peaks = [r["peak_memory_mib"] for r in ran if isinstance(r.get("peak_memory_mib"), (int, float))]
        lines.append({"engine": engine, "name": ENGINES[engine].name, "cases": len(ran), "outcomes": {o: outcomes.get(o, 0) for o in OUTCOMES},
                      "robustness": robustness_score(outcomes) if ran else None, "by_kind": kinds,
                      "recoverable_cases": len(recoverable), "intact_units": sum(r["intact_units"] or 0 for r in ran),
                      "recovered_units": sum(r["recovered_units"] or 0 for r in ran),
                      "unit_recovery": (sum(r["recovered_units"] / r["intact_units"] for r in recoverable) / len(recoverable)) if recoverable else None,
                      "median_seconds": median_seconds(ran), "timed_cases": timed_cases(ran),
                      "peak_memory_mib": max(peaks) if peaks else None,
                      "comparable": engine in together,
                      "reasons": sorted({r["reason"] for r in items if r.get("reason") and not r.get("outcome")})[:3]})
    lines.sort(key=lambda l: (l["cases"] == 0, not l["comparable"], -(l["robustness"] or 0), l["engine"]))
    rivals = [l for l in lines if l["comparable"]]
    noise = 1  # a lead of one case or less (a crash turned into a recovery) is not a ranking
    winner = rivals[0]["engine"] if len(rivals) > 1 and (rivals[0]["robustness"] - rivals[1]["robustness"]) * rivals[0]["cases"] / 100 > noise else None
    note = None if winner else ("sin ganador claro: la ventaja es de %d caso(s) o menos" % noise if len(rivals) > 1
                                else "sin ganador: ningún par de motores se midió sobre los mismos archivos")
    return {"unit": "archivos dañados en que el motor recupera lo intacto y avisa del daño (nota de 0 a 100)", "winner": winner, "note": note,
            "corpus_cases": len({r["case"] for r in rows}), "kinds": KIND_LABELS, "engines": lines}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker_main(sys.argv[2])
    from bench import settings
    from run_semantic_benchmarks import discover_pins
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engines", nargs="+", choices=SOURCE_ENGINES, default=list(SOURCE_ENGINES))
    parser.add_argument("--limit", type=int, help="solo los primeros N archivos dañados")
    parser.add_argument("--kinds", nargs="+", choices=list(KIND_LABELS))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--keep", type=Path, help="carpeta donde conservar la evidencia de cada ejecución")
    parser.add_argument("--json", type=Path, help="escribe las filas y la tabla en este archivo")
    args = parser.parse_args()
    _, cases = manifest_cases()
    cases = [c for c in cases if not args.kinds or c["kind"] in args.kinds][:args.limit]
    pins = discover_pins(settings(args.engines, args.timeout))
    folder = args.keep or Path(tempfile.mkdtemp(prefix="fs-robustness-"))
    rows = run(pins, args.engines, cases, folder, args.timeout, args.workers)
    result = table(rows)
    if args.json:
        write_json(args.json, {"rows": rows, "table": result})
    print(json.dumps({line["engine"]: {"robustez": line["robustness"], "resultados": line["outcomes"]} for line in result["engines"]}, indent=2, ensure_ascii=False))
    for row in rows:
        print("%-46s %-10s %-12s %s" % (row["case"], row["engine"], row.get("outcome"), row["reason"][:90]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
