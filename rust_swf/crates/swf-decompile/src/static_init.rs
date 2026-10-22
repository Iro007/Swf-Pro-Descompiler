//! Ordered, bounded static slot initialization without introducing source traits.
//! Class-body `var` temporaries would become members, so this proof deliberately
//! emits only assignments and expressions whose evaluation can remain on-stack.
use crate::ir::{identifier, quoted};
use crate::{Result, fail, mnemonic};
use std::time::Instant;
use swf_avm::symbols::{AbcSymbolIndex, MethodContext, TraitBinding, TraitOwner, TypeRef};
use swf_avm::{Abc, Instruction, Operand, ScriptUnit, disassemble};

const MAX_EXPRESSION: usize = 64 * 1024;
const MAX_SOURCE: usize = 8 * 1024 * 1024;

#[derive(Clone, Copy, PartialEq)]
enum SourceType {
    Any,
    Numeric,
    Boolean,
    String,
    Null,
    Array,
    Object,
    Other,
}
fn source_type(binding: &TraitBinding) -> SourceType {
    match binding.type_ref.as_ref() {
        Some(TypeRef::Any) => SourceType::Any,
        Some(TypeRef::Named(n))
            if matches!(n.namespace.kind, 0x08 | 0x16) && n.namespace.uri.is_empty() =>
        {
            match n.name.as_str() {
                "int" | "uint" | "Number" => SourceType::Numeric,
                "Boolean" => SourceType::Boolean,
                "String" => SourceType::String,
                "Array" => SourceType::Array,
                "Object" => SourceType::Object,
                _ => SourceType::Other,
            }
        }
        _ => SourceType::Other,
    }
}
fn assignment_compiles(target: SourceType, value: SourceType) -> bool {
    target == SourceType::Any
        || target == SourceType::Object
        || value == SourceType::Any
        || target == value && target != SourceType::Other
        || value == SourceType::Null
            && matches!(
                target,
                SourceType::String | SourceType::Array | SourceType::Other
            )
}
fn numeric_source(ty: SourceType) -> bool {
    matches!(ty, SourceType::Numeric | SourceType::Any)
}

#[derive(Clone)]
enum Value {
    Class,
    Expression {
        text: String,
        primitive: bool,
        copyable: bool,
        source_type: SourceType,
    },
}
impl Value {
    fn expression(
        text: String,
        primitive: bool,
        copyable: bool,
        source_type: SourceType,
    ) -> Result<Self> {
        if text.len() > MAX_EXPRESSION {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "Static initializer expression exceeds 64 KiB",
                0,
            ));
        }
        Ok(Self::Expression {
            text,
            primitive,
            copyable,
            source_type,
        })
    }
    fn text(&self, offset: usize) -> Result<&str> {
        match self {
            Self::Expression { text, .. } => Ok(text),
            Self::Class => Err(unsupported(
                "Static receiver escaped its owned slot operation",
                offset,
            )),
        }
    }
    fn primitive(&self) -> bool {
        matches!(
            self,
            Self::Expression {
                primitive: true,
                ..
            }
        )
    }
    fn source_type(&self) -> SourceType {
        match self {
            Self::Expression { source_type, .. } => *source_type,
            Self::Class => SourceType::Other,
        }
    }
}
fn unsupported(message: impl Into<String>, offset: usize) -> crate::Failure {
    fail("UNSUPPORTED_STATIC_INITIALIZER", message, offset)
}
fn index(i: &Instruction) -> Result<u32> {
    match i.operands.first() {
        Some(Operand::Unsigned(n) | Operand::Pool { index: n, .. }) => Ok(*n),
        _ => Err(fail(
            "INVALID_OPERAND",
            "Static initializer operand is missing",
            i.offset,
        )),
    }
}
fn pop(stack: &mut Vec<Value>, offset: usize) -> Result<Value> {
    stack.pop().ok_or_else(|| {
        fail(
            "STACK_UNDERFLOW",
            "Static initializer stack underflow",
            offset,
        )
    })
}

/// No calls, dynamic property reads, captured scopes, handlers, local stores,
/// branches, or const writes are admitted. Every write ends with an empty stack,
/// preventing deferred reads from crossing an observable assignment.
pub(crate) fn reconstruct(
    unit: &ScriptUnit,
    abc: &Abc,
    symbols: &AbcSymbolIndex,
    class_index: usize,
) -> Result<Vec<String>> {
    let started = Instant::now();
    let context = MethodContext::from_unit(unit)
        .ok_or_else(|| unsupported("Static initializer context is not exact", 0))?;
    if context.owner != Some(TraitOwner::Class(class_index)) || context.trait_name_index.is_some() {
        return Err(unsupported("Static initializer has an ambiguous owner", 0));
    }
    let method = &abc.methods[context.method_index];
    let body = &abc.bodies[context.body_index];
    let listing = disassemble(unit);
    if listing.state != "decoded"
        || !listing.diagnostics.is_empty()
        || !body.exceptions.is_empty()
        || !body.traits.is_empty()
        || !method.param_types.is_empty()
        || !method.options.is_empty()
        || method.flags & !0x80 != 0
        || body.local_count == 0
        || !context.return_type.as_ref().is_some_and(|ty| {
            matches!(ty, TypeRef::Any)
                || matches!(ty, TypeRef::Named(n) if matches!(n.namespace.kind, 0x08 | 0x16)
                && n.namespace.uri.is_empty() && n.name == "void")
        })
        || listing.instructions.len() > crate::MAX_INSTRUCTIONS
    {
        return Err(unsupported(
            "Static initializer has unsupported method metadata, handlers, or bytecode",
            0,
        ));
    }
    let class_name = symbols.class_names[class_index]
        .as_ref()
        .ok_or_else(|| unsupported("Static class name is unresolved", 0))?;
    let owner = TraitOwner::Class(class_index);
    let binding = |i: &Instruction, slot: bool| -> Result<&TraitBinding> {
        let value = index(i)?;
        let found = if slot {
            let matches = symbols
                .traits
                .iter()
                .filter(|t| {
                    t.owner == owner && t.slot_id == value && value != 0 && matches!(t.kind, 0 | 6)
                })
                .collect::<Vec<_>>();
            (matches.len() == 1).then(|| matches[0])
        } else {
            // A runtime multiname must never be mistaken for a static QName.
            if !matches!(
                abc.multinames.get(value as usize).map(|m| m.kind),
                Some(7 | 9)
            ) {
                return Err(unsupported(
                    "Static slot name is not an exact non-attribute name",
                    i.offset,
                ));
            }
            symbols.resolve(abc, value, owner)
        }
        .ok_or_else(|| unsupported("Static slot binding is absent or ambiguous", i.offset))?;
        if !matches!(found.kind, 0 | 6)
            || !identifier(&found.name.name)
            || !matches!(found.name.namespace.kind, 0x05 | 0x17)
                && !(matches!(found.name.namespace.kind, 0x08 | 0x16)
                    && found.name.namespace.uri.is_empty())
            || symbols.traits.iter().any(|other| {
                other.owner == owner
                    && other.name.name == found.name.name
                    && !other.name.same_binding(&found.name)
            })
        {
            return Err(unsupported(
                "Static property is not one unambiguous owned slot",
                i.offset,
            ));
        }
        Ok(found)
    };
    let target = |b: &TraitBinding| -> String {
        if matches!(b.name.namespace.kind, 0x08 | 0x16) && b.name.namespace.uri.is_empty() {
            format!("({}).public::{}", class_name.name, b.name.name)
        } else {
            format!("({}).{}", class_name.name, b.name.name)
        }
    };
    let mut primitive_slots = vec![true; abc.classes[class_index].traits.len()];
    let mut stack = Vec::<Value>::new();
    let mut scoped = false;
    let mut ended = false;
    let mut lines = Vec::new();
    let mut source_bytes = 0usize;
    for i in &listing.instructions {
        crate::budget(started, i.offset)?;
        let op = mnemonic(&i.name);
        if matches!(op.as_str(), "nop" | "debug" | "debugline" | "debugfile") {
            continue;
        }
        if ended {
            return Err(unsupported(
                "Nontrivial bytecode follows the static return",
                i.offset,
            ));
        }
        match op.as_str() {
            "getlocal0" => stack.push(Value::Class),
            "getlocal" if index(i)? == 0 => stack.push(Value::Class),
            "pushscope" => {
                if scoped
                    || !matches!(pop(&mut stack, i.offset)?, Value::Class)
                    || !stack.is_empty()
                {
                    return Err(unsupported(
                        "Static initializer scope is not its single class receiver",
                        i.offset,
                    ));
                }
                scoped = true;
            }
            "getscopeobject" if scoped && index(i)? == 0 => stack.push(Value::Class),
            "findproperty" | "findpropstrict" if scoped => {
                binding(i, false)?;
                stack.push(Value::Class);
            }
            "getlex" if scoped => {
                let b = binding(i, false)?;
                stack.push(Value::expression(
                    target(b),
                    primitive_slots[b.trait_index],
                    true,
                    source_type(b),
                )?);
            }
            "getproperty" | "getslot" => {
                if !matches!(pop(&mut stack, i.offset)?, Value::Class) {
                    return Err(unsupported(
                        "Static read receiver is not the proven class",
                        i.offset,
                    ));
                }
                let b = binding(i, op == "getslot")?;
                stack.push(Value::expression(
                    target(b),
                    primitive_slots[b.trait_index],
                    true,
                    source_type(b),
                )?);
            }
            "pushnull" | "pushundefined" | "pushtrue" | "pushfalse" | "pushnan" => {
                let text = match op.as_str() {
                    "pushundefined" => "(void 0)",
                    "pushnan" => "(0 / 0)",
                    _ => op.trim_start_matches("push"),
                };
                let ty = match op.as_str() {
                    "pushnull" => SourceType::Null,
                    "pushundefined" => SourceType::Other,
                    "pushtrue" | "pushfalse" => SourceType::Boolean,
                    _ => SourceType::Numeric,
                };
                stack.push(Value::expression(text.into(), true, true, ty)?);
            }
            "pushbyte" | "pushshort" => {
                let n = match i.operands.first() {
                    Some(Operand::Signed(n)) => *n,
                    Some(Operand::Unsigned(n)) => i64::from(*n),
                    _ => {
                        return Err(fail(
                            "INVALID_OPERAND",
                            "Static signed immediate is missing",
                            i.offset,
                        ));
                    }
                };
                let n = if op == "pushbyte" {
                    i64::from(n as i8)
                } else {
                    i64::from(n as i16)
                };
                stack.push(Value::expression(
                    n.to_string(),
                    true,
                    true,
                    SourceType::Numeric,
                )?);
            }
            "pushstring" | "pushint" | "pushuint" | "pushdouble" => {
                let n = index(i)? as usize;
                if op == "pushdouble" && n == 0 {
                    return Err(unsupported(
                        "Reserved double-pool index needs exact P-code",
                        i.offset,
                    ));
                }
                let text = match op.as_str() {
                    "pushstring" => abc.strings.get(n).map(|s| quoted(s)),
                    "pushint" => abc.integers.get(n).map(ToString::to_string),
                    "pushuint" => abc.uints.get(n).map(ToString::to_string),
                    _ => abc.doubles.get(n).and_then(|bits| {
                        let value = f64::from_le_bytes(*bits);
                        value.is_finite().then(|| {
                            if value == 0.0 && value.is_sign_negative() {
                                "-0.0".into()
                            } else {
                                value.to_string()
                            }
                        })
                    }),
                }
                .ok_or_else(|| {
                    unsupported("Static literal pool value is unrepresentable", i.offset)
                })?;
                let ty = if op == "pushstring" {
                    SourceType::String
                } else {
                    SourceType::Numeric
                };
                stack.push(Value::expression(text, true, true, ty)?);
            }
            "dup" => {
                let value = stack
                    .last()
                    .ok_or_else(|| fail("STACK_UNDERFLOW", "Static dup", i.offset))?;
                if matches!(
                    value,
                    Value::Expression {
                        copyable: false,
                        ..
                    }
                ) {
                    return Err(unsupported(
                        "Duplicating an allocation would change its identity",
                        i.offset,
                    ));
                }
                stack.push(value.clone());
            }
            "swap" => {
                let a = pop(&mut stack, i.offset)?;
                let b = pop(&mut stack, i.offset)?;
                stack.extend([a, b]);
            }
            "pop" => {
                let value = pop(&mut stack, i.offset)?;
                if matches!(
                    value,
                    Value::Expression {
                        primitive: false,
                        ..
                    }
                ) {
                    return Err(unsupported(
                        "Discarding an allocation is outside the static proof",
                        i.offset,
                    ));
                }
            }
            "newarray" => {
                let count = index(i)? as usize;
                if count > stack.len() {
                    return Err(fail("STACK_UNDERFLOW", "Static array operands", i.offset));
                }
                let values = stack.split_off(stack.len() - count);
                let parts = values
                    .iter()
                    .map(|v| v.text(i.offset))
                    .collect::<Result<Vec<_>>>()?;
                if parts.iter().map(|p| p.len()).sum::<usize>() > MAX_EXPRESSION {
                    return Err(fail(
                        "DECOMPILE_LIMIT",
                        "Static array exceeds expression budget",
                        i.offset,
                    ));
                }
                stack.push(Value::expression(
                    format!("[{}]", parts.join(", ")),
                    false,
                    false,
                    SourceType::Array,
                )?);
            }
            "converti" | "coercei" | "convertu" | "coerceu" | "convertd" | "coerced"
            | "convertb" | "coerceb" | "converts" | "negate" | "not" | "bitnot" => {
                let value = pop(&mut stack, i.offset)?;
                if !value.primitive() {
                    return Err(unsupported(
                        "Static conversion could invoke user code",
                        i.offset,
                    ));
                }
                if !matches!(op.as_str(), "convertb" | "coerceb" | "not" | "converts")
                    && !numeric_source(value.source_type())
                {
                    return Err(unsupported(
                        "Numeric source conversion needs a statically compatible operand",
                        i.offset,
                    ));
                }
                let value = value.text(i.offset)?;
                let text = match op.as_str() {
                    "converti" | "coercei" => format!("(({value}) | 0)"),
                    "convertu" | "coerceu" => format!("(({value}) >>> 0)"),
                    "convertd" | "coerced" => format!("(+({value}))"),
                    "convertb" | "coerceb" => format!("(!!({value}))"),
                    "converts" => format!("(({value}) + \"\")"),
                    "negate" => format!("(-({value}))"),
                    "not" => format!("(!({value}))"),
                    _ => format!("(~({value}))"),
                };
                let ty = match op.as_str() {
                    "converts" => SourceType::String,
                    "convertb" | "coerceb" | "not" => SourceType::Boolean,
                    _ => SourceType::Numeric,
                };
                stack.push(Value::expression(text, true, true, ty)?);
            }
            "coercea" => {
                let value = stack
                    .last()
                    .ok_or_else(|| fail("STACK_UNDERFLOW", "Static coerce_a", i.offset))?;
                value.text(i.offset)?;
            }
            "add" | "subtract" | "multiply" | "divide" | "modulo" | "lshift" | "rshift"
            | "urshift" | "bitand" | "bitor" | "bitxor" | "equals" | "strictequals"
            | "lessthan" | "lessequals" | "greaterthan" | "greaterequals" => {
                let right = pop(&mut stack, i.offset)?;
                let left = pop(&mut stack, i.offset)?;
                if !left.primitive() || !right.primitive() {
                    return Err(unsupported(
                        "Static arithmetic could invoke user code",
                        i.offset,
                    ));
                }
                let compatible = if op == "add" {
                    numeric_source(left.source_type()) && numeric_source(right.source_type())
                        || left.source_type() == SourceType::String
                        || right.source_type() == SourceType::String
                } else if matches!(op.as_str(), "equals" | "strictequals") {
                    left.source_type() == right.source_type()
                        || left.source_type() == SourceType::Any
                        || right.source_type() == SourceType::Any
                } else if matches!(
                    op.as_str(),
                    "lessthan" | "lessequals" | "greaterthan" | "greaterequals"
                ) {
                    numeric_source(left.source_type()) && numeric_source(right.source_type())
                        || left.source_type() == SourceType::String
                            && right.source_type() == SourceType::String
                } else {
                    numeric_source(left.source_type()) && numeric_source(right.source_type())
                };
                if !compatible {
                    return Err(unsupported(
                        "Static operator needs statically compatible source operands",
                        i.offset,
                    ));
                }
                let operator = match op.as_str() {
                    "add" => "+",
                    "subtract" => "-",
                    "multiply" => "*",
                    "divide" => "/",
                    "modulo" => "%",
                    "lshift" => "<<",
                    "rshift" => ">>",
                    "urshift" => ">>>",
                    "bitand" => "&",
                    "bitor" => "|",
                    "bitxor" => "^",
                    "equals" => "==",
                    "strictequals" => "===",
                    "lessthan" => "<",
                    "lessequals" => "<=",
                    "greaterthan" => ">",
                    _ => ">=",
                };
                let ty = if op == "add" {
                    if left.source_type() == SourceType::String
                        || right.source_type() == SourceType::String
                    {
                        SourceType::String
                    } else if left.source_type() == SourceType::Numeric
                        && right.source_type() == SourceType::Numeric
                    {
                        SourceType::Numeric
                    } else {
                        SourceType::Any
                    }
                } else if matches!(
                    op.as_str(),
                    "equals"
                        | "strictequals"
                        | "lessthan"
                        | "lessequals"
                        | "greaterthan"
                        | "greaterequals"
                ) {
                    SourceType::Boolean
                } else {
                    SourceType::Numeric
                };
                stack.push(Value::expression(
                    format!(
                        "(({}) {operator} ({}))",
                        left.text(i.offset)?,
                        right.text(i.offset)?
                    ),
                    true,
                    true,
                    ty,
                )?);
            }
            "setproperty" | "initproperty" | "setslot" => {
                let value = pop(&mut stack, i.offset)?;
                if !matches!(pop(&mut stack, i.offset)?, Value::Class) || !stack.is_empty() {
                    return Err(unsupported(
                        "Static write has an unproven receiver or values live across the write",
                        i.offset,
                    ));
                }
                let b = binding(i, op == "setslot")?;
                if b.kind == 6 {
                    return Err(unsupported(
                        "Const initialization needs a source declaration at its exact position",
                        i.offset,
                    ));
                }
                if !assignment_compiles(source_type(b), value.source_type()) {
                    return Err(unsupported(
                        "Implicit slot coercion needs an explicit proven source conversion",
                        i.offset,
                    ));
                }
                let primitive_type = b.type_ref.as_ref().is_some_and(|ty| {
                    matches!(ty, TypeRef::Named(n)
                    if matches!(n.namespace.kind, 0x08 | 0x16) && n.namespace.uri.is_empty()
                    && matches!(n.name.as_str(), "int" | "uint" | "Number" | "Boolean" | "String"))
                });
                primitive_slots[b.trait_index] = value.primitive() || primitive_type;
                let line = format!("{} = {};", target(b), value.text(i.offset)?);
                source_bytes += line.len();
                if source_bytes > MAX_SOURCE {
                    return Err(fail(
                        "DECOMPILE_LIMIT",
                        "Static initializer exceeds 8 MiB",
                        i.offset,
                    ));
                }
                lines.push(line);
            }
            "returnvoid" if stack.is_empty() && (scoped || lines.is_empty()) => ended = true,
            _ => {
                return Err(unsupported(
                    format!("Static initializer {} has an unproven effect", i.name),
                    i.offset,
                ));
            }
        }
        if stack.len() > body.max_stack as usize
            || stack.len() > 4096
            || body.init_scope_depth as usize + usize::from(scoped) > body.max_scope_depth as usize
        {
            return Err(fail(
                "STACK_LIMIT",
                "Static initializer exceeds its declared bounds",
                i.offset,
            ));
        }
    }
    if !ended {
        return Err(unsupported(
            "Static initializer has no proven empty-stack return",
            unit.code.len(),
        ));
    }
    if !lines.is_empty()
        && symbols.traits.iter().any(|t| {
            matches!(t.owner, TraitOwner::Instance(n) | TraitOwner::Class(n) if n == class_index)
                && t.name.name == class_name.name
        })
    {
        return Err(fail(
            "SHADOWED_CLASS_RECEIVER",
            "Static class receiver name is shadowed in source scope",
            0,
        ));
    }
    Ok(lines)
}
