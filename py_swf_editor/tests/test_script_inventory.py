import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.script_inventory import enumerate_scripts
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf, JPEXS_TESTDATA


def test_nested_actions_init_button_and_clip_are_all_counted():
    action = assemble_avm1('push "event"\ntrace\nend')
    sprite = SWFTag(39, b'\x01\0\x01\0' + SWFTag(12, action).pack() + SWFTag(0, b'').pack())
    button = SWFTag(7, b'\x02\0\0' + action)
    button2 = SWFTag(34, b'\x03\0\0\x03\0\0' + b'\0\0\x01\0' + action)
    clip = SWFTag(26, b'\x80\x01\0' + b'\0\0' + (1).to_bytes(4,'little') + (1).to_bytes(4,'little') + len(action).to_bytes(4,'little') + action + b'\0'*4)
    data = make_synthetic_swf([sprite, SWFTag(59, b'\x01\0' + action), button, button2, clip])
    units = list(enumerate_scripts(data))
    assert [u.document_id for u in units] == ['tag:0/0/actions', 'tag:1/actions', 'tag:2/button:0', 'tag:3/button2:0', 'tag:4/clip:0']
    assert all(u.code == action and not u.diagnostic for u in units)


def test_bad_container_is_counted_not_silently_dropped():
    units = list(enumerate_scripts(make_synthetic_swf([SWFTag(34, b'\x01\0\0\xff\xff') ])))
    assert len(units) == 1 and units[0].diagnostic


@pytest.mark.skipif(not JPEXS_TESTDATA.is_dir(), reason="Local corpus unavailable")
def test_real_inventory_includes_initializers_and_inner_methods():
    data = (JPEXS_TESTDATA / 'as3_new/bin/as3_new.flex.swf').read_bytes()
    units = list(enumerate_scripts(data))
    assert any(u.name.endswith('.constructor') for u in units)
    assert any(u.name.endswith('.static_init') for u in units)
    assert any(u.name.endswith('.init') for u in units)
    assert any(u.name.startswith('method_') for u in units)
    assert len({u.document_id for u in units}) == len(units)
