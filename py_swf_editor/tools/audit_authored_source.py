"""Untimed native correctness observation for one release FWS per family."""
from collections import Counter
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from py_swf.benchmark_worker import decompile
from py_swf.canonical_inventory import build_inventory
from py_swf.corpus import classify_application_units


def main():
    root=PROJECT.parent/'benchmark_inputs/parity-20261014'
    state=json.loads((root/'build-state.json').read_text(encoding='utf-8'))
    rows=[]
    for family in state['families']:
        path=root/'swf'/family['family_id']/'release.fws.swf'
        if not path.is_file():continue
        # The Flixel libraries are observed separately after the root freeze;
        # this small directed smoke does not claim full-cohort coverage.
        if family['compiler']=='haxeflixel' or family['split']=='holdout':continue
        data=path.read_bytes();inventory=classify_application_units(build_inventory(data),family.get('application_classes',[]))
        origins={u['document_id']:u.get('code_origin') for u in inventory['units']}
        result=decompile(data)
        app=[u for u in result['units'] if origins.get(u['document_id'])=='application']
        rows.append({'family_id':family['family_id'],'split':family['split'],'sha256':inventory['input_sha256'],
                     'scope_denominators':inventory['scope_denominators'],
                     'application_scopes':{scope:dict(Counter(u.get('generation') for u in app if u['scope']==scope)) for scope in inventory['scope_denominators']},
                     'diagnostics':dict(Counter(u.get('diagnostic') for u in app if u.get('diagnostic'))),
                     'canonical_inventory_sha256':inventory['sha256'],'inventory_status':result['inventory_status']})
        print(json.dumps(rows[-1]),flush=True)
    report={'cases':rows,'timing':'not_measured','behavior':'not_run','parity':False,
            'scope':'one release FWS per available development/validation non-Flixel authored family; holdout excluded until freeze'}
    out=PROJECT.parent/'reports/parity-implementation-20261014/authored-native-correctness.json'
    out.write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':main()
