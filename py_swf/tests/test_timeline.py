from py_swf.swf_parser import BitStream
from py_swf.timeline import _read_filter_list


def test_place_object_filter_list_consumes_filter_payload():
    # one BlurFilter: count/id + fixed blurX + fixed blurY + passes/reserved
    stream = BitStream(bytes([1, 1]) + b"\x00" * 9)
    filters = _read_filter_list(stream)
    assert filters[0]["type"] == "blur"
    assert stream.byte_offset == 11
