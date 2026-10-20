from pathlib import Path
import pytest

from py_swf.universal import UniversalSuite, sha
from py_swf.project import SwfProject


@pytest.fixture
def saved(tmp_path):
    source = tmp_path / 'sources'
    source.mkdir()
    entry = source / 'Counter.as'
    text = 'package { public class Counter { public function answer():int { return 7; } } }'
    entry.write_text(text)
    suite = UniversalSuite(tmp_path / 'state', tmp_path / 'tools.json')
    suite.compile_project({'profile':'native-as3','entry':str(entry),'output':str(tmp_path/'first.swf')})
    yield suite,entry,text
    suite.close()


def source_doc(suite):
    return suite.dispatch('source_files',{'id':'project:entry'})


def test_sources_only_edit_stale_guard_history_and_project_archive(saved,tmp_path):
    suite,entry,text = saved
    before = suite.state()
    doc = source_doc(suite)
    changed = text.replace('return 7','return 9')
    args = {'id':doc['id'],'source':changed,'base_revision':before['revision'],'base_source_sha256':doc['source_sha256']}
    state = suite.source_edit(args)
    assert state['source_pending'] and state['dirty']
    assert state['revision']==before['revision']
    assert state['source_revision']!=before['source_revision']
    assert suite.project.history[-1]['operation']=='source_edit'
    assert suite.project.history[-1]['metadata']['source_before']!=suite.project.history[-1]['metadata']['source_after']
    assert source_doc(suite)['source']==changed
    assert suite.project.source_documents['project:source:Counter.as']['source']==changed
    with pytest.raises(ValueError,match='fuente cambió'):
        suite.source_edit(args)
    assert suite.history()['source_revision']==before['source_revision']
    assert suite.history(True)['source_revision']==state['source_revision']
    path = tmp_path/'project.pyswfproj'
    suite.dispatch('save_project',{'path':str(path)})
    restored = SwfProject.from_archive(path)
    assert restored.source_documents['project:entry']['pending']
    assert entry.read_text()==text


def test_recompile_saved_sources_keeps_original_and_undo(saved,tmp_path):
    suite,entry,text = saved
    before = suite.state()
    doc = source_doc(suite)
    suite.source_edit({'id':doc['id'],'source':text.replace('return 7','return 12'),'base_revision':before['revision'],'base_source_sha256':doc['source_sha256']})
    result = suite.compile_saved({'output':str(tmp_path/'recompiled.swf')})
    assert result['validation']['valid'] and result['revision']!=before['revision']
    assert not result['source_pending']
    assert suite.project.original!=suite.project.working
    assert entry.read_text()==text
    assert suite.history()['source_pending']
    assert suite.history()['revision']==before['revision']


def test_failed_recompile_does_not_advance_swf_or_lose_saved_draft(saved,tmp_path):
    suite,entry,_ = saved
    before = suite.state()
    doc = source_doc(suite)
    suite.source_edit({'id':doc['id'],'source':'package {','base_revision':before['revision'],'base_source_sha256':doc['source_sha256']})
    source_revision = suite.state()['source_revision']
    with pytest.raises((ValueError,SyntaxError)):
        suite.compile_saved({'output':str(tmp_path/'invalid.swf')})
    assert suite.state()['revision']==before['revision']
    assert suite.state()['source_revision']==source_revision
    assert not (tmp_path/'invalid.swf').exists()


def test_archived_source_path_traversal_is_never_written(saved,tmp_path):
    suite,_,_ = saved
    suite.project.source_documents['project:source:evil']={'filename':'../evil.as','source':'class Evil {}'}
    with pytest.raises(ValueError,match='fuera del proyecto'):
        suite.compile_saved({'output':str(tmp_path/'unexpected.swf')})
    assert not (tmp_path/'unexpected.swf').exists()


def test_legacy_packaged_entry_alias_is_updated_and_rebuilt(tmp_path):
    folder = tmp_path / 'source' / 'demo'
    folder.mkdir(parents=True)
    entry = folder / 'Counter.as'
    text = 'package demo { public class Counter { public function answer():int { return 7; } } }'
    entry.write_text(text)
    suite = UniversalSuite(tmp_path / 'state', tmp_path / 'tools.json')
    try:
        suite.compile_project({'profile':'native-as3','entry':str(entry),'output':str(tmp_path/'original.swf'),
                               'width':640,'height':360,'frame_rate':25})
        # Old v3 archives stored a basename alias, while other sources retained
        # their package paths. Rebuilding must resolve that alias unambiguously.
        suite.project.source_documents['project:entry']['filename'] = 'Counter.as'
        suite.project.suite_metadata.pop('entry_relative')
        doc = source_doc(suite)
        suite.source_edit({'id':doc['id'],'source':text.replace('return 7','return 18'),
                           'base_revision':suite.state()['revision'],'base_source_sha256':doc['source_sha256']})
        assert suite.project.source_documents['project:entry']['filename']=='demo/Counter.as'
        assert suite.project.source_documents['project:source:demo/Counter.as']['source'].endswith('return 18; } } }')
        rebuilt = suite.compile_saved({'output':str(tmp_path/'new.swf')})
        assert rebuilt['validation']['valid']
        assert suite.movie().rect['xmax']==640*20 and suite.movie().rect['ymax']==360*20
        assert suite.movie().frame_rate==25
        assert entry.read_text()==text
    finally:
        suite.close()


def test_rebuild_does_not_mark_old_unit_source_as_current(saved,tmp_path):
    suite,_,_ = saved
    unit = next(unit for unit in suite.units() if 'answer' in unit.name)
    edited = suite.edit({'unit_id':unit.document_id,'code':'return 42;','mode':'source','base_revision':suite.state()['revision']})
    assert suite.decompile({'unit_id':unit.document_id,'engine':'python'})['saved_source']=='return 42;'
    suite.compile_saved({'output':str(tmp_path/'rebuilt-original-source.swf')})
    result = suite.decompile({'unit_id':unit.document_id,'engine':'python'})
    assert result.get('saved_source') is None
    assert suite.project.source_documents[unit.document_id]['working_swf_sha256']==edited['revision']
    assert '7' in result['source']
