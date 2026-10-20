use crate::cfg::{Graph, conditional};
use crate::ir::{Node, PropertyName, Value, emit, identifier, member, quoted};
use crate::{Result, budget, fail, mnemonic};
use std::collections::{BTreeMap, BTreeSet};
use std::time::Instant;
use swf_avm::symbols::{MethodContext, TraitOwner, TypeRef};
use swf_avm::{Instruction, Operand, ScriptUnit};

#[derive(Clone, Default)]
struct State {
    stack: Vec<Value>,
    scope: Vec<Value>,
    terminated: bool,
    activation_locals: BTreeMap<usize, Value>,
}
enum Ending {
    Next,
    Jump,
    Branch(String),
    Switch(String),
    Return,
}
struct Lifted {
    nodes: Vec<Node>,
    state: State,
    ending: Ending,
}
struct EnumerationKernel {
    object: usize,
    next: usize,
    values: bool,
}
#[derive(Clone)]
struct LoopFrame {
    head: usize,
    exit: usize,
    scope: Vec<Value>,
    activation_locals: BTreeMap<usize, Value>,
}
struct Builder<'a> {
    unit: &'a ScriptUnit,
    instructions: &'a [Instruction],
    graph: &'a Graph,
    started: Instant,
    claimed: Vec<bool>,
    prefix: String,
    next_temp: usize,
    locals: BTreeSet<usize>,
    registers: BTreeSet<usize>,
    avm1_register_count: Option<usize>,
    parameters: Vec<String>,
    max_stack: usize,
    max_scope: usize,
    init_scope: usize,
    avm2: bool,
    function_depth: usize,
    constants: Vec<String>,
    string_shadowed: bool,
    namespace_aliases: BTreeMap<String, String>,
    namespace_text: usize,
    environment_local: Option<(usize, String)>,
    enumeration: Option<(usize, String)>,
    method_context: Option<&'a MethodContext>,
    loop_frames: Vec<LoopFrame>,
    captured_scope: Vec<Option<Value>>,
    activation_created: bool,
    owned_document: bool,
    local0_expression: String,
    active_catch: Option<(usize, String)>,
    catch_created: bool,
    catch_initialized: bool,
}

pub(crate) fn reconstruct(
    unit: &ScriptUnit,
    instructions: &[Instruction],
    graph: &Graph,
    started: Instant,
    function_depth: usize,
    method_context: Option<&MethodContext>,
    captures: &[Option<Value>],
    owned_document: bool,
) -> Result<String> {
    let avm2 = unit.language == "avm2";
    if !avm2 {
        // Pool operands are decoded in byte order. A ConstantPool outside the
        // straight-line entry block may be skipped or revisited by a branch,
        // so those decoded literals are not a proven runtime environment.
        // Check the complete CFG before lifting any nested function; accepting
        // an earlier definition must not hide a later ambiguous pool update.
        for block in graph.blocks.iter().skip(1) {
            if let Some(instruction) = instructions[block.first..block.end]
                .iter()
                .find(|instruction| mnemonic(&instruction.name) == "constantpool")
            {
                return Err(fail(
                    "DYNAMIC_CONSTANT_POOL",
                    "AVM1 constant pool outside the straight-line entry block has unproven runtime context",
                    instruction.offset,
                ));
            }
        }
    }
    let avm1_function = !avm2 && unit.kind == "function" && unit.avm1_flags.is_some();
    let mut parameter_registers = vec![];
    let mut preloads = vec![];
    let mut max_stack = 50_000;
    let mut max_scope = 256;
    let mut init_scope = 0;
    let mut method_environment = 0;
    if let (Some(abc), Some(body)) = (&unit.context, unit.body_index) {
        let body = abc
            .bodies
            .get(body)
            .ok_or_else(|| fail("INVALID_METHOD", "Missing method body", 0))?;
        if body.exceptions.len() > 1 {
            return Err(fail(
                "UNSUPPORTED_EXCEPTION_FLOW",
                "Multiple/nested exception handlers require exact P-code",
                0,
            ));
        }
        max_stack = body.max_stack as usize;
        max_scope = body.max_scope_depth as usize;
        init_scope = body.init_scope_depth as usize;
        if let Some(method) = unit.method_index.and_then(|n| abc.methods.get(n)) {
            if method.flags & 2 != 0
                && !instructions
                    .iter()
                    .any(|i| mnemonic(&i.name) == "newactivation")
            {
                return Err(fail(
                    "UNSUPPORTED_ACTIVATION",
                    "NEED_ACTIVATION has no explicit activation reconstruction",
                    0,
                ));
            }
            if method.flags & 0x05 == 0x05 {
                return Err(fail(
                    "UNSUPPORTED_METHOD_ENVIRONMENT",
                    "Activation or simultaneous arguments/rest requires exact environment reconstruction",
                    0,
                ));
            }
            method_environment = method.flags & 0x05;
            if method_environment != 0
                && (body.code != unit.code
                    || unit.method_index != Some(body.method as usize)
                    || method.param_types.len() != unit.parameters.len()
                    || body.local_count as usize <= unit.parameters.len() + 1
                    || (method_environment == 1
                        && unit.parameters.iter().any(|p| p == "arguments")))
            {
                return Err(fail(
                    "UNSUPPORTED_METHOD_ENVIRONMENT",
                    "Implicit arguments/rest register or source binding is inconsistent",
                    0,
                ));
            }
        }
    }
    if avm1_function {
        let flags = unit.avm1_flags.unwrap_or_default();
        // This subset models Function2's register inputs for `this` and
        // `_root`. The remaining preload values need an ActionScript receiver
        // or activation model, and suppress flags alter variable lookup.
        if flags & !0x41 != 0 {
            return Err(fail(
                "UNSUPPORTED_REGISTER_ENVIRONMENT",
                "Only preloadThis and preloadRoot have a proven source binding",
                0,
            ));
        }
        if (unit.avm1_register_count.is_some() || !unit.avm1_registers.is_empty())
            && unit.avm1_registers.len() != unit.parameters.len()
        {
            return Err(fail(
                "UNSUPPORTED_REGISTER_ENVIRONMENT",
                "Function2 parameter/register count is inconsistent",
                0,
            ));
        }
        parameter_registers = if unit.avm1_registers.is_empty() {
            vec![0; unit.parameters.len()]
        } else {
            unit.avm1_registers
                .iter()
                .map(|n| usize::from(*n))
                .collect()
        };
        if unit.avm1_register_count.is_none()
            && (flags != 0 || parameter_registers.iter().any(|register| *register != 0))
        {
            return Err(fail(
                "UNSUPPORTED_REGISTER_ENVIRONMENT",
                "Function2 register inputs require declared register-count metadata",
                0,
            ));
        }
        if flags & 0x40 != 0
            && unit
                .parameters
                .iter()
                .zip(&parameter_registers)
                .any(|(name, register)| name == "_root" && *register == 0)
        {
            return Err(fail(
                "UNSUPPORTED_REGISTER_ENVIRONMENT",
                "A formal _root parameter makes the preloaded root binding ambiguous",
                0,
            ));
        }
        let mut register = 1;
        for (flag, expression) in [(0x01, "this"), (0x40, "_root")] {
            if flags & flag != 0 {
                preloads.push((register, expression));
                register += 1;
            }
        }
        if let Some(count) = unit.avm1_register_count
            && parameter_registers
                .iter()
                .chain(preloads.iter().map(|(register, _)| register))
                .any(|register| *register > usize::from(count))
        {
            return Err(fail(
                "UNSUPPORTED_REGISTER_ENVIRONMENT",
                "Function2 parameter or preload register exceeds the declared register count",
                0,
            ));
        }
    } else if unit.avm1_flags.is_some_and(|f| f != 0)
        || unit.avm1_registers.iter().any(|r| *r != 0)
        || unit.avm1_register_count.is_some()
    {
        return Err(fail(
            "UNSUPPORTED_REGISTER_ENVIRONMENT",
            "Function2 register/preload state is available only for a reconstructed function unit",
            0,
        ));
    }
    if !avm2
        && unit
            .parameters
            .iter()
            .enumerate()
            .any(|(n, p)| !identifier(p) || unit.parameters[..n].contains(p))
    {
        return Err(fail(
            "UNSUPPORTED_METHOD_SIGNATURE",
            "AS2 parameter names cannot be renamed without changing lexical bindings",
            0,
        ));
    }
    let mut parameters = vec![];
    for (n, p) in unit.parameters.iter().enumerate() {
        let p = if identifier(p) && !parameters.contains(p) {
            p.clone()
        } else {
            format!("argument{}", n + 1)
        };
        parameters.push(p);
    }
    let mut prefix = if function_depth > 0 {
        format!("__rustswf_nested{function_depth}")
    } else if owned_document {
        format!("__rustswf_m{}", unit.method_index.unwrap_or(0))
    } else {
        "__rustswf".to_string()
    };
    while parameters.iter().any(|s| s.starts_with(&prefix))
        || unit
            .code
            .windows(prefix.len())
            .any(|bytes| bytes == prefix.as_bytes())
        || unit.avm1_constants.iter().any(|s| s.contains(&prefix))
        || unit
            .context
            .as_ref()
            .is_some_and(|a| a.strings.iter().any(|s| s.contains(&prefix)))
        || instructions.iter().any(|i| {
            i.operands
                .iter()
                .any(|o| format!("{o:?}").contains(&prefix))
        })
    {
        budget(started, 0)?;
        prefix.push('_');
    }
    for (index, register) in parameter_registers.iter().enumerate() {
        if *register != 0 {
            // A register-bound Function2 parameter is not installed in the
            // activation object. Give its JS formal a private name so
            // ActionGetVariable(parameter_name) keeps resolving outward.
            parameters[index] = format!("{prefix}_argument{}", index + 1);
        }
    }
    let mut registers = parameter_registers
        .iter()
        .copied()
        .filter(|n| *n != 0)
        .collect::<BTreeSet<_>>();
    registers.extend(preloads.iter().map(|(register, _)| *register));
    let string_shadowed = may_bind_string(unit, instructions);
    let environment_local = (method_environment != 0).then(|| {
        (
            parameters.len() + 1,
            format!(
                "{prefix}_{}",
                if method_environment == 1 {
                    "arguments"
                } else {
                    "rest"
                }
            ),
        )
    });
    let mut b = Builder {
        unit,
        instructions,
        graph,
        started,
        claimed: vec![false; graph.exit],
        prefix,
        next_temp: 0,
        locals: BTreeSet::new(),
        registers,
        avm1_register_count: unit.avm1_register_count.map(usize::from),
        parameters,
        max_stack,
        max_scope,
        init_scope,
        avm2,
        function_depth,
        constants: unit.avm1_constants.clone(),
        string_shadowed,
        namespace_aliases: BTreeMap::new(),
        namespace_text: 0,
        environment_local,
        enumeration: None,
        method_context,
        loop_frames: vec![],
        captured_scope: captures.to_vec(),
        activation_created: false,
        owned_document,
        local0_expression: if owned_document {
            match method_context.and_then(|c| c.owner) {
                Some(TraitOwner::Class(n)) => method_context
                    .and_then(|c| c.symbols.class_names.get(n))
                    .and_then(|n| n.as_ref())
                    .map(|n| n.name.clone())
                    .unwrap_or_else(|| "this".into()),
                _ => "this".into(),
            }
        } else {
            "this".into()
        },
        active_catch: None,
        catch_created: false,
        catch_initialized: false,
    };
    if owned_document {
        if let Some(context) = method_context {
            if let Some(TraitOwner::Class(n)) = context.owner {
                let class_name = context.symbols.class_names[n]
                    .as_ref()
                    .map(|n| n.name.as_str())
                    .unwrap_or("");
                if b.parameters.iter().any(|p| p == class_name)
                    || context.symbols.traits.iter().any(|t| {
                        matches!(t.owner,TraitOwner::Instance(i)|TraitOwner::Class(i) if i==n)
                            && t.name.name == class_name
                    })
                {
                    return Err(fail(
                        "SHADOWED_CLASS_RECEIVER",
                        "Static class receiver name is shadowed in source scope",
                        0,
                    ));
                }
            }
        }
    }
    let (mut nodes, state) = if unit
        .context
        .as_ref()
        .and_then(|a| unit.body_index.and_then(|n| a.bodies.get(n)))
        .is_some_and(|body| !body.exceptions.is_empty())
    {
        b.single_catch()?
    } else {
        b.region(0, graph.exit, State::default(), None, 0)?
    };
    for (n, block) in graph.blocks.iter().enumerate() {
        if !b.claimed[n]
            && instructions[block.first..block.end].iter().any(|i| {
                !matches!(
                    mnemonic(&i.name).as_str(),
                    "end" | "nop" | "label" | "debug" | "debugline" | "debugfile"
                )
            })
        {
            return Err(fail(
                "UNREACHABLE_BYTECODE",
                "Nontrivial unreachable bytecode is retained as complete P-code",
                instructions[block.first].offset,
            ));
        }
    }
    if !state.terminated && !state.stack.is_empty() {
        return Err(fail(
            "UNBALANCED_STACK",
            "Method exits with unresolved operand stack values",
            unit.code.len(),
        ));
    }
    let mut declarations = vec![];
    for n in &b.locals {
        if *n > b.parameters.len() {
            declarations.push(Node::Line(format!("var {}_local{n};", b.prefix)));
        }
    }
    for n in &b.registers {
        declarations.push(Node::Line(format!("var {}_register{n};", b.prefix)));
    }
    if method_environment == 1 {
        declarations.push(Node::Line(format!(
            "var {} = arguments;",
            b.environment_local.as_ref().unwrap().1
        )));
    }
    let mut output = String::new();
    if avm2 || unit.kind == "function" {
        let tail = unit
            .name
            .rsplit([':', '.', '/'])
            .next()
            .unwrap_or(&unit.name);
        let name = if identifier(tail) {
            tail.to_string()
        } else {
            format!("{}_method", b.prefix)
        };
        let mut emitted_parameters = b.parameters.clone();
        let mut return_type = None;
        if let Some(context) = method_context {
            if context.parameter_types.len() != emitted_parameters.len()
                || context.optional_defaults.len() > emitted_parameters.len()
            {
                return Err(fail(
                    "UNSUPPORTED_METHOD_SIGNATURE",
                    "Signature parameter count is inconsistent",
                    0,
                ));
            }
            for (n, ty) in context.parameter_types.iter().enumerate() {
                let ty = ty.as_ref().ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_METHOD_SIGNATURE",
                        "Parameter type identity is unresolved",
                        0,
                    )
                })?;
                if *ty != TypeRef::Any {
                    emitted_parameters[n].push_str(&format!(":{}", b.render_type(ty, 0)?));
                }
            }
            let first_optional = emitted_parameters.len() - context.optional_defaults.len();
            for (n, (value, kind)) in context.optional_defaults.iter().enumerate() {
                emitted_parameters[first_optional + n]
                    .push_str(&format!(" = {}", b.default_value(*value, *kind)?));
            }
            if let Some(ty) = context.return_type.as_ref() {
                if *ty != TypeRef::Any {
                    return_type = Some(b.render_type(ty, 0)?);
                }
            } else {
                return Err(fail(
                    "UNSUPPORTED_METHOD_SIGNATURE",
                    "Return type identity is unresolved",
                    0,
                ));
            }
        }
        if method_environment == 4 {
            emitted_parameters.push(format!("...{}", b.environment_local.as_ref().unwrap().1));
        }
        let function = if avm1_function {
            let mut prologue = declarations;
            for (index, register) in parameter_registers.iter().enumerate() {
                if *register != 0 {
                    prologue.push(Node::Line(format!(
                        "{}_register{register} = {};",
                        b.prefix, b.parameters[index]
                    )));
                }
            }
            for (register, expression) in &preloads {
                prologue.push(Node::Line(format!(
                    "{}_register{register} = {expression};",
                    b.prefix
                )));
            }
            Node::FunctionWithPrologue {
                name,
                parameters: b.parameters.clone(),
                prologue,
                body: nodes,
            }
        } else {
            declarations.append(&mut nodes);
            if let Some(return_type) = return_type {
                Node::TypedFunction {
                    name,
                    parameters: emitted_parameters,
                    return_type,
                    body: declarations,
                }
            } else {
                Node::Function {
                    name,
                    parameters: emitted_parameters,
                    body: declarations,
                }
            }
        };
        emit(&[function], 0, &mut output);
    } else {
        declarations.append(&mut nodes);
        emit(&declarations, 0, &mut output);
    }
    if !b.namespace_aliases.is_empty() {
        let mut namespaces = String::new();
        for (uri, alias) in &b.namespace_aliases {
            namespaces.push_str(&format!("namespace {alias} = {};\n", quoted(uri)));
        }
        output.insert_str(0, &namespaces);
    }
    Ok(output)
}

impl Builder<'_> {
    fn builtin_type(&self, ty: &TypeRef) -> bool {
        match ty {
            TypeRef::Any => true,
            TypeRef::Named(name) => {
                name.namespace.uri.is_empty()
                    && matches!(name.namespace.kind, 0x08 | 0x16)
                    && matches!(
                        name.name.as_str(),
                        "Object"
                            | "String"
                            | "Number"
                            | "int"
                            | "uint"
                            | "Boolean"
                            | "Array"
                            | "Function"
                            | "Namespace"
                            | "XML"
                            | "XMLList"
                            | "Date"
                            | "RegExp"
                            | "Error"
                            | "EvalError"
                            | "RangeError"
                            | "ReferenceError"
                            | "SyntaxError"
                            | "TypeError"
                            | "URIError"
                    )
                    && !self.method_context.is_some_and(|context| {
                        context
                            .symbols
                            .class_names
                            .iter()
                            .flatten()
                            .any(|class| class.same_binding(name))
                    })
            }
            _ => false,
        }
    }
    fn owner_chain(&self) -> Vec<TraitOwner> {
        let Some(context) = self.method_context else {
            return vec![];
        };
        let mut chain = vec![];
        let mut owner = context.owner;
        while let Some(current) = owner {
            if chain.contains(&current) {
                break;
            }
            chain.push(current);
            owner = match current {
                TraitOwner::Instance(n) => context
                    .symbols
                    .superclasses
                    .get(n)
                    .copied()
                    .flatten()
                    .map(TraitOwner::Instance),
                _ => None,
            };
        }
        chain
    }
    fn public_name_collision(&self, name: &str, uri: &str) -> bool {
        let Some(context) = self.method_context else {
            return false;
        };
        let owners = self.owner_chain();
        context.symbols.traits.iter().any(|t| {
            owners.contains(&t.owner)
                && t.name.name == name
                && (!matches!(t.name.namespace.kind, 0x08 | 0x16) || t.name.namespace.uri != uri)
        })
    }
    fn single_catch(&mut self) -> Result<(Vec<Node>, State)> {
        let context = self.method_context.ok_or_else(|| {
            fail(
                "UNSUPPORTED_EXCEPTION_FLOW",
                "Handler needs a verified method body",
                0,
            )
        })?;
        let exception =
            self.unit.context.as_ref().unwrap().bodies[context.body_index].exceptions[0].clone();
        let find = |offset: u32| {
            self.graph
                .blocks
                .iter()
                .position(|block| self.instructions[block.first].offset == offset as usize)
                .ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_EXCEPTION_FLOW",
                        "Handler boundary needs a real CFG block",
                        offset as usize,
                    )
                })
        };
        let from = find(exception.from)?;
        let to = find(exception.to)?;
        let target = find(exception.target)?;
        if from >= to || to >= target {
            return Err(fail(
                "UNSUPPORTED_EXCEPTION_FLOW",
                "Only a disjoint forward try/handler region is supported",
                exception.from as usize,
            ));
        }
        let skip = &self.graph.blocks[to];
        if skip.end - skip.first != 1
            || mnemonic(&self.instructions[skip.first].name) != "jump"
            || skip.successors.len() != 1
            || skip.successors[0] <= target
        {
            return Err(fail(
                "UNSUPPORTED_EXCEPTION_FLOW",
                "Try fallthrough needs one verified jump over the handler",
                exception.to as usize,
            ));
        }
        let join = skip.successors[0];
        let (mut nodes, entry) = self.region(0, from, State::default(), None, 0)?;
        if entry.terminated || !entry.stack.is_empty() {
            return Err(fail(
                "UNSUPPORTED_EXCEPTION_FLOW",
                "Try entry must have an empty operand stack",
                exception.from as usize,
            ));
        }
        let (body, normal) = self.region(from, to, entry.clone(), None, 0)?;
        self.claimed[to] = true;
        let variable = format!("{}_caught0", self.prefix);
        let ty = context
            .symbols
            .type_ref(
                self.unit.context.as_ref().unwrap(),
                exception.exception_type,
            )
            .ok_or_else(|| {
                fail(
                    "UNSUPPORTED_EXCEPTION_FLOW",
                    "Catch type identity is unresolved",
                    exception.target as usize,
                )
            })?;
        let exception_type = self.render_type(&ty, exception.target as usize)?;
        // AVM2 transfers to a handler with the inherited scope only; compiler
        // handler prologues explicitly reinstall this and their catch object.
        let mut caught = entry.clone();
        caught.scope.clear();
        caught.stack.clear();
        caught.stack.push(Value::Expr(variable.clone()));
        self.active_catch = Some((0, variable.clone()));
        self.catch_created = false;
        self.catch_initialized = false;
        let handler_result = self.region(target, join, caught, None, 0);
        self.active_catch = None;
        let (handler, handled) = handler_result?;
        if self.catch_created && !self.catch_initialized {
            return Err(fail(
                "UNSUPPORTED_EXCEPTION_FLOW",
                "Catch object was not initialized from its exception value",
                exception.target as usize,
            ));
        }
        for state in [&normal, &handled] {
            if !state.stack.is_empty()
                || (!state.terminated
                    && (state.scope != entry.scope
                        || state.activation_locals != entry.activation_locals))
            {
                return Err(fail(
                    "UNSUPPORTED_EXCEPTION_FLOW",
                    "Try/catch completion changes stack or lexical environment",
                    exception.target as usize,
                ));
            }
        }
        nodes.push(Node::TryCatch {
            body,
            variable,
            exception_type,
            handler,
        });
        if normal.terminated && handled.terminated {
            let mut state = entry;
            state.terminated = true;
            return Ok((nodes, state));
        }
        let (tail, state) = self.region(join, self.graph.exit, entry, None, 0)?;
        nodes.extend(tail);
        Ok((nodes, state))
    }
    fn catch_type(&self, exception_index: usize, offset: usize) -> Result<TypeRef> {
        let context = self
            .method_context
            .ok_or_else(|| fail("UNSUPPORTED_EXCEPTION_FLOW", "No catch context", offset))?;
        let abc = self.unit.context.as_ref().unwrap();
        let exception = abc.bodies[context.body_index]
            .exceptions
            .get(exception_index)
            .ok_or_else(|| fail("UNSUPPORTED_EXCEPTION_FLOW", "Unknown catch index", offset))?;
        context
            .symbols
            .type_ref(abc, exception.exception_type)
            .ok_or_else(|| fail("UNSUPPORTED_EXCEPTION_FLOW", "Unknown catch type", offset))
    }
    fn catch_property(
        &self,
        index: u32,
        receiver: &Value,
        offset: usize,
    ) -> Result<Option<PropertyName>> {
        let Value::CatchScope {
            variable,
            exception_index,
        } = receiver
        else {
            return Ok(None);
        };
        if !self.catch_initialized {
            return Err(fail(
                "UNSUPPORTED_EXCEPTION_FLOW",
                "Catch variable was read before the original slot initialization",
                offset,
            ));
        }
        let context = self.method_context.unwrap();
        let abc = self.unit.context.as_ref().unwrap();
        let expected = abc.bodies[context.body_index].exceptions[*exception_index].variable_name;
        let names = context.symbols.candidates(abc, index);
        let expected = context.symbols.candidates(abc, expected);
        if names.len() != 1 || expected.len() != 1 || !names[0].same_binding(&expected[0]) {
            return Err(fail(
                "UNSUPPORTED_EXCEPTION_FLOW",
                "Catch property differs from its exact variable trait",
                offset,
            ));
        }
        Ok(Some(PropertyName {
            expression: variable.clone(),
            qualified: false,
        }))
    }
    fn activation_slot(
        &self,
        object: &str,
        body_index: usize,
        slot: u32,
        offset: usize,
    ) -> Result<(String, TypeRef)> {
        let context = self.method_context.ok_or_else(|| {
            fail(
                "UNSUPPORTED_ACTIVATION",
                "Activation has no verified ABC context",
                offset,
            )
        })?;
        let matches = context
            .symbols
            .traits
            .iter()
            .filter(|t| t.owner == TraitOwner::Activation(body_index) && t.slot_id == slot)
            .collect::<Vec<_>>();
        if matches.len() != 1 || matches[0].kind != 0 || slot == 0 {
            return Err(fail(
                "UNSUPPORTED_ACTIVATION",
                "Slot requires one explicit mutable activation trait",
                offset,
            ));
        }
        Ok((
            format!("{object}_slot{slot}"),
            matches[0].type_ref.clone().ok_or_else(|| {
                fail(
                    "UNSUPPORTED_TYPE",
                    "Activation slot type is unresolved",
                    offset,
                )
            })?,
        ))
    }
    fn activation_property(
        &self,
        index: u32,
        receiver: &Value,
        offset: usize,
    ) -> Result<Option<PropertyName>> {
        let Value::Activation { object, body_index } = receiver else {
            return Ok(None);
        };
        let context = self.method_context.ok_or_else(|| {
            fail(
                "UNSUPPORTED_ACTIVATION",
                "Missing activation context",
                offset,
            )
        })?;
        let abc = self.unit.context.as_ref().unwrap();
        let binding = context
            .symbols
            .resolve(abc, index, TraitOwner::Activation(*body_index))
            .ok_or_else(|| {
                fail(
                    "UNSUPPORTED_ACTIVATION",
                    "Property is not an exact activation trait",
                    offset,
                )
            })?;
        let (field, _) = self.activation_slot(object, *body_index, binding.slot_id, offset)?;
        Ok(Some(PropertyName {
            expression: field,
            qualified: false,
        }))
    }
    fn typed_assignment(
        &mut self,
        ty: &TypeRef,
        value: String,
        nodes: &mut Vec<Node>,
        offset: usize,
    ) -> Result<String> {
        if *ty == TypeRef::Any {
            return Ok(value);
        }
        let ty = self.render_type(ty, offset)?;
        let temp = self.fresh();
        nodes.push(Node::Line(format!("var {temp}:{ty} = {value};")));
        Ok(temp)
    }
    fn render_type(&mut self, ty: &TypeRef, offset: usize) -> Result<String> {
        match ty {
            TypeRef::Any => Ok("*".into()),
            TypeRef::Named(name) => {
                let owners = self.owner_chain();
                if name.namespace.uri.is_empty()
                    && (self.parameters.contains(&name.name)
                        || self.method_context.is_some_and(|context| {
                            context.symbols.traits.iter().any(|trait_info| {
                                owners.contains(&trait_info.owner)
                                    && trait_info.name.name == name.name
                            })
                        }))
                {
                    return Err(fail(
                        "SHADOWED_TYPE_BINDING",
                        "An owned member or parameter shadows the source type name",
                        offset,
                    ));
                }
                if name
                    .namespace
                    .uri
                    .chars()
                    .last()
                    .is_some_and(|c| ('\u{e000}'..='\u{f8ff}').contains(&c))
                {
                    return Err(fail(
                        "UNSUPPORTED_TYPE",
                        "Versioned API type namespace requires a player-version binding proof",
                        offset,
                    ));
                }
                if !matches!(name.namespace.kind, 0x08 | 0x16)
                    || !(identifier(&name.name)
                        || (name.name == "void" && name.namespace.uri.is_empty()))
                {
                    return Err(fail(
                        "UNSUPPORTED_TYPE",
                        "Type namespace or spelling needs its owning class document",
                        offset,
                    ));
                }
                if name.namespace.uri.is_empty() {
                    if self.parameters.contains(&name.name) {
                        return Err(fail(
                            "SHADOWED_LEXICAL_BINDING",
                            "Parameter shadows type binding",
                            offset,
                        ));
                    }
                    Ok(name.name.clone())
                } else if name.namespace.kind == 0x16
                    && name.namespace.uri.split('.').all(identifier)
                {
                    Ok(format!("{}.{}", name.namespace.uri, name.name))
                } else {
                    Err(fail(
                        "UNSUPPORTED_TYPE",
                        "Type URI has no verified package-qualified AS3 annotation syntax",
                        offset,
                    ))
                }
            }
            TypeRef::Parameterized { base, parameters } => {
                let base = self.render_type(base, offset)?;
                let params = parameters
                    .iter()
                    .map(|ty| self.render_type(ty, offset))
                    .collect::<Result<Vec<_>>>()?;
                Ok(format!("{base}.<{}>", params.join(", ")))
            }
        }
    }
    fn default_value(&self, index: u32, kind: u8) -> Result<String> {
        let abc = self
            .unit
            .context
            .as_ref()
            .ok_or_else(|| fail("UNSUPPORTED_METHOD_SIGNATURE", "Default has no ABC", 0))?;
        let value = match kind {
            0x00 => "undefined".into(),
            0x0a => "false".into(),
            0x0b => "true".into(),
            0x0c => "null".into(),
            0x01 => quoted(
                abc.string(index)
                    .ok_or_else(|| fail("INVALID_OPERAND", "Invalid default string", 0))?,
            ),
            0x03 => abc
                .integers
                .get(index as usize)
                .ok_or_else(|| fail("INVALID_OPERAND", "Invalid default int", 0))?
                .to_string(),
            0x04 => abc
                .uints
                .get(index as usize)
                .ok_or_else(|| fail("INVALID_OPERAND", "Invalid default uint", 0))?
                .to_string(),
            0x06 => {
                let bits = abc
                    .doubles
                    .get(index as usize)
                    .ok_or_else(|| fail("INVALID_OPERAND", "Invalid default Number", 0))?;
                let number = f64::from_le_bytes(*bits);
                if !number.is_finite() {
                    return Err(fail(
                        "UNSUPPORTED_METHOD_SIGNATURE",
                        "Nonfinite default needs a proven intrinsic binding",
                        0,
                    ));
                }
                if number == 0.0 && number.is_sign_negative() {
                    "-0.0".into()
                } else {
                    number.to_string()
                }
            }
            _ => {
                return Err(fail(
                    "UNSUPPORTED_METHOD_SIGNATURE",
                    "Namespace or extended numeric default requires exact signature handling",
                    0,
                ));
            }
        };
        Ok(value)
    }
    fn namespace_alias(&mut self, uri: &str, offset: usize) -> Result<String> {
        if let Some(alias) = self.namespace_aliases.get(uri) {
            return Ok(alias.clone());
        }
        let text = quoted(uri);
        if self.namespace_aliases.len() >= 128
            || self.namespace_text.saturating_add(text.len()) > 256 * 1024
        {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "Namespace declarations exceed the supported text budget",
                offset,
            ));
        }
        let alias = format!("{}_namespace{}", self.prefix, self.namespace_aliases.len());
        self.namespace_text += text.len();
        self.namespace_aliases.insert(uri.to_owned(), alias.clone());
        Ok(alias)
    }
    fn resolved_property(
        &mut self,
        uri: &str,
        name: &str,
        runtime: bool,
        offset: usize,
    ) -> Result<PropertyName> {
        if uri.is_empty() && !runtime {
            return Ok(PropertyName {
                expression: name.into(),
                qualified: false,
            });
        }
        let alias = self.namespace_alias(uri, offset)?;
        let key = if runtime {
            format!("[{name}]")
        } else if identifier(name) {
            name.into()
        } else {
            format!("[{}]", quoted(name))
        };
        Ok(PropertyName {
            expression: format!("{alias}::{key}"),
            qualified: true,
        })
    }
    fn property(&mut self, i: &Instruction, state: Option<&mut State>) -> Result<PropertyName> {
        let Some(Operand::Pool { index, .. }) = i.operands.first() else {
            return Err(fail(
                "INVALID_OPERAND",
                "Property requires a multiname reference",
                i.offset,
            ));
        };
        let abc = self.unit.context.as_ref().ok_or_else(|| {
            fail(
                "UNSUPPORTED_NAMESPACE",
                "Property has no ABC namespace context",
                i.offset,
            )
        })?;
        let multiname = abc
            .multinames
            .get(*index as usize)
            .ok_or_else(|| fail("INVALID_OPERAND", "Invalid multiname", i.offset))?;
        let kind = multiname.kind;
        let property = abc.public_property(*index);
        if let Some(property) = property {
            if let Some(name) = property.name {
                if property.namespace_uri.is_empty()
                    && matches!(
                        self.method_context.and_then(|c| c.owner),
                        Some(TraitOwner::Instance(_) | TraitOwner::Class(_))
                    )
                {
                    let key = if identifier(&name) {
                        name
                    } else {
                        format!("[{}]", quoted(&name))
                    };
                    return Ok(PropertyName {
                        expression: format!("public::{key}"),
                        qualified: true,
                    });
                }
                if property.namespace_uri.is_empty()
                    && self.public_name_collision(&name, &property.namespace_uri)
                {
                    let alias = self.namespace_alias(&property.namespace_uri, i.offset)?;
                    let key = if identifier(&name) {
                        name
                    } else {
                        format!("[{}]", quoted(&name))
                    };
                    return Ok(PropertyName {
                        expression: format!("{alias}::{key}"),
                        qualified: true,
                    });
                }
                return self.resolved_property(&property.namespace_uri, &name, false, i.offset);
            }
            let state = state.ok_or_else(|| {
                fail(
                    "UNPROVEN_RUNTIME_NAME",
                    "Runtime name is not valid for this lexical operation",
                    i.offset,
                )
            })?;
            let key = self.pop(state, i.offset)?;
            let key = primitive_key(&key).ok_or_else(|| {
                fail(
                    "UNPROVEN_RUNTIME_NAME",
                    "Runtime key is not a proven primitive literal",
                    i.offset,
                )
            })?;
            return self.resolved_property(&property.namespace_uri, &key, true, i.offset);
        }
        // A namespace set may contain private and public candidates while the
        // owning ABC traits prove exactly one binding. Emit that exact public
        // namespace; private/internal/protected source still needs a document.
        if let Some(context) = self.method_context {
            let binding = context
                .owner
                .and_then(|owner| context.symbols.resolve(abc, *index, owner))
                .or_else(|| context.symbols.resolve_script(abc, *index));
            if let Some(binding) = binding {
                if matches!(binding.name.namespace.kind, 0x08 | 0x16)
                    && abc
                        .public_namespace_uri(binding.name.namespace.pool_index)
                        .is_some()
                {
                    if binding.name.namespace.uri.is_empty()
                        && self.public_name_collision(&binding.name.name, "")
                    {
                        let alias = self.namespace_alias("", i.offset)?;
                        return Ok(PropertyName {
                            expression: format!("{alias}::{}", binding.name.name),
                            qualified: true,
                        });
                    }
                    return self.resolved_property(
                        &binding.name.namespace.uri,
                        &binding.name.name,
                        false,
                        i.offset,
                    );
                }
                if self.owned_document
                    && matches!(
                        binding.owner,
                        TraitOwner::Instance(_) | TraitOwner::Class(_)
                    )
                    && matches!(binding.name.namespace.kind, 0x05 | 0x17 | 0x18 | 0x1a)
                    && identifier(&binding.name.name)
                {
                    if binding.name.namespace.kind == 0x05 && Some(binding.owner) != context.owner {
                        return Err(fail(
                            "UNSUPPORTED_NAMESPACE",
                            "Inherited private trait is not visible in the current source owner",
                            i.offset,
                        ));
                    }
                    if binding.name.namespace.kind == 0x17 {
                        let current_class = match context.owner {
                            Some(TraitOwner::Instance(n) | TraitOwner::Class(n)) => Some(n),
                            _ => None,
                        };
                        if current_class
                            .and_then(|n| context.symbols.class_names[n].as_ref())
                            .is_none_or(|n| n.namespace.uri != binding.name.namespace.uri)
                        {
                            return Err(fail(
                                "UNSUPPORTED_NAMESPACE",
                                "Inherited internal trait belongs to a different package",
                                i.offset,
                            ));
                        }
                    }
                    let owners = self.owner_chain();
                    if context.symbols.traits.iter().any(|other| {
                        owners.contains(&other.owner)
                            && other.name.name == binding.name.name
                            && !other.name.same_binding(&binding.name)
                    }) {
                        return Err(fail(
                            "UNSUPPORTED_NAMESPACE",
                            "Same-spelled member namespaces require explicit source qualification",
                            i.offset,
                        ));
                    }
                    return Ok(PropertyName {
                        expression: binding.name.name.clone(),
                        qualified: false,
                    });
                }
            }
        }
        if matches!(kind, 0x0f | 0x11) {
            let static_name = if kind == 0x0f {
                Some(
                    abc.string(multiname.name)
                        .filter(|name| !name.is_empty())
                        .ok_or_else(|| {
                            fail(
                                "UNSUPPORTED_NAMESPACE",
                                "Wildcard RTQName is not a property name",
                                i.offset,
                            )
                        })?
                        .to_owned(),
                )
            } else {
                None
            };
            let state = state.ok_or_else(|| {
                fail(
                    "UNPROVEN_RUNTIME_NAMESPACE",
                    "Lexical runtime namespace requires exact P-code",
                    i.offset,
                )
            })?;
            let (name, runtime) = if let Some(name) = static_name {
                (name, false)
            } else {
                let key = self.pop(state, i.offset)?;
                (
                    primitive_key(&key).ok_or_else(|| {
                        fail(
                            "UNPROVEN_RUNTIME_NAME",
                            "Runtime QName key is not a proven primitive literal",
                            i.offset,
                        )
                    })?,
                    true,
                )
            };
            let Value::PublicNamespace(uri) = self.pop(state, i.offset)? else {
                return Err(fail(
                    "UNPROVEN_RUNTIME_NAMESPACE",
                    "Runtime namespace is not a proven public constant",
                    i.offset,
                ));
            };
            return self.resolved_property(&uri, &name, runtime, i.offset);
        }
        Err(fail(
            "UNSUPPORTED_NAMESPACE",
            "Namespace identity is private/internal/protected, ambiguous, versioned, or XML attribute; preserved as P-code",
            i.offset,
        ))
    }
    fn stringify(&self, value: &str, offset: usize) -> Result<String> {
        if self.string_shadowed {
            Err(fail(
                "SHADOWED_INTRINSIC",
                "String conversion has a possibly shadowed String binding; exact P-code retained",
                offset,
            ))
        } else {
            Ok(format!("String({value})"))
        }
    }
    fn fresh(&mut self) -> String {
        let name = format!("{}_value{}", self.prefix, self.next_temp);
        self.next_temp += 1;
        name
    }
    fn avm1_register(&mut self, register: usize, offset: usize) -> Result<String> {
        if self
            .avm1_register_count
            .is_some_and(|count| register > count)
        {
            return Err(fail(
                "UNSUPPORTED_REGISTER_ENVIRONMENT",
                "Function2 register access exceeds the declared register count",
                offset,
            ));
        }
        self.registers.insert(register);
        Ok(format!("{}_register{register}", self.prefix))
    }
    fn evaluate(&mut self, expression: String, nodes: &mut Vec<Node>) -> Value {
        let name = self.fresh();
        nodes.push(Node::Line(format!("var {name} = {expression};")));
        Value::Expr(name)
    }
    fn pop(&self, state: &mut State, offset: usize) -> Result<Value> {
        state.stack.pop().ok_or_else(|| {
            fail(
                "STACK_UNDERFLOW",
                "Instruction consumes an empty operand stack",
                offset,
            )
        })
    }
    fn pop_text(&self, state: &mut State, offset: usize) -> Result<String> {
        Ok(self.pop(state, offset)?.text()?.to_string())
    }
    fn operands(&self, i: &Instruction, n: usize) -> Result<usize> {
        match i.operands.get(n) {
            Some(Operand::Unsigned(v)) => Ok(*v as usize),
            Some(Operand::Signed(v)) if *v >= 0 => Ok(*v as usize),
            Some(Operand::Pool { index, .. }) => Ok(*index as usize),
            _ => Err(fail(
                "INVALID_OPERAND",
                "Expected an integer operand",
                i.offset,
            )),
        }
    }
    fn local(&mut self, n: usize, offset: usize) -> Result<String> {
        if let (Some(abc), Some(body)) = (&self.unit.context, self.unit.body_index)
            && n >= abc.bodies[body].local_count as usize
        {
            return Err(fail(
                "INVALID_LOCAL",
                "Local register is outside local_count",
                offset,
            ));
        }
        if n == 0 {
            Ok(self.local0_expression.clone())
        } else if n <= self.parameters.len() {
            Ok(self.parameters[n - 1].clone())
        } else if let Some((register, name)) = &self.environment_local
            && n == *register
        {
            Ok(name.clone())
        } else {
            self.locals.insert(n);
            Ok(format!("{}_local{n}", self.prefix))
        }
    }
    fn args(
        &self,
        state: &mut State,
        count: usize,
        offset: usize,
        reverse: bool,
    ) -> Result<Vec<String>> {
        if count > state.stack.len() {
            return Err(fail(
                "STACK_UNDERFLOW",
                "Argument or container count exceeds the operand stack",
                offset,
            ));
        }
        let mut args = (0..count)
            .map(|_| self.pop_text(state, offset))
            .collect::<Result<Vec<_>>>()?;
        if reverse {
            args.reverse();
        }
        Ok(args)
    }
    fn avm1_count(&self, state: &mut State, offset: usize) -> Result<usize> {
        self.pop(state, offset)?.literal_int().ok_or_else(|| {
            fail(
                "DYNAMIC_ARGUMENT_COUNT",
                "AVM1 count is not a known non-negative integer literal",
                offset,
            )
        })
    }
    fn avm1_name(&self, value: Value, offset: usize) -> Result<String> {
        let name = value.literal_string().ok_or_else(|| {
            fail(
                "DYNAMIC_VARIABLE_NAME",
                "Variable/constructor name is not a known string",
                offset,
            )
        })?;
        if identifier(&name) || matches!(name.as_str(), "this" | "super") {
            Ok(name)
        } else {
            Err(fail(
                "UNSUPPORTED_VARIABLE_NAME",
                "Variable name is not an ActionScript identifier",
                offset,
            ))
        }
    }
    fn target(&self, value: Value, property: &PropertyName, offset: usize) -> Result<String> {
        match value {
            Value::CatchScope { variable, .. } if property.expression == variable => Ok(variable),
            Value::CatchScope { .. } => Err(fail(
                "UNSUPPORTED_CATCH_ESCAPE",
                "Catch property was not proven",
                offset,
            )),
            Value::Activation { object, .. }
                if property.expression.starts_with(&format!("{object}_slot")) =>
            {
                Ok(member(&object, &property.expression))
            }
            Value::Activation { .. } => Err(fail(
                "UNSUPPORTED_ACTIVATION_ESCAPE",
                "Activation property was not proven from its exact trait",
                offset,
            )),
            Value::BoundReceiver {
                receiver,
                property: name,
            } if name == *property => match receiver {
                Some(receiver) if property.qualified => {
                    Ok(format!("({receiver}).{}", property.expression))
                }
                Some(receiver) => Ok(member(&receiver, &property.expression)),
                None => Ok(property.expression.clone()),
            },
            Value::BoundReceiver { .. } => Err(fail(
                "UNPROVEN_RECEIVER",
                "Proven receiver does not match its property",
                offset,
            )),
            Value::LexicalReceiver(name)
                if name == *property
                    && (property.qualified || identifier(&property.expression)) =>
            {
                Ok(property.expression.clone())
            }
            Value::LexicalReceiver(_) => Err(fail(
                "UNPROVEN_RECEIVER",
                "Lexical receiver does not match its resolved property",
                offset,
            )),
            other if property.qualified => {
                Ok(format!("({}).{}", other.text()?, property.expression))
            }
            other => Ok(member(other.text()?, &property.expression)),
        }
    }
    fn branch(&self, i: &Instruction, state: &mut State) -> Result<String> {
        let name = mnemonic(&i.name);
        if matches!(name.as_str(), "if" | "iftrue" | "iffalse") {
            let v = self.pop_text(state, i.offset)?;
            return Ok(if name == "iffalse" {
                format!("!({v})")
            } else {
                v
            });
        }
        let right = self.pop_text(state, i.offset)?;
        let left = self.pop_text(state, i.offset)?;
        let operator = match name.as_str() {
            "ifeq" => "==",
            "ifne" => "!=",
            "ifstricteq" => "===",
            "ifstrictne" => "!==",
            "iflt" | "ifnlt" => "<",
            "ifle" | "ifnle" => "<=",
            "ifgt" | "ifngt" => ">",
            "ifge" | "ifnge" => ">=",
            _ => {
                return Err(fail(
                    "UNSUPPORTED_CONTROL_FLOW",
                    "Unknown conditional instruction",
                    i.offset,
                ));
            }
        };
        // Negated comparisons retain unordered/NaN semantics; >= is not !<.
        let condition = format!("({left} {operator} {right})");
        Ok(
            if name.starts_with("ifn") && !matches!(name.as_str(), "ifne") {
                format!("!({condition})")
            } else {
                condition
            },
        )
    }

    fn lift(&mut self, block: usize, mut state: State) -> Result<Lifted> {
        let b = &self.graph.blocks[block];
        let mut nodes = vec![];
        let mut ending = Ending::Next;
        for n in b.first..b.end {
            let i = &self.instructions[n];
            budget(self.started, i.offset)?;
            let op = mnemonic(&i.name);
            if state
                .stack
                .iter()
                .any(|v| matches!(v, Value::LexicalReceiver(_)))
                && !lookup_order_safe(&op, i, &state)
            {
                return Err(fail(
                    "UNPROVEN_LOOKUP_ORDER",
                    "A potentially throwing lexical receiver lookup precedes another observable operation; use exact P-code",
                    i.offset,
                ));
            }
            if op == "lookupswitch" {
                let Value::Int32(index) = self.pop(&mut state, i.offset)? else {
                    return Err(fail(
                        "UNPROVEN_SWITCH_INDEX",
                        "lookupswitch requires a proven signed-int stack value",
                        i.offset,
                    ));
                };
                ending = Ending::Switch(index);
            } else if conditional(&i.name) {
                ending = Ending::Branch(self.branch(i, &mut state)?);
            } else if op == "jump" {
                ending = Ending::Jump;
            } else if matches!(op.as_str(), "returnvalue" | "return") {
                let value = self.pop_text(&mut state, i.offset)?;
                nodes.push(Node::Line(format!("return {value};")));
                state.terminated = true;
                ending = Ending::Return;
            } else if matches!(op.as_str(), "returnvoid" | "end") {
                if op != "end" {
                    nodes.push(Node::Line("return;".into()));
                }
                state.terminated = true;
                ending = Ending::Return;
            } else if op == "throw" {
                let v = self.pop_text(&mut state, i.offset)?;
                nodes.push(Node::Line(format!("throw {v};")));
                state.terminated = true;
                ending = Ending::Return;
            } else {
                self.step(i, &op, &mut state, &mut nodes)?;
            }
            if state.stack.len() > self.max_stack {
                return Err(fail(
                    "STACK_LIMIT",
                    "Operand stack exceeds the declared max_stack or limit",
                    i.offset,
                ));
            }
            if state.scope.len() + self.init_scope > self.max_scope {
                return Err(fail(
                    "SCOPE_LIMIT",
                    "Scope stack exceeds max_scope_depth",
                    i.offset,
                ));
            }
        }
        Ok(Lifted {
            nodes,
            state,
            ending,
        })
    }

    fn step(
        &mut self,
        i: &Instruction,
        op: &str,
        state: &mut State,
        nodes: &mut Vec<Node>,
    ) -> Result<()> {
        let offset = i.offset;
        match op {
            "nop" | "label" | "debug" | "debugline" | "debugfile" | "bkpt" | "bkptline" => {}
            "constantpool" => {
                let Some(Operand::Values(values)) = i.operands.first() else {
                    return Err(fail(
                        "INVALID_OPERAND",
                        "Constant pool is unavailable",
                        offset,
                    ));
                };
                self.constants = values
                    .iter()
                    .map(|s| {
                        serde_json::from_str::<String>(s).map_err(|_| {
                            fail(
                                "INVALID_OPERAND",
                                "Constant pool string is malformed",
                                offset,
                            )
                        })
                    })
                    .collect::<Result<Vec<_>>>()?;
            }
            "dup" | "pushduplicate" => {
                let value = state
                    .stack
                    .last()
                    .cloned()
                    .ok_or_else(|| fail("STACK_UNDERFLOW", "Empty dup", offset))?;
                state.stack.push(value);
            }
            "swap" | "stackswap" => {
                let right = self.pop(state, offset)?;
                let left = self.pop(state, offset)?;
                state.stack.extend([right, left]);
            }
            "pop" => {
                let value = self.pop(state, offset)?;
                if !matches!(
                    value,
                    Value::Activation { .. }
                        | Value::CatchScope { .. }
                        | Value::BoundReceiver { .. }
                ) {
                    value.text()?;
                }
            }
            "pushtrue" | "pushfalse" | "pushnull" | "pushundefined" | "pushnan" => {
                state.stack.push(Value::Literal(match op {
                    "pushnan" => "(0 / 0)".into(),
                    "pushundefined" => "(void 0)".into(),
                    _ => op.trim_start_matches("push").to_string(),
                }))
            }
            "pushbyte" | "pushshort" => {
                let v = match i.operands.first() {
                    Some(Operand::Signed(v)) => {
                        if op == "pushshort" {
                            i64::from(*v as u16 as i16)
                        } else {
                            *v
                        }
                    }
                    Some(Operand::Unsigned(v)) => {
                        if op == "pushbyte" {
                            i64::from(*v as u8 as i8)
                        } else {
                            i64::from(*v as u16 as i16)
                        }
                    }
                    _ => return Err(fail("INVALID_OPERAND", "Missing signed immediate", offset)),
                };
                state.stack.push(if self.avm2 {
                    Value::Int32(v.to_string())
                } else {
                    Value::Literal(v.to_string())
                });
            }
            "pushint" | "pushuint" | "pushdouble" => {
                let text = match i.operands.first() {
                    Some(Operand::Pool { text: Some(s), .. }) => s,
                    _ => {
                        return Err(fail(
                            "UNSUPPORTED_NUMERIC_LITERAL",
                            "Numeric pool value cannot be represented faithfully",
                            offset,
                        ));
                    }
                };
                if text.parse::<f64>().is_err() || text.starts_with("bits:") {
                    return Err(fail(
                        "UNSUPPORTED_NUMERIC_LITERAL",
                        "Non-finite numeric payload requires exact P-code",
                        offset,
                    ));
                }
                state.stack.push(if op == "pushint" {
                    Value::Int32(text.clone())
                } else {
                    Value::Literal(text.clone())
                });
            }
            "pushstring" => {
                let text = match i.operands.first() {
                    Some(Operand::Pool { text: Some(s), .. }) | Some(Operand::Text(s)) => s,
                    _ => return Err(fail("INVALID_OPERAND", "Missing string pool value", offset)),
                };
                state.stack.push(Value::Literal(quoted(text)));
            }
            "push" if !self.avm2 => {
                let Some(Operand::Values(values)) = i.operands.first() else {
                    return Err(fail("INVALID_OPERAND", "Missing AVM1 Push values", offset));
                };
                for text in values {
                    if let Some(index) = text
                        .strip_prefix("r:")
                        .and_then(|s| s.parse::<usize>().ok())
                    {
                        let name = self.avm1_register(index, offset)?;
                        let v = self.evaluate(name, nodes);
                        state.stack.push(v);
                    } else if valid_literal(text) {
                        state.stack.push(Value::Literal(if text == "undefined" {
                            "(void 0)".into()
                        } else {
                            text.clone()
                        }));
                    } else {
                        return Err(fail(
                            "UNSUPPORTED_NUMERIC_LITERAL",
                            "AVM1 Push contains an unresolved register/pool or numeric bit payload",
                            offset,
                        ));
                    }
                }
            }
            op if self.avm2 && op.starts_with("getlocal") => {
                let n = register_index(op, "getlocal", i, self)?;
                let local = self.local(n, offset)?;
                if let Some(value) = state.activation_locals.get(&n) {
                    state.stack.push(value.clone());
                    return Ok(());
                }
                let v = if n == 0 {
                    Value::Expr(local)
                } else {
                    self.evaluate(local, nodes)
                };
                state.stack.push(v);
            }
            op if self.avm2 && op.starts_with("setlocal") => {
                let n = register_index(op, "setlocal", i, self)?;
                if n == 0 {
                    return Err(fail(
                        "UNSUPPORTED_THIS_ASSIGNMENT",
                        "Writing local0 changes the method receiver",
                        offset,
                    ));
                }
                let value = self.pop(state, offset)?;
                let local = self.local(n, offset)?;
                if let Value::Activation { object, .. } = &value {
                    nodes.push(Node::Line(format!("{local} = {object};")));
                    state.activation_locals.insert(n, value);
                } else if matches!(value, Value::CatchScope { .. }) {
                    state.activation_locals.insert(n, value);
                } else {
                    state.activation_locals.remove(&n);
                    nodes.push(Node::Line(format!("{local} = {};", value.text()?)));
                }
            }
            "kill" => {
                let n = self.operands(i, 0)?;
                state.activation_locals.remove(&n);
                if n == 0 {
                    return Err(fail(
                        "UNSUPPORTED_THIS_ASSIGNMENT",
                        "Cannot kill method receiver",
                        offset,
                    ));
                }
                let local = self.local(n, offset)?;
                nodes.push(Node::Line(format!("{local} = (void 0);")));
            }
            "getvariable" if !self.avm2 => {
                let name = self.avm1_name(self.pop(state, offset)?, offset)?;
                let v = self.evaluate(name, nodes);
                state.stack.push(v);
            }
            "setvariable" | "definelocal" if !self.avm2 => {
                let value = self.pop_text(state, offset)?;
                let name = self.avm1_name(self.pop(state, offset)?, offset)?;
                if name == "this" || name == "super" {
                    return Err(fail(
                        "UNSUPPORTED_THIS_ASSIGNMENT",
                        "Special binding assignment requires P-code",
                        offset,
                    ));
                }
                if op == "definelocal"
                    && name == "_root"
                    && self.unit.avm1_flags.is_some_and(|flags| flags & 0x40 != 0)
                {
                    return Err(fail(
                        "UNSUPPORTED_REGISTER_ENVIRONMENT",
                        "A hoisted _root local would shadow the preloaded root binding",
                        offset,
                    ));
                }
                nodes.push(Node::Line(format!(
                    "{}{name} = {value};",
                    if op == "definelocal" { "var " } else { "" }
                )));
            }
            "definelocal2" if !self.avm2 => {
                let name = self.avm1_name(self.pop(state, offset)?, offset)?;
                if !identifier(&name) {
                    return Err(fail(
                        "UNSUPPORTED_VARIABLE_NAME",
                        "Invalid local declaration",
                        offset,
                    ));
                }
                if name == "_root" && self.unit.avm1_flags.is_some_and(|flags| flags & 0x40 != 0) {
                    return Err(fail(
                        "UNSUPPORTED_REGISTER_ENVIRONMENT",
                        "A hoisted _root local would shadow the preloaded root binding",
                        offset,
                    ));
                }
                nodes.push(Node::Line(format!("var {name};")));
            }
            "storeregister" if !self.avm2 => {
                let n = self.operands(i, 0)?;
                let register = self.avm1_register(n, offset)?;
                let value = state
                    .stack
                    .last()
                    .ok_or_else(|| fail("STACK_UNDERFLOW", "Empty register store", offset))?
                    .text()?
                    .to_string();
                nodes.push(Node::Line(format!("{register} = {value};")));
            }
            "getmember" | "setmember" if !self.avm2 => {
                let value = if op == "setmember" {
                    Some(self.pop_text(state, offset)?)
                } else {
                    None
                };
                let key = self.pop(state, offset)?;
                let receiver = self.pop_text(state, offset)?;
                let target = if let Some(key) = key.literal_string() {
                    member(&receiver, &key)
                } else {
                    format!("({receiver})[{}]", key.text()?)
                };
                if let Some(value) = value {
                    nodes.push(Node::Line(format!("{target} = {value};")));
                } else {
                    let v = self.evaluate(target, nodes);
                    state.stack.push(v);
                }
            }
            "getlex" | "findpropstrict" | "findproperty" => {
                let index = self.operands(i, 0)? as u32;
                let scopes = self
                    .captured_scope
                    .iter()
                    .flatten()
                    .cloned()
                    .chain(state.scope.iter().cloned())
                    .collect::<Vec<_>>();
                for scope in scopes.iter().rev() {
                    if matches!(scope, Value::CatchScope { .. })
                        && self.catch_property(index, scope, offset).is_ok()
                    {
                        let property = self.catch_property(index, scope, offset)?.unwrap();
                        if op == "getlex" {
                            let value = self.evaluate(property.expression, nodes);
                            state.stack.push(value);
                        } else {
                            state.stack.push(scope.clone());
                        }
                        return Ok(());
                    }
                    if let Value::Activation { object, body_index } = scope {
                        let context = self.method_context.unwrap();
                        if context
                            .symbols
                            .resolve(
                                self.unit.context.as_ref().unwrap(),
                                index,
                                TraitOwner::Activation(*body_index),
                            )
                            .is_some()
                        {
                            let property = self.activation_property(index, scope, offset)?.unwrap();
                            if op == "getlex" {
                                let value =
                                    self.evaluate(member(object, &property.expression), nodes);
                                state.stack.push(value);
                            } else {
                                state.stack.push(scope.clone());
                            }
                            return Ok(());
                        }
                    }
                }
                let name = self.property(i, None)?;
                if !name.qualified && !identifier(&name.expression) {
                    return Err(fail(
                        "UNSUPPORTED_LEXICAL_NAME",
                        "Lexical public name is not an identifier",
                        offset,
                    ));
                }
                if state.scope.iter().any(|v| {
                    v.text().ok() != Some(self.local0_expression.as_str())
                        && !matches!(v, Value::Activation { .. } | Value::CatchScope { .. })
                }) {
                    return Err(fail(
                        "UNPROVEN_SCOPE",
                        "Lexical lookup has a dynamic scope",
                        offset,
                    ));
                }
                let index = self.operands(i, 0)? as u32;
                let receiver = self.method_context.and_then(|context| {
                    let abc = self.unit.context.as_ref()?;
                    if let Some(owner) = context.owner {
                        if matches!(owner, TraitOwner::Instance(_) | TraitOwner::Class(_))
                            && context.symbols.resolve(abc, index, owner).is_some() { return Some(Some(self.local0_expression.clone())); }
                    }
                    let stable_scope = state.scope.is_empty() || matches!(context.owner, Some(TraitOwner::Script(_)))
                        || matches!(context.owner, Some(TraitOwner::Instance(n)) if abc.instances[n].flags & 1 != 0);
                    if stable_scope { context.symbols.resolve_script(abc,index).filter(|binding|matches!(binding.owner,TraitOwner::Script(n) if Some(n)==context.script_owner)).map(|_|None) } else { None }
                });
                let raw_public_name = self
                    .unit
                    .context
                    .as_ref()
                    .and_then(|abc| abc.public_property(index))
                    .filter(|p| p.namespace_uri.is_empty())
                    .and_then(|p| p.name);
                if ((!name.qualified && self.parameters.contains(&name.expression))
                    || raw_public_name
                        .as_ref()
                        .is_some_and(|name| self.parameters.contains(name)))
                    && !matches!(&receiver, Some(Some(_)))
                {
                    return Err(fail(
                        "SHADOWED_LEXICAL_BINDING",
                        "A parameter shadows the emitted lexical name; exact binding needs P-code",
                        offset,
                    ));
                }
                if op == "findproperty" && receiver.is_none() {
                    return Err(fail(
                        "UNPROVEN_LOOKUP_BINDING",
                        "Non-strict lookup of an undeclared name cannot become a throwing source identifier",
                        offset,
                    ));
                }
                if op == "getlex" {
                    let target = if let Some(receiver) = receiver {
                        self.target(
                            Value::BoundReceiver {
                                receiver,
                                property: name.clone(),
                            },
                            &name,
                            offset,
                        )?
                    } else {
                        name.expression.clone()
                    };
                    let v = self.evaluate(target, nodes);
                    state.stack.push(v);
                } else if let Some(receiver) = receiver {
                    state.stack.push(Value::BoundReceiver {
                        receiver,
                        property: name,
                    });
                } else {
                    state.stack.push(Value::LexicalReceiver(name));
                }
            }
            "callproplex" => {
                return Err(fail(
                    "UNPROVEN_CALL_RECEIVER",
                    "callproplex requires explicit null receiver reconstruction",
                    offset,
                ));
            }
            "getproperty" | "setproperty" | "initproperty" | "callproperty" | "callpropvoid"
            | "constructprop" => {
                let args = if matches!(op, "callproperty" | "callpropvoid" | "constructprop") {
                    let count = self.operands(i, 1)?;
                    self.args(state, count, offset, true)?
                } else {
                    vec![]
                };
                let value = if matches!(op, "setproperty" | "initproperty") {
                    Some(self.pop_text(state, offset)?)
                } else {
                    None
                };
                let activation_property = state
                    .stack
                    .last()
                    .map(|receiver| {
                        let index = self.operands(i, 0)? as u32;
                        if matches!(receiver, Value::CatchScope { .. }) {
                            self.catch_property(index, receiver, offset)
                        } else {
                            self.activation_property(index, receiver, offset)
                        }
                    })
                    .transpose()?
                    .flatten();
                let property = if let Some(property) = activation_property {
                    property
                } else {
                    self.property(i, Some(state))?
                };
                let receiver = self.pop(state, offset)?;
                if matches!(
                    receiver,
                    Value::Activation { .. } | Value::CatchScope { .. }
                ) && !matches!(op, "getproperty" | "setproperty" | "initproperty")
                {
                    return Err(fail(
                        "UNSUPPORTED_ACTIVATION_ESCAPE",
                        "Calling through an activation would expose its synthetic receiver",
                        offset,
                    ));
                }
                let value = if let (Some(value), Value::Activation { object, body_index }) =
                    (&value, &receiver)
                {
                    let context = self.method_context.unwrap();
                    let binding = context
                        .symbols
                        .resolve(
                            self.unit.context.as_ref().unwrap(),
                            self.operands(i, 0)? as u32,
                            TraitOwner::Activation(*body_index),
                        )
                        .unwrap();
                    let (_, ty) =
                        self.activation_slot(object, *body_index, binding.slot_id, offset)?;
                    Some(self.typed_assignment(&ty, value.clone(), nodes, offset)?)
                } else if let (
                    Some(_value),
                    Value::CatchScope {
                        exception_index, ..
                    },
                ) = (&value, &receiver)
                {
                    let _ = exception_index;
                    return Err(fail(
                        "UNSUPPORTED_EXCEPTION_FLOW",
                        "Catch variable property mutation requires exact const-slot semantics",
                        offset,
                    ));
                } else {
                    value
                };
                let target = self.target(receiver, &property, offset)?;
                if let Some(value) = value {
                    nodes.push(Node::Line(format!("{target} = {value};")));
                } else if op == "callpropvoid" {
                    nodes.push(Node::Line(format!("{target}({});", args.join(", "))));
                } else {
                    let expr = if op == "getproperty" {
                        target
                    } else if op == "constructprop" {
                        format!("new ({target})({})", args.join(", "))
                    } else {
                        format!("{target}({})", args.join(", "))
                    };
                    let v = self.evaluate(expr, nodes);
                    state.stack.push(v);
                }
            }
            "pushnamespace" => {
                let index = self.operands(i, 0)?;
                let uri = self
                    .unit
                    .context
                    .as_ref()
                    .and_then(|abc| abc.public_namespace_uri(index as u32))
                    .ok_or_else(|| {
                        fail(
                            "UNSUPPORTED_NAMESPACE",
                            "Only proven public namespace constants can qualify a runtime lookup",
                            offset,
                        )
                    })?
                    .to_owned();
                state.stack.push(Value::PublicNamespace(uri));
            }
            "constructsuper" => {
                let count = self.operands(i, 0)?;
                let args = self.args(state, count, offset, true)?;
                if self.pop_text(state, offset)? != "this" {
                    return Err(fail(
                        "UNPROVEN_CALL_RECEIVER",
                        "constructsuper receiver is not this",
                        offset,
                    ));
                }
                nodes.push(Node::Line(format!("super({});", args.join(", "))));
            }
            "construct" => {
                let count = self.operands(i, 0)?;
                let args = self.args(state, count, offset, true)?;
                let callee = self.pop_text(state, offset)?;
                let v = self.evaluate(format!("new ({callee})({})", args.join(", ")), nodes);
                state.stack.push(v);
            }
            "callfunction" | "callmethod" | "newobject" if !self.avm2 => {
                let callee = self.pop(state, offset)?;
                let receiver = if op == "callmethod" {
                    Some(self.pop_text(state, offset)?)
                } else {
                    None
                };
                let count = self.avm1_count(state, offset)?;
                let args = self.args(state, count, offset, false)?;
                let target = if let Some(receiver) = receiver {
                    match callee.literal_string() {
                        Some(s) if !s.is_empty() => member(&receiver, &s),
                        _ => {
                            return Err(fail(
                                "UNPROVEN_CALL_RECEIVER",
                                "Empty/dynamic AVM1 method name requires P-code",
                                offset,
                            ));
                        }
                    }
                } else {
                    self.avm1_name(callee, offset)?
                };
                let expr = format!(
                    "{}{}({})",
                    if op == "newobject" { "new " } else { "" },
                    target,
                    args.join(", ")
                );
                let v = self.evaluate(expr, nodes);
                state.stack.push(v);
            }
            "newarray" | "initarray" => {
                let count = if self.avm2 {
                    self.operands(i, 0)?
                } else {
                    self.avm1_count(state, offset)?
                };
                let args = self.args(state, count, offset, self.avm2)?;
                let v = self.evaluate(format!("[{}]", args.join(", ")), nodes);
                state.stack.push(v);
            }
            "newobject" | "initobject" => {
                let count = if self.avm2 {
                    self.operands(i, 0)?
                } else {
                    self.avm1_count(state, offset)?
                };
                if count > state.stack.len() / 2 {
                    return Err(fail(
                        "STACK_UNDERFLOW",
                        "Object size exceeds operand stack",
                        offset,
                    ));
                }
                let mut pairs = vec![];
                for _ in 0..count {
                    let value = self.pop_text(state, offset)?;
                    let key = self.pop(state, offset)?.literal_string().ok_or_else(|| {
                        fail(
                            "DYNAMIC_OBJECT_KEY",
                            "Object literal requires known string keys",
                            offset,
                        )
                    })?;
                    pairs.push(format!("{}: {value}", quoted(&key)));
                }
                if self.avm2 {
                    pairs.reverse();
                }
                let v = self.evaluate(format!("({{{}}})", pairs.join(", ")), nodes);
                state.stack.push(v);
            }
            "pushscope" => {
                let value = self.pop(state, offset)?;
                if !matches!(value, Value::Activation { .. } | Value::CatchScope { .. })
                    && value.text()? != self.local0_expression
                {
                    return Err(fail(
                        "UNPROVEN_SCOPE",
                        "Only lexical this scope is supported; dynamic scope requires P-code",
                        offset,
                    ));
                }
                state.scope.push(value);
            }
            "newactivation" => {
                let context = self.method_context.ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_ACTIVATION",
                        "Activation requires verified method context",
                        offset,
                    )
                })?;
                if self.activation_created
                    || self.graph.blocks.first().is_none_or(|b| {
                        !self.instructions[b.first..b.end]
                            .iter()
                            .any(|x| x.offset == offset)
                    })
                {
                    return Err(fail(
                        "UNSUPPORTED_ACTIVATION",
                        "Activation must be created once in the straight-line entry block",
                        offset,
                    ));
                }
                let abc = self.unit.context.as_ref().unwrap();
                if abc.methods[context.method_index].flags & 2 == 0 {
                    return Err(fail(
                        "UNSUPPORTED_ACTIVATION",
                        "newactivation requires NEED_ACTIVATION",
                        offset,
                    ));
                }
                let object = self.fresh();
                let traits = abc.bodies[context.body_index].traits.clone();
                let mut fields = vec![];
                for trait_info in traits {
                    let (field, ty) = self.activation_slot(
                        &object,
                        context.body_index,
                        trait_info.slot_id,
                        offset,
                    )?;
                    if !self.builtin_type(&ty) {
                        return Err(fail(
                            "UNSUPPORTED_ACTIVATION",
                            "Class slot defaults require proven class-loading and initialization effects",
                            offset,
                        ));
                    }
                    let default = if trait_info.value != 0 {
                        self.default_value(trait_info.value, trait_info.value_kind)?
                    } else {
                        match &ty {
                            TypeRef::Any => "(void 0)",
                            TypeRef::Named(name)
                                if name.namespace.uri.is_empty()
                                    && matches!(name.namespace.kind, 0x08 | 0x16)
                                    && !context
                                        .symbols
                                        .class_names
                                        .iter()
                                        .flatten()
                                        .any(|class| class.same_binding(name)) =>
                            {
                                match name.name.as_str() {
                                    "int" | "uint" => "0",
                                    "Number" => "(0 / 0)",
                                    "Boolean" => "false",
                                    _ => "null",
                                }
                            }
                            _ => "null",
                        }
                        .into()
                    };
                    fields.push(format!("{field}: {default}"));
                }
                nodes.push(Node::Line(format!(
                    "var {object} = {{{}}};",
                    fields.join(", ")
                )));
                state.stack.push(Value::Activation {
                    object,
                    body_index: context.body_index,
                });
                self.activation_created = true;
            }
            "newcatch" => {
                let index = self.operands(i, 0)?;
                let Some((expected, variable)) = &self.active_catch else {
                    return Err(fail(
                        "UNSUPPORTED_EXCEPTION_FLOW",
                        "newcatch outside its verified handler",
                        offset,
                    ));
                };
                if index != *expected {
                    return Err(fail(
                        "UNSUPPORTED_EXCEPTION_FLOW",
                        "Catch object belongs to a different handler",
                        offset,
                    ));
                }
                if self.catch_created {
                    return Err(fail(
                        "UNSUPPORTED_EXCEPTION_FLOW",
                        "Catch object must be created once",
                        offset,
                    ));
                }
                self.catch_created = true;
                state.stack.push(Value::CatchScope {
                    variable: variable.clone(),
                    exception_index: index,
                });
            }
            "getslot" | "setslot" => {
                let slot = self.operands(i, 0)? as u32;
                let value = if op == "setslot" {
                    Some(self.pop_text(state, offset)?)
                } else {
                    None
                };
                let receiver = self.pop(state, offset)?;
                let (target, ty) = if let Value::Activation { object, body_index } = receiver {
                    let (field, ty) = self.activation_slot(&object, body_index, slot, offset)?;
                    (member(&object, &field), ty)
                } else if let Value::CatchScope {
                    variable,
                    exception_index,
                } = receiver
                {
                    if slot != 1 {
                        return Err(fail(
                            "UNSUPPORTED_EXCEPTION_FLOW",
                            "Catch object has only its exception slot",
                            offset,
                        ));
                    }
                    if let Some(value) = &value {
                        if self.catch_initialized || value != &variable {
                            return Err(fail(
                                "UNSUPPORTED_EXCEPTION_FLOW",
                                "Catch initialization must store the original exception exactly once",
                                offset,
                            ));
                        }
                        self.catch_initialized = true;
                        return Ok(());
                    }
                    if !self.catch_initialized {
                        return Err(fail(
                            "UNSUPPORTED_EXCEPTION_FLOW",
                            "Catch slot read precedes initialization",
                            offset,
                        ));
                    }
                    (variable, self.catch_type(exception_index, offset)?)
                } else {
                    return Err(fail(
                        "UNPROVEN_SLOT_RECEIVER",
                        "Slot receiver is not a verified activation object",
                        offset,
                    ));
                };
                if let Some(value) = value {
                    let value = self.typed_assignment(&ty, value, nodes, offset)?;
                    nodes.push(Node::Line(format!("{target} = {value};")));
                } else {
                    let value = self.evaluate(target, nodes);
                    state.stack.push(value);
                }
            }
            "newfunction" => {
                let method = self.operands(i, 0)?;
                let abc = self
                    .unit
                    .context
                    .as_ref()
                    .ok_or_else(|| fail("UNSUPPORTED_CLOSURE", "Closure has no ABC", offset))?;
                let bodies = abc
                    .bodies
                    .iter()
                    .enumerate()
                    .filter(|(_, b)| b.method as usize == method)
                    .collect::<Vec<_>>();
                if bodies.len() != 1 {
                    return Err(fail(
                        "UNSUPPORTED_CLOSURE",
                        "Closure method does not have one body",
                        offset,
                    ));
                }
                let (body_index, body) = bodies[0];
                if body.init_scope_depth as usize != self.init_scope + state.scope.len() {
                    return Err(fail(
                        "UNSUPPORTED_CLOSURE",
                        "Captured scope depth does not match closure body",
                        offset,
                    ));
                }
                let mut captures = if self.captured_scope.is_empty() {
                    vec![None; self.init_scope]
                } else {
                    self.captured_scope.clone()
                };
                for value in &state.scope {
                    captures.push(Some(if matches!(value, Value::Activation { .. }) {
                        value.clone()
                    } else {
                        self.evaluate(value.text()?.to_string(), nodes)
                    }));
                }
                let mut nested = self.unit.clone();
                nested.code = body.code.clone();
                nested.byte_len = body.code.len();
                nested.method_index = Some(method);
                nested.body_index = Some(body_index);
                nested.id = format!(
                    "{}/method:{method}",
                    self.unit
                        .id
                        .split("/method:")
                        .next()
                        .unwrap_or(&self.unit.id)
                );
                nested.name = format!("{}_closure", self.prefix);
                nested.parameters = abc.methods[method]
                    .param_types
                    .iter()
                    .enumerate()
                    .map(|(n, _)| {
                        abc.methods[method]
                            .param_names
                            .get(n)
                            .and_then(|n| abc.string(*n))
                            .filter(|s| identifier(s))
                            .map(str::to_owned)
                            .unwrap_or_else(|| format!("arg{}", n + 1))
                    })
                    .collect();
                let result = crate::decompile_captured(
                    &nested,
                    self.started,
                    self.function_depth + 1,
                    &captures,
                    self.owned_document,
                );
                let source = result.source.ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_CLOSURE",
                        format!("Closure body requires P-code: {:?}", result.diagnostics),
                        offset,
                    )
                })?;
                let header = format!("function {}", nested.name);
                let anonymous = source.strip_prefix(&header).ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_CLOSURE",
                        "Closure needs additional namespace declarations",
                        offset,
                    )
                })?;
                let value = self.evaluate(format!("function{anonymous}"), nodes);
                state.stack.push(value);
            }
            "popscope" => {
                if state.scope.pop().is_none() {
                    return Err(fail(
                        "SCOPE_UNDERFLOW",
                        "Cannot pop the inherited scope stack",
                        offset,
                    ));
                }
            }
            "getscopeobject" => {
                let n = self.operands(i, 0)?;
                let value = state.scope.get(n).cloned().ok_or_else(|| {
                    fail("SCOPE_UNDERFLOW", "Scope index is not available", offset)
                })?;
                state.stack.push(value);
            }
            "getouterscope" => {
                let n = self.operands(i, 0)?;
                let value = self
                    .captured_scope
                    .get(n)
                    .and_then(|v| v.clone())
                    .ok_or_else(|| {
                        fail(
                            "UNPROVEN_SCOPE",
                            "Captured outer scope object requires a proven closure environment",
                            offset,
                        )
                    })?;
                state.stack.push(value);
            }
            "trace" if !self.avm2 => {
                let value = self.pop_text(state, offset)?;
                nodes.push(Node::Line(format!("trace({value});")));
            }
            "stop" | "play" | "nextframe" | "prevframe" | "stopsounds" if !self.avm2 => {
                let name = match op {
                    "nextframe" => "nextFrame",
                    "prevframe" => "prevFrame",
                    "stopsounds" => "stopAllSounds",
                    other => other,
                };
                nodes.push(Node::Line(format!("{name}();")));
            }
            "definefunction" | "definefunction2" if !self.avm2 => {
                let Some(body) = &i.body else {
                    return Err(fail(
                        "INVALID_FUNCTION",
                        "Function body is unavailable",
                        offset,
                    ));
                };
                let name = match i.operands.first() {
                    Some(Operand::Text(name)) if identifier(name) => name.clone(),
                    _ => {
                        return Err(fail(
                            "UNSUPPORTED_CLOSURE",
                            "Anonymous/dynamic function requires closure reconstruction",
                            offset,
                        ));
                    }
                };
                if i.parameters.iter().any(|p| !identifier(p)) {
                    return Err(fail(
                        "INVALID_FUNCTION",
                        "Function has unrepresentable parameter names",
                        offset,
                    ));
                }
                let mut nested = self.unit.clone();
                nested.code = body.clone();
                nested.name = name.clone();
                nested.kind = "function".into();
                nested.parameters = i.parameters.clone();
                nested.avm1_flags = None;
                nested.avm1_constants = self.constants.clone();
                nested.avm1_registers = vec![];
                nested.avm1_register_count = None;
                nested.byte_len = body.len();
                if op == "definefunction2" {
                    nested.avm1_register_count =
                        Some(u8::try_from(self.operands(i, 1)?).map_err(|_| {
                            fail(
                                "INVALID_FUNCTION",
                                "Function2 register count exceeds u8",
                                offset,
                            )
                        })?);
                    nested.avm1_flags =
                        Some(u16::try_from(self.operands(i, 2)?).map_err(|_| {
                            fail("INVALID_FUNCTION", "Function2 flags exceed u16", offset)
                        })?);
                    nested.avm1_registers = i
                        .operands
                        .iter()
                        .find_map(|operand| match operand {
                            Operand::Registers(registers) => Some(registers.clone()),
                            _ => None,
                        })
                        .ok_or_else(|| {
                            fail(
                                "INVALID_FUNCTION",
                                "Function2 parameter registers are unavailable",
                                offset,
                            )
                        })?;
                }
                let result = crate::decompile_inner(&nested, self.started, self.function_depth + 1);
                let source = result.source.ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_FUNCTION_BODY",
                        result
                            .diagnostics
                            .first()
                            .map(|d| d.message.clone())
                            .unwrap_or_else(|| "Nested function cannot be reconstructed".into()),
                        offset,
                    )
                })?;
                // DefineFunction installs its binding at this action, whereas a
                // source declaration would hoist it ahead of earlier actions.
                let mut lines: Vec<String> = source.lines().map(str::to_string).collect();
                if let Some(first) = lines.first_mut() {
                    // A named function expression would add a private name
                    // binding and turn later rebinding into different lookup
                    // semantics. The AVM1 name remains in the enclosing scope.
                    let anonymous =
                        first
                            .strip_prefix(&format!("function {name}"))
                            .ok_or_else(|| {
                                fail(
                                    "INVALID_FUNCTION",
                                    "Nested function header is not representable",
                                    offset,
                                )
                            })?;
                    *first = format!("{name} = function{anonymous}");
                }
                if let Some(last) = lines.last_mut() {
                    last.push(';');
                }
                nodes.extend(lines.into_iter().map(Node::Line));
            }
            "coercea" => {
                let value = self.pop(state, offset)?;
                state.stack.push(value);
            }
            "coerce" => {
                let index = self.operands(i, 0)? as u32;
                let context = self.method_context.ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_TYPE",
                        "Coercion requires verified method context",
                        offset,
                    )
                })?;
                let abc = self.unit.context.as_ref().unwrap();
                let ty = context.symbols.type_ref(abc, index).ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_TYPE",
                        "Coercion type identity is unresolved",
                        offset,
                    )
                })?;
                let proven = self.builtin_type(&ty)
                    || matches!(&ty,TypeRef::Named(name) if context.symbols.declares_type(name));
                if !proven {
                    return Err(fail(
                        "UNSUPPORTED_TYPE",
                        "Coercion type has no verified class or built-in binding",
                        offset,
                    ));
                }
                let ty_text = self.render_type(&ty, offset)?;
                let value = self.pop_text(state, offset)?;
                let name = self.fresh();
                // An AS3 typed assignment executes AVM2 coercion (including
                // TypeError), whereas `as` would silently return null.
                nodes.push(Node::Line(format!("var {name}:{ty_text} = {value};")));
                state.stack.push(if ty_text == "int" {
                    Value::Int32(name)
                } else {
                    Value::Expr(name)
                });
            }
            "nextname" | "nextvalue" if self.avm2 => {
                let Some((expected_offset, key)) = self.enumeration.clone() else {
                    return Err(fail(
                        "UNSUPPORTED_ENUMERATION",
                        format!("{op} requires a proven native enumeration iterator"),
                        offset,
                    ));
                };
                if expected_offset != offset {
                    return Err(fail(
                        "UNSUPPORTED_ENUMERATION",
                        format!("{op} does not match the canonical iterator"),
                        offset,
                    ));
                }
                self.pop_text(state, offset)?;
                self.pop_text(state, offset)?;
                let value = self.evaluate(key, nodes);
                state.stack.push(value);
            }
            "converts" | "convertd" | "converti" | "convertu" | "convertb" | "coerces"
            | "coerceb" | "coercei" | "coerceu" | "coerced" | "tonumber" | "tostring" => {
                let value = self.pop_text(state, offset)?;
                let ty = match op {
                    "converti" | "coercei" => "int",
                    "convertu" | "coerceu" => "uint",
                    "convertb" | "coerceb" => "Boolean",
                    "convertd" | "coerced" | "tonumber" => "Number",
                    _ => "String",
                };
                let expr = if op == "coerces" {
                    format!(
                        "({value} == null ? null : {})",
                        self.stringify(&value, offset)?
                    )
                } else {
                    match ty {
                        "int" => format!("({value} | 0)"),
                        "uint" => format!("({value} >>> 0)"),
                        "Boolean" => format!("!!({value})"),
                        "Number" => format!("+({value})"),
                        _ => self.stringify(&value, offset)?,
                    }
                };
                let v = self.evaluate(expr, nodes);
                state.stack.push(if ty == "int" {
                    Value::Int32(v.text()?.to_owned())
                } else {
                    v
                });
            }
            "astype" | "istype" => {
                let context = self.method_context.ok_or_else(|| {
                    fail(
                        "UNSUPPORTED_TYPE",
                        "Type test requires a verified method context",
                        offset,
                    )
                })?;
                let ty = context
                    .symbols
                    .type_ref(
                        self.unit.context.as_ref().unwrap(),
                        self.operands(i, 0)? as u32,
                    )
                    .ok_or_else(|| {
                        fail("UNSUPPORTED_TYPE", "Type-test QName is unresolved", offset)
                    })?;
                let ty = self.render_type(&ty, offset)?;
                let value = self.pop_text(state, offset)?;
                let v = self.evaluate(
                    format!(
                        "({value} {} {ty})",
                        if op == "astype" { "as" } else { "is" }
                    ),
                    nodes,
                );
                state.stack.push(v);
            }
            "increment" | "decrement" | "incrementi" | "decrementi" => {
                let value = self.pop_text(state, offset)?;
                let sign = if op.starts_with("inc") { "+" } else { "-" };
                let expr = if op.ends_with('i') {
                    format!("((({value} | 0) {sign} 1) | 0)")
                } else {
                    format!("(+({value}) {sign} 1)")
                };
                let v = self.evaluate(expr, nodes);
                state.stack.push(v);
            }
            "inclocal" | "declocal" | "inclocali" | "declocali" => {
                let n = self.operands(i, 0)?;
                if n == 0 {
                    return Err(fail(
                        "UNSUPPORTED_THIS_ASSIGNMENT",
                        "Incrementing receiver requires P-code",
                        offset,
                    ));
                }
                let local = self.local(n, offset)?;
                let sign = if op.starts_with("inc") { "+" } else { "-" };
                let expr = if op.ends_with('i') {
                    format!("((({local} | 0) {sign} 1) | 0)")
                } else {
                    format!("(+({local}) {sign} 1)")
                };
                nodes.push(Node::Line(format!("{local} = {expr};")));
            }
            "not" | "negate" | "negatei" | "bitnot" | "typeof" => {
                let value = self.pop_text(state, offset)?;
                let expr = match op {
                    "not" => format!("!({value})"),
                    "bitnot" => format!("~({value})"),
                    "typeof" => format!("typeof({value})"),
                    "negatei" => format!("(-({value} | 0) | 0)"),
                    _ => format!("-({value})"),
                };
                let v = self.evaluate(expr, nodes);
                state.stack.push(v);
            }
            op if binary_operator(op, self.avm2).is_some() => {
                let right = self.pop_text(state, offset)?;
                let left = self.pop_text(state, offset)?;
                let operator = binary_operator(op, self.avm2).unwrap();
                let expr = if op == "multiplyi" {
                    // AVM2 coerces the popped right operand first. Decompose
                    // into 16-bit limbs: double multiplication loses low bits
                    // above 2^53, while multiply_i wraps exact 32-bit integers.
                    let right = self
                        .evaluate(format!("({right} | 0)"), nodes)
                        .text()?
                        .to_string();
                    let left = self
                        .evaluate(format!("({left} | 0)"), nodes)
                        .text()?
                        .to_string();
                    format!(
                        "(((({left} & 65535) * ({right} & 65535)) + (((({left} >>> 16) * ({right} & 65535)) + (({left} & 65535) * ({right} >>> 16))) << 16)) | 0)"
                    )
                } else if self.avm2 && matches!(op, "subtract" | "multiply" | "divide" | "modulo") {
                    // Primitive conversion can call user code. Match AVM2's
                    // right-before-left numeric conversion, not JS's operator
                    // conversion order or evaluation of getter instructions.
                    let right = self
                        .evaluate(format!("+({right})"), nodes)
                        .text()?
                        .to_string();
                    let left = self
                        .evaluate(format!("+({left})"), nodes)
                        .text()?
                        .to_string();
                    format!("({left} {operator} {right})")
                } else if matches!(op, "addi" | "subtracti") {
                    format!("((({left} | 0) {operator} ({right} | 0)) | 0)")
                } else if !self.avm2
                    && matches!(
                        op,
                        "add" | "subtract" | "multiply" | "divide" | "equals" | "lessthan"
                    )
                {
                    format!("(+({left}) {operator} +({right}))")
                } else if !self.avm2 && op == "stringadd" {
                    format!(
                        "({} + {})",
                        self.stringify(&left, offset)?,
                        self.stringify(&right, offset)?
                    )
                } else if !self.avm2 && matches!(op, "and" | "or") {
                    format!("(!!({left}) {operator} !!({right}))")
                } else {
                    format!("({left} {operator} {right})")
                };
                let v = self.evaluate(expr, nodes);
                state.stack.push(
                    if self.avm2
                        && matches!(
                            op,
                            "addi"
                                | "subtracti"
                                | "multiplyi"
                                | "bitand"
                                | "bitor"
                                | "bitxor"
                                | "lshift"
                                | "rshift"
                        )
                    {
                        Value::Int32(v.text()?.to_owned())
                    } else {
                        v
                    },
                );
            }
            _ => {
                return Err(fail(
                    "UNSUPPORTED_INSTRUCTION",
                    format!(
                        "{} is outside the supported source subset; complete method retained as P-code",
                        i.name
                    ),
                    offset,
                ));
            }
        }
        Ok(())
    }

    fn region(
        &mut self,
        start: usize,
        stop: usize,
        mut state: State,
        omit_loop: Option<usize>,
        depth: usize,
    ) -> Result<(Vec<Node>, State)> {
        if depth > 128 {
            return Err(fail(
                "DECOMPILE_LIMIT",
                "Structured region nesting exceeds 128",
                0,
            ));
        }
        let mut current = start;
        let mut nodes = vec![];
        while current != stop && current != self.graph.exit {
            budget(
                self.started,
                self.instructions[self.graph.blocks[current].first].offset,
            )?;
            if self.claimed[current] {
                return Err(fail(
                    "UNSUPPORTED_CONTROL_FLOW",
                    "CFG region overlaps an already emitted region",
                    self.instructions[self.graph.blocks[current].first].offset,
                ));
            }
            if let Some(&body_start) = self.graph.rotated_loops.get(&current) {
                if !state.stack.is_empty() {
                    return Err(fail(
                        "UNSUPPORTED_LOOP_STACK",
                        "Rotated loop entry requires an empty stack",
                        self.instructions[self.graph.blocks[current].first].offset,
                    ));
                }
                self.claimed[current] = true;
                if let Some(kernel) = self.canonical_enumeration(body_start, current)? {
                    let suffix = if kernel.values { "value" } else { "key" };
                    let key = format!("{}_{suffix}{}", self.prefix, self.next_temp);
                    self.next_temp += 1;
                    let object = self.local(
                        kernel.object,
                        self.instructions[self.graph.blocks[current].first].offset,
                    )?;
                    let previous = self.enumeration.replace((kernel.next, key.clone()));
                    let lifted = self.region(body_start, current, state.clone(), None, depth + 1);
                    self.enumeration = previous;
                    let (body, end) = lifted?;
                    if end.terminated
                        || !end.stack.is_empty()
                        || end.scope != state.scope
                        || end.activation_locals != state.activation_locals
                    {
                        return Err(fail(
                            "INVALID_LOOP_STATE",
                            "Enumeration body does not preserve stack/scope",
                            kernel.next,
                        ));
                    }
                    nodes.push(if kernel.values {
                        Node::ForEach {
                            value: key,
                            object,
                            body,
                        }
                    } else {
                        Node::ForIn { key, object, body }
                    });
                    current += 1;
                    continue;
                }
                let header = self.lift(current, state.clone())?;
                let Ending::Branch(condition) = header.ending else {
                    return Err(fail(
                        "UNSUPPORTED_CONTROL_FLOW",
                        "Rotated loop predicate is not conditional",
                        0,
                    ));
                };
                if !header.state.stack.is_empty() || header.state.scope != state.scope {
                    return Err(fail(
                        "INVALID_LOOP_STATE",
                        "Rotated predicate must preserve empty stack and scope",
                        0,
                    ));
                }
                let (mut body, end) =
                    self.region(body_start, current, header.state.clone(), None, depth + 1)?;
                if end.terminated
                    || !end.stack.is_empty()
                    || end.scope != header.state.scope
                    || end.activation_locals != header.state.activation_locals
                {
                    return Err(fail(
                        "INVALID_LOOP_STATE",
                        "Rotated loop body does not preserve stack/scope",
                        0,
                    ));
                }
                let mut loop_nodes = header.nodes;
                loop_nodes.push(Node::If {
                    condition: format!("!({condition})"),
                    body: vec![Node::Line("break;".into())],
                    other: vec![],
                });
                loop_nodes.append(&mut body);
                nodes.push(Node::While {
                    condition: "true".into(),
                    body: loop_nodes,
                });
                state = header.state;
                current += 1;
                continue;
            }
            if let Some(&tail) = self
                .graph
                .loops
                .get(&current)
                .filter(|_| omit_loop != Some(current))
            {
                if !state.stack.is_empty() {
                    return Err(fail(
                        "UNSUPPORTED_LOOP_STACK",
                        "Loop entry requires an empty operand stack",
                        self.instructions[self.graph.blocks[current].first].offset,
                    ));
                }
                self.claimed[current] = true;
                let header = self.lift(current, state.clone())?;
                let Ending::Branch(condition) = header.ending else {
                    return Err(fail(
                        "UNSUPPORTED_CONTROL_FLOW",
                        "Loop header must end in a condition",
                        self.instructions[self.graph.blocks[current].first].offset,
                    ));
                };
                if !header.state.stack.is_empty() {
                    return Err(fail(
                        "UNSUPPORTED_LOOP_STACK",
                        "Loop condition leaves unresolved stack values",
                        self.instructions[self.graph.blocks[current].first].offset,
                    ));
                }
                let successors = &self.graph.blocks[current].successors;
                if successors.len() != 2 {
                    return Err(fail(
                        "UNSUPPORTED_CONTROL_FLOW",
                        "Loop condition must have body and exit edges",
                        0,
                    ));
                }
                let (body_start, exit, stay_condition) = if successors[0] > tail {
                    (successors[1], successors[0], format!("!({condition})"))
                } else if successors[1] > tail {
                    (successors[0], successors[1], condition)
                } else {
                    return Err(fail(
                        "UNSUPPORTED_CONTROL_FLOW",
                        "Loop has no single forward exit",
                        0,
                    ));
                };
                for b in current + 1..=tail {
                    if self.graph.blocks[b]
                        .successors
                        .iter()
                        .any(|s| *s != current && *s != exit && (*s <= current || *s > tail))
                    {
                        return Err(fail(
                            "UNSUPPORTED_CONTROL_FLOW",
                            "Loop body has a secondary exit outside its demonstrated boundary",
                            self.instructions[self.graph.blocks[b].first].offset,
                        ));
                    }
                }
                self.loop_frames.push(LoopFrame {
                    head: current,
                    exit,
                    scope: header.state.scope.clone(),
                    activation_locals: header.state.activation_locals.clone(),
                });
                let lifted_body = self.region(
                    body_start,
                    current,
                    header.state.clone(),
                    Some(current),
                    depth + 1,
                );
                self.loop_frames.pop();
                let (mut body, final_state) = lifted_body?;
                if !final_state.stack.is_empty()
                    || final_state.scope != state.scope
                    || final_state.activation_locals != header.state.activation_locals
                {
                    return Err(fail(
                        "INVALID_LOOP_STATE",
                        "Loop back edge does not preserve stack/scope state",
                        0,
                    ));
                }
                if header.nodes.is_empty() {
                    nodes.push(Node::While {
                        condition: stay_condition,
                        body,
                    });
                } else {
                    let mut loop_nodes = header.nodes;
                    loop_nodes.push(Node::If {
                        condition: format!("!({stay_condition})"),
                        body: vec![Node::Line("break;".into())],
                        other: vec![],
                    });
                    loop_nodes.append(&mut body);
                    nodes.push(Node::While {
                        condition: "true".into(),
                        body: loop_nodes,
                    });
                }
                state = header.state;
                current = exit;
                continue;
            }
            self.claimed[current] = true;
            let lifted = self.lift(current, state)?;
            nodes.extend(lifted.nodes);
            state = lifted.state;
            match lifted.ending {
                Ending::Return => return Ok((nodes, state)),
                Ending::Next | Ending::Jump => {
                    let next = self.graph.blocks[current]
                        .successors
                        .first()
                        .copied()
                        .unwrap_or(self.graph.exit);
                    if let Some(frame) = self.loop_frames.last() {
                        if (next == frame.exit || next == frame.head) && next != stop {
                            if !state.stack.is_empty()
                                || state.scope != frame.scope
                                || state.activation_locals != frame.activation_locals
                            {
                                return Err(fail(
                                    "INVALID_LOOP_STATE",
                                    "Loop completion changes stack or scope",
                                    self.instructions[self.graph.blocks[current].first].offset,
                                ));
                            }
                            nodes.push(Node::Line(
                                if next == frame.exit {
                                    "break;"
                                } else {
                                    "continue;"
                                }
                                .into(),
                            ));
                            state.terminated = true;
                            return Ok((nodes, state));
                        }
                    }
                    if next <= current && next != stop {
                        return Err(fail(
                            "UNSUPPORTED_CONTROL_FLOW",
                            "Unstructured backward jump",
                            self.instructions[self.graph.blocks[current].first].offset,
                        ));
                    }
                    current = next;
                }
                Ending::Switch(expression) => {
                    let instruction = &self.instructions[self.graph.blocks[current].end - 1];
                    let mut groups: Vec<(usize, Vec<Option<usize>>)> = vec![];
                    for (index, target) in instruction.branch_targets.iter().enumerate() {
                        let block = if *target == self.unit.code.len() {
                            self.graph.exit
                        } else {
                            self.graph
                                .blocks
                                .iter()
                                .position(|block| self.instructions[block.first].offset == *target)
                                .ok_or_else(|| {
                                    fail(
                                        "UNSUPPORTED_SWITCH_FLOW",
                                        "Switch arm must target a real forward block",
                                        instruction.offset,
                                    )
                                })?
                        };
                        if block <= current && block != self.graph.exit {
                            return Err(fail(
                                "UNSUPPORTED_SWITCH_FLOW",
                                "Backward switch edges need additional structuring",
                                instruction.offset,
                            ));
                        }
                        let label = if index == 0 { None } else { Some(index - 1) };
                        if let Some((_, labels)) =
                            groups.iter_mut().find(|(target, _)| *target == block)
                        {
                            labels.push(label);
                        } else {
                            groups.push((block, vec![label]));
                        }
                    }

                    let targets = groups.iter().map(|(block, _)| *block).collect::<Vec<_>>();
                    let start = targets.iter().copied().max().unwrap_or(current);
                    let join = (start..=self.graph.exit)
                        .find(|candidate| {
                            targets.iter().all(|target| {
                                self.graph.postdom[*target][candidate / 64]
                                    & (1_u64 << (candidate % 64))
                                    != 0
                            })
                        })
                        .ok_or_else(|| {
                            fail(
                                "UNSUPPORTED_SWITCH_FLOW",
                                "Switch arms have no finite common postdominator",
                                instruction.offset,
                            )
                        })?;
                    if stop != self.graph.exit && stop > current && join > stop {
                        return Err(fail(
                            "UNSUPPORTED_SWITCH_FLOW",
                            "Switch escapes its structured region",
                            instruction.offset,
                        ));
                    }

                    groups.sort_by_key(|(block, _)| *block);
                    let mut arms = vec![];
                    let mut live_arms = vec![];
                    for (group_index, (block, labels)) in groups.iter().enumerate() {
                        let next_arm = groups.get(group_index + 1).map(|(block, _)| *block);
                        let fallthrough = next_arm.is_some_and(|next| {
                            next < join
                                && self.graph.postdom[*block][next / 64] & (1_u64 << (next % 64))
                                    != 0
                        });
                        let stop_arm = if fallthrough { next_arm.unwrap() } else { join };
                        let (mut body, end) =
                            self.region(*block, stop_arm, state.clone(), omit_loop, depth + 1)?;
                        if fallthrough {
                            if end.terminated
                                || !end.stack.is_empty()
                                || !state.stack.is_empty()
                                || end.scope != state.scope
                                || end.activation_locals != state.activation_locals
                            {
                                return Err(fail(
                                    "UNSUPPORTED_SWITCH_FLOW",
                                    "Fallthrough must preserve an empty operand stack and identical lexical scope",
                                    instruction.offset,
                                ));
                            }
                        } else if !end.terminated {
                            body.push(Node::Line("break;".into()));
                            live_arms.push((arms.len(), end));
                        }
                        arms.push((labels.clone(), body));
                    }

                    if let Some((_, first)) = live_arms.first() {
                        let mut joined = first.clone();
                        for (_, other) in live_arms.iter().skip(1) {
                            if joined.stack.len() != other.stack.len() {
                                return Err(fail(
                                    "INCONSISTENT_STACK_JOIN",
                                    "Switch operand stack heights disagree",
                                    instruction.offset,
                                ));
                            }
                            if joined.scope != other.scope
                                || joined.activation_locals != other.activation_locals
                            {
                                return Err(fail(
                                    "INCONSISTENT_SCOPE_JOIN",
                                    "Switch scope stacks disagree",
                                    instruction.offset,
                                ));
                            }
                        }
                        for slot in 0..joined.stack.len() {
                            if live_arms
                                .iter()
                                .skip(1)
                                .any(|(_, arm)| joined.stack[slot] != arm.stack[slot])
                            {
                                let value = self.fresh();
                                nodes.push(Node::Line(format!("var {value};")));
                                for (arm_index, arm_state) in &live_arms {
                                    let arm = &mut arms[*arm_index].1;
                                    let break_index = arm.len().saturating_sub(1);
                                    arm.insert(
                                        break_index,
                                        Node::Line(format!(
                                            "{value} = {};",
                                            arm_state.stack[slot].text()?
                                        )),
                                    );
                                }
                                joined.stack[slot] = if live_arms
                                    .iter()
                                    .all(|(_, arm)| matches!(arm.stack[slot], Value::Int32(_)))
                                {
                                    Value::Int32(value)
                                } else {
                                    Value::Expr(value)
                                };
                            }
                        }
                        state = joined;
                    } else {
                        state.terminated = true;
                    }
                    nodes.push(Node::Switch { expression, arms });
                    if state.terminated {
                        return Ok((nodes, state));
                    }
                    current = join;
                }
                Ending::Branch(condition) => {
                    let successors = &self.graph.blocks[current].successors;
                    if let Some(frame) = self.loop_frames.last().cloned() {
                        if successors.len() == 2 {
                            let completion = |target| {
                                if target == frame.exit {
                                    Some("break;")
                                } else if target == frame.head {
                                    Some("continue;")
                                } else {
                                    None
                                }
                            };
                            let first = completion(successors[0]);
                            let second = completion(successors[1]);
                            if first.is_some() || second.is_some() {
                                if !state.stack.is_empty()
                                    || state.scope != frame.scope
                                    || state.activation_locals != frame.activation_locals
                                {
                                    return Err(fail(
                                        "INVALID_LOOP_STATE",
                                        "Conditional loop completion changes stack or scope",
                                        self.instructions[self.graph.blocks[current].first].offset,
                                    ));
                                }
                                let (condition, body, other, next) = if let Some(first) = first {
                                    (
                                        condition,
                                        vec![Node::Line(first.into())],
                                        second
                                            .map(|s| vec![Node::Line(s.into())])
                                            .unwrap_or_default(),
                                        successors[1],
                                    )
                                } else {
                                    (
                                        format!("!({condition})"),
                                        vec![Node::Line(second.unwrap().into())],
                                        vec![],
                                        successors[0],
                                    )
                                };
                                nodes.push(Node::If {
                                    condition,
                                    body,
                                    other,
                                });
                                if first.is_some() && second.is_some() {
                                    state.terminated = true;
                                    return Ok((nodes, state));
                                }
                                current = next;
                                continue;
                            }
                        }
                    }
                    if successors.len() == 1 {
                        current = successors[0];
                        continue;
                    }
                    let (left, right) = (successors[0], successors[1]);
                    if left <= current || right <= current {
                        return Err(fail(
                            "UNSUPPORTED_CONTROL_FLOW",
                            "Conditional backward edge requires another loop shape",
                            0,
                        ));
                    }
                    let join = self.graph.common_join(left, right).ok_or_else(|| {
                        fail(
                            "UNSUPPORTED_CONTROL_FLOW",
                            "Branch has no finite common postdominator",
                            0,
                        )
                    })?;
                    if stop != self.graph.exit && stop > current && join > stop {
                        return Err(fail(
                            "UNSUPPORTED_CONTROL_FLOW",
                            "Branch escapes its structured region",
                            0,
                        ));
                    }
                    let (mut then_nodes, then_state) =
                        self.region(left, join, state.clone(), omit_loop, depth + 1)?;
                    let (mut else_nodes, else_state) =
                        self.region(right, join, state.clone(), omit_loop, depth + 1)?;
                    if then_state.terminated && else_state.terminated {
                        state.terminated = true;
                    } else if then_state.terminated {
                        state = else_state;
                    } else if else_state.terminated {
                        state = then_state;
                    } else {
                        if then_state.stack.len() != else_state.stack.len() {
                            return Err(fail(
                                "INCONSISTENT_STACK_JOIN",
                                "Branch operand stack heights disagree",
                                0,
                            ));
                        }
                        if then_state.scope != else_state.scope
                            || then_state.activation_locals != else_state.activation_locals
                        {
                            return Err(fail(
                                "INCONSISTENT_SCOPE_JOIN",
                                "Branch scope stacks disagree",
                                0,
                            ));
                        }
                        state = then_state.clone();
                        for n in 0..state.stack.len() {
                            if then_state.stack[n] != else_state.stack[n] {
                                let value = self.fresh();
                                nodes.push(Node::Line(format!("var {value};")));
                                then_nodes.push(Node::Line(format!(
                                    "{value} = {};",
                                    then_state.stack[n].text()?
                                )));
                                else_nodes.push(Node::Line(format!(
                                    "{value} = {};",
                                    else_state.stack[n].text()?
                                )));
                                state.stack[n] = if matches!(
                                    (&then_state.stack[n], &else_state.stack[n]),
                                    (Value::Int32(_), Value::Int32(_))
                                ) {
                                    Value::Int32(value)
                                } else {
                                    Value::Expr(value)
                                };
                            }
                        }
                    }
                    nodes.push(Node::If {
                        condition,
                        body: then_nodes,
                        other: else_nodes,
                    });
                    if state.terminated {
                        return Ok((nodes, state));
                    }
                    current = join;
                }
            }
        }
        Ok((nodes, state))
    }

    /// Admit the compiler's for-in/for-each kernel only when its object/index
    /// registers are private iterator state: one nextname/nextvalue pair, no
    /// mutation or exposed reads in the body, and both registers killed after exit.
    /// Native ActionScript enumeration reproduces hasnext2's prototype walk;
    /// approximating that walk with Object.keys would change its semantics.
    fn canonical_enumeration(
        &self,
        body_start: usize,
        predicate: usize,
    ) -> Result<Option<EnumerationKernel>> {
        let block = &self.graph.blocks[predicate];
        let test = self.instructions[block.first..block.end]
            .iter()
            .filter(|i| !ignorable_debug(&mnemonic(&i.name)))
            .collect::<Vec<_>>();
        if !test.iter().any(|i| mnemonic(&i.name) == "hasnext2") {
            return Ok(None);
        }
        let unsupported = || {
            fail(
                "UNSUPPORTED_ENUMERATION",
                "Iterator registers are exposed, mutated, or not a canonical enumeration kernel",
                self.instructions[block.first].offset,
            )
        };
        if test.len() != 2
            || mnemonic(&test[0].name) != "hasnext2"
            || mnemonic(&test[1].name) != "iftrue"
        {
            return Err(unsupported());
        }
        let object = self.operands(test[0], 0)?;
        let index = self.operands(test[0], 1)?;
        if object == index
            || object <= self.parameters.len()
            || index <= self.parameters.len()
            || self
                .environment_local
                .as_ref()
                .is_some_and(|(n, _)| *n == object || *n == index)
        {
            return Err(unsupported());
        }
        // A source for-in starts at index zero. Prove that value on the sole
        // external entry edge instead of assuming every hasnext2 is a fresh
        // enumeration (hand-written ABC can resume at a nonzero index).
        let entries = self
            .graph
            .blocks
            .iter()
            .enumerate()
            .filter(|(n, b)| *n < body_start && b.successors.contains(&predicate))
            .collect::<Vec<_>>();
        if entries.len() != 1 {
            return Err(unsupported());
        }
        let entry = entries[0].1;
        let setup = self.instructions[entry.first..entry.end]
            .iter()
            .filter(|i| !ignorable_debug(&mnemonic(&i.name)))
            .collect::<Vec<_>>();
        let mut last_write = None;
        for (position, instruction) in setup.iter().enumerate() {
            let op = mnemonic(&instruction.name);
            let local = if op.starts_with("setlocal") {
                Some(register_index(&op, "setlocal", instruction, self)?)
            } else if matches!(
                op.as_str(),
                "kill" | "inclocal" | "declocal" | "inclocali" | "declocali"
            ) {
                Some(self.operands(instruction, 0)?)
            } else {
                None
            };
            if local == Some(index) {
                last_write = Some(position);
            }
        }
        let Some(position) = last_write.filter(|position| *position > 0) else {
            return Err(unsupported());
        };
        if !mnemonic(&setup[position].name).starts_with("setlocal")
            || !matches!(
                mnemonic(&setup[position - 1].name).as_str(),
                "pushbyte" | "pushshort"
            )
            || !matches!(
                setup[position - 1].operands.as_slice(),
                [Operand::Signed(0)]
            )
        {
            return Err(unsupported());
        }
        let first = self.graph.blocks[body_start].first;
        let end = self.graph.blocks[predicate].first;
        let body = self.instructions[first..end]
            .iter()
            .filter(|i| !ignorable_debug(&mnemonic(&i.name)))
            .collect::<Vec<_>>();
        if body.len() < 3 || !matches!(mnemonic(&body[2].name).as_str(), "nextname" | "nextvalue") {
            return Err(unsupported());
        }
        for (instruction, expected) in [(&body[0], object), (&body[1], index)] {
            let op = mnemonic(&instruction.name);
            if !op.starts_with("getlocal")
                || register_index(&op, "getlocal", instruction, self)? != expected
            {
                return Err(unsupported());
            }
        }
        for instruction in body.iter().skip(3) {
            let op = mnemonic(&instruction.name);
            if op == "nextname" || op == "nextvalue" || op == "hasnext" || op == "hasnext2" {
                return Err(unsupported());
            }
            let local = if op.starts_with("getlocal") {
                Some(register_index(&op, "getlocal", instruction, self)?)
            } else if op.starts_with("setlocal") {
                Some(register_index(&op, "setlocal", instruction, self)?)
            } else if matches!(
                op.as_str(),
                "kill" | "inclocal" | "declocal" | "inclocali" | "declocali"
            ) {
                Some(self.operands(instruction, 0)?)
            } else {
                None
            };
            if local.is_some_and(|n| n == object || n == index) {
                return Err(unsupported());
            }
        }
        let Some(exit) = self.graph.blocks.get(predicate + 1) else {
            return Err(unsupported());
        };
        let kills = self.instructions[exit.first..exit.end]
            .iter()
            .filter(|i| !ignorable_debug(&mnemonic(&i.name)))
            .take(2)
            .collect::<Vec<_>>();
        if kills.len() != 2 || kills.iter().any(|i| mnemonic(&i.name) != "kill") {
            return Err(unsupported());
        }
        let killed = [self.operands(kills[0], 0)?, self.operands(kills[1], 0)?];
        if killed != [object, index] && killed != [index, object] {
            return Err(unsupported());
        }
        Ok(Some(EnumerationKernel {
            object,
            next: body[2].offset,
            values: mnemonic(&body[2].name) == "nextvalue",
        }))
    }
}

fn ignorable_debug(op: &str) -> bool {
    matches!(op, "debug" | "debugline" | "debugfile" | "label" | "nop")
}

fn register_index(op: &str, prefix: &str, i: &Instruction, builder: &Builder<'_>) -> Result<usize> {
    if op == prefix {
        builder.operands(i, 0)
    } else {
        op.strip_prefix(prefix)
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| fail("INVALID_OPERAND", "Bad local register mnemonic", i.offset))
    }
}
fn may_bind_string(unit: &ScriptUnit, instructions: &[Instruction]) -> bool {
    if unit.parameters.iter().any(|p| p == "String")
        || unit.name.rsplit([':', '.', '/']).next() == Some("String")
    {
        return true;
    }
    if let Some(abc) = &unit.context {
        // Private/custom namespace members may also shadow an unqualified
        // lexical identifier. Do not assume visibility proves the intrinsic.
        let named_string = |index: u32| {
            abc.multinames
                .get(index as usize)
                .and_then(|m| abc.string(m.name))
                == Some("String")
        };
        if abc.instances.iter().any(|class| {
            named_string(class.name) || class.traits.iter().any(|t| named_string(t.name))
        }) || abc
            .classes
            .iter()
            .any(|class| class.traits.iter().any(|t| named_string(t.name)))
            || abc
                .scripts
                .iter()
                .any(|script| script.traits.iter().any(|t| named_string(t.name)))
        {
            return true;
        }
    }
    if unit.language == "avm2" {
        return false;
    }
    // AVM1 variables can be named through the operand stack. Conservatively
    // treat a String token as a possible binding rather than guess a dataflow.
    unit.avm1_constants.iter().any(|s| s == "String")
        || instructions.iter().any(|i| {
            i.operands.iter().any(|o| match o {
                Operand::Text(s) => s == "String",
                Operand::Values(values) => values
                    .iter()
                    .any(|s| serde_json::from_str::<String>(s).ok().as_deref() == Some("String")),
                _ => false,
            })
        })
}

fn valid_literal(text: &str) -> bool {
    matches!(text, "true" | "false" | "null" | "undefined")
        || serde_json::from_str::<String>(text).is_ok()
        || text.parse::<f64>().is_ok_and(|v| v.is_finite())
}
fn primitive_key(value: &Value) -> Option<String> {
    if let Value::Int32(expression) = value {
        return Some(expression.clone());
    }
    let Value::Literal(text) = value else {
        return None;
    };
    if serde_json::from_str::<String>(text).is_ok()
        || matches!(
            text.as_str(),
            "true" | "false" | "null" | "(void 0)" | "(0 / 0)"
        )
        || text.parse::<f64>().is_ok_and(|number| number.is_finite())
    {
        Some(text.clone())
    } else {
        None
    }
}
fn lookup_order_safe(op: &str, i: &Instruction, state: &State) -> bool {
    if matches!(
        op,
        "pushbyte"
            | "pushshort"
            | "pushint"
            | "pushuint"
            | "pushdouble"
            | "pushstring"
            | "pushtrue"
            | "pushfalse"
            | "pushnull"
            | "pushundefined"
            | "pushnan"
            | "pushnamespace"
            | "nop"
            | "label"
            | "debug"
            | "debugline"
            | "debugfile"
            | "coercea"
            | "swap"
    ) || op.starts_with("getlocal")
    {
        return true;
    }
    let receiver_index = match op {
        "getproperty" => state.stack.len().checked_sub(1),
        "setproperty" | "initproperty" => state.stack.len().checked_sub(2),
        "callproperty" | "callpropvoid" | "constructprop" => match i.operands.get(1) {
            Some(Operand::Unsigned(n)) => state.stack.len().checked_sub(*n as usize + 1),
            _ => None,
        },
        _ => None,
    };
    receiver_index.is_some_and(|n| matches!(state.stack[n], Value::LexicalReceiver(_)))
        && state
            .stack
            .iter()
            .filter(|v| matches!(v, Value::LexicalReceiver(_)))
            .count()
            == 1
}
fn binary_operator(op: &str, avm2: bool) -> Option<&'static str> {
    Some(match op {
        "add" | "add2" | "addi" | "stringadd" => "+",
        "subtract" | "subtracti" => "-",
        "multiply" | "multiplyi" => "*",
        "divide" => "/",
        "modulo" => "%",
        "bitand" => "&",
        "bitor" => "|",
        "bitxor" => "^",
        "lshift" | "bitlshift" => "<<",
        "rshift" | "bitrshift" => ">>",
        "urshift" | "biturshift" => ">>>",
        "equals" | "equals2" => "==",
        "strictequals" => "===",
        "lessthan" | "less2" => "<",
        "lessequals" => "<=",
        "greaterthan" | "greater" => ">",
        "greaterequals" => ">=",
        "instanceof" => "instanceof",
        "in" if avm2 => "in",
        "and" if !avm2 => "&&",
        "or" if !avm2 => "||",
        _ => return None,
    })
}
