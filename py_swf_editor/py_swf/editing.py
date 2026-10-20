"""Pure-Python script edits, built on copies and validated before commit."""

from dataclasses import asdict

from .avm1 import assemble_avm1, disassemble_avm1
from .avm2 import ABCFile, assemble_instructions, resolve_multiname
from .bytecode_validation import analyze_method, validate_avm1, decode_avm2
from .compiler import compile_actionscript
from .swf_parser import SWFTag
from .symbols import MethodBindings


SCRIPT_TAGS = {12, 59, 72, 82}


def source_edit_reason(abc, body, *, symbol_index=None):
    method = abc.methods[body.method]
    if body.traits or method.flags & 0x02:
        return "Existing activation closures require exact P-code or a verified full-program source; method replacement remains protected"
    if (any(instance.iinit == body.method for instance in abc.instances)
            or any(script.init == body.method for script in abc.scripts)
            or any(cls.cinit == body.method for cls in abc.classes)):
        return "Constructors and initializers require exact P-code to preserve class initialization"
    names = {"getsuper", "setsuper", "getlex", "finddef", "findpropstrict", "findproperty", "getproperty", "setproperty", "initproperty", "deleteproperty", "callproperty", "callproplex", "callpropvoid", "callsuper", "callsupervoid", "constructprop", "getdescendants"}
    bindings = MethodBindings(abc, body, symbol_index=symbol_index)
    parameter_names = {abc.constant_pool.strings[index] for index in method.param_names if 0 < index < len(abc.constant_pool.strings)}
    for type_index in [method.return_type, *method.param_types]:
        type_name = resolve_multiname(abc.constant_pool, type_index)
        if type_name not in ("*", "void") and (bindings.type_source_shadowed(type_name) or type_name in parameter_names):
            return "Declared type source is shadowed by a member or parameter; exact P-code or a proven type alias is required"
    try:
        for ins in decode_avm2(body.code):
            if ins.name not in names:
                continue
            multiname = abc.constant_pool.multinames[ins.args[0]]
            if multiname and multiname["kind"]==0x1B and ins.name in ("getproperty","setproperty"):
                pool=abc.constant_pool
                public_ns=next((i for i,n in enumerate(pool.namespaces) if n and n["kind"]==0x16 and n["name"]==0),None)
                indices=pool.ns_sets[multiname["ns_set"]]
                if (bindings.owner is not None or public_ns is None or indices!=[public_ns]
                        or any(n and (n["kind"]!=0x16 or n["name"]!=0) for n in pool.namespaces[1:])
                        or any(s and s!=[public_ns] for s in pool.ns_sets[1:])):
                    return "Runtime class/private/open namespace bindings require exact P-code"
                continue
            if not multiname or multiname["kind"] not in (7, 9):
                return "Runtime multinames require exact P-code"
            indices = [multiname["ns"]] if "ns" in multiname else abc.constant_pool.ns_sets[multiname["ns_set"]]
            for index in indices:
                namespace = abc.constant_pool.namespaces[index]
                if namespace["kind"] == 5 and multiname["kind"] == 7:
                    bindings.require_private(ins.args[0])
                    continue
                if namespace["kind"] in (0x17, 0x18, 0x1A) and multiname["kind"] == 7:
                    bindings.require_owned(ins.args[0], inherited=ins.name in ("getsuper", "setsuper", "callsuper", "callsupervoid"))
                    continue
                if namespace["kind"] == 8 and multiname["kind"] == 7:
                    if bindings.namespace_alias(bindings.identity(ins.args[0])) is None:
                        return "Explicit namespace has no exact declared alias; use P-code"
                    continue
                if namespace["kind"] != 0x16 or namespace["name"] != 0:
                    return "Non-default/private namespaces require exact P-code to preserve binding"
    except (ValueError, IndexError, KeyError) as error:
        return f"Original bytecode requires exact P-code: {error}"
    return None


def edit_script(tag, text, body_index=0, source=False, *, swf_version=None):
    """Return a replacement tag and diagnostics without mutating the input."""
    if tag.tag_type not in SCRIPT_TAGS:
        raise ValueError("Not a script tag")
    if tag.tag_type in (12, 59):
        if body_index != 0:
            raise ValueError("AVM1 script has only body index 0")
        if tag.tag_type == 59 and len(tag.data) < 2:
            raise ValueError("Truncated DoInitAction sprite id")
        offset = 2 if tag.tag_type == 59 else 0
        asm = compile_actionscript(text, target="as2") if source else text
        code = assemble_avm1(asm)
        validate_avm1(code)
        listing = [line.strip() for line in disassemble_avm1(code).splitlines()]
        minimum = 7 if any(line.startswith(("try ", "define_function2 ")) for line in listing) else 6 if any(line.startswith("enumerate2") for line in listing) else 5 if any(line.startswith("define_function ") for line in listing) else 1
        if swf_version is not None and swf_version < minimum:
            feature = "ActionTry" if any(line.startswith("try ") for line in listing) else "ActionDefineFunction2" if minimum == 7 else "ActionEnumerate2" if minimum == 6 else "ActionDefineFunction"
            raise ValueError(f"{feature} requires SWF version {minimum} or later; current version is {swf_version}. The original header was not changed")
        return SWFTag(tag.tag_type, tag.data[:offset] + code), {"engine": "native", "kind": "avm1", "body_index": 0, "bytes": len(code), "minimum_swf_version": minimum}
    parsed = tag.parse_doabc()
    if parsed is None:
        raise ValueError("Malformed ABC tag")
    flags, name, data = parsed
    abc = ABCFile()
    abc.parse(data, strict=True)
    if not 0 <= body_index < len(abc.method_bodies):
        raise ValueError("Method body index out of range")
    body = abc.method_bodies[body_index]
    if not 0 <= body.method < len(abc.methods):
        raise ValueError("Method definition not found")
    method = abc.methods[body.method]
    if source:
        reason = source_edit_reason(abc, body)
        if reason:
            raise ValueError(reason)
        registers = {f"local{index}": index for index in range(1, body.local_count)}
        register_types = {}
        for index, type_index in enumerate(method.param_types, start=1):
            type_name = resolve_multiname(abc.constant_pool, type_index)
            if type_name in ("int", "uint", "Number", "String", "Boolean", "Object"):
                register_types[f"local{index}"] = type_name
        for index, name_index in enumerate(method.param_names, start=1):
            if 0 < name_index < len(abc.constant_pool.strings):
                registers[abc.constant_pool.strings[name_index]] = index
                if f"local{index}" in register_types:
                    register_types[abc.constant_pool.strings[name_index]] = register_types[f"local{index}"]
        return_type = resolve_multiname(abc.constant_pool, method.return_type)
        # A numeric pool reference preserves non-public return namespaces.
        if return_type not in ("*", "void", "int", "uint", "Number", "String", "Boolean"):
            return_type = f"#{method.return_type}"
        constructor = any(instance.iinit == body.method for instance in abc.instances)
        asm = compile_actionscript(text, target="as3", registers=registers,
                                   next_register=max(body.local_count, len(method.param_types) + 1),
                                   return_type=return_type, constructor=constructor, register_types=register_types, bindings=MethodBindings(abc, body),
                                   abc=abc, method_index=body.method, init_scope_depth=body.init_scope_depth)
    else:
        asm = text
    previous = body.code
    has_regions = any(line.strip().startswith(".exception ") for line in asm.splitlines())
    exceptions = [] if source or has_regions else None
    has_activation = any(line.strip().startswith(".activation ") for line in asm.splitlines())
    traits = [] if source or has_activation else None
    body.code = assemble_instructions(abc.constant_pool, asm, exception_table=exceptions, activation_traits=traits)
    if traits is not None:
        body.traits = traits
        if traits:
            method.flags |= 2
    if source or has_regions:
        body.exceptions = exceptions
    elif body.exceptions and body.code != previous:
        raise ValueError("Changed P-code has an exception table. Offset remapping is not supported yet; preserve its bytes")
    limits = analyze_method(abc, body)
    body.max_stack = limits.max_stack
    body.local_count = limits.local_count
    body.max_scope_depth = limits.max_scope_depth
    serialized = abc.serialize()
    # Parse and analyze again after serialization. A valid in-memory ABC must
    # also survive the format boundary before it reaches the session history.
    reopened = ABCFile()
    reopened.parse(serialized)
    analyze_method(reopened, reopened.method_bodies[body_index])
    payload = serialized if tag.tag_type == 72 else flags.to_bytes(4, "little") + name.encode("utf-8") + b"\0" + serialized
    return SWFTag(tag.tag_type, payload), {"engine": "native", "kind": "avm2", "body_index": body_index, "exception_count": len(body.exceptions), **asdict(limits)}
