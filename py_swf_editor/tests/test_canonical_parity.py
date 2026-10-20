import hashlib
import json
from pathlib import Path
import sys

import pytest

from py_swf.canonical_inventory import build_inventory, attach_observations, cross_validate
from py_swf.benchmarks import match_units, quality
from py_swf.benchmark_external_worker import FLASHKIT_API_ALL_ABC, perform
from py_swf.script_inventory import enumerate_scripts
from py_swf.swf_parser import SWFTag
from py_swf.avm1 import assemble_avm1
from tests.conftest import make_synthetic_swf
from tests.native_fixtures import as3_tag


def test_independent_abc_layout_has_exact_spans_and_separate_script_scope(monkeypatch):
    data=make_synthetic_swf([as3_tag()])
    inventory=build_inventory(data)
    assert inventory['diagnostics']==[]
    assert inventory['scope_denominators']=={'script':1,'method':2}
    original=list(enumerate_scripts(data))
    for unit in inventory['units']:
        if unit['scope']=='method':
            peer=next(u for u in original if u.document_id==unit['document_id'])
            start=unit['bytecode_offset']
            assert data[start:start+unit['bytecode_bytes']]==peer.code
            assert unit['bytecode_sha256']==hashlib.sha256(peer.code).hexdigest()
    # Independence from the normal ABC parser is a requirement, not a mock result.
    monkeypatch.setattr('py_swf.avm2.ABCFile.parse',lambda *a,**k:pytest.fail('native ABC parser used'))
    assert build_inventory(data)['sha256']==inventory['sha256']


def test_all_six_pairs_do_not_require_native_as_the_anchor():
    unit={'document_id':'tag:1/method:2','unit_id':'input:tag:1/method:2','scope':'method','bytecode_sha256':'abc','generation':'available'}
    results=[{'engine':engine,'units':[unit]} for engine in ('native','rust','jpexs','pyflashkit')]
    comparison=match_units(results,{'scope_denominators':{'method':1,'class':2}})
    assert comparison['pair_count']==6
    assert all(p['matched']==1 and p['scope_metrics']['class']['matched']==0 for p in comparison['pairs'])
    assert match_units(results[1:])['pair_count']==3


def test_duplicate_and_unknown_identity_never_enters_verified_denominator():
    unit={'document_id':'x','scope':'method','bytecode_sha256':'abc','generation':'available'}
    duplicate=match_units([{'engine':'jpexs','units':[unit,unit]},{'engine':'pyflashkit','units':[unit]}])
    assert duplicate['status']=='ambiguous_duplicate_identities' and duplicate['matched']==0
    unknown=match_units([{'engine':'rust','units':[{**unit,'identity_verified':False}]},{'engine':'jpexs','units':[unit]}])
    assert unknown['status']=='identity_unverified' and unknown['matched']==0


def test_inventory_disagreement_blocks_quality_despite_generated_text():
    data=make_synthetic_swf([as3_tag()]);inventory=build_inventory(data)
    expected=next(u for u in inventory['units'] if u['scope']=='method')
    rows=attach_observations(inventory,[{**expected,'abc_sha256':'wrong','generation':'available'}])
    assert rows[0]['identity_verified'] is False and 'unit_id' not in rows[0]
    assert quality({'units':rows,'inventory_status':'disagreement'})['source_available'] is None
    comparison=match_units([{'engine':'rust','units':rows,'inventory_status':'disagreement'},{'engine':'jpexs','units':[expected]}])
    assert comparison['status']=='inventory_disagreement' and comparison['matched']==0


def test_invalid_container_is_a_counted_diagnostic():
    inventory=build_inventory(b'FWS\x08\x10\x00\x00\x00')
    assert inventory['diagnostics'] and inventory['scope_denominators']['diagnostic']==1


def test_api_mode_does_not_use_first_abc_workspace_resolution():
    compile(FLASHKIT_API_ALL_ABC,'all-abc-api','exec')
    assert 'enumerate(ws.abc_blocks)' in FLASHKIT_API_ALL_ABC
    assert 'decompile_class(abc, class_index=idx)' in FLASHKIT_API_ALL_ABC
    assert 'decompile_method(abc, method_idx=body.method, include_signature=False)' in FLASHKIT_API_ALL_ABC
    assert 'decompile_class(ws' not in FLASHKIT_API_ALL_ABC


def test_no_json_metadata_does_not_claim_coverage(monkeypatch):
    import py_swf.benchmark_external_worker as adapter
    def invoke(command,cwd):
        out=Path(command[-2]);out.mkdir();(out/'Class.as').write_text('package {class Class {}}')
        return b'', '', 1
    monkeypatch.setattr(adapter,'invoke',invoke)
    with pytest.raises(ValueError,match='no observation JSON'):
        perform('pyflashkit',{'executable':'not-run'},make_synthetic_swf([as3_tag()]),'source')


def test_real_api_multi_abc_same_class_name_keeps_distinct_paths_and_failed_rows():
    from py_swf.compiler.class_compiler import compile_class_program
    from py_swf.benchmark_engines import checked_tool
    try:config=checked_tool('pyflashkit')
    except (ValueError,OSError):pytest.skip('Pinned isolated pyflashkit unavailable')
    tags=[]
    for value in (1,2):
        abc=compile_class_program('package demo { public class Same { public function Same(){super();} public function run():int{return '+str(value)+';} } }').abc_bytes()
        tags.append(SWFTag(82,b'\0\0\0\0abc\0'+abc))
    data=make_synthetic_swf(tags)
    result=perform('pyflashkit',config,data,'source')
    assert result['status']=='measured'
    classes=[u for u in result['units'] if u['scope']=='class']
    assert len(classes)==2 and len({u['unit_id'] for u in classes})==2
    class_files=[u.get('output_file') for u in classes if u['generation']=='available']
    assert len(set(class_files))==len(class_files)
    assert all(u['identity_verified'] for u in result['units'])


def test_partial_api_failure_preserves_successful_artifacts(monkeypatch):
    import py_swf.benchmark_external_worker as adapter
    data=make_synthetic_swf([as3_tag()]);inventory=build_inventory(data)
    methods=[u for u in inventory['units'] if u['scope']=='method']
    def invoke(command,cwd):
        out=Path(command[-2]);out.mkdir()
        (out/'method.as').write_text('return 1;')
        rows=[{**methods[0],'generation':'available','source_sha256':'a'*64},
              {**methods[1],'generation':'failed','diagnostic':'independent API failure'}]
        (out/'observations.json').write_text(json.dumps({'rows':rows}))
        return b'', '', 1
    monkeypatch.setattr(adapter,'invoke',invoke)
    result=perform('pyflashkit',{'executable':'not-run'},data,'source')
    assert result['artifacts'] and result['units'][0]['generation']=='available'
    assert result['units'][1]['generation']=='failed'
    assert result['units'][1]['diagnostic']=='independent API failure'


def test_family_spec_split_and_compression_layout_are_stable():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
    from build_parity_corpus import project_specs,compression_variant
    specs=project_specs()
    assert len({s['family_id'] for s in specs})==12
    assert [sum(s['split']==split for s in specs) for split in ('development','validation','holdout')]==[6,3,3]
    data=make_synthetic_swf([as3_tag()])
    locations=[]
    for signature in ('FWS','CWS','ZWS'):
        inventory=build_inventory(compression_variant(data,signature))
        assert inventory['diagnostics']==[]
        locations.append([(u['document_id'],u['bytecode_offset'],u['bytecode_sha256']) for u in inventory['units']])
    assert locations[0]==locations[1]==locations[2]


def test_syntax_pool_copy_is_once_per_abc_and_appended_entries_are_rolled_back(monkeypatch):
    import py_swf.benchmark_worker as worker
    import py_swf.class_documents as documents
    monkeypatch.setattr(documents,'reconstruct_abc_documents',lambda *a,**k:[])
    copies=[];original=worker.deepcopy
    def copy(value):
        if type(value).__name__=='ConstantPool':copies.append(value)
        return original(value)
    monkeypatch.setattr(worker,'deepcopy',copy)
    seen=[]
    def parse(self):
        pool=self.context.pool
        assert '__syntax_poison__' not in pool.strings
        seen.append(len(pool.strings));pool.strings.append('__syntax_poison__')
        return []
    # The real parser's public binding attribute is explicit in this test.
    original_init=worker.Parser.__init__
    def init(self,*args,**kwargs):
        original_init(self,*args,**kwargs)
        self.context=args[2]
    monkeypatch.setattr(worker.Parser,'__init__',init)
    monkeypatch.setattr(worker.Parser,'parse_program',parse)
    result=worker.decompile(make_synthetic_swf([as3_tag()]))
    assert len(copies)==1 and len(seen)==2 and len(set(seen))==1
    assert all(u['identity_verified'] for u in result['units'])


def test_script_declarations_do_not_inflate_method_source_coverage():
    from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
    from py_swf.benchmark_worker import decompile
    compiled=compile_class_program('package { public class Example { public function Example(){super();} public function work():int{return 3;} } }')
    result=decompile(class_program_swf(compiled),include_source=True)
    represented=[u for u in result['units'] if u.get('represented_by_document')]
    assert represented and all(u['scope']=='method' and u['generation']=='represented_by_document' for u in represented)
    assert all(u['source_kind']=='pcode' and u['pcode_available'] and not u['compiler_eligible'] for u in represented)
    assert all(u['represented_by_document'].endswith('/script:0') for u in represented)
    classes=[u for u in result['units'] if u.get('scope')=='class']
    assert classes and classes[0]['generation']=='available'
    expected=sum(u.get('scope')=='method' and u['generation']=='available' for u in result['units'])
    assert quality(result)['source_available']==expected


def test_legacy_declaration_payload_cannot_enter_method_quality_or_pair_counts():
    declaration={'document_id':'tag:0/method:0','scope':'method','generation':'implicit','source_kind':'script_declarations',
                 'represented_by_document':'tag:0/script:0','bytecode_sha256':'same'}
    body={**declaration,'generation':'available','source_kind':'method_body','represented_by_document':None}
    assert quality({'units':[declaration]})['source_available']==0
    pair=match_units([{'engine':'native','units':[body]},{'engine':'jpexs','units':[declaration]}])
    assert pair['matched']==1
    assert pair['scope_metrics']['method']['left_source_available']==1
    assert pair['scope_metrics']['method']['right_source_available']==0
