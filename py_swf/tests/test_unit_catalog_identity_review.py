"""Independent regression checks for identity trust and duplicated documents."""
import copy
import hashlib
import json
import sqlite3

import pytest

from py_swf.benchmark_catalog import import_local_report,catalog_summary
from py_swf.benchmark_unit_catalog import catalog_units
from tests.test_benchmark_unit_catalog import make_report


def save(path,report):path.write_text(json.dumps(report),encoding='utf-8')


def test_identical_class_document_is_one_observation_with_all_details(tmp_path):
    path,report=make_report(tmp_path);case=report['cases'][0]
    unit=case['canonical_inventory']['units'][0]
    unit.update(document_id='tag:0/class:0',scope='class',unit_id=case['sha256']+':tag:0/class:0')
    observed={**unit,'identity_verified':True,'generation':'available','source_sha256':'b'*64}
    document={**observed,'source':'package { public class Demo {} }','compiler_eligibility':{'eligible':False,'reason':'readonly sample'}}
    case['engines'][0].update(units=[observed],documents=[document])
    save(path,report);db=tmp_path/'catalog.sqlite';import_local_report(db,path)
    page=catalog_units(db,include_metadata=True)
    assert page['total']==1
    metadata=page['items'][0]['metadata']
    assert metadata['source']==document['source']
    assert metadata['document_observations']==[document]
    assert metadata['document_observations'][0]['compiler_eligibility']['reason']=='readonly sample'


def test_distinct_document_outputs_remain_separate(tmp_path):
    path,report=make_report(tmp_path);engine=report['cases'][0]['engines'][0]
    document={**engine['units'][0],'source_sha256':'d'*64,'source':'different source'}
    engine['documents']=[document];save(path,report);db=tmp_path/'catalog.sqlite'
    import_local_report(db,path);assert catalog_units(db)['total']==2


@pytest.mark.parametrize('field,value',[('context',{'parameters':['wrong'],'flags':256}),
    ('abc_sha256','f'*64),('bytecode_offset',999),('body_index',7),('language','as2'),
    ('parent_unit_id','a'*64+':tag:0/class:5')])
def test_conflicting_byte_identity_metadata_rolls_back_campaign(tmp_path,field,value):
    path,report=make_report(tmp_path);db=tmp_path/'catalog.sqlite';import_local_report(db,path)
    before=catalog_summary(db)['totals'];report=copy.deepcopy(report)
    report['cases'][0]['canonical_inventory']['units'][0][field]=value
    save(path,report)
    with pytest.raises(ValueError,match='identity changed'):import_local_report(db,path)
    assert catalog_summary(db)['totals']==before and catalog_units(db)['total']==1


def test_derived_origin_labels_do_not_redefine_physical_identity(tmp_path):
    path,report=make_report(tmp_path);db=tmp_path/'catalog.sqlite';import_local_report(db,path)
    report['cases'][0]['canonical_inventory']['units'][0].update(code_origin='dependency',family_id='another',split='validation',identity_status='independently_verified')
    save(path,report);assert import_local_report(db,path)['imported']
    with sqlite3.connect(db) as connection:assert connection.execute('SELECT COUNT(*) FROM canonical_units').fetchone()[0]==1


@pytest.mark.parametrize('size',[None,True,-1])
def test_missing_hash_or_invalid_size_never_verifies(tmp_path,size):
    path,report=make_report(tmp_path);case=report['cases'][0]
    case['canonical_inventory']['units'][0].update(bytecode_sha256=None,bytecode_bytes=size)
    case['engines'][0]['units'][0].update(bytecode_sha256=None,bytecode_bytes=size,identity_verified=True)
    save(path,report);db=tmp_path/'catalog.sqlite';import_local_report(db,path)
    assert catalog_units(db)['items'][0]['identity_verified'] is False


def test_global_unit_id_alias_cannot_disagree_with_local_document_id(tmp_path):
    path,report=make_report(tmp_path)
    report['cases'][0]['canonical_inventory']['units'][0]['unit_id']='a'*64+':tag:99/method:99'
    save(path,report)
    with pytest.raises(ValueError,match='outside the input artifact'):import_local_report(tmp_path/'catalog.sqlite',path)


def test_artifact_metadata_does_not_duplicate_full_inventory(tmp_path):
    path,report=make_report(tmp_path);db=tmp_path/'catalog.sqlite';import_local_report(db,path)
    with sqlite3.connect(db) as connection:
        artifact=json.loads(connection.execute('SELECT metadata_json FROM artifacts').fetchone()[0])
        assert 'canonical_inventory' not in artifact
        retained=json.loads(connection.execute("SELECT metadata_json FROM evidence WHERE kind='canonical_inventory'").fetchone()[0])
        assert retained['inventory']==report['cases'][0]['canonical_inventory']
