import pytest

from py_swf.decompile.flow_graph import postdominators, nearest_common_postdom
from py_swf.decompile.ir import Block, Instruction
from py_swf.decompile.structure import Structurer


def test_diamond_postdominance():
    edges={0:[1,2],1:[3],2:[3],3:[4],4:[]}
    pd=postdominators(edges)
    assert pd[0]=={0,3,4}
    assert nearest_common_postdom([1,2],pd)==3


def test_reachable_from_both_is_not_necessarily_a_join():
    edges={0:[1,2],1:[3,4],2:[3],3:[4],4:[5],5:[]}
    pd=postdominators(edges)
    assert 3 not in pd[1]
    assert nearest_common_postdom([1,2],pd)==4
    blocks={pc:Block(pc,[Instruction(pc,pc+1,"nop")],successors=targets) for pc,targets in edges.items()}
    assert Structurer(blocks).join([1,2])==4


def test_closed_cycle_does_not_fabricate_a_postdominator():
    pd=postdominators({0:[1,2],1:[1],2:[]})
    assert pd[0]=={0}
    assert pd[1]=={1}
    assert nearest_common_postdom([1,2],pd) is None


def test_loop_boundary_is_treated_as_exit():
    pd=postdominators({0:[1],1:[2,3],2:[4],3:[4],4:[0]},excluded={0})
    assert nearest_common_postdom([2,3],pd)==4


def test_shared_tail_in_exclusive_arms_is_not_dropped_or_run_twice():
    from py_swf.avm2 import ABCFile, MethodInfo, MethodBodyInfo, assemble_instructions, search_or_add_string
    from py_swf.decompile.avm2_dec import decompile_method
    from py_swf.compiler import compile_actionscript
    from tests.native_control_vm import run_bytes
    abc, body = ABCFile(), MethodBodyInfo()
    method = MethodInfo()
    method.param_types = [0,0]
    method.param_names = [search_or_add_string(abc.constant_pool,'a'), search_or_add_string(abc.constant_pool,'b')]
    abc.methods = [method]
    body.code = assemble_instructions(abc.constant_pool,'getlocal_1\niftrue A\nfindpropstrict trace\npushstring "B"\ncallpropvoid trace 1\njump J\nA:\ngetlocal_2\niftrue J\njump C\nJ:\nfindpropstrict trace\npushstring "J"\ncallpropvoid trace 1\nC:\nfindpropstrict trace\npushstring "C"\ncallpropvoid trace 1\nreturnvoid')
    text, error = decompile_method(abc,body)
    assert error is None, error
    compiled_abc, compiled_body = ABCFile(), MethodBodyInfo()
    compiled_abc.methods = [MethodInfo()]
    compiled_body.code = assemble_instructions(compiled_abc.constant_pool,compile_actionscript(text,'as3',registers={'a':1,'b':2},next_register=3))
    for a,b,expected in [(False,False,['B','J','C']), (False,True,['B','J','C']), (True,False,['C']), (True,True,['J','C'])]:
        before = run_bytes(body.code,'as3',abc=abc,body=body,arguments=[a,b])
        after = run_bytes(compiled_body.code,'as3',abc=compiled_abc,body=compiled_body,arguments=[a,b])
        assert before.traces == after.traces == expected


@pytest.mark.parametrize('variant',['flex','air'])
def test_real_always_break_reconstructed_behavior(variant):
    from tests.conftest import JPEXS_TESTDATA
    from tests.test_native_reconstruction import corpus_method, compile_bytes
    from tests.native_control_vm import run_bytes
    from py_swf.decompile.avm2_dec import decompile_method
    if not JPEXS_TESTDATA.is_dir():
        pytest.skip('Local corpus unavailable')
    abc,body=corpus_method(JPEXS_TESTDATA / f'as3_new/bin/as3_new.{variant}.swf','TestAlwaysBreak.run')
    text,error=decompile_method(abc,body)
    assert error is None,error
    code,new_abc,new_body=compile_bytes(text,'as3')
    before=run_bytes(body.code,'as3',abc=abc,body=body)
    after=run_bytes(code,'as3',abc=new_abc,body=new_body)
    assert before.traces==after.traces==['a','b','d','e','f']


@pytest.mark.parametrize('method',['TestTryCatchInIf.run','TestTryCatchInIf2.run'])
@pytest.mark.parametrize('random_value',[-11,0,11])
@pytest.mark.parametrize('inject',[False,True])
def test_real_try_in_if_normal_and_exception_paths(method,random_value,inject):
    from tests.conftest import JPEXS_TESTDATA
    from tests.test_native_reconstruction import corpus_method, compile_bytes
    from tests.native_control_vm import run_bytes, FlashError, Thrown
    from py_swf.decompile.avm2_dec import decompile_method
    if not JPEXS_TESTDATA.is_dir():
        pytest.skip('Local corpus unavailable')
    abc,body=corpus_method(JPEXS_TESTDATA / 'as3_cross_compile/bin/as3_cross_compile.flex.swf',method)
    text,error=decompile_method(abc,body)
    assert error is None,error
    code,new_abc,new_body=compile_bytes(text,'as3')
    injected=FlashError('injected')
    def run(data,abc,body):
        events=[]
        def trace(value):
            events.append(value)
            if inject and value in ('a','b'):
                raise Thrown(injected)
        result=run_bytes(data,'as3',abc=abc,body=body,variables={'Math':{'random':lambda:random_value},'trace':trace})
        return events,result.value,result.error,result.raised
    assert run(body.code,abc,body)==run(code,new_abc,new_body)
