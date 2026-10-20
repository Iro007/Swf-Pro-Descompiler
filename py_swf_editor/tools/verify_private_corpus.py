"""Replay the unchanged historical denominator and safely recompile gained units."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.native_source_coverage import survey
from py_swf.avm2 import ABCFile, disassemble_instructions
from py_swf.bytecode_validation import decode_avm2
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.engines import NativePythonEngine
from py_swf.editing import source_edit_reason
from py_swf.swf_parser import SWFFile


def verify(baseline_path,output):
    baseline=json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    files,gained,regressions,edits=[],[],[],[]
    for previous in baseline["files"]:
        path=ROOT / previous["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest()==previous["sha256"],"Historical corpus changed"
        current=survey(path)
        files.append(current)
        old={(c["tag"],c.get("body")):c for c in previous["cases"]}
        assert len(current["cases"])==len(old),"Historical denominator changed"
        movie=SWFFile()
        movie.read_bytes(path.read_bytes())
        for case in current["cases"]:
            key=(case["tag"],case.get("body"))
            row={"file":str(path.relative_to(ROOT)),"tag":key[0],"body":key[1],"method":case.get("method")}
            if old[key]["structured"] and not case["structured"]:
                regressions.append({**row,"diagnostic":case["diagnostic"]})
            if not old[key]["structured"] and case["structured"]:
                gained.append(row)
                if key[1] is None:
                    continue
                abc=ABCFile()
                abc.parse(movie.tags[key[0]].parse_doabc()[2])
                body=abc.method_bodies[key[1]]
                reason=source_edit_reason(abc,body)
                if reason:
                    edits.append({**row,"status":"protected","diagnostic":reason})
                    continue
                source,error=decompile_method(abc,body)
                try:
                    saved=NativePythonEngine().compile_source(path.read_bytes(),key[0],source,key[1])
                    reopened=SWFFile()
                    reopened.read_bytes(saved)
                    fresh=ABCFile()
                    fresh.parse(reopened.tags[key[0]].parse_doabc()[2])
                    new_body=fresh.method_bodies[key[1]]
                    private=lambda a,b:{i.args[0] for i in decode_avm2(b.code) if i.name in ("getlex","getproperty","setproperty","findpropstrict","findproperty","callproperty","callpropvoid") and a.constant_pool.multinames[i.args[0]]["kind"]==7 and a.constant_pool.namespaces[a.constant_pool.multinames[i.args[0]]["ns"]]["kind"]==5}
                    before,after=private(abc,body),private(fresh,new_body)
                    assert after<=before,"Private operands must not acquire another identity"
                    assert abc.constant_pool.namespaces==fresh.constant_pool.namespaces[:len(abc.constant_pool.namespaces)],"Original namespace indices changed"
                    assert all(b.code==fresh.method_bodies[n].code for n,b in enumerate(abc.method_bodies) if n!=key[1])
                    again,error=decompile_method(fresh,new_body)
                    assert error is None,error
                    edits.append({**row,"status":"saved_reopened","private_before":sorted(before),"private_after":sorted(after),
                                  "behavior":"not_verified","candidate_sha256":hashlib.sha256(saved).hexdigest()})
                except Exception as error:
                    edits.append({**row,"status":"compile_unsupported","diagnostic":str(error) or type(error).__name__,"behavior":"not_verified"})
        assert hashlib.sha256(path.read_bytes()).hexdigest()==previous["sha256"]
    total=sum(len(f["cases"]) for f in files)
    structured=sum(c["structured"] for f in files for c in f["cases"])
    result={"engine":"native","scope":baseline["scope"],"baseline_sha256":hashlib.sha256(Path(baseline_path).read_bytes()).hexdigest(),
            "summary":{"total":total,"structured":structured,"pcode":total-structured},"previous_summary":baseline["summary"],
            "gained":gained,"regressions":regressions,"edit_checks":edits,"files":files,"originals_preserved":True,
            "semantic_parity":False,"runtime_verification":"not_run"}
    Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({key:result[key] for key in ("summary","previous_summary","gained","regressions","edit_checks")},ensure_ascii=False,indent=2))
    assert total==baseline["summary"]["total"] and not regressions
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--baseline",type=Path,default=ROOT / "reports/native-source-coverage-postdom.json")
    parser.add_argument("--output",type=Path,default=ROOT / "reports/native-private-corpus.json")
    args=parser.parse_args()
    verify(args.baseline,args.output)
