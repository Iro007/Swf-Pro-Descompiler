//! Read-only AVM inventory and disassembly. GPL-3.0-or-later.
//! Opcode names/layouts reference the sibling py_swf_editor native readers
//! (py_swf/avm1.py, avm2_opcodes.py) and Adobe's AVM2 binary format.
pub mod abc;
pub mod avm1;
pub mod avm2;
mod inventory;
pub mod symbols;

pub use abc::{Abc, MetadataInfo, MethodBody, MethodInfo, PublicProperty, TraitInfo};
pub use inventory::{class_outline, inventory};
use serde::Serialize;
use std::sync::Arc;
pub use swf_core::Diagnostic;

pub const MAX_DECODED_INSTRUCTIONS: usize = 100_000;
pub const MAX_OPERAND_TEXT_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_PCODE_BYTES: usize = 4 * 1024 * 1024;
const PCODE_FOOTER_RESERVE: usize = 1024;

/// Budgets apply to complete instructions, never truncated literal values.
#[derive(Default)]
pub(crate) struct DecodeBudget {
    text: usize,
    pcode: usize,
}
impl DecodeBudget {
    pub fn remaining_text(&self) -> usize {
        MAX_OPERAND_TEXT_BYTES.saturating_sub(self.text)
    }
    pub fn accept(&mut self, i: &Instruction) -> Result<(), Diagnostic> {
        let text = instruction_text_len(i);
        let row = row_len_bound(i);
        if text > self.remaining_text() {
            return Err(diag(
                "avm_text_limit",
                "Decoded operand text exceeds 2 MiB; whole instruction retained in raw tail",
                i.offset,
            ));
        }
        if row > (MAX_PCODE_BYTES - PCODE_FOOTER_RESERVE).saturating_sub(self.pcode) {
            return Err(diag(
                "avm_pcode_limit",
                "P-code listing exceeds 4 MiB; whole instruction retained in raw tail",
                i.offset,
            ));
        }
        self.text += text;
        self.pcode += row;
        Ok(())
    }
}

pub(crate) fn quoted_len(s: &str) -> usize {
    // escape_debug is conservative for a string's apostrophes and otherwise
    // models the escaped byte count without allocating a quoted copy.
    2 + s
        .chars()
        .map(|c| c.escape_debug().map(char::len_utf8).sum::<usize>())
        .sum::<usize>()
}
fn operand_len(o: &Operand) -> usize {
    match o {
        Operand::Signed(v) => v.to_string().len(),
        Operand::Unsigned(v) => v.to_string().len(),
        Operand::Text(v) => quoted_len(v),
        Operand::NumberBits { kind, bits } => {
            kind.len().saturating_add(bits.len()).saturating_add(3)
        }
        Operand::Pool { kind, index, text } => {
            kind.len()
                + index.to_string().len()
                + 2
                + text.as_ref().map(|s| 1 + quoted_len(s)).unwrap_or(0)
        }
        Operand::Registers(v) => 2 + v.iter().map(|n| n.to_string().len() + 2).sum::<usize>(),
        Operand::Values(v) => v.iter().map(|s| s.len() + 2).sum::<usize>(),
        Operand::Raw(v) => v.len() + 4,
    }
}
fn instruction_text_len(i: &Instruction) -> usize {
    i.name.len()
        + i.parameters.iter().map(|s| quoted_len(s)).sum::<usize>()
        + i.operands.iter().map(|o| operand_len(o) + 1).sum::<usize>()
}
fn row_len_bound(i: &Instruction) -> usize {
    32 + i.name.len()
        + i.operands.iter().map(|o| operand_len(o) + 1).sum::<usize>()
        + if i.branch_targets.is_empty() {
            0
        } else {
            16 + i
                .branch_targets
                .iter()
                .map(|n| n.to_string().len() + 2)
                .sum::<usize>()
        }
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct ScriptUnit {
    pub id: String,
    pub name: String,
    pub language: String,
    pub kind: String,
    pub tag_path: Vec<usize>,
    pub method_index: Option<usize>,
    pub body_index: Option<usize>,
    pub abc_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub abc_sha256: Option<String>,
    pub byte_start: usize,
    pub byte_len: usize,
    pub sha256: String,
    pub diagnostics: Vec<Diagnostic>,
    pub parameters: Vec<String>,
    pub avm1_flags: Option<u16>,
    pub event_flags: Option<u32>,
    pub avm1_registers: Vec<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub avm1_register_count: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub avm1_constants_sha256: Option<String>,
    #[serde(skip)]
    pub avm1_constants: Vec<String>,
    #[serde(skip)]
    pub code: Vec<u8>,
    #[serde(skip)]
    pub context: Option<Arc<Abc>>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
pub enum Operand {
    Signed(i64),
    Unsigned(u32),
    NumberBits {
        kind: String,
        bits: String,
    },
    Text(String),
    Pool {
        kind: String,
        index: u32,
        text: Option<String>,
    },
    Registers(Vec<u8>),
    Values(Vec<String>),
    Raw(String),
}

#[derive(Debug, Clone, Serialize)]
pub struct Instruction {
    pub offset: usize,
    pub length: usize,
    pub opcode: u8,
    pub name: String,
    pub operands: Vec<Operand>,
    pub branch_targets: Vec<usize>,
    #[serde(skip)]
    pub body: Option<Vec<u8>>,
    pub parameters: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Disassembly {
    pub instructions: Vec<Instruction>,
    pub pcode: String,
    pub diagnostics: Vec<Diagnostic>,
    pub state: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ClassInfo {
    pub name: String,
    pub superclass: String,
    pub interfaces: Vec<String>,
    pub fields: Vec<String>,
    pub methods: Vec<String>,
    pub source_tag: Vec<usize>,
}

pub fn disassemble(unit: &ScriptUnit) -> Disassembly {
    if unit.kind == "abc_outline" {
        return Disassembly {
            instructions: vec![],
            pcode: format!(
                "; ABC signatures: {} methods, no method bodies\n",
                unit.context.as_ref().map(|a| a.methods.len()).unwrap_or(0)
            ),
            diagnostics: unit.diagnostics.clone(),
            state: "outline_only".into(),
        };
    }
    if !unit.diagnostics.is_empty() && unit.kind == "abc_raw" {
        return Disassembly {
            instructions: vec![],
            pcode: format!("; ABC raw: {} bytes\n", unit.code.len()),
            diagnostics: unit.diagnostics.clone(),
            state: "unsupported".into(),
        };
    }
    if unit.language == "avm2" {
        avm2::disassemble(unit)
    } else {
        avm1::disassemble_with_pool(&unit.code, &unit.avm1_constants)
    }
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
pub(crate) fn diag(code: &str, message: impl Into<String>, offset: usize) -> Diagnostic {
    Diagnostic {
        code: code.into(),
        message: message.into(),
        offset: Some(offset),
    }
}

pub(crate) fn finish(
    mut instructions: Vec<Instruction>,
    mut diagnostics: Vec<Diagnostic>,
    raw: &[u8],
    mut tail: Option<usize>,
) -> Disassembly {
    use std::fmt::Write;
    let mut pcode = String::new();
    let mut rendered = 0;
    for i in &instructions {
        if row_len_bound(i) > (MAX_PCODE_BYTES - PCODE_FOOTER_RESERVE).saturating_sub(pcode.len()) {
            diagnostics.push(diag(
                "avm_pcode_limit",
                "P-code listing exceeds 4 MiB; raw bytes preserved",
                i.offset,
            ));
            tail = Some(i.offset);
            break;
        }
        let args = i
            .operands
            .iter()
            .map(|o| match o {
                Operand::Signed(v) => v.to_string(),
                Operand::Unsigned(v) => v.to_string(),
                Operand::Text(v) => format!("{v:?}"),
                Operand::NumberBits { kind, bits } => format!("{kind}:0x{bits}"),
                Operand::Pool { kind, index, text } => format!(
                    "{kind}[{index}]{}",
                    text.as_ref().map(|v| format!("={v:?}")).unwrap_or_default()
                ),
                Operand::Registers(v) => format!("{v:?}"),
                Operand::Values(v) => v.join(", "),
                Operand::Raw(v) => format!("raw:{v}"),
            })
            .collect::<Vec<_>>()
            .join(" ");
        let targets = if i.branch_targets.is_empty() {
            String::new()
        } else {
            format!(" ; targets={:?}", i.branch_targets)
        };
        let _ = writeln!(pcode, "{:06x}: {} {}{}", i.offset, i.name, args, targets);
        rendered += 1;
    }
    if rendered < instructions.len() {
        instructions.truncate(rendered);
    }
    if let Some(start) = tail {
        if diagnostics.iter().any(|d| {
            matches!(
                d.code.as_str(),
                "avm_text_limit" | "avm_pcode_limit" | "avm_instruction_limit"
            )
        }) {
            let _ = writeln!(
                pcode,
                "; listing stopped at a decode budget; export the complete raw bytecode to preserve the remainder"
            );
        }
        let _ = writeln!(
            pcode,
            "; undecoded raw tail @{start:06x}, {} bytes; first {} bytes: {}",
            raw.len() - start,
            (raw.len() - start).min(256),
            hex(&raw[start..raw.len().min(start + 256)])
        );
    }
    let state = if diagnostics.is_empty() {
        "decoded"
    } else if instructions.is_empty() {
        "invalid"
    } else {
        "partial"
    };
    Disassembly {
        instructions,
        pcode,
        diagnostics,
        state: state.into(),
    }
}

#[cfg(test)]
mod budget_tests {
    use super::*;
    #[test]
    fn pcode_writer_is_bounded_and_retains_raw_tail() {
        let i = Instruction {
            offset: 0,
            length: 1,
            opcode: 1,
            name: "test".into(),
            operands: vec![Operand::Text("x".repeat(MAX_PCODE_BYTES))],
            branch_targets: vec![],
            body: None,
            parameters: vec![],
        };
        let d = finish(vec![i], vec![], &[1], None);
        assert!(d.instructions.is_empty());
        assert!(d.pcode.len() <= MAX_PCODE_BYTES);
        assert!(d.diagnostics.iter().any(|d| d.code == "avm_pcode_limit"));
        assert!(d.pcode.contains("raw tail @000000"));
    }
}
