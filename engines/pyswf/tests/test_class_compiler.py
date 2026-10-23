import pytest

from py_swf.avm2 import ABCFile,resolve_multiname
from py_swf.bytecode_validation import analyze_method,decode_avm2
from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
from py_swf.swf_parser import SWFFile
from tests.native_control_vm import run_bytes


SOURCE='''package demo {
    [Inspectable(name="counter")]
    public final class Counter {
        private var total:int = 2;
        public static const VERSION:String = "v1";
        public function Counter(start:int = 0) { super(); this.total = start; }
        public function add(n:int = 1):int { this.total = this.total + n; return this.total; }
        public function get value():int { return this.total; }
        public function set value(n:int):void { this.total = n; }
    }
}'''


def test_complete_object_class_emits_traits_signatures_constructor_and_script():
    compiled=compile_class_program(SOURCE)
    abc=ABCFile()
    abc.parse(compiled.abc_bytes())
    assert compiled.class_names==["demo.Counter"]
    assert len(abc.instances)==len(abc.classes)==1
    instance=abc.instances[0]
    assert resolve_multiname(abc.constant_pool,instance.name)=="demo::Counter"
    assert instance.flags&2
    assert [trait.kind_flags&15 for trait in instance.traits]==[0,1,2,3]
    ctor=abc.methods[instance.iinit]
    assert len(ctor.param_types)==1 and ctor.options[0]["kind"]==3
    body=next(b for b in abc.method_bodies if b.method==instance.iinit)
    assert [i.name for i in decode_avm2(body.code)].count("constructsuper")==1
    assert abc.scripts[0].traits[0].metadata
    for body in abc.method_bodies:
        assert analyze_method(abc,body).max_stack==body.max_stack


@pytest.mark.parametrize("signature",["FWS","CWS","ZWS"])
def test_class_program_swf_reopens_without_losing_abc(signature):
    compiled=compile_class_program(SOURCE)
    movie=SWFFile()
    movie.read_bytes(class_program_swf(compiled,signature))
    assert movie.signature==signature
    assert movie.tags[0].data[0]&8
    assert movie.tags[1].parse_doabc()[2]==compiled.abc_bytes()


def test_class_method_private_binding_is_preserved_not_flattened():
    compiled=compile_class_program(SOURCE)
    abc=compiled.abc
    instance=abc.instances[0]
    field=instance.traits[0]
    method=instance.traits[1]
    body=next(b for b in abc.method_bodies if b.method==method.method)
    references=[i.args[0] for i in decode_avm2(body.code) if i.name in ("getproperty","setproperty")]
    assert references==[field.name,field.name,field.name]
    assert abc.constant_pool.namespaces[abc.constant_pool.multinames[field.name]["ns"]]["kind"]==5


def test_simple_class_method_result_from_saved_bytecode():
    compiled=compile_class_program('package demo { public class Arithmetic { public function run(n:int):int { return n * 3 + 2; } } }')
    abc=compiled.abc
    trait=abc.instances[0].traits[0]
    body=next(b for b in abc.method_bodies if b.method==trait.method)
    result=run_bytes(body.code,"as3",abc=abc,body=body,arguments=(4,))
    assert result.value==14 and not result.raised


@pytest.mark.parametrize("source",[
    'public class Bad extends Unknown {}',
    'public class Bad { public var x:int = calculate(); }',
    'public class Bad { public function Bad() { trace(1); super(); } }',
    'public class Bad { public function run():int { return unknown; } }',
    'public class Bad { public function run():int; }',
    'public class Object {}',
    'public class Bad { public function Bad() { return 1; } }',
    'public final dynamic class Bad {}',
    'public class Bad { public static var x:int; public function f():int { return this.x; } }',
    'public class Bad { public const X:int=1; public function f():void { X++; } }',
    'public class Bad { public const X:int=1; public function f():void { this.X=3; } }',
    'public class Bad { public static function f():Object { return this; } }',
    'public class Bad { public function f(x:void):void {} }',
    'public class Bad { public function f(...x:int):void {} }',
    'public class Bad { public function f(...x:* = null):void {} }',
    'public class Bad { public function get x():int { return 1; } public function set x(n:String):void {} }',
    'public class Bad { public function set x(n:int=1):void {} }',
])
def test_unsupported_class_semantics_fail_explicitly(source):
    with pytest.raises((ValueError,SyntaxError)):
        compile_class_program(source)


def test_class_abi_allows_body_changes_but_not_signature_changes():
    original=compile_class_program(SOURCE)
    assert compile_class_program(SOURCE.replace("n; return","n + 2; return")).abi_sha256==original.abi_sha256
    assert compile_class_program(SOURCE.replace("add(n:int = 1)","add(n:int = 2)")).abi_sha256!=original.abi_sha256


def test_static_increment_from_instance_uses_explicit_class_receiver():
    abc=compile_class_program('public class A { private static var count:int=0; public function f():int { count++; return count; } }').abc
    body=next(b for b in abc.method_bodies if b.method==abc.instances[0].traits[0].method)
    instructions=decode_avm2(body.code)
    assert sum(i.name=="getlex" and i.args[0]==abc.instances[0].name for i in instructions)==3


def test_literal_markup_and_comments_are_not_e4x():
    abc=compile_class_program('public dynamic class A { public var s:String="<node/>"; public function f():String { /* <node/> */ return "</node>"; } }').abc
    assert "<node/>" in abc.constant_pool.strings
    assert not abc.instances[0].flags&1


def test_abc_is_reproducible_across_process_hash_seeds():
    import os
    import subprocess
    import sys
    command=[sys.executable,"-c","import hashlib,sys; from py_swf.compiler.class_compiler import compile_class_program; print(hashlib.sha256(compile_class_program(sys.argv[1]).abc_bytes()).hexdigest())",SOURCE]
    hashes=[subprocess.check_output(command,env=dict(os.environ,PYTHONHASHSEED=seed),text=True).strip() for seed in ("7","98765")]
    assert hashes[0]==hashes[1]
