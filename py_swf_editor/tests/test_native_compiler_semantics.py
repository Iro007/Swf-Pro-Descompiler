"""Execute the supported compiler subset in a small test-only reference VM.

This checks values and side effects, not merely the presence of opcode names.
It is not used by the application and is not a complete Flash runtime.
"""

import json

import pytest

from py_swf.avm1 import assemble_avm1, parse_push_values
from py_swf.avm2 import ABCFile, assemble_instructions, resolve_multiname
from py_swf.bytecode_validation import decode_avm2
from py_swf.compiler import compile_actionscript


def execute(source, target):
    traces, calls = [], []
    environment = {"trace": lambda *args: traces.extend(args), "mark": lambda: calls.append(True) or True}
    asm = compile_actionscript(source, target)
    if target == "as2":
        data = assemble_avm1(asm)
        instructions = {}
        pc = 0
        while pc < len(data):
            start, opcode = pc, data[pc]
            pc += 1
            payload = b""
            if opcode >= 0x80:
                size = int.from_bytes(data[pc:pc + 2], "little")
                pc += 2
                payload = data[pc:pc + size]
                pc += size
            instructions[start] = (opcode, payload, pc)
    else:
        abc = ABCFile()
        data = assemble_instructions(abc.constant_pool, asm)
        instructions = {ins.offset: ins for ins in decode_avm2(data)}
    stack, registers, scope = [], {0: environment}, []
    pc, steps = 0, 0
    while pc in instructions:
        steps += 1
        assert steps < 2000, "Generated loop did not terminate"
        if target == "as2":
            op, payload, pc = instructions[pc]
            if op == 0:
                break
            if op == 0x96:
                for token in parse_push_values(payload):
                    stack.append(None if token in ("null", "undefined") else json.loads(token))
            elif op == 0x1c:
                stack.append(environment.get(stack.pop()))
            elif op == 0x1d:
                value, name = stack.pop(), stack.pop()
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
                traces.append(stack.pop())
            elif op == 0x3d:
                name, count = stack.pop(), stack.pop()
                args = [stack.pop() for _ in range(count)]
                stack.append(environment[name](*args))
            elif op in (0x99, 0x9d):
                take = True if op == 0x99 else bool(stack.pop())
                if take:
                    pc += int.from_bytes(payload, "little", signed=True)
            elif op in (0x47, 0x0b, 0x48, 0x67, 0x49, 0x66):
                right, left = stack.pop(), stack.pop()
                stack.append({0x47: lambda: left + right, 0x0b: lambda: left - right, 0x48: lambda: left < right,
                              0x67: lambda: left > right, 0x49: lambda: left == right, 0x66: lambda: type(left) is type(right) and left == right}[op]())
            else:
                raise AssertionError(f"Reference VM does not implement AVM1 opcode {op:x}")
        else:
            ins = instructions[pc]
            pc, op, args = ins.end, ins.name, ins.args
            if op.startswith("getlocal"):
                index = int(op[-1]) if "_" in op else args[0]
                stack.append(registers.get(index))
            elif op.startswith("setlocal"):
                registers[int(op[-1]) if "_" in op else args[0]] = stack.pop()
            elif op == "pushscope":
                scope.append(stack.pop())
            elif op in ("nop", "label", "returnvoid"):
                if op == "returnvoid":
                    break
            elif op == "returnvalue":
                stack.pop()
                break
            elif op == "pushundefined" or op == "pushnull":
                stack.append(None)
            elif op in ("pushtrue", "pushfalse"):
                stack.append(op == "pushtrue")
            elif op == "pushbyte":
                stack.append(args[0] - 256 if args[0] > 127 else args[0])
            elif op in ("pushint", "pushdouble", "pushstring"):
                values = {"pushint": abc.constant_pool.integers, "pushdouble": abc.constant_pool.doubles, "pushstring": abc.constant_pool.strings}[op]
                stack.append(values[args[0]])
            elif op == "findpropstrict":
                stack.append(environment)
            elif op == "getproperty":
                stack.append(stack.pop()[resolve_multiname(abc.constant_pool, args[0])])
            elif op == "callproperty":
                call_args = [stack.pop() for _ in range(args[1])][::-1]
                obj = stack.pop()
                stack.append(obj[resolve_multiname(abc.constant_pool, args[0])](*call_args))
            elif op == "dup":
                stack.append(stack[-1])
            elif op == "pop":
                stack.pop()
            elif op in ("iftrue", "iffalse", "jump"):
                value = None if op == "jump" else stack.pop()
                if op == "jump" or bool(value) == (op == "iftrue"):
                    pc += args[0]
            elif op in ("add", "subtract", "lessthan", "greaterthan", "equals", "strictequals"):
                right, left = stack.pop(), stack.pop()
                stack.append({"add": lambda: left + right, "subtract": lambda: left - right, "lessthan": lambda: left < right,
                              "greaterthan": lambda: left > right, "equals": lambda: left == right,
                              "strictequals": lambda: type(left) is type(right) and left == right}[op]())
            elif op in ("coerce_i", "coerce_u", "convert_d"):
                value = stack.pop()
                if op == 'convert_d':
                    stack.append(float(value))
                else:
                    value = int(value) & 0xffffffff
                    stack.append(value - 2 ** 32 if op == 'coerce_i' and value >= 2 ** 31 else value)
            else:
                raise AssertionError(f"Reference VM does not implement AVM2 {op}")
    return traces, calls


@pytest.mark.parametrize("target", ["as2", "as3"])
def test_native_loops_break_continue_and_short_circuit(target):
    source = 'var n:int = 0; false && mark(); true || mark(); while(n < 5) { n++; if(n == 2) {continue;} trace(n); if(n == 3) {break;} } trace("España 🌍");'
    traces, calls = execute(source, target)
    assert traces == [1, 3, "España 🌍"]
    assert calls == []


@pytest.mark.parametrize("target", ["as2", "as3"])
def test_calls_are_values_and_postfix_keeps_original_value(target):
    traces, calls = execute('var n:int = 1; trace(n++); trace(++n); trace(mark() || false);', target)
    assert traces == [1, 3, True]
    assert calls == [True]


@pytest.mark.parametrize("source", ["class A {}", "var x = @;", "var x = [1,,2];", "break;", "return ?;"])
def test_unsupported_or_malformed_source_fails_before_assembly(source):
    with pytest.raises((SyntaxError, ValueError)):
        compile_actionscript(source, "as3")


def test_typed_int_prefix_result_wraps_before_returning_value():
    traces, _ = execute('var n:int=2147483647; trace(++n); trace(n);', 'as3')
    assert traces == [-2147483648, -2147483648]
