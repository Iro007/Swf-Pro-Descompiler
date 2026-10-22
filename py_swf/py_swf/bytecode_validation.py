"""Native bytecode validation at the editing boundary.

AVM2 validation checks instruction boundaries, branch/exception destinations,
pool references and stack/scope heights across control-flow joins. It computes
method limits; it is not a complete AVM2 type verifier or Flash interpreter.
"""

from collections import deque
from dataclasses import dataclass

from .avm2 import ByteReader
from .avm2_opcodes import OPCODES
from .avm1 import AVM1_OPCODES, BLOCK_ACTIONS


class BytecodeValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Instruction:
    offset: int
    end: int
    name: str
    args: tuple


@dataclass(frozen=True)
class MethodLimits:
    max_stack: int
    local_count: int
    max_scope_depth: int
    instruction_count: int


def decode_avm2(code):
    reader, instructions = ByteReader(code), []
    while reader.offset < len(code):
        offset = reader.offset
        try:
            opcode = reader.read_byte()
            if opcode not in OPCODES:
                raise BytecodeValidationError(f"Unknown AVM2 opcode 0x{opcode:02x} at offset {offset}")
            name, kinds = OPCODES[opcode]
            args = []
            if name == "lookupswitch":
                default, count = reader.read_s24(), reader.read_u30()
                if count > (len(code) - reader.offset) // 3 - 1:
                    raise BytecodeValidationError(f"Truncated switch at offset {offset}")
                args = [default, count, tuple(reader.read_s24() for _ in range(count + 1))]
            else:
                for kind in kinds:
                    value = reader.read_byte() if kind == "byte" else reader.read_s24() if kind == "s24" else reader.read_u30()
                    if kind == "u30" and value >= 2 ** 30:
                        raise BytecodeValidationError(f"Invalid u30 operand at offset {offset}")
                    args.append(value)
            instructions.append(Instruction(offset, reader.offset, name, tuple(args)))
        except (EOFError, IndexError) as error:
            raise BytecodeValidationError(f"Truncated AVM2 instruction at offset {offset}") from error
    if not instructions:
        raise BytecodeValidationError("Method bytecode cannot be empty")
    return instructions


_BRANCH_TWO = {"ifeq", "ifne", "iflt", "ifle", "ifgt", "ifge", "ifstricteq", "ifstrictne", "ifnlt", "ifnle", "ifngt", "ifnge"}
_BINARY = {"add", "subtract", "multiply", "divide", "modulo", "lshift", "rshift", "urshift", "bitand", "bitor", "bitxor", "equals", "strictequals", "lessthan", "lessequals", "greaterthan", "greaterequals", "instanceof", "in", "add_i", "subtract_i", "multiply_i", "nextname", "nextvalue", "hasnext", "istypelate", "astypelate"}
_UNARY = {"convert_s", "convert_i", "convert_u", "convert_d", "convert_b", "convert_o", "coerce_a", "coerce_b", "coerce_i", "coerce_u", "coerce_d", "coerce_s", "coerce_o", "checkfilter", "esc_xelem", "esc_xattr", "negate", "negate_i", "increment", "increment_i", "decrement", "decrement_i", "typeof", "not", "bitnot", "sxi1", "sxi8", "sxi16", "li8", "li16", "li32", "lf32", "lf64"}
_PUSH = {"pushbyte", "pushshort", "pushstring", "pushint", "pushuint", "pushdouble", "pushtrue", "pushfalse", "pushnull", "pushundefined", "pushnan", "pushnamespace", "getlocal", "getlocal_0", "getlocal_1", "getlocal_2", "getlocal_3", "getglobalscope", "getscopeobject", "newactivation", "newfunction", "newcatch", "getglobalslot", "hasnext2"}
_NEUTRAL = {"nop", "label", "bkpt", "bkptline", "debug", "debugline", "debugfile", "timestamp", "kill", "inclocal", "declocal", "inclocal_i", "declocal_i", "dxns", "jump", "popscope", "returnvoid"}
_MULTINAME = {"getsuper", "setsuper", "getlex", "finddef", "findpropstrict", "findproperty", "getproperty", "setproperty", "initproperty", "deleteproperty", "coerce", "astype", "istype", "callproperty", "callproplex", "callpropvoid", "callsuper", "callsupervoid", "constructprop", "getdescendants"}


def _check_index(values, index, label, offset):
    if not 0 <= index < len(values):
        raise BytecodeValidationError(f"Invalid {label} index {index} at offset {offset}")


def _runtime_name(pool, index):
    if index == 0:
        return 0
    item = pool.multinames[index]
    if not item:
        raise BytecodeValidationError(f"Invalid multiname index {index}")
    kind = item["kind"]
    return 2 if kind in (0x11, 0x12) else 1 if kind in (0x0f, 0x10, 0x1b, 0x1c) else 0


def _effect(ins, pool):
    name, args = ins.name, ins.args
    if name == "getouterscope":
        return 0, 1
    dynamic = _runtime_name(pool, args[0]) if name in _MULTINAME else 0
    if name in _PUSH:
        return 0, 1
    if name in _NEUTRAL:
        return 0, 0
    if name in _UNARY or name in ("getslot", "newclass"):
        return 1, 1
    if name in ("coerce", "astype", "istype"):
        return 1, 1
    if name in _BINARY:
        return 2, 1
    if name == "dup":
        return 1, 2
    if name == "swap":
        return 2, 2
    if name in ("pop", "returnvalue", "throw", "pushscope", "pushwith", "iftrue", "iffalse", "lookupswitch", "dxnslate", "setglobalslot") or name.startswith("setlocal"):
        return 1, 0
    if name in _BRANCH_TWO:
        return 2, 0
    if name in ("si8", "si16", "si32", "sf32", "sf64", "setslot"):
        return 2, 0
    if name in ("getlex", "finddef", "findpropstrict", "findproperty"):
        return dynamic, 1
    if name in ("getproperty", "getsuper", "getdescendants", "deleteproperty"):
        return 1 + dynamic, 1
    if name in ("setproperty", "initproperty", "setsuper"):
        return 2 + dynamic, 0
    if name in ("callproperty", "callproplex", "callsuper", "constructprop", "callpropvoid", "callsupervoid"):
        return 1 + dynamic + args[1], 0 if name.endswith("void") else 1
    if name in ("callmethod", "callstatic"):
        return 1 + args[1], 1
    if name == "call":
        return args[0] + 2, 1
    if name == "construct":
        return args[0] + 1, 1
    if name == "constructsuper":
        return args[0] + 1, 0
    if name in ("applytype", "newarray", "newobject"):
        return args[0] + 1 if name == "applytype" else args[0] * (2 if name == "newobject" else 1), 1
    raise BytecodeValidationError(f"Validation not implemented for {name}; keep original bytecode")


def analyze_method(abc, body):
    instructions = decode_avm2(body.code)
    by_offset = {ins.offset: ins for ins in instructions}
    pool = abc.constant_pool
    _check_index(abc.methods, body.method, "method", 0)
    method = abc.methods[body.method]
    locals_needed = max(body.local_count, len(method.param_types) + 1 + bool(method.flags & (0x01 | 0x04)))
    successors = {}
    for ins in instructions:
        name, args = ins.name, ins.args
        if name in _MULTINAME:
            _check_index(pool.multinames, args[0], "multiname", ins.offset)
        for mnemonic, values in (("pushstring", pool.strings), ("pushint", pool.integers), ("pushuint", pool.uintegers), ("pushdouble", pool.doubles), ("pushnamespace", pool.namespaces), ("dxns", pool.strings), ("debugfile", pool.strings)):
            if name == mnemonic:
                _check_index(values, args[0], mnemonic, ins.offset)
        if name in ("newfunction", "callstatic"):
            _check_index(abc.methods, args[0], "method", ins.offset)
        if name == "newclass":
            _check_index(abc.classes, args[0], "class", ins.offset)
        if name == "newcatch":
            _check_index(body.exceptions, args[0], "exception", ins.offset)
        local_indices = args if name == "hasnext2" else args[:1] if name in ("getlocal", "setlocal", "kill", "inclocal", "declocal", "inclocal_i", "declocal_i") else (int(name[-1]),) if name.startswith(("getlocal_", "setlocal_")) else ()
        if local_indices:
            locals_needed = max(locals_needed, max(local_indices) + 1)
            if locals_needed > 65536:
                raise BytecodeValidationError("Local register limit exceeded")
        if name == "lookupswitch":
            targets = [ins.offset + args[0], *(ins.offset + value for value in args[2])]
        elif name == "jump":
            targets = [ins.end + args[0]]
        elif name in _BRANCH_TWO or name in ("iftrue", "iffalse"):
            targets = [ins.end + args[0], ins.end]
        elif name in ("returnvoid", "returnvalue", "throw"):
            targets = []
        else:
            targets = [ins.end]
        # Haxe emits unreachable jumps to the EOF sentinel after returnvalue.
        # EOF is rejected below whenever reachable, including handler entries;
        # every other target must remain an instruction boundary even in dead code.
        for target in targets:
            if target not in by_offset and target != len(body.code):
                raise BytecodeValidationError(f"Invalid branch target {target} at offset {ins.offset}")
        successors[ins.offset] = targets
        _effect(ins, pool)  # Verify even unreachable instruction schemas.
    states = {0: (0, body.init_scope_depth)}
    pending = deque([0])
    for exception in body.exceptions:
        _check_index(pool.multinames, exception.exc_type, "exception type", exception.target_offset)
        _check_index(pool.multinames, exception.var_name, "exception variable", exception.target_offset)
        if exception.from_offset not in by_offset or exception.to_offset not in (*by_offset, len(body.code)) or exception.from_offset >= exception.to_offset or exception.target_offset not in by_offset:
            raise BytecodeValidationError("Invalid exception range/target")
        state = (1, body.init_scope_depth)
        if exception.target_offset in states and states[exception.target_offset] != state:
            raise BytecodeValidationError("Conflicting exception handler stack")
        states[exception.target_offset] = state
        pending.append(exception.target_offset)
    max_stack, max_scope = 0, body.init_scope_depth
    while pending:
        offset = pending.popleft()
        if offset == len(body.code):
            raise BytecodeValidationError("Reachable method falls through without return/throw")
        ins = by_offset[offset]
        stack, scope = states[offset]
        pop, push = _effect(ins, pool)
        if stack < pop:
            raise BytecodeValidationError(f"Stack underflow at offset {offset}: {ins.name}")
        if ins.name == "getscopeobject" and ins.args[0] >= scope:
            raise BytecodeValidationError(f"Invalid scope index at offset {offset}")
        if ins.name == "getouterscope" and ins.args[0] >= body.init_scope_depth:
            raise BytecodeValidationError(f"Invalid outer scope index at offset {offset}")
        if ins.name in ("pushscope", "pushwith"):
            scope += 1
        elif ins.name == "popscope":
            if scope <= body.init_scope_depth:
                raise BytecodeValidationError(f"Scope underflow at offset {offset}")
            scope -= 1
        new_stack = stack - pop + push
        max_stack, max_scope = max(max_stack, stack, new_stack), max(max_scope, scope)
        state = (new_stack, scope)
        for target in successors[offset]:
            if target in states:
                if states[target] != state:
                    raise BytecodeValidationError(f"Incompatible stack/scope heights at join {target}")
            else:
                states[target] = state
                pending.append(target)
    return MethodLimits(max_stack, locals_needed, max_scope, len(instructions))


def validate_avm1(data):
    """Check record boundaries and branch/block destinations recursively."""
    from .avm1 import parse_try_header
    def parse(code, depth=0, inline_try=False):
        if depth > 32:
            raise BytecodeValidationError("Nested AVM1 block limit exceeded")
        if not code:
            raise BytecodeValidationError("AVM1 action stream cannot be empty")
        offset, boundaries, targets = 0, {0}, []
        while offset < len(code):
            start = offset
            opcode = code[offset]
            offset += 1
            if opcode == 0:
                if offset != len(code):
                    raise BytecodeValidationError("Data after AVM1 End action")
                break
            if opcode not in AVM1_OPCODES:
                raise BytecodeValidationError(f"Unknown AVM1 action at offset {start}")
            payload = b""
            if opcode >= 0x80:
                if offset + 2 > len(code):
                    raise BytecodeValidationError("Truncated AVM1 action length")
                size = int.from_bytes(code[offset:offset + 2], "little")
                offset += 2
                payload = code[offset:offset + size]
                if len(payload) != size:
                    raise BytecodeValidationError("Truncated AVM1 action payload")
                offset += size
            if opcode == 0x9E and payload:
                raise BytecodeValidationError("AVM1 Call action must have an empty payload")
            if opcode in (0x99, 0x9d):
                if len(payload) != 2:
                    raise BytecodeValidationError("Invalid AVM1 branch operand")
                targets.append(offset + int.from_bytes(payload, "little", signed=True))
            if opcode in BLOCK_ACTIONS:
                if len(payload) < 2:
                    raise BytecodeValidationError("Truncated AVM1 block header")
                length = int.from_bytes(payload[-2:], "little")
                block = code[offset:offset + length]
                if len(block) != length:
                    raise BytecodeValidationError("Truncated AVM1 block")
                if block:
                    parse(block, depth + 1)
                offset += length
            elif opcode == 0x8F:
                try:
                    _, sizes, _ = parse_try_header(payload)
                except (ValueError, UnicodeError) as error:
                    raise BytecodeValidationError(str(error)) from error
                for length in sizes:
                    block_start = offset
                    block = code[offset:offset + length]
                    if len(block) != length:
                        raise BytecodeValidationError("Truncated AVM1 Try block")
                    if block:
                        nested_boundaries, nested_targets = parse(block, depth + 1, inline_try=True)
                        boundaries.update(block_start + point for point in nested_boundaries)
                        targets.extend(block_start + target for target in nested_targets)
                    offset += length
            boundaries.add(offset)
        # Try streams may jump out to their enclosing action stream (e.g.
        # skipping catch on normal completion). Resolve at the enclosing level,
        # never across a function/with boundary.
        if not inline_try and any(target not in boundaries for target in targets):
            raise BytecodeValidationError("AVM1 branch target is not an action boundary")
        return boundaries, targets
    parse(data)
