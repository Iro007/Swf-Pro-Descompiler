"""Binary shape rebuild contracts, without a viewer or browser."""
from copy import deepcopy

import pytest

from py_swf.shape_edit import (
    BitWriter, _write_edges, _write_fill_style_array, _write_line_style_array,
    rebuild_shape_tag, update_shape_vertex,
)
from py_swf.shapes import parse_shape_tag
from py_swf.swf_parser import BitStream, SWFFile, SWFTag, make_rect
from py_swf.universal import UniversalSuite


def color(r=12, g=34, b=56, a=255):
    return dict(r=r, g=g, b=b, a=a)


def matrix():
    return dict(scaleX=2.5, scaleY=1.25, rotateSkew0=-0.5, rotateSkew1=0.75,
                translateX=70000, translateY=-40000)


def fixture(tag_type=32):
    parsed = dict(
        shape_id=513,
        bounds=dict(xmin=-200, xmax=2000, ymin=-500, ymax=2000),
        fill_styles=[dict(type=0, color=color()), dict(type=0, color=color(90,80,70))],
        line_styles=[dict(width=500, color=color(10,20,30)), dict(width=20, color=color())],
        groups=[dict(fill_style0=0, fill_style1=2, line_style=2, subpaths=[
            [dict(cmd='move',x=10,y=20),dict(cmd='line',x=30,y=20),
             dict(cmd='curve',cx=40,cy=30,x=30,y=40)],
            [dict(cmd='move',x=-5,y=-10),dict(cmd='line',x=0,y=0)],
        ])],
    )
    if tag_type == 83:
        parsed.update(edge_bounds=dict(xmin=-100,xmax=1000,ymin=-200,ymax=1200),shape_flags=5)
        for style in parsed['line_styles']:
            style.update(startCap=2,joinStyle=2,noHScale=True,noVScale=False,
                         pixelHinting=True,noClose=True,endCap=1,reserved=17,miterLimit=1024)
    return parsed


@pytest.mark.parametrize('tag_type',[2,22,32,83])
def test_shape_binary_roundtrip_multiple_subpaths_and_power_of_two_style_counts(tag_type):
    original=fixture(tag_type)
    decoded=parse_shape_tag(rebuild_shape_tag(original,tag_type))
    assert decoded==original
    assert parse_shape_tag(rebuild_shape_tag(decoded,tag_type))==original


def test_straight_edge_number_of_bits_precedes_general_line_flag():
    writer=BitWriter()
    _write_edges(writer,[dict(fill_style0=0,fill_style1=0,line_style=0,
                             subpaths=[[dict(cmd='move',x=0,y=0),dict(cmd='line',x=1,y=0)]])],0,0)
    stream=BitStream(writer.get_bytes())
    assert stream.read_bits(1)==0 and stream.read_bits(5)==1  # StateMoveTo
    move_bits=stream.read_bits(5)
    assert (stream.read_signed_bits(move_bits),stream.read_signed_bits(move_bits))==(0,0)
    assert stream.read_bits(1)==1 and stream.read_bits(1)==1
    number_of_bits=stream.read_bits(4)+2
    assert number_of_bits==6 and stream.read_bits(1)==1
    assert (stream.read_signed_bits(number_of_bits),stream.read_signed_bits(number_of_bits))==(20,0)


@pytest.mark.parametrize('fill_type',[0x10,0x12,0x13,0x40,0x41,0x42,0x43])
def test_gradient_and_bitmap_matrices_preserve_fixed_values_and_byte_aligned_ids(fill_type):
    parsed=fixture(83)
    style=dict(type=fill_type,matrix=matrix())
    if fill_type<0x40:
        style['gradient']=dict(spread=2,interpolation=1,stops=[
            dict(ratio=0,color=color(a=70)),dict(ratio=255,color=color(255,0,0,a=220)),
        ])
        if fill_type==0x13:style['gradient']['focalPoint']=-0.5
    else:style['bitmapId']=500
    parsed['fill_styles'][1]=style
    parsed['line_styles'][0].pop('color')
    parsed['line_styles'][0]['fillStyle']=deepcopy(style)
    tag=rebuild_shape_tag(parsed,83)
    assert parse_shape_tag(tag)==parsed
    if fill_type==0x13:
        assert b'\x80\xff' in tag.data  # signed FIXED8 little endian, not ff80


def test_byte_aligned_integer_writes_advance_cursor():
    writer=BitWriter()
    writer.write_u16_le(500);writer.write_bits(0x44,8)
    writer.write_u32_le(123456789);writer.write_bits(0x55,8)
    assert writer.get_bytes()==b'\xf4\x01\x44\x15\xcd\x5b\x07\x55'


def test_define_shape_one_literal_255_fill_count_and_limit():
    parsed=fixture(2)
    parsed['fill_styles']=[dict(type=0,color=color()) for _ in range(255)]
    parsed['groups'][0]['fill_style1']=255
    assert parse_shape_tag(rebuild_shape_tag(parsed,2))==parsed
    parsed['fill_styles'].append(dict(type=0,color=color()))
    with pytest.raises(ValueError,match='count format limit'):rebuild_shape_tag(parsed,2)
    assert parse_shape_tag(rebuild_shape_tag(parsed,22))==parsed


def raw_styles_then_edges():
    """Known raw records: style changes without MoveTo, then StateNewStyles."""
    writer=BitWriter()
    writer.data=bytearray(make_rect(0,200,0,200));writer.bit_offset=len(writer.data)*8
    _write_fill_style_array(writer,[dict(type=0,color=color())],False)
    _write_line_style_array(writer,[],False,False)
    writer.write_bits(1,4);writer.write_bits(0,4)
    writer.write_bits(0,1);writer.write_bits(4,5);writer.write_bits(1,1)  # fill1=1
    def horizontal(delta):
        writer.write_bits(1,1);writer.write_bits(1,1);writer.write_bits(3,4)
        writer.write_bits(0,1);writer.write_bits(0,1);writer.write_signed_bits(delta,5)
    horizontal(10)  # no MoveTo: edge starts at (0,0)
    writer.write_bits(0,1);writer.write_bits(4,5);writer.write_bits(0,1)
    horizontal(10)  # unset style must not change the previous edge
    writer.write_bits(0,1);writer.write_bits(16,5);writer.align()
    _write_fill_style_array(writer,[dict(type=0,color=color(200,100,0))],False)
    _write_line_style_array(writer,[],False,False)
    writer.write_bits(1,4);writer.write_bits(0,4)
    horizontal(10)  # current zero style remains zero
    writer.write_bits(0,1);writer.write_bits(4,5);writer.write_bits(1,1)
    horizontal(10)  # index1 in new array becomes flattened index2
    writer.write_bits(0,1);writer.write_bits(0,5)
    return SWFTag(22,b'\x01\x00'+writer.get_bytes())


def test_style_changes_without_move_preserve_each_edge_and_new_style_array_offsets():
    parsed=parse_shape_tag(raw_styles_then_edges())
    assert [(group['fill_style1'],group['subpaths']) for group in parsed['groups']]==[
        (1,[[dict(cmd='move',x=0,y=0),dict(cmd='line',x=0.5,y=0)]]),
        (0,[[dict(cmd='move',x=0.5,y=0),dict(cmd='line',x=1,y=0)]]),
        (0,[[dict(cmd='move',x=1,y=0),dict(cmd='line',x=1.5,y=0)]]),
        (2,[[dict(cmd='move',x=1.5,y=0),dict(cmd='line',x=2,y=0)]]),
    ]
    rebuilt=parse_shape_tag(rebuild_shape_tag(parsed,22))
    def styled_paths(value):
        return [(group['fill_style0'],group['fill_style1'],group['line_style'],path)
                for group in value['groups'] for path in group['subpaths']]
    assert styled_paths(rebuilt)==styled_paths(parsed)  # consecutive equal style runs may coalesce
    assert rebuilt['fill_styles']==parsed['fill_styles']


@pytest.mark.parametrize('mutation,match',[
    (lambda parsed:parsed['groups'][0]['subpaths'][0][1].update(x=100000),'17-bit'),
    (lambda parsed:parsed['groups'][0]['subpaths'][0][0].update(x=float('inf')),'finite'),
    (lambda parsed:parsed['groups'][0].update(fill_style1=3),'missing style'),
    (lambda parsed:parsed['fill_styles'][0].update(type=0x55),'Unsupported shape fill style'),
])
def test_unrepresentable_or_unknown_shape_data_rejects_instead_of_truncating(mutation,match):
    parsed=fixture()
    mutation(parsed)
    before=deepcopy(parsed)
    with pytest.raises(ValueError,match=match):rebuild_shape_tag(parsed,32)
    assert parsed==before


def test_unrepresentable_matrix_scale_rejects():
    parsed=fixture()
    parsed['fill_styles'][0]=dict(type=0x40,bitmapId=2,matrix=matrix())
    parsed['fill_styles'][0]['matrix']['scaleX']=50000
    with pytest.raises(ValueError,match='31-bit'):rebuild_shape_tag(parsed,32)


def test_largest_negative_signed_rect_coordinate_does_not_wrap_bit_width():
    parsed=fixture()
    parsed['bounds']['xmin']=-(1<<30)
    assert parse_shape_tag(rebuild_shape_tag(parsed,32))==parsed
    parsed['bounds']['xmin']-=1
    with pytest.raises(ValueError,match='31-bit'):rebuild_shape_tag(parsed,32)


def test_suite_shape_point_preserves_siblings_styles_shape4_flags_and_edge_bounds(tmp_path):
    parsed=fixture(83)
    movie=SWFFile();movie.tags=[rebuild_shape_tag(parsed,83),SWFTag(9,b'\x12\x34\x56'),SWFTag(1,b'')]
    path=tmp_path/'shape.swf';path.write_bytes(movie.save_bytes())
    suite=UniversalSuite(tmp_path/'state',tmp_path/'tools.json')
    try:
        before=suite.open(path)
        result=suite.resource_edit(dict(operation='shape-point',tag_index=0,group=0,subpath=0,
                                       vertex=1,x=35,y=22,base_revision=before['revision']))
        assert result['validation']['valid']
        update_shape_vertex(parsed,0,0,1,35,22)
        assert parse_shape_tag(suite.movie().tags[0])==parsed
        assert suite.movie().tags[1].data==b'\x12\x34\x56'
        unchanged=suite.state()['revision']
        with pytest.raises(ValueError,match='17-bit'):
            suite.resource_edit(dict(operation='shape-point',tag_index=0,group=0,subpath=0,
                                     vertex=1,x=100000,y=22,base_revision=unchanged))
        assert suite.state()['revision']==unchanged
        suite.history()
        assert parse_shape_tag(suite.movie().tags[0])==fixture(83)
    finally:suite.close()
