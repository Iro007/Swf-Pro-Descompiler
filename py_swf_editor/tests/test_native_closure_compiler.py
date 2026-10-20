"""Nested functions compile to real bytecode; only the independent CLI VM runs."""
import pytest

from py_swf.avm1 import assemble_avm1,disassemble_avm1
from py_swf.bytecode_validation import validate_avm1
from py_swf.compiler import compile_actionscript
from py_swf.editing import edit_script
from py_swf.swf_parser import SWFTag
from tests.native_control_vm import run_bytes


def as2(source,variables=None):
    code=assemble_avm1(compile_actionscript(source,'as2'));validate_avm1(code)
    return run_bytes(code,'as2',variables=variables)


def test_as2_closure_keeps_live_local_cell_after_factory_returns():
    source='function make(start) {var n=start; return function(){n++;return n;};} var a=make(1);var b=make(10);trace(a());trace(b());trace(a());'
    assert as2(source).traces==[2,11,3]


def test_as2_local_declaration_shadows_outer_without_overwriting_it():
    result=as2('var n=100;var f=function(){var n=2;return function(){n++;return n;};};var c=f();trace(c());trace(n);')
    assert result.traces==[3,100]


def test_as2_named_local_functions_are_hoisted_and_recursion_is_bounded():
    result=as2('trace(f(5));function f(n){if(n<=1)return 1;return n*f(n-1);}')
    assert result.traces==[120]


def test_as2_nested_function_declaration_closes_over_its_parent_parameter():
    result=as2('function make(n){return inner;function inner(){return n;}}var f=make(7);trace(f());')
    assert result.traces==[7]


def test_as2_missing_argument_shadows_an_outer_variable():
    assert as2('var n=99;var f=function(n){return n;};trace(f());').traces==[None]


def test_as2_parameter_shadows_the_parent_capture():
    assert as2('var n=99;var f=function(n){return function(n){return n;};};var g=f(8);trace(g(2));trace(n);').traces==[2,99]


def test_as2_argument_side_effects_remain_in_source_order():
    events=[]
    def tick(name):events.append(name);return name
    result=as2('function f(a,b){trace(a);trace(b);}f(tick("first"),tick("second"));',{'tick':tick})
    assert events==['first','second'] and result.traces==['first','second']


def test_as2_immediately_invoked_anonymous_function_and_arguments_array():
    assert as2('trace((function(a){return arguments[0]+1;})(4));').traces==[5]


def test_as2_functions_nested_in_loop_literals_keep_the_live_variable():
    assert as2('var f;for(var k in data){f=function(){return k;};}trace(f());',{'data':{'a':1,'b':2}}).traces==['b']


@pytest.mark.parametrize('source',[
    'var f=function named(){return 1;};',
    'var f=function(n:int):int{return n;};',
    'if(true){function f(){return 1;}}',
    'try {throw 1;} catch(e) {var f=function(){return e;};}',
    'function trace(n){return n;}',
])
def test_as2_unlinked_function_bindings_are_explicitly_rejected(source):
    with pytest.raises((ValueError,SyntaxError)):as2(source)


def test_avm1_function_roundtrip_and_minimum_version_preserve_input():
    tag=SWFTag(12,b'\0')
    with pytest.raises(ValueError,match='ActionDefineFunction requires SWF version 5'):
        edit_script(tag,'var f=function(){return 1;};trace(f());',source=True,swf_version=4)
    assert tag.data==b'\0'
    edited,_=edit_script(tag,'var f=function(){return 1;};trace(f());',source=True,swf_version=5)
    assert assemble_avm1(disassemble_avm1(edited.data))==edited.data


def as3(source,variables=None,*,registers=None,register_types=None,arguments=()):
    from py_swf.avm2 import ABCFile,MethodInfo,MethodBodyInfo,assemble_instructions
    from py_swf.bytecode_validation import analyze_method
    abc=ABCFile();abc.methods=[MethodInfo()]
    assembly=compile_actionscript(source,'as3',abc=abc,method_index=0,registers=registers,register_types=register_types,next_register=max((registers or {}).values(),default=0)+1)
    body=MethodBodyInfo();body.local_count=1
    body.code=assemble_instructions(abc.constant_pool,assembly,exception_table=body.exceptions,activation_traits=body.traits)
    limits=analyze_method(abc,body)
    body.max_stack,body.local_count,body.max_scope_depth=limits.max_stack,limits.local_count,limits.max_scope_depth
    abc.method_bodies.insert(0,body)
    parsed=ABCFile();parsed.parse(abc.serialize(),strict=True)
    return run_bytes(parsed.method_bodies[0].code,'as3',abc=parsed,body=parsed.method_bodies[0],variables=variables,arguments=arguments),parsed,assembly


def test_as3_closure_keeps_live_activation_after_factory_return():
    result,abc,_=as3('function make(start:int):Function {var n:int=start;return function():int {n++;return n;};} var a=make(1);var b=make(10);trace(a());trace(b());trace(a());')
    assert result.traces==[2,11,3]
    assert any(method.flags&2 for method in abc.methods)
    assert any(body.traits for body in abc.method_bodies)


def test_as3_grandchild_captures_two_distinct_live_activations():
    result,_,_=as3('function make(a:int):Function {var n:int=a;return function(b:int):Function {var k:int=b;return function():int {n++;k++;return n+k;};};} var mid=make(1);var first=mid(10);var second=mid(20);trace(first());trace(second());trace(first());')
    assert result.traces==[13,24,16]


def test_as3_shadowed_parameter_and_typed_default_capture_are_preserved():
    result,_,_=as3('var n:int=100;var f=function(n:int):Function {return function(n:int):int{return n;};};var g=f(8);trace(g(2));var read=function():int{return later;};trace(read());var later:int=7;trace(read());trace(n);')
    assert result.traces==[2,0,7,100]


def test_as3_hoisted_recursion_uses_the_same_function_cell():
    result,_,_=as3('trace(f(5));function f(n:int):int {if(n<=1)return 1;return n*f(n-1);}')
    assert result.traces==[120]


def test_as3_capture_survives_exception_scope_reset_and_finally():
    result,_,_=as3('var n:int=1;var f=function():int{return n;};try{throw "x";}catch(e){n=2;trace(f());}finally{n=3;trace(f());}trace(f());')
    assert result.traces==[2,3,3]


def test_as3_enumeration_closures_share_live_function_scoped_variable():
    result,_,_=as3('var f;for each(var n:int in [1,2,3]){f=function():int{return n;};}trace(f());')
    assert result.traces==[3]


def test_as3_parameter_aliases_share_one_capture_cell():
    result,_,_=as3('var f=function():int {return local1;};x=7;trace(f());',registers={'x':1,'local1':1},register_types={'x':'int','local1':'int'},arguments=(2,))
    assert result.traces==[7]


def test_activation_directives_cannot_be_discarded_when_converted_to_text():
    from py_swf.avm2 import assemble_instructions
    _,abc,assembly=as3('var n:int=1;var f=function():int{return n;};trace(f());')
    with pytest.raises(ValueError,match='activation_traits output'):
        assemble_instructions(abc.constant_pool,str(assembly))


@pytest.mark.parametrize('source',[
    'var f=function(){return this;};','var f=function(){return arguments;};',
    'try{throw 1;}catch(e){var f=function(){return e;};}',
    'if(true){function f(){return 1;}}',
    'var f=function():Missing{return null;};',
])
def test_as3_unproven_receiver_catch_or_type_bindings_stay_rejected(source):
    with pytest.raises((ValueError,SyntaxError)):as3(source)


def test_class_program_closures_preserve_parameter_shadowing_a_static_constant():
    from py_swf.compiler.class_compiler import compile_class_program
    abc=compile_class_program('public class Counter {public static const n:int=999;public function make(n:int):Function {return function():int{n++;return n;};}}').abc
    method=abc.instances[0].traits[0].method;body=next(body for body in abc.method_bodies if body.method==method)
    result=run_bytes(body.code,'as3',abc=abc,body=body,arguments=(4,),captured=tuple({} for _ in range(body.init_scope_depth)))
    assert result.value()==5 and result.value()==6


def test_editing_adds_declared_children_and_preserves_existing_opaque_guard():
    from py_swf.avm2 import ABCFile
    from py_swf.editing import source_edit_reason
    from tests.native_fixtures import as3_tag
    original=as3_tag();edited,_=edit_script(original,'var n:int=2;var f=function():int{n++;return n;};trace(f());return n;',1,source=True)
    abc=ABCFile();abc.parse(edited.parse_doabc()[2],strict=True)
    body=abc.method_bodies[1]
    result=run_bytes(body.code,'as3',abc=abc,body=body)
    assert result.traces==[3] and result.value==3
    assert source_edit_reason(abc,body) is not None
    with pytest.raises(ValueError,match='activation closures'):
        edit_script(edited,'return 1;',1,source=True)
    assert original.data!=edited.data
