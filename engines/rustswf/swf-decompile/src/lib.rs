//! Conservative ActionScript reconstruction. GPL-3.0-or-later.
//!
//! A complete unit is emitted or the exact disassembly is retained. Reads and
//! operations that may invoke user code are evaluated at their bytecode position.
mod cfg;
mod document;
mod hierarchy;
mod static_init;
pub use hierarchy::{HierarchyProvenance, VerifiedHierarchy};
mod ir;
mod reconstruct;
pub use document::{
    DocumentResult, OwnedMethodFragment, SourceDocument, decompile_document,
    decompile_document_with_hierarchy,
};

use serde::Serialize;
use std::time::{Duration, Instant};
pub use swf_avm::symbols::MethodContext;
use swf_avm::{ScriptUnit, disassemble};
use swf_core::Diagnostic;

pub const MAX_INSTRUCTIONS: usize = 50_000;
pub const MAX_BLOCKS: usize = 2_000;

#[derive(Debug, Clone, Serialize)]
pub struct DecompileResult {
    pub unit_id: String,
    pub state: String,
    pub source: Option<String>,
    pub pcode: String,
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Debug)]
pub(crate) struct Failure {
    pub code: &'static str,
    pub message: String,
    pub offset: Option<usize>,
}
pub(crate) type Result<T> = std::result::Result<T, Failure>;
pub(crate) fn fail(code: &'static str, message: impl Into<String>, offset: usize) -> Failure {
    Failure {
        code,
        message: message.into(),
        offset: Some(offset),
    }
}

pub fn decompile(unit: &ScriptUnit) -> DecompileResult {
    decompile_inner(unit, Instant::now(), 0)
}

/// Use an explicit, verified ABC context. Mismatched contexts retain P-code.
pub fn decompile_with_context(unit: &ScriptUnit, context: &MethodContext) -> DecompileResult {
    if !context.matches_unit(unit) {
        let mut result =
            decompile_inner_with_context(unit, Instant::now(), 0, None, true, &[], false);
        result.source = None;
        result.state = "pcode_only".into();
        result.diagnostics.push(Diagnostic {
            code: "INVALID_METHOD_CONTEXT".into(),
            message: "Method context does not belong to this exact ABC body and owner".into(),
            offset: Some(0),
        });
        return result;
    }
    decompile_inner_with_context(unit, Instant::now(), 0, Some(context), false, &[], false)
}

pub(crate) fn decompile_inner(
    unit: &ScriptUnit,
    started: Instant,
    depth: usize,
) -> DecompileResult {
    let context = MethodContext::from_unit(unit);
    decompile_inner_with_context(unit, started, depth, context.as_ref(), false, &[], false)
}

pub(crate) fn decompile_captured(
    unit: &ScriptUnit,
    started: Instant,
    depth: usize,
    captures: &[Option<ir::Value>],
    owned_document: bool,
) -> DecompileResult {
    let context = MethodContext::from_unit(unit);
    decompile_inner_with_context(
        unit,
        started,
        depth,
        context.as_ref(),
        false,
        captures,
        owned_document,
    )
}
pub(crate) fn decompile_owned_with_hierarchy(
    unit: &ScriptUnit,
    hierarchy: Option<&VerifiedHierarchy>,
) -> DecompileResult {
    let context = MethodContext::from_unit(unit);
    let mut result =
        decompile_inner_with_context(unit, Instant::now(), 0, context.as_ref(), false, &[], true);
    if result.source.is_none() {
        return result;
    }
    if let (Some(context), Some(hierarchy), Some(abc)) =
        (context.as_ref(), hierarchy, unit.context.as_ref())
    {
        let class_index = match context.owner {
            Some(
                swf_avm::symbols::TraitOwner::Instance(n) | swf_avm::symbols::TraitOwner::Class(n),
            ) => Some(n),
            _ => None,
        };
        if let Some(class_index) = class_index {
            let mut types = context
                .parameter_types
                .iter()
                .flatten()
                .chain(context.return_type.iter())
                .cloned()
                .collect::<Vec<_>>();
            types.extend(
                abc.bodies[context.body_index]
                    .traits
                    .iter()
                    .filter_map(|t| context.symbols.type_ref(abc, t.type_name)),
            );
            types.extend(
                disassemble(unit)
                    .instructions
                    .iter()
                    .filter(|i| {
                        matches!(mnemonic(&i.name).as_str(), "coerce" | "astype" | "istype")
                    })
                    .filter_map(|i| match i.operands.first() {
                        Some(swf_avm::Operand::Pool { index, .. }) => {
                            context.symbols.type_ref(abc, *index)
                        }
                        _ => None,
                    }),
            );
            let shadowed=types.iter().any(|ty|matches!(ty,swf_avm::symbols::TypeRef::Named(name) if name.namespace.uri.is_empty()&&hierarchy.ancestor_has_member(abc,&context.symbols,class_index,&name.name)));
            if shadowed {
                result.source = None;
                result.state = "pcode_only".into();
                result.diagnostics.push(Diagnostic {
                    code: "SHADOWED_TYPE_BINDING".into(),
                    message: "Verified ancestor metadata shadows a source type binding".into(),
                    offset: Some(0),
                });
            }
        }
    }
    result
}

fn decompile_inner_with_context(
    unit: &ScriptUnit,
    started: Instant,
    depth: usize,
    context: Option<&MethodContext>,
    reject: bool,
    captures: &[Option<ir::Value>],
    owned_document: bool,
) -> DecompileResult {
    let assembly = disassemble(unit);
    let mut result = DecompileResult {
        unit_id: unit.id.clone(),
        state: "pcode_only".into(),
        source: None,
        pcode: assembly.pcode,
        diagnostics: assembly.diagnostics,
    };
    if assembly.state != "decoded" || !unit.diagnostics.is_empty() {
        result.diagnostics.extend(unit.diagnostics.clone());
        return result;
    }
    if reject {
        return result;
    }
    let reconstruction = (|| {
        if unit.parameters.len() > 1024 {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "Method exceeds 1,024 parameters",
                0,
            ));
        }
        if unit.language == "avm2"
            && context.is_none()
            && unit
                .context
                .as_ref()
                .and_then(|abc| unit.body_index.and_then(|n| abc.bodies.get(n)))
                .is_some_and(|body| {
                    body.code == unit.code && unit.method_index == Some(body.method as usize)
                })
        {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "Verified ABC method context exceeded its symbol/type budget",
                0,
            ));
        }
        if depth > 32 {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "Nested function depth exceeds 32",
                0,
            ));
        }
        if assembly.instructions.len() > MAX_INSTRUCTIONS {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "Method exceeds 50,000 instructions",
                0,
            ));
        }
        let graph = cfg::Graph::new(
            &assembly.instructions,
            unit.code.len(),
            started,
            has_verified_avm2_body_without_handlers(unit),
            &unit
                .context
                .as_ref()
                .and_then(|abc| unit.body_index.and_then(|n| abc.bodies.get(n)))
                .map(|body| {
                    body.exceptions
                        .iter()
                        .flat_map(|e| [e.from as usize, e.to as usize, e.target as usize])
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default(),
        )?;
        let source = reconstruct::reconstruct(
            unit,
            &assembly.instructions,
            &graph,
            started,
            depth,
            context,
            captures,
            owned_document,
        )?;
        Ok((source, graph.compiler_epilogue))
    })();
    match reconstruction {
        Ok((source, compiler_epilogue)) => {
            result.state = "structured".into();
            result.source = Some(source);
            if let Some(offset) = compiler_epilogue {
                result.diagnostics.push(Diagnostic {
                    code: "COMPILER_EPILOGUE_RETAINED".into(),
                    message: "Verified unreachable compiler default-return epilogue was omitted from reconstructed source; complete original P-code is retained.".into(),
                    offset: Some(offset),
                });
            }
        }
        Err(error) => result.diagnostics.push(Diagnostic {
            code: error.code.into(),
            message: error.message,
            offset: error.offset,
        }),
    }
    result
}

fn has_verified_avm2_body_without_handlers(unit: &ScriptUnit) -> bool {
    if unit.language != "avm2" {
        return false;
    }
    let Some(abc) = &unit.context else {
        return false;
    };
    let Some(body_index) = unit.body_index else {
        return false;
    };
    let Some(body) = abc.bodies.get(body_index) else {
        return false;
    };
    body.code == unit.code
        && body.exceptions.is_empty()
        && unit.method_index == Some(body.method as usize)
        && abc.methods.get(body.method as usize).is_some()
}

pub(crate) fn budget(started: Instant, offset: usize) -> Result<()> {
    if started.elapsed() > Duration::from_secs(30) {
        Err(fail(
            "DECOMPILE_TIMEOUT",
            "Reconstruction exceeded the 30 second budget",
            offset,
        ))
    } else {
        Ok(())
    }
}

pub(crate) fn mnemonic(name: &str) -> String {
    name.to_ascii_lowercase().replace('_', "")
}
