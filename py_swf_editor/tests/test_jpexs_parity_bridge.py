"""Real pinned JPEXS bridge identity, offset context, and document scopes."""
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import pytest

from py_swf.benchmark_process import Worker
from py_swf.benchmarks import reference_command, source_available_in_scope
from py_swf.canonical_inventory import build_inventory,attach_observations
from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
from py_swf.development_paths import normalize_development_config
from py_swf.swf_parser import SWFTag
from py_swf.avm1 import assemble_avm1
from tests.conftest import make_synthetic_swf
from tests.test_nested_avm1_inventory import function,pool,normalized

PROJECT=Path(__file__).resolve().parents[1]
WORKSPACE=PROJECT.parent


def bridge_command(kind):
    if kind=='development':return reference_command()
    config=normalize_development_config(json.loads((PROJECT/'.dev-references/toolchain.json').read_text()),PROJECT)
    folder=WORKSPACE/'toolpacks/jpexs-bridge';jar=Path(config['ffdec_jar'])
    return [config['java'],'-Xmx1024m','-Djava.awt.headless=true','-Duser.home={reference_home}',
            '-Dpyswf.playerglobal='+str(jar.parent/'flashlib/playerglobal32_0.swc'),'-cp',str(folder)+';'+str(jar)+';'+str(jar.parent/'lib/*'),'UniversalBridge']


@pytest.fixture(params=['development','application'])
def bridge(request):
    try:command=bridge_command(request.param)
    except (OSError,ValueError):pytest.skip('Pinned JPEXS reference unavailable')
    with Worker(command,cwd=PROJECT,timeout=90) as worker:yield worker


def observe(worker,data):
    inventory=build_inventory(data)
    result=worker.request({'operation':'decompile','swf_base64':base64.b64encode(data).decode(),'include_source':True})
    assert result['ok'],result
    observations=attach_observations(inventory,result['units'])
    assert all(u['identity_verified'] for u in observations),[(u.get('document_id'),u.get('diagnostic')) for u in observations if not u['identity_verified']]
    assert Counter(u['scope'] for u in observations)==Counter(inventory['scope_denominators'])
    return inventory,observations,result


def test_seventeen_real_avm1_function_spans_and_contexts(bridge):
    paths=[WORKSPACE/'benchmark_inputs/as2_definefunc_regs.swf']
    paths += [WORKSPACE/'benchmark_inputs/cloud-matched-micro-20261013'/('ruffle_avm1_'+name+'.swf') for name in ('closure_scope','timeline_function_def','try_catch_finally')]
    total=0
    for path in paths:
        if not path.is_file():pytest.skip('Optional immutable original fixtures unavailable')
        original=path.read_bytes();inventory,units,_=observe(bridge,original)
        canonical={u['document_id']:u for u in inventory['units']}
        for unit in units:
            if unit['scope']!='function':continue
            total+=1
            assert unit['avm1_context']==canonical[unit['document_id']]['context']
            assert unit['generation']=='available' and unit['source_mapping']=='graph_source_action_file_offset'
            assert unit['source_header_byte_offset']<unit['bytecode_offset']
            assert unit['source'].lstrip().startswith('function')
        assert path.read_bytes()==original
    assert total==17


@pytest.mark.parametrize('signature',['FWS','CWS','ZWS'])
def test_function2_register_preloads_unicode_pool_and_nested_sprite_offsets(bridge,signature):
    leaf=function('leaf',b'\x96\x02\0\x08\0\x3e\0',function2=True)
    outer=function('outer',pool('local á🙂')+leaf+b'\0')
    code=pool('captured')+outer+function('sibling',b'\0')+b'\0'
    sprite=SWFTag(39,b'\x01\0\x01\0'+SWFTag(12,code).pack()+SWFTag(0,b'').pack())
    data=make_synthetic_swf([sprite],signature=signature);inventory,units,_=observe(bridge,data)
    canonical={u['document_id']:u for u in inventory['units']}
    for unit in units:
        if unit['scope']!='function':continue
        assert unit['avm1_context']==canonical[unit['document_id']]['context']
        start=unit['bytecode_offset'];body=normalized(data)[start:start+unit['bytecode_bytes']]
        assert hashlib.sha256(body).hexdigest()==unit['bytecode_sha256']
    leaf=next(u for u in units if u.get('name')=='leaf')
    assert leaf['avm1_context']['register_count']==3
    assert leaf['avm1_context']['registers']==[2] and leaf['avm1_context']['flags']==0x100


def test_class_script_aggregates_are_exact_and_initializers_not_method_gain(bridge):
    compiled=compile_class_program('package example { public class Counter { private var total:int=2; public function Counter(){super();} public function add(n:int):int{return total+n;} } }')
    inventory,units,result=observe(bridge,class_program_swf(compiled))
    classes=[u for u in units if u['scope']=='class'];scripts=[u for u in units if u['scope']=='script']
    assert len(classes)==len(scripts)==1
    assert classes[0]['generation']=='available' and classes[0]['completeness']=='complete'
    represented=[u for u in units if u['scope']=='method' and u['generation']=='represented_by_document']
    assert represented and all(not source_available_in_scope(u) for u in represented)
    assert all(u.get('pcode') and u.get('pcode_available') for u in represented)
    assert result['documents'] and '/traits:' in result['documents'][0]['document_id']
    data=class_program_swf(compiled)
    candidate=bridge.request({'operation':'compile','swf_base64':base64.b64encode(data).decode(),
                              'document_id':classes[0]['document_id'],'source':classes[0]['source']})
    assert candidate['ok'] and candidate['swf_reopened'],candidate


def test_two_abc_classes_with_same_name_are_distinct_exact_units(bridge):
    tags=[]
    for value in (1,2):
        abc=compile_class_program('package duplicate { public class Same { public function Same(){super();} public function run():int{return '+str(value)+';} } }').abc_bytes()
        tags.append(SWFTag(82,b'\0\0\0\0same\0'+abc))
    _,units,_=observe(bridge,make_synthetic_swf(tags))
    classes=[u for u in units if u['scope']=='class']
    assert len(classes)==2 and len({u['document_id'] for u in classes})==2
    assert len({u['abc_sha256'] for u in classes})==2


def test_duplicate_function_names_never_match_by_name_or_hide_omitted_spans(bridge):
    code=function('same',assemble_avm1('push 1\nreturn\nend'))+function('same',assemble_avm1('push 2\nreturn\nend'))+b'\0'
    _,units,_=observe(bridge,make_synthetic_swf([SWFTag(12,code)]))
    functions=[u for u in units if u['scope']=='function']
    assert len(functions)==2 and len({u['document_id'] for u in functions})==2
    for value,unit in zip((1,2),functions):
        assert unit['read_only'] and not unit['compiler_eligible']
        if unit['generation']=='available':
            assert 'return '+str(value)+';' in unit['source']
            assert unit['source_mapping']=='graph_source_action_file_offset'
        else:
            assert unit['generation']=='failed' and unit['source_mapping']=='unverified' and unit['diagnostic']


def test_bridge_build_pins_all_shared_helper_classes_and_moved_paths():
    config=json.loads((PROJECT/'.dev-references/toolchain.json').read_text())
    assert str(PROJECT) in config['java'] and str(PROJECT) in config['reference_classpath']
    assert any('ParityBridgeInventory$OffsetWriter.class' in name for name in config['artifact_sha256'])
    for name,digest in config['artifact_sha256'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
    suite=json.loads((WORKSPACE/'toolpacks/lock.json').read_text())['tools']['jpexs-bridge']
    assert 'ParityBridgeInventory$OffsetWriter.class' in suite['classes']
    for name,digest in suite['classes'].items():assert hashlib.sha256((WORKSPACE/'toolpacks/jpexs-bridge'/name).read_bytes()).hexdigest()==digest
