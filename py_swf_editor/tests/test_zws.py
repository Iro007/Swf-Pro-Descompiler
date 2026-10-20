"""Regression tests for ZWS (LZMA) SWF decoding."""

from pathlib import Path

import pytest

from py_swf.robust_parser import read_swf_bytes_robust
from py_swf.swf_parser import SWFFile

from py_swf.corpus import corpus_root
CORPUS = corpus_root() / "amf3/AmfTest.swf"


def test_zws_roundtrip_generated():
    swf = SWFFile()
    swf.signature = "ZWS"
    swf.version = 13
    original = swf.save_bytes()
    assert original[:3] == b"ZWS"

    parsed = SWFFile()
    parsed.read_bytes(original)
    assert parsed.signature == "ZWS"
    assert [t.tag_type for t in parsed.tags] == [t.tag_type for t in swf.tags]
    assert parsed.save_bytes() == original

    robust = read_swf_bytes_robust(original)
    assert len(robust.tags) == len(swf.tags)


@pytest.mark.skipif(not CORPUS.exists(), reason="JPEXS corpus not available")
def test_zws_real_file_parses():
    data = CORPUS.read_bytes()
    assert data[:3] == b"ZWS"
    swf = SWFFile()
    swf.read_bytes(data)
    assert swf.tags
    robust = read_swf_bytes_robust(data)
    assert len(robust.tags) == len(swf.tags)
