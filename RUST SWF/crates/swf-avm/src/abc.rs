//! Bounded ABC reader. Numeric pools preserve original bits, including NaNs.
use crate::{Diagnostic, diag};
use serde::Serialize;
use sha2::{Digest, Sha256};

#[derive(Debug, Clone)]
pub struct Abc {
    /// SHA-256 of the complete ABC bytes received by the reader.
    pub raw_sha256: String,
    pub major: u16,
    pub minor: u16,
    pub integers: Vec<i32>,
    pub uints: Vec<u32>,
    pub doubles: Vec<[u8; 8]>,
    pub decimals: Vec<[u8; 16]>,
    pub floats: Vec<[u8; 4]>,
    pub strings: Vec<String>,
    pub namespaces: Vec<Namespace>,
    pub ns_sets: Vec<Vec<u32>>,
    pub multinames: Vec<Multiname>,
    pub methods: Vec<MethodInfo>,
    pub instances: Vec<InstanceInfo>,
    pub classes: Vec<Class>,
    pub scripts: Vec<Script>,
    pub bodies: Vec<MethodBody>,
    pub metadata_count: usize,
    pub metadata: Vec<MetadataInfo>,
}
/// Uninterpreted ABC metadata: vendors can add arbitrary names and key/value pairs.
/// Original varints are retained for a future lossless writer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MetadataInfo {
    pub name: u32,
    pub keys: Vec<u32>,
    pub values: Vec<u32>,
    pub raw: Vec<u8>,
}
/// Public namespace identity is URI based; Namespace and PackageNamespace are equivalent.
/// Protected, internal and private namespaces deliberately do not share this representation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublicProperty {
    pub namespace_uri: String,
    pub name: Option<String>,
}
#[derive(Debug, Clone)]
pub struct Namespace {
    pub kind: u8,
    pub name: u32,
}
#[derive(Debug, Clone, Default)]
pub struct Multiname {
    pub kind: u8,
    pub namespace: u32,
    pub name: u32,
    pub ns_set: u32,
    pub parameters: Vec<u32>,
}
#[derive(Debug, Clone)]
pub struct MethodInfo {
    pub return_type: u32,
    pub param_types: Vec<u32>,
    pub name: u32,
    pub flags: u8,
    pub options: Vec<(u32, u8)>,
    pub param_names: Vec<u32>,
}
#[derive(Debug, Clone)]
pub struct TraitInfo {
    pub name: u32,
    pub kind: u8,
    pub attributes: u8,
    pub slot_id: u32,
    pub type_name: u32,
    pub value: u32,
    pub value_kind: u8,
    pub method: Option<u32>,
    pub class: Option<u32>,
    pub metadata: Vec<u32>,
}
#[derive(Debug, Clone)]
pub struct InstanceInfo {
    pub name: u32,
    pub super_name: u32,
    pub flags: u8,
    pub protected_namespace: Option<u32>,
    pub interfaces: Vec<u32>,
    pub init: u32,
    pub traits: Vec<TraitInfo>,
}
#[derive(Debug, Clone)]
pub struct Class {
    pub init: u32,
    pub traits: Vec<TraitInfo>,
}
#[derive(Debug, Clone)]
pub struct Script {
    pub init: u32,
    pub traits: Vec<TraitInfo>,
}
#[derive(Debug, Clone, Serialize)]
pub struct ExceptionInfo {
    pub from: u32,
    pub to: u32,
    pub target: u32,
    pub exception_type: u32,
    pub variable_name: u32,
}
#[derive(Debug, Clone)]
pub struct MethodBody {
    pub method: u32,
    pub max_stack: u32,
    pub local_count: u32,
    pub init_scope_depth: u32,
    pub max_scope_depth: u32,
    pub code: Vec<u8>,
    pub code_offset: usize,
    pub exceptions: Vec<ExceptionInfo>,
    pub traits: Vec<TraitInfo>,
}

pub(crate) struct Reader<'a> {
    pub bytes: &'a [u8],
    pub pos: usize,
}
impl<'a> Reader<'a> {
    pub fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, pos: 0 }
    }
    pub fn remaining(&self) -> usize {
        self.bytes.len() - self.pos
    }
    pub fn fail(&self, msg: impl Into<String>) -> Diagnostic {
        diag("abc_invalid", msg, self.pos)
    }
    pub fn take(&mut self, n: usize) -> Result<&'a [u8], Diagnostic> {
        let end = self
            .pos
            .checked_add(n)
            .ok_or_else(|| self.fail("offset overflow"))?;
        if end > self.bytes.len() {
            return Err(self.fail("truncated bytes"));
        }
        let s = &self.bytes[self.pos..end];
        self.pos = end;
        Ok(s)
    }
    pub fn byte(&mut self) -> Result<u8, Diagnostic> {
        Ok(self.take(1)?[0])
    }
    pub fn u16(&mut self) -> Result<u16, Diagnostic> {
        Ok(u16::from_le_bytes(self.take(2)?.try_into().unwrap()))
    }
    pub fn u32var(&mut self) -> Result<u32, Diagnostic> {
        let mut value = 0u32;
        for shift in (0..35).step_by(7) {
            let b = self.byte()?;
            if shift == 28 && b & 0xf0 != 0 {
                return Err(self.fail("invalid 32-bit varint"));
            }
            value |= u32::from(b & 0x7f) << shift;
            if b & 0x80 == 0 {
                return Ok(value);
            }
        }
        Err(self.fail("unterminated varint"))
    }
    pub fn u30(&mut self) -> Result<u32, Diagnostic> {
        let n = self.u32var()?;
        if n > 0x3fff_ffff {
            Err(self.fail("u30 exceeds 30 bits"))
        } else {
            Ok(n)
        }
    }
    pub fn s24(&mut self) -> Result<i32, Diagnostic> {
        let b = self.take(3)?;
        let v = i32::from(b[0]) | (i32::from(b[1]) << 8) | (i32::from(b[2]) << 16);
        Ok(if v & 0x800000 != 0 { v - 0x1000000 } else { v })
    }
    pub fn count(&mut self, min_bytes: usize) -> Result<usize, Diagnostic> {
        let n = self.u30()? as usize;
        if n > 1_000_000 || n > self.remaining() / min_bytes.max(1) {
            return Err(self.fail("count exceeds remaining bytes or allocation limit"));
        }
        Ok(n)
    }
    fn pool_count(&mut self, min_bytes: usize) -> Result<usize, Diagnostic> {
        let n = self.u30()?.saturating_sub(1) as usize;
        if n > 1_000_000 || n > self.remaining() / min_bytes.max(1) {
            return Err(self.fail("pool count exceeds remaining bytes or allocation limit"));
        }
        Ok(n)
    }
    fn refs(&mut self, min_bytes: usize) -> Result<Vec<u32>, Diagnostic> {
        let n = self.count(min_bytes)?;
        (0..n).map(|_| self.u30()).collect()
    }
    fn traits(&mut self) -> Result<Vec<TraitInfo>, Diagnostic> {
        let n = self.count(3)?;
        let mut traits = Vec::with_capacity(n);
        for _ in 0..n {
            let name = self.u30()?;
            let flags = self.byte()?;
            let kind = flags & 15;
            let mut t = TraitInfo {
                name,
                kind,
                attributes: flags >> 4,
                slot_id: 0,
                type_name: 0,
                value: 0,
                value_kind: 0,
                method: None,
                class: None,
                metadata: vec![],
            };
            match kind {
                0 | 6 => {
                    t.slot_id = self.u30()?;
                    t.type_name = self.u30()?;
                    t.value = self.u30()?;
                    if t.value != 0 {
                        t.value_kind = self.byte()?;
                    }
                }
                1..=3 => {
                    t.slot_id = self.u30()?;
                    t.method = Some(self.u30()?);
                }
                4 => {
                    t.slot_id = self.u30()?;
                    t.class = Some(self.u30()?);
                }
                5 => {
                    t.slot_id = self.u30()?;
                    t.method = Some(self.u30()?);
                }
                _ => return Err(self.fail("unknown trait kind")),
            }
            if flags & 0x80 != 0 {
                return Err(self.fail("unknown trait attribute"));
            }
            if flags & 0x40 != 0 {
                t.metadata = self.refs(1)?;
            }
            traits.push(t);
        }
        Ok(traits)
    }
}

impl Abc {
    pub fn parse(bytes: &[u8]) -> Result<Self, Diagnostic> {
        let mut r = Reader::new(bytes);
        let minor = r.u16()?;
        let major = r.u16()?;
        if !matches!((major, minor), (46, 16) | (46, 17) | (47, 16)) {
            return Err(diag(
                "abc_version_unsupported",
                format!("ABC {major}.{minor} is not a supported Flash dialect"),
                0,
            ));
        }
        let mut a = Self {
            raw_sha256: format!("{:x}", Sha256::digest(bytes)),
            major,
            minor,
            integers: vec![0],
            uints: vec![0],
            doubles: vec![[0; 8]],
            decimals: vec![[0; 16]],
            floats: vec![[0; 4]],
            strings: vec![String::new()],
            namespaces: vec![Namespace { kind: 0, name: 0 }],
            ns_sets: vec![vec![]],
            multinames: vec![Multiname::default()],
            methods: vec![],
            instances: vec![],
            classes: vec![],
            scripts: vec![],
            bodies: vec![],
            metadata_count: 0,
            metadata: vec![],
        };
        for _ in 0..r.pool_count(1)? {
            a.integers.push(r.u32var()? as i32);
        }
        for _ in 0..r.pool_count(1)? {
            a.uints.push(r.u32var()?);
        }
        for _ in 0..r.pool_count(8)? {
            a.doubles.push(r.take(8)?.try_into().unwrap());
        }
        if minor == 17 {
            for _ in 0..r.pool_count(16)? {
                a.decimals.push(r.take(16)?.try_into().unwrap());
            }
        }
        if major == 47 {
            for _ in 0..r.pool_count(4)? {
                a.floats.push(r.take(4)?.try_into().unwrap());
            }
        }
        for _ in 0..r.pool_count(1)? {
            let n = r.u30()? as usize;
            let s = r.take(n)?;
            a.strings.push(
                std::str::from_utf8(s)
                    .map_err(|_| r.fail("invalid UTF-8 in ABC string"))?
                    .to_owned(),
            );
        }
        for _ in 0..r.pool_count(2)? {
            let kind = r.byte()?;
            if !matches!(kind, 0x05 | 0x08 | 0x16 | 0x17 | 0x18 | 0x19 | 0x1a) {
                return Err(r.fail("unknown namespace kind"));
            }
            a.namespaces.push(Namespace {
                kind,
                name: r.u30()?,
            });
        }
        for _ in 0..r.pool_count(1)? {
            a.ns_sets.push(r.refs(1)?);
        }
        for _ in 0..r.pool_count(1)? {
            let kind = r.byte()?;
            let mut m = Multiname {
                kind,
                ..Default::default()
            };
            match kind {
                0x07 | 0x0d => {
                    m.namespace = r.u30()?;
                    m.name = r.u30()?;
                }
                0x0f | 0x10 => m.name = r.u30()?,
                0x11 | 0x12 => {}
                0x09 | 0x0e => {
                    m.name = r.u30()?;
                    m.ns_set = r.u30()?;
                }
                0x1b | 0x1c => m.ns_set = r.u30()?,
                0x1d => {
                    m.name = r.u30()?;
                    m.parameters = r.refs(1)?;
                    if m.parameters.is_empty() {
                        return Err(r.fail("TypeName needs parameters"));
                    }
                }
                _ => return Err(r.fail("unknown multiname kind")),
            }
            a.multinames.push(m);
        }
        for _ in 0..r.count(4)? {
            let param_count = r.count(1)?;
            let return_type = r.u30()?;
            let param_types = (0..param_count)
                .map(|_| r.u30())
                .collect::<Result<Vec<_>, _>>()?;
            let name = r.u30()?;
            let flags = r.byte()?;
            if flags & 0x10 != 0 {
                return Err(r.fail("unsupported method flag 0x10"));
            }
            let mut options = vec![];
            if flags & 8 != 0 {
                let n = r.count(2)?;
                if n > param_count {
                    return Err(r.fail("too many optional parameters"));
                }
                for _ in 0..n {
                    options.push((r.u30()?, r.byte()?));
                }
            }
            let param_names = if flags & 0x80 != 0 {
                (0..param_count)
                    .map(|_| r.u30())
                    .collect::<Result<Vec<_>, _>>()?
            } else {
                vec![]
            };
            a.methods.push(MethodInfo {
                return_type,
                param_types,
                name,
                flags,
                options,
                param_names,
            });
        }
        a.metadata_count = r.count(2)?;
        for _ in 0..a.metadata_count {
            let start = r.pos;
            let name = r.u30()?;
            a.ref_check(name, a.strings.len(), "metadata name", false)?;
            let n = r.count(2)?;
            // ABC metadata stores all keys first, then all values.
            let mut keys = Vec::with_capacity(n);
            let mut values = Vec::with_capacity(n);
            for destination in [&mut keys, &mut values] {
                for _ in 0..n {
                    let x = r.u30()?;
                    a.ref_check(x, a.strings.len(), "metadata string", false)?;
                    destination.push(x);
                }
            }
            a.metadata.push(MetadataInfo {
                name,
                keys,
                values,
                raw: bytes[start..r.pos].to_vec(),
            });
        }
        let classes = r.count(6)?;
        for _ in 0..classes {
            let name = r.u30()?;
            let super_name = r.u30()?;
            let flags = r.byte()?;
            if flags & !15 != 0 {
                return Err(r.fail("unknown instance flag"));
            }
            let protected_namespace = if flags & 8 != 0 { Some(r.u30()?) } else { None };
            let interfaces = r.refs(1)?;
            let init = r.u30()?;
            let traits = r.traits()?;
            a.instances.push(InstanceInfo {
                name,
                super_name,
                flags,
                protected_namespace,
                interfaces,
                init,
                traits,
            });
        }
        for _ in 0..classes {
            a.classes.push(Class {
                init: r.u30()?,
                traits: r.traits()?,
            });
        }
        for _ in 0..r.count(2)? {
            a.scripts.push(Script {
                init: r.u30()?,
                traits: r.traits()?,
            });
        }
        for _ in 0..r.count(8)? {
            let method = r.u30()?;
            let max_stack = r.u30()?;
            let local_count = r.u30()?;
            let init_scope_depth = r.u30()?;
            let max_scope_depth = r.u30()?;
            let len = r.u30()? as usize;
            let code_offset = r.pos;
            let code = r.take(len)?.to_vec();
            if max_scope_depth < init_scope_depth {
                return Err(r.fail("scope depth decreases"));
            }
            let mut exceptions = vec![];
            for _ in 0..r.count(5)? {
                let e = ExceptionInfo {
                    from: r.u30()?,
                    to: r.u30()?,
                    target: r.u30()?,
                    exception_type: r.u30()?,
                    variable_name: r.u30()?,
                };
                if e.from > e.to || e.to as usize > len || e.target as usize >= len {
                    return Err(r.fail("invalid exception range"));
                }
                exceptions.push(e);
            }
            let traits = r.traits()?;
            a.bodies.push(MethodBody {
                method,
                max_stack,
                local_count,
                init_scope_depth,
                max_scope_depth,
                code,
                code_offset,
                exceptions,
                traits,
            });
        }
        if r.remaining() != 0 {
            return Err(r.fail("trailing bytes: unsupported layout or malformed ABC"));
        }
        a.validate()?;
        Ok(a)
    }
    fn ref_check(
        &self,
        index: u32,
        len: usize,
        name: &str,
        nonzero: bool,
    ) -> Result<(), Diagnostic> {
        if index as usize >= len || (nonzero && index == 0) {
            Err(diag(
                "abc_pool_reference",
                format!("invalid {name} index {index}"),
                0,
            ))
        } else {
            Ok(())
        }
    }
    fn constant(&self, index: u32, kind: u8) -> Result<(), Diagnostic> {
        let len = match kind {
            0x03 => self.integers.len(),
            0x04 => self.uints.len(),
            0x06 => self.doubles.len(),
            0x01 => self.strings.len(),
            0x02 if self.minor == 17 => self.decimals.len(),
            0x02 if self.major == 47 => self.floats.len(),
            0x05 | 0x08 | 0x16 | 0x17 | 0x18 | 0x19 | 0x1a => self.namespaces.len(),
            0x00 | 0x0a | 0x0b | 0x0c => return Ok(()),
            _ => {
                return Err(diag(
                    "abc_pool_reference",
                    format!("unknown constant kind {kind:#x}"),
                    0,
                ));
            }
        };
        self.ref_check(index, len, "constant", false)
    }
    fn validate_traits(&self, traits: &[TraitInfo]) -> Result<(), Diagnostic> {
        for t in traits {
            self.ref_check(t.name, self.multinames.len(), "trait name", true)?;
            self.ref_check(t.type_name, self.multinames.len(), "trait type", false)?;
            if let Some(m) = t.method {
                self.ref_check(m, self.methods.len(), "trait method", false)?;
            }
            if let Some(c) = t.class {
                self.ref_check(c, self.classes.len(), "trait class", false)?;
            }
            if t.value != 0 {
                self.constant(t.value, t.value_kind)?;
            }
            for m in &t.metadata {
                self.ref_check(*m, self.metadata_count, "trait metadata", false)?;
            }
        }
        Ok(())
    }
    fn validate(&self) -> Result<(), Diagnostic> {
        for n in &self.namespaces {
            self.ref_check(n.name, self.strings.len(), "namespace string", false)?;
        }
        for set in &self.ns_sets {
            for n in set {
                self.ref_check(*n, self.namespaces.len(), "namespace set", true)?;
            }
        }
        for m in &self.multinames {
            self.ref_check(
                m.namespace,
                self.namespaces.len(),
                "multiname namespace",
                false,
            )?;
            self.ref_check(
                m.ns_set,
                self.ns_sets.len(),
                "multiname namespace set",
                false,
            )?;
            self.ref_check(
                m.name,
                if m.kind == 0x1d {
                    self.multinames.len()
                } else {
                    self.strings.len()
                },
                "multiname name",
                false,
            )?;
            for p in &m.parameters {
                self.ref_check(*p, self.multinames.len(), "TypeName parameter", false)?;
            }
        }
        for m in &self.methods {
            self.ref_check(m.return_type, self.multinames.len(), "return type", false)?;
            self.ref_check(m.name, self.strings.len(), "method name", false)?;
            for p in &m.param_types {
                self.ref_check(*p, self.multinames.len(), "parameter type", false)?;
            }
            for p in &m.param_names {
                self.ref_check(*p, self.strings.len(), "parameter name", false)?;
            }
            for (v, k) in &m.options {
                self.constant(*v, *k)?;
            }
        }
        for i in &self.instances {
            self.ref_check(i.name, self.multinames.len(), "instance name", true)?;
            self.ref_check(i.super_name, self.multinames.len(), "super type", false)?;
            if let Some(n) = i.protected_namespace {
                self.ref_check(n, self.namespaces.len(), "protected namespace", true)?;
            }
            for n in &i.interfaces {
                self.ref_check(*n, self.multinames.len(), "interface type", true)?;
            }
            self.ref_check(i.init, self.methods.len(), "instance initializer", false)?;
            self.validate_traits(&i.traits)?;
        }
        for c in &self.classes {
            self.ref_check(c.init, self.methods.len(), "class initializer", false)?;
            self.validate_traits(&c.traits)?;
        }
        for s in &self.scripts {
            self.ref_check(s.init, self.methods.len(), "script initializer", false)?;
            self.validate_traits(&s.traits)?;
        }
        let mut seen = std::collections::HashSet::new();
        for b in &self.bodies {
            self.ref_check(b.method, self.methods.len(), "body method", false)?;
            if !seen.insert(b.method) {
                return Err(diag("abc_invalid", "duplicate method body", b.code_offset));
            }
            if (b.local_count as usize) < self.methods[b.method as usize].param_types.len() + 1 {
                return Err(diag(
                    "abc_invalid",
                    "local count cannot contain receiver and parameters",
                    b.code_offset,
                ));
            }
            for e in &b.exceptions {
                self.ref_check(
                    e.exception_type,
                    self.multinames.len(),
                    "exception type",
                    false,
                )?;
                self.ref_check(
                    e.variable_name,
                    self.multinames.len(),
                    "exception variable",
                    false,
                )?;
            }
            self.validate_traits(&b.traits)?;
        }
        Ok(())
    }
    pub fn string(&self, index: u32) -> Option<&str> {
        self.strings.get(index as usize).map(String::as_str)
    }
    pub fn multiname_display(&self, index: u32) -> String {
        self.display_inner(index, &mut vec![])
    }
    fn display_inner(&self, index: u32, visited: &mut Vec<u32>) -> String {
        if index == 0 {
            return "*".into();
        }
        if visited.len() >= 64 || visited.contains(&index) {
            return format!("multiname[{index}]:cycle");
        }
        visited.push(index);
        let result = if let Some(m) = self.multinames.get(index as usize) {
            if m.kind == 0x1d {
                format!(
                    "{}.<{}>",
                    self.display_inner(m.name, visited),
                    m.parameters
                        .iter()
                        .map(|p| self.display_inner(*p, visited))
                        .collect::<Vec<_>>()
                        .join(",")
                )
            } else if matches!(m.kind, 0x07 | 0x0d) {
                let ns = self
                    .namespaces
                    .get(m.namespace as usize)
                    .and_then(|n| self.string(n.name))
                    .unwrap_or("");
                let name = self.string(m.name).unwrap_or("?");
                if ns.is_empty() {
                    name.into()
                } else {
                    format!("{ns}::{name}")
                }
            } else {
                format!(
                    "multiname[{index}]({})",
                    self.string(m.name).unwrap_or("runtime")
                )
            }
        } else {
            format!("multiname[{index}]:invalid")
        };
        visited.pop();
        result
    }
    /// Only an unqualified public QName can be emitted as an ordinary property.
    pub fn public_name(&self, index: u32) -> Option<String> {
        self.public_name_str(index).map(str::to_owned)
    }
    pub fn public_name_str(&self, index: u32) -> Option<&str> {
        let m = self.multinames.get(index as usize)?;
        let public_ns = |n: u32| {
            let ns = self.namespaces.get(n as usize)?;
            if matches!(ns.kind, 0x08 | 0x16) && self.string(ns.name)?.is_empty() {
                Some(())
            } else {
                None
            }
        };
        if m.kind == 0x07 {
            public_ns(m.namespace)?;
            self.string(m.name)
        } else if m.kind == 0x09 {
            let set = self.ns_sets.get(m.ns_set as usize)?;
            if set.is_empty() {
                return None;
            }
            for namespace in set {
                public_ns(*namespace)?;
            }
            self.string(m.name)
        } else {
            None
        }
    }
    pub fn public_namespace_uri(&self, index: u32) -> Option<&str> {
        let namespace = self.namespaces.get(index as usize)?;
        if !matches!(namespace.kind, 0x08 | 0x16) {
            return None;
        }
        let uri = self.string(namespace.name)?;
        // Playerglobal API markers need version-aware name resolution, not literal aliases.
        if uri
            .chars()
            .last()
            .is_some_and(|c| ('\u{e000}'..='\u{f8ff}').contains(&c))
        {
            return None;
        }
        Some(uri)
    }
    pub fn public_property(&self, index: u32) -> Option<PublicProperty> {
        let multiname = self.multinames.get(index as usize)?;
        let uri = match multiname.kind {
            0x07 => self.public_namespace_uri(multiname.namespace)?,
            0x09 | 0x1b => {
                let set = self.ns_sets.get(multiname.ns_set as usize)?;
                let first = self.public_namespace_uri(*set.first()?)?;
                if set
                    .iter()
                    .any(|namespace| self.public_namespace_uri(*namespace) != Some(first))
                {
                    return None;
                }
                first
            }
            _ => return None,
        };
        let name = if multiname.kind == 0x1b {
            None
        } else {
            let name = self.string(multiname.name)?;
            if name.is_empty() {
                return None;
            }
            Some(name.to_owned())
        };
        Some(PublicProperty {
            namespace_uri: uri.to_owned(),
            name,
        })
    }
    pub fn method_name(&self, index: usize) -> String {
        for i in &self.instances {
            if i.init as usize == index {
                return format!("{} constructor", self.multiname_display(i.name));
            }
            for t in &i.traits {
                if t.method.map(|m| m as usize) == Some(index) {
                    return self.multiname_display(t.name);
                }
            }
        }
        for (ci, c) in self.classes.iter().enumerate() {
            if c.init as usize == index {
                return format!(
                    "{} static initializer",
                    self.multiname_display(self.instances[ci].name)
                );
            }
            for t in &c.traits {
                if t.method.map(|m| m as usize) == Some(index) {
                    return self.multiname_display(t.name);
                }
            }
        }
        for s in &self.scripts {
            for t in &s.traits {
                if t.method.map(|m| m as usize) == Some(index) {
                    return self.multiname_display(t.name);
                }
            }
        }
        self.methods
            .get(index)
            .and_then(|m| self.string(m.name))
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| format!("method_{index}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn basic(major: u16, minor: u16) -> Vec<u8> {
        let mut b = vec![];
        b.extend(minor.to_le_bytes());
        b.extend(major.to_le_bytes());
        b.extend([0, 0, 0]);
        if minor == 17 {
            b.push(0);
        }
        if major == 47 {
            b.extend([2, 1, 0, 128, 127]);
        }
        b.extend([
            0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0x47, 0, 0,
        ]);
        b
    }
    #[test]
    fn extended_float_bits() {
        let a = Abc::parse(&basic(47, 16)).unwrap();
        assert_eq!(a.floats[1], [1, 0, 128, 127]);
        assert_eq!(a.bodies[0].code, [0x47]);
    }
    #[test]
    fn decimal_layout() {
        assert_eq!(Abc::parse(&basic(46, 17)).unwrap().bodies.len(), 1);
    }
    #[test]
    fn exact_layout() {
        let mut b = basic(46, 16);
        b.push(0);
        assert!(Abc::parse(&b).is_err());
        assert!(Abc::parse(&basic(47, 17)).is_err());
    }
    #[test]
    fn u30_bounds() {
        assert_eq!(
            Reader::new(&[255, 255, 255, 255, 3]).u30().unwrap(),
            0x3fffffff
        );
        assert!(Reader::new(&[255, 255, 255, 255, 4]).u30().is_err());
        assert!(Reader::new(&[128, 128, 128, 128, 128]).u30().is_err());
    }
    #[test]
    fn signed_word() {
        assert_eq!(Reader::new(&[127]).u32var().unwrap() as i32, 127);
        assert_eq!(
            Reader::new(&[255, 255, 255, 255, 15]).u32var().unwrap() as i32,
            -1
        );
    }
    #[test]
    fn excessive_counts() {
        let mut b = vec![16, 0, 46, 0, 255, 255, 255, 255, 3];
        b.extend([0; 40]);
        assert!(Abc::parse(&b).is_err());
    }
    #[test]
    fn invalid_string_index() {
        let mut b = basic(46, 16);
        b[14] = 1;
        assert_eq!(Abc::parse(&b).unwrap_err().code, "abc_pool_reference");
    }
    #[test]
    fn private_namespace_is_not_public() {
        let mut a = Abc::parse(&basic(46, 16)).unwrap();
        a.strings.push("property".into());
        a.namespaces.push(Namespace {
            kind: 0x05,
            name: 0,
        });
        a.multinames.push(Multiname {
            kind: 0x07,
            namespace: 1,
            name: 1,
            ..Default::default()
        });
        assert_eq!(a.public_name(1), None);
        a.namespaces[1].kind = 0x16;
        assert_eq!(a.public_name(1), Some("property".into()));
    }
    #[test]
    fn unknown_metadata_preserves_keys_values_and_original_encoding() {
        let mut bytes = vec![16, 0, 46, 0, 0, 0, 0, 5];
        for value in ["vendor:unknown", "custom", "value", "ñ"] {
            bytes.push(value.len() as u8);
            bytes.extend_from_slice(value.as_bytes());
        }
        bytes.extend([0, 0, 0, 0, 1]); // Namespaces, ns sets, multinames, methods, metadata count.
        let original = [0x81, 0, 2, 2, 0, 3, 4]; // Nonminimal name varint; all keys before values.
        bytes.extend(original);
        bytes.extend([0, 0, 0]); // Classes, scripts and method bodies.
        let parsed = Abc::parse(&bytes).unwrap();
        assert_eq!(parsed.metadata_count, 1);
        assert_eq!(parsed.metadata.len(), 1);
        assert_eq!(parsed.metadata[0].name, 1);
        assert_eq!(parsed.metadata[0].keys, [2, 0]);
        assert_eq!(parsed.metadata[0].values, [3, 4]);
        assert_eq!(parsed.metadata[0].raw, original);
        assert_eq!(parsed.strings[parsed.metadata[0].values[1] as usize], "ñ");
        let last_value = bytes.len() - 4;
        bytes[last_value] = 8;
        assert_eq!(Abc::parse(&bytes).unwrap_err().code, "abc_pool_reference");
    }
    #[test]
    fn public_namespace_identity_uses_uri_without_collapsing_private_or_internal() {
        let mut a = Abc::parse(&basic(46, 16)).unwrap();
        a.strings
            .extend(["value".into(), "urn:example".into(), "urn:other".into()]);
        a.namespaces.extend([
            Namespace {
                kind: 0x08,
                name: 2,
            },
            Namespace {
                kind: 0x16,
                name: 2,
            },
            Namespace {
                kind: 0x17,
                name: 2,
            },
            Namespace {
                kind: 0x05,
                name: 2,
            },
            Namespace {
                kind: 0x16,
                name: 3,
            },
        ]);
        a.ns_sets.push(vec![1, 2, 1]);
        a.multinames.push(Multiname {
            kind: 0x09,
            ns_set: 1,
            name: 1,
            ..Default::default()
        });
        let public = a.public_property(1).unwrap();
        assert_eq!(public.namespace_uri, "urn:example");
        assert_eq!(public.name.as_deref(), Some("value"));
        for namespace in [3, 4, 5] {
            a.ns_sets[1] = vec![1, namespace];
            assert!(a.public_property(1).is_none());
        }
        a.ns_sets[1] = vec![1, 2];
        a.multinames[1].kind = 0x1b;
        assert!(a.public_property(1).unwrap().name.is_none());
        a.multinames[1].kind = 0x0e;
        assert!(
            a.public_property(1).is_none(),
            "XML attributes cannot become ordinary properties"
        );
    }
}
