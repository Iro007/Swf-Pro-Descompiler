"""Read-only container/payload comparison for fixed public corpus samples."""
import hashlib
import json
from pathlib import Path
import sys
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from py_swf.swf_parser import SWFFile,decompress_zws


def uncompressed_payload(data):
    if data[:3]==b"CWS":
        return zlib.decompress(data[8:])
    if data[:3]==b"ZWS":
        return decompress_zws(data[8:],int.from_bytes(data[4:8],"little"))
    if data[:3]==b"FWS":
        return data[8:]
    raise ValueError("Not a supported SWF container")


def inspect(path):
    path=Path(path)
    original=path.read_bytes()
    swf=SWFFile()
    swf.read_bytes(original)
    saved=swf.save_bytes()
    left,right=uncompressed_payload(original),uncompressed_payload(saved)
    return {"file":str(path.relative_to(ROOT)),"signature":original[:3].decode(),"original_bytes":len(original),"saved_bytes":len(saved),
            "container_identical":original==saved,"header_identical":original[:8]==saved[:8],"uncompressed_payload_identical":left==right,
            "payload_sha256":hashlib.sha256(left).hexdigest(),"reencoded_payload_sha256":hashlib.sha256(right).hexdigest(),
            "original_preserved":path.read_bytes()==original}


if __name__=="__main__":
    folder=ROOT / "third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata"
    for relative in ("as3_ctor_field_init/bin/as3_ctor_field_init.swf","as3_ctor_init_after_local/bin/as3_ctor_init_after_local.swf",
                     "as3_foreach_convert_i/bin/as3_foreach_convert_i.swf","float/bin/float.air_harman.swf"):
        print(json.dumps(inspect(folder / relative)))
