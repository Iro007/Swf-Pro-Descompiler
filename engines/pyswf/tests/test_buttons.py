import pytest

from py_swf import buttons
from py_swf.swf_parser import SWFTag
from tests.conftest import JPEXS_TESTDATA


def test_empty_define_button():
    parsed = buttons.parse_button(SWFTag(7, (12).to_bytes(2, "little") + b"\x00"))
    assert parsed["character_id"] == 12
    assert parsed["records"] == []


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="no corpus")
def test_buttons_in_corpus_parse_without_crashing():
    from py_swf.swf_parser import SWFFile
    root = JPEXS_TESTDATA
    checked = 0
    for path in root.rglob("*.swf"):
        swf = SWFFile()
        try:
            swf.read_bytes(path.read_bytes())
        except Exception:
            continue
        for tag in swf.tags:
            if tag.tag_type in buttons.BUTTON_TAG_TYPES:
                parsed = buttons.parse_button(tag)
                assert parsed is not None
                checked += 1
    assert checked > 0
