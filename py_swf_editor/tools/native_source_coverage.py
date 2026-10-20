"""Read-only, Java-free coverage survey. Success is reconstruction, not parity."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from py_swf.avm2 import ABCFile, build_method_mapping
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.swf_parser import SWFFile


def survey(path):
    data = path.read_bytes()
    movie = SWFFile()
    movie.read_bytes(data)
    cases = []
    for tag_index, tag in enumerate(movie.tags):
        if tag.is_doabc:
            abc = ABCFile()
            abc.parse(tag.parse_doabc()[2])
            names = build_method_mapping(abc)
            for index, body in enumerate(abc.method_bodies):
                method = names.get(body.method, "")
                if not method.endswith(".run"):
                    continue
                start = time.perf_counter()
                source, error = decompile_method(abc, body)
                cases.append(dict(tag=tag_index, body=index, method=method, structured=error is None, diagnostic=error, elapsed_ms=round((time.perf_counter() - start)*1000, 3)))
        elif tag.tag_type == 12:
            start = time.perf_counter()
            source, error = decompile_avm1(tag.data)
            cases.append(dict(tag=tag_index, structured=error is None, diagnostic=error, elapsed_ms=round((time.perf_counter() - start)*1000, 3)))
    assert path.read_bytes() == data
    return dict(file=str(path), sha256=hashlib.sha256(data).hexdigest(), cases=cases)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/native-source-coverage.json"))
    args = parser.parse_args()
    files = [survey(p) for p in args.files]
    cases = [c for f in files for c in f["cases"]]
    summary = dict(total=len(cases), structured=sum(c["structured"] for c in cases), pcode=sum(not c["structured"] for c in cases))
    diagnostics = Counter(c["diagnostic"].split(" at ")[0] for c in cases if c["diagnostic"])
    report = dict(engine="native", java_used=False, scope="Root DoAction tags and class .run methods only; no Flash execution or semantic parity claim", summary=summary, diagnostics=diagnostics.most_common(), files=files)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    print(json.dumps(diagnostics.most_common(12)))


if __name__ == "__main__":
    main()
