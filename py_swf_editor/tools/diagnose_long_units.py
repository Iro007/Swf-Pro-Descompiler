from pathlib import Path
import sys
import json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from py_swf.script_inventory import enumerate_scripts
from py_swf.decompile.lift import avm1_instructions,avm2_instructions
for relative in ("as2_long/as2_long.swf","as3_long/bin/as3_long.flex.swf"):
    data=(ROOT / "third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata" / relative).read_bytes()
    for unit in enumerate_scripts(data):
        if unit.diagnostic:
            continue
        instructions=(avm1_instructions if unit.language=="as2" else avm2_instructions)(unit.code)
        print(json.dumps({"file":relative,"unit":unit.document_id,"bytes":len(unit.code),"instructions":len(instructions)}))
