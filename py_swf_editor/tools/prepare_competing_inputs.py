"""Copy only fixed public corpus inputs into an isolated manual test folder."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT / "third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata"
FILES=("as2_definefunc_regs/as2_definefunc_regs.swf","as3_cross_compile/bin/as3_cross_compile.flex.swf","as3_new/bin/as3_new.flex.swf")


def prepare():
    destination=ROOT / "Competing_tools/benchmark_inputs"
    destination.mkdir(exist_ok=True)
    cases=[]
    for relative in FILES:
        source=SOURCE / relative
        target=destination / source.name
        data=source.read_bytes()
        digest=hashlib.sha256(data).hexdigest()
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            raise ValueError("Existing manual input differs; refusing overwrite")
        if not target.exists():
            shutil.copyfile(source,target)
        cases.append({"file":target.name,"source":relative,"sha256":digest,"bytes":len(data),"public_corpus":True})
    (destination / "manifest.json").write_text(json.dumps({"schema_version":1,"source_revision":"b2ef2d3d3f44291a4bdfa899c986bc39a89fa6aa","cases":cases,"policy":"No personal SWFs; no protected or blocked installers"},indent=2),encoding="utf-8")
    return {"folder":str(destination),"cases":len(cases),"installers_executed":False}


if __name__=="__main__":
    print(json.dumps(prepare(),indent=2))
