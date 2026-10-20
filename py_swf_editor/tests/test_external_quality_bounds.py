"""Failed tools and packaged reference discovery must retain honest states."""
from py_swf.benchmarks import quality


def test_failed_external_response_has_unknown_output_coverage():
    result = quality({'ok': False, 'units': [], 'native_returncode': 1})
    assert result['source_available'] is None
    assert result['disassembly_available'] is None
    assert result['pcode'] is None
    assert result['unit_mapping'] == 'not_verified'
    assert result['expected_units_reported'] is None


def test_total_startup_failure_does_not_fabricate_empty_canonical_inventory():
    import sys
    from py_swf.benchmarks import run_engine
    result = run_engine('native', b'unread input', {'cold_runs': 1, 'hot_runs': 1, 'warmups': 0},
                        timeout=.1, command_factory=lambda _: [sys.executable, '-u', '-c', 'import time; time.sleep(1)'])
    assert result['status'] == 'failed' and result['failures']
    assert result['hot']['median_ms'] is None and result['cold']['median_ms'] is None
    assert result['quality']['expected_units_reported'] is None
    assert result['quality']['source_available'] is None
    assert result['quality']['unit_mapping'] == 'not_verified'


def test_verified_empty_inventory_remains_a_measured_zero():
    result = quality({'ok': True, 'units': [], 'eligible_units': 0})
    assert result['source_available'] == 0
    assert result['unit_mapping'] == 'canonical'


def test_frozen_reference_lookup_uses_existing_workspace(monkeypatch):
    from pathlib import Path
    import py_swf.benchmarks as benchmarks
    import py_swf.suite_tools as suite_tools
    project = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(benchmarks.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(benchmarks, 'ROOT', project / 'missing-extraction-root')
    monkeypatch.setattr(suite_tools, 'PROJECT', project)
    command = benchmarks.reference_command()
    assert command[-1] == 'DevelopmentReferenceBridge'
    assert '-Djava.awt.headless=true' in command
    assert str(project / 'missing-extraction-root') not in '\n'.join(command)


def test_worker_preserves_workspace_path_and_drops_unrelated_credentials(tmp_path, monkeypatch):
    import json
    import sys
    from py_swf.benchmark_process import Worker
    child = tmp_path / 'environment_worker.py'
    child.write_text('''import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"ok": True, "request_id": request["request_id"],
        "workspace": os.environ.get("FLASH_SUITE_WORKSPACE"),
        "credential_inherited": "FLASH_TEST_PRIVATE_TOKEN" in os.environ}), flush=True)
''', encoding='utf-8')
    monkeypatch.setenv('FLASH_SUITE_WORKSPACE', str(tmp_path))
    monkeypatch.setenv('FLASH_TEST_PRIVATE_TOKEN', 'synthetic-test-value')
    with Worker([sys.executable, str(child)], cwd=tmp_path, timeout=30) as worker:
        result = worker.request({'operation': 'probe'})
    assert result['workspace'] == str(tmp_path)
    assert result['credential_inherited'] is False
