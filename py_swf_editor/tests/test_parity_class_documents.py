"""Whole declaration reconstruction and immutable candidate admission."""
import hashlib

import pytest

from py_swf.abc_symbols import AbcSymbolIndex
from py_swf.avm2 import ABCFile, assemble_instructions, resolve_multiname
from py_swf.bytecode_validation import analyze_method, decode_avm2
from py_swf.class_documents import compile_reconstructed_document, reconstruct_abc_documents
from py_swf.compiler.class_compiler import compile_class_program
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.source_document import native_source_documents
from py_swf.swf_parser import SWFTag
from tests.native_control_vm import run_bytes


def tag_for(abc):
    return SWFTag(82, b"\0\0\0\0parity\0" + abc.serialize())


def method_for(abc, class_index, name):
    trait = next(t for t in abc.instances[class_index].traits if resolve_multiname(abc.constant_pool, t.name).rsplit("::", 1)[-1] == name)
    return next(body for body in abc.method_bodies if body.method == trait.method)


def locator_for(tag, scope="script"):
    abc = ABCFile(); abc.parse(tag.parse_doabc()[2])
    sections = reconstruct_abc_documents(abc)
    documents = native_source_documents(tag, 3, b"immutable original SWF", result={"kind": "avm2", "sections": sections})
    return next(d for d in documents if d["kind"] == scope), next(s for s in sections if s["scope"] == scope)


def compile_parameter_body(source):
    """Compile a method fragment with the same local1 formal argument binding."""
    from py_swf.avm2 import MethodInfo, MethodBodyInfo
    from py_swf.compiler.as_compiler import compile_actionscript
    abc = ABCFile(); abc.methods = [MethodInfo()]
    abc.methods[0].param_types = [0]
    assembly = compile_actionscript(source, "as3", registers={"local1": 1}, next_register=2)
    body = MethodBodyInfo(); body.local_count = 2
    body.code = assemble_instructions(abc.constant_pool, assembly, exception_table=body.exceptions)
    limits = analyze_method(abc, body)
    body.max_stack, body.local_count, body.max_scope_depth = limits.max_stack, limits.local_count, limits.max_scope_depth
    return body.code, abc, body


def test_complete_class_and_script_include_every_body_and_real_private_visibility():
    source = 'package demo { public class A { private var n:int=2; public function answer():int{return this.n;} } }'
    abc = compile_class_program(source).abc
    sections = reconstruct_abc_documents(abc)
    cls, script = sections
    assert cls["completeness"] == script["completeness"] == "complete"
    assert "private var n:int = 2;" in cls["source"]
    assert "public var n" not in cls["source"]
    assert set(script["unit_ids"]) == {f"method:{b.method}" for b in abc.method_bodies}
    init = next(b for b in abc.method_bodies if b.method == abc.scripts[0].init)
    text, error = decompile_method(abc, init)
    assert text is None and "separate script/class" in error
    text, error = decompile_method(abc, init, include_script_declarations=True)
    assert error is None and "class A" in text and "return this.n;" in text


def test_failed_member_keeps_an_explicit_partial_document_and_no_empty_implementation():
    abc = compile_class_program('public class A { public function answer():int{return 3;} }').abc
    method_for(abc, 0, "answer").code = b"\xff"
    cls, script = reconstruct_abc_documents(abc)
    assert cls["completeness"] == script["completeness"] == "partial"
    assert "Incomplete function answer" in cls["source"]
    assert "retained as P-code" in cls["source"]
    assert not cls["editable_source"] and not script["editable_source"]


def test_attribute_namespace_declaration_is_not_flattened_into_normal_field():
    abc = compile_class_program('public class A { public var value:int; }').abc
    name = abc.instances[0].traits[0].name
    abc.constant_pool.multinames[name]["kind"] = 13
    cls = reconstruct_abc_documents(abc)[0]
    assert cls["completeness"] == "partial"
    assert any("Attribute QName" in error for error in cls["diagnostics"])


@pytest.mark.parametrize("mutation", ["extra_call", "wrong_base", "wrong_class"])
def test_newclass_bootstrap_rejects_executable_extra_code_and_wrong_identities(mutation):
    abc = compile_class_program('public class A {}').abc
    init = next(body for body in abc.method_bodies if body.method == abc.scripts[0].init)
    from py_swf.avm2 import disassemble_instructions
    assembly = disassemble_instructions(abc.constant_pool, init.code)
    if mutation == "extra_call":
        assembly = assembly.replace("returnvoid", 'findpropstrict trace\npushstring "side effect"\ncallpropvoid trace 1\nreturnvoid')
    elif mutation == "wrong_class":
        assembly = assembly.replace("newclass 0", "newclass 1")
    else:
        assembly = assembly.replace("getlex Object", "pushnull")
    init.code = assemble_instructions(abc.constant_pool, assembly)
    text, error = decompile_method(abc, init)
    assert text is None and error


@pytest.mark.parametrize("scope", ["class", "script"])
def test_atomic_source_document_edit_reopens_and_runs_changed_method(scope):
    abc = compile_class_program('public class A { public function answer(n:int):int{return n+1;} }').abc
    original = tag_for(abc)
    document, section = locator_for(original, scope)
    assert document["compiler_eligibility"]["eligible"]
    saved, audit = compile_reconstructed_document(original, document, section["source"].replace("n + 1", "n + 2"))
    assert original.data == tag_for(abc).data
    assert audit["exact_original_roundtrip"]
    reopened = ABCFile(); reopened.parse(saved.parse_doabc()[2], strict=True)
    body = method_for(reopened, 0, "answer")
    result = run_bytes(body.code, "as3", abc=reopened, body=body, arguments=(4,))
    assert result.value == 6 and not result.raised
    for body in reopened.method_bodies:
        analyze_method(reopened, body)


@pytest.mark.parametrize("field", ["tag_sha256", "original_source_sha256"])
def test_stale_source_and_byte_guards_reject_without_mutating_original(field):
    tag = tag_for(compile_class_program('public class A {}').abc)
    document, section = locator_for(tag)
    before = tag.data
    with pytest.raises(ValueError, match="stale"):
        compile_reconstructed_document(tag, {**document, field: "0" * 64}, section["source"])
    assert tag.data == before


def test_declaration_changes_are_rejected_before_candidate_admission():
    tag = tag_for(compile_class_program('public class A { public function f():int{return 3;} }').abc)
    document, section = locator_for(tag)
    with pytest.raises(ValueError, match="signature"):
        compile_reconstructed_document(tag, document, section["source"].replace("f():int", "f():Number"))


def test_ordered_static_initializer_compiles_as_one_cinit_and_preserves_trace():
    abc = compile_class_program('public class A { { trace("first"); } { trace("second"); } }').abc
    cinit = next(body for body in abc.method_bodies if body.method == abc.classes[0].cinit)
    original = run_bytes(cinit.code, "as3", abc=abc, body=cinit)
    assert original.traces == ["first", "second"]
    source = reconstruct_abc_documents(abc)[0]["source"]
    recompiled = compile_class_program(source).abc
    cinit2 = next(body for body in recompiled.method_bodies if body.method == recompiled.classes[0].cinit)
    rebuilt = run_bytes(cinit2.code, "as3", abc=recompiled, body=cinit2)
    assert rebuilt.traces == original.traces


def test_namespace_uri_alias_and_qualified_calls_roundtrip_without_flattening():
    source = 'package app { public namespace ns="urn:parity"; public class A { ns function f():int{return 3;} public function run():int{return this.ns::f();} } }'
    compiled = compile_class_program(source)
    classes, script = reconstruct_abc_documents(compiled.abc)
    assert classes["completeness"] == script["completeness"] == "complete"
    assert 'namespace ns = "urn:parity"' in classes["source"]
    assert "ns function f" in classes["source"] and "this.ns::f()" in classes["source"]
    assert script["compiler_eligibility"]["eligible"]
    rebuilt = compile_class_program(script["source"])
    assert rebuilt.abc_bytes() == compiled.abc_bytes()
    context = AbcSymbolIndex([rebuilt.abc]).method_context(0, rebuilt.abc.instances[0].traits[1].method)
    assert context.owner.kind == "instance" and context.owner.index == 0


def test_reference_coerce_uses_typed_assignment_and_accepts_qualified_type_source():
    source = 'package demo { public class Target {} public class A { public function f(value:Object):Target{return value;} } }'
    abc = compile_class_program(source).abc
    cls = next(s for s in reconstruct_abc_documents(abc) if s.get("class_index") == 1)
    assert cls["completeness"] == "complete"
    assert ":demo.Target = value;" in cls["source"]
    assert " as " not in cls["source"] and "demo.Target(value)" not in cls["source"]
    rebuilt = compile_class_program('package demo {public class Target {}}', sources={"A.as": cls["source"]}).abc
    references = [resolve_multiname(rebuilt.constant_pool, i.args[0]) for body in rebuilt.method_bodies for i in decode_avm2(body.code) if i.name == "coerce"]
    assert "demo::Target" in references


def test_reserved_public_namespace_qualifier_compiles_to_the_blank_public_qname():
    source = 'public class A { public var value:int; public function read():int{return (this).public::value;} public function write(n:int):void{(this).public::value=n;} }'
    abc = compile_class_program(source).abc
    public_name = abc.instances[0].traits[0].name
    for name in ("read", "write"):
        refs = [ins.args[0] for ins in decode_avm2(method_for(abc, 0, name).code) if ins.name in ("getproperty", "setproperty")]
        assert refs == [public_name]
    namespace = abc.constant_pool.namespaces[abc.constant_pool.multinames[public_name]["ns"]]
    assert namespace["kind"] == 0x16 and namespace["name"] == 0


def test_explicit_public_never_selects_a_same_spelled_private_name_on_method_edit():
    from tests.test_private_bindings import private_fixture, tag_for as private_tag
    from py_swf.editing import edit_script
    abc, private = private_fixture()
    before = private_tag(abc)
    with pytest.raises(ValueError, match="blank public QName"):
        edit_script(before, 'return this.public::secret;', 1, source=True)
    assert before.data == private_tag(abc).data
    assert abc.constant_pool.namespaces[abc.constant_pool.multinames[private]["ns"]]["kind"] == 5


@pytest.mark.parametrize("mutation", ["duplicate", "unrelated"])
def test_duplicate_or_unrelated_middle_bootstrap_scope_is_not_omitted(mutation):
    from py_swf.avm2 import disassemble_instructions
    abc = compile_class_program('public class A {public function f():int{return 3;}}').abc
    init = next(body for body in abc.method_bodies if body.method == abc.scripts[0].init)
    assembly = "\n".join(line.strip() for line in disassemble_instructions(abc.constant_pool, init.code).splitlines())
    extra = "Object" if mutation == "duplicate" else "String"
    assembly = assembly.replace("getscopeobject 0", f"getlex {extra}\npushscope\ngetscopeobject 0", 1)
    assembly = assembly.replace("popscope", "popscope\npopscope", 1)
    init.code = assemble_instructions(abc.constant_pool, assembly)
    init.max_scope_depth += 1
    for body in abc.method_bodies:
        if body is not init:
            body.init_scope_depth += 1; body.max_scope_depth += 1
    classes, script = reconstruct_abc_documents(abc)
    assert classes["completeness"] == "complete"
    assert script["completeness"] == "partial"
    assert any("order or multiplicity" in error for error in script["diagnostics"])
    text, error = decompile_method(abc, method_for(abc, 0, "f"))
    assert error is None and "return 3;" in text


def test_swapped_declared_ancestor_scopes_do_not_count_as_complete_script():
    from py_swf.avm2 import disassemble_instructions
    abc = compile_class_program('public class A {} public class B extends A {public function f():int{return 3;}}').abc
    init = next(body for body in abc.method_bodies if body.method == abc.scripts[0].init)
    assembly = "\n".join(line.strip() for line in disassemble_instructions(abc.constant_pool, init.code).splitlines())
    old = "getlex Object\npushscope\ngetlex A\npushscope"
    assert old in assembly
    init.code = assemble_instructions(abc.constant_pool, assembly.replace(old, "getlex A\npushscope\ngetlex Object\npushscope", 1))
    assert reconstruct_abc_documents(abc)[-1]["completeness"] == "partial"


def test_display_bootstrap_contract_is_derived_from_hash_verified_sdk_metadata():
    from py_swf.decompile.hierarchy_contract import external_hierarchy, PLAYERGLOBAL_32_SHA256
    contract = external_hierarchy()
    if not contract:
        pytest.skip("Verified local playerglobal metadata unavailable")
    assert len(PLAYERGLOBAL_32_SHA256) == 64
    assert contract[(0x16, "flash.display", "Sprite")][0] == (0x16, "flash.display", "DisplayObjectContainer")
    with pytest.raises(TypeError):
        contract[(0x16, "flash.display", "Sprite")] = None
    abc = compile_class_program('import flash.display.Sprite; public class Main extends Sprite {public function Main(){super();}}').abc
    assert reconstruct_abc_documents(abc)[-1]["completeness"] == "complete"
    assert "import flash.display.Sprite;" in reconstruct_abc_documents(abc)[0]["source"]


def test_private_number_cannot_replace_intrinsic_conversion_with_a_member_call():
    from py_swf.editing import edit_script
    abc = compile_class_program('public class A {private var Number:int=1;public function convert(value:*):*{return value;}}').abc
    body = method_for(abc, 0, "convert")
    body.code = assemble_instructions(abc.constant_pool, 'getlocal_0\npushscope\ngetlocal_1\nconvert_d\nsetlocal_2\ngetlocal_2\nreturnvalue')
    source, error = decompile_method(abc, body)
    assert error is None and "+(" in source and "Number(" not in source
    replacement, _ = edit_script(tag_for(abc), source, abc.method_bodies.index(body), source=True)
    parsed = ABCFile(); parsed.parse(replacement.parse_doabc()[2], strict=True)
    edited = parsed.method_bodies[abc.method_bodies.index(body)]
    assert all(ins.name != "callproperty" for ins in decode_avm2(edited.code))
    assert run_bytes(edited.code, "as3", abc=parsed, body=edited, arguments=(7,)).value == 7


def test_shadowed_type_signature_is_partial_and_preserves_exact_method_pcode():
    abc = compile_class_program('public class A {private var Number:int=1;public function value(input:*):Number{return input;}}').abc
    cls = reconstruct_abc_documents(abc)[0]
    assert cls["completeness"] == "partial"
    assert any("shadowed" in message for message in cls["diagnostics"])
    from py_swf.editing import source_edit_reason
    assert "shadowed" in source_edit_reason(abc, method_for(abc, 0, "value"))


def test_conversion_side_effect_runs_once_before_later_trace_even_when_result_is_discarded():
    abc = ABCFile(); from py_swf.avm2 import MethodInfo, MethodBodyInfo
    abc.methods = [MethodInfo()]
    body = MethodBodyInfo(); body.local_count = 2
    body.code = assemble_instructions(abc.constant_pool, 'getlocal_1\nconvert_d\npop\nfindpropstrict trace\npushstring "after"\ncallpropvoid trace 1\nreturnvoid')
    source, error = decompile_method(abc, body)
    assert error is None
    code, rebuilt, rebuilt_body = compile_parameter_body(source)
    def execute(code, abc, body):
        events = []
        class Numeric:
            def __float__(self):
                events.append("convert"); return 7.0
        run_bytes(code, "as3", abc=abc, body=body, arguments=(Numeric(),), variables={"trace": lambda value: events.append(value)})
        return events
    assert execute(body.code, abc, body) == execute(code, rebuilt, rebuilt_body) == ["convert", "after"]


@pytest.mark.parametrize("opcode, expected", [("convert_i", -1), ("convert_u", 2**32-1), ("convert_b", True)])
def test_numeric_and_boolean_intrinsics_roundtrip_without_named_function_binding(opcode, expected):
    abc = ABCFile(); from py_swf.avm2 import MethodInfo, MethodBodyInfo
    abc.methods = [MethodInfo()]
    body = MethodBodyInfo(); body.local_count = 2
    body.code = assemble_instructions(abc.constant_pool, "getlocal_1\n" + opcode + "\nreturnvalue")
    source, error = decompile_method(abc, body)
    assert error is None, error
    assert all(word not in source for word in ("int(", "uint(", "Boolean("))
    code, rebuilt, rebuilt_body = compile_parameter_body(source)
    assert run_bytes(code, "as3", abc=rebuilt, body=rebuilt_body, arguments=(-1,)).value == expected


def test_instance_scope_appends_class_object_before_activation_capture():
    source = 'public class Main {private function make(local1:int):Function {var base:int=local1;return function(local1:int):int{return base+local1;};}}'
    abc = compile_class_program(source).abc
    body = method_for(abc, 0, "make")
    assert body.init_scope_depth == 3
    closure = next(b for b in abc.method_bodies if b.method not in (body.method, abc.instances[0].iinit, abc.classes[0].cinit, abc.scripts[0].init))
    assert any(i.name == "getouterscope" and i.args == (4,) for i in decode_avm2(closure.code))
    result = run_bytes(body.code, "as3", abc=abc, body=body, arguments=(6,), captured=tuple({} for _ in range(3)))
    assert result.value(4) == 10
