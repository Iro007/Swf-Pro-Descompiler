"""Small real-format SWFs for native edit/save/reopen checks."""

from py_swf.avm2 import ABCFile, MethodInfo, MethodBodyInfo, ScriptInfo, TraitsInfo, assemble_instructions, search_or_add_multiname, search_or_add_string
from py_swf.bytecode_validation import analyze_method
from py_swf.swf_parser import SWFFile, SWFTag


def as3_tag(tag_type=82):
    abc = ABCFile()
    init = MethodInfo()
    init.return_type = search_or_add_multiname(abc.constant_pool, "void")
    work = MethodInfo()
    work.name = search_or_add_string(abc.constant_pool, "work")
    work.param_types = [search_or_add_multiname(abc.constant_pool, "int")]
    work.param_names = [search_or_add_string(abc.constant_pool, "score")]
    work.flags = 0x80  # HAS_PARAM_NAMES
    abc.methods = [init, work]
    script = ScriptInfo()
    script.init = 0
    trait = TraitsInfo()
    trait.name = search_or_add_multiname(abc.constant_pool, "work")
    trait.kind_flags, trait.slot_id, trait.function = 5, 1, 1
    script.traits = [trait]
    abc.scripts = [script]
    for index, code in enumerate(("getlocal_0\npushscope\nreturnvoid", 'pushstring "original"\nreturnvalue')):
        body = MethodBodyInfo()
        body.method = index
        body.local_count = 1 if index == 0 else 2
        body.code = assemble_instructions(abc.constant_pool, code)
        abc.method_bodies.append(body)
        limits = analyze_method(abc, body)
        body.max_stack, body.max_scope_depth = limits.max_stack, limits.max_scope_depth
    data = abc.serialize()
    return SWFTag(tag_type, data if tag_type == 72 else b"\x00\x00\x00\x00native\x00" + data)


def swf_with(tags, signature="FWS"):
    swf = SWFFile()
    swf.signature, swf.version = signature, 15
    swf.tags = list(tags) + [SWFTag(1, b"")]
    return swf.save_bytes()
