//! Bounded CFG, instruction boundary validation, and finite postdominators.
use crate::{MAX_BLOCKS, Result, budget, fail, mnemonic};
use std::collections::{BTreeSet, HashMap, VecDeque};
use std::time::Instant;
use swf_avm::{Instruction, Operand};

#[derive(Debug)]
pub(crate) struct Block {
    pub first: usize,
    pub end: usize,
    pub successors: Vec<usize>,
}
pub(crate) struct Graph {
    pub blocks: Vec<Block>,
    pub postdom: Vec<Vec<u64>>,
    pub exit: usize,
    pub loops: HashMap<usize, usize>,
    /// Compiler-rotated while loops, entered at the predicate block. The body
    /// has no external entry; executing the predicate before every body visit
    /// therefore preserves the original bytecode order.
    pub rotated_loops: HashMap<usize, usize>,
    /// Byte offset of a verified compiler default-return epilogue omitted from
    /// source reconstruction. The disassembly is assembled before the CFG and
    /// remains complete in `DecompileResult::pcode`.
    pub compiler_epilogue: Option<usize>,
}
pub(crate) fn conditional(name: &str) -> bool {
    matches!(
        mnemonic(name).as_str(),
        "if" | "iftrue"
            | "iffalse"
            | "ifeq"
            | "ifne"
            | "iflt"
            | "ifle"
            | "ifgt"
            | "ifge"
            | "ifstricteq"
            | "ifstrictne"
            | "ifnlt"
            | "ifnle"
            | "ifngt"
            | "ifnge"
    )
}
pub(crate) fn terminal(name: &str) -> bool {
    matches!(
        mnemonic(name).as_str(),
        "return" | "returnvalue" | "returnvoid" | "throw" | "end"
    )
}

impl Graph {
    pub fn new(
        instructions: &[Instruction],
        code_len: usize,
        started: Instant,
        allow_compiler_epilogue: bool,
        exception_boundaries: &[usize],
    ) -> Result<Self> {
        if instructions.is_empty() {
            return Ok(Self {
                blocks: vec![],
                postdom: vec![],
                exit: 0,
                loops: HashMap::new(),
                rotated_loops: HashMap::new(),
                compiler_epilogue: None,
            });
        }
        let offsets: HashMap<_, _> = instructions
            .iter()
            .enumerate()
            .map(|(n, i)| (i.offset, n))
            .collect();
        let mut leaders = BTreeSet::from([0]);
        for boundary in exception_boundaries {
            if *boundary != code_len {
                leaders.insert(*offsets.get(boundary).ok_or_else(|| {
                    fail(
                        "INVALID_EXCEPTION_RANGE",
                        "Exception boundary is not an instruction boundary",
                        *boundary,
                    )
                })?);
            }
        }
        for (n, i) in instructions.iter().enumerate() {
            budget(started, i.offset)?;
            for target in &i.branch_targets {
                if *target == code_len {
                    continue;
                }
                let index = offsets.get(target).ok_or_else(|| {
                    fail(
                        "INVALID_BRANCH_TARGET",
                        "Branch does not target an instruction boundary",
                        i.offset,
                    )
                })?;
                leaders.insert(*index);
            }
            if (conditional(&i.name)
                || matches!(mnemonic(&i.name).as_str(), "jump" | "lookupswitch")
                || terminal(&i.name))
                && n + 1 < instructions.len()
            {
                leaders.insert(n + 1);
            }
        }
        if leaders.len() > MAX_BLOCKS {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "Method exceeds 2,000 CFG blocks",
                0,
            ));
        }
        let leaders: Vec<_> = leaders.into_iter().collect();
        let original_exit = leaders.len();
        let mut by_instruction = vec![0; instructions.len()];
        let mut blocks = vec![];
        for (b, first) in leaders.iter().copied().enumerate() {
            let end = leaders.get(b + 1).copied().unwrap_or(instructions.len());
            by_instruction[first..end].fill(b);
            blocks.push(Block {
                first,
                end,
                successors: vec![],
            });
        }
        for b in 0..blocks.len() {
            let i = &instructions[blocks[b].end - 1];
            let next = if b + 1 < original_exit {
                b + 1
            } else {
                original_exit
            };
            if mnemonic(&i.name) == "lookupswitch" {
                if i.branch_targets.len() < 2 {
                    return Err(fail(
                        "INVALID_BRANCH_TARGET",
                        "Switch needs default and at least one case",
                        i.offset,
                    ));
                }
                for offset in &i.branch_targets {
                    let target = if *offset == code_len {
                        original_exit
                    } else {
                        by_instruction[offsets[offset]]
                    };
                    if !blocks[b].successors.contains(&target) {
                        blocks[b].successors.push(target);
                    }
                }
            } else if conditional(&i.name) || mnemonic(&i.name) == "jump" {
                if i.branch_targets.len() != 1 {
                    return Err(fail(
                        "INVALID_BRANCH_TARGET",
                        "Expected one branch target",
                        i.offset,
                    ));
                }
                let target = if i.branch_targets[0] == code_len {
                    original_exit
                } else {
                    by_instruction[offsets[&i.branch_targets[0]]]
                };
                blocks[b].successors.push(target);
                if conditional(&i.name) && target != next {
                    blocks[b].successors.push(next);
                }
            } else if !terminal(&i.name) {
                blocks[b].successors.push(next);
            }
        }
        let compiler_epilogue = if allow_compiler_epilogue {
            verified_compiler_epilogue(instructions, &blocks, code_len)
        } else {
            None
        };
        let mut blocks = blocks;
        if let Some((first_block, _)) = compiler_epilogue {
            blocks.truncate(first_block);
        }
        let exit = blocks.len();
        // The old virtual exit may be the target of an explicit branch to the
        // end of the method. Keep that edge attached to the new virtual exit.
        if exit != original_exit {
            for block in &mut blocks {
                for successor in &mut block.successors {
                    if *successor == original_exit {
                        *successor = exit;
                    }
                }
            }
        }
        let mut predecessors = vec![vec![]; exit + 1];
        for (b, block) in blocks.iter().enumerate() {
            for s in &block.successors {
                predecessors[*s].push(b);
            }
        }
        // Virtual exit for terminal blocks. Closed SCCs do not postdominate exits.
        let mut reverse = predecessors.clone();
        for (b, block) in blocks.iter().enumerate() {
            if block.successors.is_empty() {
                reverse[exit].push(b);
            }
        }
        let mut finite = vec![false; exit + 1];
        let mut queue = VecDeque::from([exit]);
        while let Some(b) = queue.pop_front() {
            if finite[b] {
                continue;
            }
            finite[b] = true;
            queue.extend(reverse[b].iter().copied());
        }
        let words = (exit + 1).div_ceil(64);
        let mut postdom = vec![vec![u64::MAX; words]; exit + 1];
        postdom[exit].fill(0);
        set(&mut postdom[exit], exit);
        for b in 0..exit {
            if !finite[b] {
                postdom[b].fill(0);
                set(&mut postdom[b], b);
            }
        }
        for _ in 0..=exit {
            budget(started, 0)?;
            let mut changed = false;
            for b in (0..exit).rev() {
                if !finite[b] {
                    continue;
                }
                let successors = if blocks[b].successors.is_empty() {
                    vec![exit]
                } else {
                    blocks[b].successors.clone()
                };
                let mut next = postdom[successors[0]].clone();
                for s in &successors[1..] {
                    for (n, other) in next.iter_mut().zip(&postdom[*s]) {
                        *n &= *other;
                    }
                }
                set(&mut next, b);
                if next != postdom[b] {
                    postdom[b] = next;
                    changed = true;
                }
            }
            if !changed {
                break;
            }
        }
        let mut loops = HashMap::new();
        let mut rotated_loops = HashMap::new();
        let mut backedge_tail = HashMap::<usize, usize>::new();
        for (tail, block) in blocks.iter().enumerate() {
            for head in &block.successors {
                if *head <= tail {
                    backedge_tail
                        .entry(*head)
                        .and_modify(|end| *end = (*end).max(tail))
                        .or_insert(tail);
                }
            }
        }
        for (tail, block) in blocks.iter().enumerate() {
            for head in &block.successors {
                if *head <= tail {
                    let interval_tail = backedge_tail[head];
                    let header_tested = conditional(&instructions[blocks[*head].end - 1].name)
                        && blocks[*head].successors.len() == 2
                        && blocks[*head].successors.iter().any(|s| *s > interval_tail)
                        && blocks[*head]
                            .successors
                            .iter()
                            .any(|s| *s > *head && *s <= interval_tail);
                    if header_tested {
                        loops.insert(*head, interval_tail);
                        for incoming in predecessors.iter().take(interval_tail + 1).skip(head + 1) {
                            if incoming.iter().any(|p| *p < *head || *p > interval_tail) {
                                return Err(fail(
                                    "IRREDUCIBLE_CONTROL_FLOW",
                                    "Loop has an external entry into its body",
                                    instructions[block.first].offset,
                                ));
                            }
                        }
                        continue;
                    }
                    if conditional(&instructions[block.end - 1].name) && *head < tail {
                        // Accept only a while loop whose first entry goes to
                        // its predicate. A true do/while entry at the body, an
                        // external body entry, or a secondary exit is not this
                        // shape and must retain its exact P-code.
                        if block.successors.len() != 2
                            || block.successors[0] != *head
                            || block.successors[1] != tail + 1
                            || predecessors[*head].as_slice() != [tail]
                            || !predecessors[tail].iter().any(|p| *p < *head)
                            || predecessors[*head + 1..tail]
                                .iter()
                                .any(|incoming| incoming.iter().any(|p| *p < *head || *p >= tail))
                            || blocks[*head..tail].iter().any(|body| {
                                body.successors.is_empty()
                                    || body.successors.iter().any(|s| *s < *head || *s > tail)
                            })
                        {
                            return Err(fail(
                                "UNSUPPORTED_CONTROL_FLOW",
                                "Conditional back edge is not a single-entry rotated while loop",
                                instructions[block.first].offset,
                            ));
                        }
                        if rotated_loops.insert(tail, *head).is_some() {
                            return Err(fail(
                                "UNSUPPORTED_CONTROL_FLOW",
                                "Rotated loop has multiple back edges",
                                instructions[block.first].offset,
                            ));
                        }
                        continue;
                    }
                    if loops.insert(*head, tail).is_some() {
                        return Err(fail(
                            "UNSUPPORTED_CONTROL_FLOW",
                            "Loop has multiple back edges",
                            instructions[block.first].offset,
                        ));
                    }
                    if mnemonic(&instructions[block.end - 1].name) != "jump" {
                        return Err(fail(
                            "UNSUPPORTED_CONTROL_FLOW",
                            "Only header-tested reducible loops are supported",
                            instructions[block.first].offset,
                        ));
                    }
                    for incoming in predecessors.iter().take(tail + 1).skip(head + 1) {
                        if incoming.iter().any(|p| *p < *head || *p > tail) {
                            return Err(fail(
                                "IRREDUCIBLE_CONTROL_FLOW",
                                "Loop has an external entry into its body",
                                instructions[block.first].offset,
                            ));
                        }
                    }
                }
            }
        }
        Ok(Self {
            blocks,
            postdom,
            exit,
            loops,
            rotated_loops,
            compiler_epilogue: compiler_epilogue.map(|(_, offset)| offset),
        })
    }
    pub fn common_join(&self, left: usize, right: usize) -> Option<usize> {
        (left.max(right)..=self.exit)
            .find(|n| has(&self.postdom[left], *n) && has(&self.postdom[right], *n))
    }
}

/// Recognize only a linear, unreachable compiler default-return suffix after
/// a reachable return. This deliberately does not try to classify arbitrary
/// dead code: calls, property access, branches, and unknown instructions all
/// leave the normal unreachable-bytecode guard in place.
fn verified_compiler_epilogue(
    instructions: &[Instruction],
    blocks: &[Block],
    code_len: usize,
) -> Option<(usize, usize)> {
    if blocks.len() < 2 {
        return None;
    }
    let mut reachable = vec![false; blocks.len()];
    let mut pending = VecDeque::from([0]);
    while let Some(block) = pending.pop_front() {
        if block >= blocks.len() || reachable[block] {
            continue;
        }
        reachable[block] = true;
        pending.extend(blocks[block].successors.iter().copied());
    }

    for first_block in 1..blocks.len() {
        let first = blocks[first_block].first;
        let Some(previous) = first.checked_sub(1) else {
            continue;
        };
        if !reachable[first_block - 1]
            || reachable[first_block..]
                .iter()
                .any(|is_reachable| *is_reachable)
            || !matches!(
                mnemonic(&instructions[previous].name).as_str(),
                "returnvalue" | "returnvoid"
            )
        {
            continue;
        }
        // No branch from anywhere in the method may enter the suffix, even
        // from a currently unreachable block. This keeps the boundary simple
        // to explain and avoids hiding future control-flow interpretations.
        let start_offset = instructions[first].offset;
        if instructions.iter().any(|instruction| {
            instruction
                .branch_targets
                .iter()
                .any(|target| *target >= start_offset && *target < code_len)
        }) {
            continue;
        }
        if is_default_return_epilogue(&instructions[first..]) {
            return Some((first_block, start_offset));
        }
    }
    None
}

fn is_default_return_epilogue(instructions: &[Instruction]) -> bool {
    let Some((last, prefix)) = instructions.split_last() else {
        return false;
    };
    if mnemonic(&last.name) == "returnvoid" {
        return prefix.is_empty() && has_no_operands_or_branches(last);
    }
    if mnemonic(&last.name) != "returnvalue" || !has_no_operands_or_branches(last) {
        return false;
    }
    let Some((default, casts)) = prefix.split_first() else {
        return false;
    };
    if !is_default_value(default) {
        return false;
    }
    casts.iter().all(|instruction| {
        matches!(
            mnemonic(&instruction.name).as_str(),
            "coercea"
                | "coerceb"
                | "coerced"
                | "coercei"
                | "coerces"
                | "coerceu"
                | "convertb"
                | "convertd"
                | "converti"
                | "converts"
                | "convertu"
        ) && has_no_operands_or_branches(instruction)
    })
}

fn is_default_value(instruction: &Instruction) -> bool {
    match mnemonic(&instruction.name).as_str() {
        "pushundefined" | "pushnull" | "pushfalse" | "pushnan" => {
            has_no_operands_or_branches(instruction)
        }
        "pushbyte" | "pushshort" => {
            instruction.branch_targets.is_empty()
                && instruction.parameters.is_empty()
                && matches!(instruction.operands.as_slice(), [Operand::Signed(0)])
        }
        _ => false,
    }
}

fn has_no_operands_or_branches(instruction: &Instruction) -> bool {
    instruction.operands.is_empty()
        && instruction.parameters.is_empty()
        && instruction.branch_targets.is_empty()
}

fn set(bits: &mut [u64], n: usize) {
    bits[n / 64] |= 1 << (n % 64);
}
fn has(bits: &[u64], n: usize) -> bool {
    bits[n / 64] & (1 << (n % 64)) != 0
}
