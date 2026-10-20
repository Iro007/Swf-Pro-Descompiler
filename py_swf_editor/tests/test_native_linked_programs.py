import pytest

from py_swf.avm2 import ABCFile, MethodBodyInfo, MethodInfo, assemble_instructions, resolve_multiname
from py_swf.bytecode_validation import analyze_method, BytecodeValidationError, decode_avm2
from py_swf.compiler.class_compiler import compile_class_program, class_program_swf
from py_swf.engines import NativePythonEngine
from py_swf.swf_parser import SWFFile
from py_swf.validation import validate_swf_bytes
from tests.native_control_vm import run_bytes


INHERITANCE = '''package demo {
public interface IValue { function value():int; }
public class Base {
 protected var total:int = 2;
 public function Base(n:int=1) { super(); total=n; }
 public function value():int { return total; }
}
public class Child extends Base implements IValue {
 public function Child() { super(3); }
 override public function value():int { return super.value()+1; }
}
}'''


def test_inheritance_interfaces_override_preserve_exact_traits_and_captured_scope():
    compiled = compile_class_program(INHERITANCE)
    abc = compiled.abc
    assert compiled.class_names == ['demo.IValue', 'demo.Base', 'demo.Child']
    assert abc.instances[0].flags & 4 and abc.instances[0].super_name == 0
    assert abc.instances[2].super_name == abc.instances[1].name
    assert abc.instances[2].interfaces == [abc.instances[0].name]
    interface_trait = abc.instances[0].traits[0]
    namespace = abc.constant_pool.namespaces[abc.constant_pool.multinames[interface_trait.name]['ns']]
    assert namespace['kind'] == 8 and abc.constant_pool.strings[namespace['name']] == 'demo:IValue'
    override = abc.instances[2].traits[0]
    assert override.kind_flags & 0x20
    body = next(b for b in abc.method_bodies if b.method == override.method)
    assert body.init_scope_depth == 4  # global + Object + Base + class object
    assert any(ins.name == 'callsuper' and ins.args[0] == abc.instances[1].traits[1].name for ins in decode_avm2(body.code))
    assert not any(b.method == abc.instances[0].iinit for b in abc.method_bodies)
    assert validate_swf_bytes(class_program_swf(compiled))['valid']


def test_multifile_packages_imports_new_and_local_coercion_are_linked():
    primary = '''package app { import model.Counter; public class Main {
      public function run():int { var item:Counter = new Counter(4); return item.value(); }
    } }'''
    extra = {'model/Counter.as': '''package model { public class Counter {
      private var n:int=0; public function Counter(start:int) { super(); n=start; }
      public function value():int { return n; }
    } }'''}
    compiled = compile_class_program(primary, sources=extra)
    qname = next(i.name for i in compiled.abc.instances if resolve_multiname(compiled.abc.constant_pool, i.name) == 'model::Counter')
    body = next(b for b in compiled.abc.method_bodies if any(t.method == b.method for t in compiled.abc.instances[0].traits))
    assert any(i.name == 'constructprop' and i.args == (qname, 1) for i in decode_avm2(body.code))
    assert any(i.name == 'coerce' and i.args == (qname,) for i in decode_avm2(body.code))
    assert validate_swf_bytes(class_program_swf(compiled))['valid']


@pytest.mark.parametrize('base', ['Sprite', 'MovieClip'])
@pytest.mark.parametrize('signature', ['FWS', 'CWS', 'ZWS'])
def test_document_class_generates_stage_and_symbolclass(base, signature):
    source = f'''package app {{ import flash.display.{base}; public class Main extends {base} {{
      public function Main() {{ super(); trace(42); }}
    }} }}'''
    data = NativePythonEngine().compile_classes(source, signature, document_class='app.Main', width=640, height=480, frame_rate=30)
    movie = SWFFile(); movie.read_bytes(data, strict=True)
    assert movie.rect['xmax'] == 12800 and movie.rect['ymax'] == 9600 and movie.frame_rate == 30
    symbol = next(t for t in movie.tags if t.tag_type == 76)
    assert symbol.data == b'\x01\0\0\0app.Main\0'
    abc = ABCFile(); abc.parse(next(t for t in movie.tags if t.is_doabc).parse_doabc()[2])
    ctor = next(b for b in abc.method_bodies if b.method == abc.instances[0].iinit)
    assert ctor.init_scope_depth == (8 if base == 'Sprite' else 9)
    assert validate_swf_bytes(data)['valid']


def test_numeric_initializer_is_folded_with_number_arithmetic():
    compiled = compile_class_program('public class A { public var n:int=(2+3)*4; public function f():int { return 7*3+2; } }')
    assert compiled.abc.constant_pool.integers[compiled.abc.instances[0].traits[0].vindex] == 20
    method = compiled.abc.instances[0].traits[1].method
    body = next(b for b in compiled.abc.method_bodies if b.method == method)
    assert run_bytes(body.code, 'as3', abc=compiled.abc, body=body).value == 23


def test_interface_typed_local_calls_exact_interface_namespace():
    program = compile_class_program(INHERITANCE.replace('public class Child extends', 'public class Consumer { public function use():int { var value:IValue = new Child(); return value.value(); } } public class Child extends'))
    interface = next(instance for instance in program.abc.instances if instance.flags & 4)
    qname = interface.traits[0].name
    assert any(ins.name == 'callproperty' and ins.args[0] == qname for body in program.abc.method_bodies for ins in decode_avm2(body.code))


def test_document_class_rejects_a_required_constructor_parameter():
    compiled = compile_class_program('import flash.display.Sprite; public class Main extends Sprite { public function Main(n:int){super();} }')
    with pytest.raises(ValueError, match='zero arguments'):
        class_program_swf(compiled, document_class='Main')


def test_library_classes_preserve_requested_stage_without_document_class():
    data = NativePythonEngine().compile_classes(
        'public class Counter { public function answer():int { return 7; } }',
        width=640, height=360, frame_rate=25,
    )
    movie = SWFFile(); movie.read_bytes(data, strict=True)
    assert movie.rect == {'xmin': 0, 'xmax': 12800, 'ymin': 0, 'ymax': 7200}
    assert movie.frame_rate == 25
    assert not any(tag.tag_type == 76 for tag in movie.tags)
    with pytest.raises(ValueError, match='dimensions/frame rate'):
        NativePythonEngine().compile_classes('public class Counter {}', width=0)


@pytest.mark.parametrize('source', [
    'public class A extends B {} public class B extends A {}',
    'public final class A {} public class B extends A {}',
    'public class A { public function f():int{return 1;} } public class B extends A { public function f():int{return 2;} }',
    'public class A { public function f():int{return 1;} } public class B extends A { override public function f():String{return "x";} }',
    'public interface I { function f(n:int):int; } public class A implements I { public function f():int{return 1;} }',
    'public interface I { function f():int; } public class A implements I {}',
    'public class A { private var secret:int=1; } public class B extends A { public function f():int{return secret;} }',
    'public class A { public function A(n:int){super();} } public class B extends A {}',
    'public class A { public function f():Object { return new Missing(); } }',
    'public interface I { function f():void; } public class A { public function f():Object { return new I(); } }',
    'import flash.display.Missing; public class A extends Missing {}',
])
def test_unresolved_or_incompatible_program_semantics_stay_rejected(source):
    with pytest.raises((ValueError, SyntaxError)):
        compile_class_program(source)


def test_dead_eof_jump_is_valid_but_reachable_eof_and_mid_instruction_remain_invalid():
    abc = ABCFile(); abc.methods = [MethodInfo()]
    body = MethodBodyInfo(); body.local_count = 1
    body.code = assemble_instructions(abc.constant_pool, 'returnvoid\njump E\nE:')
    analyze_method(abc, body)
    body.code = assemble_instructions(abc.constant_pool, 'jump E\nE:')
    with pytest.raises(BytecodeValidationError, match='falls through'):
        analyze_method(abc, body)
    body.code = bytes.fromhex('4710feffff')  # dead jump into its own s24 operand
    with pytest.raises(BytecodeValidationError, match='Invalid branch target'):
        analyze_method(abc, body)
