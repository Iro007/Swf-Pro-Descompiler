"""Bounded CFG postdominance with explicit exits and non-terminating regions.

Original implementation of the standard fixed-point data-flow equations.
Phoenix/JPEXS are architectural references, not imported runtime dependencies.
"""
from .ir import ReconstructionError


def reachable(edges, start, forbidden=()):
    seen, pending = set(), [start]
    while pending:
        node = pending.pop()
        if node in seen or node in forbidden or node not in edges:
            continue
        seen.add(node)
        pending.extend(edges[node])
    return seen


def postdominators(edges, excluded=(), *, max_nodes=2000):
    nodes = set(edges) - set(excluded)
    if len(nodes) > max_nodes:
        raise ReconstructionError("Postdominance graph limit exceeded")
    exit_node = object()
    successors = {n: set(edges[n]) & nodes for n in nodes}
    for n in nodes:
        if not successors[n] or any(s not in nodes for s in edges[n]):
            successors[n].add(exit_node)
    predecessors = {n: set() for n in (*nodes, exit_node)}
    for n, targets in successors.items():
        for target in targets:
            predecessors[target].add(n)
    finite = reachable(predecessors, exit_node)
    universe = nodes | {exit_node}
    result = {n: ({n} if n not in finite else set(universe)) for n in nodes}
    result[exit_node] = {exit_node}
    # Nodes in a closed SCC cannot be assumed to pass through an exit/join.
    for _ in range(max_nodes + 1):
        changed = False
        for n in sorted(nodes, reverse=True):
            if n not in finite:
                continue
            updated = {n} | set.intersection(*(result[s] for s in successors[n]))
            if result[n] != updated:
                result[n], changed = updated, True
        if not changed:
            return {n: result[n] - {exit_node} for n in nodes}
    raise ReconstructionError("Postdominance work budget exceeded")


def nearest_common_postdom(starts, dominators):
    if not starts or any(n not in dominators for n in starts):
        return None
    common = set.intersection(*(dominators[n] for n in starts))
    if not common:
        return None
    candidates = [n for n in common if all(other in dominators[n] for other in common)]
    return candidates[0] if len(candidates) == 1 else None
