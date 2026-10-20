"""Recompile focused array/object/computed-access statements from source corpora.

Usage: .venv/Scripts/python.exe tools/verify_dynamic_corpus.py PATH [PATH ...]
Paths may be ActionScript source files or directories containing .as/.xml files.
This verifies compilation only; it does not claim semantic equivalence.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from py_swf.avm1 import assemble_avm1
from py_swf.compiler import compile_actionscript


_CANDIDATE = re.compile(r"(?:=\s*\[|=\s*\{|\[[^\]\n]+\]\s*(?:=|\+\+|--)?)")


def candidate_statements(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    snippets = re.findall(r"<!\[CDATA\[(.*?)\]\]>", text, re.S) if path.suffix.lower() == ".xml" else [text]
    candidates = []
    for snippet in snippets:
        for line in snippet.splitlines():
            source = line.strip()
            if (source.startswith("delete ") or re.search(r"\]\s*\(", source)):
                continue
            if _CANDIDATE.search(source) and ";" in source and not source.startswith(("//", "*")):
                candidates.append(source)
    return list(dict.fromkeys(candidates))


def verify(paths: list[Path], limit: int | None = None) -> dict:
    files = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.suffix.lower() in (".as", ".xml")))
        elif path.is_file() and path.suffix.lower() in (".as", ".xml"):
            files.append(path)
    results = []
    for path in files:
        for source in candidate_statements(path):
            if limit is not None and len(results) >= limit:
                break
            try:
                assembly = compile_actionscript(source, "as2")
                assemble_avm1(assembly)
                results.append({"file": str(path), "source": source, "status": "compiled"})
            except (SyntaxError, ValueError) as error:
                results.append({"file": str(path), "source": source, "status": "rejected", "diagnostic": str(error)})
        if limit is not None and len(results) >= limit:
            break
    return {"files_scanned": len(files), "candidates": len(results),
            "compiled": sum(r["status"] == "compiled" for r in results),
            "rejected": sum(r["status"] == "rejected" for r in results), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    report = verify(args.paths, args.limit)
    for item in report["results"]:
        print(f'{item["status"].upper()} {item["file"]}: {item["source"]}')
        if item.get("diagnostic"):
            print("  " + item["diagnostic"])
    print(f'Corpus candidates: {report["compiled"]}/{report["candidates"]} compiled; {report["files_scanned"]} source files scanned (compilation only, no semantic-parity claim).')
    return 0 if report["candidates"] and not report["rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
