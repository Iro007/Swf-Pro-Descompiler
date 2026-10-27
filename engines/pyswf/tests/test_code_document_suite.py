"""Whole-document compilation retains the caller's live revision and guards."""
import hashlib
import pytest

from py_swf.compiler.class_compiler import compile_class_program
from py_swf.universal import UniversalSuite
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf


def swf_for(abc):
    return make_synthetic_swf([SWFTag(82, b'\0\0\0\0parity\0' + abc.serialize())])


@pytest.fixture
def document_suite(tmp_path):
    original = tmp_path / 'original.swf'
    original.write_bytes(swf_for(compile_class_program('package { public class A { public function f():int { return 2; } } }').abc))
    suite = UniversalSuite(tmp_path / 'state')
    suite.open(original)
    yield suite, original, tmp_path
    suite.close()


def test_read_documents_does_not_create_saved_sources(document_suite):
    suite, _, _ = document_suite
    before = suite.state()['revision']
    unit = next(u for u in suite.units() if u.abc is not None)
    page = suite.dispatch('code_documents', {'tag_path': list(unit.tag_path)})
    assert {item['scope'] for item in page['items']} == {'class', 'script'}
    assert all('source' not in item for item in page['items'])
    assert suite.require_project().source_documents == {}
    assert suite.state()['revision'] == before


def test_compile_shared_document_exports_without_commit(document_suite):
    suite, original, temporary = document_suite
    unit = next(u for u in suite.units() if u.abc is not None)
    identity = 'tag:' + '/'.join(map(str, unit.tag_path)) + '/class:0'
    document = suite.dispatch('code_documents', {'document_id': identity})
    assert document['completeness'] == 'complete' and document['compiler_eligibility']['eligible']
    before = suite.state()['revision']
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    output = temporary / 'candidate.swf'
    result = suite.dispatch('compile_code_document', {'document_id': identity,
        'base_revision': before, 'base_source_sha256': document['original_source_sha256'],
        'tag_sha256': document['tag_sha256'], 'source': document['source'].replace('return 2', 'return 4'),
        'source_engine': 'rust', 'output': str(output)})
    assert output.is_file() and result['compiler_engine'] == 'native' and result['compiler_delegated']
    assert result['candidate_sha256'] != before and result['committed'] is False
    assert suite.state()['revision'] == before
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest


def test_stale_locator_and_missing_target_rejected_before_history(document_suite):
    suite, _, temporary = document_suite
    unit = next(u for u in suite.units() if u.abc is not None)
    identity = 'tag:' + '/'.join(map(str, unit.tag_path)) + '/class:0'
    document = suite.dispatch('code_documents', {'document_id': identity})
    args = {'document_id': identity, 'source': document['source'], 'base_revision': suite.state()['revision'],
            'base_source_sha256': document['original_source_sha256'], 'tag_sha256': document['tag_sha256']}
    before = suite.state()
    with pytest.raises(ValueError, match='salida'):
        suite.dispatch('compile_code_document', args)
    with pytest.raises(ValueError, match='cambiaron'):
        suite.dispatch('compile_code_document', {**args, 'base_source_sha256': '0' * 64, 'output': str(temporary / 'stale.swf')})
    assert suite.state() == before
    assert not (temporary / 'stale.swf').exists()


def test_partial_read_document_cannot_be_recompiled(tmp_path):
    from py_swf.avm2 import assemble_instructions
    program = compile_class_program('package { public class A { public function f():int { return 2; } } }')
    abc = program.abc
    from tests.test_parity_class_documents import method_for
    body = method_for(abc, 0, 'f')
    # An escaped XML literal interpolation has no source form the decompiler can prove, so the class stays partial.
    body.code = assemble_instructions(abc.constant_pool, 'pushbyte 2\nesc_xelem\nreturnvalue')
    original = tmp_path / 'partial.swf'
    original.write_bytes(swf_for(abc))
    suite = UniversalSuite(tmp_path / 'state')
    try:
        suite.open(original)
        unit = next(u for u in suite.units() if u.abc is not None)
        identity = 'tag:' + '/'.join(map(str, unit.tag_path)) + '/class:0'
        document = suite.code_documents({'document_id': identity})
        assert document['completeness'] == 'partial' and not document['compiler_eligibility']['eligible']
    finally:
        suite.close()


def test_rust_script_keeps_all_owned_class_sources(document_suite, monkeypatch):
    from py_swf.avm2 import TraitsInfo
    from types import SimpleNamespace
    suite, _, _ = document_suite
    unit = next(u for u in suite.units() if u.abc is not None)
    # Model one script containing two independently reconstructed classes.
    second = TraitsInfo(); second.kind_flags = 4; second.class_idx = 1
    unit.abc.scripts[0].traits.append(second)
    monkeypatch.setattr(suite, 'units', lambda: [unit])
    monkeypatch.setattr(suite, '_rust_unit', lambda u: ('same-unit', 0))
    suite.rust_worker = SimpleNamespace(close=lambda: None, call=lambda *args: {
        'state': 'structured', 'documents': [
            {'class_index': 0, 'script_index': 0, 'source': 'class A {}'},
            {'class_index': 1, 'script_index': 0, 'source': 'class B {}'}],
        'initialization_order': [1, 0]})
    # Native reconstruction cannot invent a nonexistent class. Mock only its
    # declaration metadata; the suite must still require both Rust owners.
    from py_swf.decompile.backends import NativeBackend
    monkeypatch.setattr(NativeBackend, 'decompile', lambda *args: SimpleNamespace(as_dict=lambda: {
        'sections': [{'scope': 'script', 'script_index': 0, 'source': 'native'}]}))
    monkeypatch.setattr('py_swf.source_document.native_source_documents', lambda *args, **kwargs: [{
        'scope': 'script', 'document_id': 'tag:0/script:0',
        'symbol_context': {'script_index': 0}, 'diagnostics': [],
        'compiler_eligibility': {'eligible': True, 'reason': None}}])
    record = suite.code_documents({'tag_path': list(unit.tag_path), 'engine': 'rust', 'include_source': True})['items'][0]
    assert record['source'] == 'class B {}\n\nclass A {}'
    assert record['completeness'] == 'complete'
    suite.cache.clear()
    suite.rust_worker.call = lambda *args: {'state': 'structured', 'documents': [
        {'class_index': 0, 'script_index': 0, 'source': 'class A {}'}], 'initialization_order': [0]}
    record = suite.code_documents({'tag_path': list(unit.tag_path), 'engine': 'rust', 'include_source': True})['items'][0]
    assert record['source'] is None and record['completeness'] == 'partial'


def test_rpc_document_commit_requires_persistent_state(capsys):
    from py_swf.suite_cli import main
    assert main(['rpc', '--rpc-operation', 'compile_code_document', '--args-json', '{"commit":true}']) == 1
    assert 'conservar la sesión' in capsys.readouterr().err
