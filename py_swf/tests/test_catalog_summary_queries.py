"""Summary SQL stays bounded while every phase/raw-derived metric is retained."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import py_swf.benchmark_catalog as catalog
from tests.test_benchmark_catalog import report, save_report


def edge_report():
    value=report()
    value['label']='Catalog summary equivalence fixture'
    native=value['cases'][0]['engines'][0]
    native.update(family_id='owned-project',split='development',adapter_mode='exact',
        inventory_status='verified',canonical_inventory_sha256='c'*64,include_scope=['method'],
        quality_dimensions={'behavior':'not_run'},valid_performance_summary=False,
        timing_scope='exact fixture scope',memory_scope='sampled tree RSS')
    native['quality'].update(disassembly_available=None,
        scope_metrics={'method':{'canonical_denominator':2,'identity_verified':2,'source_available':1,'disassembly_available':None}},
        origin_scope_metrics={'application':{'method':{'canonical_denominator':1,'identity_verified':1,'source_available':1,'disassembly_available':None}}})
    native['cold_samples']=[{'ok':True,'exit_code':0,'wall_ms':5,'total_with_startup_ms':105,
        'peak_tree_rss_bytes':4096,'rss_samples':3,'rss_sample_interval_s':.02,'rusage_maxrss_kib':20}]
    native['hot_samples']=[
        {'ok':True,'exit_code':0,'wall_ms':2,'peak_tree_rss_bytes':1000,'memory_samples':2,'rss_sample_interval_s':.01,
         'adapter_status':{'stage':'generation','status':'complete'},'as_file_count':2,'as_bytes':30,'code_output_file_count':3},
        {'ok':True,'exit_code':0,'wall_ms':10,'wall_is_censored':'true','peak_tree_rss_bytes':2000,
         'rss_samples':0,'rusage_maxrss_kib':77,'as_error_marker_count':2},
        {'ok':False,'exit_code':1,'wall_ms':7,'rss_sample_interval_s':.02},
        {'ok':False,'timed_out':True,'exit_code':-9,'wall_ms':8},
        {'ok':False,'exit_code':0,'wall_ms':9,'adapter_status':['indeterminate',None]},
    ]
    native['warmup_samples']=[]
    value['cases'][0]['comparison']={'quality_equivalent':False,'scope_metrics':{'method':{'matched':2}}}
    value['cases'][0]['engines'].append({'engine':'flasm','operation':'disassemble','status':'output_unmapped',
        'artifact_unit_scope':'file','quality':{'expected_units_reported':2,'source_available':None,
        'disassembly_available':None,'unit_mapping':'not_verified'},'behavior':'passed'})
    value['cases'].append({'relative':'unidentified.swf','bytes':12,'engines':[{'engine':'native',
        'status':'indeterminate','quality':{},'hot_samples':[{'wall_ms':3,'adapter_status':'unknown'}]}]})
    return value


def normalized(summary):
    return {**summary,'database':'<catalog>'}


def test_summary_preserves_all_phase_metrics_and_empty_observations(tmp_path):
    database=tmp_path/'catalog.sqlite'
    catalog.import_local_report(database,save_report(tmp_path,edge_report()))
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    actual=normalized(catalog.catalog_summary(database))
    expected=json.loads((Path(__file__).parent/'fixtures/catalog_summary_snapshot.json').read_text(encoding='utf-8'))
    assert actual==expected
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
    assert normalized(catalog.catalog_summary(database,limit=0))=={**expected,'rows':[],'truncated':True}
    for filters in ({'engine':'native'},{'operation':'disassemble'},{'environment':'Windows'},
                    {'family_id':'owned-project'},{'scope':'method'},{'adapter_mode':'exact'}):
        filtered=catalog.catalog_summary(database,**filters)
        assert all(row in actual['rows'] for row in filtered['rows'])


def test_summary_uses_grouped_reads_not_one_query_per_validation_or_campaign(tmp_path,monkeypatch):
    database=tmp_path/'catalog.sqlite'
    for campaign in range(5):
        value=report();value['label']=f'Campaign {campaign}'
        value['created_at']=f'2026-10-13T0{campaign}:00:00Z'
        base=value['cases'][0]
        value['cases']=[{**deepcopy(base),'relative':f'case-{ordinal}.swf'} for ordinal in range(12)]
        catalog.import_local_report(database,save_report(tmp_path,value,name=f'campaign-{campaign}.json'))
    original=sqlite3.connect
    statements=[]
    def connect(filename,*args,**kwargs):
        assert '?mode=ro' in filename and kwargs.get('uri') is True
        connection=original(filename,*args,**kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(catalog.sqlite3,'connect',connect)
    summary=catalog.catalog_summary(database)
    assert len(summary['rows'])==180 and len(summary['campaigns'])==5
    assert any('query_only=ON' in sql for sql in statements)
    assert not any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP')) for sql in statements)
    assert len(statements)<=20, f'{len(statements)} statements for 60 validations; summary should batch reads'


def test_grouped_run_read_respects_small_sqlite_parameter_limits(tmp_path,monkeypatch):
    database=tmp_path/'catalog.sqlite'
    value=report();base=value['cases'][0]
    value['cases']=[{**deepcopy(base),'relative':f'case-{ordinal}.swf'} for ordinal in range(25)]
    catalog.import_local_report(database,save_report(tmp_path,value))
    expected=catalog.catalog_summary(database)
    original=sqlite3.connect
    statements=[]
    def connect(filename,*args,**kwargs):
        connection=original(filename,*args,**kwargs)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,16)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(catalog.sqlite3,'connect',connect)
    assert catalog.catalog_summary(database)==expected
    assert len([sql for sql in statements if 'FROM runs WHERE validation_id IN (' in sql])==2


def test_semantic_evidence_survives_import_and_summary_without_inventing_quality(tmp_path):
    value = report()
    evidence = {'schema_version': 1, 'evaluation_id': 'a' * 64,
        'source': {'project_scope': 'complete_generated_project'},
        'compilations': [{'compiler': 'flex', 'status': 'compiled',
            'runtime': {'status': 'reference_unsupported', 'records': []}}],
        'cache': {'key': 'a' * 64, 'hit': False}}
    value['cases'][0]['engines'][0]['quality_evaluation'] = evidence
    database = tmp_path / 'catalog.sqlite'
    path = save_report(tmp_path, value)
    first = catalog.import_local_report(database, path)
    summary = catalog.catalog_summary(database)
    rows = [row for row in summary['rows'] if 'quality_evaluation' in row]
    assert rows and all(row['quality_evaluation'] == evidence for row in rows)
    assert catalog.import_local_report(database, path)['campaign_id'] == first['campaign_id']
    assert catalog.import_local_report(database, path)['imported'] is False
