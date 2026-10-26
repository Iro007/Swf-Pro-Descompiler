"""Integration invariants for the application worker, not compiler mirrors."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from py_swf.engines import NativePythonEngine
from py_swf.project import SwfProject
from py_swf.universal import UniversalSuite, publish


@pytest.fixture
def suite(tmp_path):
    data = NativePythonEngine().compile_classes('package { public class Counter { public function answer():int { return 7; } } }')
    source = tmp_path / 'input.swf'
    source.write_bytes(data)
    instance = UniversalSuite(tmp_path / 'recovery', tmp_path / 'tools.json')
    instance.open(source)
    yield instance
    instance.close()


def answer_unit(suite):
    return next(u for u in suite.units() if 'answer' in u.name)


def test_rust_identity_mapping_distinguishes_identical_bytecode_locations(suite):
    from py_swf.universal import sha
    unit = answer_unit(suite)
    correct = {'id':unit.document_id,'sha256':sha(unit.code),'tag_path':list(unit.tag_path),
               'language':'avm2','byte_len':len(unit.code),'event_flags':None}
    class Worker:
        def call(self, operation, args, deadline):
            return {'total':2,'items':[{**correct,'id':'another-method'},correct]} if operation=='scripts' else {}
        def close(self):
            pass
    suite.rust_worker = Worker()
    assert suite._rust_unit(unit)[0]==unit.document_id


def test_rust_identity_mapping_rejects_wrong_event_flags(suite):
    from py_swf.universal import sha
    unit = answer_unit(suite)
    class Worker:
        def call(self, operation, args, deadline):
            return {'total':1,'items':[{'id':unit.document_id,'sha256':sha(unit.code),'tag_path':list(unit.tag_path),
                                      'language':'avm2','byte_len':len(unit.code),'event_flags':1}]} if operation=='scripts' else {}
        def close(self):
            pass
    suite.rust_worker = Worker()
    with pytest.raises(ValueError,match='identidad'):
        suite._rust_unit(unit)


def test_failed_edit_and_stale_edit_preserve_state_and_original(suite):
    state = suite.state()
    source = Path(state['source_path']).read_bytes()
    with pytest.raises((ValueError, SyntaxError)):
        suite.edit({'unit_id': answer_unit(suite).document_id, 'code': 'if (', 'mode': 'source', 'base_revision': state['revision']})
    assert suite.state()['revision'] == state['revision']
    assert not suite.state()['history']['undo']
    with pytest.raises(ValueError, match='cambió'):
        suite.edit({'unit_id': answer_unit(suite).document_id, 'code': 'return 8;', 'mode': 'source', 'base_revision': '0' * 64})
    assert Path(state['source_path']).read_bytes() == source


def test_edit_undo_redo_recovery_and_v3_roundtrip(suite, tmp_path):
    old = suite.state()
    unit_id = answer_unit(suite).document_id
    new = suite.edit({'unit_id': unit_id, 'code': 'return 9;', 'mode': 'source', 'base_revision': old['revision']})
    assert new['revision'] != old['revision']
    assert new['dirty'] and new['validation']['valid']
    assert suite.history()['revision'] == old['revision']
    assert suite.history(True)['revision'] == new['revision']
    recovered = UniversalSuite(suite.directory, tmp_path / 'tools.json')
    assert recovered.state()['revision'] == new['revision']
    assert recovered.state()['history']['undo']
    archive = tmp_path / 'saved.pyswfproj'
    result = recovered.dispatch('save_project', {'path': str(archive)})
    assert result['format_version'] == 3
    loaded = SwfProject.from_archive(archive)
    assert loaded.original != loaded.working
    assert loaded.source_documents[unit_id]['source'] == 'return 9;'
    assert loaded.suite_metadata['protocol_version'] == 1
    recovered.close()


def test_exports_never_replace_input_or_existing_output(suite, tmp_path):
    with pytest.raises(ValueError, match='original'):
        suite.dispatch('export_swf', {'path': suite.origin})
    target = tmp_path / 'output.swf'
    result = suite.dispatch('export_swf', {'path': str(target)})
    assert result['path'] == str(target)
    with pytest.raises(FileExistsError):
        suite.dispatch('export_swf', {'path': str(target)})
    with pytest.raises(FileExistsError):
        publish(target, b'bad')
    assert target.read_bytes() == suite.project.working


def test_worker_correlation_and_failure_recovery(tmp_path):
    import os
    env = {**os.environ, 'FLASH_SUITE_STATE_DIR': str(tmp_path / 'worker-state'), 'PYTHONIOENCODING': 'utf-8'}
    requests = [{'id': 11, 'op': 'ping', 'args': {}}, {'id': 12, 'op': 'invalid', 'args': {}}, {'id': 13, 'op': 'engines', 'args': {}}]
    result = subprocess.run([sys.executable, '-m', 'py_swf.universal_worker', 'worker'], input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True, text=True, encoding='utf-8', env=env, timeout=20)
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r['id'] for r in replies] == [11, 12, 13]
    assert replies[0]['ok'] and not replies[1]['ok'] and replies[2]['ok']
    assert next(e for e in replies[2]['result']['engines'] if e['id'] == 'python')['available']


def test_no_macro_or_command_auto_execution_in_hxml(suite, tmp_path):
    entry = tmp_path / 'evil.hxml'
    entry.write_text('--cmd echo unexpected\n-swf x.swf\n')
    # Check profile input before launching a configured compiler.
    from unittest.mock import patch
    with patch.object(suite.registry, 'executable', return_value=sys.executable):
        with pytest.raises(ValueError, match='comandos'):
            suite.compile_project({'entry': str(entry), 'profile': 'haxe', 'output': str(tmp_path / 'result.swf')})


def test_comparison_keeps_unavailable_engines(suite):
    results = suite.compare({'unit_id': answer_unit(suite).document_id})
    assert [r['engine'] for r in results['results']] == ['python', 'rust', 'jpexs']
    assert results['results'][0]['source']
    assert results['parity'] is False and results['superiority'] is False


def test_export_native_xfl(suite, tmp_path):
    out_dir = tmp_path / 'exported_xfl'
    res = suite.export({'format': 'xfl', 'path': str(out_dir)})
    assert res['engine'] == 'python'
    assert Path(res['path']).is_dir()
    assert (out_dir / 'DOMDocument.xml').exists()

