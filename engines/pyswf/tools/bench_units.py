"""Per-unit fidelity: does the source an engine emitted for ONE class, method or action behave like the original?

The decompilers table judges a whole file: the project the engine emitted must recompile and print the same trace. Here every
verified unit the engine emitted source for is judged on its own. Its source is compiled alone by the single-unit edit path
of PySWF (py_swf/unit_editing.edit_unit, the API behind the `edit` operation in source mode, the same compiler whoever wrote
the source), the bytes of that unit are spliced into the original SWF and everything else stays as it was, and Ruffle must
print the original's trace.

Units, per language:

    method   an AS3 method body, spliced by itself
    class    an AS3 class: the editable methods of the class the engine emitted source for, spliced together
    action   an AS2 action (frame, init, button or clip code); nested functions travel inside their action

Outcomes: same (the trace is the original's), different, no_compile (the single-unit path did not take the source) and
not_run (the unit is never executed by the case's trace). A unit counts as executed only if a marker trace placed at its
entry shows up in Ruffle's output; one that never runs cannot show a difference, so it is left out of the denominator
instead of inflating the score. Units the single-unit path cannot take for any engine (closures with an activation,
private namespaces, and so on: `source_edit_reason`) are counted apart as out of scope, never against an engine.
Fidelity = same / (units evaluated - not_run). docs/BENCHMARKS_CODIGO.md has the definition.

The work of a case is bounded (`--unit-cap`, default 20 per case in the quick mode, all in the complete one): the units are
chosen from the SWF's own inventory, the same for every engine, evenly spread. Compiling runs in its own process under a
timeout and Ruffle checks the candidates in batches, so a unit that hangs the compiler or the player is one more `different`
or `no_compile`, never the end of the benchmark.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

from py_swf.bench_results import UNIT_OUTCOMES, comparable_engines, median_seconds, unit_fidelity
from py_swf.benchmark_engines import ENGINES
from py_swf.closeness import _same_code
from run_ruffle_trace_probe import bounded_run
from run_semantic_benchmarks import write_json

KINDS = ("class", "method", "action")
KIND_LABELS = {"class": "Clases", "method": "Métodos", "action": "Acciones (AS2)"}
CHUNK = 24
SPLICE_TIMEOUT = 180
ORACLE_TIMEOUT = 120
MARKER = "__FS_UNIT__"
# Types the edit path knows for a register; a parameter alias keeps its declared type only when it is one of these.
PRIMITIVES = ("int", "uint", "Number", "String", "Boolean", "Object")
ACTION_KINDS = ("frame", "init", "button", "button2", "clip")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def natural(text):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


# --- the oracle: Ruffle prints the original's trace -----------------------------------------------------------------

class TraceOracle:
    """Runs candidate SWFs in the headless Ruffle harness, many per process, and says which print the expected trace.

    The harness runs a test per folder under tests/swfs/ (test.swf, the expected output.txt and test.toml) and passes when the
    trace it prints equals output.txt, exactly what the other benchmarks use to judge a reconstruction."""

    def __init__(self, runtime, expected_trace, ticks=1, threads=4):
        self.runtime, self.expected, self.ticks, self.threads = Path(runtime) if runtime else None, list(expected_trace), ticks, threads

    def available(self):
        return bool(self.runtime) and self.runtime.is_file()

    def run(self, candidates):
        """{name: SWF bytes} -> {name: True if Ruffle printed the expected trace}. A batch that fails as a whole is split."""
        results = {}
        names = list(candidates)
        for start in range(0, len(names), CHUNK):
            results.update(self._chunk({name: candidates[name] for name in names[start:start + CHUNK]}))
        return results

    def _chunk(self, candidates):
        folder = Path(tempfile.mkdtemp(prefix="fs-trace-"))  # short: the harness opens its tests relative to its cwd
        try:
            names = {}
            for index, (name, data) in enumerate(candidates.items()):
                test = "trace_u%05d" % index
                names[test] = name
                where = folder / "harness/tests/swfs" / test
                where.mkdir(parents=True)
                (where / "test.swf").write_bytes(data)
                (where / "output.txt").write_text("\n".join(self.expected) + "\n", encoding="utf-8")
                (where / "test.toml").write_text("num_ticks = %d\nlog_warnings = false\n[player_options]\nversion = 32\nwith_audio = false\n"
                                                 "with_video = false\nmax_execution_duration = { secs = 15, nanos = 0 }\n" % self.ticks, encoding="utf-8")
            run = bounded_run([str(self.runtime), "trace_u", "--test-threads", str(self.threads)], folder / "harness",
                              timeout=ORACLE_TIMEOUT)
            found = {}
            for line in run["stdout"].splitlines():
                match = re.match(r"^test (trace_u\d+) \.\.\. (ok|FAILED)\b", line)
                if match and match.group(1) in names:
                    found[names[match.group(1)]] = match.group(2) == "ok"
            missing = [name for name in candidates if name not in found]
            if missing and len(candidates) > 1:  # the process died or hung: judge the rest in smaller batches
                half = len(missing) // 2 or 1
                for part in (missing[:half], missing[half:]):
                    if part:
                        found.update(self._chunk({name: candidates[name] for name in part}))
            else:
                for name in missing:
                    found[name] = False  # alone and still no verdict: the player did not finish with this candidate
            return found
        finally:
            shutil.rmtree(folder, ignore_errors=True)


# --- reading a method written as a declaration ----------------------------------------------------------------------

_HEAD = re.compile(r"\A\s*(?:[A-Za-z_$][\w$]*\s+)*?function\s*(?:(?:get|set)\s+)?(?:[A-Za-z_$][\w$]*)?\s*\(", re.S)


def split_arguments(text):
    """The comma-separated pieces of a parameter list, ignoring commas inside brackets, braces, generics and strings."""
    pieces, depth, quote, current = [], 0, None, []
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char in "([{<":
            depth += 1
            current.append(char)
        elif char in ")]}>":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            pieces.append("".join(current))
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        pieces.append("".join(current))
    return [piece.strip() for piece in pieces]


def without_leading_comments(text):
    """The text without the comments and blank lines before its first statement (GoSWF and AsmSWF open with a banner)."""
    while True:
        stripped = text.lstrip()
        if stripped.startswith("//"):
            text = stripped.partition("\n")[2]
        elif stripped.startswith("/*") and "*/" in stripped:
            text = stripped.partition("*/")[2]
        else:
            return stripped


def unwrap_method(text):
    """(parameters, body) of source written as a method declaration (`public function add(a:int):int { ... }`, what JPEXS,
    RustSWF and the others emit); (None, text) when it already is a body, as PySWF's own method source is.
    A parameter is (name, type or None, is_rest)."""
    text = without_leading_comments(text)
    head = _HEAD.match(text)
    if not head:
        return None, text
    depth, quote, position = 1, None, head.end()
    while position < len(text) and depth:
        char = text[position]
        if quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        position += 1
    if depth:
        return None, text
    inside = text[head.end():position - 1]
    rest = text[position:]
    opening, closing = rest.find("{"), rest.rstrip().rfind("}")
    if opening < 0 or closing <= opening:
        return None, text
    parameters = []
    for piece in split_arguments(inside):
        piece = piece.split("=", 1)[0].strip()
        rest_parameter = piece.startswith("...")
        name, _, kind = piece.lstrip(".").partition(":")
        if name.strip():
            parameters.append((name.strip(), kind.strip() or None, rest_parameter))
    return parameters, rest[opening + 1:closing]


def method_body(text, known_names=()):
    """The body the single-unit path takes: the statements of the method, its declared parameters turned into local aliases
    of the registers `localN` the path binds (`var param1:int = local1;`), unless the ABC already names that register."""
    parameters, body = unwrap_method(text)
    if parameters is None:
        return body
    aliases = []
    for index, (name, kind, is_rest) in enumerate(parameters, start=1):
        if name == "local%d" % index or name in known_names:
            continue
        aliases.append("var %s%s = local%d;\n" % (name, ":" + kind if kind in PRIMITIVES and not is_rest else "", index))
    return "".join(aliases) + body


# --- the worker: compile units one at a time and splice them into the original ---------------------------------------

def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def splice_stage(request):
    """Splice every unit of the request into the original SWF: -> [{id, ok, file | reason}]. Runs in its own process."""
    from py_swf.script_inventory import enumerate_scripts
    from py_swf.unit_editing import edit_unit
    data = Path(request["original"]).read_bytes()
    folder = Path(request["folder"])
    (folder / "cand").mkdir(parents=True, exist_ok=True)
    units = {unit.document_id: unit for unit in enumerate_scripts(data) if not unit.diagnostic}
    results = []
    for index, task in enumerate(request["units"]):
        try:
            candidate = data
            for part in task["parts"]:
                unit = units.get(part["id"])
                if unit is None:
                    raise ValueError("la unidad no existe en el SWF original")
                if request["stage"] == "probe":
                    from py_swf.avm1 import disassemble_avm1
                    from py_swf.avm2 import disassemble_instructions
                    if unit.language == "as3":
                        code = ("findpropstrict trace\npushstring \"%s\"\ncallproperty trace 1\npop\n" % MARKER
                                + disassemble_instructions(unit.abc.constant_pool, unit.code, exact=True, exceptions=unit.body.exceptions))
                    else:
                        code = "push \"%s\"\ntrace\n%s" % (MARKER, disassemble_avm1(unit.code))
                    candidate = edit_unit(candidate, {"document_id": unit.document_id, "bytecode_sha256": sha(unit.code)}, code, source=False)
                    continue
                text = read_text(part["source_file"])
                if unit.language == "as3":
                    pool = unit.abc.constant_pool
                    known = {pool.strings[i] for i in unit.abc.methods[unit.body.method].param_names if 0 < i < len(pool.strings)}
                    text = method_body(text, known)
                candidate = edit_unit(candidate, {"document_id": unit.document_id, "bytecode_sha256": sha(unit.code)}, text,
                                      source=True, allow_initializers=True)
            target = folder / "cand" / ("%d.swf" % index)
            target.write_bytes(candidate)
            results.append({"id": task["id"], "ok": True, "file": str(target)})
        except Exception as error:  # whatever the compiler said is the reason; it never stops the other units
            message = " ".join(str(error).split())
            results.append({"id": task["id"], "ok": False, "reason": ("%s: %s" % (type(error).__name__, message))[:220]})
    return results


def worker_main(request_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    write_json(Path(request["folder"]) / "worker-result.json", {"results": splice_stage(request)})
    return 0


def run_stage(stage, original, tasks, folder):
    """Run the splice or probe stage for `tasks` in a process of its own. -> ({id: result}, why not | None)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "request.json", {"stage": stage, "original": str(original), "folder": str(folder), "units": tasks})
    process = bounded_run([sys.executable, str(Path(__file__).resolve()), "--worker", str(folder / "request.json")], PROJECT,
                          timeout=SPLICE_TIMEOUT)
    path = folder / "worker-result.json"
    if not path.is_file():
        why = "el compilador superó el plazo" if process["timed_out"] else "el compilador se cayó: " + ((process["stderr"].strip().splitlines() or ["sin mensaje"])[-1])[:160]
        return {}, why
    return {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))["results"]}, None


# --- what to judge ----------------------------------------------------------------------------------------------------

def scope_reason(reason):
    """Why the single-unit path cannot take a method for any engine, in a few words (`source_edit_reason` speaks English)."""
    lowered = reason.lower()
    if "activation" in lowered:
        return "cierres con activación"
    if "namespace" in lowered or "binding" in lowered:
        return "espacios de nombres privados o explícitos"
    if "multiname" in lowered:
        return "multinombres de ejecución"
    if "shadowed" in lowered:
        return "tipos ocultos por un miembro o parámetro"
    return "código que exige P-code exacto"


def plan_case(data, canonical, cap):
    """The units of one case the single-unit path can take, chosen from the SWF's inventory (the same for every engine).

    -> {"units": [{id, kind, parts: [ids]}] (at most `cap` when it is positive), "methods": [ids to keep the source of],
        "total": n, "out_of_scope": {reason: count}}. Parts of a class are all the editable methods of that class."""
    from py_swf.canonical_inventory import build_inventory
    from py_swf.editing import source_edit_reason
    from py_swf.script_inventory import enumerate_scripts
    inventory = build_inventory(data)
    parsed = {unit.document_id: unit for unit in enumerate_scripts(data) if not unit.diagnostic}
    editable, refused = {}, Counter()
    for unit in parsed.values():
        if unit.language != "as3":
            continue
        try:
            reason = source_edit_reason(unit.abc, unit.body, allow_initializers=True)
        except Exception as error:  # an ABC the path cannot even analyse is out of its reach
            reason = "%s: %s" % (type(error).__name__, error)
        if reason is None:
            editable[unit.document_id] = unit
        else:
            refused[scope_reason(reason)] += 1
    units = [{"id": name, "kind": "method", "parts": [name]} for name in editable]
    for item in inventory["units"]:
        if item["scope"] == "class" and item.get("identity_status") != "diagnostic":
            prefix = item["document_id"].rsplit("/", 1)[0]
            parts = [name for name in ("%s/method:%d" % (prefix, i) for i in item["context"]["method_indices"]) if name in editable]
            if parts:
                units.append({"id": item["document_id"], "kind": "class", "parts": parts})
    units += [{"id": name, "kind": "action", "parts": [name]} for name, unit in parsed.items()
              if unit.language == "as2" and unit.kind in ACTION_KINDS]
    units.sort(key=lambda unit: (KINDS.index(unit["kind"]), natural(unit["id"])))
    total = len(units)
    if cap and total > cap:  # an even spread over the sorted list: the same units for every engine, every run
        units = [units[round(i * (total - 1) / (cap - 1))] for i in range(cap)] if cap > 1 else units[:1]
    methods = sorted({part for unit in units for part in unit["parts"] if unit["kind"] != "action"})
    return {"units": units, "methods": methods, "total": total, "out_of_scope": dict(refused)}


def probe_case(original, plan, folder):
    """Which parts of the plan does the case's trace execute? -> {id: True | False | None (could not be probed)}.

    Each part is rebuilt with a marker trace at its entry; if the marker shows up in the output the trace is no longer the
    expected one and the part ran."""
    parts = sorted({part for unit in plan["units"] for part in unit["parts"]}, key=natural)
    built, why = run_stage("probe", original, [{"id": part, "parts": [{"id": part}]} for part in parts], Path(folder) / "probe")
    return built, parts, why


# --- judging the source of one engine ---------------------------------------------------------------------------------

def engine_sources(job, canonical):
    """What an engine emitted that can be judged: {method id: source file}, {class ids with source}, {action id: source file},
    each checked against the identity and bytecode of the SWF's own inventory. `job` is the finished decompiler job."""
    methods, classes, actions = {}, set(), {}
    source = job.get("source", {})
    for record in source.get("methods", []):
        known = canonical.get((record.get("document_id"), "method"))
        if known and record.get("source_file") and record.get("bytecode_sha256") == known["sha"] and record.get("generation") == "available":
            methods[record["document_id"]] = record["source_file"]
    for record in source.get("documents", []):
        known = canonical.get((record.get("document_id"), record.get("scope")))
        if known is None or not record.get("source_file") or not _same_code(record, known):
            continue
        if record["scope"] == "class":
            classes.add(record["document_id"])
        elif record["scope"] == "action":
            actions[record["document_id"]] = record["source_file"]
    return methods, classes, actions


def judge_engine(engine, job, ctx, folder):
    """Judge every unit of the plan the engine has source for. -> the row of (engine, case)."""
    row = {"engine": engine, "family": ctx["family"], "case": ctx["case"], "language": ctx["language"], "measured": True,
           "units": {name: 0 for name in UNIT_OUTCOMES}, "by_kind": {kind: {name: 0 for name in UNIT_OUTCOMES} for kind in KINDS},
           "details": [], "reason": ""}
    methods, classes, actions = engine_sources(job, ctx["canonical"])
    tasks = []
    for unit in ctx["plan"]["units"]:
        if unit["kind"] == "method" and unit["id"] in methods:
            parts = [{"id": unit["id"], "source_file": methods[unit["id"]]}]
        elif unit["kind"] == "class" and unit["id"] in classes:
            parts = [{"id": part, "source_file": methods[part]} for part in unit["parts"] if part in methods]
        elif unit["kind"] == "action" and unit["id"] in actions:
            parts = [{"id": unit["id"], "source_file": actions[unit["id"]]}]
        else:
            continue
        if parts:
            tasks.append({"id": unit["id"], "kind": unit["kind"], "parts": parts})
    if not tasks:
        return {**row, "reason": "no emitió fuente verificable de ninguna unidad evaluable"}

    def record(task, outcome, reason=""):
        row["units"][outcome] += 1
        row["by_kind"][task["kind"]][outcome] += 1
        if outcome != "same" and len(row["details"]) < 12:
            row["details"].append({"unit": task["id"], "kind": task["kind"], "outcome": outcome, "reason": reason[:160]})

    for start in range(0, len(tasks), CHUNK):
        chunk = tasks[start:start + CHUNK]
        compiled, why = run_stage("splice", ctx["original"], chunk, Path(folder) / ("splice-%03d" % (start // CHUNK)))
        candidates = {}
        for task in chunk:
            result = compiled.get(task["id"])
            if result is None or not result["ok"]:
                record(task, "no_compile", (result or {}).get("reason") or why or "sin resultado del compilador")
            else:
                candidates[task["id"]] = Path(result["file"]).read_bytes()
        verdicts = ctx["oracle"].run(candidates) if candidates else {}
        for task in chunk:
            if task["id"] not in candidates:
                continue
            parts = [ctx["executed"].get(part["id"]) for part in task["parts"]]
            if parts and all(flag is False for flag in parts):
                record(task, "not_run", "la traza del caso nunca ejecuta la unidad")
            elif verdicts.get(task["id"]):
                record(task, "same")
            else:
                record(task, "different", "Ruffle imprime otra traza")
        shutil.rmtree(Path(folder) / ("splice-%03d" % (start // CHUNK)), ignore_errors=True)
    return row


def evaluate_case(case, data, canonical, plan, jobs, runtime, folder, workers=4):
    """All engines on one case. -> rows (one per engine; an engine with no job is a row that says why)."""
    from concurrent.futures import ThreadPoolExecutor
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / "original.swf"
    original.write_bytes(data)
    oracle = TraceOracle(runtime, case["expected_trace"])
    base = {"family": case["family_id"], "case": case["relative"], "language": case["language"]}
    if not oracle.available():
        return [{**base, "engine": engine, "measured": False, "reason": "no está el arnés de Ruffle"} for engine in jobs]
    if not plan["units"]:
        return [{**base, "engine": engine, "measured": True, "units": {name: 0 for name in UNIT_OUTCOMES},
                 "by_kind": {kind: {name: 0 for name in UNIT_OUTCOMES} for kind in KINDS}, "details": [],
                 "reason": "el SWF no tiene unidades que la ruta de edición pueda tomar"} for engine in jobs]
    if not oracle.run({"original": data}).get("original"):
        return [{**base, "engine": engine, "measured": False, "reason": "Ruffle no reproduce la traza del original"} for engine in jobs]
    marks, parts, why = probe_case(original, plan, folder)
    verdicts = oracle.run({part: Path(marks[part]["file"]).read_bytes() for part in parts if part in marks and marks[part]["ok"]})
    # The marker line breaks the expected trace exactly when the part runs; a part that could not be rebuilt stays unknown.
    executed = {part: (not verdicts[part]) if part in verdicts else None for part in parts}
    ctx = {**base, "plan": plan, "canonical": canonical, "oracle": oracle, "original": original, "executed": executed}

    def one(item):
        engine, job = item
        try:
            return judge_engine(engine, job, ctx, folder / engine)
        except Exception as error:  # the benchmark goes on
            return {**base, "engine": engine, "measured": False, "reason": "fallo del banco: %s: %s" % (type(error).__name__, error)}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        rows = list(pool.map(one, jobs.items()))
    return rows


# --- the table ------------------------------------------------------------------------------------------------------

def counts(items):
    total = Counter()
    for item in items:
        total.update(item)
    return {name: total.get(name, 0) for name in UNIT_OUTCOMES}


def line_counts(units):
    evaluated = sum(units.values())
    return {**units, "evaluated": evaluated, "judged": evaluated - units["not_run"], "fidelity": unit_fidelity(units)}


def table(rows, out_of_scope=None):
    """One line per engine over the cases it was measured on, ranked only among engines measured on the same cases."""
    by_engine = defaultdict(list)
    for row in rows:
        by_engine[row["engine"]].append(row)
    together = comparable_engines(rows)
    lines = []
    for engine, items in by_engine.items():
        ran = [r for r in items if r.get("measured", True)]
        units = counts(r["units"] for r in ran)
        kinds = {kind: line_counts(counts(r["by_kind"][kind] for r in ran)) for kind in KINDS}
        lines.append({"engine": engine, "name": ENGINES[engine].name, "cases": len(ran), **line_counts(units), "by_kind": kinds,
                      "comparable": engine in together,
                      "reasons": sorted({r["reason"] for r in items if r.get("reason")})[:3]})
    lines.sort(key=lambda l: (l["cases"] == 0, not l["comparable"], -(l["fidelity"] if l["fidelity"] is not None else -1), l["engine"]))
    rivals = [l for l in lines if l["comparable"] and l["fidelity"] is not None]
    # a lead of one unit or less is not a ranking
    winner = rivals[0]["engine"] if len(rivals) > 1 and (rivals[0]["fidelity"] - rivals[1]["fidelity"]) * min(rivals[0]["judged"], rivals[1]["judged"]) > 1 else None
    note = None if winner else ("sin ganador claro: la ventaja es de una unidad o menos" if len(rivals) > 1
                                else "sin ganador: ningún par de motores juzgó unidades sobre los mismos casos")
    return {"unit": "unidades (clases, métodos y acciones) cuyo fuente, compilado solo y empalmado en el SWF original, imprime la misma traza que el original",
            "winner": winner, "note": note, "corpus_cases": len({r["case"] for r in rows}),
            "out_of_scope": out_of_scope or {}, "kinds": KIND_LABELS, "engines": lines}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker_main(sys.argv[2])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("swf", type=Path, help="un SWF del corpus: se muestran sus unidades evaluables")
    parser.add_argument("--cap", type=int, default=20)
    args = parser.parse_args()
    from py_swf.closeness import canonical_units
    data = args.swf.read_bytes()
    plan = plan_case(data, canonical_units(data), args.cap)
    print(json.dumps({"total": plan["total"], "elegidas": len(plan["units"]), "fuera_de_alcance": plan["out_of_scope"],
                      "unidades": [(unit["kind"], unit["id"]) for unit in plan["units"]]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
