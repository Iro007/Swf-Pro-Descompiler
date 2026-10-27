"""How close is an engine's decompiled text to the reference decompiler's (JPEXS)?

This measures textual similarity per verified unit plus how many of the reference's
units the engine also produced. It says nothing about correctness: behavior is judged
elsewhere (recompile and compare the Ruffle trace).

It also counts how many of the SWF's own code units (its ABC/action inventory, no
decompiler involved) an engine emitted verified source for: a figure every engine,
JPEXS included, is judged on equally.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re

from .canonical_inventory import build_inventory

MAX_TOKENS = 20000
MAX_PER_UNIT = 200

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<line>//[^\n]*)
  | (?P<block>/\*.*?(?:\*/|\Z))
  | (?P<str>"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?)
  | (?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<id>[A-Za-z_$][\w$]*)
  | (?P<op>>>>=|<<=|>>=|>>>|===|!==|\.\.\.|\+\+|--|&&|\|\||==|!=|<=|>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<|>>|::|.)
""", re.X | re.S)


def _number(text):
    try:
        value = int(text, 16) if text[:2] in ("0x", "0X") else float(text)
    except ValueError:
        return text
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return repr(value)


def _string(text):
    body = text[1:-1] if len(text) > 1 and text[-1] == text[0] else text[1:]
    body = body.replace("\\'", "'").replace('\\"', '"')
    return "S:" + body


def normalize(source):
    """Tokens of the source with comments, whitespace, quote style and 1 vs 1.0 erased."""
    tokens = []
    for match in _TOKEN.finditer(source or ""):
        kind = match.lastgroup
        if kind in ("ws", "line", "block"):
            continue
        text = match.group()
        tokens.append(_number(text) if kind == "num" else _string(text) if kind == "str" else text)
    return tokens


def similarity(a, b):
    ta, tb = normalize(a)[:MAX_TOKENS], normalize(b)[:MAX_TOKENS]
    if ta == tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return SequenceMatcher(None, ta, tb, autojunk=False).ratio()


def _same_code(unit, known):
    """Hash equality when the engine reports one; otherwise the engine must name exactly the
    method bodies the inventory says the class/script owns (native documents do not carry a hash).
    A script document also carries the methods of the classes it defines, so it may name more; that
    looser match only counts when the engine itself says the script's source is complete, since a
    partial script (P-code where it could not rebuild source) is not source for the whole script."""
    if unit.get("bytecode_sha256") is not None:
        return unit["bytecode_sha256"] == known["sha"]
    if not unit.get("unit_ids"):
        return False
    unit_methods = set(unit["unit_ids"])
    if unit_methods == known["methods"]:
        return True
    return (unit.get("scope") == "script" and unit.get("completeness") == "complete"
            and bool(known["methods"]) and known["methods"].issubset(unit_methods))


def _verified(units, canonical):
    """Units with source whose identity and bytecode match the canonical inventory."""
    good, bad = {}, 0
    for unit in units or []:
        key = (unit.get("document_id"), unit.get("scope"))
        known = canonical.get(key)
        if known is None or unit.get("source") is None or key in good or not _same_code(unit, known):
            bad += 1
            continue
        good[key] = unit
    return good, bad


# What a decompiler emits as a document; methods and nested functions travel inside their class or action.
DOCUMENT_SCOPES = ("class", "script", "action")


def canonical_units(data):
    """The code units of the SWF, keyed by (document_id, scope), from its own inventory."""
    canonical = {}
    for u in build_inventory(data)["units"]:
        if u.get("identity_status") == "diagnostic":
            continue
        prefix = u["document_id"].rsplit("/", 1)[0]
        methods = {"%s/method:%d" % (prefix, i) for i in (u.get("context") or {}).get("method_indices", [])}
        canonical[(u["document_id"], u["scope"])] = {"sha": u["bytecode_sha256"], "methods": methods}
    return canonical


def inventory_coverage(engine_units, canonical):
    """Of the units of the SWF that can be a document (classes, scripts and actions), how many the engine
    emitted verified source for. The units come from the SWF's inventory, never from another decompiler."""
    documents = {key: known for key, known in canonical.items() if key[1] in DOCUMENT_SCOPES}
    emitted, _ = _verified(engine_units, documents)
    return {"units": len(documents), "with_source": len(emitted)}


def compare(engine_units, reference_units, data, canonical=None):
    if canonical is None:
        canonical = canonical_units(data)
    engine, engine_bad = _verified(engine_units, canonical)
    reference, reference_bad = _verified(reference_units, canonical)
    per_unit = []
    for key, unit in reference.items():
        if key in engine:
            per_unit.append({"document_id": key[0], "scope": key[1],
                             "similarity": round(similarity(engine[key]["source"], unit["source"]), 4)})
    total, matched = len(reference), len(per_unit)
    scores = [item["similarity"] for item in per_unit]
    return {"reference_units": total, "matched": matched,
            "coverage": matched / total if total else None,
            "closeness": sum(scores) / matched if matched else None,
            "weighted": sum(scores) / total if total else None,
            "excluded": {"engine": engine_bad, "reference": reference_bad},
            "per_unit": per_unit[:MAX_PER_UNIT]}
