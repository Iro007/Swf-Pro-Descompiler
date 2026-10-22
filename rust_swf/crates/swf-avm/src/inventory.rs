//! Byte-accurate script inventory. Script container layout reference:
//! py_swf_editor/py_swf/script_inventory.py, GPL-3.0-or-later.
use crate::abc::Reader;
use crate::avm1::cstring;
use crate::{Abc, ClassInfo, Operand, ScriptUnit, diag};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use swf_core::{Diagnostic, Movie, TagRecord};

const MAX_UNITS: usize = 100_000;
const MAX_CODE_BYTES: usize = 256 * 1024 * 1024;
fn unit(
    tag: &TagRecord,
    id: String,
    name: String,
    language: &str,
    kind: &str,
    start: usize,
    code: &[u8],
) -> ScriptUnit {
    ScriptUnit {
        id,
        name,
        language: language.into(),
        kind: kind.into(),
        tag_path: tag.path.clone(),
        byte_start: tag.data_start + start,
        byte_len: code.len(),
        sha256: crate::hex(&Sha256::digest(code)),
        code: code.to_vec(),
        diagnostics: tag.diagnostics.clone(),
        ..Default::default()
    }
}
fn failure(tag: &TagRecord, code: &[u8], mut d: Diagnostic) -> ScriptUnit {
    d.offset = d.offset.map(|x| tag.data_start + x);
    let mut u = unit(
        tag,
        format!("{}/diagnostic", tag.id),
        tag.name.clone(),
        "unknown",
        "diagnostic",
        0,
        code,
    );
    u.diagnostics.push(d);
    u
}

pub fn inventory(movie: &Movie) -> Vec<ScriptUnit> {
    let mut units = vec![];
    let mut code_bytes = 0;
    for tag in &movie.tags {
        if units.len() >= MAX_UNITS {
            let mut u = failure(
                tag,
                &[],
                diag(
                    "script_inventory_limit",
                    "inventory exceeds 100,000 units; remaining tags not inventoried",
                    0,
                ),
            );
            u.byte_len = 0;
            units.push(u);
            return units;
        }
        let data = movie.tag_data(tag);
        let parsed = (|| -> Result<Vec<ScriptUnit>, Diagnostic> {
            let mut output = vec![];
            match tag.code {
                72 | 82 => {
                    let start = if tag.code == 82 {
                        let mut r = Reader::new(data);
                        r.take(4)?;
                        cstring(&mut r)?;
                        r.pos
                    } else {
                        0
                    };
                    let bytes = &data[start..];
                    let a = match Abc::parse(bytes) {
                        Ok(a) => Arc::new(a),
                        Err(mut d) => {
                            d.offset = d.offset.map(|x| x + start);
                            let mut u = failure(tag, bytes, d);
                            u.language = "avm2".into();
                            u.kind = "abc_raw".into();
                            u.byte_start = tag.data_start + start;
                            u.byte_len = bytes.len();
                            output.push(u);
                            return Ok(output);
                        }
                    };
                    // A legal ABC can contain signatures only. Keep it visible in inventory.
                    if a.bodies.is_empty() {
                        let mut u = unit(
                            tag,
                            format!("{}/abc", tag.id),
                            tag.symbol
                                .clone()
                                .unwrap_or_else(|| "ABC signatures".into()),
                            "avm2",
                            "abc_outline",
                            start,
                            bytes,
                        );
                        u.abc_version = Some(format!("{}.{}", a.major, a.minor));
                        u.abc_sha256 = Some(a.raw_sha256.clone());
                        u.context = Some(a);
                        output.push(u);
                    } else {
                        for (body_index, body) in a
                            .bodies
                            .iter()
                            .enumerate()
                            .take(MAX_UNITS.saturating_sub(units.len()))
                        {
                            let method = body.method as usize;
                            let mut u = unit(
                                tag,
                                format!("{}/method:{method}", tag.id),
                                a.method_name(method),
                                "avm2",
                                "method",
                                start + body.code_offset,
                                &body.code,
                            );
                            u.method_index = Some(method);
                            u.body_index = Some(body_index);
                            u.abc_version = Some(format!("{}.{}", a.major, a.minor));
                            u.abc_sha256 = Some(a.raw_sha256.clone());
                            let m = &a.methods[method];
                            u.parameters = m
                                .param_types
                                .iter()
                                .enumerate()
                                .map(|(i, _)| {
                                    m.param_names
                                        .get(i)
                                        .and_then(|n| a.string(*n))
                                        .filter(|n| !n.is_empty())
                                        .map(str::to_owned)
                                        .unwrap_or_else(|| format!("arg{}", i + 1))
                                })
                                .collect();
                            u.context = Some(a.clone());
                            output.push(u);
                        }
                        if a.bodies.len() > MAX_UNITS.saturating_sub(units.len()) {
                            output.push(failure(
                                tag,
                                &[],
                                diag(
                                    "script_inventory_limit",
                                    "ABC methods exceed remaining inventory unit budget",
                                    start,
                                ),
                            ));
                        }
                    }
                }
                12 | 59 => {
                    let start = if tag.code == 59 { 2 } else { 0 };
                    if data.len() < start {
                        return Err(diag(
                            "script_container_truncated",
                            "DoInitAction lacks character id",
                            0,
                        ));
                    }
                    output.push(unit(
                        tag,
                        format!("{}/actions", tag.id),
                        tag.name.clone(),
                        "avm1",
                        if start == 0 { "frame" } else { "init" },
                        start,
                        &data[start..],
                    ));
                }
                7 | 34 | 26 | 70 | 94 => {
                    for (ordinal, (kind, start, end, event)) in
                        embedded(data, tag.code, movie.info.version)?
                            .into_iter()
                            .enumerate()
                    {
                        let mut u = unit(
                            tag,
                            format!("{}/{kind}:{ordinal}", tag.id),
                            format!("{} {kind} {ordinal} flags={event:#x}", tag.name),
                            "avm1",
                            kind,
                            start,
                            &data[start..end],
                        );
                        u.avm1_flags = None;
                        u.event_flags = if kind == "button" { None } else { Some(event) };
                        output.push(u);
                    }
                }
                _ => {}
            }
            Ok(output)
        })();
        let output = match parsed {
            Ok(v) => v,
            Err(d) => vec![failure(tag, data, d)],
        };
        for mut u in output {
            if units.len() >= MAX_UNITS || u.code.len() > MAX_CODE_BYTES.saturating_sub(code_bytes)
            {
                u.code.clear();
                u.diagnostics.push(diag(
                    "script_inventory_limit",
                    "inventory units or copied code budget exceeded",
                    u.byte_start,
                ));
                u.kind = "diagnostic".into();
                units.push(u);
                return units;
            }
            code_bytes += u.code.len();
            if u.language == "avm1" {
                let nested = discover_functions(
                    &u,
                    &u.code,
                    u.byte_start,
                    &u.avm1_constants,
                    true,
                    0,
                    MAX_UNITS.saturating_sub(units.len() + 1),
                );
                units.push(u);
                for mut child in nested {
                    if child.code.len() > MAX_CODE_BYTES.saturating_sub(code_bytes) {
                        child.code.clear();
                        child.diagnostics.push(diag(
                            "script_inventory_limit",
                            "copied nested function byte budget exceeded",
                            child.byte_start,
                        ));
                        child.kind = "diagnostic".into();
                        units.push(child);
                        return units;
                    }
                    code_bytes += child.code.len();
                    units.push(child);
                }
            } else {
                units.push(u);
            }
        }
    }
    units
}

fn discover_functions(
    parent: &ScriptUnit,
    bytes: &[u8],
    base: usize,
    initial_pool: &[String],
    initial_pool_verified: bool,
    depth: usize,
    remaining: usize,
) -> Vec<ScriptUnit> {
    if remaining == 0 {
        return vec![];
    }
    if depth >= 64 {
        let mut u = parent.clone();
        u.id = format!("{}/nest-limit:{base}", parent.id);
        u.kind = "diagnostic".into();
        u.code.clear();
        u.diagnostics.push(diag(
            "script_nesting_limit",
            "AVM1 nested body depth exceeds 64",
            base,
        ));
        return vec![u];
    }
    let assembly = crate::avm1::disassemble_with_pool(bytes, initial_pool);
    // Linear pool lineage is not a runtime data-flow proof. A branch may
    // bypass a later pool, and a loop can revisit a declaration with another
    // pool active. Keep the byte inventory but reject source reconstruction.
    let first_branch = assembly
        .instructions
        .iter()
        .filter(|i| matches!(i.opcode, 0x99 | 0x9d | 0x8a | 0x8d))
        .map(|i| i.offset)
        .min();
    let pool_offsets = assembly
        .instructions
        .iter()
        .filter(|i| i.opcode == 0x88)
        .map(|i| i.offset)
        .collect::<Vec<_>>();
    let pool_verified = initial_pool_verified
        && !first_branch.is_some_and(|branch| {
            pool_offsets.len() > 1 || pool_offsets.iter().any(|offset| *offset > branch)
        });
    let scan_limit = assembly
        .diagnostics
        .iter()
        .find(|d| {
            matches!(
                d.code.as_str(),
                "avm_text_limit" | "avm_pcode_limit" | "avm_instruction_limit"
            )
        })
        .cloned();
    let mut output = vec![];
    let mut pool = initial_pool.to_vec();
    for i in assembly.instructions {
        if output.len() >= remaining {
            break;
        }
        if i.opcode == 0x88
            && let Some(Operand::Values(v)) = i.operands.first()
        {
            pool = v
                .iter()
                .filter_map(|s| serde_json::from_str::<String>(s).ok())
                .collect();
        }
        if let Some(body) = i.body {
            let body_base = base + i.offset + i.length - body.len();
            if matches!(i.opcode, 0x8e | 0x9b) {
                let name = i
                    .operands
                    .first()
                    .and_then(|o| {
                        if let Operand::Text(s) = o {
                            Some(s.clone())
                        } else {
                            None
                        }
                    })
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| format!("anonymous_{:x}", body_base));
                let flags = if i.opcode == 0x8e {
                    i.operands.get(2).and_then(|o| {
                        if let Operand::Unsigned(v) = o {
                            Some(*v as u16)
                        } else {
                            None
                        }
                    })
                } else {
                    Some(0)
                };
                let register_count = if i.opcode == 0x8e {
                    i.operands.get(1).and_then(|o| {
                        if let Operand::Unsigned(v) = o {
                            u8::try_from(*v).ok()
                        } else {
                            None
                        }
                    })
                } else {
                    None
                };
                let registers = i
                    .operands
                    .iter()
                    .find_map(|o| {
                        if let Operand::Registers(v) = o {
                            Some(v.clone())
                        } else {
                            None
                        }
                    })
                    .unwrap_or_default();
                let mut u = ScriptUnit {
                    id: format!("{}/function:{body_base:x}", parent.id),
                    name,
                    language: "avm1".into(),
                    kind: "function".into(),
                    tag_path: parent.tag_path.clone(),
                    byte_start: body_base,
                    byte_len: body.len(),
                    sha256: crate::hex(&Sha256::digest(&body)),
                    parameters: i.parameters,
                    avm1_flags: flags,
                    event_flags: parent.event_flags,
                    avm1_registers: registers,
                    avm1_register_count: register_count,
                    // Identity of the captured pool is independently checked
                    // by the benchmark bridge, including empty pools. Code
                    // hashes alone cannot detect wrong inherited literals.
                    avm1_constants_sha256: Some(crate::hex(&Sha256::digest(
                        serde_json::to_vec(&pool).expect("string pool serializes"),
                    ))),
                    avm1_constants: pool.clone(),
                    code: body.clone(),
                    ..Default::default()
                };
                if !pool_verified {
                    u.diagnostics.push(diag(
                        "AVM1_POOL_CONTEXT_UNPROVEN",
                        "Captured constant pool follows ambiguous control flow; linear lineage is not a runtime proof",
                        body_base,
                    ));
                }
                let children = discover_functions(
                    &u,
                    &body,
                    body_base,
                    &pool,
                    pool_verified,
                    depth + 1,
                    remaining.saturating_sub(output.len() + 1),
                );
                output.push(u);
                output.extend(children);
            } else if i.opcode == 0x8f {
                let sizes = i
                    .operands
                    .iter()
                    .skip(1)
                    .take(3)
                    .filter_map(|o| {
                        if let Operand::Unsigned(n) = o {
                            Some(*n as usize)
                        } else {
                            None
                        }
                    })
                    .collect::<Vec<_>>();
                let mut offset = 0;
                for size in sizes {
                    let children = discover_functions(
                        parent,
                        &body[offset..offset + size],
                        body_base + offset,
                        &pool,
                        pool_verified,
                        depth + 1,
                        remaining.saturating_sub(output.len()),
                    );
                    output.extend(children);
                    offset += size;
                }
            } else {
                output.extend(discover_functions(
                    parent,
                    &body,
                    body_base,
                    &pool,
                    pool_verified,
                    depth + 1,
                    remaining.saturating_sub(output.len()),
                ));
            }
        }
    }
    if let Some(d) = scan_limit {
        let warning = diag(
            "script_function_scan_limit",
            format!(
                "Nested function inventory is partial due to {}; parent unit preserves complete raw bytecode",
                d.code
            ),
            base + d.offset.unwrap_or(0),
        );
        if output.len() < remaining {
            output.push(ScriptUnit {
                id: format!("{}/inventory-partial:{base:x}", parent.id),
                name: "Partial nested function inventory".into(),
                language: "avm1".into(),
                kind: "diagnostic".into(),
                tag_path: parent.tag_path.clone(),
                event_flags: parent.event_flags,
                byte_start: base,
                diagnostics: vec![warning],
                ..Default::default()
            });
        } else if let Some(last) = output.last_mut() {
            last.diagnostics.push(warning);
        }
    }
    output
}

pub fn class_outline(units: &[ScriptUnit]) -> Vec<ClassInfo> {
    let mut seen = std::collections::HashSet::new();
    let mut result = vec![];
    for u in units {
        let Some(a) = &u.context else { continue };
        if !seen.insert(u.tag_path.clone()) {
            continue;
        }
        for (index, i) in a.instances.iter().enumerate() {
            let traits = i.traits.iter().chain(a.classes[index].traits.iter());
            let mut fields = vec![];
            let mut methods = vec![format!("{} constructor", a.multiname_display(i.name))];
            for t in traits {
                if let Some(m) = t.method {
                    methods.push(format!("{} (method {m})", a.multiname_display(t.name)));
                } else if matches!(t.kind, 0 | 6) {
                    fields.push(format!(
                        "{}: {}",
                        a.multiname_display(t.name),
                        a.multiname_display(t.type_name)
                    ));
                }
            }
            result.push(ClassInfo {
                name: a.multiname_display(i.name),
                superclass: a.multiname_display(i.super_name),
                interfaces: i
                    .interfaces
                    .iter()
                    .map(|n| a.multiname_display(*n))
                    .collect(),
                fields,
                methods,
                source_tag: u.tag_path.clone(),
            });
        }
    }
    result
}

struct Bits<'a> {
    data: &'a [u8],
    bit: usize,
}
impl Bits<'_> {
    fn read(&mut self, n: usize) -> Result<u32, Diagnostic> {
        if n > 32
            || self
                .bit
                .checked_add(n)
                .is_none_or(|end| end > self.data.len() * 8)
        {
            return Err(diag(
                "script_container_truncated",
                "truncated bit structure",
                self.bit / 8,
            ));
        }
        let mut value = 0;
        for _ in 0..n {
            value = (value << 1) | u32::from((self.data[self.bit / 8] >> (7 - self.bit % 8)) & 1);
            self.bit += 1;
        }
        Ok(value)
    }
    fn skip(&mut self, n: usize) -> Result<(), Diagnostic> {
        if self
            .bit
            .checked_add(n)
            .is_none_or(|end| end > self.data.len() * 8)
        {
            return Err(diag(
                "script_container_truncated",
                "truncated bit structure",
                self.bit / 8,
            ));
        }
        self.bit += n;
        Ok(())
    }
}
fn matrix(r: &mut Reader<'_>) -> Result<(), Diagnostic> {
    let mut b = Bits {
        data: &r.bytes[r.pos..],
        bit: 0,
    };
    if b.read(1)? != 0 {
        let n = b.read(5)? as usize;
        b.skip(n * 2)?;
    }
    if b.read(1)? != 0 {
        let n = b.read(5)? as usize;
        b.skip(n * 2)?;
    }
    let n = b.read(5)? as usize;
    b.skip(n * 2)?;
    r.take(b.bit.div_ceil(8))?;
    Ok(())
}
fn color(r: &mut Reader<'_>) -> Result<(), Diagnostic> {
    let mut b = Bits {
        data: &r.bytes[r.pos..],
        bit: 0,
    };
    let add = b.read(1)? as usize;
    let multiply = b.read(1)? as usize;
    let n = b.read(4)? as usize;
    b.skip(n * 4 * (add + multiply))?;
    r.take(b.bit.div_ceil(8))?;
    Ok(())
}
fn filters(r: &mut Reader<'_>) -> Result<(), Diagnostic> {
    for _ in 0..r.byte()? {
        let kind = r.byte()?;
        let size = match kind {
            0 => 23,
            1 => 9,
            2 => 15,
            3 => 27,
            6 => 80,
            4 | 7 => r.byte()? as usize * 5 + 19,
            5 => {
                let x = r.byte()? as usize;
                let y = r.byte()? as usize;
                13 + 4 * x * y
            }
            _ => {
                return Err(diag(
                    "script_filter_unsupported",
                    format!("unknown filter {kind}; clip actions retained raw"),
                    r.pos - 1,
                ));
            }
        };
        r.take(size)?;
    }
    Ok(())
}
fn embedded(
    data: &[u8],
    tag: u16,
    version: u8,
) -> Result<Vec<(&'static str, usize, usize, u32)>, Diagnostic> {
    let mut r = Reader::new(data);
    let mut result = vec![];
    match tag {
        7 => {
            r.take(2)?;
            loop {
                let flags = r.byte()?;
                if flags == 0 {
                    break;
                }
                if flags & !15 != 0 {
                    return Err(diag(
                        "script_button_flags",
                        "unknown DefineButton record flag",
                        r.pos - 1,
                    ));
                }
                r.take(4)?;
                matrix(&mut r)?;
            }
            if r.pos < data.len() {
                result.push(("button", r.pos, data.len(), 0));
            }
        }
        34 => {
            r.take(3)?;
            let action_offset = r.u16()? as usize;
            if action_offset == 0 {
                return Ok(result);
            }
            let pos = 3 + action_offset;
            if pos < 5 || pos >= data.len() {
                return Err(diag(
                    "script_button_offset",
                    "invalid Button2 action offset",
                    3,
                ));
            }
            r.pos = pos;
            loop {
                let start = r.pos;
                let size = r.u16()? as usize;
                let flags = r.u16()? as u32;
                let end = if size == 0 {
                    data.len()
                } else {
                    start.checked_add(size).ok_or_else(|| {
                        diag("script_button_size", "button record overflow", start)
                    })?
                };
                if end < r.pos || end > data.len() {
                    return Err(diag(
                        "script_button_size",
                        "invalid ButtonCondAction size",
                        start,
                    ));
                }
                result.push(("button2", r.pos, end, flags));
                r.pos = end;
                if size == 0 {
                    break;
                }
                if r.remaining() == 0 {
                    return Err(diag(
                        "script_button_end",
                        "Button2 lacks final size-zero record",
                        r.pos,
                    ));
                }
            }
        }
        26 | 70 | 94 => {
            let flags = r.byte()?;
            let extra = if tag == 70 || tag == 94 { r.byte()? } else { 0 };
            r.take(2)?;
            if extra & 8 != 0 || (extra & 16 != 0 && flags & 2 != 0) {
                cstring(&mut r)?;
            }
            if flags & 2 != 0 {
                r.take(2)?;
            }
            if flags & 4 != 0 {
                matrix(&mut r)?;
            }
            if flags & 8 != 0 {
                color(&mut r)?;
            }
            if flags & 16 != 0 {
                r.take(2)?;
            }
            if flags & 32 != 0 {
                cstring(&mut r)?;
            }
            if flags & 64 != 0 {
                r.take(2)?;
            }
            if extra & 1 != 0 {
                filters(&mut r)?;
            }
            if extra & 2 != 0 {
                r.take(1)?;
            }
            if extra & 4 != 0 {
                r.take(1)?;
            }
            if extra & 32 != 0 {
                r.take(1)?;
            }
            if extra & 64 != 0 {
                r.take(4)?;
            }
            if flags & 128 == 0 {
                return Ok(result);
            }
            let width = if version >= 6 { 4 } else { 2 };
            r.take(2 + width)?;
            loop {
                let raw = r.take(width)?;
                let event = if width == 4 {
                    u32::from_le_bytes(raw.try_into().unwrap())
                } else {
                    u16::from_le_bytes(raw.try_into().unwrap()) as u32
                };
                if event == 0 {
                    break;
                }
                let size = u32::from_le_bytes(r.take(4)?.try_into().unwrap()) as usize;
                let end = r
                    .pos
                    .checked_add(size)
                    .ok_or_else(|| diag("script_clip_size", "clip event size overflow", r.pos))?;
                if event & 0x20000 != 0 {
                    r.take(1)?;
                }
                if end < r.pos || end > data.len() {
                    return Err(diag("script_clip_size", "clip record exceeds tag", r.pos));
                }
                result.push(("clip", r.pos, end, event));
                r.pos = end;
            }
            if tag != 94 && r.remaining() != 0 {
                return Err(diag(
                    "script_clip_tail",
                    "trailing bytes after clip actions",
                    r.pos,
                ));
            }
        }
        _ => {}
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn movie(tags: &[(u16, Vec<u8>)]) -> Movie {
        let mut b = vec![b'F', b'W', b'S', 10, 0, 0, 0, 0, 0x08, 0, 0, 12, 1, 0];
        for (code, data) in tags {
            let n = data.len();
            if n < 63 {
                b.extend((code << 6 | n as u16).to_le_bytes());
            } else {
                b.extend((code << 6 | 63).to_le_bytes());
                b.extend((n as u32).to_le_bytes());
            }
            b.extend(data);
        }
        b.extend([0, 0]);
        let len = b.len() as u32;
        b[4..8].copy_from_slice(&len.to_le_bytes());
        Movie::from_bytes(&b, "fixture.swf").unwrap()
    }
    #[test]
    fn frame_and_init() {
        let m = movie(&[(12, vec![6, 0]), (59, vec![1, 0, 7, 0])]);
        let u = inventory(&m);
        assert_eq!(u.len(), 2);
        assert_eq!(u[0].code, [6, 0]);
        assert_eq!(u[1].code, [7, 0]);
        assert_eq!(u[1].byte_start, m.tags[1].data_start + 2);
    }
    #[test]
    fn embedded_function() {
        let m = movie(&[(12, vec![0x9b, 6, 0, b'f', 0, 0, 0, 1, 0, 0, 0])]);
        let u = inventory(&m);
        assert_eq!(u.len(), 2);
        assert_eq!(u[1].kind, "function");
        assert_eq!(u[1].name, "f");
        assert_eq!(u[1].code, [0]);
    }
    #[test]
    fn captured_function_pool() {
        let m = movie(&[(
            12,
            vec![
                0x88, 4, 0, 1, 0, b'x', 0, 0x9b, 6, 0, b'f', 0, 0, 0, 7, 0, 0x96, 2, 0, 8, 0, 0x3e,
                0, 0,
            ],
        )]);
        let u = inventory(&m);
        assert_eq!(u.len(), 2);
        assert_eq!(u[1].avm1_constants, ["x"]);
        assert_eq!(
            u[1].avm1_constants_sha256.as_deref(),
            Some(crate::hex(&Sha256::digest(b"[\"x\"]")).as_str())
        );
        let d = crate::disassemble(&u[1]);
        assert!(d.diagnostics.is_empty());
        assert!(
            matches!(&d.instructions[0].operands[0], Operand::Values(v) if v == &vec!["\"x\"".to_string()])
        );
    }
    #[test]
    fn nested_sprite_timeline() {
        let m = movie(&[(39, vec![1, 0, 1, 0, 2, 3, 6, 0, 0, 0])]);
        let u = inventory(&m);
        assert_eq!(u.len(), 1);
        assert_eq!(u[0].tag_path, [0, 0]);
        assert_eq!(u[0].code, [6, 0]);
    }
    #[test]
    fn nested_scan_limit_is_visible_and_parent_raw_is_intact() {
        let mut code = vec![0x06; crate::MAX_DECODED_INSTRUCTIONS];
        code.extend([0x9b, 6, 0, b'f', 0, 0, 0, 1, 0, 0, 0]);
        let m = movie(&[(12, code.clone())]);
        let u = inventory(&m);
        assert_eq!(u[0].code, code);
        assert!(u.iter().any(|u| {
            u.diagnostics
                .iter()
                .any(|d| d.code == "script_function_scan_limit")
        }));
        assert!(!u.iter().any(|u| u.kind == "function"));
    }
    #[test]
    fn button_and_clip() {
        let button = vec![1, 0, 0, 6, 0];
        let clip = vec![
            0x80, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 2, 0, 0, 0, 6, 0, 0, 0, 0, 0,
        ];
        let m = movie(&[(7, button), (26, clip)]);
        let u = inventory(&m);
        assert_eq!(u[0].kind, "button");
        assert_eq!(u[0].event_flags, None);
        let clip = u.iter().find(|x| x.kind == "clip").unwrap();
        assert_eq!(clip.event_flags, Some(1));
    }
    #[test]
    fn malformed_counted() {
        let m = movie(&[(82, vec![0, 0, 0, 0, 0, 16, 0, 48, 0])]);
        let u = inventory(&m);
        assert_eq!(u.len(), 1);
        assert_eq!(u[0].kind, "abc_raw");
        assert_eq!(u[0].diagnostics[0].code, "abc_version_unsupported");
    }
    #[test]
    fn deprecated_doabc1_is_tag72() {
        let abc = vec![
            16, 0, 46, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1,
            0x47, 0, 0,
        ];
        let m = movie(&[(72, abc)]);
        let u = inventory(&m);
        assert_eq!(u.len(), 1);
        assert_eq!(u[0].kind, "method");
        assert_eq!(u[0].method_index, Some(0));
        assert_eq!(u[0].abc_version.as_deref(), Some("46.16"));
        assert_eq!(u[0].code, [0x47]);
        assert!(crate::disassemble(&u[0]).diagnostics.is_empty());
    }
    #[test]
    fn button2_boundary() {
        let items = embedded(&[1, 0, 0, 2, 0, 0, 0, 1, 0, 6, 0], 34, 10).unwrap();
        assert_eq!(items[0], ("button2", 9, 11, 1));
    }
}
