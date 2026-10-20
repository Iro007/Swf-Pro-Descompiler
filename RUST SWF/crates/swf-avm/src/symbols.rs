//! Exact ABC symbol identities and method ownership. GPL-3.0-or-later.
//! This module is an original implementation based on the ABC tables already
//! parsed by this crate. Display names are never used as binding identities.
use crate::{Abc, ScriptUnit, TraitInfo};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::sync::{Arc, Mutex, OnceLock, Weak};

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct NamespaceId {
    pub abc_identity: String,
    pub pool_index: u32,
    pub kind: u8,
    pub uri: String,
}
impl NamespaceId {
    /// Public namespace identity is URI based. A private namespace is unique
    /// to its ABC and pool entry even when its display URI is empty.
    pub fn same_binding(&self, other: &Self) -> bool {
        if matches!(self.kind, 0x08 | 0x16) && matches!(other.kind, 0x08 | 0x16) {
            self.uri == other.uri
        } else if self.kind == 0x05 || other.kind == 0x05 {
            self.kind == other.kind
                && self.abc_identity == other.abc_identity
                && self.pool_index == other.pool_index
        } else if matches!(self.kind, 0x18 | 0x1a) || matches!(other.kind, 0x18 | 0x1a) {
            self.kind == other.kind
                && self.abc_identity == other.abc_identity
                && self.pool_index == other.pool_index
        } else {
            self.kind == other.kind && self.uri == other.uri
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct QualifiedName {
    pub namespace: NamespaceId,
    pub name: String,
}
impl QualifiedName {
    pub fn same_binding(&self, other: &Self) -> bool {
        self.name == other.name && self.namespace.same_binding(&other.namespace)
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
pub enum TypeRef {
    Any,
    Named(QualifiedName),
    Parameterized {
        base: Box<TypeRef>,
        parameters: Vec<TypeRef>,
    },
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", content = "index", rename_all = "snake_case")]
pub enum TraitOwner {
    Script(usize),
    Instance(usize),
    Class(usize),
    Activation(usize),
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct TraitBinding {
    pub owner: TraitOwner,
    pub trait_index: usize,
    pub name_index: u32,
    pub name: QualifiedName,
    pub kind: u8,
    pub slot_id: u32,
    pub type_ref: Option<TypeRef>,
    pub method_index: Option<u32>,
    pub class_index: Option<u32>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AbcSymbolIndex {
    pub abc_identity: String,
    /// Internal parsed-context guard, separate from raw ABC interchange identity.
    pub context_fingerprint: String,
    pub traits: Vec<TraitBinding>,
    pub class_names: Vec<Option<QualifiedName>>,
    pub superclasses: Vec<Option<usize>>,
    pub complete: bool,
    #[serde(skip)]
    indexed_text: usize,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct MethodContext {
    pub abc_identity: String,
    pub abc_sha256: String,
    pub method_index: usize,
    pub body_index: usize,
    /// None denotes an unowned or multiply owned method; no this binding can
    /// then be inferred. Shared method bodies must not choose an arbitrary owner.
    pub owner: Option<TraitOwner>,
    pub script_owner: Option<usize>,
    pub trait_name_index: Option<u32>,
    pub parameter_types: Vec<Option<TypeRef>>,
    pub return_type: Option<TypeRef>,
    pub optional_defaults: Vec<(u32, u8)>,
    pub symbols: Arc<AbcSymbolIndex>,
}

impl AbcSymbolIndex {
    pub fn script_for_owner(&self, owner: TraitOwner) -> Option<usize> {
        match owner {
            TraitOwner::Script(n) => Some(n),
            TraitOwner::Instance(n) | TraitOwner::Class(n) => {
                let name = self.class_names.get(n)?.as_ref()?;
                let scripts = self
                    .traits
                    .iter()
                    .filter(|t| t.class_index == Some(n as u32) && t.name.same_binding(name))
                    .filter_map(|t| {
                        if let TraitOwner::Script(n) = t.owner {
                            Some(n)
                        } else {
                            None
                        }
                    })
                    .collect::<Vec<_>>();
                (scripts.len() == 1).then(|| scripts[0])
            }
            TraitOwner::Activation(_) => None,
        }
    }
    pub fn declares_type(&self, name: &QualifiedName) -> bool {
        let providers = self
            .class_names
            .iter()
            .enumerate()
            .filter(|(_, class)| class.as_ref().is_some_and(|class| class.same_binding(name)))
            .map(|(n, _)| n)
            .collect::<Vec<_>>();
        providers.len() == 1
            && self
                .script_for_owner(TraitOwner::Instance(providers[0]))
                .is_some()
    }
    pub fn new(abc: &Abc, abc_identity: String) -> Self {
        let trait_count = abc
            .instances
            .iter()
            .map(|i| i.traits.len())
            .chain(abc.classes.iter().map(|c| c.traits.len()))
            .chain(abc.scripts.iter().map(|s| s.traits.len()))
            .chain(abc.bodies.iter().map(|b| b.traits.len()))
            .fold(0usize, usize::saturating_add);
        let name_text = abc
            .instances
            .iter()
            .map(|i| i.name)
            .chain(
                abc.instances
                    .iter()
                    .flat_map(|i| i.traits.iter().map(|t| t.name)),
            )
            .chain(
                abc.classes
                    .iter()
                    .flat_map(|c| c.traits.iter().map(|t| t.name)),
            )
            .chain(
                abc.scripts
                    .iter()
                    .flat_map(|s| s.traits.iter().map(|t| t.name)),
            )
            .chain(
                abc.bodies
                    .iter()
                    .flat_map(|b| b.traits.iter().map(|t| t.name)),
            )
            .map(|n| qname_cost(abc, n).saturating_add(abc_identity.len()))
            .fold(0usize, usize::saturating_add);
        if trait_count > 50_000
            || abc.instances.len() > 10_000
            || name_text > 8 * 1024 * 1024
            || abc_identity.len() > 4096
        {
            return Self {
                abc_identity,
                context_fingerprint: String::new(),
                traits: vec![],
                class_names: vec![],
                superclasses: vec![],
                complete: false,
                indexed_text: 0,
            };
        }
        let class_names = abc
            .instances
            .iter()
            .map(|i| qname(abc, &abc_identity, i.name))
            .collect::<Vec<_>>();
        let superclasses = abc
            .instances
            .iter()
            .map(|i| {
                let name = qname(abc, &abc_identity, i.super_name)?;
                class_names
                    .iter()
                    .position(|n| n.as_ref().is_some_and(|n| n.same_binding(&name)))
            })
            .collect();
        let mut index = Self {
            context_fingerprint: abc_fingerprint(abc),
            abc_identity,
            traits: vec![],
            class_names,
            superclasses,
            complete: true,
            indexed_text: name_text,
        };
        for (n, instance) in abc.instances.iter().enumerate() {
            index.add(abc, TraitOwner::Instance(n), &instance.traits);
        }
        for (n, class) in abc.classes.iter().enumerate() {
            index.add(abc, TraitOwner::Class(n), &class.traits);
        }
        for (n, script) in abc.scripts.iter().enumerate() {
            index.add(abc, TraitOwner::Script(n), &script.traits);
        }
        for (n, body) in abc.bodies.iter().enumerate() {
            index.add(abc, TraitOwner::Activation(n), &body.traits);
        }
        index
    }
    fn add(&mut self, abc: &Abc, owner: TraitOwner, traits: &[TraitInfo]) {
        if !self.complete {
            return;
        }
        for (trait_index, t) in traits.iter().enumerate() {
            if let Some(name) = qname(abc, &self.abc_identity, t.name) {
                let type_ref = type_ref(abc, &self.abc_identity, t.type_name, 0);
                self.indexed_text = self
                    .indexed_text
                    .saturating_add(type_ref.as_ref().map(type_text_cost).unwrap_or(0));
                if self.indexed_text > 8 * 1024 * 1024 {
                    self.complete = false;
                    return;
                }
                self.traits.push(TraitBinding {
                    owner,
                    trait_index,
                    name_index: t.name,
                    name,
                    kind: t.kind,
                    slot_id: t.slot_id,
                    type_ref,
                    method_index: t.method,
                    class_index: t.class,
                });
            }
        }
    }
    /// Static candidates only. Attribute and runtime names remain unresolved.
    pub fn candidates(&self, abc: &Abc, index: u32) -> Vec<QualifiedName> {
        let Some(m) = abc.multinames.get(index as usize) else {
            return vec![];
        };
        if m.kind == 0x07 {
            return qname(abc, &self.abc_identity, index).into_iter().collect();
        }
        if m.kind != 0x09 {
            return vec![];
        }
        let Some(name) = abc.string(m.name).filter(|s| !s.is_empty()) else {
            return vec![];
        };
        let Some(set) = abc.ns_sets.get(m.ns_set as usize) else {
            return vec![];
        };
        let text = set
            .iter()
            .map(|n| {
                abc.namespaces
                    .get(*n as usize)
                    .and_then(|n| abc.string(n.name))
                    .map(str::len)
                    .unwrap_or(0)
                    .saturating_add(name.len())
                    .saturating_add(self.abc_identity.len())
            })
            .fold(0usize, usize::saturating_add);
        if set.len() > 256 || text > 2 * 1024 * 1024 {
            return vec![];
        }
        abc.ns_sets
            .get(m.ns_set as usize)
            .into_iter()
            .flatten()
            .filter_map(|n| {
                Some(QualifiedName {
                    namespace: namespace(abc, &self.abc_identity, *n)?,
                    name: name.into(),
                })
            })
            .collect()
    }
    pub fn resolve<'a>(
        &'a self,
        abc: &Abc,
        index: u32,
        owner: TraitOwner,
    ) -> Option<&'a TraitBinding> {
        let names = self.candidates(abc, index);
        let mut current = Some(owner);
        let mut seen = vec![];
        while let Some(owner) = current {
            if seen.contains(&owner) {
                return None;
            }
            seen.push(owner);
            let matches = self
                .traits
                .iter()
                .filter(|t| t.owner == owner && names.iter().any(|n| n.same_binding(&t.name)))
                .collect::<Vec<_>>();
            // Getter/setter pairs represent one property binding.
            if !matches.is_empty() {
                if matches
                    .iter()
                    .all(|t| t.name.same_binding(&matches[0].name))
                {
                    return Some(matches[0]);
                }
                return None;
            }
            current = match owner {
                TraitOwner::Instance(n) => self
                    .superclasses
                    .get(n)
                    .copied()
                    .flatten()
                    .map(TraitOwner::Instance),
                _ => None,
            };
        }
        None
    }
    pub fn resolve_script<'a>(&'a self, abc: &Abc, index: u32) -> Option<&'a TraitBinding> {
        let names = self.candidates(abc, index);
        let matches = self
            .traits
            .iter()
            .filter(|t| {
                matches!(t.owner, TraitOwner::Script(_))
                    && names.iter().any(|n| n.same_binding(&t.name))
            })
            .collect::<Vec<_>>();
        let first = *matches.first()?;
        matches
            .iter()
            .all(|t| t.owner == first.owner && t.name.same_binding(&first.name))
            .then_some(first)
    }
    pub fn type_ref(&self, abc: &Abc, index: u32) -> Option<TypeRef> {
        type_ref(abc, &self.abc_identity, index, 0)
    }
}

impl MethodContext {
    pub fn from_unit(unit: &ScriptUnit) -> Option<Self> {
        let abc = unit.context.as_ref()?;
        let body_index = unit.body_index?;
        let body = abc.bodies.get(body_index)?;
        let method_index = unit.method_index?;
        if method_index != body.method as usize || body.code != unit.code {
            return None;
        }
        let method = abc.methods.get(method_index)?;
        if method.param_types.len() > 1024 {
            return None;
        }
        // The container id distinguishes separately loaded ABCs with identical
        // bytes. Hashing parsed content also guards caller supplied contexts.
        let container = unit.id.split("/method:").next().unwrap_or(&unit.id);
        let symbols = cached_symbols(abc, container);
        if !symbols.complete {
            return None;
        }
        let abc_identity = symbols.abc_identity.clone();
        let mut owners = symbols
            .traits
            .iter()
            .filter(|t| t.method_index == Some(method_index as u32))
            .map(|t| (t.owner, Some(t.name_index)))
            .collect::<Vec<_>>();
        for (n, i) in abc.instances.iter().enumerate() {
            if i.init as usize == method_index {
                owners.push((TraitOwner::Instance(n), None));
            }
        }
        for (n, c) in abc.classes.iter().enumerate() {
            if c.init as usize == method_index {
                owners.push((TraitOwner::Class(n), None));
            }
        }
        for (n, s) in abc.scripts.iter().enumerate() {
            if s.init as usize == method_index {
                owners.push((TraitOwner::Script(n), None));
            }
        }
        let (owner, trait_name_index) = if owners.len() == 1 {
            (Some(owners[0].0), owners[0].1)
        } else {
            (None, None)
        };
        let mut parameter_types = vec![];
        let mut signature_text = 0usize;
        for n in &method.param_types {
            let ty = type_ref(abc, &abc_identity, *n, 0);
            signature_text =
                signature_text.saturating_add(ty.as_ref().map(type_text_cost).unwrap_or(0));
            if signature_text > 8 * 1024 * 1024 {
                return None;
            }
            parameter_types.push(ty);
        }
        let return_type = type_ref(abc, &abc_identity, method.return_type, 0);
        if parameter_types
            .iter()
            .flatten()
            .map(type_text_cost)
            .chain(return_type.iter().map(type_text_cost))
            .fold(0usize, usize::saturating_add)
            > 8 * 1024 * 1024
        {
            return None;
        }
        Some(Self {
            abc_identity: abc_identity.clone(),
            abc_sha256: abc.raw_sha256.clone(),
            method_index,
            body_index,
            owner,
            script_owner: owner.and_then(|owner| symbols.script_for_owner(owner)),
            trait_name_index,
            parameter_types,
            return_type,
            optional_defaults: method.options.clone(),
            symbols,
        })
    }
    pub fn matches_unit(&self, unit: &ScriptUnit) -> bool {
        Self::from_unit(unit).is_some_and(|other| other == *self)
    }
}

pub fn abc_fingerprint(abc: &Abc) -> String {
    // Only used internally, never an on-disk or cross-engine interchange hash.
    struct HashWriter(Sha256);
    impl std::fmt::Write for HashWriter {
        fn write_str(&mut self, text: &str) -> std::fmt::Result {
            self.0.update(text.as_bytes());
            Ok(())
        }
    }
    use std::fmt::Write;
    let mut writer = HashWriter(Sha256::new());
    let _ = write!(&mut writer, "{abc:?}");
    format!("{:x}", writer.0.finalize())
}
type SymbolCacheEntry = (Weak<Abc>, String, Arc<AbcSymbolIndex>);
fn cached_symbols(abc: &Arc<Abc>, container: &str) -> Arc<AbcSymbolIndex> {
    static CACHE: OnceLock<Mutex<Vec<SymbolCacheEntry>>> = OnceLock::new();
    let mut cache = CACHE
        .get_or_init(|| Mutex::new(vec![]))
        .lock()
        .unwrap_or_else(|p| p.into_inner());
    cache.retain(|(abc, _, _)| abc.strong_count() > 0);
    if let Some((_, _, symbols)) = cache
        .iter()
        .find(|(weak, id, _)| id == container && weak.as_ptr() == Arc::as_ptr(abc))
    {
        return symbols.clone();
    }
    let identity = format!("{container}:{}", abc.raw_sha256);
    let index = Arc::new(AbcSymbolIndex::new(abc, identity));
    if cache.len() >= 64 {
        cache.remove(0);
    }
    cache.push((Arc::downgrade(abc), container.into(), index.clone()));
    index
}
fn namespace(abc: &Abc, identity: &str, index: u32) -> Option<NamespaceId> {
    let n = abc.namespaces.get(index as usize)?;
    if index == 0 {
        return None;
    }
    Some(NamespaceId {
        abc_identity: identity.into(),
        pool_index: index,
        kind: n.kind,
        uri: abc.string(n.name)?.into(),
    })
}
fn qname(abc: &Abc, identity: &str, index: u32) -> Option<QualifiedName> {
    let m = abc.multinames.get(index as usize)?;
    if m.kind != 0x07 {
        return None;
    }
    Some(QualifiedName {
        namespace: namespace(abc, identity, m.namespace)?,
        name: abc.string(m.name).filter(|s| !s.is_empty())?.into(),
    })
}
fn qname_cost(abc: &Abc, index: u32) -> usize {
    let Some(m) = abc.multinames.get(index as usize) else {
        return 0;
    };
    abc.string(m.name)
        .map(str::len)
        .unwrap_or(0)
        .saturating_add(
            abc.namespaces
                .get(m.namespace as usize)
                .and_then(|n| abc.string(n.name))
                .map(str::len)
                .unwrap_or(0),
        )
}
fn type_text_cost(ty: &TypeRef) -> usize {
    match ty {
        TypeRef::Any => 0,
        TypeRef::Named(n) => n
            .name
            .len()
            .saturating_add(n.namespace.uri.len())
            .saturating_add(n.namespace.abc_identity.len()),
        TypeRef::Parameterized { base, parameters } => parameters
            .iter()
            .map(type_text_cost)
            .fold(type_text_cost(base), usize::saturating_add),
    }
}
fn type_ref(abc: &Abc, identity: &str, index: u32, depth: usize) -> Option<TypeRef> {
    let mut nodes = 256;
    let mut text = 2 * 1024 * 1024;
    type_ref_inner(abc, identity, index, depth, &mut nodes, &mut text)
}
fn type_ref_inner(
    abc: &Abc,
    identity: &str,
    index: u32,
    depth: usize,
    nodes: &mut usize,
    text: &mut usize,
) -> Option<TypeRef> {
    if depth >= 64 || *nodes == 0 {
        return None;
    }
    *nodes -= 1;
    if index == 0 {
        return Some(TypeRef::Any);
    }
    let m = abc.multinames.get(index as usize)?;
    if m.kind == 0x1d {
        return Some(TypeRef::Parameterized {
            base: Box::new(type_ref_inner(
                abc,
                identity,
                m.name,
                depth + 1,
                nodes,
                text,
            )?),
            parameters: m
                .parameters
                .iter()
                .map(|n| type_ref_inner(abc, identity, *n, depth + 1, nodes, text))
                .collect::<Option<Vec<_>>>()?,
        });
    }
    if m.kind == 0x09 {
        let set = abc.ns_sets.get(m.ns_set as usize)?;
        if set.len() > 256 {
            return None;
        }
        let ns = abc.namespaces.get(*set.first()? as usize)?;
        if abc
            .string(ns.name)?
            .len()
            .saturating_add(identity.len())
            .saturating_add(abc.string(m.name)?.len())
            > *text
        {
            return None;
        }
        let first = namespace(abc, identity, *set.first()?)?;
        if !set
            .iter()
            .all(|n| namespace(abc, identity, *n).is_some_and(|n| first.same_binding(&n)))
        {
            return None;
        }
        let cost = first
            .uri
            .len()
            .saturating_add(identity.len())
            .saturating_add(abc.string(m.name)?.len());
        if cost > *text {
            return None;
        }
        *text -= cost;
        return Some(TypeRef::Named(QualifiedName {
            namespace: first,
            name: abc.string(m.name).filter(|s| !s.is_empty())?.into(),
        }));
    }
    let cost = qname_cost(abc, index).saturating_add(identity.len());
    if cost > *text {
        return None;
    }
    *text -= cost;
    qname(abc, identity, index).map(TypeRef::Named)
}
