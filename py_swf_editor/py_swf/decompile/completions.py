"""Reverse the native compiler's validated pending-completion lowering.

Recognition uses graph predicates, exception ranges, and protected exits, not
source caches. Unproven patterns leave the method read-only with a diagnostic.
"""
from dataclasses import replace

from ..script_strings import parse_string
from ..avm1 import parse_push_values
from ..avm2 import resolve_multiname
from .ir import Instruction, ReconstructionError, TryRegion, Value, name, render
from .lift import State


def assignment_range(instructions, stmt, target):
    end = next(i for i, ins in enumerate(instructions) if ins.pc == stmt.pc)
    if instructions[end].op.startswith("setlocal"):
        if end < 1 or instructions[end - 1].op not in ("pushbyte", "pushshort", "pushint"):
            raise ReconstructionError("Completion store is not a literal")
        return instructions[end - 1:end + 1]
    if instructions[end].op not in ("set_variable", "define_local"):
        raise ReconstructionError("Unrecognized completion store")
    begin = end
    while begin > 0 and end - begin < 5:
        begin -= 1
        part = instructions[begin:end + 1]
        if any(ins.op not in ("push", "stack_swap", "set_variable", "define_local") for ins in part):
            break
        tokens = [tok for ins in part if ins.op == "push" for tok in parse_push_values(ins.args[0])]
        if len(tokens) == 2 and any(tok.startswith('"') and parse_string(tok) == target for tok in tokens):
            return part
    raise ReconstructionError("Completion assignment has observable or unrecognized instructions")


def guard_regions(instructions, blocks, lifter):
    # Unreachable guards (e.g. finally always returns) still describe the
    # protected body's original exits. Lift only guard-like blocks on demand.
    for block in blocks.values():
        if block.condition is None and block.instructions[-1].op == "branch":
            try:
                lifter.lift(block, State(objects=tuple(sorted(lifter.seed_objects.items()))))
            except ReconstructionError:
                pass
    guards = {}
    for pc, block in blocks.items():
        cond = block.condition
        if block.term != "branch" or not cond or cond.kind != "binary" or cond.text != "===":
            continue
        left, right = cond.args
        if left.kind != "name" or right.kind != "literal" or right.literal != 1:
            continue
        variable, cursor, routes, members = left.text, pc, {}, set()
        while cursor in blocks and cursor not in members:
            current = blocks[cursor]
            value = current.condition
            if current.term != "branch" or not value or value.kind != "binary" or value.text != "===" or value.args[0].kind != "name" or value.args[0].text != variable or value.args[1].kind != "literal":
                break
            members.add(cursor)
            routes[value.args[1].literal] = current.successors[0]
            cursor = current.successors[1]
        if cursor not in blocks or blocks[cursor].statements or len(blocks[cursor].successors) != 1:
            continue
        after = blocks[cursor].successors[0]
        if after <= max(members):
            continue
        # First completion must rethrow the stored exception, not merely be
        # a user-written numeric if chain which happens to use the same shape.
        thrown = blocks.get(routes[1])
        if thrown and not thrown.statements:
            try:
                lifter.lift(thrown)
            except ReconstructionError:
                continue
        if not thrown or len(thrown.statements) != 1 or thrown.statements[-1].kind != "throw" or thrown.statements[-1].value.kind != "name":
            continue
        guards[variable] = {"start": cond.origin, "after": after, "routes": routes, "block": pc}
    return guards


def recover(instructions, blocks, lifter):
    guards = guard_regions(instructions, blocks, lifter)
    if not guards:
        raise ReconstructionError("Unrecognized native exception completion guards")
    regions, replacements, seeds = [], {}, {instructions[0].pc: State()}
    routes = {}
    def resolve(variable, code, trail=()):
        key = (variable, code)
        if key in routes:
            return routes[key]
        if key in trail or len(trail) > 32:
            raise ReconstructionError("Cyclic completion forwarding")
        pc = guards[variable]["routes"][code]
        block = blocks.get(pc)
        if block is None:
            raise ReconstructionError("Invalid completion target")
        if not block.statements:
            lifter.lift(block, State(objects=tuple(sorted(lifter.seed_objects.items()))))
        meaningful = block.statements
        if meaningful and meaningful[-1].kind in ("return", "throw"):
            if len(meaningful) != 1:
                raise ReconstructionError("Completion trampoline contains side effects")
            result = (meaningful[-1].kind, meaningful[-1].value)
        elif meaningful and meaningful[-1].kind == "assign" and meaningful[-1].target.text in guards and meaningful[-1].value.kind == "literal":
            result = resolve(meaningful[-1].target.text, meaningful[-1].value.literal, (*trail, key))
        elif not meaningful and len(block.successors) == 1:
            result = ("jump", block.successors[0])
        else:
            raise ReconstructionError("Unrecognized completion trampoline")
        routes[key] = result
        return result

    for variable, guard in guards.items():
        for code in guard["routes"]:
            resolve(variable, code)
        if lifter.target == "as3":
            failed = []
            for entry in lifter.body.exceptions:
                block = blocks.get(entry.target_offset)
                if block and any(s.kind == "assign" and s.target.text == variable and s.value.kind == "literal" and s.value.literal == 1 for s in block.statements):
                    failed.append(entry)
            if not failed:
                raise ReconstructionError("No exception handler proves completion guard ownership")
            target = failed[0].target_offset
            associated = [e for e in lifter.body.exceptions if e.target_offset == target]
            user = [e for e in lifter.body.exceptions if e.var_name and any(e.target_offset == f.from_offset for f in associated)]
            if not user:
                primary = min(associated, key=lambda e: e.from_offset)
                begin, stop = primary.from_offset, primary.to_offset
            else:
                begin, stop = min((e.from_offset, e.to_offset) for e in user)
                user = [e for e in user if (e.from_offset, e.to_offset) == (begin, stop)]
            normal_block = blocks.get(stop)
            if not normal_block or len(normal_block.successors) != 1:
                raise ReconstructionError("No proven normal finally entry")
            final = normal_block.successors[0]
            catches = []
            for caught in user:
                extent = next(f for f in associated if f.from_offset == caught.target_offset)
                text = resolve_multiname(lifter.pool, caught.var_name).split("::")[-1]
                catches.append((text, resolve_multiname(lifter.pool, caught.exc_type).replace("::", "."), caught.target_offset, extent.to_offset))
                seeds[caught.target_offset] = State((name(text),), objects=tuple(sorted(lifter.seed_objects.items())))
            for ins in instructions:
                if target <= ins.pc < final:
                    replacements[ins.pc] = replace(ins, op="nop", args=())
        else:
            primary_candidates = []
            initializers = [s.pc for b in blocks.values() for s in b.statements if s.kind == "assign" and s.target.text == variable and s.value.kind == "literal" and s.value.literal == 0]
            for ins in instructions:
                if ins.op != "try":
                    continue
                _, caught, begin_body, end_body, end_catch, _ = ins.args
                caught_name = parse_string(caught) if not caught.startswith("r:") else "r" + caught[2:]
                if not caught_name.startswith("$pyswf_temp_"):
                    continue
                if not any(pc < ins.pc for pc in initializers):
                    continue
                writes = [s for b in blocks.values() if end_body <= b.pc < end_catch for s in b.statements if s.kind == "assign" and s.target.text == variable and s.value.kind == "literal" and s.value.literal == 1]
                if writes:
                    primary_candidates.append(ins)
            if not primary_candidates:
                raise ReconstructionError("No ActionTry proves completion guard ownership")
            primary = min(primary_candidates, key=lambda i: i.pc)
            _, caught, body_begin, stop, final, _ = primary.args
            begin = primary.pc
            caught_name = parse_string(caught)
            catches = []
            helpers = [ins for ins in instructions if ins.op == "try" and stop <= ins.pc < final]
            direct = [ins for ins in helpers if not any(other.pc < ins.pc < other.args[5] for other in helpers)]
            for helper in direct:
                _, _, catch_begin, catch_stop, helper_end, _ = helper.args
                catch_name = lifter.unique_name(f"$caught_{helper.pc}")
                typ = "*"
                for block in blocks.values():
                    cond = block.condition
                    if not cond or not stop <= block.pc < helper.pc:
                        continue
                    if cond.kind == "unary" and cond.text == "!":
                        cond = cond.args[0]
                    if cond.kind == "binary" and cond.text == "instanceof" and cond.args[0].kind == "name" and cond.args[0].text == caught_name:
                        typ = render(cond.args[1])
                catches.append((catch_name, typ, helper.pc, catch_stop))
                lifter.name_aliases.append((caught_name, catch_name, catch_begin, catch_stop))
                seeds[helper.pc] = State()
        if final >= guard["start"]:
            # Empty finally starts with an anchor nop before the first guard.
            if final != guard["start"]:
                raise ReconstructionError("Finally boundaries overlap completion dispatch")
        region = TryRegion(begin, stop, catches, guard["after"], final, guard["start"], route_variable=variable, normal_stop=final)
        regions.append(region)
        seeds[final] = State(scopes=(name("this"),) if lifter.target == "as3" else (), objects=tuple(sorted(lifter.seed_objects.items())))
        for ins in instructions:
            if guard["start"] <= ins.pc < guard["after"]:
                replacements[ins.pc] = replace(ins, op="nop", args=())
        # Only the immediately preceding initialization belongs to this try.
        initial = [(s.pc, s) for b in blocks.values() for s in b.statements if s.kind == "assign" and s.target.text == variable and s.value.kind == "literal" and s.value.literal == 0 and s.pc < begin]
        if not initial:
            raise ReconstructionError("Missing pending-completion initializer")
        stmt = max(initial)[1]
        for ins in assignment_range(instructions, stmt, variable):
            replacements[ins.pc] = replace(ins, op="nop", args=())
    # Turn each protected route write + exit into its original abrupt completion.
    for block in blocks.values():
        if not block.statements or block.instructions[-1].op != "jump":
            continue
        stmt = block.statements[-1]
        if stmt.kind != "assign" or stmt.target.text not in guards or stmt.value.kind != "literal":
            continue
        variable, code = stmt.target.text, stmt.value.literal
        if code not in guards[variable]["routes"] or code == 1:
            continue
        kind, value = resolve(variable, code)
        for ins in assignment_range(instructions, stmt, variable):
            replacements[ins.pc] = replace(ins, op="nop", args=())
        jump = block.instructions[-1]
        replacements[jump.pc] = replace(jump, args=(value,)) if kind == "jump" else replace(jump, op="structured_exit", args=(kind, value))
    return [replacements.get(ins.pc, ins) for ins in instructions], regions, seeds
