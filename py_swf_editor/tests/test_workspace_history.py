from fastapi.testclient import TestClient

from py_swf.avm1 import assemble_avm1
from py_swf.swf_parser import SWFTag
from server.app import app
from tests.conftest import make_synthetic_swf

client = TestClient(app)


def open_script():
    data = make_synthetic_swf([SWFTag(12, assemble_avm1('push "before"\ntrace\nend'))])
    return client.post('/api/files', files={'file': ('test.swf', data)}).json()['session_id'], data


def test_snapshot_compare_restore_undo_and_project_roundtrip():
    sid, original = open_script()
    assert client.post(f'/api/files/{sid}/snapshot', json={'name': 'initial'}).status_code == 200
    assert client.post(f'/api/files/{sid}/tags/0/assemble', json={'code': 'push "after"\ntrace\nend'}).status_code == 200
    edited = client.get(f'/api/files/{sid}/download').content
    result = client.get(f'/api/files/{sid}/snapshot/compare', params={'left': 'initial'}).json()
    assert result['identical'] is False
    assert result['changed_tags'][0]['index'] == 0
    assert result['changed_tags'][0]['change'] == 'modified'
    assert client.get(f'/api/files/{sid}/download').content == edited  # comparison is read-only
    assert client.get(f'/api/files/{sid}/history').json()['dirty'] is True
    assert client.post(f'/api/files/{sid}/undo').status_code == 200
    assert client.get(f'/api/files/{sid}/history').json()['dirty'] is False
    assert client.get(f'/api/files/{sid}/download').content == original
    assert client.post(f'/api/files/{sid}/redo').status_code == 200
    project = client.get(f'/api/files/{sid}/project').content
    restored_sid = client.post('/api/files/projects', files={'file': ('test.pyswfproj', project)}).json()['session_id']
    assert client.get(f'/api/files/{restored_sid}/history').json()['dirty'] is True
    assert client.post(f'/api/files/{restored_sid}/snapshot/restore', json={'name': 'initial'}).status_code == 200
    assert client.get(f'/api/files/{restored_sid}/download').content == original
    assert client.get(f'/api/files/{restored_sid}/history').json()['dirty'] is False
    assert client.post(f'/api/files/{restored_sid}/undo').status_code == 200
    assert client.get(f'/api/files/{restored_sid}/download').content == edited


def test_snapshot_comparison_not_found_and_header_only_changes():
    sid, _ = open_script()
    assert client.get(f'/api/files/{sid}/snapshot/compare', params={'left': 'missing'}).status_code == 404
    assert client.post(f'/api/files/{sid}/snapshot', json={'name': 'first'}).status_code == 200
    assert client.post(f'/api/files/{sid}/snapshot', json={'name': 'second'}).status_code == 200
    result = client.get(f'/api/files/{sid}/snapshot/compare', params={'left': 'first', 'right': 'second'}).json()
    assert result['identical'] is True
    assert result['changed_tags'] == []
    from server.sessions import registry
    registry.get(sid).swf.frame_rate = 30
    result = client.get(f'/api/files/{sid}/snapshot/compare', params={'left': 'first'}).json()
    assert result['identical'] is False
    assert result['header_changed'] is True
    assert result['changed_tags'] == []


def test_failed_script_edit_preserves_working_file_and_history():
    sid, original = open_script()
    assert client.post(f'/api/files/{sid}/tags/0/assemble', json={'code': 'not_an_opcode'}).status_code == 422
    assert client.get(f'/api/files/{sid}/download').content == original
    history = client.get(f'/api/files/{sid}/history').json()
    assert history['undo'] == 0
    assert history['entries'] == []
    assert history['dirty'] is False
