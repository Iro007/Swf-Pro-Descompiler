//! AVM1 action layouts and names reference py_swf_editor/py_swf/avm1.py.
use crate::abc::Reader;
use crate::{
    DecodeBudget, Diagnostic, Disassembly, Instruction, MAX_DECODED_INSTRUCTIONS, Operand, diag,
    finish,
};
use std::borrow::Cow;

fn name(op: u8) -> Option<&'static str> {
    Some(match op {
        0x00 => "end",
        0x04 => "next_frame",
        0x05 => "prev_frame",
        0x06 => "play",
        0x07 => "stop",
        0x08 => "toggle_quality",
        0x09 => "stop_sounds",
        0x0a => "add",
        0x0b => "subtract",
        0x0c => "multiply",
        0x0d => "divide",
        0x0e => "equals",
        0x0f => "less_than",
        0x10 => "and",
        0x11 => "or",
        0x12 => "not",
        0x13 => "string_equals",
        0x14 => "string_length",
        0x15 => "string_extract",
        0x17 => "pop",
        0x18 => "to_integer",
        0x1c => "get_variable",
        0x1d => "set_variable",
        0x20 => "set_target2",
        0x21 => "string_add",
        0x22 => "get_property",
        0x23 => "set_property",
        0x24 => "clone_sprite",
        0x25 => "remove_sprite",
        0x26 => "trace",
        0x27 => "start_drag",
        0x28 => "end_drag",
        0x29 => "string_less_than",
        0x2a => "throw",
        0x2b => "cast_op",
        0x2c => "implements_op",
        // Flash Lite extension: JPEXS ActionFSCommand2 is a short action.
        0x2d => "fs_command2",
        0x30 => "random_number",
        0x31 => "mb_string_length",
        0x32 => "char_to_ascii",
        0x33 => "ascii_to_char",
        0x34 => "get_time",
        0x35 => "mb_string_extract",
        0x36 => "mb_char_to_ascii",
        0x37 => "mb_ascii_to_char",
        0x3a => "delete",
        0x3b => "delete2",
        0x3c => "define_local",
        0x3d => "call_function",
        0x3e => "return",
        0x3f => "modulo",
        0x40 => "new_object",
        0x41 => "define_local2",
        0x42 => "init_array",
        0x43 => "init_object",
        0x44 => "type_of",
        0x45 => "target_path",
        0x46 => "enumerate",
        0x47 => "add2",
        0x48 => "less2",
        0x49 => "equals2",
        0x4a => "to_number",
        0x4b => "to_string",
        0x4c => "push_duplicate",
        0x4d => "stack_swap",
        0x4e => "get_member",
        0x4f => "set_member",
        0x50 => "increment",
        0x51 => "decrement",
        0x52 => "call_method",
        0x53 => "new_method",
        0x54 => "instance_of",
        0x55 => "enumerate2",
        0x60 => "bit_and",
        0x61 => "bit_or",
        0x62 => "bit_xor",
        0x63 => "bit_lshift",
        0x64 => "bit_rshift",
        0x65 => "bit_urshift",
        0x66 => "strict_equals",
        0x67 => "greater",
        0x68 => "string_greater",
        0x69 => "extends",
        0x81 => "goto_frame",
        0x83 => "get_url",
        0x87 => "store_register",
        0x88 => "constant_pool",
        0x89 => "strict_mode",
        0x8a => "wait_for_frame",
        0x8b => "set_target",
        0x8c => "goto_label",
        0x8d => "wait_for_frame2",
        0x8e => "define_function2",
        0x8f => "try",
        0x94 => "with",
        0x96 => "push",
        0x99 => "jump",
        0x9a => "get_url2",
        0x9b => "define_function",
        0x9d => "if",
        0x9e => "call",
        0x9f => "goto_frame2",
        _ => return None,
    })
}

pub(crate) fn cstring(r: &mut Reader<'_>) -> Result<String, Diagnostic> {
    let end = r.bytes[r.pos..]
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| diag("avm1_string", "unterminated string", r.pos))?;
    let bytes = r.take(end)?;
    r.take(1)?;
    String::from_utf8(bytes.to_vec()).map_err(|_| {
        diag(
            "avm1_string_encoding",
            "non UTF-8 string; raw bytes retained",
            r.pos - end - 1,
        )
    })
}
fn quoted(s: &str) -> String {
    serde_json::to_string(s).unwrap()
}

pub fn disassemble(data: &[u8]) -> Disassembly {
    disassemble_with_pool(data, &[])
}
pub fn disassemble_with_pool(data: &[u8], initial_pool: &[String]) -> Disassembly {
    let mut r = Reader::new(data);
    let mut instructions = vec![];
    let mut diagnostics = vec![];
    let mut tail = None;
    let mut pool: Cow<'_, [String]> = Cow::Borrowed(initial_pool);
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
        let Some(mnemonic) = name(op) else {
            diagnostics.push(diag(
                "avm1_unknown_opcode",
                format!("unknown action 0x{op:02x}; raw tail preserved"),
                start,
            ));
            tail = Some(start);
            break;
        };
        let mut operands = vec![];
        let mut branches = vec![];
        let mut body = None;
        let mut parameters = vec![];
        let parsed = (|| -> Result<(), Diagnostic> {
            if op >= 0x80 {
                let len = r.u16()? as usize;
                let payload = r.take(len)?;
                let mut p = Reader::new(payload);
                match op {
                    0x81 => operands.push(Operand::Unsigned(p.u16()? as u32)),
                    0x83 => {
                        operands.push(Operand::Text(cstring(&mut p)?));
                        operands.push(Operand::Text(cstring(&mut p)?));
                    }
                    0x87 | 0x89 | 0x8d | 0x9a => operands.push(Operand::Unsigned(p.byte()? as u32)),
                    0x88 => {
                        let n = p.u16()? as usize;
                        if n > p.remaining() {
                            return Err(diag(
                                "avm1_pool_count",
                                "pool count exceeds remaining bytes",
                                start,
                            ));
                        }
                        let mut new = vec![];
                        for _ in 0..n {
                            new.push(cstring(&mut p)?);
                        }
                        operands.push(Operand::Values(new.iter().map(|s| quoted(s)).collect()));
                        pool = Cow::Owned(new);
                    }
                    0x8a => {
                        operands.push(Operand::Unsigned(p.u16()? as u32));
                        operands.push(Operand::Unsigned(p.byte()? as u32));
                    }
                    0x8b | 0x8c => operands.push(Operand::Text(cstring(&mut p)?)),
                    0x96 => {
                        let mut values = vec![];
                        while p.remaining() > 0 {
                            let value = match p.byte()? {
                                0 => quoted(&cstring(&mut p)?),
                                1 => {
                                    let raw: [u8; 4] = p.take(4)?.try_into().unwrap();
                                    let v = f32::from_le_bytes(raw) as f64;
                                    if v.is_finite() {
                                        v.to_string()
                                    } else {
                                        format!("raw_f32:0x{}", crate::hex(&raw))
                                    }
                                }
                                2 => "null".into(),
                                3 => "undefined".into(),
                                4 => format!("r:{}", p.byte()?),
                                5 => {
                                    if p.byte()? == 0 {
                                        "false".into()
                                    } else {
                                        "true".into()
                                    }
                                }
                                6 => {
                                    let raw = p.take(8)?;
                                    let mut swapped = [0; 8];
                                    swapped[..4].copy_from_slice(&raw[4..]);
                                    swapped[4..].copy_from_slice(&raw[..4]);
                                    let v = f64::from_le_bytes(swapped);
                                    if v.is_finite() {
                                        v.to_string()
                                    } else {
                                        format!("raw_f64:0x{}", crate::hex(raw))
                                    }
                                }
                                7 => i32::from_le_bytes(p.take(4)?.try_into().unwrap()).to_string(),
                                8 | 9 => {
                                    let width = payload[p.pos - 1];
                                    let index = if width == 8 {
                                        p.byte()? as usize
                                    } else {
                                        p.u16()? as usize
                                    };
                                    if let Some(s) = pool.get(index) {
                                        if s.len() > budget.remaining_text()
                                            || crate::quoted_len(s) > budget.remaining_text()
                                        {
                                            return Err(diag(
                                                "avm_text_limit",
                                                "Pool literal exceeds remaining 2 MiB operand text budget; literal not copied or truncated",
                                                start,
                                            ));
                                        }
                                        quoted(s)
                                    } else {
                                        return Err(diag(
                                            "avm1_pool_reference",
                                            format!("constant {index} not in active pool"),
                                            start,
                                        ));
                                    }
                                }
                                kind => {
                                    return Err(diag(
                                        "avm1_push_type",
                                        format!("unknown push value kind {kind}"),
                                        start,
                                    ));
                                }
                            };
                            values.push(value);
                        }
                        operands.push(Operand::Values(values));
                    }
                    0x99 | 0x9d => {
                        let delta = p.u16()? as i16;
                        let target = r.pos as i64 + i64::from(delta);
                        if target < 0 || target > data.len() as i64 {
                            return Err(diag(
                                "avm1_branch_range",
                                "branch target outside action list",
                                start,
                            ));
                        }
                        operands.push(Operand::Signed(delta as i64));
                        branches.push(target as usize);
                    }
                    0x9e => {
                        if p.remaining() != 0 {
                            return Err(diag(
                                "avm1_call_payload",
                                "ActionCall must have an empty payload",
                                start,
                            ));
                        }
                    }
                    0x9f => {
                        let flags = p.byte()?;
                        if flags & !3 != 0 {
                            return Err(diag("avm1_flags", "unknown goto_frame2 flag", start));
                        }
                        operands.push(Operand::Unsigned(flags as u32));
                        if flags & 2 != 0 {
                            operands.push(Operand::Unsigned(p.u16()? as u32));
                        }
                    }
                    0x8e | 0x9b => {
                        let function_name = cstring(&mut p)?;
                        operands.push(Operand::Text(function_name));
                        let count = p.u16()? as usize;
                        if count > p.remaining() {
                            return Err(diag(
                                "avm1_function_parameters",
                                "parameter count exceeds bytes",
                                start,
                            ));
                        }
                        if op == 0x8e {
                            let register_count = p.byte()?;
                            let flags = p.u16()?;
                            if flags & !0x1ff != 0 {
                                return Err(diag("avm1_flags", "reserved function2 flags", start));
                            }
                            operands.push(Operand::Unsigned(register_count as u32));
                            operands.push(Operand::Unsigned(flags as u32));
                            let mut registers = vec![];
                            for _ in 0..count {
                                let register = p.byte()?;
                                if register > register_count {
                                    return Err(diag(
                                        "avm1_register_reference",
                                        "parameter register exceeds register_count",
                                        start,
                                    ));
                                }
                                registers.push(register);
                                parameters.push(cstring(&mut p)?);
                            }
                            operands.push(Operand::Registers(registers));
                        } else {
                            for _ in 0..count {
                                parameters.push(cstring(&mut p)?);
                            }
                        }
                        let code_size = p.u16()? as usize;
                        body = Some(r.take(code_size)?.to_vec());
                        operands.push(Operand::Unsigned(code_size as u32));
                    }
                    0x94 => {
                        let size = p.u16()? as usize;
                        body = Some(r.take(size)?.to_vec());
                        operands.push(Operand::Unsigned(size as u32));
                    }
                    0x8f => {
                        let flags = p.byte()?;
                        if flags & !7 != 0 {
                            return Err(diag("avm1_flags", "reserved try flags", start));
                        }
                        let sizes = [p.u16()? as usize, p.u16()? as usize, p.u16()? as usize];
                        if flags & 1 == 0 && sizes[1] != 0 || flags & 2 == 0 && sizes[2] != 0 {
                            return Err(diag(
                                "avm1_try_sizes",
                                "try body sizes conflict with flags",
                                start,
                            ));
                        }
                        operands.push(Operand::Unsigned(flags as u32));
                        for n in sizes {
                            operands.push(Operand::Unsigned(n as u32));
                        }
                        if flags & 4 != 0 {
                            operands.push(Operand::Unsigned(p.byte()? as u32));
                        } else {
                            operands.push(Operand::Text(cstring(&mut p)?));
                        }
                        let all = sizes.iter().sum();
                        body = Some(r.take(all)?.to_vec());
                    }
                    _ => {
                        p.take(p.remaining())?;
                        operands.push(Operand::Raw(crate::hex(payload)));
                    }
                }
                if p.remaining() != 0 {
                    return Err(diag(
                        "avm1_payload_length",
                        "unexpected bytes in action payload",
                        start,
                    ));
                }
            }
            Ok(())
        })();
        if let Err(mut d) = parsed {
            if d.code == "abc_invalid" {
                d.code = "avm1_truncated".into();
            }
            d.offset = Some(start);
            diagnostics.push(d);
            tail = Some(start);
            break;
        }
        let instruction = Instruction {
            offset: start,
            length: r.pos - start,
            opcode: op,
            name: mnemonic.into(),
            operands,
            branch_targets: branches,
            body,
            parameters,
        };
        if let Err(d) = budget.accept(&instruction) {
            diagnostics.push(d);
            tail = Some(start);
            break;
        }
        instructions.push(instruction);
        if op == 0 {
            if r.remaining() > 0 {
                diagnostics.push(diag(
                    "avm1_trailing_bytes",
                    "bytes follow terminating End; retained as raw",
                    r.pos,
                ));
                tail = Some(r.pos);
            }
            break;
        }
    }
    if tail.is_none() {
        let mut boundaries = instructions
            .iter()
            .map(|i| i.offset)
            .collect::<std::collections::HashSet<_>>();
        boundaries.insert(data.len());
        for i in &instructions {
            for target in &i.branch_targets {
                if !boundaries.contains(target) {
                    diagnostics.push(diag(
                        "avm1_branch_alignment",
                        format!("target {target:#x} is not an action boundary"),
                        i.offset,
                    ));
                }
            }
        }
        // WaitForFrame's SkipCount counts actions, not bytes.
        for index in 0..instructions.len() {
            if matches!(instructions[index].opcode, 0x8a | 0x8d) {
                let operand = if instructions[index].opcode == 0x8a {
                    1
                } else {
                    0
                };
                if let Some(Operand::Unsigned(skip)) = instructions[index].operands.get(operand) {
                    let target = index + 1 + *skip as usize;
                    if target > instructions.len() {
                        diagnostics.push(diag(
                            "avm1_wait_skip",
                            "skip count exceeds remaining actions",
                            instructions[index].offset,
                        ));
                    } else {
                        let pc = instructions
                            .get(target)
                            .map(|i| i.offset)
                            .unwrap_or(data.len());
                        instructions[index].branch_targets.push(pc);
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
    #[test]
    fn push_integer() {
        let d = disassemble(&[0x96, 5, 0, 7, 255, 255, 255, 255, 0x3e, 0]);
        assert!(d.diagnostics.is_empty());
        assert!(
            matches!(&d.instructions[0].operands[0],Operand::Values(v)if v==&vec!["-1".to_string()])
        );
    }
    #[test]
    fn branches() {
        let d = disassemble(&[0x99, 2, 0, 0, 0, 0]);
        assert_eq!(d.instructions[0].branch_targets, [5]);
        assert!(d.diagnostics.is_empty());
    }
    #[test]
    fn invalid_branch_alignment() {
        let d = disassemble(&[0x99, 2, 0, 1, 0, 0x96, 2, 0, 2, 3, 0]);
        assert!(
            d.diagnostics
                .iter()
                .any(|d| d.code == "avm1_branch_alignment")
        );
    }
    #[test]
    fn inline_function() {
        let d = disassemble(&[0x9b, 6, 0, b'f', 0, 0, 0, 1, 0, 0, 0]);
        assert_eq!(d.instructions.len(), 2);
        assert_eq!(d.instructions[0].body.as_deref(), Some(&[0][..]));
        assert!(d.diagnostics.is_empty());
    }
    #[test]
    fn truncated_function() {
        assert!(
            !disassemble(&[0x9b, 5, 0, 0, 0, 0, 2, 0])
                .diagnostics
                .is_empty()
        );
    }
    #[test]
    fn unknown_tail() {
        let d = disassemble(&[0x06, 0xff, 4, 0, 0, 0, 0, 0]);
        assert_eq!(d.instructions.len(), 1);
        assert!(d.pcode.contains("undecoded raw tail"));
    }
    #[test]
    fn constant_pool() {
        let d = disassemble(&[0x88, 4, 0, 1, 0, b'x', 0, 0x96, 2, 0, 8, 0, 0]);
        assert!(
            matches!(&d.instructions[1].operands[0],Operand::Values(v)if v==&vec!["\"x\"".to_string()])
        );
    }
    #[test]
    fn instruction_budget_keeps_complete_raw_input() {
        let code = vec![0x06; crate::MAX_DECODED_INSTRUCTIONS + 1];
        let d = disassemble(&code);
        assert_eq!(d.instructions.len(), crate::MAX_DECODED_INSTRUCTIONS);
        assert!(
            d.diagnostics
                .iter()
                .any(|d| d.code == "avm_instruction_limit")
        );
        assert!(d.pcode.len() <= crate::MAX_PCODE_BYTES);
        assert_eq!(code.len(), crate::MAX_DECODED_INSTRUCTIONS + 1);
        assert!(d.pcode.contains("raw tail"));
    }
    #[test]
    fn repeated_pool_literal_stops_at_whole_action() {
        let literal = "x".repeat(65536);
        let mut code = [0x96, 2, 0, 8, 0].repeat(64);
        code.push(0);
        let d = disassemble_with_pool(&code, std::slice::from_ref(&literal));
        assert!(d.diagnostics.iter().any(|d| d.code == "avm_text_limit"));
        assert!(d.instructions.len() < 64);
        let exact = serde_json::to_string(&literal).unwrap();
        for i in &d.instructions {
            assert!(matches!(&i.operands[0],Operand::Values(v) if v==&vec![exact.clone()]));
        }
        assert!(d.pcode.len() <= crate::MAX_PCODE_BYTES);
        assert_eq!(code.len(), 321);
    }
}
