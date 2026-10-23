import io
import wave
import pytest
from py_swf.swf_parser import SWFFile,SWFTag,make_rect
from py_swf.universal import UniversalSuite
from py_swf.text_fonts import parse_edit_text
from py_swf.sounds import export_sound
from tests.test_suite_font_documents import fixture as font_fixture


@pytest.fixture
def suite(tmp_path):
    movie=SWFFile()
    movie.tags=[font_fixture(),SWFTag(37,b'\x09\x00'+make_rect(0,2000,0,1000)+b'\x80\x00value\x00Old\x00'),SWFTag(14,b'\x03\x00\x3e\x02\x00\x00\x00\x00\x00\x01\x00'),SWFTag(1,b''),SWFTag(0,b'')]
    path=tmp_path/'resources.swf'
    path.write_bytes(movie.save_bytes())
    instance=UniversalSuite(tmp_path/'state',tmp_path/'tools.json')
    instance.open(path)
    yield instance
    instance.close()


def test_resource_metadata_and_font_edit_preserve_history(suite):
    before=suite.state()
    info=suite.resource_info({'tag_index':0})
    assert info['kind']=='font' and info['coordinate_units']=='font units'
    assert info['glyphs'][0]['points'][1]['x']==50
    after=suite.resource_edit({'operation':'font-glyph','tag_index':0,'glyph_index':0,'point_index':1,'x':80,'y':20,'base_revision':before['revision']})
    assert after['revision']!=before['revision']
    assert suite.resource_info({'tag_index':0})['glyphs'][0]['points'][1]['x']==80
    assert suite.history()['revision']==before['revision']
    with pytest.raises(ValueError,match='cambió'):
        suite.resource_edit({'operation':'font-properties','tag_index':0,'bold':True,'base_revision':after['revision']})


def test_text_and_audio_edit_by_api_without_window(suite,tmp_path):
    before=suite.state()
    assert suite.resource_info({'tag_index':1})['parsed']['initial_text']=='Old'
    changed=suite.resource_edit({'operation':'text','tag_index':1,'text':'Texto nuevo','base_revision':before['revision']})
    assert suite.resource_info({'tag_index':1})['parsed']['initial_text']=='Texto nuevo'
    wav=tmp_path/'new.wav'
    with wave.open(str(wav),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(44100);stream.writeframes(b'\x01\x00\x02\x00\x03\x00')
    final=suite.resource_edit({'operation':'replace-sound','tag_index':2,'path':str(wav),'base_revision':changed['revision']})
    assert final['validation']['valid']
    data,extension=export_sound(suite.movie().tags[2])
    assert extension=='wav'
    with wave.open(io.BytesIO(data)) as stream:
        assert stream.getnframes()==3 and stream.readframes(3)==b'\x01\x00\x02\x00\x03\x00'
