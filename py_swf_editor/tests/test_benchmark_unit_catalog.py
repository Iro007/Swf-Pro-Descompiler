import hashlib
import json
import sqlite3

import pytest

from py_swf.benchmark_catalog import import_local_report, catalog_summary
from py_swf.benchmark_unit_catalog import catalog_units, catalog_evidence
from py_swf.universal import benchmark_catalog_details


def make_report(tmp_path, *, canonical=True):
    input_hash, code_hash = 'a' * 64, hashlib.sha256(b'\x47').hexdigest()
    unit = {'unit_id': input_hash + ':tag:0/method:0', 'document_id': 'tag:0/method:0',
            'input_sha256': input_hash, 'scope': 'method', 'language': 'as3',
            'bytecode_sha256': code_hash, 'bytecode_bytes': 1, 'context': {'owner': 'script:0'}}
    report = {'schema_version': 1, 'created_at': '2026-10-14T00:00:00Z', 'profile': 'smoke',
              'operation': 'source', 'provenance': {'implementation_unchanged': True},
              'cases': [{'sha256': input_hash, 'relative': 'known.swf', 'bytes': 20,
                         'engines': [{'engine': 'native', 'status': 'measured',
                                      'units': [{**unit, 'identity_verified': True, 'generation': 'available',
                                                 'source_sha256': 'b' * 64}],
                                      'hot_samples': [{'ok': True, 'wall_ms': 2}]}]}]}
    if canonical:
        report['cases'][0]['canonical_inventory'] = {'units': [unit], 'sha256': 'c' * 64}
    path = tmp_path / 'benchmark.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    return path, report


def test_canonical_identity_and_observation_detail_are_durable(tmp_path):
    database = tmp_path / 'catalog.sqlite'
    path, report = make_report(tmp_path)
    imported = import_local_report(database, path)
    assert imported['imported']
    page = catalog_units(database, engine='native', scope='method', include_metadata=True)
    assert page['total'] == 1 and page['items'][0]['identity_verified'] is True
    assert page['items'][0]['canonical']['context'] == {'owner': 'script:0'}
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    assert catalog_units(database, offset=1)['items'] == []
    evidence = catalog_evidence(database, campaign=imported['campaign_id'], kind='canonical_inventory', include_metadata=True)
    assert evidence['items'][0]['metadata']['inventory'] == report['cases'][0]['canonical_inventory']
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest
    assert not import_local_report(database, path)['imported']
    assert catalog_units(database)['total'] == 1


def test_legacy_backfill_retains_old_ids_runs_and_unknown_identity(tmp_path):
    database = tmp_path / 'catalog.sqlite'
    path, _ = make_report(tmp_path, canonical=False)
    imported = import_local_report(database, path)
    before = catalog_summary(database)['totals']
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE catalog_meta SET value='1' WHERE key='schema_version'")
        connection.execute('DELETE FROM unit_observations')
    assert not import_local_report(database, path)['imported']
    assert catalog_summary(database)['totals'] == before
    row = catalog_units(database)['items'][0]
    assert row['campaign_id'] == imported['campaign_id'] and row['unit_id'] is None
    assert row['identity_verified'] is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()[0] == '2'


def test_conflicting_canonical_identity_rolls_back_new_campaign(tmp_path):
    database = tmp_path / 'catalog.sqlite'
    path, report = make_report(tmp_path)
    import_local_report(database, path)
    before = catalog_summary(database)['totals']
    report['cases'][0]['canonical_inventory']['units'][0]['bytecode_sha256'] = 'd' * 64
    path.write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        import_local_report(database, path)
    assert catalog_summary(database)['totals'] == before
    assert catalog_units(database)['total'] == 1


def test_missing_legacy_identity_is_counted_and_preserved(tmp_path):
    database = tmp_path / 'catalog.sqlite'
    path, report = make_report(tmp_path, canonical=False)
    report['cases'][0]['engines'][0]['units'] = [{'generation': 'failed', 'diagnostic': 'decoder stopped'}]
    path.write_text(json.dumps(report), encoding='utf-8')
    import_local_report(database, path)
    row = catalog_units(database, include_metadata=True)['items'][0]
    assert row['document_id'] == 'unidentified:0' and row['metadata']['identity_missing']
    assert row['status'] == 'failed' and row['identity_verified'] is None


def test_details_rpc_bounds_and_readonly_database(tmp_path):
    database = tmp_path / 'catalog.sqlite'
    path, _ = make_report(tmp_path)
    import_local_report(database, path)
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    result = benchmark_catalog_details({'database': str(database), 'limit': 1}, 'units')
    assert result['total'] == 1 and len(result['items']) == 1
    with pytest.raises(ValueError, match='pagination'):
        benchmark_catalog_details({'database': str(database), 'limit': 201}, 'units')
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest


def test_foreign_schema_is_rejected_without_mutation(tmp_path):
    database = tmp_path / 'foreign.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE catalog_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version','99')")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    path, _ = make_report(tmp_path)
    with pytest.raises(ValueError, match='Unsupported'):
        import_local_report(database, path)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest
