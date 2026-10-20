//! Complete bounded ABC class documents. GPL-3.0-or-later.
//! Source is returned only after class ownership, every body, and the script
//! bootstrap have been accounted for. Original P-code accompanies every result.
use crate::ir::{identifier, quoted};
use crate::{Result, fail, mnemonic};
use serde::Serialize;
use std::collections::BTreeSet;
use std::sync::Arc;
use swf_avm::symbols::{AbcSymbolIndex, QualifiedName, TraitOwner, TypeRef};
use swf_avm::{Abc, Operand, ScriptUnit, disassemble};

#[derive(Debug, Clone, Serialize)]
pub struct SourceDocument {
    pub path: String,
    pub source: String,
    pub class_index: usize,
    pub script_index: usize,
}
#[derive(Debug, Clone, Serialize)]
pub struct OwnedMethodFragment {
    pub unit_id: String,
    pub method_index: usize,
    pub body_index: usize,
    pub owner: TraitOwner,
    pub owner_name: String,
    pub trait_name_index: Option<u32>,
    pub requires_original_owner: bool,
    pub source: String,
}
#[derive(Debug, Clone, Serialize)]
pub struct DocumentResult {
    pub state: String,
    pub documents: Vec<SourceDocument>,
    pub pcode: Vec<crate::DecompileResult>,
    pub diagnostics: Vec<swf_core::Diagnostic>,
    pub initialization_order: Vec<usize>,
    pub hierarchy_source: Option<crate::HierarchyProvenance>,
    pub method_fragments: Vec<OwnedMethodFragment>,
    pub source_tier: String,
}

pub fn decompile_document(unit: &ScriptUnit) -> DocumentResult {
    decompile_document_inner(unit, None)
}
pub fn decompile_document_with_hierarchy(
    unit: &ScriptUnit,
    hierarchy: &crate::VerifiedHierarchy,
) -> DocumentResult {
    decompile_document_inner(unit, Some(hierarchy))
}
fn decompile_document_inner(
    unit: &ScriptUnit,
    hierarchy: Option<&crate::VerifiedHierarchy>,
) -> DocumentResult {
    let mut result = DocumentResult {
        state: "pcode_only".into(),
        documents: vec![],
        pcode: vec![],
        diagnostics: vec![],
        initialization_order: vec![],
        hierarchy_source: hierarchy.map(|h| h.provenance.clone()),
        method_fragments: vec![],
        source_tier: "pcode_only".into(),
    };
    let Some(abc) = &unit.context else {
        result.diagnostics.push(swf_core::Diagnostic {
            code: "INVALID_DOCUMENT_CONTEXT".into(),
            message: "An ABC context is required".into(),
            offset: None,
        });
        return result;
    };
    if abc.bodies.len() > 10_000 {
        result.diagnostics.push(swf_core::Diagnostic {
            code: "DECOMPILE_LIMIT".into(),
            message: "ABC document exceeds 10,000 method bodies; raw input is retained".into(),
            offset: None,
        });
        return result;
    }
    let mut pcode_bytes = 0usize;
    for (body_index, body) in abc.bodies.iter().enumerate() {
        let u = method_unit(
            unit,
            abc.clone(),
            body.method as usize,
            body_index,
            &abc.method_name(body.method as usize),
        );
        let listing = disassemble(&u);
        pcode_bytes = pcode_bytes.saturating_add(listing.pcode.len());
        if pcode_bytes > 64 * 1024 * 1024 {
            result.diagnostics.push(swf_core::Diagnostic{code:"DECOMPILE_LIMIT".into(),message:"Document P-code exceeds 64 MiB; listed units remain exact and raw input is retained".into(),offset:None});
            return result;
        }
        result.pcode.push(crate::DecompileResult {
            unit_id: u.id,
            state: "pcode_only".into(),
            source: None,
            pcode: listing.pcode,
            diagnostics: listing.diagnostics,
        });
    }
    let reconstructed = (|| -> Result<(Vec<SourceDocument>, Vec<usize>)> {
        if !unit.diagnostics.is_empty() {
            return Err(fail(
                "INVALID_DOCUMENT_CONTEXT",
                "Input unit has diagnostics",
                0,
            ));
        }
        if abc.instances.len() != abc.classes.len() || abc.instances.is_empty() {
            return Err(fail(
                "UNSUPPORTED_SOURCE_DOCUMENT",
                "Document needs a nonempty, paired class table",
                0,
            ));
        }
        let symbols = AbcSymbolIndex::new(abc, format!("document:{}", unit.id));
        if !symbols.complete {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "ABC symbol index exceeded its document budget",
                0,
            ));
        }
        let mut order = vec![];
        let mut owners = vec![None; abc.classes.len()];
        let mut claimed = BTreeSet::new();
        for (script_index, script) in abc.scripts.iter().enumerate() {
            let classes = bootstrap(unit, abc, script_index, &symbols, hierarchy)?;
            claimed.insert(script.init as usize);
            for class_index in classes {
                if owners[class_index].replace(script_index).is_some() {
                    return Err(fail(
                        "UNSUPPORTED_SOURCE_DOCUMENT",
                        "Class has multiple script owners",
                        0,
                    ));
                }
                order.push(class_index);
            }
        }
        if owners.iter().any(Option::is_none) {
            return Err(fail(
                "UNSUPPORTED_SOURCE_DOCUMENT",
                "Every class needs one verified script declaration",
                0,
            ));
        }
        let mut documents = vec![];
        for class_index in &order {
            let script_index = owners[*class_index].unwrap();
            documents.push(class_document(
                unit,
                abc,
                *class_index,
                script_index,
                &symbols,
                &mut claimed,
                hierarchy,
            )?);
        }
        let mut paths = BTreeSet::new();
        if documents
            .iter()
            .any(|document| !paths.insert(document.path.clone()))
        {
            return Err(fail(
                "UNSUPPORTED_SOURCE_DOCUMENT",
                "Distinct class identities would overwrite the same source path",
                0,
            ));
        }
        // Nested newfunction bodies are represented inside their owning source
        // method. Include their transitive references in the coverage audit.
        loop {
            let before = claimed.len();
            for body in &abc.bodies {
                if claimed.contains(&(body.method as usize)) {
                    let body_index = abc
                        .bodies
                        .iter()
                        .position(|b| std::ptr::eq(b, body))
                        .unwrap();
                    let u = method_unit(
                        unit,
                        abc.clone(),
                        body.method as usize,
                        body_index,
                        "coverage",
                    );
                    for i in disassemble(&u).instructions {
                        if mnemonic(&i.name) == "newfunction" {
                            claimed.insert(operand(&i, 0)?);
                        }
                    }
                }
            }
            if claimed.len() == before {
                break;
            }
        }
        if abc
            .bodies
            .iter()
            .any(|body| !claimed.contains(&(body.method as usize)))
        {
            return Err(fail(
                "UNSUPPORTED_SOURCE_DOCUMENT",
                "An unowned method body is not represented by the documents",
                0,
            ));
        }
        Ok((documents, order))
    })();
    match reconstructed {
        Ok((documents, order)) => {
            result.state = "structured".into();
            result.documents = documents;
            result.initialization_order = order;
            result.source_tier = "complete_document".into();
        }
        Err(e) => result.diagnostics.push(swf_core::Diagnostic {
            code: e.code.into(),
            message: e.message,
            offset: e.offset,
        }),
    }
    if result.documents.is_empty() {
        let mut fragment_bytes = 0usize;
        for (body_index, body) in abc.bodies.iter().enumerate() {
            let mut fragment = method_unit(
                unit,
                abc.clone(),
                body.method as usize,
                body_index,
                &abc.method_name(body.method as usize),
            );
            let Some(context) = swf_avm::symbols::MethodContext::from_unit(&fragment) else {
                continue;
            };
            let Some(owner) = context.owner else {
                continue;
            };
            let class_index = match owner {
                TraitOwner::Instance(n) | TraitOwner::Class(n) => n,
                _ => continue,
            };
            if class_index >= abc.instances.len() || class_index >= abc.classes.len() {
                continue;
            }
            if owner == TraitOwner::Class(class_index)
                && abc.classes[class_index].init as usize == context.method_index
            {
                continue;
            }
            if owner == TraitOwner::Instance(class_index)
                && abc.instances[class_index].init as usize == context.method_index
            {
                if let Some(name) = context.symbols.class_names[class_index].as_ref() {
                    fragment.name = name.name.clone();
                }
            }
            let owned = crate::decompile_owned_with_hierarchy(&fragment, hierarchy);
            if let Some(source) = owned.source {
                fragment_bytes = fragment_bytes.saturating_add(source.len());
                if fragment_bytes > 16 * 1024 * 1024 {
                    result.method_fragments.clear();
                    break;
                }
                result.method_fragments.push(OwnedMethodFragment {
                    unit_id: fragment.id,
                    method_index: context.method_index,
                    body_index,
                    owner,
                    owner_name: abc.multiname_display(abc.instances[class_index].name),
                    trait_name_index: context.trait_name_index,
                    requires_original_owner: true,
                    source,
                });
            }
        }
        if !result.method_fragments.is_empty() {
            result.source_tier = "owned_method_fragments".into();
        }
    }
    result
}

fn method_unit(
    parent: &ScriptUnit,
    abc: Arc<Abc>,
    method: usize,
    body: usize,
    name: &str,
) -> ScriptUnit {
    let b = &abc.bodies[body];
    let m = &abc.methods[method];
    ScriptUnit {
        id: format!(
            "{}/method:{method}",
            parent.id.split("/method:").next().unwrap_or(&parent.id)
        ),
        name: name.into(),
        language: "avm2".into(),
        kind: "method".into(),
        code: b.code.clone(),
        byte_len: b.code.len(),
        byte_start: b.code_offset,
        method_index: Some(method),
        body_index: Some(body),
        parameters: m
            .param_types
            .iter()
            .enumerate()
            .map(|(n, _)| {
                m.param_names
                    .get(n)
                    .and_then(|n| abc.string(*n))
                    .filter(|s| identifier(s))
                    .map(str::to_owned)
                    .unwrap_or_else(|| format!("arg{}", n + 1))
            })
            .collect(),
        context: Some(abc.clone()),
        ..Default::default()
    }
}
fn body_index(abc: &Abc, method: usize) -> Result<usize> {
    let bodies = abc
        .bodies
        .iter()
        .enumerate()
        .filter(|(_, b)| b.method as usize == method)
        .map(|(n, _)| n)
        .collect::<Vec<_>>();
    if bodies.len() != 1 {
        return Err(fail(
            "UNSUPPORTED_SOURCE_DOCUMENT",
            "Method needs exactly one body",
            0,
        ));
    }
    Ok(bodies[0])
}
fn operand(i: &swf_avm::Instruction, n: usize) -> Result<usize> {
    match i.operands.get(n) {
        Some(Operand::Unsigned(v)) | Some(Operand::Pool { index: v, .. }) => Ok(*v as usize),
        _ => Err(fail(
            "INVALID_OPERAND",
            "Missing document operand",
            i.offset,
        )),
    }
}

#[derive(Clone, PartialEq)]
enum BootstrapValue {
    Global,
    Type(TypeRef),
    Class(usize),
}
fn same_type(a: &TypeRef, b: &TypeRef) -> bool {
    match (a, b) {
        (TypeRef::Any, TypeRef::Any) => true,
        (TypeRef::Named(a), TypeRef::Named(b)) => a.same_binding(b),
        (
            TypeRef::Parameterized {
                base: a,
                parameters: ap,
            },
            TypeRef::Parameterized {
                base: b,
                parameters: bp,
            },
        ) => {
            same_type(a, b)
                && ap.len() == bp.len()
                && ap.iter().zip(bp).all(|(a, b)| same_type(a, b))
        }
        _ => false,
    }
}
fn captured_hierarchy(
    abc: &Abc,
    symbols: &AbcSymbolIndex,
    class_index: usize,
    external: Option<&crate::VerifiedHierarchy>,
) -> Result<Vec<TypeRef>> {
    let mut ty = symbols
        .type_ref(abc, abc.instances[class_index].super_name)
        .ok_or_else(|| {
            fail(
                "UNSUPPORTED_SCRIPT_DOCUMENT",
                "Superclass identity is unresolved",
                0,
            )
        })?;
    let mut chain = vec![];
    let mut seen = vec![class_index];
    loop {
        let TypeRef::Named(name) = &ty else {
            return Err(fail(
                "UNSUPPORTED_SCRIPT_DOCUMENT",
                "Class hierarchy needs statically named types",
                0,
            ));
        };
        chain.push(ty.clone());
        if name.namespace.uri.is_empty()
            && matches!(name.namespace.kind, 0x08 | 0x16)
            && name.name == "Object"
            && !symbols
                .class_names
                .iter()
                .flatten()
                .any(|class| class.same_binding(name))
        {
            break;
        }
        let providers = symbols
            .class_names
            .iter()
            .enumerate()
            .filter(|(_, class)| class.as_ref().is_some_and(|class| class.same_binding(name)))
            .map(|(n, _)| n)
            .collect::<Vec<_>>();
        if providers.len() > 1 {
            return Err(fail(
                "UNSUPPORTED_SCRIPT_DOCUMENT",
                "Superclass has ambiguous declaration providers",
                0,
            ));
        }
        let Some(base) = providers.first().copied() else {
            let key = crate::hierarchy::TypeKey::from_type(&ty).ok_or_else(|| {
                fail(
                    "UNSUPPORTED_SCRIPT_DOCUMENT",
                    "External superclass QName is not representable",
                    0,
                )
            })?;
            let hierarchy = external.ok_or_else(|| {
                fail(
                    "UNSUPPORTED_SCRIPT_DOCUMENT",
                    "External ancestor hierarchy has no verified metadata contract",
                    0,
                )
            })?;
            let record = hierarchy.lookup(&key).ok_or_else(|| {
                fail(
                    "UNSUPPORTED_SCRIPT_DOCUMENT",
                    "External ancestor declaration is missing or ambiguous",
                    0,
                )
            })?;
            if record.interface {
                return Err(fail(
                    "UNSUPPORTED_SCRIPT_DOCUMENT",
                    "External interface cannot be a superclass",
                    0,
                ));
            }
            let parent = record.parent.as_ref().ok_or_else(|| {
                fail(
                    "UNSUPPORTED_SCRIPT_DOCUMENT",
                    "External hierarchy has no proven Object root",
                    0,
                )
            })?;
            ty = parent.as_type(&hierarchy.provenance);
            if chain.len() >= 64 {
                return Err(fail(
                    "UNSUPPORTED_SCRIPT_DOCUMENT",
                    "External hierarchy is cyclic or exceeds 64 ancestors",
                    0,
                ));
            }
            continue;
        };
        if abc.instances[base].flags & 4 != 0 {
            return Err(fail(
                "UNSUPPORTED_SCRIPT_DOCUMENT",
                "Interface cannot be captured as superclass",
                0,
            ));
        }
        if seen.contains(&base) || seen.len() >= 64 {
            return Err(fail(
                "UNSUPPORTED_SCRIPT_DOCUMENT",
                "Class hierarchy is cyclic or exceeds 64 ancestors",
                0,
            ));
        }
        seen.push(base);
        ty = symbols
            .type_ref(abc, abc.instances[base].super_name)
            .ok_or_else(|| {
                fail(
                    "UNSUPPORTED_SCRIPT_DOCUMENT",
                    "Ancestor type is unresolved",
                    0,
                )
            })?;
    }
    chain.reverse();
    Ok(chain)
}
fn bootstrap(
    parent: &ScriptUnit,
    abc: &Arc<Abc>,
    script_index: usize,
    symbols: &AbcSymbolIndex,
    external: Option<&crate::VerifiedHierarchy>,
) -> Result<Vec<usize>> {
    let script = &abc.scripts[script_index];
    if script.traits.iter().any(|t| t.kind != 4) {
        return Err(fail(
            "UNSUPPORTED_SCRIPT_DOCUMENT",
            "Script functions and slots require their own source document",
            0,
        ));
    }
    let body_index = body_index(abc, script.init as usize)?;
    let u = method_unit(
        parent,
        abc.clone(),
        script.init as usize,
        body_index,
        "bootstrap",
    );
    let a = disassemble(&u);
    if a.state != "decoded"
        || !a.diagnostics.is_empty()
        || !abc.bodies[body_index].exceptions.is_empty()
        || !abc.bodies[body_index].traits.is_empty()
    {
        return Err(fail(
            "UNSUPPORTED_SCRIPT_DOCUMENT",
            format!(
                "Script bootstrap has unproven scope/handlers: decode={}, handlers={}, activation_traits={}, init_scope_depth={}",
                a.state,
                abc.bodies[body_index].exceptions.len(),
                abc.bodies[body_index].traits.len(),
                abc.bodies[body_index].init_scope_depth
            ),
            0,
        ));
    }
    let mut stack = vec![];
    // Script initializers execute with a fresh empty outer ScopeChain. Their
    // declared init_scope_depth is a verifier bound, not a captured object.
    // Only getscopeobject (local frame) is accepted below; getouterscope stays
    // unsupported, and actual class capture identities are checked in full.
    let mut scopes = vec![];
    let mut order = vec![];
    let mut ended = false;
    for i in &a.instructions {
        if ended
            && !matches!(
                mnemonic(&i.name).as_str(),
                "nop" | "debug" | "debugline" | "debugfile"
            )
        {
            return Err(fail(
                "UNSUPPORTED_SCRIPT_DOCUMENT",
                "Unreachable bootstrap bytecode",
                i.offset,
            ));
        }
        match mnemonic(&i.name).as_str() {
            "nop" | "debug" | "debugline" | "debugfile" => {}
            "getlocal0" => stack.push(BootstrapValue::Global),
            "getscopeobject" => {
                stack.push(scopes.get(operand(i, 0)?).cloned().ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_SCRIPT_DOCUMENT",
                        "Unknown bootstrap scope",
                        i.offset,
                    )
                })?)
            }
            "pushscope" => scopes.push(
                stack
                    .pop()
                    .ok_or_else(|| fail("STACK_UNDERFLOW", "Bootstrap pushscope", i.offset))?,
            ),
            "popscope" => {
                if scopes.len() <= 1 {
                    return Err(fail(
                        "UNSUPPORTED_SCRIPT_DOCUMENT",
                        "Bootstrap popped global scope",
                        i.offset,
                    ));
                }
                scopes.pop();
            }
            "getlex" => {
                let ty = symbols
                    .type_ref(abc, operand(i, 0)? as u32)
                    .ok_or_else(|| {
                        fail(
                            "UNSUPPORTED_SCRIPT_DOCUMENT",
                            "Bootstrap type identity is unresolved",
                            i.offset,
                        )
                    })?;
                if !matches!(&ty,TypeRef::Named(n) if matches!(n.namespace.kind,0x08|0x16)) {
                    return Err(fail(
                        "UNSUPPORTED_SCRIPT_DOCUMENT",
                        "Bootstrap class binding has unsupported namespace",
                        i.offset,
                    ));
                }
                stack.push(BootstrapValue::Type(ty));
            }
            "newclass" => {
                let n = operand(i, 0)?;
                let instance = abc
                    .instances
                    .get(n)
                    .ok_or_else(|| fail("INVALID_OPERAND", "Bootstrap class index", i.offset))?;
                let superclass = symbols.type_ref(abc, instance.super_name).ok_or_else(|| {
                    fail("UNSUPPORTED_SCRIPT_DOCUMENT", "Unknown base type", i.offset)
                })?;
                let hierarchy = captured_hierarchy(abc, symbols, n, external)?;
                if !stack.pop().is_some_and(|value|matches!(value,BootstrapValue::Type(ty) if same_type(&ty,&superclass)))
                    || scopes.first()!=Some(&BootstrapValue::Global)
                    || scopes.len()!=hierarchy.len()+1
                    || !scopes.iter().skip(1).zip(&hierarchy).all(|(scope,ty)|matches!(scope,BootstrapValue::Type(actual) if same_type(actual,ty)))
                {
                    return Err(fail(
                        "UNSUPPORTED_SCRIPT_DOCUMENT",
                        "Class base/captured scope differs from canonical bootstrap",
                        i.offset,
                    ));
                }
                stack.push(BootstrapValue::Class(n));
            }
            "initproperty" => {
                let Some(BootstrapValue::Class(n)) = stack.pop() else {
                    return Err(fail(
                        "UNSUPPORTED_SCRIPT_DOCUMENT",
                        "Bootstrap initializer is not a class",
                        i.offset,
                    ));
                };
                if stack.pop() != Some(BootstrapValue::Global) {
                    return Err(fail(
                        "UNSUPPORTED_SCRIPT_DOCUMENT",
                        "Class bootstrap receiver is not global",
                        i.offset,
                    ));
                }
                let binding = symbols
                    .resolve(abc, operand(i, 0)? as u32, TraitOwner::Script(script_index))
                    .ok_or_else(|| {
                        fail(
                            "UNSUPPORTED_SCRIPT_DOCUMENT",
                            "Class declaration has no exact script trait",
                            i.offset,
                        )
                    })?;
                if binding.class_index != Some(n as u32)
                    || symbols.class_names[n]
                        .as_ref()
                        .is_none_or(|name| !name.same_binding(&binding.name))
                    || order.contains(&n)
                {
                    return Err(fail(
                        "UNSUPPORTED_SCRIPT_DOCUMENT",
                        "Class trait and newclass identities disagree",
                        i.offset,
                    ));
                }
                order.push(n);
            }
            "returnvoid" => {
                if !stack.is_empty() || scopes != [BootstrapValue::Global] {
                    return Err(fail(
                        "UNSUPPORTED_SCRIPT_DOCUMENT",
                        "Bootstrap exits with unresolved state",
                        i.offset,
                    ));
                }
                ended = true;
            }
            _ => {
                return Err(fail(
                    "UNSUPPORTED_SCRIPT_DOCUMENT",
                    format!(
                        "Bootstrap {} has an effect not represented by class declarations",
                        i.name
                    ),
                    i.offset,
                ));
            }
        }
        if stack.len() > abc.bodies[body_index].max_stack as usize
            || scopes.len() > abc.bodies[body_index].max_scope_depth as usize
        {
            return Err(fail(
                "STACK_LIMIT",
                "Bootstrap exceeds declared bounds",
                i.offset,
            ));
        }
    }
    if !ended || order.len() != script.traits.len() {
        return Err(fail(
            "UNSUPPORTED_SCRIPT_DOCUMENT",
            "Class declarations do not cover the complete script",
            0,
        ));
    }
    Ok(order)
}

fn source_type(ty: &TypeRef) -> Result<String> {
    match ty {
        TypeRef::Any => Ok("*".into()),
        TypeRef::Named(n)
            if matches!(n.namespace.kind, 0x08 | 0x16)
                && (identifier(&n.name) || n.name == "void")
                && (n.namespace.uri.is_empty() || n.namespace.uri.split('.').all(identifier)) =>
        {
            Ok(if n.namespace.uri.is_empty() {
                n.name.clone()
            } else {
                format!("{}.{}", n.namespace.uri, n.name)
            })
        }
        TypeRef::Parameterized { base, parameters } => Ok(format!(
            "{}.<{}>",
            source_type(base)?,
            parameters
                .iter()
                .map(source_type)
                .collect::<Result<Vec<_>>>()?
                .join(", ")
        )),
        _ => Err(fail(
            "UNSUPPORTED_TYPE",
            "Document type needs an exact named namespace binding",
            0,
        )),
    }
}
fn visibility(
    name: &QualifiedName,
    package: &str,
    owner: TraitOwner,
    abc: &Abc,
) -> Result<&'static str> {
    match name.namespace.kind {
        0x08 | 0x16 if name.namespace.uri.is_empty() => Ok("public"),
        0x05 => Ok("private"),
        0x17 if name.namespace.uri == package => Ok("internal"),
        0x18 if matches!(owner,TraitOwner::Instance(n) if abc.instances[n].protected_namespace==Some(name.namespace.pool_index)) => {
            Ok("protected")
        }
        0x1a if matches!(owner, TraitOwner::Class(_)) => Ok("protected"),
        _ => Err(fail(
            "UNSUPPORTED_NAMESPACE",
            "Member namespace has no exact AS3 declaration form",
            0,
        )),
    }
}
fn constant(abc: &Abc, value: u32, kind: u8) -> Result<String> {
    Ok(match kind {
        0x01 => quoted(
            abc.string(value)
                .ok_or_else(|| fail("INVALID_OPERAND", "Field string default", 0))?,
        ),
        0x03 => abc.integers[value as usize].to_string(),
        0x04 => abc.uints[value as usize].to_string(),
        0x06 => {
            let n = f64::from_le_bytes(abc.doubles[value as usize]);
            if !n.is_finite() {
                return Err(fail(
                    "UNSUPPORTED_SOURCE_DOCUMENT",
                    "Nonfinite trait default is unproven",
                    0,
                ));
            }
            if n == 0.0 && n.is_sign_negative() {
                "-0.0".into()
            } else {
                n.to_string()
            }
        }
        0x0a => "false".into(),
        0x0b => "true".into(),
        0x0c => "null".into(),
        0 => "undefined".into(),
        _ => {
            return Err(fail(
                "UNSUPPORTED_SOURCE_DOCUMENT",
                "Trait default kind needs exact declaration support",
                0,
            ));
        }
    })
}
fn class_document(
    parent: &ScriptUnit,
    abc: &Arc<Abc>,
    class_index: usize,
    script_index: usize,
    symbols: &AbcSymbolIndex,
    claimed: &mut BTreeSet<usize>,
    external: Option<&crate::VerifiedHierarchy>,
) -> Result<SourceDocument> {
    let instance = &abc.instances[class_index];
    let class = &abc.classes[class_index];
    let name = symbols.class_names[class_index].as_ref().ok_or_else(|| {
        fail(
            "UNSUPPORTED_SOURCE_DOCUMENT",
            "Class name identity is unresolved",
            0,
        )
    })?;
    if name.namespace.kind != 0x16
        || !identifier(&name.name)
        || (!name.namespace.uri.is_empty() && !name.namespace.uri.split('.').all(identifier))
        || instance.flags & 4 != 0
    {
        return Err(fail(
            "UNSUPPORTED_SOURCE_DOCUMENT",
            "Class package/interface form is unsupported",
            0,
        ));
    }
    let package = &name.namespace.uri;
    let mut type_owners = vec![];
    let mut current = Some(class_index);
    while let Some(n) = current {
        if type_owners.contains(&n) {
            return Err(fail(
                "UNSUPPORTED_TYPE",
                "Class type scope hierarchy is cyclic",
                0,
            ));
        }
        type_owners.push(n);
        current = symbols.superclasses.get(n).copied().flatten();
    }
    let check_type = |ty: &TypeRef| -> Result<()> {
        fn check(
            ty: &TypeRef,
            package: &str,
            owners: &[usize],
            symbols: &AbcSymbolIndex,
            abc: &Abc,
            class_index: usize,
            external: Option<&crate::VerifiedHierarchy>,
        ) -> Result<()> {
            match ty {
                TypeRef::Named(n) if n.namespace.uri.is_empty() => {
                    let members=symbols.traits.iter().any(|t|matches!(t.owner,TraitOwner::Instance(i)|TraitOwner::Class(i) if owners.contains(&i))&&t.name.name==n.name);
                    let package_shadow = symbols.class_names.iter().flatten().any(|c| {
                        c.namespace.uri == package && c.name == n.name && !c.same_binding(n)
                    });
                    if members
                        || package_shadow
                        || external.is_some_and(|h| {
                            h.ancestor_has_member(abc, symbols, class_index, &n.name)
                        })
                    {
                        return Err(fail(
                            "SHADOWED_TYPE_BINDING",
                            "Class declaration shadows a source annotation type",
                            0,
                        ));
                    }
                }
                TypeRef::Parameterized { base, parameters } => {
                    check(base, package, owners, symbols, abc, class_index, external)?;
                    for p in parameters {
                        check(p, package, owners, symbols, abc, class_index, external)?;
                    }
                }
                _ => {}
            }
            Ok(())
        }
        check(
            ty,
            package,
            &type_owners,
            symbols,
            abc,
            class_index,
            external,
        )
    };
    let mut imports = BTreeSet::new();
    fn collect_imports(ty: &TypeRef, package: &str, imports: &mut BTreeSet<String>) {
        match ty {
            TypeRef::Named(n)
                if matches!(n.namespace.kind, 0x08 | 0x16)
                    && !n.namespace.uri.is_empty()
                    && n.namespace.uri != package
                    && n.namespace.uri.split('.').all(identifier) =>
            {
                imports.insert(format!("{}.{}", n.namespace.uri, n.name));
            }
            TypeRef::Parameterized { base, parameters } => {
                collect_imports(base, package, imports);
                for p in parameters {
                    collect_imports(p, package, imports);
                }
            }
            _ => {}
        }
    }
    for index in std::iter::once(instance.super_name)
        .chain(instance.interfaces.iter().copied())
        .chain(
            instance
                .traits
                .iter()
                .chain(&class.traits)
                .map(|t| t.type_name),
        )
        .chain(
            abc.methods
                .iter()
                .flat_map(|m| std::iter::once(m.return_type).chain(m.param_types.iter().copied())),
        )
    {
        if let Some(ty) = symbols.type_ref(abc, index) {
            collect_imports(&ty, package, &mut imports);
        }
    }
    let mut text = format!("package {package} {{\n");
    for import in imports {
        text.push_str(&format!("    import {import};\n"));
    }
    text.push_str(&format!(
        "    public {}{}class {}",
        if instance.flags & 2 != 0 {
            "final "
        } else {
            ""
        },
        if instance.flags & 1 == 0 {
            "dynamic "
        } else {
            ""
        },
        name.name
    ));
    let implicit_object=symbols.type_ref(abc,instance.super_name).is_some_and(|t|matches!(t,TypeRef::Named(n) if n.namespace.uri.is_empty()&&matches!(n.namespace.kind,0x08|0x16)&&n.name=="Object"));
    if instance.super_name != 0 && !implicit_object {
        text.push_str(&format!(
            " extends {}",
            source_type(
                &symbols
                    .type_ref(abc, instance.super_name)
                    .ok_or_else(|| fail("UNSUPPORTED_TYPE", "Superclass type is unresolved", 0))?
            )?
        ));
    }
    if !instance.interfaces.is_empty() {
        text.push_str(&format!(
            " implements {}",
            instance
                .interfaces
                .iter()
                .map(|n| symbols
                    .type_ref(abc, *n)
                    .ok_or_else(|| fail("UNSUPPORTED_TYPE", "Interface type unresolved", 0))
                    .and_then(|ty| source_type(&ty)))
                .collect::<Result<Vec<_>>>()?
                .join(", ")
        ));
    }
    text.push_str(" {\n");
    let private_namespaces = instance
        .traits
        .iter()
        .chain(&class.traits)
        .filter_map(|t| {
            symbols.traits.iter().find(|b| {
                matches!(b.owner,TraitOwner::Instance(n)|TraitOwner::Class(n) if n==class_index)
                    && b.name_index == t.name
            })
        })
        .filter(|t| t.name.namespace.kind == 5)
        .map(|t| t.name.namespace.pool_index)
        .collect::<BTreeSet<_>>();
    if private_namespaces.len() > 1 {
        return Err(fail(
            "UNSUPPORTED_NAMESPACE",
            "Source class cannot merge distinct private namespace pool entries",
            0,
        ));
    }
    for owner in [
        TraitOwner::Instance(class_index),
        TraitOwner::Class(class_index),
    ] {
        let traits = match owner {
            TraitOwner::Instance(_) => &instance.traits,
            _ => &class.traits,
        };
        let private = traits
            .iter()
            .filter_map(|t| {
                symbols
                    .traits
                    .iter()
                    .find(|b| b.owner == owner && b.name_index == t.name)
            })
            .filter(|t| t.name.namespace.kind == 5)
            .map(|t| t.name.namespace.pool_index)
            .collect::<BTreeSet<_>>();
        if private.len() > 1 {
            return Err(fail(
                "UNSUPPORTED_NAMESPACE",
                "One source class cannot recreate multiple private namespace identities",
                0,
            ));
        }
        for t in traits {
            if !t.metadata.is_empty() {
                return Err(fail(
                    "UNSUPPORTED_SOURCE_DOCUMENT",
                    "Trait metadata must be reconstructed before source publication",
                    0,
                ));
            }
            let binding = symbols
                .traits
                .iter()
                .find(|b| b.owner == owner && b.name_index == t.name)
                .ok_or_else(|| {
                    fail("UNSUPPORTED_SOURCE_DOCUMENT", "Trait name is unresolved", 0)
                })?;
            if !identifier(&binding.name.name) {
                return Err(fail(
                    "UNSUPPORTED_SOURCE_DOCUMENT",
                    "Trait name is not a source identifier",
                    0,
                ));
            }
            let access = visibility(&binding.name, package, owner, abc)?;
            let static_text = if matches!(owner, TraitOwner::Class(_)) {
                "static "
            } else {
                ""
            };
            match t.kind {
                0 | 6 => {
                    let ty = binding
                        .type_ref
                        .as_ref()
                        .ok_or_else(|| fail("UNSUPPORTED_TYPE", "Field type is unresolved", 0))?;
                    check_type(ty)?;
                    text.push_str(&format!(
                        "        {access} {static_text}{} {}:{}",
                        if t.kind == 6 { "const" } else { "var" },
                        binding.name.name,
                        source_type(ty)?
                    ));
                    if t.value != 0 {
                        text.push_str(&format!(" = {}", constant(abc, t.value, t.value_kind)?));
                    }
                    text.push_str(";\n");
                }
                1..=3 => {
                    let method = t.method.unwrap() as usize;
                    let body = body_index(abc, method)?;
                    let u = method_unit(parent, abc.clone(), method, body, &binding.name.name);
                    let result = crate::decompile_owned_with_hierarchy(&u, external);
                    let source = result.source.ok_or_else(|| {
                        fail(
                            "UNSUPPORTED_SOURCE_DOCUMENT",
                            format!(
                                "Owned method {} requires P-code: {:?}",
                                binding.name.name, result.diagnostics
                            ),
                            0,
                        )
                    })?;
                    if !source.starts_with("function ") {
                        return Err(fail(
                            "UNSUPPORTED_NAMESPACE",
                            "Method namespace aliases need document bindings",
                            0,
                        ));
                    }
                    let source = source.replacen(
                        "function ",
                        &format!(
                            "{access} {static_text}{}{}function {}",
                            if t.attributes & 2 != 0 {
                                "override "
                            } else {
                                ""
                            },
                            if t.attributes & 1 != 0 { "final " } else { "" },
                            if t.kind == 2 {
                                "get "
                            } else if t.kind == 3 {
                                "set "
                            } else {
                                ""
                            }
                        ),
                        1,
                    );
                    for line in source.lines() {
                        text.push_str("        ");
                        text.push_str(line);
                        text.push('\n');
                    }
                    claimed.insert(method);
                }
                _ => {
                    return Err(fail(
                        "UNSUPPORTED_SOURCE_DOCUMENT",
                        "Nested class/function trait needs its own owner reconstruction",
                        0,
                    ));
                }
            }
        }
    }
    let cinit = body_index(abc, class.init as usize)?;
    let cu = method_unit(
        parent,
        abc.clone(),
        class.init as usize,
        cinit,
        "class_initializer",
    );
    let static_lines = crate::static_init::reconstruct(&cu, abc, symbols, class_index)?;
    if !static_lines.is_empty() {
        text.push_str("        // Static initializer, in original assignment order.\n        {\n");
        for line in static_lines {
            text.push_str("            ");
            text.push_str(&line);
            text.push('\n');
        }
        text.push_str("        }\n");
    }
    claimed.insert(class.init as usize);
    let init = body_index(abc, instance.init as usize)?;
    let iu = method_unit(
        parent,
        abc.clone(),
        instance.init as usize,
        init,
        &name.name,
    );
    let source = crate::decompile_owned_with_hierarchy(&iu, external)
        .source
        .ok_or_else(|| fail("UNSUPPORTED_SOURCE_DOCUMENT", "Constructor needs P-code", 0))?;
    if !source.starts_with("function ") {
        return Err(fail(
            "UNSUPPORTED_NAMESPACE",
            "Constructor namespace aliases need document bindings",
            0,
        ));
    }
    let source = source.replacen("function ", "public function ", 1);
    for line in source.lines() {
        text.push_str("        ");
        text.push_str(line);
        text.push('\n');
    }
    claimed.insert(instance.init as usize);
    text.push_str("    }\n}\n");
    Ok(SourceDocument {
        path: format!("{}/{}.as", package.replace('.', "/"), name.name)
            .trim_start_matches('/')
            .into(),
        source: text,
        class_index,
        script_index,
    })
}
