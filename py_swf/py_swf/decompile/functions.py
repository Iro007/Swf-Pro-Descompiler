"""Inline actions and ABC closures, reconstructed from bytecode only.

Captured slots keep their lexical bindings, not snapshots of their values.
Recursive/malicious function graphs have a shared depth and work budget.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..avm2 import resolve_multiname
from ..symbols import source_identifier
from .ir import ReconstructionError, Value, name


@dataclass
class FunctionContext:
    active: list = field(default_factory=list)
    remaining: int = 200000

    @contextmanager
    def enter(self, key, size):
        if key in self.active or len(self.active) >= 32:
            raise ReconstructionError("Recursive/nested function reconstruction limit exceeded")
        self.remaining -= size
        if self.remaining < 0:
            raise ReconstructionError("Nested function reconstruction work budget exceeded")
        self.active.append(key)
        try:
            yield
        finally:
            self.active.pop()


def context_for(lifter):
    if lifter.context is None:
        lifter.context = FunctionContext()
        if lifter.body:
            lifter.context.active.append(lifter.body.method)
    return lifter.context


def indent(text):
    return "\n".join("    " + line for line in text.splitlines())


def action_header(payload, version2=False, *, details=False):
    offset = 0
    def string():
        nonlocal offset
        end = payload.find(b"\0", offset)
        if end < 0:
            raise ReconstructionError("Unterminated function name/parameter")
        value = payload[offset:end].decode("utf-8")
        offset = end + 1
        return value
    def integer(size):
        nonlocal offset
        if offset + size > len(payload):
            raise ReconstructionError("Truncated function header")
        value = int.from_bytes(payload[offset:offset + size], "little")
        offset += size
        return value
    function_name = string()
    count = integer(2)
    registers, flags = (integer(1), integer(2)) if version2 else (0, 0)
    params = []
    for _ in range(count):
        reg = integer(1) if version2 else 0
        params.append((reg, string()))
    integer(2)
    if offset != len(payload):
        raise ReconstructionError("Unexpected bytes in function header")
    if function_name and not source_identifier(function_name) or any(not source_identifier(p) for _, p in params):
        raise ReconstructionError("Function/parameter name cannot be represented as source")
    if len({p for _, p in params}) != len(params):
        raise ReconstructionError("Duplicate function parameters need P-code")
    mapping = {}
    if flags & ~0x1FF:
        raise ReconstructionError("Reserved DefineFunction2 flags require P-code")
    reg = 1
    for flag, text in ((1, "this"), (4, "arguments"), (16, "super"), (64, "_root"), (128, "_parent"), (256, "_global")):
        if flags & flag:
            mapping[reg] = text
            reg += 1
    if reg - 1 > registers:
        raise ReconstructionError("Function preloads exceed its declared register count")
    for index, text in params:
        if index:
            if index in mapping or index > registers:
                raise ReconstructionError("Conflicting/out-of-range function register binding")
            mapping[index] = text
    result = (function_name, [p for _, p in params], mapping)
    return (*result, registers, flags) if details else result


def action_container(ins, parent):
    from .lift import Lifter, avm1_instructions
    from .native import reconstruct
    from .regions import as2_regions
    ctx = context_for(parent)
    child = Lifter("as2", pool=parent.pool, context=ctx)
    function_name, params, mapping = "", [], {}
    if ins.op != "with":
        function_name, params, mapping, registers, flags = action_header(ins.args[0], ins.op == "define_function2", details=True)
        child.register_limit = registers if ins.op == "define_function2" else None
        child.suppressed_variables = {text for flag, text in ((2, "this"), (8, "arguments"), (32, "super")) if flags & flag}
        child.parameters.update(params)
        child.register_names.update(mapping)
    else:
        child.parameters.update(parent.parameters)
        child.register_names.update(parent.register_names)
        child.used_names.update(parent.used_names)
    with ctx.enter((id(ins), ins.pc), len(ins.args[1])):
        instructions = avm1_instructions(ins.args[1])
        instructions, regions, seeds = as2_regions(instructions)
        text, reads = reconstruct(instructions, child, regions, seeds, details=True)
    if ins.op == "with":
        parent.pool = child.pool
        parent.register_names.update(child.register_names)
        parent.used_names.update(child.used_names)
        return Value("container", indent(text), tuple(name(r) for r in sorted(reads)), origin=ins.pc), ""
    signature = "function" + (" " + function_name if function_name else "") + "(" + ", ".join(params) + ")"
    captures = reads - set(params) - set(mapping.values())
    return Value("function", signature + " {\n" + indent(text) + "\n}", tuple(name(r) for r in sorted(captures)), origin=ins.pc), function_name


def abc_function(index, parent, scopes, pc):
    from .lift import Lifter, avm2_instructions
    from .native import decompile_as3
    ctx = context_for(parent)
    if not 0 <= index < len(parent.abc.methods):
        raise ReconstructionError("Invalid newfunction method index")
    body = next((b for b in parent.abc.method_bodies if b.method == index), None)
    if body is None:
        raise ReconstructionError("Missing newfunction body")
    captured = parent.captured or tuple(Value("unknown_scope", "") for _ in range(parent.body.init_scope_depth))
    captured = (*captured, *scopes)
    child = Lifter("as3", parent.abc, body, context=ctx, captured=captured)
    child.prepare(avm2_instructions(body.code))
    captured_names = {text for obj in captured if obj.kind == "activation" for _, _, text, _ in obj.literal or ()}
    all_captured = [text for obj in captured if obj.kind == "activation" for _, _, text, _ in obj.literal or ()]
    if len(set(all_captured)) != len(all_captured):
        raise ReconstructionError("Shadowed outer activation slots need hygienic aliases; use P-code")
    if captured_names & set(child.local_names.values()):
        raise ReconstructionError("Shadowed captured binding needs a hygienic alias; use P-code")
    method = parent.abc.methods[index]
    if method.flags & (1 | 4 | 8):
        raise ReconstructionError("arguments/rest/default parameters need signature-aware reconstruction")
    params = []
    for reg, typ in enumerate(method.param_types, 1):
        pname = child.local(reg).text
        annotation = resolve_multiname(parent.pool, typ).replace("::", ".")
        params.append(pname + (":" + annotation if annotation != "*" else ""))
    with ctx.enter(index, len(body.code)):
        text, reads = decompile_as3(parent.abc, body, context=ctx, captured=captured, details=True)
    result_type = resolve_multiname(parent.pool, method.return_type).replace("::", ".")
    captures = reads - {child.local(i).text for i in range(1, len(method.param_types) + 1)}
    signature = "function(" + ", ".join(params) + "):" + result_type
    return Value("function", signature + " {\n" + indent(text) + "\n}", tuple(name(r) for r in sorted(captures)), origin=pc)
