"""AVM1 constant-pool identities across branches and exception entries."""
from collections import deque
from .ir import ReconstructionError


def pool_values(payload):
    if len(payload) < 2:
        raise ReconstructionError("Truncated constant pool header")
    count, cursor, values = int.from_bytes(payload[:2], "little"), 2, []
    for _ in range(count):
        end = payload.find(b"\0", cursor)
        if end < 0:
            raise ReconstructionError("Malformed constant pool")
        values.append(payload[cursor:end].decode("utf-8"))
        cursor = end + 1
    if cursor != len(payload):
        raise ReconstructionError("Unexpected bytes after constant pool")
    return tuple(values)


def seed_pools(instructions, initial_pool):
    if not instructions:
        return {}
    by_pc = {ins.pc: ins for ins in instructions}
    handlers = []
    for ins in instructions:
        if ins.op == "try":
            flags, _, begin, try_end, catch_end, final_end = ins.args
            if flags & 1:
                handlers.append((begin, try_end, try_end))
            if flags & 2:
                handlers.append((begin, catch_end, catch_end))
    incoming = {instructions[0].pc: {tuple(initial_pool)}}
    pending = deque([(instructions[0].pc, tuple(initial_pool))])
    work = 0
    while pending:
        work += 1
        if work > 200000:
            raise ReconstructionError("Constant pool flow budget exceeded")
        pc, pool = pending.popleft()
        ins = by_pc[pc]
        after = pool_values(ins.args[0]) if ins.op == "constant_pool" else pool
        if ins.op == "with" and handlers:
            from .lift import avm1_instructions
            if any(child.op == "constant_pool" for child in avm1_instructions(ins.args[1])):
                raise ReconstructionError("Nested with constant-pool mutation crosses a handler; use P-code")
        edges = []
        if ins.op in ("branch", "jump"):
            edges.append(ins.args[-1])
        if ins.op not in ("jump", "return", "throw", "end"):
            edges.append(ins.end)
        # Exceptions can leave at any instruction in a protected range. Both
        # pre- and post-pool identities are retained rather than guessing when
        # an exception occurs around a pool-changing operation.
        exceptional = [target for begin, stop, target in handlers if begin <= pc < stop]
        for target, candidate in [*((target, after) for target in edges), *((target, pool) for target in exceptional), *((target, after) for target in exceptional)]:
            if target not in by_pc:
                continue
            choices = incoming.setdefault(target, set())
            if candidate not in choices:
                if len(choices) >= 64:
                    raise ReconstructionError("Constant pool identity budget exceeded")
                choices.add(candidate)
                pending.append((target, candidate))
    return {pc: next(iter(choices)) if len(choices) == 1 else None for pc, choices in incoming.items()}
