"""Python-only, fail-closed structured source reconstruction."""
from .ir import Block, Instruction, ReconstructionError, Statement, Switch, Value, blocks_for, literal, name
from .lift import Lifter, State, avm1_instructions, avm2_instructions
from .structure import Structurer, clean, source, scope_regions
from .regions import as2_regions, as3_regions


def register_index(ins):
    if ins.op.startswith(("getlocal_", "setlocal_")):
        return int(ins.op[-1])
    return ins.args[0] if ins.op in ("getlocal", "setlocal") else None


def fold_flex_switches(instructions, lifter):
    """Recover case expressions from Flex's compare -> index -> lookup lowering.

    No source/debug line text is used. The selector register must be killed at
    the table so replacing the internal dispatch cannot affect later reads.
    """
    result = list(instructions)
    for lookup in list(instructions):
        if lookup.op != "lookup":
            continue
        current = {ins.pc: i for i, ins in enumerate(result)}
        if lookup.pc not in current:
            continue
        end = current[lookup.pc]
        checks = []
        for index in range(max(0, end - 1200), end - 2):
            ins, value, jump = result[index:index + 3]
            if ins.op == "branch" and ins.args[0] == "ifstrictne" and value.op in ("pushshort", "pushbyte") and jump.op == "jump" and jump.args[0] <= lookup.pc:
                case = value.args[0]
                if 0 <= case < len(lookup.args[1]) and jump.args[0] in current:
                    checks.append((index, case, jump.args[0]))
        if not checks or len({join for _, _, join in checks}) != 1:
            continue
        first = checks[0][0]
        stores = [i for i in range(max(0, first - 24), first) if result[i].op.startswith("setlocal")]
        if not stores:
            continue
        store = stores[-1]
        index = register_index(result[store])
        if not any(ins.op == "kill" and ins.args[0] == index for ins in result[checks[-1][0]:end]):
            continue
        destinations = {ins.args[-1] for ins in result if ins.op in ("branch", "jump")}
        candidates = [i for i in range(max(0, store - 12), store + 1) if result[i].pc in destinations and result[i].pc > max(lookup.args[1])]
        if not candidates:
            continue
        begin = candidates[-1]
        prefix = Block(result[begin].pc, result[begin:store + 1])
        try:
            state = lifter.lift(prefix)
            if state.stack or len(prefix.statements) != 1 or prefix.statements[0].target.text != lifter.local(index).text:
                continue
            selector, cases = prefix.statements[0].value, []
            cursor = store + 1
            for branch, case, _ in checks:
                part = Block(result[cursor].pc, result[cursor:branch + 1])
                lifter.lift(part)
                cond = part.condition
                if part.statements or cond.kind != "binary" or cond.text != "!==":
                    raise ReconstructionError("Non-comparison switch dispatch")
                left, right = cond.args
                selected = lifter.local(index).text
                value = left if right.kind == "name" and right.text == selected else right if left.kind == "name" and left.text == selected else None
                if value is None:
                    raise ReconstructionError("Selector changed between case comparisons")
                cases.append((value, lookup.args[1][case]))
                cursor = current.get(result[branch].args[-1], branch + 3)
            spec = Switch(selector, cases, lookup.args[0])
            replacement = Instruction(result[begin].pc, lookup.end, "structured_switch", (spec,))
            result[begin:end + 1] = [replacement]
        except ReconstructionError:
            continue
    return result


def reconstruct(instructions, lifter, regions=(), seeds=None, completion_recovered=False, details=False):
    if len(instructions)>50000:
        raise ReconstructionError("Structured reconstruction instruction budget exceeded (50000); exact P-code retained")
    lifter.prepare(instructions)
    if lifter.target == "as3":
        instructions = fold_flex_switches(instructions, lifter)
    leaders = {p for r in regions for p in (r.start, r.stop, r.after, r.final_start, r.final_stop) if p is not None}
    leaders.update(p for r in regions for _, _, begin, end in r.catches for p in (begin, end))
    if lifter.body:
        leaders.update(p for e in lifter.body.exceptions for p in (e.from_offset, e.to_offset, e.target_offset))
    leaders.update(p for ins in instructions if ins.op == "try" for p in (ins.pc, *ins.args[2:]))
    blocks = blocks_for(instructions, leaders)
    if not blocks:
        return ("// empty method", set()) if details else "// empty method"
    for block in blocks.values():
        ins = block.instructions[-1]
        if ins.op == "structured_switch":
            spec = ins.args[0]
            block.successors = list(dict.fromkeys([*(p for _, p in spec.cases), spec.default]))
    lifter.lift_graph(blocks, seeds)
    if lifter.preloaded_initializers:
        blocks[min(blocks)].statements[:0] = lifter.preloaded_initializers
    native_completion = lifter.target == "as3" and lifter.body.exceptions and not regions or lifter.target == "as2" and any(ins.op == "try" and '$pyswf_temp_' in ins.args[1] for ins in instructions)
    if native_completion and not completion_recovered:
        from .completions import recover
        rewritten, regions, seeds = recover(instructions, blocks, lifter)
        return reconstruct(rewritten, lifter, regions, seeds, completion_recovered=True, details=details)
    for block in blocks.values():
        if block.instructions[-1].op == "structured_switch":
            block.switch, block.term = block.instructions[-1].args[0], "switch"
        # Stack-preservation temporaries whose only consumer is pop can be
        # discarded before choosing a for latch. Calls/getters are retained.
        reads = set().union(*(s.value.reads for b in blocks.values() for s in b.statements if s.value), *(b.condition.reads for b in blocks.values() if b.condition))
        block.statements = [s for s in block.statements if not (s.kind == "assign" and s.target.text.startswith("$value") and s.target.text not in reads and not s.value.effectful)]
    structurer = Structurer(blocks, regions, fresh=lifter.fresh)
    nodes = scope_regions(structurer.region(min(blocks)))
    nodes = clean(nodes, lifter.parameters, lifter.target == "as3", lifter.local_types)
    result = "\n".join(source(nodes))
    if details:
        from .structure import expressions
        return result, set().union(*(v.reads for v in expressions(nodes)))
    return result


def decompile_as3(abc, body, *, context=None, captured=(), details=False, document_scope="method"):
    if len(body.code) > 512000:
        raise ReconstructionError("Method exceeds the bounded source reconstruction size")
    instructions = avm2_instructions(body.code)
    if any(ins.op == "newclass" for ins in instructions):
        if document_scope != "script":
            raise ReconstructionError("Class bootstrap is represented by a separate script/class source document; method P-code retained")
        from .script_initializers import declaration_order
        from ..class_documents import reconstruct_class_sections
        order = declaration_order(abc, body)
        classes = {section["class_index"]: section for section in reconstruct_class_sections(abc, class_indices=set(order))}
        if any(classes[index]["completeness"] != "complete" for index in order):
            raise ReconstructionError("Script class declarations contain unsupported members; exact P-code retained")
        result = "\n".join(classes[index]["source"] for index in order)
        return (result, set()) if details else result
    lifter = Lifter("as3", abc, body, context=context, captured=captured)
    regions, seeds = [], None
    if body.exceptions:
        instructions, regions, seeds = as3_regions(instructions, lifter)
    return reconstruct(instructions, lifter, regions, seeds, details=details)


def decompile_as2(data, pool=None):
    if len(data) > 512000:
        raise ReconstructionError("Action stream exceeds the bounded source reconstruction size")
    instructions = avm1_instructions(data)
    instructions, regions, seeds = as2_regions(instructions)
    return reconstruct(instructions, Lifter("as2", pool=pool), regions, seeds)
