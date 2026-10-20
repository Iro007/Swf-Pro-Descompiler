import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import collect_parity_correctness as collector
from py_swf.avm1 import assemble_avm1
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf


def test_release_family_engine_matrix_retains_full_reports_and_stops_on_source_change(tmp_path,monkeypatch):
    corpus=tmp_path/'corpus';corpus.mkdir();(corpus/'manifest.sha256').write_text('manifest')
    data=make_synthetic_swf([SWFTag(12,assemble_avm1('push 1\ntrace\nend'))]);cases=[]
    for name in ('alpha','beta'):
        path=corpus/(name+'.swf');path.write_bytes(data)
        cases.append({'relative':path.name,'sha256':hashlib.sha256(data).hexdigest(),'family_id':name,'split':'development','debug':False,'compression':'FWS'})
    monkeypatch.setattr(collector,'read_immutable_manifest',lambda *a:{'cases':cases,'families':[{'family_id':c['family_id']} for c in cases]})
    monkeypatch.setattr(collector,'verify_freeze',lambda *a:{'implementation_sha256':'frozen'})
    state={'digest':'frozen'};monkeypatch.setattr(collector,'implementation_digest',lambda:state['digest'])
    calls=[]
    def run(engine,data,profile,**kwargs):
        calls.append(engine);state['digest']='changed'
        return {'engine':engine,'status':'measured','units':[],'diagnostics':['preserved partial output']}
    monkeypatch.setattr(collector,'run_engine',run)
    report=collector.collect(corpus,tmp_path/'out',tmp_path/'freeze.json',engines=('native','jpexs'))
    assert report['scheduled_entries']==4 and len(report['entries'])==4
    assert calls==['native'] and report['completed_entries']==1 and report['aborted_entries']==3
    entry=report['entries'][0];raw=Path(entry['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==entry['sha256']
    decoded=json.loads(raw)
    assert decoded['cases'][0]['canonical_inventory']['units']
    assert decoded['cases'][0]['engines'][0]['diagnostics']==['preserved partial output']
