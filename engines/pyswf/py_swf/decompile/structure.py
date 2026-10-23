"""Bounded region structuring; joins are emitted once, outside their branches."""
from dataclasses import dataclass, field, replace

from .ir import ReconstructionError, Statement, Switch, Value, binary, literal, name, negate, render
from .flow_graph import postdominators, nearest_common_postdom


@dataclass
class Node:
    kind: str
    condition: Value | None = None
    body: list = field(default_factory=list)
    other: list = field(default_factory=list)
    initial: list = field(default_factory=list)
    update: list = field(default_factory=list)
    cases: list = field(default_factory=list)
    catches: list = field(default_factory=list)
    finalizer: list = field(default_factory=list)


def source(nodes, level=0):
    lines, indent = [], "    " * level
    for node in nodes:
        if isinstance(node, Statement):
            text = node.source()
            if text:
                lines.extend(indent + line for line in text.splitlines())
            continue
        if node.kind in ("if", "while", "for", "with"):
            header = render(node.condition)
            if node.kind == "for":
                initial = ", ".join(s.source().rstrip(";") for s in node.initial)
                update = ", ".join(s.source().rstrip(";") for s in node.update)
                header = f"{initial}; {header}; {update}"
            lines.append(f"{indent}{node.kind} ({header}) {{")
            lines.extend(source(node.body, level + 1))
            lines.append(indent + "}")
            if node.other:
                lines[-1] += " else {"
                lines.extend(source(node.other, level + 1))
                lines.append(indent + "}")
        elif node.kind == "switch":
            lines.append(f"{indent}switch ({render(node.condition)}) {{")
            for case, body in node.cases:
                lines.append(indent + "    " + ("default:" if case is None else "case " + render(case) + ":"))
                lines.extend(source(body, level + 2))
            lines.append(indent + "}")
        elif node.kind in ("forin", "foreach"):
            binding = node.initial[0]
            target = ("var " if binding.declared else "") + render(binding.target)
            if binding.declared and binding.type_name:
                target += ":" + binding.type_name
            keyword = "for each" if node.kind == "foreach" else "for"
            lines.append(f"{indent}{keyword} ({target} in {render(node.condition)}) {{")
            lines.extend(source(node.body, level + 1))
            lines.append(indent + "}")
        elif node.kind == "try":
            lines.append(indent + "try {")
            lines.extend(source(node.body, level + 1))
            lines.append(indent + "}")
            for caught, typ, body in node.catches:
                annotation = ":" + typ if typ and typ != "*" else ""
                lines[-1] += f" catch ({caught}{annotation}) {{"
                lines.extend(source(body, level + 1))
                lines.append(indent + "}")
            if node.finalizer:
                lines[-1] += " finally {"
                lines.extend(source(node.finalizer, level + 1))
                lines.append(indent + "}")
        else:
            raise ReconstructionError(f"Unknown region {node.kind}")
    return lines


def scope_regions(nodes):
    """Pair lexical with boundaries after CFG structuring, never across arms."""
    result, scopes = [], []
    current = result
    for node in nodes:
        if isinstance(node, Statement) and node.kind == "scope_enter":
            region = Node("with", node.value)
            current.append(region)
            scopes.append(current)
            current = region.body
        elif isinstance(node, Statement) and node.kind == "scope_exit":
            if not scopes:
                raise ReconstructionError("Unbalanced dynamic scope exit")
            current = scopes.pop()
        else:
            if isinstance(node, Node):
                node.body = scope_regions(node.body)
                node.other = scope_regions(node.other)
                node.finalizer = scope_regions(node.finalizer)
                node.cases = [(c, scope_regions(b)) for c, b in node.cases]
                node.catches = [(n, t, scope_regions(b)) for n, t, b in node.catches]
            current.append(node)
    if scopes:
        raise ReconstructionError("Dynamic scope exits cross control-flow arms; use P-code")
    return result


def expressions(nodes):
    for node in nodes:
        if isinstance(node, Statement):
            if node.value:
                yield node.value
            if node.target and node.target.kind != "name":
                yield node.target
        else:
            if node.condition:
                yield node.condition
            yield from expressions(node.body + node.other + node.initial + node.update + node.finalizer)
            for case, body in node.cases:
                if case:
                    yield case
                yield from expressions(body)
            for _, _, body in node.catches:
                yield from expressions(body)


def clean(nodes, parameters=(), as3=False, local_types=None):
    """Remove only unused pure compiler temporaries; do not delete effects."""
    reads = set().union(*(v.reads for v in expressions(nodes)))
    declared, types = set(parameters), local_types or {}
    def walk(items):
        result = []
        for node in items:
            if isinstance(node, Statement):
                if node.kind == "assign" and node.target.kind == "name":
                    key = node.target.text
                    if key.startswith(("$value", "$pyswf_temp_")) and key not in reads and not node.value.effectful:
                        continue
                    if as3 and key != "this" and key not in declared or node.declared:
                        node.declared = key not in declared
                        declared.add(key)
                    typ = types.get(key)
                    if typ in ("int", "uint", "Number", "String", "Boolean", "Object"):
                        node.type_name = typ
                        if node.value.kind == "cast" and node.value.text == typ:
                            node.value = node.value.args[0]
                result.append(node)
            else:
                node.initial = walk(node.initial)
                node.update = walk(node.update)
                node.body, node.other = walk(node.body), walk(node.other)
                node.cases = [(c, walk(b)) for c, b in node.cases]
                node.catches = [(n, t, walk(b)) for n, t, b in node.catches]
                node.finalizer = walk(node.finalizer)
                result.append(node)
        return result
    return walk(nodes)


class Structurer:
    def __init__(self, blocks, regions=(), fresh=None):
        if len(blocks) > 2000:
            raise ReconstructionError("Method exceeds the bounded structuring limit (2000 blocks)")
        self.all_regions = tuple(regions)
        self.region_groups = {}
        for region in regions:
            self.region_groups.setdefault(region.start, []).append(region)
        for group in self.region_groups.values():
            group.sort(key=lambda region: (region.normal_stop or region.after, region.stop), reverse=True)
        self.blocks, self.regions = blocks, {start: group[0] for start, group in self.region_groups.items()}
        self.fresh = fresh
        self.claimed, self.steps = set(), 0
        self.controls, self.active_regions = [], set()
        self.omitted = {}
        self.boundaries = []
        self.postdom_cache = {}
        self.covered_try_headers = {r.start for r in regions} | {begin for r in regions for _, _, begin, _ in r.catches}
        self.predecessors = {pc: set() for pc in blocks}
        for pc, block in blocks.items():
            for target in block.successors:
                if target in self.predecessors:
                    self.predecessors[target].add(pc)
        self.loops = self.natural_loops()
        # A compiler still emits hasnext2/nextvalue for a for-in body whose
        # every path returns or throws. Such a body has no back edge and thus
        # is absent from natural_loops, but its enumeration is still real.
        # Existing binding/initialization proofs below apply to these regions.
        for pc, block in blocks.items():
            if pc in self.loops or block.term != "branch" or not block.condition or block.condition.kind != "enumerate" or len(block.successors) != 2:
                continue
            inside, outside = block.successors
            members = self.reachable(inside, {pc, outside})
            if not members or outside in members:
                continue
            exits = [(m, target) for m in members for target in blocks[m].successors if target not in members]
            terminals = [blocks[m] for m in members if not blocks[m].successors]
            if not exits and terminals and all(b.term == "exit" for b in terminals) and all(self.predecessors[m] <= (members | {pc}) for m in members):
                self.loops[pc] = {pc, *members}
        self.fold_switches()

    def reachable(self, start, forbidden=(), allowed=None):
        seen, todo = set(), [start]
        while todo:
            pc = todo.pop()
            if pc in seen or pc in forbidden or pc not in self.blocks or allowed is not None and pc not in allowed:
                continue
            seen.add(pc)
            todo.extend(self.blocks[pc].successors)
        return seen

    def natural_loops(self):
        entry = min(self.blocks)
        edges = {pc: list(block.successors) for pc, block in self.blocks.items()}
        # A source-level return/throw inside try still executes finally, which
        # may itself continue/break. These implicit edges are needed to find
        # the enclosing loop after the completion dispatch has been reversed.
        for pc, block in self.blocks.items():
            containing = [r for r in self.all_regions if r.final_start is not None and (r.start <= pc < r.stop or any(begin <= pc < end for _, _, begin, end in r.catches))]
            if block.term == "exit" and containing:
                region = min(containing, key=lambda r: r.stop - r.start)
                edges[pc].extend((region.final_start, region.after))
        preds = {pc: set() for pc in self.blocks}
        for pc, targets in edges.items():
            for target in targets:
                if target in preds:
                    preds[target].add(pc)
        reached, todo = set(), [entry]
        while todo:
            pc = todo.pop()
            if pc not in reached and pc in edges:
                reached.add(pc)
                todo.extend(edges[pc])
        dom = {pc: ({pc} if pc == entry else set(reached)) for pc in reached}
        changed = True
        while changed:
            changed = False
            for pc in sorted(reached):
                if pc == entry:
                    continue
                parents = preds[pc] & reached
                updated = {pc} | (set.intersection(*(dom[p] for p in parents)) if parents else set())
                if updated != dom[pc]:
                    dom[pc], changed = updated, True
        loops = {}
        for tail in reached:
            for head in edges[tail]:
                if head in dom[tail]:
                    members, todo = {head, tail}, [tail] if tail != head else []
                    while todo:
                        for predecessor in preds[todo.pop()] & reached:
                            if predecessor not in members:
                                members.add(predecessor)
                                if predecessor != head:
                                    todo.append(predecessor)
                    loops.setdefault(head, set()).update(members)
        return loops

    def fold_switches(self):
        for start, block in list(self.blocks.items()):
            if start in self.claimed or block.switch or block.term != "branch":
                continue
            checks, selector, pc, visited = [], None, start, set()
            while pc in self.blocks and pc not in visited:
                current = self.blocks[pc]
                cond = current.condition
                if current.term != "branch" or not cond or cond.kind != "binary" or cond.text != "===":
                    break
                left, right = cond.args
                candidates = [v for v in (left, right) if v.kind == "name" and (v.text.startswith(("local", "$register", "$pyswf_temp_")))]
                selected = next((v for v in candidates if selector is None or v.text == selector.text), None)
                if not selected or pc != start and current.statements:
                    break
                selector = selected
                case = right if selected is left else left
                if case.effectful:
                    break
                visited.add(pc)
                checks.append((case, current.successors[0]))
                pc = current.successors[1]
            captured_once = bool(selector and block.statements and block.statements[-1].kind == "assign" and block.statements[-1].target.text == selector.text)
            if not checks or len(checks) == 1 and not captured_once or pc not in self.blocks:
                continue
            last = self.blocks[pc]
            default = last.successors[0] if not last.statements and last.term == "jump" and len(last.successors) == 1 else pc
            if any(target in visited for _, target in checks):
                continue
            block.switch = Switch(selector, checks, default)
            block.term = "switch"
            block.successors = list(dict.fromkeys([*(target for _, target in checks), default]))
            self.claimed.update(visited - {start})
            if default != pc:
                self.claimed.add(pc)

    def join(self, starts, excluded=()):
        key = tuple(sorted(excluded))
        if key not in self.postdom_cache:
            self.postdom_cache[key] = postdominators({pc:b.successors for pc,b in self.blocks.items()}, excluded)
        proven = nearest_common_postdom(starts, self.postdom_cache[key])
        if proven is not None:
            return proven
        distances = []
        for start in starts:
            todo, found = [(start, 0)], {}
            while todo:
                pc, depth = todo.pop(0)
                if pc in found or pc in excluded or pc not in self.blocks:
                    continue
                found[pc] = depth
                todo.extend((target, depth + 1) for target in self.blocks[pc].successors)
            distances.append(found)
        common = set.intersection(*(set(d) for d in distances)) if distances else set()
        if not common:
            return None
        # Early return/throw paths need not pass through the normal join.
        # However a path bypassing a candidate and rejoining its nonterminal
        # tail proves it is NOT the join; choosing it would drop/duplicate code.
        edges = {pc:b.successors for pc,b in self.blocks.items()}
        from .flow_graph import reachable
        for candidate in sorted(common, key=lambda pc:(max(d[pc] for d in distances),sum(d[pc] for d in distances),pc)):
            tail = reachable(edges,candidate,excluded) - {candidate}
            tail = {pc for pc in tail if self.blocks[pc].term != "exit" or any(s.kind not in ("return","throw") for s in self.blocks[pc].statements)}
            if not any(tail & reachable(edges,start,{candidate,*excluded}) for start in starts):
                return candidate
        return None

    def transfer(self, target):
        nested_break = False
        for cont, brk in reversed(self.controls):
            if cont == target:
                return Statement("continue")
            if brk == target:
                if nested_break:
                    raise ReconstructionError("Multi-level break needs a label; use P-code")
                return Statement("break")
            nested_break = True
        return None

    def enumeration_registers_escape(self, start, registers):
        """Check the old cursor/object values until a kill or overwriting store.

        Compiler registers may be reused by a later enumeration. That later
        use is safe only when each old value is definitely killed/overwritten
        on the path before it is read again.
        """
        todo, seen = [(start, frozenset(registers))], set()
        while todo:
            pc, incoming = todo.pop()
            if (pc, incoming) in seen or pc not in self.blocks or not incoming:
                continue
            seen.add((pc, incoming))
            live = set(incoming)
            for ins in self.blocks[pc].instructions:
                if ins.op.startswith(("getlocal_", "setlocal_")):
                    index = int(ins.op[-1])
                elif ins.op in ("getlocal", "setlocal", "kill"):
                    index = ins.args[0]
                else:
                    index = None
                if ins.op.startswith("getlocal") and index in live or ins.op == "hasnext2" and live.intersection(ins.args):
                    return True
                if ins.op.startswith("setlocal") or ins.op == "kill":
                    live.discard(index)
            todo.extend((target, frozenset(live)) for target in self.blocks[pc].successors)
        return False

    def region(self, start, stop=None, allowed=None, suppress_loop=None):
        nodes, pc = [], start
        while pc is not None and pc != stop:
            self.steps += 1
            if self.steps > 10000:
                raise ReconstructionError("Structuring work budget exceeded")
            transfer = self.transfer(pc)
            if transfer and pc != start:
                nodes.append(transfer)
                break
            if pc not in self.blocks:
                if pc == stop:
                    break
                raise ReconstructionError(f"Invalid region target {pc}")
            if allowed is not None and pc not in allowed:
                if transfer:
                    nodes.append(transfer)
                    break
                raise ReconstructionError(f"Unstructured exit to {pc}")
            available_regions = [region for region in self.region_groups.get(pc, ()) if id(region) not in self.active_regions]
            if available_regions:
                region = available_regions[0]
                self.active_regions.add(id(region))
                node = Node("try")
                self.boundaries.append({r for r in (region.after, region.final_start, region.normal_stop) if r is not None})
                node.body = self.region(pc, region.stop, suppress_loop=suppress_loop)
                for caught, typ, begin, end in region.catches:
                    node.catches.append((caught, typ, self.region(begin, end)))
                self.boundaries.pop()
                if region.final_start is not None:
                    node.finalizer = self.region(region.final_start, region.final_stop)
                    if not node.finalizer:
                        # Keep an empty finally as syntax, not as a vanished clause.
                        node.finalizer = [Statement("expr", Value("raw", "undefined"))]
                self.active_regions.remove(id(region))
                nodes.append(node)
                pc = region.after
                continue
            if pc in self.claimed:
                raise ReconstructionError(f"Overlapping regions at {pc}; refusing to duplicate/drop code")
            block = self.blocks[pc]
            if block.term == "try" and pc not in self.covered_try_headers:
                raise ReconstructionError(f"Unrecognized try container at {pc}")
            if pc in self.loops and pc != suppress_loop:
                members = self.loops[pc]
                if block.condition and block.condition.kind == "enumerate":
                    inside = [p for p in block.successors if p in members]
                    outside = [p for p in block.successors if p not in members]
                    if len(inside) != 1 or len(outside) != 1 or block.statements:
                        raise ReconstructionError("Ambiguous enumeration loop")
                    entry = self.blocks[inside[0]]
                    binding = entry.statements[0] if entry.statements else None
                    value = binding.value if binding and binding.kind == "assign" else None
                    nullable_string = bool(value and value.kind == "enumeration_string")
                    if nullable_string:
                        # A property destination can evaluate its receiver
                        # before nextvalue/coerce_s. Moving that evaluation
                        # into the body would reorder getters/conversions.
                        if binding.target.kind != "name":
                            raise ReconstructionError("Nullable enumeration destination requires a proven local binding; use P-code")
                        value = value.args[0]
                    # Moving this assignment into the enumeration header
                    # removes its RHS. Until every conversion can be retained
                    # with the same evaluation order, even a single primitive
                    # cast must stay in P-code (int(11.9) is not 11.9).
                    if value and value.kind in ("cast", "primitive_conversion"):
                        raise ReconstructionError("Enumeration binding conversion cannot be preserved by the header; use P-code")
                    same = lambda a, b: a.kind == b.kind == "name" and a.text == b.text
                    if not value or value.kind != "next" or not all(same(a, b) for a, b in zip(value.args, block.condition.args)):
                        raise ReconstructionError("Enumeration body is not a proven nextname/nextvalue binding")
                    hidden = {v.text for v in block.condition.args}
                    if hidden & binding.target.reads or any(hidden & s.value.reads or s.kind == "assign" and s.target.kind == "name" and s.target.text in hidden for m in members for s in self.blocks[m].statements if s is not binding and s.value):
                        raise ReconstructionError("Enumeration registers have additional consumers")
                    raw_header = next((i for i in block.instructions if i.op == "hasnext2"), None)
                    if any(hidden & self.blocks[m].condition.reads for m in members - {pc} if self.blocks[m].condition) or raw_header is None or self.enumeration_registers_escape(outside[0], raw_header.args):
                        raise ReconstructionError("Enumeration registers escape the proven loop")
                    if len(nodes) < 2 or not all(isinstance(n, Statement) and n.kind == "assign" for n in nodes[-2:]):
                        raise ReconstructionError("Enumeration has no adjacent object/cursor initialization")
                    cursor, obj = nodes[-2:]
                    if cursor.target.text != block.condition.args[1].text or cursor.value.kind != "literal" or cursor.value.literal != 0 or obj.target.text != block.condition.args[0].text:
                        raise ReconstructionError("Enumeration initialization differs from compiler pattern")
                    del nodes[-2:]
                    self.claimed.add(pc)
                    if nullable_string:
                        if self.fresh is None:
                            raise ReconstructionError("Typed enumeration needs a hygienic temporary; use P-code")
                        # Preserve the original destination assignment in the
                        # body. Its old value therefore survives an empty loop,
                        # and members/setters still run after each conversion.
                        temporary = self.fresh(binding.pc)
                        binding.value = temporary
                        declaration = Statement("assign", Value("raw", "undefined"), temporary,
                                                binding.pc, True, "String")
                    else:
                        self.omitted[inside[0]] = {id(binding)}
                        declaration = Statement("assign", Value("raw", "undefined"), binding.target, binding.pc)
                    self.controls.append((pc, outside[0]))
                    body = self.region(inside[0], pc, members, suppress_loop=pc)
                    self.controls.pop()
                    nodes.append(Node("foreach" if value.text == "each" else "forin", obj.value, body, initial=[declaration]))
                    pc = outside[0]
                    continue
                if block.term != "branch" or block.statements or len([p for p in block.successors if p not in members]) != 1:
                    # General reducible loop: retain evaluation order and all
                    # internal break paths instead of inventing a for latch.
                    exits = {p for m in members for p in self.blocks[m].successors if p not in members}
                    if len(exits) != 1:
                        raise ReconstructionError(f"Loop has multiple unstructured exits at {pc}")
                    after = next(iter(exits))
                    self.controls.append((pc, after))
                    body = self.region(pc, None, members, suppress_loop=pc)
                    self.controls.pop()
                    nodes.append(Node("while", literal(True), body))
                    pc = after
                    continue
                if block.term != "branch" or len(block.successors) != 2:
                    raise ReconstructionError(f"Unstructured loop header at {pc}")
                inside = [p for p in block.successors if p in members]
                outside = [p for p in block.successors if p not in members]
                if len(inside) != 1 or len(outside) != 1 or block.statements:
                    raise ReconstructionError(f"Loop condition has statements/ambiguous exits at {pc}")
                self.claimed.add(pc)
                condition = block.condition if block.successors[0] == inside[0] else negate(block.condition)
                tails = [p for p in self.predecessors[pc] if p in members]
                update, update_pc = [], pc
                if len(tails) == 1:
                    tail = self.blocks[tails[0]]
                    if tail.statements:
                        candidate = tail.statements[-1]
                        if candidate.kind == "assign" and candidate.target.kind == "name":
                            snapshots = {statement.target.text: statement for statement in tail.statements[:-1] if statement.kind == "assign" and statement.target.kind == "name" and statement.target.text.startswith("$value")}
                            used_snapshots = set()
                            def inline_snapshot(value):
                                if value.kind == "name" and value.text in snapshots:
                                    used_snapshots.add(id(snapshots[value.text]))
                                    return inline_snapshot(snapshots[value.text].value)
                                return replace(value, args=tuple(inline_snapshot(argument) for argument in value.args))
                            expanded = inline_snapshot(candidate.value)
                            value = expanded.args[0] if expanded.kind in ("cast", "primitive_conversion") else expanded
                            left = value.args[0] if value.kind == "binary" else None
                            if left and left.kind in ("cast", "primitive_conversion") and left.text == "Number":
                                left = left.args[0]
                            if value.kind == "binary" and value.text in ("+", "-") and left.kind == "name" and left.text == candidate.target.text and candidate.target.text in condition.reads:
                                update = [replace(candidate, value=expanded)]
                                update_ids = {id(candidate), *used_snapshots}
                                update_pc = tails[0] if all(id(statement) in update_ids for statement in tail.statements) else pc
                # A continue directly to the condition would skip the update;
                # promoting that loop to for would change behavior.
                if update and any(p != tails[0] and pc in self.blocks[p].successors for p in members - {pc}):
                    update = []
                self.controls.append((update_pc if update else pc, outside[0]))
                if update:
                    self.omitted[tails[0]] = update_ids
                body = self.region(inside[0], pc, members, suppress_loop=pc)
                self.controls.pop()
                if update:
                    initial = []
                    if nodes and isinstance(nodes[-1], Statement) and nodes[-1].kind == "assign" and nodes[-1].target.text == update[0].target.text:
                        initial.append(nodes.pop())
                    nodes.append(Node("for", condition, body, initial=initial, update=update))
                else:
                    nodes.append(Node("while", condition, body))
                pc = outside[0]
                continue
            self.claimed.add(pc)
            nodes.extend(replace(s) for s in block.statements if id(s) not in self.omitted.get(pc, ()))
            if block.term == "exit" or not block.successors:
                break
            if block.switch or block.term == "lookup":
                spec = block.switch
                if spec is None:
                    spec = Switch(block.condition, [(literal(i), target) for i, target in enumerate(block.instructions[-1].args[1])], block.instructions[-1].args[0])
                if nodes and isinstance(nodes[-1], Statement) and nodes[-1].kind == "assign" and spec.selector.kind == "name" and nodes[-1].target.text == spec.selector.text:
                    capture = nodes[-1]
                    used_elsewhere = any(spec.selector.text in s.value.reads for p, b in self.blocks.items() if p != pc and p not in self.claimed for s in b.statements if s.value) or any(spec.selector.text in case.reads for case, _ in spec.cases)
                    if not used_elsewhere:
                        nodes.pop()
                        spec.selector = capture.value
                targets = list(dict.fromkeys([*(p for _, p in spec.cases), spec.default]))
                control_targets = {p for pair in self.controls for p in pair if p is not None}
                after = self.join(targets, excluded={pc, *control_targets})
                if after is None:
                    # A case ending in continue/return does not constrain the
                    # normal switch join. Locate the shared exit beyond the
                    # case bodies, excluding the enclosing loop's destinations.
                    reached = set().union(*(self.reachable(p, forbidden={pc, *control_targets}) for p in targets))
                    candidates = [p for p in reached if p > max(targets) and p not in control_targets and len(self.predecessors[p] & reached) >= 2]
                    after = min(candidates) if candidates else None
                if after is None:
                    # All cases may return/throw; no normal continuation.
                    after = stop
                self.controls.append((None, after))
                grouped = {}
                for case, target in spec.cases:
                    grouped.setdefault(target, []).append(case)
                grouped.setdefault(spec.default, []).append(None)
                cases = []
                for target in sorted(grouped):
                    labels = grouped[target]
                    if target == after and labels == [None]:
                        continue
                    later = min((t for t in grouped if t > target), default=after)
                    end = later if later != after else after
                    body = [] if target == after else self.region(target, end)
                    # Jumps to the switch exit are break; falling into the next
                    # case is preserved without inventing a break.
                    for label in labels[:-1]:
                        cases.append((label, []))
                    cases.append((labels[-1], body))
                self.controls.pop()
                nodes.append(Node("switch", spec.selector, cases=cases))
                pc = after
                continue
            if block.term == "branch":
                first, second = block.successors
                cond = block.condition
                if cond.kind == "unary" and cond.text == "!" or block.instructions[-1].args[0] == "iffalse":
                    first, second, cond = second, first, negate(cond)
                a, b = self.transfer(first), self.transfer(second)
                if a or b:
                    yes = first if a else second
                    test = cond if a else negate(cond)
                    nodes.append(Node("if", test, [a or b]))
                    pc = second if a else first
                    continue
                join = self.join((first, second), excluded=set(p for p, _ in self.controls if p is not None))
                if join is None and stop is not None:
                    join = stop
                if join == pc:
                    raise ReconstructionError("Conditional back edge without loop region")
                if stop is not None and join is not None and join != stop and allowed is not None and join not in allowed:
                    join = stop
                node = Node("if", cond)
                before_arm = set(self.claimed)
                node.body = self.region(first, join)
                body_claimed = self.claimed - before_arm
                loop_members = set().union(*self.loops.values()) if self.loops else set()
                protected = {p for r in self.all_regions for p in self.blocks if r.start <= p < r.stop or r.final_start is not None and r.final_start <= p < r.final_stop or any(begin <= p < end for _,_,begin,end in r.catches)}
                # An acyclic tail may be emitted in BOTH mutually exclusive
                # if arms: it still runs once on any path. Never allow this
                # across loops, catches/finally or previously executed blocks.
                reusable = body_claimed & self.reachable(second, forbidden={join}) - loop_members - protected
                self.claimed.difference_update(reusable)
                node.other = self.region(second, join)
                self.claimed.update(body_claimed)
                nodes.append(node)
                pc = join
                continue
            target = block.successors[0]
            transfer = self.transfer(target)
            if transfer:
                if transfer.kind != "continue" or target != stop:
                    nodes.append(transfer)
                break
            if target == stop:
                break
            if self.boundaries and target in self.boundaries[-1]:
                break
            pc = target
        return nodes
