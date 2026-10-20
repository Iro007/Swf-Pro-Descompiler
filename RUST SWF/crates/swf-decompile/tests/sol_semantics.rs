use std::process::Command;
use std::sync::Arc;
use swf_avm::{Abc, ScriptUnit, disassemble};
use swf_decompile::decompile;

fn action(opcode: u8, payload: &[u8]) -> Vec<u8> {
    let mut code = vec![opcode];
    if opcode >= 0x80 {
        code.extend((payload.len() as u16).to_le_bytes());
        code.extend(payload);
    }
    code
}

fn string(value: &str) -> Vec<u8> {
    let mut payload = vec![0];
    payload.extend(value.as_bytes());
    payload.push(0);
    action(0x96, &payload)
}

fn integer(value: i32) -> Vec<u8> {
    let mut payload = vec![7];
    payload.extend(value.to_le_bytes());
    action(0x96, &payload)
}

fn register(index: u8) -> Vec<u8> {
    action(0x96, &[4, index])
}

fn function2(name: &str, parameters: &[(&str, u8)], count: u8, flags: u16, body: &[u8]) -> Vec<u8> {
    let mut payload = name.as_bytes().to_vec();
    payload.push(0);
    payload.extend((parameters.len() as u16).to_le_bytes());
    payload.push(count);
    payload.extend(flags.to_le_bytes());
    for (parameter, register) in parameters {
        payload.push(*register);
        payload.extend(parameter.as_bytes());
        payload.push(0);
    }
    payload.extend((body.len() as u16).to_le_bytes());
    let mut code = action(0x8e, &payload);
    code.extend(body);
    code
}

fn avm1(code: &[u8]) -> ScriptUnit {
    ScriptUnit {
        id: "sol-parent".into(),
        name: "frame".into(),
        language: "avm1".into(),
        kind: "frame".into(),
        code: code.to_vec(),
        byte_len: code.len(),
        ..Default::default()
    }
}

fn child(body: &[u8], parameters: &[(&str, u8)], count: Option<u8>, flags: u16) -> ScriptUnit {
    let mut unit = avm1(body);
    unit.name = "named".into();
    unit.kind = "function".into();
    unit.parameters = parameters.iter().map(|(name, _)| (*name).into()).collect();
    unit.avm1_registers = parameters.iter().map(|(_, register)| *register).collect();
    unit.avm1_flags = Some(flags);
    unit.avm1_register_count = count;
    unit
}

fn source(unit: &ScriptUnit) -> String {
    let result = decompile(unit);
    assert_eq!(
        result.state, "structured",
        "{:?}\n{}",
        result.diagnostics, result.pcode
    );
    result.source.expect("structured source")
}

fn node(script: String) -> serde_json::Value {
    let result = Command::new("node")
        .args(["-e", &script])
        .output()
        .expect("Node semantic fixture");
    assert!(
        result.status.success(),
        "{}\n{script}",
        String::from_utf8_lossy(&result.stderr)
    );
    serde_json::from_slice(&result.stdout).expect("fixture must emit JSON")
}

fn assert_exact_fallback(unit: &ScriptUnit) {
    let result = decompile(unit);
    assert_eq!(result.state, "pcode_only", "{:?}", result.diagnostics);
    assert!(result.source.is_none());
    assert_eq!(result.pcode, disassemble(unit).pcode);
    assert_eq!(result.unit_id, unit.id);
}

#[test]
fn parent_function2_definition_and_bytecode_call_preserve_argument_binding() {
    let mut body = register(2);
    body.push(0x3e);
    let mut code = function2("named", &[("value", 2)], 2, 1, &body);
    code.extend(integer(37));
    code.extend(integer(1));
    code.extend(string("named"));
    code.extend([0x3d, 0x26, 0]); // CallFunction; Trace; End.
    let text = source(&avm1(&code));
    assert_eq!(
        node(format!(
            "var named;let effects=[];function trace(x){{effects.push(x);}}\n{text}\nconsole.log(JSON.stringify(effects));"
        )),
        serde_json::json!([37])
    );
}

#[test]
fn parent_function2_does_not_hoist_or_add_a_private_function_name() {
    let mut body = string("named");
    body.extend([0x1c, 0x3e]);
    let mut code = string("named");
    code.extend([0x1c, 0x44, 0x26]);
    code.extend(function2("named", &[], 0, 0, &body));
    code.push(0);
    let text = source(&avm1(&code));
    assert_eq!(
        node(format!(
            "var named;let effects=[];function trace(x){{effects.push(x);}}\n{text}\nlet saved=named;named=91;console.log(JSON.stringify([effects,saved()]));"
        )),
        serde_json::json!([["undefined"], 91])
    );
}

#[test]
fn parent_function2_register_formal_does_not_shadow_outer_variable() {
    let mut body = string("value");
    body.extend([0x1c, 0x3e]);
    let mut code = function2("named", &[("value", 1)], 1, 0, &body);
    code.push(0);
    let text = source(&avm1(&code));
    assert_eq!(
        node(format!(
            "var named;let value=91;\n{text}\nconsole.log(JSON.stringify(named(37)));"
        )),
        91
    );
}

#[test]
fn parent_function2_preloads_receiver_and_root_before_observable_body_actions() {
    let mut body = register(1);
    body.push(0x26);
    body.extend(register(2));
    body.extend([0x26, 0]);
    let mut code = function2("named", &[("argument", 1)], 2, 0x41, &body);
    code.push(0);
    let text = source(&avm1(&code));
    // A preload occupying a parameter register replaces that argument before
    // the first body action; the call receiver is observed before root.
    assert_eq!(
        node(format!(
            "var named;let _root={{kind:'root'}};let effects=[];function trace(x){{effects.push(x.kind);}}\n{text}\nnamed.call({{kind:'receiver'}},{{kind:'argument'}});console.log(JSON.stringify(effects));"
        )),
        serde_json::json!(["receiver", "root"])
    );
}

#[test]
fn parent_function2_preload_root_cannot_be_shadowed_by_hoisted_local() {
    let mut body = string("_root");
    body.extend(string("local"));
    body.push(0x3c); // DefineLocal.
    body.extend(register(1));
    body.push(0x3e);
    let mut code = function2("named", &[], 1, 0x40, &body);
    code.push(0);
    let unit = avm1(&code);
    // Until root is represented independently of activation locals, refuse
    // source instead of reading the hoisted local before its initialization.
    assert_exact_fallback(&unit);
    assert_exact_fallback(&child(&body, &[], Some(1), 0x40));
}

#[test]
fn parent_function2_register_count_bounds_preloads_and_body_registers() {
    for (flags, body) in [
        (0x41, &[0][..]),
        (0, &[0x96, 2, 0, 4, 2, 0x3e][..]),
        (0, &[0x96, 5, 0, 7, 9, 0, 0, 0, 0x87, 1, 0, 2, 0x17, 0][..]),
    ] {
        assert_exact_fallback(&avm1(&function2("named", &[], 1, flags, body)));
        assert_exact_fallback(&child(body, &[], Some(1), flags));
    }
}

#[test]
fn function2_register_inputs_require_declared_count_metadata() {
    let mut body = register(1);
    body.push(0x3e);
    assert_exact_fallback(&child(&body, &[("value", 1)], None, 0));
    assert_exact_fallback(&child(&body, &[], None, 1));
}

fn method(code: &[u8], parameters: &[&str]) -> ScriptUnit {
    let mut bytes = vec![
        16,
        0,
        46,
        0,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        parameters.len() as u8,
        0,
    ];
    bytes.extend(vec![0; parameters.len()]);
    bytes.extend([
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        8,
        (parameters.len() + 1) as u8,
        0,
        3,
        code.len() as u8,
    ]);
    bytes.extend(code);
    bytes.extend([0, 0]);
    ScriptUnit {
        id: "sol-switch".into(),
        name: "sample".into(),
        language: "avm2".into(),
        kind: "method".into(),
        method_index: Some(0),
        body_index: Some(0),
        parameters: parameters.iter().map(|name| (*name).into()).collect(),
        code: code.to_vec(),
        byte_len: code.len(),
        context: Some(Arc::new(Abc::parse(&bytes).expect("valid ABC"))),
        ..Default::default()
    }
}

#[test]
fn switch_join_retains_an_inherited_stack_slot_and_converts_selector_once() {
    // Keep an inherited integer across the switch, merge the arm's second
    // integer, then add at the common join. Each arm has a disjoint block.
    let code = [
        0x24, 7, 0xd1, 0x73, 0x1b, 23, 0, 0, 1, 11, 0, 0, 17, 0, 0, 0x24, 10, 0x10, 12, 0, 0, 0x24,
        20, 0x10, 6, 0, 0, 0x24, 30, 0x10, 0, 0, 0, 0xa0, 0x48,
    ];
    let text = source(&method(&code, &["index"]));
    assert_eq!(
        node(format!(
            "{text}\nlet conversions=0;let selector={{valueOf(){{conversions++;return 1;}}}};console.log(JSON.stringify([sample(selector),sample(-1),sample(0),conversions]));"
        )),
        serde_json::json!([27, 37, 17, 1])
    );
}

#[test]
fn switch_rejects_inconsistent_scope_stack_and_preserves_exact_pcode() {
    let code = [
        0xd1, 0x73, 0x1b, 14, 0, 0, 0, 8, 0, 0, 0xd0, 0x30, 0x10, 4, 0, 0, 0x10, 0, 0, 0, 0x47,
    ];
    let unit = method(&code, &["index"]);
    assert_exact_fallback(&unit);
    assert!(
        decompile(&unit)
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.code == "INCONSISTENT_SCOPE_JOIN")
    );
}

#[test]
fn default_shaped_dead_tail_with_a_branch_is_never_omitted() {
    // The reachable return is followed by dead code that branches into the
    // default-shaped final suffix. The full dead region must remain visible.
    let code = [0x24, 11, 0x48, 0x10, 0, 0, 0, 0x21, 0x83, 0x48];
    let unit = method(&code, &[]);
    assert_exact_fallback(&unit);
    assert!(
        !decompile(&unit)
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.code == "COMPILER_EPILOGUE_RETAINED")
    );
}

#[test]
fn compiler_epilogue_requires_matching_body_identity_and_no_handlers() {
    let code = [0x24, 11, 0x48, 0x21, 0x83, 0x48];
    let mut unit = method(&code, &[]);
    let mut context = (*unit.context.take().unwrap()).clone();
    context.bodies[0].code[1] = 19;
    unit.context = Some(Arc::new(context));
    assert_exact_fallback(&unit);
    assert!(
        !decompile(&unit)
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.code == "COMPILER_EPILOGUE_RETAINED")
    );

    let mut unit = method(&code, &[]);
    let mut context = (*unit.context.take().unwrap()).clone();
    context.bodies[0]
        .exceptions
        .push(swf_avm::abc::ExceptionInfo {
            from: 0,
            to: 3,
            target: 3,
            exception_type: 0,
            variable_name: 0,
        });
    unit.context = Some(Arc::new(context));
    assert_exact_fallback(&unit);
    assert!(
        !decompile(&unit)
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.code == "COMPILER_EPILOGUE_RETAINED")
    );
}
