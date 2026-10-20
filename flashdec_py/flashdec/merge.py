"""Choose the best engine output for every script path."""
from __future__ import annotations

from dataclasses import dataclass, field
from posixpath import dirname

from .scoring import score


@dataclass
class Choice:
    path: str
    engine: str
    text: str
    scores: dict = field(default_factory=dict)
    native_path: str | None = None
    only_native: bool = False
    units: list = field(default_factory=list)

    def as_dict(self):
        return {"path": self.path, "engine": self.engine, "native_path": self.native_path,
                "only_native": self.only_native, "units": self.units,
                "scores": {k: (v.as_dict() if v else None) for k, v in self.scores.items()}}


def _pick(candidates: dict[str, str | None], prefer: list[str]):
    scored = {engine: score(text) for engine, text in candidates.items()}
    ranked = sorted(
        (engine for engine, s in scored.items() if s is not None),
        key=lambda engine: (-scored[engine].value, prefer.index(engine) if engine in prefer else 99),
    )
    if not ranked:
        engine = next((e for e in prefer if candidates.get(e) is not None), next(iter(candidates)))
        return engine, scored
    return ranked[0], scored


def merge(native_scripts, jpexs_scripts: dict[str, str] | None, prefer: list[str]) -> list[Choice]:
    native_by_path = {s.path: s for s in native_scripts}
    jpexs_scripts = jpexs_scripts or {}
    pairs: list[tuple[str, object, str | None]] = []  # (output path, native script, jpexs path)
    jpexs_left = [p for p in jpexs_scripts if p not in native_by_path]
    for script in native_scripts:
        if script.path in jpexs_scripts:
            pairs.append((script.path, script, script.path))
    paired_native = {p for p, _, _ in pairs}
    # Event handlers: JPEXS names them from flags we do not reproduce. Pair the
    # leftovers that live in the same container directory, in tag order.
    for script in native_scripts:
        if script.path in paired_native:
            continue
        match = None
        if script.pairable:
            folder = dirname(script.path)
            for candidate in jpexs_left:
                parent = dirname(candidate)
                if parent == folder or parent.startswith(folder + "_") or dirname(parent) == folder:
                    match = candidate
                    break
        if match:
            jpexs_left.remove(match)
            pairs.append((match, script, match))
        else:
            pairs.append((script.path, script, None))
    for path in jpexs_left:
        pairs.append((path, None, path))

    choices = []
    for path, native, jpexs_path in sorted(pairs, key=lambda item: item[0].lower()):
        candidates = {}
        if native is not None:
            candidates["native"] = native.source
        if jpexs_path is not None:
            candidates["jpexs"] = jpexs_scripts[jpexs_path]
        engine, scored = _pick(candidates, prefer)
        choices.append(Choice(path, engine, candidates[engine], scored,
                              native.path if native is not None else None,
                              only_native=native is not None and jpexs_path is None,
                              units=list(native.units) if native is not None else []))
    return choices
