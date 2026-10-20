from pathlib import Path
import struct

import pytest

from py_swf.avm2 import ABCFile
from py_swf.script_inventory import enumerate_scripts


def float_edit_tag():
    from tests.native_fixtures import as3_tag
    from py_swf.swf_parser import SWFTag

    abc=ABCFile()
    abc.parse(as3_tag().parse_doabc()[2])
    abc.major_version,abc.minor_version=47,16
    abc.constant_pool.float_raw.extend((struct.pack("<f",1.25),bytes.fromhex("0100807f")))
    abc.constant_pool.floats.extend((1.25,float("nan")))
    return SWFTag(82,b"\0\0\0\0float-edit\0"+abc.serialize())


def test_source_edit_preserves_abc47_float_bits_on_copy():
    from py_swf.editing import edit_script
    from tests.native_fixtures import swf_with
    from py_swf.swf_parser import SWFFile

    tag=float_edit_tag()
    original=tag.data
    replacement,diagnostic=edit_script(tag,"return score + 7;",body_index=1,source=True)
    assert tag.data==original and diagnostic["engine"]=="native"
    saved=swf_with([replacement],"CWS")
    swf=SWFFile()
    swf.read_bytes(saved)
    abc=ABCFile()
    abc.parse(swf.tags[0].parse_doabc()[2])
    assert (abc.major_version,abc.minor_version)==(47,16)
    assert abc.constant_pool.float_raw[1:]==[struct.pack("<f",1.25),bytes.fromhex("0100807f")]
    assert abc.serialize()==swf.tags[0].parse_doabc()[2]


def extended_abc(major=47,minor=16,raw_values=(struct.pack("<f",1.25),bytes.fromhex("0100807f"))):
    # Pool order is int, uint, double, optional decimal, optional float,
    # string, namespace, ns_set, multiname. Signaling NaN bits stay opaque.
    data=struct.pack("<HH",minor,major)+b"\0\0\0"
    if minor>=17:
        data+=b"\0"
    if (major,minor)>=(47,16):
        data+=bytes([len(raw_values)+1])+b"".join(raw_values)
    data+=b"\0\0\0\0"
    data+=b"\x01\0\0\0\0"  # one parameterless method
    data+=b"\0\0\x01\0\0"  # metadata=0, classes=0, script(init=0,traits=0)
    data+=b"\x01\0\0\x01\0\0\x01\x47\0\0"  # returnvoid body
    return data


def test_abc47_float_pool_does_not_desynchronize_methods_and_nan_roundtrip():
    raw=extended_abc()
    abc=ABCFile()
    abc.parse(raw)
    assert abc.major_version==47 and len(abc.method_bodies)==1
    assert abc.method_bodies[0].code==b"\x47"
    assert abc.serialize()==raw


def test_decimal_layout_gate_keeps_opaque_payloads():
    raw=extended_abc(major=46,minor=17,raw_values=())
    abc=ABCFile()
    abc.parse(raw)
    assert abc.serialize()==raw


def test_float_mutation_is_rejected_instead_of_losing_nan_bits():
    abc=ABCFile()
    abc.parse(extended_abc())
    abc.constant_pool.floats[1]=2.0
    with pytest.raises(ValueError,match="raw encoding"):
        abc.serialize()


@pytest.mark.parametrize("relative,expected",[
    ("as3_ctor_field_init/bin/as3_ctor_field_init.swf",22),
    ("as3_ctor_init_after_local/bin/as3_ctor_init_after_local.swf",6),
    ("as3_foreach_convert_i/bin/as3_foreach_convert_i.swf",11),
    ("float/bin/float.air_harman.swf",4),
])
def test_real_abc47_units_not_replaced_by_one_eof_diagnostic(relative,expected):
    from py_swf.corpus import corpus_root
    path=corpus_root() / relative
    if not path.exists():
        pytest.skip("Pinned public corpus unavailable")
    units=list(enumerate_scripts(path.read_bytes()))
    assert not any(u.diagnostic for u in units)
    assert len(units)==expected
