//! AVM2 widths adapted from py_swf/py_swf/avm2_opcodes.py (GPL).
use crate::abc::Reader;
use crate::{
    DecodeBudget, Disassembly, Instruction, MAX_DECODED_INSTRUCTIONS, Operand, ScriptUnit, diag,
    finish,
};

// Operand widths: u30, byte, signed 24-bit, lookupswitch.
const TABLE: &[(u8, &str, &[u8])] = &[
    (0x01, "bkpt", &[]),
    (0x02, "nop", &[]),
    (0x03, "throw", &[]),
    (0x04, "getsuper", &[0]),
    (0x05, "setsuper", &[0]),
    (0x06, "dxns", &[0]),
    (0x07, "dxnslate", &[]),
    (0x08, "kill", &[0]),
    (0x09, "label", &[]),
    (0x0c, "ifnlt", &[2]),
    (0x0d, "ifnle", &[2]),
    (0x0e, "ifngt", &[2]),
    (0x0f, "ifnge", &[2]),
    (0x10, "jump", &[2]),
    (0x11, "iftrue", &[2]),
    (0x12, "iffalse", &[2]),
    (0x13, "ifeq", &[2]),
    (0x14, "ifne", &[2]),
    (0x15, "iflt", &[2]),
    (0x16, "ifle", &[2]),
    (0x17, "ifgt", &[2]),
    (0x18, "ifge", &[2]),
    (0x19, "ifstricteq", &[2]),
    (0x1a, "ifstrictne", &[2]),
    (0x1b, "lookupswitch", &[3]),
    (0x1c, "pushwith", &[]),
    (0x1d, "popscope", &[]),
    (0x1e, "nextname", &[]),
    (0x1f, "hasnext", &[]),
    (0x20, "pushnull", &[]),
    (0x21, "pushundefined", &[]),
    (0x22, "pushfloat", &[0]),
    (0x23, "nextvalue", &[]),
    (0x24, "pushbyte", &[1]),
    (0x25, "pushshort", &[0]),
    (0x26, "pushtrue", &[]),
    (0x27, "pushfalse", &[]),
    (0x28, "pushnan", &[]),
    (0x29, "pop", &[]),
    (0x2a, "dup", &[]),
    (0x2b, "swap", &[]),
    (0x2c, "pushstring", &[0]),
    (0x2d, "pushint", &[0]),
    (0x2e, "pushuint", &[0]),
    (0x2f, "pushdouble", &[0]),
    (0x30, "pushscope", &[]),
    (0x31, "pushnamespace", &[0]),
    (0x32, "hasnext2", &[0, 0]),
    (0x35, "li8", &[]),
    (0x36, "li16", &[]),
    (0x37, "li32", &[]),
    (0x38, "lf32", &[]),
    (0x39, "lf64", &[]),
    (0x3a, "si8", &[]),
    (0x3b, "si16", &[]),
    (0x3c, "si32", &[]),
    (0x3d, "sf32", &[]),
    (0x3e, "sf64", &[]),
    (0x40, "newfunction", &[0]),
    (0x41, "call", &[0]),
    (0x42, "construct", &[0]),
    (0x43, "callmethod", &[0, 0]),
    (0x44, "callstatic", &[0, 0]),
    (0x45, "callsuper", &[0, 0]),
    (0x46, "callproperty", &[0, 0]),
    (0x47, "returnvoid", &[]),
    (0x48, "returnvalue", &[]),
    (0x49, "constructsuper", &[0]),
    (0x4a, "constructprop", &[0, 0]),
    (0x4c, "callproplex", &[0, 0]),
    (0x4e, "callsupervoid", &[0, 0]),
    (0x4f, "callpropvoid", &[0, 0]),
    (0x50, "sxi1", &[]),
    (0x51, "sxi8", &[]),
    (0x52, "sxi16", &[]),
    (0x53, "applytype", &[0]),
    (0x55, "newobject", &[0]),
    (0x56, "newarray", &[0]),
    (0x57, "newactivation", &[]),
    (0x58, "newclass", &[0]),
    (0x59, "getdescendants", &[0]),
    (0x5a, "newcatch", &[0]),
    (0x5d, "findpropstrict", &[0]),
    (0x5e, "findproperty", &[0]),
    (0x5f, "finddef", &[0]),
    (0x60, "getlex", &[0]),
    (0x61, "setproperty", &[0]),
    (0x62, "getlocal", &[0]),
    (0x63, "setlocal", &[0]),
    (0x64, "getglobalscope", &[]),
    (0x65, "getscopeobject", &[1]),
    (0x66, "getproperty", &[0]),
    (0x67, "getouterscope", &[0]),
    (0x68, "initproperty", &[0]),
    (0x6a, "deleteproperty", &[0]),
    (0x6c, "getslot", &[0]),
    (0x6d, "setslot", &[0]),
    (0x6e, "getglobalslot", &[0]),
    (0x6f, "setglobalslot", &[0]),
    (0x70, "convert_s", &[]),
    (0x71, "esc_xelem", &[]),
    (0x72, "esc_xattr", &[]),
    (0x73, "convert_i", &[]),
    (0x74, "convert_u", &[]),
    (0x75, "convert_d", &[]),
    (0x76, "convert_b", &[]),
    (0x77, "convert_o", &[]),
    (0x78, "checkfilter", &[]),
    (0x79, "convert_f", &[]),
    (0x7a, "unplus", &[]),
    (0x80, "coerce", &[0]),
    (0x81, "coerce_b", &[]),
    (0x82, "coerce_a", &[]),
    (0x83, "coerce_i", &[]),
    (0x84, "coerce_d", &[]),
    (0x85, "coerce_s", &[]),
    (0x86, "astype", &[0]),
    (0x87, "astypelate", &[]),
    (0x88, "coerce_u", &[]),
    (0x89, "coerce_o", &[]),
    (0x90, "negate", &[]),
    (0x91, "increment", &[]),
    (0x92, "inclocal", &[0]),
    (0x93, "decrement", &[]),
    (0x94, "declocal", &[0]),
    (0x95, "typeof", &[]),
    (0x96, "not", &[]),
    (0x97, "bitnot", &[]),
    (0xa0, "add", &[]),
    (0xa1, "subtract", &[]),
    (0xa2, "multiply", &[]),
    (0xa3, "divide", &[]),
    (0xa4, "modulo", &[]),
    (0xa5, "lshift", &[]),
    (0xa6, "rshift", &[]),
    (0xa7, "urshift", &[]),
    (0xa8, "bitand", &[]),
    (0xa9, "bitor", &[]),
    (0xaa, "bitxor", &[]),
    (0xab, "equals", &[]),
    (0xac, "strictequals", &[]),
    (0xad, "lessthan", &[]),
    (0xae, "lessequals", &[]),
    (0xaf, "greaterthan", &[]),
    (0xb0, "greaterequals", &[]),
    (0xb1, "instanceof", &[]),
    (0xb2, "istype", &[0]),
    (0xb3, "istypelate", &[]),
    (0xb4, "in", &[]),
    (0xc0, "increment_i", &[]),
    (0xc1, "decrement_i", &[]),
    (0xc2, "inclocal_i", &[0]),
    (0xc3, "declocal_i", &[0]),
    (0xc4, "negate_i", &[]),
    (0xc5, "add_i", &[]),
    (0xc6, "subtract_i", &[]),
    (0xc7, "multiply_i", &[]),
    (0xd0, "getlocal_0", &[]),
    (0xd1, "getlocal_1", &[]),
    (0xd2, "getlocal_2", &[]),
    (0xd3, "getlocal_3", &[]),
    (0xd4, "setlocal_0", &[]),
    (0xd5, "setlocal_1", &[]),
    (0xd6, "setlocal_2", &[]),
    (0xd7, "setlocal_3", &[]),
    (0xef, "debug", &[1, 0, 1, 0]),
    (0xf0, "debugline", &[0]),
    (0xf1, "debugfile", &[0]),
    (0xf2, "bkptline", &[0]),
    (0xf3, "timestamp", &[]),
];
fn pool_kind(op: u8) -> Option<&'static str> {
    match op {
        0x04 | 0x05 | 0x45 | 0x46 | 0x4a | 0x4c | 0x4e | 0x4f | 0x59 | 0x5d | 0x5e | 0x5f
        | 0x60 | 0x61 | 0x66 | 0x68 | 0x6a | 0x80 | 0x86 | 0xb2 => Some("multiname"),
        0x06 | 0x2c | 0xf1 => Some("string"),
        0x2d => Some("int"),
        0x2e => Some("uint"),
        0x2f => Some("double"),
        0x22 => Some("float"),
        0x31 => Some("namespace"),
        0x40 | 0x44 => Some("method"),
        0x58 => Some("class"),
        _ => None,
    }
}

fn operand(
    unit: &ScriptUnit,
    op: u8,
    index: usize,
    value: u32,
    remaining_text: usize,
) -> Result<Operand, crate::Diagnostic> {
    let kind = if index == 0 {
        pool_kind(op)
    } else if op == 0xef && index == 1 {
        Some("string")
    } else {
        None
    };
    if let Some(kind) = kind {
        let Some(a) = unit.context.as_ref() else {
            return Ok(Operand::Pool {
                kind: kind.into(),
                index: value,
                text: None,
            });
        };
        let bounded_text = |s: Option<&str>| -> Result<Option<String>, crate::Diagnostic> {
            match s {
                Some(s) if s.len() > remaining_text || crate::quoted_len(s) > remaining_text => {
                    Err(diag(
                        "avm_text_limit",
                        "Pool literal exceeds remaining 2 MiB operand text budget; literal not copied or truncated",
                        0,
                    ))
                }
                Some(s) => Ok(Some(s.to_owned())),
                None => Ok(None),
            }
        };
        let (len, text) = match kind {
            "multiname" => (a.multinames.len(), bounded_text(a.public_name_str(value))?),
            "string" => (a.strings.len(), bounded_text(a.string(value))?),
            "int" => (
                a.integers.len(),
                a.integers.get(value as usize).map(ToString::to_string),
            ),
            "uint" => (
                a.uints.len(),
                a.uints.get(value as usize).map(ToString::to_string),
            ),
            "double" => (
                a.doubles.len(),
                a.doubles.get(value as usize).map(|b| {
                    let n = f64::from_le_bytes(*b);
                    if n.is_finite() {
                        n.to_string()
                    } else {
                        format!("bits:0x{}", crate::hex(b))
                    }
                }),
            ),
            "float" => (
                a.floats.len(),
                a.floats
                    .get(value as usize)
                    .map(|b| format!("bits:0x{}", crate::hex(b))),
            ),
            "namespace" => (
                a.namespaces.len(),
                bounded_text(
                    a.namespaces
                        .get(value as usize)
                        .and_then(|n| a.string(n.name)),
                )?,
            ),
            "method" => (
                a.methods.len(),
                bounded_text(a.methods.get(value as usize).and_then(|m| a.string(m.name)))?,
            ),
            "class" => (a.classes.len(), None),
            _ => unreachable!(),
        };
        if value as usize >= len {
            return Err(diag(
                "avm2_pool_reference",
                format!("invalid {kind} index {value}"),
                0,
            ));
        }
        Ok(Operand::Pool {
            kind: kind.into(),
            index: value,
            text,
        })
    } else {
        Ok(Operand::Unsigned(value))
    }
}

pub fn disassemble(unit: &ScriptUnit) -> Disassembly {
    let data = &unit.code;
    let mut r = Reader::new(data);
    let mut instructions = vec![];
    let mut diagnostics = vec![];
    let mut tail = None;
    let mut budget = DecodeBudget::default();
    while r.remaining() > 0 {
        let start = r.pos;
        if instructions.len() >= MAX_DECODED_INSTRUCTIONS {
            diagnostics.push(diag(
                "avm_instruction_limit",
                "instruction count exceeds 100,000; raw tail preserved",
                start,
            ));
            tail = Some(start);
            break;
        }
        let op = r.byte().unwrap();
        let entry = TABLE.iter().find(|(byte, _, _)| *byte == op);
        let Some((_, name, widths)) = entry else {
            diagnostics.push(diag(
                "avm2_unknown_opcode",
                format!("unknown opcode 0x{op:02x}; raw tail preserved"),
                start,
            ));
            tail = Some(start);
            break;
        };
        if matches!(op, 0x22 | 0x79 | 0x7a) && unit.context.as_ref().is_some_and(|a| a.major != 47)
        {
            diagnostics.push(diag(
                "avm2_dialect_opcode",
                "float opcode is unavailable in this ABC dialect",
                start,
            ));
            tail = Some(start);
            break;
        }
        let mut operands = vec![];
        let mut branches = vec![];
        let parsed = (|| -> Result<(), crate::Diagnostic> {
            for (index, width) in widths.iter().enumerate() {
                match width {
                    0 => {
                        let n = if op == 0x25 { r.u32var()? } else { r.u30()? };
                        if op == 0x25 {
                            operands.push(Operand::Signed((n as i16) as i64));
                        } else {
                            operands.push(operand(unit, op, index, n, budget.remaining_text())?);
                        }
                    }
                    1 => {
                        let n = r.byte()?;
                        if op == 0x24 {
                            operands.push(Operand::Signed((n as i8) as i64));
                        } else {
                            operands.push(Operand::Unsigned(n as u32));
                        }
                    }
                    2 => {
                        let delta = r.s24()?;
                        let target = (r.pos as i64) + i64::from(delta);
                        if target < 0 || target >= data.len() as i64 {
                            return Err(diag(
                                "avm2_branch_range",
                                "branch target outside method",
                                start,
                            ));
                        }
                        operands.push(Operand::Signed(delta as i64));
                        branches.push(target as usize);
                    }
                    3 => {
                        let default = r.s24()?;
                        let n = r.u30()? as usize;
                        if n >= MAX_DECODED_INSTRUCTIONS
                            || n.checked_add(1).is_none_or(|v| v > r.remaining() / 3)
                        {
                            return Err(diag(
                                "avm2_switch_count",
                                "switch count exceeds remaining bytes",
                                start,
                            ));
                        }
                        operands.push(Operand::Signed(default as i64));
                        operands.push(Operand::Unsigned(n as u32));
                        let mut offsets = vec![default];
                        for _ in 0..=n {
                            let d = r.s24()?;
                            offsets.push(d);
                            operands.push(Operand::Signed(d as i64));
                        }
                        for d in offsets {
                            let target = start as i64 + i64::from(d);
                            if target < 0 || target >= data.len() as i64 {
                                return Err(diag(
                                    "avm2_branch_range",
                                    "switch target outside method",
                                    start,
                                ));
                            }
                            branches.push(target as usize);
                        }
                    }
                    _ => unreachable!(),
                }
            }
            Ok(())
        })();
        if let Err(mut d) = parsed {
            d.offset = Some(start);
            diagnostics.push(d);
            tail = Some(start);
            break;
        }
        let instruction = Instruction {
            offset: start,
            length: r.pos - start,
            opcode: op,
            name: (*name).into(),
            operands,
            branch_targets: branches,
            body: None,
            parameters: vec![],
        };
        if let Err(d) = budget.accept(&instruction) {
            diagnostics.push(d);
            tail = Some(start);
            break;
        }
        instructions.push(instruction);
    }
    if tail.is_none() {
        let boundaries = instructions
            .iter()
            .map(|i| i.offset)
            .collect::<std::collections::HashSet<_>>();
        for i in &instructions {
            for target in &i.branch_targets {
                if !boundaries.contains(target) {
                    diagnostics.push(diag(
                        "avm2_branch_alignment",
                        format!("target {target:#x} is not an instruction boundary"),
                        i.offset,
                    ));
                }
            }
        }
        if let Some(a) = &unit.context
            && let Some(index) = unit.body_index
            && let Some(b) = a.bodies.get(index)
        {
            for e in &b.exceptions {
                for p in [e.from, e.to, e.target] {
                    if p as usize != data.len() && !boundaries.contains(&(p as usize)) {
                        diagnostics.push(diag(
                            "avm2_exception_alignment",
                            "exception endpoint is not an instruction boundary",
                            p as usize,
                        ));
                    }
                }
            }
            for i in &instructions {
                let locals = matches!(
                    i.opcode,
                    0x08 | 0x62 | 0x63 | 0x92 | 0x94 | 0xc2 | 0xc3 | 0x32
                );
                if locals {
                    for o in &i.operands {
                        if let Operand::Unsigned(n) = o
                            && *n >= b.local_count
                        {
                            diagnostics.push(diag(
                                "avm2_local_reference",
                                format!("local {n} exceeds local_count {}", b.local_count),
                                i.offset,
                            ));
                        }
                    }
                }
            }
        }
    }
    finish(instructions, diagnostics, data, tail)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn unit(code: &[u8]) -> ScriptUnit {
        ScriptUnit {
            id: "test".into(),
            name: "test".into(),
            language: "avm2".into(),
            kind: "method".into(),
            byte_len: code.len(),
            code: code.to_vec(),
            ..Default::default()
        }
    }
    #[test]
    fn branch_alignment() {
        let d = disassemble(&unit(&[0x10, 1, 0, 0, 0x24, 1, 0x47]));
        assert!(
            d.diagnostics
                .iter()
                .any(|x| x.code == "avm2_branch_alignment")
        );
    }
    #[test]
    fn branch_relative_to_next() {
        let d = disassemble(&unit(&[0x10, 0, 0, 0, 0x47]));
        assert_eq!(d.instructions[0].branch_targets, [4]);
        assert!(d.diagnostics.is_empty());
    }
    #[test]
    fn unknown_stops() {
        let d = disassemble(&unit(&[0x02, 0xff, 0x47]));
        assert_eq!(d.instructions.len(), 1);
        assert!(d.pcode.contains("raw tail"));
    }
    #[test]
    fn switch_relative_to_opcode() {
        let d = disassemble(&unit(&[0x1b, 8, 0, 0, 0, 8, 0, 0, 0x47]));
        assert_eq!(d.instructions[0].branch_targets, [8, 8]);
        assert!(d.diagnostics.is_empty());
    }
    #[test]
    fn signed_byte() {
        let d = disassemble(&unit(&[0x24, 255, 0x48]));
        assert!(matches!(d.instructions[0].operands[0], Operand::Signed(-1)));
    }
    #[test]
    fn truncation() {
        assert_eq!(disassemble(&unit(&[0x10, 0])).state, "invalid");
    }
    #[test]
    fn short_sign_extension() {
        let d = disassemble(&unit(&[0x25, 255, 255, 3, 0x48]));
        assert!(matches!(d.instructions[0].operands[0], Operand::Signed(-1)));
    }
    fn context_with_string(s: String) -> std::sync::Arc<crate::Abc> {
        let mut a = crate::Abc::parse(&[16, 0, 46, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]).unwrap();
        a.strings.push(s);
        std::sync::Arc::new(a)
    }
    #[test]
    fn instruction_budget_keeps_complete_unit_code() {
        let u = unit(&vec![0x02; crate::MAX_DECODED_INSTRUCTIONS + 1]);
        let d = disassemble(&u);
        assert_eq!(d.instructions.len(), crate::MAX_DECODED_INSTRUCTIONS);
        assert!(
            d.diagnostics
                .iter()
                .any(|d| d.code == "avm_instruction_limit")
        );
        assert_eq!(u.code.len(), crate::MAX_DECODED_INSTRUCTIONS + 1);
        assert!(d.pcode.len() <= crate::MAX_PCODE_BYTES);
    }
    #[test]
    fn oversized_pool_literal_is_not_copied_or_truncated() {
        let mut u = unit(&[0x2c, 1, 0x48]);
        u.context = Some(context_with_string(
            "x".repeat(crate::MAX_OPERAND_TEXT_BYTES + 1),
        ));
        let d = disassemble(&u);
        assert!(d.instructions.is_empty());
        assert!(d.diagnostics.iter().any(|d| d.code == "avm_text_limit"));
        assert_eq!(u.code, [0x2c, 1, 0x48]);
        assert_eq!(
            u.context.as_ref().unwrap().strings[1].len(),
            crate::MAX_OPERAND_TEXT_BYTES + 1
        );
        assert!(d.pcode.len() < 1024);
    }
    #[test]
    fn repeated_string_references_have_text_budget() {
        let mut u = unit(&[0x2c, 1].repeat(64));
        u.context = Some(context_with_string("x".repeat(65536)));
        let d = disassemble(&u);
        assert!(d.instructions.len() < 64);
        assert!(d.diagnostics.iter().any(|d| d.code == "avm_text_limit"));
        for i in &d.instructions {
            assert!(matches!(&i.operands[0],Operand::Pool{text:Some(s),..} if s.len()==65536));
        }
        assert_eq!(u.code.len(), 128);
        assert!(d.pcode.len() <= crate::MAX_PCODE_BYTES);
    }
}
