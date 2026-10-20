//! Immutable hierarchy facts parsed from a SHA-verified SWC. GPL-3.0-or-later.
//! Only type declarations and names are retained; SDK code is never executed.
use anyhow::{Result, ensure};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use swf_avm::symbols::{NamespaceId, QualifiedName, TypeRef};
use swf_avm::{Abc, inventory};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub(crate) struct TypeKey {
    pub kind: u8,
    pub uri: String,
    pub name: String,
}
impl TypeKey {
    pub fn from_type(ty: &TypeRef) -> Option<Self> {
        let TypeRef::Named(name) = ty else {
            return None;
        };
        if !matches!(name.namespace.kind, 0x08 | 0x16 | 0x17) {
            return None;
        }
        Some(Self {
            kind: if matches!(name.namespace.kind, 0x08 | 0x16) {
                0x16
            } else {
                0x17
            },
            uri: name.namespace.uri.clone(),
            name: name.name.clone(),
        })
    }
    fn from_abc(abc: &Abc, index: u32) -> Option<Self> {
        let m = abc.multinames.get(index as usize)?;
        if m.kind != 7 {
            return None;
        }
        let ns = abc.namespaces.get(m.namespace as usize)?;
        if !matches!(ns.kind, 0x08 | 0x16 | 0x17) {
            return None;
        }
        Some(Self {
            kind: if matches!(ns.kind, 0x08 | 0x16) {
                0x16
            } else {
                0x17
            },
            uri: abc.string(ns.name)?.into(),
            name: abc.string(m.name)?.into(),
        })
    }
    pub fn as_type(&self, provenance: &HierarchyProvenance) -> TypeRef {
        TypeRef::Named(QualifiedName {
            namespace: NamespaceId {
                abc_identity: format!("swc:{}", provenance.source_sha256),
                pool_index: 0,
                kind: self.kind,
                uri: self.uri.clone(),
            },
            name: self.name.clone(),
        })
    }
}
#[derive(Debug, Clone, Serialize)]
pub struct HierarchyProvenance {
    pub source_path: PathBuf,
    pub source_sha256: String,
    pub library_sha256: String,
    pub class_records: usize,
    pub ambiguous_records: usize,
}
#[derive(Debug, Clone)]
pub(crate) struct HierarchyRecord {
    pub parent: Option<TypeKey>,
    pub interface: bool,
    pub member_names: BTreeSet<String>,
}
#[derive(Debug, Clone)]
pub struct VerifiedHierarchy {
    pub provenance: HierarchyProvenance,
    records: BTreeMap<TypeKey, Option<HierarchyRecord>>,
}
impl VerifiedHierarchy {
    pub(crate) fn ancestor_has_member(
        &self,
        abc: &Abc,
        symbols: &swf_avm::symbols::AbcSymbolIndex,
        class_index: usize,
        name: &str,
    ) -> bool {
        let mut ty = symbols.type_ref(abc, abc.instances[class_index].super_name);
        let mut seen = BTreeSet::new();
        for _ in 0..64 {
            let Some(current) = ty else {
                return false;
            };
            let Some(key) = TypeKey::from_type(&current) else {
                return true;
            };
            if !seen.insert(key.clone()) {
                return true;
            }
            let own = symbols
                .class_names
                .iter()
                .enumerate()
                .filter(|(_, n)| {
                    n.as_ref().is_some_and(|n| {
                        TypeKey::from_type(&TypeRef::Named(n.clone())).as_ref() == Some(&key)
                    })
                })
                .map(|(n, _)| n)
                .collect::<Vec<_>>();
            if own.len() > 1 {
                return true;
            }
            if let Some(n) = own.first() {
                ty = symbols.type_ref(abc, abc.instances[*n].super_name);
                continue;
            }
            let Some(record) = self.lookup(&key) else {
                return true;
            };
            if record.member_names.contains(name) {
                return true;
            }
            ty = record.parent.as_ref().map(|p| p.as_type(&self.provenance));
        }
        true
    }
    pub fn load_swc(path: &Path, sha256: &str) -> Result<Self> {
        let library = swf_core::read_verified_swc_library(path, sha256)?;
        let units = inventory(&library.movie);
        ensure!(
            units.iter().all(|u| u.diagnostics.is_empty()),
            "Hierarchy SDK ABC parse has diagnostics"
        );
        let mut seen = BTreeSet::new();
        let mut records = BTreeMap::new();
        let mut text = 0usize;
        for unit in &units {
            let Some(abc) = &unit.context else {
                continue;
            };
            if !seen.insert(unit.tag_path.clone()) {
                continue;
            }
            for (n, instance) in abc.instances.iter().enumerate() {
                let Some(key) = TypeKey::from_abc(abc, instance.name) else {
                    continue;
                };
                let parent = if instance.super_name == 0 {
                    None
                } else {
                    Some(TypeKey::from_abc(abc, instance.super_name).ok_or_else(|| {
                        anyhow::anyhow!("SDK superclass QName is not representable")
                    })?)
                };
                let member_names = instance
                    .traits
                    .iter()
                    .chain(abc.classes.get(n).into_iter().flat_map(|c| c.traits.iter()))
                    .filter_map(|t| {
                        abc.multinames
                            .get(t.name as usize)
                            .and_then(|m| abc.string(m.name))
                            .filter(|s| !s.is_empty())
                            .map(str::to_owned)
                    })
                    .collect::<BTreeSet<_>>();
                text = text
                    .saturating_add(key.uri.len())
                    .saturating_add(key.name.len())
                    .saturating_add(member_names.iter().map(String::len).sum::<usize>());
                ensure!(
                    records.len() < 50_000 && text <= 8 * 1024 * 1024,
                    "Hierarchy SDK exceeds declaration/name budget"
                );
                let record = HierarchyRecord {
                    parent,
                    interface: instance.flags & 4 != 0,
                    member_names,
                };
                if records.contains_key(&key) {
                    records.insert(key, None);
                } else {
                    records.insert(key, Some(record));
                }
            }
        }
        ensure!(
            !records.is_empty(),
            "SWC contains no representable class declarations"
        );
        let provenance = HierarchyProvenance {
            source_path: library.source_path,
            source_sha256: library.source_sha256,
            library_sha256: library.library_sha256,
            class_records: records.len(),
            ambiguous_records: records.values().filter(|r| r.is_none()).count(),
        };
        Ok(Self {
            provenance,
            records,
        })
    }
    pub(crate) fn lookup(&self, key: &TypeKey) -> Option<&HierarchyRecord> {
        self.records.get(key)?.as_ref()
    }
}
