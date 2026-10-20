"""
Decompilador AVM1 mejorado: simulación de pila + CFG para if/else, loops,
try/catch, with, switch. Genera pseudo-AS2 legible.
"""
import struct
from collections import defaultdict

from ..avm1 import AVM1_OPCODES, parse_push_values, BLOCK_ACTIONS, _read_cstring


def _decode(data):
    """Devuelve lista de (pc, code, payload) y set de PCs destino de branch."""
    actions = []
    targets = set()
    i = 0
    while i < len(data):
        pc = i
        code = data[i]
        i += 1
        if code == 0:
            actions.append((pc, 0, b""))
            break
        if code < 0x80:
            actions.append((pc, code, b""))
        else:
            if i + 2 > len(data):
                break
            length = int.from_bytes(data[i:i + 2], "little")
            i += 2
            payload = data[i:i + length]
            i += length
            actions.append((pc, code, payload))
            if code in (0x99, 0x9D):  # jump, if
                off = int.from_bytes(payload[0:2], "little", signed=True)
                targets.add(pc + 5 + off)
            elif code in BLOCK_ACTIONS:
                # define_function, define_function2, with - block ends after inline code
                if len(payload) >= 2:
                    code_size = int.from_bytes(payload[-2:], "little")
                    targets.add(pc + 3 + len(payload) + code_size)
    return actions, targets


_BINOPS = {
    "add": "+", "add2": "+", "subtract": "-", "multiply": "*", "divide": "/",
    "modulo": "%", "bit_and": "&", "bit_or": "|", "bit_xor": "^",
    "bit_lshift": "<<", "bit_rshift": ">>", "bit_urshift": ">>>",
    "equals": "==", "equals2": "==", "strict_equals": "===",
    "less_than": "<", "less2": "<", "greater": ">", "and": "&&", "or": "||",
    "string_equals": "==", "string_add": "+",
}

_UNOPS = {"not": "!"}
_INCOPS = {"increment": "++", "decrement": "--"}


class AVMBasicBlock:
    def __init__(self, start_pc):
        self.start_pc = start_pc
        self.actions = []
        self.successors = []
        self.predecessors = []
        self.end_pc = None
        self.loop_header = False

    def add_action(self, pc, code, payload):
        self.actions.append((pc, code, payload))
        self.end_pc = pc


def build_avm1_cfg(actions):
    """Construye CFG para AVM1."""
    if not actions:
        return {}, None
    pc_to_idx = {pc: i for i, (pc, _, _) in enumerate(actions)}

    def add_leader(leaders, pc):
        if pc in pc_to_idx:
            leaders.add(pc)
    
    leaders = {actions[0][0]}
    for pc, code, payload in actions:
        mn = AVM1_OPCODES.get(code, f"action_0x{code:02X}")
        if mn in ("if", "jump") and len(payload) >= 2:
            off = int.from_bytes(payload[0:2], "little", signed=True)
            targets = pc + 5 + off
            add_leader(leaders, targets)
            # fall-through
            idx = pc_to_idx[pc]
            if idx + 1 < len(actions):
                leaders.add(actions[idx + 1][0])
        elif mn in ("define_function", "define_function2", "with"):
            # Block action - after inline code
            if len(payload) >= 2:
                code_size = int.from_bytes(payload[-2:], "little")
                target = pc + 3 + len(payload) + code_size
                add_leader(leaders, target)
            idx = pc_to_idx[pc]
            if idx + 1 < len(actions):
                leaders.add(actions[idx + 1][0])
        elif code == 0:  # end
            idx = pc_to_idx[pc]
            if idx + 1 < len(actions):
                leaders.add(actions[idx + 1][0])
    
    sorted_leaders = sorted(leaders)
    blocks = {pc: AVMBasicBlock(pc) for pc in sorted_leaders}
    
    current = None
    for pc, code, payload in actions:
        if pc in leaders:
            current = blocks[pc]
        current.add_action(pc, code, payload)
    
    # Conectar
    for block in blocks.values():
        last_pc, last_code, last_payload = block.actions[-1]
        mn = AVM1_OPCODES.get(last_code, f"action_0x{last_code:02X}")
        
        if mn == "if" and len(last_payload) >= 2:
            target = last_pc + 5 + int.from_bytes(last_payload[0:2], "little", signed=True)
            if target in blocks:
                block.successors.append(blocks[target])
                blocks[target].predecessors.append(block)
            # fall-through
            idx = pc_to_idx[last_pc]
            if idx + 1 < len(actions):
                next_pc = actions[idx + 1][0]
                if next_pc in blocks:
                    block.successors.append(blocks[next_pc])
                    blocks[next_pc].predecessors.append(block)
        elif mn == "jump" and len(last_payload) >= 2:
            target = last_pc + 5 + int.from_bytes(last_payload[0:2], "little", signed=True)
            if target in blocks:
                block.successors.append(blocks[target])
                blocks[target].predecessors.append(block)
        elif mn in ("define_function", "define_function2", "with"):
            if len(last_payload) >= 2:
                code_size = int.from_bytes(last_payload[-2:], "little")
                target = last_pc + 3 + len(last_payload) + code_size
                if target in blocks:
                    block.successors.append(blocks[target])
                    blocks[target].predecessors.append(block)
            idx = pc_to_idx[last_pc]
            if idx + 1 < len(actions):
                next_pc = actions[idx + 1][0]
                if next_pc in blocks:
                    block.successors.append(blocks[next_pc])
                    blocks[next_pc].predecessors.append(block)
        elif last_code == 0:  # end
            pass
        else:
            idx = pc_to_idx[last_pc]
            if idx + 1 < len(actions):
                next_pc = actions[idx + 1][0]
                if next_pc in blocks:
                    block.successors.append(blocks[next_pc])
                    blocks[next_pc].predecessors.append(block)
    
    # Detectar loops (back edges)
    entry = blocks[actions[0][0]]
    visited = set()
    stack = set()
    
    def dfs(b):
        visited.add(b)
        stack.add(b)
        for s in b.successors:
            if s not in visited:
                dfs(s)
            elif s in stack:
                s.loop_header = True
        stack.remove(b)
    
    dfs(entry)
    
    return blocks, entry


def decompile_avm1(data, constant_pool=None):
    from .native import decompile_as2
    from ..avm1 import disassemble_avm1
    try:
        return decompile_as2(data, constant_pool), None
    except Exception as error:
        return "// Native reconstruction incomplete; exact P-code follows\n" + disassemble_avm1(data), str(error)


def _legacy_decompile_avm1(data, constant_pool=None):
    """
    Decompila AVM1 a pseudo-AS2 con control flow.
    Devuelve (source, error).
    """
    actions, targets = _decode(data)
    if not actions:
        return "// empty", None
    
    pool = list(constant_pool or [])
    blocks, entry = build_avm1_cfg(actions)
    
    # Verificar si hay try/catch (no soportado en v1 de forma completa)
    has_try = False
    for pc, code, payload in actions:
        if code == 0x8E:  # define_function2 puede tener try
            # Simplificación: asumimos que si hay exception handlers en el ABC...
            pass
    
    ctx = AVM1DecompileContext(pool, blocks, entry)
    try:
        lines = decompile_avm1_block(ctx, entry)
        return "\n".join(lines), None
    except Exception as e:
        # Fallback a desassembly simple
        return fallback_disassemble(actions, pool), f"fallback: {e}"


class AVM1DecompileContext:
    def __init__(self, pool, blocks, entry):
        self.pool = pool
        self.blocks = blocks
        self.entry = entry
        self.visited = set()
        self.indent = 0
        self.stack = []
    
    def ind(self):
        return "    " * self.indent
    
    def push(self, expr):
        self.stack.append(expr)
    
    def pop(self):
        return self.stack.pop() if self.stack else "undefined"
    
    def pop_e(self):
        return self.pop()


def decompile_avm1_block(ctx, block):
    if block in ctx.visited:
        return []
    ctx.visited.add(block)
    
    lines = []
    
    # Procesar acciones del bloque
    for pc, code, payload in block.actions:
        mn = AVM1_OPCODES.get(code, f"action_0x{code:02X}")
        
        if mn == "constant_pool":
            count = int.from_bytes(payload[0:2], "little")
            off = 2
            ctx.pool = []
            for _ in range(count):
                end = payload.find(b"\x00", off)
                if end == -1:
                    break
                ctx.pool.append(payload[off:end].decode("utf-8", errors="replace"))
                off = end + 1
        elif mn == "push":
            for tok in parse_push_values(payload):
                if tok.startswith("c:"):
                    idx = int(tok[2:])
                    ctx.push('"' + (ctx.pool[idx] if idx < len(ctx.pool) else f"c{idx}") + '"')
                elif tok.startswith("r:"):
                    ctx.push(f"r{tok[2:]}")
                else:
                    ctx.push(tok)
        elif mn == "get_variable":
            name = ctx.pop()
            nm = name[1:-1] if name.startswith('"') else name
            ctx.push(nm)
        elif mn == "set_variable":
            val = ctx.pop()
            name = ctx.pop()
            nm = name[1:-1] if name.startswith('"') else name
            lines.append(f"{ctx.ind()}{nm} = {val};")
        elif mn == "get_member":
            member = ctx.pop()
            obj = ctx.pop()
            mm = member[1:-1] if member.startswith('"') else f"[{member}]"
            ctx.push(f"{obj}.{mm}" if member.startswith('"') else f"{obj}[{member}]")
        elif mn == "set_member":
            val = ctx.pop()
            member = ctx.pop()
            obj = ctx.pop()
            if member.startswith('"'):
                lines.append(f"{ctx.ind()}{obj}.{member[1:-1]} = {val};")
            else:
                lines.append(f"{ctx.ind()}{obj}[{member}] = {val};")
        elif mn == "trace":
            lines.append(f"{ctx.ind()}trace({ctx.pop()});")
        elif mn in _BINOPS:
            b = ctx.pop()
            a = ctx.pop()
            ctx.push(f"({a} {_BINOPS[mn]} {b})")
        elif mn in _UNOPS:
            ctx.push(f"{_UNOPS[mn]}({ctx.pop()})")
        elif mn in _INCOPS:
            var = ctx.pop()
            ctx.push(f"{_INCOPS[mn]}{var}")
        elif mn == "call_function":
            name = ctx.pop()
            argc = ctx.pop()
            try:
                n = int(argc)
            except ValueError:
                n = 0
            args = [ctx.pop() for _ in range(n)][::-1]
            nm = name[1:-1] if name.startswith('"') else name
            ctx.push(f"{nm}({', '.join(args)})")
        elif mn == "call_method":
            method = ctx.pop()
            obj = ctx.pop()
            argc = ctx.pop()
            try:
                n = int(argc)
            except ValueError:
                n = 0
            args = [ctx.pop() for _ in range(n)][::-1]
            mm = method[1:-1] if method.startswith('"') else method
            ctx.push(f"{obj}.{mm}({', '.join(args)})")
        elif mn == "pop":
            if ctx.stack:
                expr = ctx.pop()
                if expr.endswith(")"):
                    lines.append(f"{ctx.ind()}{expr};")
        elif mn == "define_local":
            val = ctx.pop()
            name = ctx.pop()
            nm = name[1:-1] if name.startswith('"') else name
            lines.append(f"{ctx.ind()}var {nm} = {val};")
        elif mn in ("define_function", "define_function2"):
            try:
                name, off = _read_cstring(payload, 0)
                count = int.from_bytes(payload[off:off+2], "little")
                off += 2
                params = []
                if mn == "define_function":
                    for _ in range(count):
                        p, off = _read_cstring(payload, off)
                        params.append(p)
                else: # define_function2
                    off += 3 # skip reg_count and flags
                    for _ in range(count):
                        off += 1 # skip reg
                        p, off = _read_cstring(payload, off)
                        params.append(p)
                param_str = ", ".join(params)
                if name:
                    lines.append(f"{ctx.ind()}function {name}({param_str}) {{")
                else:
                    lines.append(f"{ctx.ind()}function({param_str}) {{")
                ctx.indent += 1
            except Exception:
                lines.append(f"{ctx.ind()}function() {{")
                ctx.indent += 1
        elif mn == "with":
            obj = ctx.pop()
            lines.append(f"{ctx.ind()}with ({obj}) {{")
            ctx.indent += 1
        elif mn in ("stop", "play", "next_frame", "prev_frame", "stop_sounds"):
            lines.append(f"{ctx.ind()}{mn}();")
        elif mn == "goto_frame":
            frame = int.from_bytes(payload[0:2], "little")
            lines.append(f"{ctx.ind()}gotoAndStop({frame});")
        elif mn == "goto_label":
            label = payload[:-1].decode("utf-8", errors="ignore")
            lines.append(f"{ctx.ind()}gotoAndPlay(\"{label}\");")
        elif mn == "return":
            if ctx.stack:
                lines.append(f"{ctx.ind()}return {ctx.pop()};")
            else:
                lines.append(f"{ctx.ind()}return;")
        elif code == 0:  # end
            if ctx.indent > 0:
                ctx.indent -= 1
                lines.append(f"{ctx.ind()}}}")
        else:
            lines.append(f"{ctx.ind()}// {mn}")
    
    # Control flow
    if block.successors:
        if block.loop_header:
            # while loop
            lines.append(f"{ctx.ind()}while (true) {{")
            ctx.indent += 1
            for succ in block.successors:
                if succ != block:  # evita auto-loop infinito en output
                    lines.extend(decompile_avm1_block(ctx, succ))
            ctx.indent -= 1
            lines.append(f"{ctx.ind()}}}")
            return lines
        
        if len(block.successors) == 2:
            # if/else
            last_pc, last_code, last_payload = block.actions[-1]
            mn = AVM1_OPCODES.get(last_code, "")
            if mn == "if":
                cond = ctx.pop()  # La condición debería estar en stack
                lines.append(f"{ctx.ind()}if ({cond}) {{")
                ctx.indent += 1
                
                # Determinar then/else por target del branch
                target = last_pc + 5 + int.from_bytes(last_payload[0:2], "little", signed=True)
                then_block = None
                else_block = None
                for succ in block.successors:
                    if succ.start_pc == target:
                        then_block = succ
                    else:
                        else_block = succ
                
                if then_block and then_block not in ctx.visited:
                    lines.extend(decompile_avm1_block(ctx, then_block))
                ctx.indent -= 1
                lines.append(f"{ctx.ind()}}}")
                
                if else_block and else_block not in ctx.visited:
                    # Verificar si else es solo jump a after
                    if not (len(else_block.actions) == 1 and 
                           AVM1_OPCODES.get(else_block.actions[0][1], "") == "jump"):
                        lines.append(f"{ctx.ind()}else {{")
                        ctx.indent += 1
                        lines.extend(decompile_avm1_block(ctx, else_block))
                        ctx.indent -= 1
                        lines.append(f"{ctx.ind()}}}")
                return lines
        
        # Single successor
        for succ in block.successors:
            if succ not in ctx.visited:
                lines.extend(decompile_avm1_block(ctx, succ))
    
    return lines


def fallback_disassemble(actions, pool):
    """Desassembly simple como fallback."""
    lines = ["// Fallback disassembly:"]
    for pc, code, payload in actions:
        mn = AVM1_OPCODES.get(code, f"action_0x{code:02X}")
        if code < 0x80:
            lines.append(f"    {mn}")
        else:
            args = []
            if mn == "push":
                for tok in parse_push_values(payload):
                    if tok.startswith("c:"):
                        idx = int(tok[2:])
                        args.append('"' + (pool[idx] if idx < len(pool) else f"c{idx}") + '"')
                    else:
                        args.append(tok)
            elif mn in ("jump", "if"):
                off = int.from_bytes(payload[0:2], "little", signed=True)
                args.append(f"L_{pc + 5 + off}")
            elif mn == "goto_frame":
                args.append(str(int.from_bytes(payload[0:2], "little")))
            elif mn == "constant_pool":
                count = int.from_bytes(payload[0:2], "little")
                args.append(f"[{count} strings]")
            else:
                args.append(payload.hex())
            lines.append(f"    {mn} {' '.join(args)}")
    return "\n".join(lines)
