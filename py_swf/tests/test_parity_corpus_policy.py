import hashlib
import json
from pathlib import Path
import sys

import pytest

from py_swf.corpus import read_immutable_manifest
from py_swf.benchmarks import build_manifest, collect_paths, summarize_family_quality


def sealed_fixture(root, rows):
    data=json.dumps({'immutable':True,'cases':rows,'families':[]}).encode()
    (root/'manifest.json').write_bytes(data)
    (root/'manifest.sha256').write_text(hashlib.sha256(data).hexdigest())


def test_sealed_manifest_is_authoritative_and_source_family_never_reassigned(tmp_path):
    active=tmp_path/'swf/family/debug.fws.swf';active.parent.mkdir(parents=True);active.write_bytes(b'identity fixture')
    retired=tmp_path/'retired/old.swf';retired.parent.mkdir();retired.write_bytes(b'retired source')
    row={'relative':'swf/family/debug.fws.swf','family_id':'family','split':'holdout','sha256':hashlib.sha256(active.read_bytes()).hexdigest()}
    sealed_fixture(tmp_path,[row])
    assert collect_paths(tmp_path,'full')==[active]
    assert collect_paths(tmp_path,'full',family_ids=['family'])==[active]
    assert collect_paths(tmp_path,'full',case_relatives=[row['relative']])==[active]
    with pytest.raises(ValueError,match='excluded corpus case'):collect_paths(tmp_path,'full',case_relatives=['retired/old.swf'])
    with pytest.raises(ValueError,match='Unknown corpus family'):collect_paths(tmp_path,'full',family_ids=['absent'])
    built=build_manifest([active],tmp_path,1729)['cases'][0]
    assert built['family_id']=='family' and built['split']=='holdout'
    active.write_bytes(b'changed')
    with pytest.raises(ValueError,match='corpus file changed'):read_immutable_manifest(tmp_path)


def test_family_split_leak_and_duplicate_rows_are_rejected(tmp_path):
    (tmp_path/'a.swf').write_bytes(b'a');(tmp_path/'b.swf').write_bytes(b'b')
    rows=[{'relative':name,'sha256':hashlib.sha256(value).hexdigest(),'family_id':'same','split':split}
          for name,value,split in [('a.swf',b'a','development'),('b.swf',b'b','holdout')]]
    sealed_fixture(tmp_path,rows)
    with pytest.raises(ValueError,match='leaks across'):read_immutable_manifest(tmp_path)
    sealed_fixture(tmp_path,[rows[0],rows[0]])
    with pytest.raises(ValueError,match='Duplicate'):read_immutable_manifest(tmp_path)


def test_coverage_weights_families_equally_and_keeps_missing_null():
    def case(family,available,denominator):
        return {'family_id':family,'relative':family,'engines':[{'engine':'native','quality':{'origin_scope_metrics':{'application':{'method':{'canonical_denominator':denominator,'source_available':available}}}}}]}
    summary=summarize_family_quality([case('large',100,100),case('small',0,1),case('small',0,1),case('missing',None,2)])[0]
    assert summary['mean_source_coverage']==.5
    assert summary['measured_families']==2 and summary['scheduled_families']==3
    assert summary['families']['missing'] is None




