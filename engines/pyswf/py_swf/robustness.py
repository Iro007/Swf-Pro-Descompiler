"""How an engine copes with a damaged SWF: which units are still intact, and what happened to the engine.

The robustness benchmark (tools/bench_robustness.py) runs every decompiler on the files of corpus/damaged/. Two
questions are answered here, both without any decompiler:

- Which units of the original SWF are still intact in the damaged file? `intact_units` reads the original's byte-derived
  inventory (`canonical_inventory`) and the damaged byte ranges recorded in the corpus manifest. A unit is intact when no
  damaged range touches the bytes of its own code nor the structure it cannot be read without (the record header of its
  tag, its constant pool and tables, its sprite headers).
- Which of the five outcomes describes the run? `classify` turns what the harness saw from outside the engine (did the
  process finish, how did it end, what did it emit, did it say anything new about the file) into recovered, partial,
  clean_error, crash or hang. docs/BENCHMARKS_CODIGO.md defines each one.

Offsets use the basis of the canonical inventory: the decompressed SWF including its eight-byte header.
"""
from __future__ import annotations

import re

from .bench_results import OUTCOME_SCORES, OUTCOMES  # noqa: F401  (re-exported: the outcomes are defined with the scoring)
from .canonical_inventory import abc_layout, build_inventory
from .swf_parser import BitStream, decompress_body, parse_rect

# Code containers at the top level of a movie (and, through the inventory, the ones nested in sprites).
CODE_TAGS = (12, 59, 72, 82)


# --- the bytes of a movie ------------------------------------------------------------------------

def decompressed(data):
    """The movie with its eight-byte header followed by the inflated body: the offset basis of the inventory."""
    if len(data) < 8 or data[:3] not in (b"FWS", b"CWS", b"ZWS"):
        raise ValueError("Invalid SWF header")
    body = decompress_body(data[:3].decode("ascii"), data[8:], int.from_bytes(data[4:8], "little"))
    return data[:8] + body


def tag_spans(full):
    """Every tag of a decompressed movie (header included) as {path: (record start, payload start, payload end, code)},
    nested sprites included, plus the offset of the first tag. `path` is the tuple of tag indices, as in the inventory."""
    stream = BitStream(full[8:])
    parse_rect(stream)
    first = 8 + stream.byte_offset + 4
    spans = {}

    def walk(start, end, parent=()):
        position, index = start, 0
        while position + 2 <= end:
            header = int.from_bytes(full[position:position + 2], "little")
            code, length, size = header >> 6, header & 63, 2
            if length == 63:
                if position + 6 > end:
                    break
                length, size = int.from_bytes(full[position + 2:position + 6], "little"), 6
            payload = position + size
            stop = min(end, payload + length)
            path = (*parent, index)
            spans[path] = (position, payload, stop, code)
            if code == 39 and stop - payload >= 4:
                walk(payload + 4, stop, path)
            position, index = payload + length, index + 1
            if code == 0:
                break

    walk(first, len(full))
    return spans, first


# --- intact units --------------------------------------------------------------------------------

def _overlaps(ranges, start, end):
    return any(low < end and start < high for low, high in ranges)


def intact_units(original, ranges):
    """Which units of `original` survive damage to the byte `ranges` ([start, end) pairs in the decompressed layout).

    Returns {"documents": {(document_id, scope), ...}, "methods": {document_id, ...}, "total_documents": n,
    "total_methods": n}: the classes, scripts and actions (the units the Índice Flash counts as documents) and the AS3
    method bodies that no range touches. A unit is intact when the damage reaches neither its own bytes nor the
    structure it cannot be read without:

    - AS3: the body records it owns (header, code, exception table and traits; a class owns the bodies of its methods, a
      script those of its own functions and of the classes it declares) and, shared by every unit of the DoABC tag, the
      record header and everything before the first body (constant
      pool, method, class and script tables), plus the headers of the sprites above the tag;
    - AS2: its code bytes and everything else in its tag record (the record header, a sprite id, the button or clip data)."""
    inventory = build_inventory(original)
    full = decompressed(original)
    spans, _ = tag_spans(full)
    bodies, preamble = {}, {}
    for block in inventory["abc_blocks"]:
        path, base = tuple(block["tag_path"]), block["byte_offset"]
        records = {body["method_index"]: (base + body["begin"], base + body["end"])
                   for body in abc_layout(full[base:base + block["bytes"]])["bodies"]}
        bodies[path] = records
        preamble[path] = min((begin for begin, _ in records.values()), default=spans[path][2])
    code, class_methods, script_classes = {}, {}, {}
    for block in inventory["abc_blocks"]:
        for script, classes in block["script_class_indices"].items():
            script_classes[(tuple(block["tag_path"]), int(script))] = classes
    for unit in inventory["units"]:
        if unit["scope"] in ("action", "function") and unit.get("identity_status") != "diagnostic" and unit["bytecode_offset"] is not None:
            code.setdefault(tuple(unit["tag_path"]), []).append((unit["bytecode_offset"], unit["bytecode_offset"] + unit["bytecode_bytes"]))
        elif unit["scope"] == "class" and unit.get("identity_status") != "diagnostic":
            class_methods[(tuple(unit["tag_path"]), unit["class_index"])] = set((unit.get("context") or {}).get("method_indices", ()))

    def structure(path):
        """The bytes of a tag, and of the sprites above it, that stop every unit inside from being read."""
        broken = []
        for depth in range(1, len(path) + 1):
            if path[:depth] not in spans:
                return [(0, 1 << 62)]
            record, payload, end, _ = spans[path[:depth]]
            if depth < len(path):
                broken.append((record, payload + 4))  # a sprite: its record header, its id and its frame count
            elif path in bodies:
                broken.append((record, preamble[path]))
            else:
                cursor = record
                for low, high in sorted(code.get(path, ())):
                    if low > cursor:
                        broken.append((cursor, low))
                    cursor = max(cursor, high)
                broken.append((cursor, end))
        return broken

    cache = {}

    def safe(path, own):
        if path not in cache:
            cache[path] = not any(_overlaps(ranges, low, high) for low, high in structure(path))
        return cache[path] and not any(_overlaps(ranges, low, high) for low, high in own)

    methods, documents = set(), set()
    total_methods = total_documents = 0
    for unit in inventory["units"]:
        scope, path = unit["scope"], tuple(unit["tag_path"])
        if unit.get("identity_status") == "diagnostic" or scope == "function":
            continue
        if scope == "method":
            total_methods += 1
            if safe(path, [bodies[path][unit["method_index"]]] if unit["method_index"] in bodies.get(path, {}) else []):
                methods.add(unit["document_id"])
        elif scope == "action":
            total_documents += 1
            if safe(path, [(unit["bytecode_offset"], unit["bytecode_offset"] + unit["bytecode_bytes"])]):
                documents.add((unit["document_id"], scope))
        elif scope in ("class", "script"):
            total_documents += 1
            indices = set((unit.get("context") or {}).get("method_indices", ()))
            if scope == "script":  # the document of a script holds the classes it declares: their bodies are part of it
                for class_index in script_classes.get((path, unit["script_index"]), ()):
                    indices |= class_methods.get((path, class_index), set())
            owned = [bodies.get(path, {}).get(index) for index in indices]
            if safe(path, [span for span in owned if span]):
                documents.add((unit["document_id"], scope))
    return {"documents": documents, "methods": methods, "total_documents": total_documents, "total_methods": total_methods}


# --- what happened to the engine ----------------------------------------------------------------

# An error text that names a failure inside the engine rather than a problem with the file: the engine did not
# validate its input, it fell over it. Python, Java (JPEXS), Rust, Go and Node (the engines of the suite) are covered.
_INTERNAL = re.compile("|".join((
    r"Traceback \(most recent call last\)",
    r"\b(?:IndexError|KeyError|TypeError|AttributeError|RecursionError|MemoryError|ZeroDivisionError|OverflowError|"
    r"AssertionError|UnboundLocalError|NameError|UnicodeDecodeError|UnicodeEncodeError|struct\.error)\b",
    r"\bjava\.lang\.(?:NullPointer|ArrayIndexOutOfBounds|StringIndexOutOfBounds|IndexOutOfBounds|ClassCast|NegativeArraySize|"
    r"Arithmetic|StackOverflow|OutOfMemory|IllegalState|UnsupportedOperation|ConcurrentModification)\w*",
    r"\n\s+at [\w.$<>]+\([\w$]+\.java:\d+\)",
    r"\bpanicked at\b", r"\bthread '[^']*' panicked", r"\bRUST_BACKTRACE\b", r"\bindex out of bounds: the len is\b",
    r"\bOption::unwrap\(\)|\bResult::unwrap\(\)", r"\battempt to (?:add|subtract|multiply|divide|shift) with overflow\b",
    r"\bhas overflowed its stack\b|\bstack overflow\b",
    r"\bpanic: |\bgoroutine \d+ \[|\bruntime error: |\bnil pointer dereference\b|\bfatal error: ",
    r"\bCannot read propert(?:y|ies) of (?:undefined|null)\b|\bis not a function\b|\bMaximum call stack size exceeded\b|"
    r"\bJavaScript heap out of memory\b|\bReferenceError\b|\bRangeError\b",
    r"\n\s+at [^\n]*\(?[^\n]*:\d+:\d+\)?",
)), re.I)
# Windows ends a process that faulted with an NTSTATUS code (0xC0000005 is an access violation): never a controlled exit.
_FAULT = 0xC0000000


def internal_error(text):
    """True if an error text reads like a stack trace or an unhandled exception of the engine itself."""
    return bool(text) and bool(_INTERNAL.search(text))


def abnormal_exit(returncode):
    """A return code that is a fault of the process (killed, access violation...) rather than a deliberate exit status."""
    if returncode is None:
        return False
    code = returncode & 0xFFFFFFFF
    return returncode < 0 or code >= _FAULT


def classify(process, observation, baseline=(), intact=None):
    """The outcome of one engine on one damaged file, and why.

    `process`     what the harness saw from outside: {"timed_out": bool, "returncode": int|None, "stderr": str}
    `observation` what the worker reported (None if it left no result): {"documents": [...], "methods": [...],
                  "diagnostics": [str], "failure": None | {"kind": "error"|"crash"|"timeout", "message": str}}
                  where a document or method is {"document_id", "scope", "verified": bool, "has_source": bool}
    `baseline`    the diagnostics the same engine gave for the undamaged file: only a message that is new counts as a report
    `intact`      the intact units of the original (`intact_units`), or None when nothing is known

    The order of the questions is the definition: a timeout is a hang; a process that fell (abnormal exit, no result,
    a stack trace, an error the engine did not mean to give) is a crash; no output with a clear message is a clean error;
    output for intact units together with a message about the damage is recovered; any other output is partial."""
    if process.get("timed_out"):
        return "hang", "superó el plazo"
    if observation is None:
        code = process.get("returncode")
        tail = (process.get("stderr") or "").strip().splitlines()
        return "crash", "el proceso terminó sin resultado (código %s)%s" % (code, ": " + tail[-1][:160] if tail else "")
    failure = observation.get("failure") or None
    documents, methods = observation.get("documents", []), observation.get("methods", [])
    produced = any(item.get("has_source") for item in (*documents, *methods))
    if failure and failure.get("kind") == "timeout":
        return "hang", failure.get("message") or "superó el plazo"
    if failure and failure.get("kind") == "crash":
        return "crash", failure.get("message") or "el motor se cayó"
    new = [text for text in observation.get("diagnostics", []) if text not in set(baseline)]
    message = (failure or {}).get("message") or ""
    fell = abnormal_exit(process.get("returncode"))
    if failure and internal_error(message):
        return "crash", message
    if not produced:
        text = message or (new[0] if new else "")
        if fell or internal_error(text):
            return "crash", text or "salida anormal del proceso"
        if text:
            return "clean_error", text
        if observation.get("opened") is False:
            return "clean_error", "el motor no abrió el archivo y no dijo por qué"
        return "clean_error", "sin salida ni mensaje"
    if fell:
        return "crash", message or "salida anormal del proceso"
    reported = bool(new) or bool(message)
    covered = False
    if intact is not None:
        wanted_documents, wanted_methods = intact["documents"], intact["methods"]
        covered = any(item.get("has_source") and item.get("verified") and (item["document_id"], item["scope"]) in wanted_documents
                      for item in documents) or any(
            item.get("has_source") and item.get("verified") and item["document_id"] in wanted_methods for item in methods)
    if covered and reported:
        return "recovered", (new or [message])[0]
    why = "sin aviso del daño" if not reported else "sin salida para lo intacto"
    return "partial", why
