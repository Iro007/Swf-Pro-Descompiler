"""Bounded reference interpreter for native compiler tests, NOT a Flash runtime.

Runs emitted bytes, including AVM1 Try containers and AVM2 exception tables.
Does not use the source AST to determine results. Unsupported opcodes fail.
"""

from dataclasses import dataclass
from collections import ChainMap
import json
import math

from py_swf.avm1 import assemble_avm1, parse_push_values, parse_try_header
from py_swf.avm2 import ABCFile, MethodInfo, MethodBodyInfo, assemble_instructions, resolve_multiname
from py_swf.bytecode_validation import analyze_method, decode_avm2, validate_avm1
from py_swf.compiler import compile_actionscript


class Thrown(Exception):
    def __init__(self, value):
        self.value = value


@dataclass
class Result:
    traces: list
    value: object = None
    error: object = None
    raised: bool = False


def strict_equal(left, right):
    # Both AVMs have one Number category, unlike Python's int/float distinction.
    numbers = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    return (numbers(left) and numbers(right) or type(left) is type(right)) and left == right


class FlashError(Exception):
    @property
    def message(self):
        return str(self)

    def getStackTrace(self):
        return "reference stack"


class DefinitionError(FlashError):
    pass


def flash_string(value):
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "null" if value is None else str(value)


def flash_add(left, right):
    return flash_string(left) + flash_string(right) if isinstance(left, str) or isinstance(right, str) else left + right


def matches(value, type_name, environment):
    if type_name == "*":
        return True
    if type_name == "Object":
        return value is not None
    if type_name == "String":
        return isinstance(value, str)
    if type_name == "Boolean":
        return isinstance(value, bool)
    if type_name in ("Number", "int", "uint"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    cls = environment.get(type_name)
    return isinstance(cls, type) and isinstance(value, cls)


def run_source(source, target, **options):
    compiled = compile_actionscript(source, target, **options)
    if target == "as2":
        code = assemble_avm1(compiled)
        validate_avm1(code)
        return run_bytes(code, target)
    abc = ABCFile()
    abc.methods = [MethodInfo()]
    body = MethodBodyInfo()
    body.code = assemble_instructions(abc.constant_pool, compiled, exception_table=body.exceptions)
    limits = analyze_method(abc, body)
    body.max_stack, body.local_count, body.max_scope_depth = limits.max_stack, limits.local_count, limits.max_scope_depth
    return run_bytes(body.code, target, abc=abc, body=body)


def run_bytes(code, target, *, abc=None, body=None, variables=None, arguments=(), captured=(), _environment=None, _registers=None, _constants=(), _traces=None, _depth=0):
    assert _depth < 32, "Reference function depth exceeded"
    result = Result([] if _traces is None else _traces)
    def explode():
        raise Thrown("boom")
    environment = {"trace": lambda *args: result.traces.extend(args), "explode": explode,
                   "Error": Exception, "DefinitionError": DefinitionError, "TypeError": TypeError, "score": 10,
                   "Number": float, "int": int, "uint": lambda v: int(v) & 0xffffffff,
                   "Boolean": bool, "String": flash_string, "Infinity": float("inf"), "NaN": float("nan")}
    environment.update(variables or {})
    environment = _environment if _environment is not None else environment
    stack, registers, scope, frames, constants = [], {0: environment, 1: 10}, [], [], list(_constants)
    registers.update({i: value for i, value in enumerate(arguments, 1)})
    registers.update(_registers or {})
    with_frames = []
    instructions = {}
    if target == "as2":
        pc = 0
        while pc < len(code):
            offset, op = pc, code[pc]
            pc += 1
            payload = b""
            if op >= 0x80:
                size = int.from_bytes(code[pc:pc + 2], "little")
                pc += 2
                payload = code[pc:pc + size]
                pc += size
            instructions[offset] = (op, payload, pc)
    else:
        instructions = {ins.offset: ins for ins in decode_avm2(code)}
    pc, steps = 0, 0
    def owner(key):
        for obj in reversed((*captured, *scope)):
            if isinstance(obj, dict) and (key in obj or key in obj.get("names", {})):
                return obj
        return environment
    def get_property(obj, key):
        if isinstance(obj, dict):
            return obj["slots"].get(obj["names"][key]) if key in obj.get("names", {}) else obj.get(key)
        return obj[int(key)] if isinstance(obj, list) else getattr(obj, key)
    def set_property(obj, key, value):
        if isinstance(obj, dict) and key in obj.get("names", {}):
            obj["slots"][obj["names"][key]] = value
        elif isinstance(obj, list):
            while len(obj) <= int(key):
                obj.append(None)
            obj[int(key)] = value
        else:
            obj[key] = value
    def complete(kind, value):
        nonlocal pc
        while frames:
            frame = frames[-1]
            if kind == "jump" and frame["begin"] <= value < frame["stop"]:
                pc = value
                return False
            if kind == "throw" and frame["phase"] == "try" and frame["flags"] & 1:
                stack[frame["stack"]:] = []
                key = frame["name"]
                if key.startswith("r:"):
                    registers[int(key[2:])] = value
                else:
                    environment[json.loads(key)] = value
                frame["phase"], frame["begin"], frame["stop"] = "catch", frame["catch"], frame["catch_end"]
                pc = frame["catch"]
                return False
            if frame["phase"] != "finally" and frame["flags"] & 2:
                frame["pending"] = (kind, value)
                frame["phase"], frame["begin"], frame["stop"] = "finally", frame["final"], frame["end"]
                pc = frame["final"]
                return False
            frames.pop()
        if kind == "return":
            result.value = value
            return True
        if kind == "throw":
            result.error, result.raised = value, True
            return True
        pc = value
        return False
    while pc in instructions:
        while with_frames and not with_frames[-1][0] <= pc < with_frames[-1][1]:
            _, _, environment = with_frames.pop()
        while frames and pc >= frames[-1]["stop"]:
            frame = frames[-1]
            if frame["phase"] != "finally" and frame["flags"] & 2:
                frame["phase"], frame["begin"], frame["stop"] = "finally", frame["final"], frame["end"]
                pc = frame["final"]
                break
            frames.pop()
            if frame.get("pending"):
                if complete(*frame["pending"]):
                    return result
                break
            if pc == frame["stop"]:
                pc = frame["end"]
        if pc not in instructions:
            break
        steps += 1
        assert steps < 10000, "Generated bytecode did not terminate"
        offset = pc
        try:
            if target == "as2":
                op, payload, pc = instructions[pc]
                if op == 0:
                    break
                if op == 0x96:
                    for token in parse_push_values(payload):
                        stack.append(None if token in ("null", "undefined") else registers.get(int(token[2:])) if token.startswith("r:") else constants[int(token[2:])] if token.startswith("c:") else float(token) if token in ("inf", "-inf", "nan") else json.loads(token))
                elif op == 0x88:
                    count, cursor, constants = int.from_bytes(payload[:2], "little"), 2, []
                    for _ in range(count):
                        end = payload.index(0, cursor)
                        constants.append(payload[cursor:end].decode("utf-8"))
                        cursor = end + 1
                elif op == 0x87:
                    registers[payload[0]] = stack[-1]
                elif op in (0x8e, 0x9b):
                    from py_swf.decompile.functions import action_header
                    fn_name, params, mapping = action_header(payload, op == 0x8e)
                    size = int.from_bytes(payload[-2:], "little")
                    nested = code[pc:pc + size]
                    saved_environment, saved_constants = environment, tuple(constants)
                    def function(*args, _nested=nested, _params=tuple(params), _mapping=dict(mapping), _env=saved_environment, _const=saved_constants):
                        bindings = {name:args[index] if index<len(args) else None for index,name in enumerate(_params)}
                        bindings["arguments"] = list(args)
                        local_environment = ChainMap(bindings, _env)
                        regs = {r: bindings.get(p, _env.get(p)) for r, p in _mapping.items()}
                        child = run_bytes(_nested, "as2", _environment=local_environment, _registers=regs, _constants=_const, _traces=result.traces, _depth=_depth + 1)
                        if child.raised:
                            raise Thrown(child.error)
                        return child.value
                    if fn_name:
                        environment[fn_name] = function
                    else:
                        stack.append(function)
                    pc += size
                elif op == 0x94:
                    obj = stack.pop()
                    with_frames.append((pc, pc + int.from_bytes(payload, "little"), environment))
                    environment = ChainMap(obj, environment)
                elif op in (0x50, 0x51):
                    stack.append(float(stack.pop()) + (1 if op == 0x50 else -1))
                elif op == 0x8f:
                    flags, sizes, name = parse_try_header(payload)
                    frames.append({"begin": pc, "stop": pc + sizes[0], "end": pc + sum(sizes), "catch": pc + sizes[0], "final": pc + sizes[0] + sizes[1],
                                   "catch_end": pc + sizes[0] + sizes[1], "name": name, "flags": flags,
                                   "phase": "try", "stack": len(stack)})
                elif op == 0x1c:
                    stack.append(environment.get(stack.pop()))
                elif op in (0x1d, 0x3c):
                    value, name = stack.pop(), stack.pop()
                    if op == 0x1d and isinstance(environment, ChainMap):
                        destination = next((m for m in environment.maps if name in m), environment.maps[-1])
                        destination[name] = value
                    else:
                        environment[name] = value
                elif op == 0x4c:
                    stack.append(stack[-1])
                elif op == 0x4d:
                    stack[-1], stack[-2] = stack[-2], stack[-1]
                elif op == 0x17:
                    stack.pop()
                elif op == 0x4a:
                    stack.append(float(stack.pop()))
                elif op == 0x12:
                    stack.append(not stack.pop())
                elif op == 0x26:
                    result.traces.append(stack.pop())
                elif op == 0x3d:
                    name, count = stack.pop(), int(stack.pop())
                    args = [stack.pop() for _ in range(count)]
                    stack.append(environment[name](*args))
                elif op == 0x4e:
                    name, obj = stack.pop(), stack.pop()
                    stack.append(get_property(obj, name))
                elif op == 0x4f:
                    value, name, obj = stack.pop(), stack.pop(), stack.pop()
                    set_property(obj, name, value)
                elif op in (0x46, 0x55):
                    obj = environment.get(stack.pop()) if op == 0x46 else stack.pop()
                    keys = list(range(len(obj))) if isinstance(obj, list) else list(obj or {})
                    stack.append(None)
                    stack.extend(reversed(keys))
                elif op == 0x42:
                    stack.append([stack.pop() for _ in range(int(stack.pop()))])
                elif op == 0x43:
                    obj = {}
                    for _ in range(int(stack.pop())):
                        value, key = stack.pop(), stack.pop()
                        obj[key] = value
                    stack.append(obj)
                elif op == 0x54:
                    cls, value = stack.pop(), stack.pop()
                    stack.append(isinstance(value, cls))
                elif op in (0x99, 0x9d):
                    if op == 0x99 or bool(stack.pop()):
                        pc += int.from_bytes(payload, "little", signed=True)
                elif op == 0x3e:
                    if complete("return", stack.pop()):
                        return result
                elif op == 0x2a:
                    raise Thrown(stack.pop())
                elif op in (0x47, 0x0b, 0x0c, 0x0d, 0x3f, 0x48, 0x67, 0x49, 0x66):
                    right, left = stack.pop(), stack.pop()
                    stack.append({0x47: lambda: flash_add(left, right), 0x0b: lambda: left - right,
                                  0x0c: lambda: left * right, 0x0d: lambda: left / right, 0x3f: lambda: left % right,
                                  0x48: lambda: left < right, 0x67: lambda: left > right,
                                  0x49: lambda: left == right, 0x66: lambda: strict_equal(left, right)}[op]())
                else:
                    raise AssertionError(f"Reference interpreter does not implement AVM1 {op:x}")
            else:
                ins = instructions[pc]
                pc, op, args = ins.end, ins.name, ins.args
                if op.startswith("getlocal"):
                    stack.append(registers.get(int(op[-1]) if "_" in op else args[0]))
                elif op.startswith("setlocal"):
                    registers[int(op[-1]) if "_" in op else args[0]] = stack.pop()
                elif op in ("pushscope", "pushwith"):
                    scope.append(stack.pop())
                elif op in ("nop", "label", "debugline", "debugfile", "debug", "coerce_a"):
                    pass
                elif op == "kill":
                    registers[args[0]] = None
                elif op == "newactivation":
                    traits=[trait for trait in body.traits if trait.kind_flags&15 in (0,6)]
                    defaults={trait.slot_id:0 if resolve_multiname(abc.constant_pool,trait.type_name) in ('int','uint') else False if resolve_multiname(abc.constant_pool,trait.type_name)=='Boolean' else float('nan') if resolve_multiname(abc.constant_pool,trait.type_name)=='Number' else None for trait in traits}
                    stack.append({"slots":defaults,"names":{resolve_multiname(abc.constant_pool,trait.name):trait.slot_id for trait in traits}})
                elif op == "newfunction":
                    child_body = next(b for b in abc.method_bodies if b.method == args[0])
                    saved_scope = (*captured, *scope)
                    def function(*values, _body=child_body, _scope=saved_scope):
                        child = run_bytes(_body.code, "as3", abc=abc, body=_body, arguments=values, captured=_scope, _environment=environment, _traces=result.traces, _depth=_depth + 1)
                        if child.raised:
                            raise Thrown(child.error)
                        return child.value
                    stack.append(function)
                elif op == "coerce":
                    pass  # selected typed closure/array fixtures only
                elif op == "newarray":
                    stack.append([stack.pop() for _ in range(args[0])][::-1])
                elif op == "newobject":
                    pairs = []
                    for _ in range(args[0]):
                        value, key = stack.pop(), stack.pop()
                        pairs.append((key, value))
                    stack.append(dict(reversed(pairs)))
                elif op == "hasnext2":
                    obj, cursor = registers[args[0]], int(registers[args[1]]) + 1
                    keys = list(range(len(obj))) if isinstance(obj, list) else list(obj or {})
                    registers[args[1]] = cursor if cursor <= len(keys) else 0
                    stack.append(cursor <= len(keys))
                elif op in ("nextname", "nextvalue"):
                    cursor, obj = int(stack.pop()), stack.pop()
                    keys = list(range(len(obj))) if isinstance(obj, list) else list(obj or {})
                    key = keys[cursor - 1]
                    stack.append(key if op == "nextname" else obj[key])
                elif op == "newcatch":
                    stack.append({"slots": {}})
                elif op == "getscopeobject":
                    stack.append(scope[args[0]])
                elif op == "getouterscope":
                    stack.append(captured[args[0]])
                elif op == "popscope":
                    scope.pop()
                elif op == "getslot":
                    stack.append(stack.pop()["slots"].get(args[0]))
                elif op == "setslot":
                    value, obj = stack.pop(), stack.pop()
                    obj["slots"][args[0]] = value
                elif op == "returnvoid":
                    return result
                elif op == "returnvalue":
                    result.value = stack.pop()
                    return result
                elif op == "throw":
                    raise Thrown(stack.pop())
                elif op in ("pushundefined", "pushnull"):
                    stack.append(None)
                elif op in ("pushtrue", "pushfalse"):
                    stack.append(op == "pushtrue")
                elif op in ("pushbyte", "pushshort"):
                    stack.append(args[0] - 256 if args[0] > 127 else args[0])
                elif op in ("pushint", "pushdouble", "pushstring"):
                    values = {"pushint": abc.constant_pool.integers, "pushdouble": abc.constant_pool.doubles, "pushstring": abc.constant_pool.strings}[op]
                    stack.append(values[args[0]])
                elif op in ("findpropstrict", "findproperty", "getglobalscope"):
                    stack.append(environment if op == "getglobalscope" else owner(resolve_multiname(abc.constant_pool, args[0])))
                elif op == "getlex":
                    key = resolve_multiname(abc.constant_pool, args[0])
                    stack.append(get_property(owner(key), key))
                elif op == "getproperty":
                    mn = abc.constant_pool.multinames[args[0]]
                    key = stack.pop() if mn["kind"] == 0x1b else resolve_multiname(abc.constant_pool, args[0])
                    obj = stack.pop()
                    stack.append(get_property(obj, key))
                elif op == "setproperty":
                    value = stack.pop()
                    mn = abc.constant_pool.multinames[args[0]]
                    key = stack.pop() if mn["kind"] == 0x1b else resolve_multiname(abc.constant_pool, args[0])
                    set_property(stack.pop(), key, value)
                elif op in ("callproperty", "callpropvoid", "constructprop"):
                    values = [stack.pop() for _ in range(args[1])][::-1]
                    obj = stack.pop()
                    key = resolve_multiname(abc.constant_pool, args[0])
                    value = get_property(obj, key)(*values)
                    if op != "callpropvoid":
                        stack.append(value)
                elif op == "call":
                    values = [stack.pop() for _ in range(args[0])][::-1]
                    stack.pop()  # receiver for the selected global functions
                    stack.append(stack.pop()(*values))
                elif op == "dup":
                    stack.append(stack[-1])
                elif op == "pop":
                    stack.pop()
                elif op == "swap":
                    stack[-1], stack[-2] = stack[-2], stack[-1]
                elif op in ("iftrue", "iffalse", "jump"):
                    if op == "jump" or bool(stack.pop()) == (op == "iftrue"):
                        pc += args[0]
                elif op in ("ifeq", "ifne", "iflt", "ifle", "ifgt", "ifge", "ifstricteq", "ifstrictne", "ifnlt", "ifnle", "ifngt", "ifnge"):
                    right, left = stack.pop(), stack.pop()
                    condition = {"ifeq": lambda: left == right, "ifne": lambda: left != right,
                                 "iflt": lambda: left < right, "ifle": lambda: left <= right,
                                 "ifgt": lambda: left > right, "ifge": lambda: left >= right,
                                 "ifstricteq": lambda: strict_equal(left, right), "ifstrictne": lambda: not strict_equal(left, right),
                                 "ifnlt": lambda: not left < right, "ifnle": lambda: not left <= right,
                                 "ifngt": lambda: not left > right, "ifnge": lambda: not left >= right}[op]()
                    if condition:
                        pc += args[0]
                elif op == "lookupswitch":
                    value = int(stack.pop())
                    relative = args[2][value] if 0 <= value < len(args[2]) else args[0]
                    pc = ins.offset + relative
                elif op in ("increment", "increment_i", "decrement", "decrement_i"):
                    stack.append(stack.pop() + (1 if op.startswith("increment") else -1))
                elif op in ("add", "subtract", "multiply", "divide", "modulo", "lessthan", "lessequals", "greaterthan", "greaterequals", "equals", "strictequals"):
                    right, left = stack.pop(), stack.pop()
                    stack.append({"add": lambda: flash_add(left, right), "subtract": lambda: left - right,
                                  "multiply": lambda: left * right, "divide": lambda: left / right, "modulo": lambda: left % right,
                                  "lessthan": lambda: left < right, "lessequals": lambda: left <= right,
                                  "greaterthan": lambda: left > right, "greaterequals": lambda: left >= right,
                                  "equals": lambda: left == right, "strictequals": lambda: strict_equal(left, right)}[op]())
                elif op == "not":
                    stack.append(not stack.pop())
                elif op in ("bitand", "bitor", "bitxor", "lshift", "rshift", "urshift"):
                    def uint32(value):
                        number = float(value) if value is not None else 0.0
                        return (int(number) if math.isfinite(number) else 0) & 0xffffffff
                    right, left = uint32(stack.pop()), uint32(stack.pop())
                    signed = lambda value: value - 2**32 if value >= 2**31 else value
                    if op == "urshift":
                        result_value = left >> (right & 31)
                    else:
                        result_value = {"bitand": lambda: left & right, "bitor": lambda: left | right, "bitxor": lambda: left ^ right,
                                        "lshift": lambda: (left << (right & 31)) & 0xffffffff, "rshift": lambda: signed(left) >> (right & 31)}[op]()
                        result_value = signed(result_value & 0xffffffff)
                    stack.append(result_value)
                elif op in ("coerce_i", "convert_i", "coerce_u", "convert_u", "convert_d", "coerce_d", "coerce_s", "coerce_b", "coerce_o"):
                    value = stack.pop()
                    if op in ("coerce_d", "convert_d"):
                        value = float(value)
                    elif op == "coerce_s":
                        value = None if value is None else flash_string(value)
                    elif op == "coerce_b":
                        value = bool(value)
                    elif op in ("coerce_i", "convert_i", "coerce_u", "convert_u"):
                        value = (int(value) if value is not None else 0) & 0xffffffff
                        if op in ("coerce_i", "convert_i") and value >= 2 ** 31:
                            value -= 2 ** 32
                    stack.append(value)
                else:
                    raise AssertionError(f"Reference interpreter does not implement AVM2 {op}")
        except Thrown as thrown:
            if target == "as2":
                if complete("throw", thrown.value):
                    return result
            else:
                handler = next((e for e in body.exceptions if e.from_offset <= offset < e.to_offset
                                and matches(thrown.value, resolve_multiname(abc.constant_pool, e.exc_type), environment)), None)
                if handler is None:
                    result.error, result.raised = thrown.value, True
                    return result
                stack, scope, pc = [thrown.value], [], handler.target_offset
    return result
