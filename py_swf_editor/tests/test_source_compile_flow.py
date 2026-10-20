from fastapi.testclient import TestClient

from py_swf.avm1 import assemble_avm1, disassemble_avm1
from py_swf.compiler import compile_actionscript
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.swf_parser import SWFTag
from server.app import app
from tests.conftest import make_synthetic_swf


def test_compiler_output_is_accepted_and_keeps_assignment_stack_order():
    asm = compile_actionscript('score = 10; trace(score);', target='as1')
    bytecode = assemble_avm1(asm)
    source, error = decompile_avm1(bytecode)
    assert error is None
    assert 'score = 10;' in source
    assert 'trace(score);' in source


def test_compile_endpoint_rewrites_script_with_undo():
    client = TestClient(app)
    data = make_synthetic_swf([SWFTag(12, assemble_avm1('push "old"\ntrace\nend'))])
    sid = client.post('/api/files', files={'file': ('script.swf', data)}).json()['session_id']
    result = client.post(f'/api/files/{sid}/tags/0/compile-source', json={'source': 'trace("source");'})
    assert result.status_code == 200, result.text
    raw = client.get(f'/api/files/{sid}/tags/0/raw').content
    assert 'push "source"' in disassemble_avm1(raw)
    assert client.post(f'/api/files/{sid}/undo').status_code == 200
    assert client.get(f'/api/files/{sid}/download').content == data


def test_unsupported_source_does_not_silently_replace_script():
    client = TestClient(app)
    data = make_synthetic_swf([SWFTag(12, assemble_avm1('push "old"\ntrace\nend'))])
    sid = client.post('/api/files', files={'file': ('script.swf', data)}).json()['session_id']
    response = client.post(f'/api/files/{sid}/tags/0/compile-source', json={'source': 'with (scope) { trace("source"); }'})
    assert response.status_code == 422
    assert 'Unsupported syntax' in response.json()['detail']
    assert client.get(f'/api/files/{sid}/download').content == data
    assert client.get(f'/api/files/{sid}/history').json()['undo'] == 0
