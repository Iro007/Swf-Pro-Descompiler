"""Read-only ABC version diagnostics for the fixed public corpus."""
from pathlib import Path
import sys
import json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from py_swf.swf_parser import SWFFile
from py_swf.avm2 import ABCFile


for relative in ["as3_ctor_field_init/bin/as3_ctor_field_init.swf","as3_foreach_convert_i/bin/as3_foreach_convert_i.swf","float/bin/float.air_harman.swf","as3_new/bin/as3_new.flex.swf"]:
    movie=SWFFile()
    movie.read_bytes((ROOT / "third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata" / relative).read_bytes())
    for i,tag in enumerate(movie.tags):
        if tag.is_doabc:
            raw=tag.parse_doabc()[2]
            version={"file":relative,"tag":i,"major":int.from_bytes(raw[2:4],"little"),"minor":int.from_bytes(raw[:2],"little")}
            try:
                abc=ABCFile()
                abc.parse(raw)
                version["bodies"]=len(abc.method_bodies)
            except Exception as error:
                version["error"]=str(error)
            print(json.dumps(version))
            break
