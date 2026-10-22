"""
Decompilador AVM2 mejorado: análisis de flujo de control con CFG,
reconstrucción de if/else, while/for, try/catch, switch.
"""
from collections import defaultdict
from ..avm2 import ByteReader, resolve_multiname
from ..avm2_opcodes import OPCODES


def _decode(pool, code):
    """Decodifica bytecode a lista de instrucciones (pc, mnemonic, args)."""
    reader = ByteReader(code)
    instrs = []
    targets = set()
    while reader.offset < len(code):
        pc = reader.offset
        try:
            opcode = reader.read_byte()
        except EOFError:
            break
        if opcode not in OPCODES:
            instrs.append((pc, f"raw_0x{opcode:02X}", []))
            continue
        mnemonic, arg_types = OPCODES[opcode]
        args = []
        try:
            if mnemonic == "lookupswitch":
                default = reader.read_s24()
                count = reader.read_u30()
                cases = [reader.read_s24() for _ in range(count + 1)]
                args = [default, count, cases]
                targets.add(pc + default)
                for c in cases:
                    targets.add(pc + c)
            else:
                for at in arg_types:
                    if at == "u30":
                        args.append(reader.read_u30())
                    elif at == "s24":
                        v = reader.read_s24()
                        args.append(v)
                        targets.add(pc + 4 + v)
                    elif at == "byte":
                        value = reader.read_byte()
                        args.append(value - 256 if mnemonic == "pushbyte" and value >= 128 else value)
        except EOFError:
            pass
        instrs.append((pc, mnemonic, args))
    return instrs, targets


_BINOPS = {
    "add": "+", "add_i": "+", "subtract": "-", "subtract_i": "-",
    "multiply": "*", "multiply_i": "*", "divide": "/", "modulo": "%",
    "lshift": "<<", "rshift": ">>", "urshift": ">>>",
    "bitand": "&", "bitor": "|", "bitxor": "^",
    "equals": "==", "strictequals": "===", "lessthan": "<", "lessequals": "<=",
    "greaterthan": ">", "greaterequals": ">=",
}

_BRANCH_OPS = {
    "iftrue", "iffalse", "ifeq", "ifne", "iflt", "ifle", "ifgt", "ifge",
    "ifstricteq", "ifstrictne", "ifnlt", "ifnle", "ifngt", "ifnge",
}

_BRANCH_INVERSE = {
    "iffalse": "iftrue", "iftrue": "iffalse",
    "ifeq": "ifne", "ifne": "ifeq",
    "iflt": "ifge", "ifle": "ifgt",
    "ifgt": "ifle", "ifge": "iflt",
    "ifstricteq": "ifstrictne", "ifstrictne": "ifstricteq",
    "ifnlt": "ifnge", "ifnle": "ifngt",
    "ifngt": "ifnle", "ifnge": "ifnlt",
}

_UNOPS = {"not": "!", "negate": "-", "bitnot": "~"}
_INCOPS = {"increment": "++", "decrement": "--", "increment_i": "++", "decrement_i": "--"}


class BasicBlock:
    def __init__(self, start_pc):
        self.start_pc = start_pc
        self.instructions = []
        self.successors = []
        self.predecessors = []
        self.end_pc = None
        self.loop_header = False
        self.dominators = set()
        self.immediate_dominator = None

    def add_instr(self, pc, mn, args):
        self.instructions.append((pc, mn, args))
        self.end_pc = pc

    def __repr__(self):
        return f"BB({self.start_pc}-{self.end_pc})"


def build_cfg(instrs):
    """Construye bloques básicos y CFG a partir de instrucciones lineales."""
    if not instrs:
        return {}, None
    pc_to_idx = {pc: i for i, (pc, _, _) in enumerate(instrs)}

    def add_leader(leaders, pc):
        # Obfuscated/corrupt ABC frequently contains a branch outside the
        # method. Never create an empty block for such a target.
        if pc in pc_to_idx:
            leaders.add(pc)

    def branch_target(pc, args):
        return pc + 4 + args[0] if args else None
    
    # Identificar líderes (inicios de bloques)
    leaders = {instrs[0][0]}
    for pc, mn, args in instrs:
        if mn in _BRANCH_OPS:
            target = branch_target(pc, args)
            if target is not None:
                add_leader(leaders, target)
            # La instrucción después del branch también es líder
            idx = pc_to_idx[pc]
            if idx + 1 < len(instrs):
                leaders.add(instrs[idx + 1][0])
        elif mn == "jump":
            target = branch_target(pc, args)
            if target is not None:
                add_leader(leaders, target)
        elif mn == "lookupswitch":
            if len(args) == 3:
                default, _, cases = args
                add_leader(leaders, pc + default)
                for c in cases:
                    add_leader(leaders, pc + c)
            idx = pc_to_idx[pc]
            if idx + 1 < len(instrs):
                leaders.add(instrs[idx + 1][0])
        elif mn in ("throw", "returnvalue", "returnvoid"):
            idx = pc_to_idx[pc]
            if idx + 1 < len(instrs):
                leaders.add(instrs[idx + 1][0])
    
    # Crear bloques
    sorted_leaders = sorted(leaders)
    blocks = {pc: BasicBlock(pc) for pc in sorted_leaders}
    
    # Asignar instrucciones a bloques
    current_block = None
    for pc, mn, args in instrs:
        if pc in leaders:
            current_block = blocks[pc]
        current_block.add_instr(pc, mn, args)
    
    # Conectar bloques (successors/predecessors)
    for block in blocks.values():
        last_pc, last_mn, last_args = block.instructions[-1]
        
        if last_mn in _BRANCH_OPS:
            target = branch_target(last_pc, last_args)
            if target in blocks:
                block.successors.append(blocks[target])
                blocks[target].predecessors.append(block)
            # Fall-through
            idx = pc_to_idx[last_pc]
            if idx + 1 < len(instrs):
                next_pc = instrs[idx + 1][0]
                if next_pc in blocks:
                    block.successors.append(blocks[next_pc])
                    blocks[next_pc].predecessors.append(block)
        elif last_mn == "jump":
            target = branch_target(last_pc, last_args)
            if target in blocks:
                block.successors.append(blocks[target])
                blocks[target].predecessors.append(block)
        elif last_mn == "lookupswitch" and len(last_args) == 3:
            default, _, cases = last_args
            for t in [default] + cases:
                target = last_pc + t
                if target in blocks:
                    block.successors.append(blocks[target])
                    blocks[target].predecessors.append(block)
        elif last_mn not in ("throw", "returnvalue", "returnvoid"):
            idx = pc_to_idx[last_pc]
            if idx + 1 < len(instrs):
                next_pc = instrs[idx + 1][0]
                if next_pc in blocks:
                    block.successors.append(blocks[next_pc])
                    blocks[next_pc].predecessors.append(block)
    
    # Orden topológico para dominadores
    entry = blocks[instrs[0][0]]
    compute_dominators(blocks, entry)
    detect_loops(blocks, entry)
    
    return blocks, entry


def compute_dominators(blocks, entry):
    """Algoritmo de dominadores (Lengauer-Tarjan simplificado)."""
    all_blocks = list(blocks.values())
    for b in all_blocks:
        b.dominators = set(all_blocks)
    entry.dominators = {entry}
    
    changed = True
    while changed:
        changed = False
        for b in all_blocks:
            if b is entry:
                continue
            preds = [p for p in b.predecessors if p in blocks.values()]
            if not preds:
                new_doms = {b}
            else:
                new_doms = {b} | set.intersection(*[p.dominators for p in preds])
            if new_doms != b.dominators:
                b.dominators = new_doms
                changed = True
    
    # Dominador inmediato
    for b in all_blocks:
        if b is entry:
            b.immediate_dominator = None
        else:
            candidates = b.dominators - {b}
            if candidates:
                b.immediate_dominator = max(candidates, key=lambda x: len(x.dominators))


def detect_loops(blocks, entry):
    """Detecta cabeceras de bucles (back edges)."""
    visited = set()
    stack = set()
    
    def dfs(b):
        visited.add(b)
        stack.add(b)
        for s in b.successors:
            if s not in visited:
                dfs(s)
            elif s in stack:
                # Back edge: s domina b => s es cabecera de bucle
                s.loop_header = True
        stack.remove(b)
    
    dfs(entry)


class DecompileContext:
    def __init__(self, pool, blocks, entry, exceptions):
        self.pool = pool
        self.blocks = blocks
        self.entry = entry
        self.exceptions = exceptions
        self.block_output = {}
        self.visited = set()
        self.indent = 0
        self.if_stack = []  # Para rastrear if/else
        self.loop_stack = []  # Para rastrear while/do-while

    def ind(self):
        return "    " * self.indent


def format_expr(expr, prec=100):
    """Formatea expresión con precedencia (placeholder para pretty-print)."""
    return expr


def decompile_block(ctx, block):
    """Decompila un bloque básico a líneas de código."""
    if block in ctx.visited:
        return []
    ctx.visited.add(block)
    
    lines = []
    stack = []
    
    def push(expr, prec=100):
        stack.append((expr, prec))
    
    def pop():
        return stack.pop() if stack else ("_", 100)
    
    def pop_e():
        return pop()[0]
    
    # Procesar instrucciones del bloque
    for pc, mn, args in block.instructions:
        # Manejar labels
        if mn == "label":
            continue
        
        if mn in ("getlocal_0", "getlocal_1", "getlocal_2", "getlocal_3"):
            reg = int(mn[-1])
            push("this" if reg == 0 else f"local{reg}")
        elif mn == "getlocal":
            push("this" if args[0] == 0 else f"local{args[0]}")
        elif mn in ("setlocal_0", "setlocal_1", "setlocal_2", "setlocal_3"):
            reg = int(mn[-1])
            val = pop_e()
            lines.append(f"{ctx.ind()}local{reg} = {val};")
        elif mn == "setlocal":
            val = pop_e()
            lines.append(f"{ctx.ind()}local{args[0]} = {val};")
        elif mn == "pushbyte" or mn == "pushshort":
            push(str(args[0]))
        elif mn == "pushint":
            push(str(ctx.pool.integers[args[0]] if args[0] < len(ctx.pool.integers) else 0))
        elif mn == "pushuint":
            push(str(ctx.pool.uintegers[args[0]] if args[0] < len(ctx.pool.uintegers) else 0))
        elif mn == "pushdouble":
            push(str(ctx.pool.doubles[args[0]] if args[0] < len(ctx.pool.doubles) else 0))
        elif mn == "pushstring":
            s = ctx.pool.strings[args[0]] if args[0] < len(ctx.pool.strings) else ""
            push('"' + s.replace('"', '\\"') + '"')
        elif mn == "pushtrue":
            push("true")
        elif mn == "pushfalse":
            push("false")
        elif mn == "pushnull":
            push("null")
        elif mn == "pushundefined":
            push("undefined")
        elif mn == "pushnan":
            push("NaN")
        elif mn == "dup":
            top = stack[-1] if stack else ("_", 100)
            stack.append(top)
        elif mn == "pop":
            if stack:
                expr = pop_e()
                if expr.endswith(")"):
                    lines.append(f"{ctx.ind()}{expr};")
        elif mn in ("coerce_a", "coerce_s", "coerce_d", "coerce_i", "coerce_u",
                    "convert_i", "convert_u", "convert_d", "convert_b", "convert_s",
                    "convert_o", "coerce_b", "checkfilter", "nop",
                    "pushscope", "popscope", "pushwith"):
            pass
        elif mn == "coerce":
            pass
        elif mn == "getlex":
            push(resolve_multiname(ctx.pool, args[0]))
        elif mn in ("findpropstrict", "findproperty"):
            push("")
        elif mn == "getproperty":
            obj = pop_e()
            name = resolve_multiname(ctx.pool, args[0]).split("::")[-1]
            push(f"{obj}.{name}" if obj else name, prec=90)
        elif mn in ("setproperty", "initproperty"):
            val = pop_e()
            obj = pop_e()
            name = resolve_multiname(ctx.pool, args[0]).split("::")[-1]
            target = f"{obj}.{name}" if obj else name
            lines.append(f"{ctx.ind()}{target} = {val};")
        elif mn in ("callproperty", "callproplex", "callpropvoid"):
            argc = args[1]
            call_args = [pop_e() for _ in range(argc)][::-1]
            obj = pop_e()
            name = resolve_multiname(ctx.pool, args[0]).split("::")[-1]
            target = f"{obj}.{name}" if obj else name
            call = f"{target}({', '.join(call_args)})"
            if mn == "callpropvoid":
                lines.append(f"{ctx.ind()}{call};")
            else:
                push(call, prec=90)
        elif mn == "constructprop":
            argc = args[1]
            call_args = [pop_e() for _ in range(argc)][::-1]
            obj = pop_e()
            name = resolve_multiname(ctx.pool, args[0]).split("::")[-1]
            push(f"new {name}({', '.join(call_args)})", prec=90)
        elif mn == "constructsuper":
            argc = args[0]
            call_args = [pop_e() for _ in range(argc)][::-1]
            pop_e()
            lines.append(f"{ctx.ind()}super({', '.join(call_args)});")
        elif mn == "returnvalue":
            lines.append(f"{ctx.ind()}return {pop_e()};")
        elif mn == "returnvoid":
            lines.append(f"{ctx.ind()}return;")
        elif mn in _BINOPS:
            b = pop_e()
            a = pop_e()
            push(f"({a} {_BINOPS[mn]} {b})", prec=50)
        elif mn in _UNOPS:
            push(f"{_UNOPS[mn]}({pop_e()})", prec=60)
        elif mn in _INCOPS:
            # ++x / --x
            var = pop_e()
            op = _INCOPS[mn]
            lines.append(f"{ctx.ind()}{op}{var};")
        elif mn == "newarray":
            items = [pop_e() for _ in range(args[0])][::-1]
            push(f"[{', '.join(items)}]")
        elif mn == "newobject":
            pairs = []
            for _ in range(args[0]):
                v = pop_e()
                k = pop_e()
                pairs.append(f"{k}: {v}")
            push("{" + ", ".join(pairs[::-1]) + "}")
        elif mn in ("throw",):
            lines.append(f"{ctx.ind()}throw {pop_e()};")
        elif mn == "lookupswitch":
            if len(args) == 3:
                block.switch_selector = pop_e()
                block.switch_default = args[0]
                block.switch_cases = list(args[2])
        elif mn in _BRANCH_OPS:
            if mn in ("ifeq", "ifne", "iflt", "ifle", "ifgt", "ifge",
                      "ifstricteq", "ifstrictne", "ifnlt", "ifnle", "ifngt", "ifnge"):
                right = pop_e()
                left = pop_e()
                operators = {
                    "ifeq": "==", "ifne": "!=", "iflt": "<", "ifle": "<=",
                    "ifgt": ">", "ifge": ">=", "ifstricteq": "===",
                    "ifstrictne": "!==", "ifnlt": ">=", "ifnle": ">",
                    "ifngt": "<=", "ifnge": "<",
                }
                block.branch_condition = f"({left} {operators[mn]} {right})"
            else:
                block.branch_condition = pop_e()
            block.branch_mnemonic = mn
        elif mn == "jump":
            block.unconditional_target = pc + 4 + args[0]
        else:
            lines.append(f"{ctx.ind()}// UNKNOWN: {mn} {args}")
    
    # Manejar successors (control flow)
    if block.successors:
        if block.instructions and block.instructions[-1][1] == "lookupswitch":
            return handle_switch(ctx, block, lines)
        # Loop header
        if block.loop_header:
            return handle_loop(ctx, block, lines)
        
        # If/else
        if len(block.successors) == 2:
            return handle_if_else(ctx, block, lines)
        
        # Single successor (fall-through or jump)
        if len(block.successors) == 1:
            succ = block.successors[0]
            if succ not in ctx.visited:
                lines.extend(decompile_block(ctx, succ))
    
    return lines


def handle_switch(ctx, block, lines):
    """Emit a lookup switch while preserving case targets and diagnostics."""
    selector = getattr(block, "switch_selector", "value")
    lines.append(f"{ctx.ind()}switch ({selector}) {{")
    ctx.indent += 1
    for case_index, relative in enumerate(getattr(block, "switch_cases", [])):
        target_pc = block.instructions[-1][0] + relative
        target = ctx.blocks.get(target_pc)
        lines.append(f"{ctx.ind()}case {case_index}:")
        ctx.indent += 1
        if target is None:
            lines.append(f"{ctx.ind()}// invalid target L_{target_pc};")
        elif target not in ctx.visited:
            lines.extend(decompile_block(ctx, target))
        ctx.indent -= 1
    default_pc = block.instructions[-1][0] + getattr(block, "switch_default", 0)
    default = ctx.blocks.get(default_pc)
    lines.append(f"{ctx.ind()}default:")
    ctx.indent += 1
    if default is None:
        lines.append(f"{ctx.ind()}// invalid target L_{default_pc};")
    elif default not in ctx.visited:
        lines.extend(decompile_block(ctx, default))
    ctx.indent -= 1
    ctx.indent -= 1
    lines.append(f"{ctx.ind()}}}")
    return lines


def handle_loop(ctx, block, lines):
    """Detecta y emite while/do-while/for loops."""
    # Buscar el latch (bloque que salta de vuelta a la cabecera)
    latch = None
    for pred in block.predecessors:
        if pred != block:
            # Verificar si pred termina en jump hacia block
            last_mn = pred.instructions[-1][1] if pred.instructions else None
            if last_mn == "jump":
                pred_args = pred.instructions[-1][2]
                if not pred_args:
                    continue
                target = pred.instructions[-1][0] + 4 + pred_args[0]
                if target == block.start_pc:
                    latch = pred
                    break
    
    if latch:
        def can_reach(start, target):
            pending = [start]
            seen = {block}
            while pending:
                current = pending.pop()
                if current is target:
                    return True
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(current.successors)
            return False

        body_candidates = [succ for succ in block.successors if can_reach(succ, latch)]
        body = body_candidates[0] if body_candidates else None
        exits = [succ for succ in block.successors if succ is not body]
        if body is None:
            return lines

        # Build the condition for the path that enters the body. For iffalse
        # the fall-through path is the true path; for relational branches the
        # stored predicate already represents the taken branch.
        condition = getattr(block, "branch_condition", "true")
        last_mn = block.instructions[-1][1] if block.instructions else ""
        branch_args = block.instructions[-1][2]
        target_pc = block.instructions[-1][0] + 4 + branch_args[0] if branch_args else None
        body_is_target = body.start_pc == target_pc
        if last_mn == "iffalse":
            if body_is_target:
                condition = f"!({condition})"
        elif last_mn == "iftrue":
            if not body_is_target:
                condition = f"!({condition})"
        elif not body_is_target:
            condition = f"!({condition})"

        lines.append(f"{ctx.ind()}while ({condition}) {{")
        ctx.indent += 1
        lines.extend(decompile_block(ctx, body))
        ctx.indent -= 1
        lines.append(f"{ctx.ind()}}}")
        for exit_block in exits:
            if exit_block not in ctx.visited:
                lines.extend(decompile_block(ctx, exit_block))
        return lines
    
    # Fallback
    for succ in block.successors:
        if succ not in ctx.visited:
            lines.extend(decompile_block(ctx, succ))
    return lines


def handle_if_else(ctx, block, lines):
    """Reconstruye if/else a partir de branch condicional + fall-through."""
    # El último instr del block es el branch condicional
    last_pc, last_mn, last_args = block.instructions[-1]
    
    if last_mn not in _BRANCH_OPS or not last_args:
        # No es un branch condicional, tratar como fall-through
        for succ in block.successors:
            if succ not in ctx.visited:
                lines.extend(decompile_block(ctx, succ))
        return lines
    
    # La simulación de pila del bloque guarda la condición real.
    cond = getattr(block, "branch_condition", "condition")
    
    # Identificar then/else blocks. AVM2's iffalse branch target is the
    # *false* arm, so invert the successor roles while keeping the readable
    # source condition intact (``if (x)`` instead of ``if (!x)``).
    then_block = None
    else_block = None
    
    # El target del branch es uno, el fall-through es el otro
    branch_target = last_pc + 4 + last_args[0]
    fallthrough_pc = None
    idx = list(ctx.blocks.keys()).index(last_pc) if last_pc in ctx.blocks else -1
    target_block = None
    fallthrough_block = None
    for succ in block.successors:
        if succ.start_pc == branch_target:
            target_block = succ
        else:
            fallthrough_block = succ

    if last_mn == "iffalse":
        then_block, else_block = fallthrough_block, target_block
    else:
        then_block, else_block = target_block, fallthrough_block
    
    if then_block is None and else_block is None:
        # Fallback
        for succ in block.successors:
            if succ not in ctx.visited:
                lines.extend(decompile_block(ctx, succ))
        return lines
    
    # Emitir if
    lines.append(f"{ctx.ind()}if ({cond}) {{")
    ctx.indent += 1
    if then_block and then_block not in ctx.visited:
        lines.extend(decompile_block(ctx, then_block))
    ctx.indent -= 1
    lines.append(f"{ctx.ind()}}}")
    
    if else_block and else_block not in ctx.visited:
        # Verificar si else_block es solo un jump a after-if
        if len(else_block.instructions) == 1 and else_block.instructions[0][1] == "jump":
            # else vacío, no emitir
            pass
        else:
            lines.append(f"{ctx.ind()}else {{")
            ctx.indent += 1
            lines.extend(decompile_block(ctx, else_block))
            ctx.indent -= 1
            lines.append(f"{ctx.ind()}}}")
    
    return lines


def decompile_method(abc, body, method_name="method", *, include_script_declarations=False):
    from .native import decompile_as3
    try:
        return decompile_as3(abc, body, document_scope="script" if include_script_declarations else "method"), None
    except Exception as error:
        return None, f"Native reconstruction incomplete: {error}; use exact P-code"


def _legacy_decompile_method(abc, body, method_name="method"):
    """
    Decompila un MethodBodyInfo a pseudo-AS3 con control flow.
    Devuelve (source, error).
    """
    pool = abc.constant_pool
    
    if body.exceptions:
        # Intentar decompilar con try/catch
        try:
            return decompile_with_exceptions(abc, body, method_name)
        except Exception as e:
            return None, f"try/catch decompilation failed: {e}"
    
    instrs, _ = _decode(pool, body.code)
    if not instrs:
        return "// empty method", None
    
    blocks, entry = build_cfg(instrs)
    
    ctx = DecompileContext(pool, blocks, entry, body.exceptions)
    ctx.blocks = blocks
    
    try:
        lines = decompile_block(ctx, entry)
        return "\n".join(lines), None
    except Exception as e:
        return None, f"decompilation error: {e}"


def decompile_with_exceptions(abc, body, method_name):
    """Decompilación estructurada de métodos con bloques try/catch."""
    pool = abc.constant_pool
    instrs, _ = _decode(pool, body.code)
    if not instrs:
        return "// empty method", None

    blocks, entry = build_cfg(instrs)
    ctx = DecompileContext(pool, blocks, entry, body.exceptions)
    ctx.blocks = blocks

    # Si hay excepciones definidas, reconstruir la estructura try/catch
    if body.exceptions:
        lines = []
        for exc in body.exceptions:
            from_pc = getattr(exc, "from_offset", getattr(exc, "from_pc", 0))
            to_pc = getattr(exc, "to_offset", getattr(exc, "to_pc", 0))
            target_pc = getattr(exc, "target_offset", getattr(exc, "target_pc", 0))
            
            exc_type_name = "Error"
            if hasattr(exc, "exc_type") and exc.exc_type:
                try:
                    exc_type_name = resolve_multiname(pool, exc.exc_type)
                except Exception:
                    exc_type_name = "Error"

            var_name = "error"
            if hasattr(exc, "var_name") and exc.var_name:
                try:
                    var_name = resolve_multiname(pool, exc.var_name)
                except Exception:
                    var_name = "e"

            try_instrs = [instr for instr in instrs if from_pc <= instr[0] < to_pc]
            catch_instrs = [instr for instr in instrs if instr[0] >= target_pc]

            lines.append("try {")
            if try_instrs:
                try_blocks, try_entry = build_cfg(try_instrs)
                try_ctx = DecompileContext(pool, try_blocks, try_entry, [])
                try_ctx.indent = 1
                try_lines = decompile_block(try_ctx, try_entry)
                lines.extend(try_lines)
            else:
                lines.append("    // body")
            lines.append(f"}} catch ({var_name}:{exc_type_name}) {{")
            if catch_instrs:
                catch_blocks, catch_entry = build_cfg(catch_instrs)
                catch_ctx = DecompileContext(pool, catch_blocks, catch_entry, [])
                catch_ctx.indent = 1
                catch_lines = decompile_block(catch_ctx, catch_entry)
                lines.extend(catch_lines)
            lines.append("}")
            return "\n".join(lines), None

    lines = decompile_block(ctx, entry)
    return "\n".join(lines), None



# ============================================================
# Fallback: desensamblador simple para métodos no decompilables
# ============================================================

def disassemble_method(abc, body):
    """Genera desassembly legible como fallback."""
    pool = abc.constant_pool
    from ..avm2 import disassemble_instructions
    return disassemble_instructions(pool, body.code)
