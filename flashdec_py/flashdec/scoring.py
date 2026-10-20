"""Quality score for one decompiled script file.

The same rules are implemented in RUST SWF/crates/flashdec/src/scoring.rs.
Keep both in sync: a file is judged only by its text, so any engine can be
compared with any other on the same output path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Each occurrence costs HARD_PENALTY points: the engine admits the code is
# missing, wrong or replaced by placeholders.
HARD_MARKERS = (
    "Decompilation error",
    "§§",                        # JPEXS unresolved stack / goto placeholders
    "// no decompilable",        # PySWF method fallback
    "Native reconstruction incomplete",
    "retained as P-code",        # PySWF: one per method left as bytecode
    "Fuente no reconstruida",    # RustSWF
    "pcode_only",
    "Error during",
    "decompilation timeout",
    "//unresolved jump",
)
# Readable but suspicious constructs.
SOFT_MARKERS = (
    "// Unsupported",
    "/* unsupported",
    "unknown_opcode",
    "Code may be obfuscated",
)
HARD_PENALTY = 30
SOFT_PENALTY = 5
UNBALANCED_PENALTY = 40
MAX_MARKER_HITS = 10


@dataclass
class Score:
    value: int
    hard: int = 0
    soft: int = 0
    balanced: bool = True
    reasons: list[str] = field(default_factory=list)

    def as_dict(self):
        return {"value": self.value, "hard": self.hard, "soft": self.soft,
                "balanced": self.balanced, "reasons": self.reasons}


def _strip_strings_and_comments(text: str) -> str:
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'":
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def balanced(text: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for c in _strip_strings_and_comments(text):
        if c in "([{":
            stack.append(c)
        elif c in pairs:
            if not stack or stack.pop() != pairs[c]:
                return False
    return not stack


def score(text: str | None) -> Score | None:
    """None means the engine produced nothing for this path."""
    if text is None or not text.strip():
        return None
    result = Score(100)
    for marker in HARD_MARKERS:
        hits = min(text.count(marker), MAX_MARKER_HITS)
        if hits:
            result.hard += hits
            result.reasons.append(f"{marker!r} x{hits}")
    for marker in SOFT_MARKERS:
        hits = min(text.count(marker), MAX_MARKER_HITS)
        if hits:
            result.soft += hits
            result.reasons.append(f"{marker!r} x{hits}")
    result.balanced = balanced(text)
    if not result.balanced:
        result.reasons.append("unbalanced brackets")
    result.value -= result.hard * HARD_PENALTY + result.soft * SOFT_PENALTY
    if not result.balanced:
        result.value -= UNBALANCED_PENALTY
    return result
