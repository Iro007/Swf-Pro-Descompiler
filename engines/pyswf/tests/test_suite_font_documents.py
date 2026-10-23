from copy import deepcopy
import pytest
from py_swf.font_documents import read_font,properties,move_point
from py_swf.font_edit import FontWriter,_write_glyph_records
from py_swf.swf_parser import SWFTag,make_rect


def fixture():
    glyph = [[{'cmd':'move','x':0,'y':0},{'cmd':'line','x':50,'y':0},{'cmd':'line','x':50,'y':50},{'cmd':'line','x':0,'y':0}]]
    writer = FontWriter()
    _write_glyph_records(writer,glyph)
    data = writer.get_bytes()
    flags = 0x80|0x20|4  # layout, small text, wide codes: preserve all bits
    # Two glyphs: three offsets, relative to the start of the six-byte table.
    offsets = b''.join(v.to_bytes(2,'little') for v in (6,6+len(data),6+len(data)*2))
    layout = (900).to_bytes(2,'little')+(100).to_bytes(2,'little')+(-10).to_bytes(2,'little',signed=True)
    layout += b'\x10\x00\x20\x00' + make_rect(0,50,0,50)*2 + b'\x01\x00\x41\x00\x42\x00\xf6\xff'
    return SWFTag(48,b'\x01\x00'+bytes([flags,7,4])+b'Test'+b'\x02\x00'+offsets+data*2+b'\x41\x00\x42\x00'+layout)


def test_font_properties_preserve_unknown_flags_language_glyphs_codes_layout_tail():
    original = fixture()
    before = read_font(original)
    changed = properties(original,{'font_name':'Changed name','bold':True,'leading':25})
    after = read_font(changed)
    assert changed.data[2]&0x20 and changed.data[3]==7
    assert before['_raw']==after['_raw']
    assert before['codes']==after['codes']
    assert after['layout']['ascent']==900 and after['layout']['descent']==100 and after['layout']['leading']==25
    assert original.data[before['_code_end']+6:]==changed.data[after['_code_end']+6:]


def test_glyph_move_rewrites_only_selected_outline_and_its_bounds():
    original = fixture()
    before = read_font(original)
    changed = move_point(original,0,1,120,10)
    after = read_font(changed)
    assert after['glyphs'][0][0][1]['x']==120
    assert after['_raw'][1]==before['_raw'][1]
    assert after['codes']==before['codes']
    assert after['layout']==before['layout']
    assert changed.data[-8:]==original.data[-8:]  # kerning preserved


def test_bad_font_offsets_and_out_of_range_edits_reject():
    original = fixture()
    broken = bytearray(original.data)
    base = read_font(original)['_base']
    broken[base:base+2]=b'\xff\xff'
    with pytest.raises(ValueError,match='Offsets'):
        read_font(SWFTag(48,bytes(broken)))
    with pytest.raises(ValueError):
        move_point(original,99,0,10,10)
    with pytest.raises(ValueError):
        properties(original,{'leading':40000})
