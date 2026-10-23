"""Exception-region recovery from action sizes and ABC metadata/lowerings.

Patterns are validated before rewriting control transfers. Incompatible or
overlapping layouts fail closed; no names or source files from the corpus are
used to decide the output.
"""
from dataclasses import replace

from ..avm2 import resolve_multiname
from ..script_strings import parse_string
from .ir import Instruction, ReconstructionError, TryRegion, Value, name
from .lift import State


SKIP = {"label", "nop", "debugline", "debugfile", "debug", "kill", "popscope"}


def local_index(ins):
    return int(ins.op[-1]) if "_" in ins.op else ins.args[0]


def terminal_route(instructions, target):
    part = [i for i in instructions if i.pc >= target]
    value = None
    for ins in part[:20]:
        if ins.op in SKIP:
            continue
        if ins.op.startswith("getlocal"):
            if value is not None:
                return None
            value = local_index(ins)
        elif ins.op == "return":
            return ("return", value) if ins.args[0] == (value is not None) else None
        elif ins.op == "throw":
            return ("throw", value) if value is not None else None
        elif ins.op == "jump":
            return ("jump", ins.args[0]) if value is None else None
        else:
            return None
    return None


def as2_regions(instructions):
    # AVM1 inline function bodies may end exactly at a catch/finally boundary,
    # without an ActionEnd byte. Keep that valid fall-through as an explicit
    # inert exit, while rejecting sizes into the middle of an action or beyond
    # the available body. It must not make arbitrary missing targets valid.
    instructions = list(instructions)
    end = instructions[-1].end if instructions else 0
    boundaries = {ins.pc for ins in instructions} | {end}
    for ins in instructions:
        if ins.op == "try" and any(pc not in boundaries for pc in ins.args[2:]):
            raise ReconstructionError("Invalid AVM1 try/catch/finally action boundary; use P-code")
    if any(ins.op == "try" and ins.args[-1] == end for ins in instructions):
        instructions.append(Instruction(end, end, "end"))
    regions, seeds = [], {instructions[0].pc: State()} if instructions else {}
    for ins in instructions:
        if ins.op != "try":
            continue
        flags, caught, begin, try_end, catch_end, final_end = ins.args
        caught = "r" + caught[2:] if caught.startswith("r:") else parse_string(caught)
        if caught.startswith("$pyswf_temp_"):
            seeds[try_end] = State()
            continue
        catches = [(caught, "*", try_end, catch_end)] if flags & 1 else []
        region = TryRegion(ins.pc, try_end, catches, final_end,
                           catch_end if flags & 2 else None, final_end if flags & 2 else None)
        regions.append(region)
        if flags & 1:
            seeds[try_end] = State()
        if flags & 2:
            seeds[catch_end] = State()
    return instructions, regions, seeds


def as3_regions(instructions, lifter):
    entries = list(lifter.body.exceptions)
    by_pc = {ins.pc: i for i, ins in enumerate(instructions)}
    lifter.prepare(instructions)
    seeds = {instructions[0].pc: State()} if instructions else {}
    regions, replacements, ignored_entries = [], {}, set()
    if entries and not any(i.op == "newcatch" for i in instructions):
        for entry in entries:
            text = resolve_multiname(lifter.pool, entry.var_name).split("::")[-1] if entry.var_name else f"$exception_{entry.target_offset}"
            seeds[entry.target_offset] = State((name(text),), objects=tuple(sorted(lifter.seed_objects.items())))
        return instructions, regions, seeds
    # Flex/AIR finally: normal completion pushes -1, abrupt completions push
    # an index, all enter one finally block followed by lookupswitch.
    for index, entry in sorted(enumerate(entries), key=lambda item: item[1].to_offset - item[1].from_offset, reverse=True):
        if index in ignored_entries:
            continue
        if entry.exc_type or entry.var_name:
            continue
        handler = [i for i in instructions if i.pc >= entry.target_offset]
        first_transfer = next((i for i in handler[:40] if i.op in ("throw", "return", "jump", "branch", "lookup")), None)
        if first_transfer is not None and first_transfer.op in ("throw", "return"):
            # A forwarding catch-all belongs to an enclosing finally. A later
            # unrelated jump must not be mistaken for its own dispatch.
            continue
        jump = next((i for i in handler[:40] if i.op == "jump" and i.args[0] > entry.target_offset), None)
        if not jump:
            continue
        final_start = jump.args[0]
        dispatch = next((i for i in instructions if i.pc >= final_start and i.op == "lookup" and all(target < final_start for target in i.args[1]) and i.args[0] >= i.end), None)
        if not dispatch:
            continue
        effective = [replacements.get(ins.pc, ins) for ins in instructions]
        routes = {n: terminal_route(effective, target) for n, target in enumerate(dispatch.args[1])}
        if not routes or any(route is None for route in routes.values()) or not any(route[0] == "throw" for route in routes.values()):
            continue
        typed = [(n, e) for n, e in enumerate(entries) if n != index and entry.from_offset <= e.from_offset < e.to_offset <= entry.to_offset and e.target_offset < entry.to_offset]
        groups = {(e.from_offset, e.to_offset) for _, e in typed}
        if len(groups) > 1:
            # Keep nested catches as independent regions. Only the group
            # sharing this finally's protected entry is its own catch clause;
            # consuming every enclosed handler would flatten nested order.
            for left in groups:
                for right in groups:
                    overlap = max(left[0], right[0]) < min(left[1], right[1])
                    nested = left[0] <= right[0] and right[1] <= left[1] or right[0] <= left[0] and left[1] <= right[1]
                    if overlap and not nested:
                        raise ReconstructionError("Crossing typed handler ranges cannot be represented by nested try statements")
            outer = [group for group in groups if all(ins.op in SKIP for ins in instructions if entry.from_offset <= ins.pc < group[0])]
            selected = max(outer, key=lambda group: group[1]) if outer else None
            typed = [(n, e) for n, e in typed if (e.from_offset, e.to_offset) == selected]
            groups = {selected} if selected is not None else set()
        if typed:
            begin, stop = next(iter(groups))
            exits = [i.args[0] for i in instructions if stop <= i.pc < min(e.target_offset for _, e in typed) and i.op == "jump"]
            normal = exits[-1] if exits else entry.to_offset
            catches = []
            ordered = sorted(typed, key=lambda item: item[1].target_offset)
            for position, (n, caught) in enumerate(ordered):
                end = ordered[position + 1][1].target_offset if position + 1 < len(ordered) else normal
                catch_name = resolve_multiname(lifter.pool, caught.var_name).split("::")[-1] if caught.var_name else "error"
                typ = resolve_multiname(lifter.pool, caught.exc_type).replace("::", ".")
                catches.append((catch_name, typ, caught.target_offset, end))
                if not caught.exc_type and not caught.var_name:
                    part = [i for i in instructions if caught.target_offset <= i.pc < end]
                    from .ir import Block
                    probe = Block(caught.target_offset, part)
                    lifter.lift(probe, State((name(catch_name),), objects=tuple(sorted(lifter.seed_objects.items()))))
                    if len(probe.statements) == 1 and probe.statements[0].kind == "throw" and probe.statements[0].value.kind == "name" and probe.statements[0].value.text == catch_name:
                        catches.pop()  # compiler-generated forwarding catch
                    else:
                        raise ReconstructionError("Anonymous catch-all is not a proven forwarding handler")
                seeds[caught.target_offset] = State((name(catch_name),), objects=tuple(sorted(lifter.seed_objects.items())))
                ignored_entries.add(n)
        else:
            stop, normal, catches = entry.to_offset, entry.to_offset, []
        region = TryRegion(entry.from_offset, stop, catches, dispatch.args[0], final_start, dispatch.pc, normal_stop=normal)
        regions.append(region)
        ignored_entries.add(index)
        seeds[final_start] = State(scopes=tuple([name("this"), *lifter.seed_objects.values()]), objects=tuple(sorted(lifter.seed_objects.items())))
        for offset, ins in enumerate(instructions):
            if ins.op != "jump" or ins.args[0] != final_start:
                continue
            previous = offset - 1
            while previous >= 0 and instructions[previous].op in ("debugline", "debugfile", "debug"):
                previous -= 1
            selector = instructions[previous]
            if selector.op not in ("pushbyte", "pushshort", "pushint"):
                raise ReconstructionError("Finally completion index is not a proven literal")
            value = selector.args[0]
            if selector.op == "pushbyte" and value > 127:
                value -= 256
            if selector.op == "pushint":
                value = lifter.pool.integers[value]
            if entry.target_offset <= ins.pc < final_start:
                # Internal exception path is represented by the finally clause,
                # not emitted as a source-level catch-all and rethrow switch.
                continue
            if value == -1:
                replacements[selector.pc] = replace(selector, op="nop", args=())
                replacements[ins.pc] = replace(ins, args=(region.after,))
            elif value in routes:
                kind, operand = routes[value]
                if kind == "jump":
                    replacements[selector.pc] = replace(selector, op="nop", args=())
                    replacements[ins.pc] = replace(ins, args=(operand,))
                else:
                    replacements[selector.pc] = replace(selector, op="getlocal", args=(operand,)) if operand is not None else replace(selector, op="nop", args=())
                    replacements[ins.pc] = replace(ins, op=kind, args=(operand is not None,) if kind == "return" else ())
            else:
                raise ReconstructionError("Invalid finally completion index")
        # The handler and backwards completion trampolines are now represented
        # by catch/finally/return nodes. Keep their offsets as inert anchors so
        # metadata/region boundaries remain resolvable without executing them.
        for ins in instructions:
            if entry.target_offset <= ins.pc < final_start:
                replacements[ins.pc] = replace(ins, op="nop", args=())
        replacements[dispatch.pc] = replace(dispatch, op="jump", args=(region.after,))
    remaining = [(n, e) for n, e in enumerate(entries) if n not in ignored_entries]
    grouped = {}
    for index, entry in remaining:
        grouped.setdefault((entry.from_offset, entry.to_offset), []).append((index, entry))
    for (begin, stop), group in grouped.items():
        if any(not e.exc_type and not e.var_name for _, e in group):
            raise ReconstructionError("Unrecognized finally/catch-all lowering; use P-code")
        ordered = sorted(group, key=lambda item: item[1].target_offset)
        normal = next((i.args[0] for i in instructions if stop <= i.pc < ordered[0][1].target_offset and i.op == "jump"), None)
        if normal is None:
            raise ReconstructionError("No proven normal exit for catch region")
        catches = []
        for position, (_, caught) in enumerate(ordered):
            end = ordered[position + 1][1].target_offset if position + 1 < len(ordered) else normal
            text = resolve_multiname(lifter.pool, caught.var_name).split("::")[-1]
            catches.append((text, resolve_multiname(lifter.pool, caught.exc_type).replace("::", "."), caught.target_offset, end))
            seeds[caught.target_offset] = State((name(text),), objects=tuple(sorted(lifter.seed_objects.items())))
        regions.append(TryRegion(begin, stop, catches, normal, normal_stop=normal))
    return [replacements.get(ins.pc, ins) for ins in instructions], regions, seeds
