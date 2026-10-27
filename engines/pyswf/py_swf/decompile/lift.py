"""Strict bytecode-to-IR lifting. Unknown operations never become silent comments."""
from collections import deque
from dataclasses import dataclass
import math

from ..avm1 import AVM1_OPCODES, BLOCK_ACTIONS, parse_push_values, parse_try_header
from ..avm2 import resolve_multiname
from ..bytecode_validation import decode_avm2
from ..script_strings import parse_string
from ..symbols import MethodBindings, name_identity, source_identifier
from .ir import Block, Instruction, ReconstructionError, Statement, Value, binary, literal, name, negate, render


AVM2_BINARY = {"add": "+", "add_i": "+", "subtract": "-", "subtract_i": "-", "multiply": "*", "multiply_i": "*", "divide": "/", "modulo": "%", "equals": "==", "strictequals": "===", "lessthan": "<", "lessequals": "<=", "greaterthan": ">", "greaterequals": ">=", "bitand": "&", "bitor": "|", "bitxor": "^", "lshift": "<<", "rshift": ">>", "urshift": ">>>"}
AVM1_BINARY = {"add": "+", "add2": "+", "subtract": "-", "multiply": "*", "divide": "/", "modulo": "%", "equals": "==", "equals2": "==", "strict_equals": "===", "less_than": "<", "less2": "<", "greater": ">", "bit_and": "&", "bit_or": "|", "bit_xor": "^"}
COMPARE = {"ifeq": "==", "ifne": "!=", "iflt": "<", "ifle": "<=", "ifgt": ">", "ifge": ">=", "ifstricteq": "===", "ifstrictne": "!==", "ifnlt": "<", "ifnle": "<=", "ifngt": ">", "ifnge": ">="}


def avm2_instructions(code):
    result = []
    for ins in decode_avm2(code):
        op, args = ins.name, ins.args
        if op == "lookupswitch":
            op, args = "lookup", (ins.offset + args[0], tuple(ins.offset + v for v in args[2]))
        elif op == "jump":
            args = (ins.end + args[0],)
        elif op in COMPARE or op in ("iftrue", "iffalse"):
            op, args = "branch", (op, ins.end + args[0])
        elif op in ("returnvalue", "returnvoid"):
            op, args = "return", (op == "returnvalue",)
        result.append(Instruction(ins.offset, ins.end, op, tuple(args)))
    return result


def avm1_instructions(data):
    result, offset = [], 0
    while offset < len(data):
        pc, opcode = offset, data[offset]
        offset += 1
        payload = b""
        if opcode >= 0x80:
            if offset + 2 > len(data):
                raise ReconstructionError("Truncated action header")
            size = int.from_bytes(data[offset:offset + 2], "little")
            offset += 2
            payload = data[offset:offset + size]
            if len(payload) != size:
                raise ReconstructionError("Truncated action payload")
            offset += size
        op = "end" if opcode == 0 else AVM1_OPCODES.get(opcode)
        if op is None:
            raise ReconstructionError(f"Unknown action 0x{opcode:02x} at {pc}")
        args = (payload,)
        if op in ("jump", "if"):
            if len(payload) != 2:
                raise ReconstructionError("Truncated action branch")
            target = offset + int.from_bytes(payload, "little", signed=True)
            op, args = ("jump", (target,)) if op == "jump" else ("branch", ("iftrue", target))
        elif op == "try":
            flags, sizes, caught = parse_try_header(payload)
            try_end = offset + sizes[0]
            catch_end = try_end + sizes[1]
            args = (flags, caught, offset, try_end, catch_end, catch_end + sizes[2])
        elif op == "return":
            args = (True,)
        if opcode in BLOCK_ACTIONS:
            if len(payload) < 2:
                raise ReconstructionError("Truncated inline action container")
            size = int.from_bytes(payload[-2:], "little")
            nested = data[offset:offset + size]
            if len(nested) != size:
                raise ReconstructionError("Truncated inline action body")
            offset += size
            args = (payload, nested)
        result.append(Instruction(pc, offset, op, tuple(args)))
    return result


@dataclass(frozen=True)
class State:
    stack: tuple = ()
    scopes: tuple = ()
    objects: tuple = ()
    constant_pool: tuple | None = None


class Lifter:
    def __init__(self, target, abc=None, body=None, pool=None, context=None, captured=()):
        self.target, self.abc, self.body = target, abc, body
        self.pool = abc.constant_pool if abc else list(pool or [])
        self.bindings = MethodBindings(abc, body, symbol_index=getattr(abc, "_native_symbol_index", None)) if abc and body else None
        self.local_names, self.special = {}, {}
        self.local_types, self.parameters = {}, set()
        self.counter = 0
        self.seed_objects = {}
        self.name_aliases = []
        self.used_names, self.register_names = set(), {}
        self.ignored = set()
        self.seed_scopes = ()
        self.context = context
        self.initial_pool = tuple(self.pool) if target == "as2" else None
        self.register_limit = None
        self.suppressed_variables = set()
        self.preloaded_initializers = []
        self.captured = tuple(captured)
        if abc and body and body.method < len(abc.methods):
            method = abc.methods[body.method]
            for i, typ in enumerate(method.param_types, 1):
                pname = self.pool.strings[method.param_names[i - 1]] if i <= len(method.param_names) else f"local{i}"
                self.local_names[i] = pname
                self.parameters.add(pname)
                self.local_types[pname] = resolve_multiname(self.pool, typ)
        self.activation_slots = {t.slot_id: (resolve_multiname(self.pool, t.name).split("::")[-1], resolve_multiname(self.pool, t.type_name)) for t in getattr(body, "traits", []) if t.kind_flags & 15 in (0, 6)}
        self.activation = Value("activation", "", literal=tuple((t.slot_id, t.name, *self.activation_slots[t.slot_id]) for t in getattr(body, "traits", []) if t.kind_flags & 15 in (0, 6)))
        for obj in self.captured:
            if obj.kind == "activation":
                for _, _, text, typ in obj.literal or ():
                    self.local_types.setdefault(text, typ)
                    self.parameters.add(text)  # captured bindings are not local declarations

    def local(self, index, pc=-1):
        receiver = self.bindings.class_name if self.bindings is not None and self.bindings.static and self.bindings.class_name else "this"
        return name(receiver if index == 0 else self.local_names.get(index, f"local{index}"), pc)

    def fresh(self, pc):
        self.counter += 1
        return name(self.unique_name(f"$value{pc}_{self.counter}"), pc)

    def unique_name(self, base):
        candidate, suffix = base, 0
        while candidate in self.used_names:
            suffix += 1
            candidate = f"{base}_{suffix}"
        self.used_names.add(candidate)
        return candidate

    def register(self, index, pc=-1):
        if self.register_limit is not None and not 0 <= index <= self.register_limit:
            raise ReconstructionError("Register reference exceeds DefineFunction2 register count")
        if index not in self.register_names:
            self.register_names[index] = self.unique_name(f"$register{index}")
        return name(self.register_names[index], pc)

    def prepare(self, instructions):
        if sum(i.op == "newactivation" for i in instructions) > 1:
            raise ReconstructionError("Multiple activation identities require separate lexical bindings; use P-code")
        self.used_names.update(self.local_names.values())
        self.used_names.update(self.pool.strings if self.target == "as3" else self.pool)
        if self.target == "as2":
            for ins in instructions:
                if ins.op == "push":
                    self.used_names.update(parse_string(token) for token in parse_push_values(ins.args[0]) if token.startswith('"'))
            def register_writes(stream):
                for instruction in stream:
                    if instruction.op == "store_register" and instruction.args[0]:
                        yield instruction.args[0][0]
                    elif instruction.op == "with":
                        yield from register_writes(avm1_instructions(instruction.args[1]))
            for register in sorted(set(register_writes(instructions))):
                original = self.register_names.get(register)
                if original in ("this", "arguments", "_root", "_parent", "_global"):
                    alias = self.unique_name(f"$preload{register}")
                    self.register_names[register] = alias
                    self.preloaded_initializers.append(Statement("assign", name(original), name(alias), instructions[0].pc, True))
        # Names come from embedded debug metadata, not from the .as fixtures.
        if self.target == "as3":
            for ins in instructions:
                if ins.op == "debug" and ins.args[0] == 1 and 0 < ins.args[1] < len(self.pool.strings):
                    index = ins.args[2] + 1
                    self.local_names.setdefault(index, self.pool.strings[ins.args[1]])
            for i, ins in enumerate(instructions[:40]):
                if ins.op in ("jump", "branch", "lookup"):
                    break
                if ins.op == "newactivation":
                    store = next((j for j in instructions[i + 1:i + 5] if j.op.startswith("setlocal")), None)
                    if store:
                        reg = int(store.op[-1]) if "_" in store.op else store.args[0]
                        self.seed_objects[reg] = self.activation

    def symbol(self, index, *, inherited=False):
        mn = self.pool.multinames[index]
        if not mn or mn["kind"] != 7:
            text = resolve_multiname(self.pool, index)
            if "::" in text:
                namespace, local = text.rsplit("::", 1)
                text = namespace + "." + local if namespace else local
            if not all(source_identifier(part) for part in text.split(".")) and text != "*":
                raise ReconstructionError("Unrepresentable qualified name; use P-code")
            return Value("qualified", text) if "." in text or text == "*" else name(text)
        if mn and mn["kind"] == 7 and self.pool.namespaces[mn["ns"]]["kind"] == 5:
            try:
                identity = self.bindings.require_private(index)
            except ValueError as error:
                raise ReconstructionError(str(error)) from error
            return Value("name", identity.local, binding=identity)
        try:
            identity = name_identity(self.pool, index)
        except (ValueError, KeyError, IndexError) as error:
            raise ReconstructionError("Runtime or unresolved qualified name; use P-code") from error
        if identity.kind != 7 or len(identity.namespaces) != 1:
            raise ReconstructionError("Unrepresentable qualified name; use P-code")
        namespace = identity.namespaces[0]
        if namespace.kind in (0x17, 0x18, 0x1A):
            try:
                owned = self.bindings.require_owned(index, inherited=inherited)
            except ValueError as error:
                # Package-internal types are legal only inside the exact
                # owning package and must have a declared provider.
                from ..abc_symbols import AbcSymbolIndex
                provider = AbcSymbolIndex([self.abc]).resolve_type(self.pool, index, owner_package=self.bindings.owner_package)
                if namespace.kind != 0x17 or provider is None:
                    raise ReconstructionError("Unrepresentable qualified name: " + str(error)) from error
                return Value("qualified", ".".join(filter(None, (namespace.uri, identity.local))), binding=identity)
            return Value("name", owned.local, binding=owned)
        if namespace.kind == 0x16:
            parts = namespace.uri.split(".") if namespace.uri else []
            if any(not source_identifier(part) for part in parts):
                raise ReconstructionError("Unrepresentable qualified name; use P-code")
            if namespace.uri and not source_identifier(identity.local):
                raise ReconstructionError("Unrepresentable qualified name; use P-code")
            text = ".".join((*parts, identity.local)) if parts else identity.local
            return Value("qualified", text, binding=identity) if parts else name(text)
        if namespace.kind == 0x08 and not namespace.uri and source_identifier(identity.local):
            return name(identity.local)
        if namespace.kind == 0x08 and namespace.uri:
            alias = self.bindings.namespace_alias(identity)
            if alias is not None and source_identifier(identity.local):
                return Value("name", alias + "::" + identity.local, binding=identity)
            if namespace.uri == "http://adobe.com/AS3/2006/builtin" and source_identifier(identity.local):
                return Value("name", identity.local, binding=identity)
        raise ReconstructionError("Unrepresentable qualified name; use P-code")

    def lexical_binding(self, index, scopes):
        obj = self.lexical_object(index, scopes)
        if obj:
            _, _, text, _ = next(item for item in obj.literal if name_identity(self.pool, item[1]) == name_identity(self.pool, index))
            return name(text)
        return None

    def lexical_object(self, index, scopes):
        for obj in reversed((*self.captured, *scopes)):
            if obj.kind == "activation":
                for _, multiname, text, _ in obj.literal or ():
                    if name_identity(self.pool, multiname) == name_identity(self.pool, index):
                        return obj
        return None

    def lift(self, block, state=State()):
        if self.target == "as2":
            self.pool = list(self.initial_pool if state.constant_pool is None else state.constant_pool)
        stack, scopes, statements = list(state.stack), list(state.scopes), []
        special = dict(state.objects)
        term, condition = "jump", None
        def pop():
            if not stack:
                raise ReconstructionError(f"Expression stack underflow in block {block.pc}")
            return stack.pop()
        def emit(stmt):
            statements.append(stmt)
        def store(target, value, pc, declared=False, typ=None):
            # Saving an old value across a write is necessary for n++, not
            # just prettier formatting: otherwise later reads observe new n.
            if target.kind == "name":
                for i, old in enumerate(stack):
                    if target.text in old.reads:
                        temp = self.fresh(pc)
                        emit(Statement("assign", old, temp, pc, True))
                        stack[i] = temp
            emit(Statement("assign", value, target, pc, declared, typ))
        for instruction_index, ins in enumerate(block.instructions):
            pc, op, args = ins.pc, ins.op, ins.args
            if pc in self.ignored:
                continue
            if op == "kill":
                special[args[0]] = Value("raw", "undefined", origin=pc)
                continue
            if op in ("nop", "label", "debug", "debugfile", "debugline"):
                continue
            if op == "jump":
                term = "jump"
                continue
            if op == "branch":
                mn = args[0]
                if mn in ("iftrue", "iffalse"):
                    condition = pop()
                    if mn == "iffalse":
                        condition = negate(condition)
                else:
                    right, left = pop(), pop()
                    condition = binary(COMPARE[mn], left, right, pc)
                    if mn.startswith("ifn") and mn != "ifne":
                        condition = negate(condition)
                term = "branch"
                continue
            if op == "lookup":
                condition, term = pop(), "lookup"
                continue
            if op in ("return", "throw", "end"):
                value = pop() if op == "throw" or op == "return" and args[0] else None
                if op == "return" and value is not None and self.target == "as3" and value.kind in ("cast", "primitive_conversion") and value.text in ("int", "uint", "Number", "Boolean"):
                    declared = resolve_multiname(self.pool, self.abc.methods[self.body.method].return_type) if 0 <= self.body.method < len(self.abc.methods) else "*"
                    if declared == value.text:
                        # A declared primitive return performs this same
                        # conversion. String is excluded: convert_s(null)
                        # and a nullable String return have different values.
                        value = value.args[0]
                for discarded in stack:
                    if discarded.effectful:
                        emit(Statement("expr", discarded, pc=pc))
                stack.clear()
                if op != "end":
                    emit(Statement(op, value, pc=pc))
                term = "exit"
                continue
            if op == "try":
                term = "try"
                continue
            if op == "structured_switch":
                term = "switch"
                continue
            if op == "structured_exit":
                emit(Statement(args[0], args[1], pc=pc))
                term = "exit"
                continue
            if op in ("dup", "push_duplicate"):
                if not stack:
                    raise ReconstructionError(f"Empty dup at {pc}")
                if stack[-1].effectful:
                    temp = self.fresh(pc)
                    emit(Statement("assign", stack[-1], temp, pc, True))
                    stack[-1] = temp
                stack.append(stack[-1])
                continue
            if op in ("swap", "stack_swap"):
                right, left = pop(), pop()
                stack.extend((right, left))
                continue
            if op == "pop":
                value = pop()
                if value.effectful:
                    emit(Statement("expr", value, pc=pc))
                continue
            binary_ops = AVM2_BINARY if self.target == "as3" else AVM1_BINARY
            if op in binary_ops:
                right, left = pop(), pop()
                stack.append(binary(binary_ops[op], left, right, pc))
                continue
            if op in ("not", "negate", "negate_i", "bitnot"):
                value = pop()
                stack.append(negate(value) if op == "not" else Value("unary", "~" if op == "bitnot" else "-", (value,), origin=pc))
                continue
            if op in ("increment", "increment_i", "decrement", "decrement_i"):
                stack.append(binary("+" if op.startswith("increment") else "-", pop(), literal(1, pc), pc))
                continue
            if self.target == "as2":
                payload = args[0] if args else b""
                if op in ("define_function", "define_function2", "with"):
                    from .functions import action_container
                    value, function_name = action_container(ins, self)
                    if op == "with" or function_name:
                        if op == "with":
                            obj = pop()
                            value = Value("container", "with (" + render(obj) + ") {\n" + value.text + "\n}", (obj, *value.args), origin=pc)
                        emit(Statement("container", value, pc=pc))
                    else:
                        stack.append(value)
                elif op == "constant_pool":
                    if len(payload) < 2:
                        raise ReconstructionError("Truncated constant pool header")
                    count = int.from_bytes(payload[:2], "little")
                    offset, values = 2, []
                    for _ in range(count):
                        end = payload.find(b"\0", offset)
                        if end < 0:
                            raise ReconstructionError("Malformed constant pool")
                        values.append(payload[offset:end].decode("utf-8"))
                        offset = end + 1
                    if offset != len(payload):
                        raise ReconstructionError("Unexpected bytes after constant pool")
                    self.pool = values
                elif op == "push":
                    for token in parse_push_values(payload):
                        if token.startswith("c:"):
                            index = int(token[2:])
                            if index >= len(self.pool):
                                raise ReconstructionError("Invalid constant index")
                            value = literal(self.pool[index], pc)
                        elif token.startswith("r:"):
                            value = self.register(int(token[2:]), pc)
                        elif token[0:1] == '"':
                            value = literal(parse_string(token), pc)
                        elif token in ("null", "undefined"):
                            value = Value("raw", token, origin=pc)
                        elif token in ("true", "false"):
                            value = literal(token == "true", pc)
                        else:
                            value = literal(float(token) if any(c in token for c in ".eE") or token in ("inf", "-inf", "nan") else int(token), pc)
                        stack.append(value)
                elif op == "get_variable":
                    target = pop()
                    if target.kind != "literal" or not isinstance(target.literal, str):
                        raise ReconstructionError("Dynamic variable lookup needs P-code")
                    text = next((new for old, new, begin, end in self.name_aliases if old == target.literal and begin <= pc < end), target.literal)
                    if text in self.suppressed_variables:
                        raise ReconstructionError("Suppressed function variable has an outer lexical binding; use P-code")
                    stack.append(name(text, min(pc, target.origin) if target.origin >= 0 else pc))
                elif op in ("set_variable", "define_local"):
                    value, target = pop(), pop()
                    if target.kind != "literal" or not isinstance(target.literal, str):
                        raise ReconstructionError("Dynamic variable assignment needs P-code")
                    text = next((new for old, new, begin, end in self.name_aliases if old == target.literal and begin <= pc < end), target.literal)
                    store(name(text, pc), value, pc, op == "define_local")
                elif op == "define_local2":
                    target = pop()
                    store(name(target.literal, pc), Value("raw", "undefined"), pc, True)
                elif op == "store_register":
                    if not stack:
                        raise ReconstructionError("Empty register store")
                    target = self.register(payload[0], pc)
                    if target.text in ("this", "super", "arguments", "_root", "_parent", "_global"):
                        raise ReconstructionError("Preloaded register reassignment needs a hygienic alias; use P-code")
                    store(target, stack[-1], pc, declared=target.text.startswith("$register"))
                    stack[-1] = target
                elif op == "get_member":
                    key, obj = pop(), pop()
                    stack.append(Value("member", "", (obj, key), origin=pc))
                elif op == "set_member":
                    value, key, obj = pop(), pop(), pop()
                    store(Value("member", "", (obj, key), origin=pc), value, pc)
                elif op in ("trace", "stop", "play", "next_frame", "prev_frame", "stop_sounds"):
                    params = (pop(),) if op == "trace" else ()
                    emit(Statement("expr", Value("call", "", (name(op), *params), origin=pc), pc=pc))
                elif op in ("call_function", "call_method"):
                    fn = pop()
                    obj = pop() if op == "call_method" else None
                    count = pop()
                    if count.kind != "literal" or not isinstance(count.literal, (int, float)):
                        raise ReconstructionError("Dynamic argument count")
                    params = tuple(pop() for _ in range(int(count.literal)))
                    # AVM1 arguments are pushed in reverse order, popped in
                    # source order (unlike AVM2). Do NOT reverse them again.
                    callee = name(fn.literal) if fn.kind == "literal" and isinstance(fn.literal, str) else fn
                    if obj is not None:
                        callee = Value("member", "", (obj, fn), origin=pc)
                    stack.append(Value("call", "", (callee, *params), origin=pc))
                elif op in ("to_number", "to_integer", "to_string"):
                    typ = {"to_number": "Number", "to_integer": "int", "to_string": "String"}[op]
                    stack.append(Value("cast", typ, (pop(),), origin=pc))
                elif op == "instance_of":
                    right, left = pop(), pop()
                    stack.append(binary("instanceof", left, right, pc))
                elif op in ("init_array", "init_object"):
                    count = pop()
                    if count.kind != "literal" or type(count.literal) not in (int, float) or not math.isfinite(count.literal) or count.literal != int(count.literal) or not 0 <= count.literal <= len(stack) // (2 if op == "init_object" else 1):
                        raise ReconstructionError("Invalid/dynamic literal container size")
                    size = int(count.literal)
                    width = size * (2 if op == "init_object" else 1)
                    # AVM1 pops array elements in source order, but evaluates
                    # them in reverse push order. Preserve calls/getters first.
                    for i in range(len(stack) - width, len(stack)):
                        if stack[i].effectful:
                            temp = self.fresh(pc)
                            emit(Statement("assign", stack[i], temp, pc, True))
                            stack[i] = temp
                    if op == "init_array":
                        stack.append(Value("array", "", tuple(pop() for _ in range(size)), origin=pc))
                    else:
                        values = []
                        for _ in range(size):
                            value, key = pop(), pop()
                            if key.kind != "literal" or not isinstance(key.literal, str):
                                raise ReconstructionError("Dynamic object literal keys need P-code")
                            values.extend((key, value))
                        stack.append(Value("object", "", tuple(values), origin=pc))
                elif op == "new_object":
                    constructor, count = pop(), pop()
                    if constructor.kind != "literal" or not isinstance(constructor.literal, str) or count.kind != "literal" or type(count.literal) not in (int, float) or not math.isfinite(count.literal) or count.literal != int(count.literal) or not 0 <= count.literal <= len(stack):
                        raise ReconstructionError("Dynamic constructor/argument count needs P-code")
                    stack.append(Value("construct", "", (name(constructor.literal), *(pop() for _ in range(int(count.literal)))), origin=pc))
                else:
                    raise ReconstructionError(f"Unsupported AS2 operation {op} at {pc}")
                continue
            if op.startswith("getlocal"):
                index = int(op[-1]) if "_" in op else args[0]
                stack.append(special.get(index, self.local(index, pc)))
            elif op.startswith("setlocal"):
                index = int(op[-1]) if "_" in op else args[0]
                value = pop()
                if value.kind in ("activation", "catch"):
                    special[index] = value
                else:
                    special.pop(index, None)
                    store(self.local(index, pc), value, pc)
            elif op in ("inclocal", "inclocal_i", "declocal", "declocal_i"):
                target = self.local(args[0], pc)
                store(target, binary("+" if op.startswith("inc") else "-", target, literal(1), pc), pc)
            elif op in ("pushscope", "pushwith"):
                obj = pop()
                if obj.effectful:
                    temp = self.fresh(pc)
                    emit(Statement("assign", obj, temp, pc, True))
                    obj = temp
                if op == "pushwith":
                    emit(Statement("scope_enter", obj, pc=pc))
                    obj = Value("with_scope", "", (obj,), origin=pc)
                scopes.append(obj)
            elif op == "popscope":
                if not scopes:
                    raise ReconstructionError(f"Scope stack underflow at {pc}")
                obj = scopes.pop()
                if obj.kind == "with_scope":
                    emit(Statement("scope_exit", pc=pc))
            elif op == "getscopeobject":
                if args[0] >= len(scopes):
                    raise ReconstructionError(f"Unresolved scope object at {pc}")
                obj = scopes[args[0]]
                stack.append(obj.args[0] if obj.kind == "with_scope" else obj)
            elif op == "newactivation":
                stack.append(self.activation)
            elif op == "newfunction":
                from .functions import abc_function
                stack.append(abc_function(args[0], self, scopes, pc))
            elif op == "newarray":
                values = tuple(pop() for _ in range(args[0]))[::-1]
                stack.append(Value("array", "", values, origin=pc))
            elif op == "newobject":
                pairs = []
                for _ in range(args[0]):
                    value, key = pop(), pop()
                    pairs.append((key, value))
                stack.append(Value("object", "", tuple(v for pair in reversed(pairs) for v in pair), origin=pc))
            elif op == "hasnext2":
                stack.append(Value("enumerate", "", (self.local(args[0], pc), self.local(args[1], pc)), origin=pc))
            elif op in ("nextname", "nextvalue"):
                cursor, obj = pop(), pop()
                stack.append(Value("next", "each" if op == "nextvalue" else "in", (obj, cursor), origin=pc))
            elif op == "getouterscope":
                if args[0] >= len(self.captured) or self.captured[args[0]].kind == "unknown_scope":
                    raise ReconstructionError(f"Unresolved captured scope at {pc}")
                obj = self.captured[args[0]]
                if obj.kind == "name" and obj.text == "this":
                    raise ReconstructionError("Captured this needs a lexical alias; use P-code")
                stack.append(obj.args[0] if obj.kind == "with_scope" else obj)
            elif op == "newcatch":
                index = args[0]
                if index >= len(self.body.exceptions):
                    raise ReconstructionError("Invalid newcatch index")
                caught = self.body.exceptions[index]
                text = resolve_multiname(self.pool, caught.var_name).split("::")[-1] if caught.var_name else "$exception"
                stack.append(Value("catch", text, origin=pc))
            elif op in ("setslot", "getslot"):
                value = pop() if op == "setslot" else None
                obj = pop()
                if obj.kind == "catch" and args[0] == 1:
                    if op == "getslot":
                        stack.append(name(obj.text, pc))
                elif obj.kind == "activation" and any(slot == args[0] for slot, *_ in obj.literal or ()):
                    _, _, text, typ = next(item for item in obj.literal if item[0] == args[0])
                    self.local_types[text] = typ
                    if op == "getslot":
                        stack.append(name(text, pc))
                    else:
                        store(name(text, pc), value, pc, typ=typ)
                else:
                    raise ReconstructionError(f"Unresolved slot {args[0]} at {pc}")
            elif op in ("pushbyte", "pushshort"):
                value = args[0] - 256 if op == "pushbyte" and args[0] > 127 else args[0]
                stack.append(literal(value, pc))
            elif op in ("pushint", "pushuint", "pushdouble", "pushstring"):
                values = {"pushint": self.pool.integers, "pushuint": self.pool.uintegers, "pushdouble": self.pool.doubles, "pushstring": self.pool.strings}[op]
                stack.append(literal(values[args[0]], pc))
            elif op in ("pushtrue", "pushfalse"):
                stack.append(literal(op == "pushtrue", pc))
            elif op in ("pushnull", "pushundefined", "pushnan"):
                stack.append(Value("raw", {"pushnull": "null", "pushundefined": "undefined", "pushnan": "NaN"}[op], origin=pc))
            elif op in ("coerce_a", "checkfilter"):
                pass
            elif op.startswith(("coerce", "convert")):
                typ = {"i": "int", "u": "uint", "d": "Number", "s": "String", "b": "Boolean", "o": "Object"}.get(op[-1])
                if op == "coerce":
                    typ = self.symbol(args[0]).text
                if not typ:
                    raise ReconstructionError(f"Unknown coercion {op}")
                value = pop()
                if op in ("coerce_i", "convert_i", "coerce_u", "convert_u", "coerce_d", "convert_d", "coerce_b", "convert_b"):
                    converted = Value("primitive_conversion", typ, (value,), origin=value.origin)
                    following = next((item for item in block.instructions[instruction_index + 1:] if item.op not in ("nop", "label", "debug", "debugline", "debugfile")), None)
                    declared = resolve_multiname(self.pool, self.abc.methods[self.body.method].return_type) if 0 <= self.body.method < len(self.abc.methods) else "*"
                    if following is not None and following.op == "return" and following.args == (True,) and declared == typ:
                        stack.append(converted)
                    else:
                        temp = self.fresh(pc)
                        emit(Statement("assign", converted, temp, pc, True))
                        stack.append(temp)
                    continue
                if op == "coerce" and value.kind == "function" and typ == "Function":
                    stack.append(value)
                    continue
                if op == "coerce" and typ not in ("*", "int", "uint", "Number", "Boolean", "String"):
                    # Assignment to a typed temporary expresses AVM2 coerce
                    # (null stays null and invalid references throw). Type(x)
                    # is a call and `as` would silently return null instead.
                    if self.bindings.type_source_shadowed(typ) or typ in self.local_names.values():
                        raise ReconstructionError("Typed coercion has a shadowed source type; exact P-code retained")
                    temp = self.fresh(pc)
                    self.local_types[temp.text] = typ
                    emit(Statement("assign", value, temp, pc, True, typ))
                    stack.append(temp)
                    continue
                if op == "coerce_s":
                    if value.kind == "raw" and value.text in ("null", "undefined"):
                        stack.append(Value("raw", "null", origin=value.origin))
                        continue
                    if value.kind == "literal" and isinstance(value.literal, str):
                        stack.append(value)
                        continue
                    if value.kind == "next":
                        if self.bindings.type_source_shadowed("String") or "String" in self.local_names.values():
                            raise ReconstructionError("Nullable String temporary has a shadowed type; exact P-code retained")
                        # A typed enumeration temporary can express coerce_s
                        # exactly, including null/undefined -> null. Keep this
                        # internal until the structurer proves the loop; a
                        # plain String(...) cast would turn null into text.
                        stack.append(Value("enumeration_string", "String", (value,), origin=value.origin))
                        continue
                    if not self.string_value(value):
                        raise ReconstructionError("Nullable string coercion needs type-aware reconstruction; use P-code")
                    stack.append(value)  # includes nullable, already typed String locals
                    continue
                if typ == "String" and ("String" in self.local_names.values() or "String" in self.bindings.members):
                    raise ReconstructionError("String conversion has a shadowed intrinsic; exact P-code retained")
                if value.kind == "cast" and value.text == typ:
                    stack.append(value)
                else:
                    stack.append(Value("cast", typ, (value,), origin=value.origin))
            elif op == "astype":
                try:
                    typ = self.symbol(args[0])
                except (ReconstructionError, ValueError):
                    typ = name(resolve_multiname(self.pool, args[0]).replace("::", "."))
                value = pop()
                stack.append(binary("as", value, typ, pc=pc))
            elif op == "astypelate":
                target_type = pop()
                value = pop()
                stack.append(binary("as", value, target_type, pc=pc))
            elif op == "istype":
                try:
                    typ = self.symbol(args[0])
                except (ReconstructionError, ValueError):
                    typ = name(resolve_multiname(self.pool, args[0]).replace("::", "."))
                value = pop()
                stack.append(binary("is", value, typ, pc=pc))
            elif op == "istypelate":
                target_type = pop()
                value = pop()
                stack.append(binary("is", value, target_type, pc=pc))
            elif op == "typeof":
                value = pop()
                stack.append(Value("unary", "typeof ", (value,), origin=pc))
            elif op in ("getlex", "findpropstrict", "findproperty", "getglobalscope"):
                mn = self.pool.multinames[args[0]] if op != "getglobalscope" else None
                lexical = self.lexical_binding(args[0], scopes) if op != "getglobalscope" else None
                owned = mn and mn["kind"] == 7 and self.pool.namespaces[mn["ns"]]["kind"] in (5, 8, 0x17, 0x18, 0x1A) and lexical is None and any(name_identity(self.pool,args[0]) in choices for choices in self.bindings.members.values())
                if owned:
                    prop = self.symbol(args[0])
                    expected_receiver = self.bindings.class_name if self.bindings.static else "this"
                    if not scopes or any(scope.kind != "name" or scope.text != expected_receiver for scope in scopes):
                        raise ReconstructionError("Private lexical lookup has an unproven dynamic scope; use P-code")
                    receiver = Value("name", self.bindings.class_name if self.bindings.static else "this", binding=prop.binding)
                    stack.append(Value("member", "", (receiver, literal(prop.text)), origin=pc, binding=prop.binding) if op == "getlex" else receiver)
                elif op == "getlex":
                    stack.append(self.lexical_binding(args[0], scopes) or self.symbol(args[0]))
                else:
                    stack.append(self.lexical_object(args[0], scopes) or Value("global", "") if op != "getglobalscope" else Value("global", ""))
            elif op in ("getproperty", "setproperty", "initproperty", "callproperty", "callpropvoid", "callproplex", "constructprop", "getsuper", "setsuper", "callsuper", "callsupervoid", "getdescendants", "deleteproperty"):
                params = tuple(pop() for _ in range(args[1]))[::-1] if op.startswith(("call", "construct")) else ()
                value = pop() if op in ("setproperty", "initproperty", "setsuper") else None
                mn = self.pool.multinames[args[0]]
                key = None
                if mn and mn["kind"] == 0x1B:
                    namespaces = self.pool.ns_sets[mn["ns_set"]]
                    key = pop()
                    numeric = key.kind == "literal" and type(key.literal) is int and key.literal >= 0
                    if not namespaces or not numeric and any(self.pool.namespaces[n]["kind"] not in (0x16, 0x17) for n in namespaces):
                        raise ReconstructionError("Non-public runtime namespace needs P-code")
                obj = pop()
                if mn is None or mn["kind"] not in (7, 9, 0x1B):
                    raise ReconstructionError("Runtime multinames require P-code")
                if key is not None:
                    if obj.kind == "global":
                        raise ReconstructionError("Runtime global property lookup needs P-code")
                    target = Value("descendant" if op == "getdescendants" else "member", "", (obj, key), origin=pc)
                else:
                    prop = self.lexical_binding(args[0], scopes) or self.symbol(args[0], inherited=op in ("getsuper", "setsuper", "callsuper", "callsupervoid"))
                    if op in ("getsuper", "setsuper", "callsuper", "callsupervoid"):
                        try:
                            self.bindings.require_owned(args[0], inherited=True)
                        except ValueError as error:
                            raise ReconstructionError(str(error)) from error
                        if obj.kind != "name" or obj.text != "this":
                            raise ReconstructionError("super has an unproven receiver; use P-code")
                        obj = name("super", pc)
                    if prop.binding and prop.binding.nonpublic:
                        expected = self.bindings.class_name if self.bindings.static else "this"
                        if obj.kind != "name" or obj.text not in (expected, "super"):
                            raise ReconstructionError("Private/non-public member has an unproven receiver; use P-code")
                    if prop.binding and prop.binding.package_qualified and obj.kind not in ("global", "activation"):
                        raise ReconstructionError("Qualified package name requires a proven global receiver; use P-code")
                    kind = "descendant" if op == "getdescendants" else "member"
                    target = prop if obj.kind in ("global", "activation") and op != "getdescendants" else Value(kind, "", (obj, literal(prop.text.split(".")[-1])), origin=pc, binding=prop.binding)
                    if obj.kind == "global" and any(v.kind == "with_scope" for v in scopes):
                        target = Value("dynamic_name", prop.text, origin=pc)
                if op == "getdescendants":
                    stack.append(target)
                elif op == "deleteproperty":
                    stack.append(Value("unary", "delete ", (target,), origin=pc))
                elif op in ("getproperty", "getsuper"):
                    stack.append(target)
                elif value is not None:
                    store(target, value, pc)
                else:
                    if op=="callproplex" and target.binding is not None:
                        raise ReconstructionError("Private function's null receiver needs explicit call semantics; use P-code")
                    call = Value("construct" if op == "constructprop" else "call", "", (target, *params), origin=pc)
                    if op in ("callpropvoid", "callsupervoid"):
                        emit(Statement("expr", call, pc=pc))
                    else:
                        stack.append(call)
            elif op == "constructsuper":
                params = tuple(pop() for _ in range(args[0]))[::-1]
                pop()
                emit(Statement("expr", Value("call", "", (name("super"), *params)), pc=pc))
            elif op == "construct":
                params = tuple(pop() for _ in range(args[0]))[::-1]
                callee = pop()
                if callee.binding is not None and getattr(callee.binding, "private", False):
                    raise ReconstructionError("Private constructor call needs explicit receiver semantics; use P-code")
                stack.append(Value("construct", "", (callee, *params), origin=pc))
            elif op == "call":
                params = tuple(pop() for _ in range(args[0]))[::-1]
                receiver = pop()
                callee = pop()
                if callee.binding is not None:
                    raise ReconstructionError("Private function value call needs explicit receiver semantics; use P-code")
                if receiver.kind != "global" and not (receiver.kind == "raw" and receiver.text == "null"):
                    if callee.kind != "function":
                        raise ReconstructionError("Non-global call receiver cannot be discarded; use P-code")
                    callee = Value("member", "", (callee, literal("call")), origin=pc)
                    params = (receiver, *params)
                stack.append(Value("call", "", (callee, *params), origin=pc))
            elif op == "dxns":
                if not 0 < args[0] < len(self.pool.strings):
                    raise ReconstructionError(f"dxns names no string at {pc}; use P-code")
                emit(Statement("expr", Value("raw", "default xml namespace = " + render(literal(self.pool.strings[args[0]])), origin=pc), pc=pc))
            elif op == "dxnslate":
                val = pop()
                emit(Statement("expr", Value("raw", f'default xml namespace = {render(val)}', origin=pc), pc=pc))
            elif op in ("esc_xelem", "esc_xattr"):
                # Escapes of an interpolated XML literal ({x} inside <a>...</a>); no call expression means the same.
                raise ReconstructionError(f"XML literal interpolation ({op}) needs P-code")
            else:
                raise ReconstructionError(f"Unsupported AS3 operation {op} at {pc}")
        block.statements, block.term, block.condition = statements, term, condition
        block.stack_out = tuple(stack)
        return State(tuple(stack), tuple(scopes), tuple(sorted(special.items())), tuple(self.pool) if self.target == "as2" else None)

    def string_value(self, value):
        if value.kind == "literal":
            return isinstance(value.literal, str)
        if value.kind == "name":
            return self.local_types.get(value.text) == "String"
        if value.kind == "cast":
            return value.text == "String"
        return value.kind == "binary" and value.text == "+" and any(self.nonnullable_string_value(v) for v in value.args)

    def nonnullable_string_value(self, value):
        # A nullable String local may contain null: null + 1 is Number(1),
        # so coerce_s on that result cannot be elided. Only a literal or an
        # explicit String(...) conversion proves concatenation in '+'.
        if value.kind == "literal":
            return isinstance(value.literal, str)
        if value.kind == "cast":
            return value.text == "String"
        return value.kind == "binary" and value.text == "+" and any(self.nonnullable_string_value(v) for v in value.args)

    def lift_graph(self, blocks, seeds=None):
        seeds = seeds or {min(blocks): State()}
        if self.target == "as2":
            from .pool_flow import seed_pools
            inferred = seed_pools([ins for block in blocks.values() for ins in block.instructions], self.initial_pool)
            if any(pc in inferred and inferred[pc] is None for pc in seeds):
                raise ReconstructionError("Exception entry captures multiple constant-pool identities; use P-code")
            seeds = {pc: State(state.stack, state.scopes, state.objects, inferred.get(pc, self.initial_pool)) for pc, state in seeds.items()}
        phis = {}
        # Restarting propagates each new merge through consumers already
        # visited on another arm. Back edges/effectful values are not guessed.
        for _ in range(64):
            pending, states, done, incoming = deque(seeds), dict(seeds), set(), {}
            restart = False
            while pending and not restart:
                pc = pending.popleft()
                if pc in done or pc not in blocks:
                    continue
                done.add(pc)
                state = self.lift(blocks[pc], states[pc])
                emitted = set()
                for target in blocks[pc].successors:
                    if target not in blocks:
                        continue
                    outgoing = list(state.stack)
                    for (join, index), temp in phis.items():
                        if join != target:
                            continue
                        if index >= len(outgoing):
                            raise ReconstructionError("Effectful/incompatible expression merge needs P-code")
                        if (join, index) not in emitted:
                            blocks[pc].statements.append(Statement("assign", outgoing[index], temp, blocks[pc].instructions[-1].pc))
                            emitted.add((join, index))
                        outgoing[index] = temp
                    edge = State(tuple(outgoing), state.scopes, state.objects, state.constant_pool)
                    incoming.setdefault(target, {})[pc] = edge
                    old = states.get(target)
                    if old is None:
                        states[target] = edge
                        pending.append(target)
                    elif tuple((v.kind, v.text, v.args) for v in old.stack) != tuple((v.kind, v.text, v.args) for v in edge.stack):
                        first = next((i for i in blocks[target].instructions if i.op not in ("nop", "label", "debugline", "debugfile", "debug")), None)
                        discarded = first and first.op == "pop" and len(old.stack) == len(edge.stack) == 1 and not old.stack[0].effectful and not edge.stack[0].effectful
                        if not discarded:
                            safe = (len(old.stack) == len(edge.stack) and target not in seeds and all(p < target for p in incoming[target]) and not any(v.kind == "with_scope" for v in state.scopes))
                            if not safe:
                                raise ReconstructionError(f"Unresolved stack join at {target}; source would be ambiguous")
                            for index, (a, b) in enumerate(zip(old.stack, edge.stack)):
                                if (a.kind, a.text, a.args) != (b.kind, b.text, b.args) and (target, index) not in phis:
                                    phis[target, index] = self.fresh(target)
                            restart = True
                            break
                    # Proven exception lowerings seed handlers after their
                    # scope reset, whereas the normal edge still carries the
                    # inert catch scope until the region emitter removes it.
                    # Outside that proven lowering all scope identities agree.
                    if old is not None and old.scopes != edge.scopes and (not self.body or not self.body.exceptions or any(v.kind in ("with_scope", "unknown_scope") for v in (*old.scopes, *edge.scopes))):
                        raise ReconstructionError(f"Dynamic/captured scope mismatch at join {target}")
                    if old is not None and old.constant_pool != edge.constant_pool:
                        raise ReconstructionError(f"Constant pool mismatch at join {target}; source would capture a different value")
                    if old is not None and old.objects != edge.objects and (not self.body or not self.body.exceptions):
                        raise ReconstructionError(f"Activation/register identity mismatch at join {target}")
            if not restart:
                return done
        raise ReconstructionError("Expression merge work budget exceeded")
