"""Enumeration semantics checked by the bytecode VM, without browser/GUI."""
import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.avm2 import ABCFile,MethodBodyInfo,MethodInfo,assemble_instructions
from py_swf.bytecode_validation import analyze_method,validate_avm1
from py_swf.compiler import compile_actionscript
from py_swf.compiler.class_compiler import compile_class_program
from py_swf.editing import edit_script
from py_swf.swf_parser import SWFTag
from tests.native_control_vm import run_bytes,Thrown


def compiled(source,target):
    assembly=compile_actionscript(source,target)
    if target=='as2':
        code=assemble_avm1(assembly);validate_avm1(code)
        return code,None,None
    abc=ABCFile();abc.methods=[MethodInfo()]
    body=MethodBodyInfo();body.local_count=1
    body.code=assemble_instructions(abc.constant_pool,assembly,exception_table=body.exceptions)
    limits=analyze_method(abc,body)
    body.max_stack,body.local_count,body.max_scope_depth=limits.max_stack,limits.local_count,limits.max_scope_depth
    abc.method_bodies=[body]
    return body.code,abc,body


def run(source,target,variables=None):
    code,abc,body=compiled(source,target)
    return run_bytes(code,target,abc=abc,body=body,variables=variables)


@pytest.mark.parametrize('target',['as2','as3'])
def test_collection_evaluated_once_and_break_continue_use_correct_loop(target):
    calls=[]
    def collection():calls.append('collection');return {'a':11,'b':22,'c':33,'d':44}
    result=run('for(var key in collection()) {if(key=="b") continue; trace(key); if(key=="c") break;} trace("done");',target,{'collection':collection})
    assert calls==['collection'] and result.traces==['a','c','done']


@pytest.mark.parametrize('target',['as2','as3'])
@pytest.mark.parametrize('value',[{},[],None])
def test_empty_collection_keeps_existing_target(value,target):
    result=run('var key="keep"; for(key in data) {trace("unexpected");} trace(key);',target,{'data':value})
    assert result.traces==['keep']


@pytest.mark.parametrize('target',['as2','as3'])
def test_nested_enumerations_and_abrupt_return_are_stack_safe(target):
    result=run('for(var x in first) {for(var y in second) {trace(x); trace(y); return 7;}}',target,{'first':{'a':1,'b':2},'second':{'c':3,'d':4}})
    assert result.value==7 and result.traces==['a','c']


@pytest.mark.parametrize('target',['as2','as3'])
def test_finally_runs_on_continue_and_break(target):
    result=run('for(var key in data) {try {if(key=="a")continue; break;} finally {trace(key);}} trace("end");',target,{'data':{'a':1,'b':2,'c':3}})
    assert result.traces==['a','b','end']


def test_as3_for_each_values_are_read_once_before_each_body():
    events=[]
    class Values(dict):
        def __getitem__(self,key):events.append('get:'+key);return super().__getitem__(key)
    def collection():events.append('collection');return Values(a=10,b=20,c=30)
    def seen(value):events.append('body:'+str(value))
    result=run('for each(var value:int in collection()) {seen(value); if(value==20)continue; trace(value);}', 'as3',{'collection':collection,'seen':seen})
    assert events==['collection','get:a','body:10','get:b','body:20','get:c','body:30']
    assert result.traces==[10,30]


def test_as3_value_getter_exception_reaches_catch_and_finally():
    class Values(dict):
        def __getitem__(self,key):
            if key=='b':raise Thrown('getter failed')
            return super().__getitem__(key)
    result=run('try {for each(var value in data) {trace(value);}} catch(e) {trace(e);} finally {trace("finally");}', 'as3',{'data':Values(a=1,b=2,c=3)})
    assert result.traces==[1,'getter failed','finally']


def test_as3_for_each_array_and_named_existing_variable():
    assert run('var n:int=0; for each(n in [1,2,3]) {trace(n);} trace(n);','as3').traces==[1,2,3,3]


def test_class_program_for_each_compiles_with_exact_method_limits():
    abc=compile_class_program('public class Sum {public function run():int {var n:int=0;for each(var value:int in [1,2,3]){n+=value;}return n;}}').abc
    method=abc.instances[0].traits[0].method;body=next(b for b in abc.method_bodies if b.method==method)
    assert run_bytes(body.code,'as3',abc=abc,body=body).value==6
    assert analyze_method(abc,body).max_stack==body.max_stack


@pytest.mark.parametrize('source',[
    'for(var key=1 in data) {}','for(var key=undefined in data) {}','for(var a,b in data) {}',
    'for(obj.key in data) {}','for each(var key=0;key<2;key++) {}',
])
def test_ambiguous_enumeration_targets_or_initializers_are_rejected(source):
    with pytest.raises((ValueError,SyntaxError)):compiled(source,'as3')


def test_as2_for_each_and_old_swf_version_are_explicitly_rejected():
    with pytest.raises(SyntaxError,match='belongs to AS3'):compiled('for each(var n in data) {}','as2')
    tag=SWFTag(12,b'\0')
    with pytest.raises(ValueError,match='ActionEnumerate2 requires SWF version 6'):
        edit_script(tag,'for(var key in data){trace(key);}',source=True,swf_version=5)
    assert tag.data==b'\0'
