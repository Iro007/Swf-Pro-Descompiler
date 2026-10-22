use std::process::Command;
use std::sync::Arc;
use swf_avm::{Abc, ScriptUnit};
use swf_decompile::decompile;

fn unit(language: &str, code: &[u8]) -> ScriptUnit {
    ScriptUnit {
        id: "fixture".into(),
        name: "sample".into(),
        language: language.into(),
        kind: if language == "avm2" {
            "method"
        } else {
            "frame"
        }
        .into(),
        code: code.to_vec(),
        byte_len: code.len(),
        ..Default::default()
    }
}
fn source(unit: &ScriptUnit) -> String {
    let result = decompile(unit);
    assert_eq!(
        result.state, "structured",
        "{:?}\n{}",
        result.diagnostics, result.pcode
    );
    result.source.unwrap()
}

// These checks execute emitted ActionScript's shared ECMAScript subset. Their
// expected results are source-level oracles, not copies of the stack lifter.
fn node(script: String) -> serde_json::Value {
    let output = Command::new("node")
        .args(["-e", &script])
        .output()
        .expect("Node is required for semantic fixture checks");
    assert!(
        output.status.success(),
        "{}\n{script}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("semantic check must emit JSON")
}
fn action(opcode: u8, payload: &[u8]) -> Vec<u8> {
    let mut result = vec![opcode];
    if opcode >= 0x80 {
        result.extend((payload.len() as u16).to_le_bytes());
        result.extend(payload);
    }
    result
}
fn push(values: &[&str]) -> Vec<u8> {
    let mut payload = vec![];
    for value in values {
        if let Ok(value) = value.parse::<i32>() {
            payload.push(7);
            payload.extend(value.to_le_bytes());
        } else {
            payload.push(0);
            payload.extend(value.as_bytes());
            payload.push(0);
        }
    }
    action(0x96, &payload)
}

// A minimal valid ABC fixture with public names capture/value and method context.
fn abc(code: &[u8], parameters: usize) -> Arc<Abc> {
    let mut bytes = vec![16, 0, 46, 0, 1, 1, 1, 3, 7];
    bytes.extend(b"capture");
    bytes.push(5);
    bytes.extend(b"value");
    bytes.extend([2, 0x16, 0, 1, 3, 7, 1, 1, 7, 1, 2]);
    bytes.push(1);
    bytes.push(parameters as u8);
    bytes.push(0);
    bytes.extend(vec![0; parameters]);
    bytes.extend([0, 0]);
    bytes.extend([
        0,
        0,
        0,
        1,
        0,
        8,
        (parameters + 1) as u8,
        0,
        1,
        code.len() as u8,
    ]);
    bytes.extend(code);
    bytes.extend([0, 0]);
    Arc::new(Abc::parse(&bytes).expect("valid minimal ABC fixture"))
}
fn method(code: &[u8], parameters: &[&str]) -> ScriptUnit {
    let mut unit = unit("avm2", code);
    unit.context = Some(abc(code, parameters.len()));
    unit.body_index = Some(0);
    unit.method_index = Some(0);
    unit.parameters = parameters.iter().map(|s| s.to_string()).collect();
    unit
}

#[test]
fn arithmetic_and_local_snapshot_are_evaluated_before_assignment() {
    let code = [0xd1, 0x24, 9, 0xd5, 0x48];
    let text = source(&method(&code, &["x"]));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify([sample(2), sample(7)]));"
        )),
        serde_json::json!([2, 7])
    );
    let text = source(&unit("avm2", &[0x24, 2, 0x24, 3, 0xa0, 0x48]));
    assert_eq!(
        node(format!("{text}\nconsole.log(JSON.stringify(sample()));")),
        5
    );
}

#[test]
fn pushshort_sign_extends_16_bits_and_integer_product_wraps_exactly() {
    let text = source(&unit("avm2", &[0x25, 0xff, 0xff, 3, 0x48]));
    assert_eq!(
        node(format!("{text}\nconsole.log(JSON.stringify(sample()));")),
        -1
    );
    let text = source(&method(&[0xd1, 0xd2, 0xc7, 0x48], &["left", "right"]));
    assert_eq!(
        node(format!(
            "function int(x){{return x|0;}}\n{text}\nconsole.log(JSON.stringify([sample(2147483647,2147483647),sample(-2147483648,3),sample(-3,7)]));"
        )),
        serde_json::json!([1, -2147483648, -21])
    );
}

#[test]
fn numeric_conversion_order_matches_the_avm2_pop_order() {
    for opcode in [0xa1, 0xa2, 0xa3, 0xa4, 0xc7] {
        let text = source(&method(&[0xd1, 0xd2, opcode, 0x48], &["left", "right"]));
        let actual = node(format!(
            "function int(x){{return x|0;}};let effects=[];let left={{valueOf(){{effects.push('left');return 8;}}}};let right={{valueOf(){{effects.push('right');return 2;}}}};\n{text}\nsample(left,right);console.log(JSON.stringify(effects));"
        ));
        assert_eq!(actual, serde_json::json!(["right", "left"]));
    }
}

#[test]
fn primitive_conversions_do_not_call_shadowing_parameter_bindings() {
    let parameters = [
        "Number",
        "Boolean",
        "int",
        "uint",
        "NaN",
        "undefined",
        "value",
    ];
    for (opcode, expected) in [
        (0x75, serde_json::json!(6)),
        (0x73, serde_json::json!(6)),
        (0x74, serde_json::json!(6)),
        (0x76, serde_json::json!(true)),
    ] {
        let text = source(&method(&[0x62, 7, opcode, 0x48], &parameters));
        let actual = node(format!(
            "function hostile(){{throw Error('shadowed intrinsic invoked');}};let value={{valueOf(){{return 6;}}}};\n{text}\nconsole.log(JSON.stringify(sample(hostile,hostile,hostile,hostile,99,123,value)));"
        ));
        assert_eq!(actual, expected);
    }
    let text = source(&method(&[0x28, 0x48], &["Infinity", "NaN", "undefined"]));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify(isNaN(sample(11,12,13))));"
        )),
        true
    );
    let text = source(&method(&[0x21, 0x48], &["Infinity", "NaN", "undefined"]));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify(typeof sample(11,12,13)));"
        )),
        "undefined"
    );
}

#[test]
fn named_as2_number_function_does_not_intercept_native_conversion() {
    let mut body = push(&["0"]);
    body.extend([0x3e, 0]);
    let mut payload = b"Number\0\0\0".to_vec();
    payload.extend((body.len() as u16).to_le_bytes());
    let mut code = action(0x9b, &payload);
    code.extend(body);
    code.extend(push(&["17"]));
    code.extend([0x4a, 0x26, 0]);
    let text = source(&unit("avm1", &code));
    assert_eq!(
        node(format!(
            "let observed=[];function trace(x){{observed.push(x);}}\n{text}\nconsole.log(JSON.stringify(observed));"
        )),
        serde_json::json!([17])
    );
}

#[test]
fn shadowed_string_conversion_retains_the_entire_method_as_pcode() {
    for opcode in [0x70, 0x85] {
        let result = decompile(&method(&[0xd2, opcode, 0x48], &["String", "value"]));
        assert!(result.source.is_none());
        assert!(
            result
                .diagnostics
                .iter()
                .any(|d| d.code == "SHADOWED_INTRINSIC")
        );
    }
    let mut u = method(&[0xd1, 0x70, 0x48], &["value"]);
    u.name = "String".into();
    let result = decompile(&u);
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "SHADOWED_INTRINSIC")
    );
}

#[test]
fn avm1_arguments_are_popped_in_source_order() {
    let mut code = push(&["second", "first", "2", "capture"]);
    code.extend([0x3d, 0x26, 0]);
    let text = source(&unit("avm1", &code));
    let script = format!(
        "let calls=[]; function capture(a,b){{calls.push([a,b]); return a+b;}} function trace(x){{}}\n{text}\nconsole.log(JSON.stringify(calls));"
    );
    assert_eq!(node(script), serde_json::json!([["first", "second"]]));
}

#[test]
fn avm2_arguments_are_popped_in_reverse_source_order() {
    let code = [0x5d, 1, 0x24, 2, 0x24, 7, 0x46, 1, 2, 0x48];
    let text = source(&method(&code, &[]));
    assert_eq!(
        node(format!(
            "function capture(a,b){{return [a,b];}}\n{text}\nconsole.log(JSON.stringify(sample()));"
        )),
        serde_json::json!([2, 7])
    );
}

#[test]
fn duplicate_call_value_executes_the_call_exactly_once() {
    let code = [0x5d, 1, 0x46, 1, 0, 0x2a, 0xa0, 0x48];
    let text = source(&method(&code, &[]));
    let actual = node(format!(
        "let calls=0; function capture(){{calls++; return 11;}}\n{text}\nconsole.log(JSON.stringify([sample(), calls]));"
    ));
    assert_eq!(actual, serde_json::json!([22, 1]));
}

#[test]
fn duplicate_getter_executes_the_getter_exactly_once() {
    let code = [0xd1, 0x66, 2, 0x2a, 0xa0, 0x48];
    let text = source(&method(&code, &["object"]));
    assert_eq!(
        node(format!(
            "let calls=0;let object={{get value(){{calls++; return 5;}}}};\n{text}\nconsole.log(JSON.stringify([sample(object), calls]));"
        )),
        serde_json::json!([10, 1])
    );
}

#[test]
fn if_else_returns_and_lazy_boolean_join_match_the_source_oracle() {
    let code = [0xd1, 0x12, 3, 0, 0, 0x24, 1, 0x48, 0x24, 2, 0x48];
    let text = source(&method(&code, &["x"]));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify([sample(true),sample(false)]));"
        )),
        serde_json::json!([1, 2])
    );
    // x || capture(): a retained duplicated x on the taken edge, a call only
    // on the untaken edge, and a common stack-value join.
    let code = [0xd1, 0x2a, 0x11, 6, 0, 0, 0x29, 0x5d, 1, 0x46, 1, 0, 0x48];
    let text = source(&method(&code, &["x"]));
    let actual = node(format!(
        "let calls=0;function capture(){{calls++;return 42;}}\n{text}\nconsole.log(JSON.stringify([sample(9),calls,sample(0),calls]));"
    ));
    assert_eq!(actual, serde_json::json!([9, 0, 42, 1]));
}

#[test]
fn while_condition_and_update_are_repeated_at_runtime() {
    let code = [
        0x24, 0, 0xd5, 0x09, 0xd1, 0x24, 3, 0xad, 0x12, 6, 0, 0, 0x92, 1, 0x10, 0xf1, 0xff, 0xff,
        0xd1, 0x48,
    ];
    let mut u = method(&code, &["unused"]);
    u.parameters[0] = "index".into();
    let text = source(&u);
    assert!(text.contains("while ("));
    assert_eq!(
        node(format!("{text}\nconsole.log(JSON.stringify(sample(99)));")),
        3
    );
}

#[test]
fn reducible_if_inside_a_loop_preserves_its_body_and_join() {
    let code = [
        0x24, 0, 0xd5, 0x09, 0xd1, 0x24, 3, 0xad, 0x12, 20, 0, 0, 0xd1, 0x24, 1, 0xab, 0x12, 6, 0,
        0, 0x5d, 1, 0xd1, 0x4f, 1, 1, 0x92, 1, 0x10, 0xe3, 0xff, 0xff, 0xd1, 0x48,
    ];
    let text = source(&method(&code, &["index"]));
    assert_eq!(
        node(format!(
            "let calls=[];function capture(x){{calls.push(x);}}\n{text}\nconsole.log(JSON.stringify([sample(9),calls]));"
        )),
        serde_json::json!([3, [1]])
    );
}

#[test]
fn finite_resource_budgets_and_unreachable_code_fail_closed() {
    let mut code = vec![0x02; swf_decompile::MAX_INSTRUCTIONS];
    code.push(0x47);
    let result = decompile(&unit("avm2", &code));
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "DECOMPILE_LIMIT")
    );
    let mut code = [0x10, 0, 0, 0].repeat(swf_decompile::MAX_BLOCKS);
    code.push(0x47);
    let result = decompile(&unit("avm2", &code));
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "DECOMPILE_LIMIT")
    );
    let result = decompile(&unit("avm2", &[0x47, 0x57, 0x29, 0x47]));
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "UNREACHABLE_BYTECODE")
    );
}

#[test]
fn avm1_named_function_recurses_and_preserves_captured_pool() {
    let body = [0x96, 2, 0, 8, 0, 0x3e, 0];
    let mut payload = b"named\0\0\0".to_vec();
    payload.extend((body.len() as u16).to_le_bytes());
    let mut code = action(0x88, &[1, 0, b'x', 0]);
    code.extend(action(0x9b, &payload));
    code.extend(body);
    code.push(0);
    let text = source(&unit("avm1", &code));
    assert_eq!(
        node(format!("{text}\nconsole.log(JSON.stringify(named()));")),
        "x"
    );
}

#[test]
fn avm1_function_binding_is_not_hoisted_before_its_action() {
    let body = [0x96, 5, 0, 7, 9, 0, 0, 0, 0x3e, 0];
    let mut code = push(&["named"]);
    code.extend([0x1c, 0x44, 0x26]);
    let mut payload = b"named\0\0\0".to_vec();
    payload.extend((body.len() as u16).to_le_bytes());
    code.extend(action(0x9b, &payload));
    code.extend(body);
    code.push(0);
    let text = source(&unit("avm1", &code));
    // AVM1 GetVariable returns undefined for absent names; ECMAScript reads an
    // undeclared identifier differently, so provide its initial undefined slot.
    assert_eq!(
        node(format!(
            "var named;let observed=[];function trace(x){{observed.push(x);}}\n{text}\nconsole.log(JSON.stringify([observed,named()]));"
        )),
        serde_json::json!([["undefined"], 9])
    );
}

#[test]
fn avm1_function_name_lookup_remains_in_the_enclosing_scope() {
    let mut body = push(&["named"]);
    body.extend([0x1c, 0x3e, 0]);
    let mut payload = b"named\0\0\0".to_vec();
    payload.extend((body.len() as u16).to_le_bytes());
    let mut code = action(0x9b, &payload);
    code.extend(body);
    code.push(0);
    let text = source(&unit("avm1", &code));
    assert_eq!(
        node(format!(
            "var named;\n{text}\nlet saved=named;named=17;console.log(JSON.stringify(saved()));"
        )),
        17
    );
}

#[test]
fn negated_comparison_retains_unordered_nan_behavior() {
    let code = [0xd1, 0xd2, 0x0c, 3, 0, 0, 0x24, 1, 0x48, 0x24, 2, 0x48];
    let text = source(&method(&code, &["left", "right"]));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify([sample(NaN,0),sample(0,1),sample(2,1)]));"
        )),
        serde_json::json!([2, 1, 2])
    );
}

#[test]
fn lexical_receiver_lookup_is_not_moved_after_argument_effects() {
    let code = [0x5d, 1, 0xd1, 0x66, 2, 0x46, 1, 1, 0x48];
    let result = decompile(&method(&code, &["object"]));
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "UNPROVEN_LOOKUP_ORDER")
    );
}

#[test]
fn activation_without_verified_environment_removes_the_entire_source_method() {
    let u = unit("avm2", &[0x24, 1, 0x29, 0x57, 0x29, 0x47]);
    let result = decompile(&u);
    assert_eq!(result.state, "pcode_only");
    assert!(result.source.is_none());
    assert!(result.pcode.contains("pushbyte"));
    assert!(result.pcode.contains("newactivation"));
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "UNSUPPORTED_ACTIVATION")
    );
}

#[test]
fn adversarial_stack_branch_and_scope_are_diagnostic() {
    for code in [
        &[0x29, 0x47][..],
        &[0x1d, 0x47][..],
        &[0x10, 1, 0, 0, 0x24, 1, 0x47][..],
    ] {
        let result = decompile(&unit("avm2", code));
        assert!(result.source.is_none());
        assert!(!result.diagnostics.is_empty());
    }
}

#[test]
fn unproven_namespace_never_becomes_a_public_property() {
    let mut u = method(&[0xd1, 0x66, 2, 0x48], &["object"]);
    let mut context = (*u.context.take().unwrap()).clone();
    context.namespaces[1].kind = 0x05;
    u.context = Some(Arc::new(context));
    let result = decompile(&u);
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "UNSUPPORTED_NAMESPACE")
    );
}

fn namespaced(code: &[u8], kind: u8, uri: &str) -> ScriptUnit {
    let mut u = method(code, &["object"]);
    let mut context = (*u.context.take().unwrap()).clone();
    context.strings.push(uri.into());
    context.namespaces[1].kind = kind;
    context.namespaces[1].name = (context.strings.len() - 1) as u32;
    u.context = Some(Arc::new(context));
    u
}

#[test]
fn qualified_public_qname_retains_uri_and_distinct_namespace_identities() {
    for kind in [0x08, 0x16] {
        let text = source(&namespaced(&[0xd1, 0x66, 2, 0x48], kind, "urn:example"));
        assert!(text.contains("namespace __rustswf_namespace0 = \"urn:example\";"));
        assert!(text.contains(".__rustswf_namespace0::value"));
        assert!(!text.contains(").value"));
    }
    for kind in [0x05, 0x17, 0x18, 0x19, 0x1a] {
        let result = decompile(&namespaced(&[0xd1, 0x66, 2, 0x48], kind, "urn:example"));
        assert!(
            result.source.is_none(),
            "namespace kind {kind:#x} must retain exact P-code"
        );
    }
}

#[test]
fn equivalent_public_namespace_set_is_supported_but_mixed_private_set_is_not() {
    let mut u = method(&[0xd1, 0x66, 2, 0x48], &["object"]);
    let mut a = (*u.context.take().unwrap()).clone();
    a.namespaces.push(swf_avm::abc::Namespace {
        kind: 0x08,
        name: 0,
    });
    a.ns_sets.push(vec![1, 2, 1]);
    a.multinames[2].kind = 0x09;
    a.multinames[2].namespace = 0;
    a.multinames[2].ns_set = 1;
    u.context = Some(Arc::new(a.clone()));
    let text = source(&u);
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify(sample({{value:37}})));"
        )),
        serde_json::json!(37)
    );
    a.namespaces[2].kind = 0x05;
    u.context = Some(Arc::new(a));
    assert!(decompile(&u).source.is_none());
}

#[test]
fn runtime_names_require_primitive_keys_and_public_namespace_proof() {
    let mut u = method(&[0xd1, 0x24, 3, 0x66, 2, 0x48], &["object"]);
    let mut a = (*u.context.take().unwrap()).clone();
    a.multinames[2].kind = 0x1b;
    a.multinames[2].namespace = 0;
    a.ns_sets.push(vec![1]);
    a.multinames[2].ns_set = 1;
    u.context = Some(Arc::new(a.clone()));
    let text = source(&u);
    assert!(text.contains(".__rustswf_namespace0::[3]"));
    assert!(text.contains("namespace __rustswf_namespace0 = \"\";"));
    let mut dynamic = method(&[0xd1, 0xd2, 0x66, 2, 0x48], &["object", "untrustedKey"]);
    a.bodies[0].code = dynamic.code.clone();
    a.bodies[0].local_count = 3;
    dynamic.context = Some(Arc::new(a));
    let result = decompile(&dynamic);
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "UNPROVEN_RUNTIME_NAME")
    );
}

#[test]
fn runtime_qnames_preserve_public_constant_and_reject_namespace_value_escape() {
    let mut u = namespaced(&[0xd1, 0x31, 1, 0x66, 2, 0x48], 0x08, "urn:runtime");
    let mut a = (*u.context.take().unwrap()).clone();
    a.multinames[2].kind = 0x0f;
    a.multinames[2].namespace = 0;
    u.context = Some(Arc::new(a));
    assert!(source(&u).contains(".__rustswf_namespace0::value"));
    let escaped = namespaced(&[0x31, 1, 0x48], 0x08, "urn:runtime");
    let result = decompile(&escaped);
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "UNPROVEN_NAMESPACE_VALUE")
    );
}

#[test]
fn lexical_parameter_shadowing_cannot_change_a_global_binding() {
    let result = decompile(&method(&[0x60, 2, 0x48], &["value"]));
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "SHADOWED_LEXICAL_BINDING")
    );
}

#[test]
fn terminal_switch_uses_signed_int_index_default_and_shared_case_targets() {
    let code = [
        0xd1, 0x73, 0x1b, 17, 0, 0, 1, 11, 0, 0, 14, 0, 0, 0x24, 10, 0x48, 0x24, 20, 0x48, 0x24,
        30, 0x48,
    ];
    let text = source(&method(&code, &["index"]));
    assert!(text.contains("switch ("));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify([-2,-1,0,1,2,1.9,NaN,4294967296].map(sample)));"
        )),
        serde_json::json!([30, 30, 10, 20, 30, 20, 10, 10])
    );
    let mut shared = code;
    shared[10] = 11; // Cases 0 and 1 share the same return block.
    shared[16] = 0x02;
    shared[17] = 0x02;
    shared[18] = 0x02; // Unreachable no-ops are benign.
    let text = source(&method(&shared, &["index"]));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify([0,1,2].map(sample)));"
        )),
        serde_json::json!([10, 10, 30])
    );
}

#[test]
fn switch_selector_is_coerced_once_and_overlapping_arms_keep_pcode() {
    let code = [
        0xd1, 0x73, 0x1b, 17, 0, 0, 1, 11, 0, 0, 14, 0, 0, 0x24, 10, 0x48, 0x24, 20, 0x48, 0x24,
        30, 0x48,
    ];
    let text = source(&method(&code, &["index"]));
    assert_eq!(
        node(format!(
            "{text}\nlet calls=0;let result=sample({{valueOf(){{calls++;return 1;}}}});console.log(JSON.stringify([result,calls]));"
        )),
        serde_json::json!([20, 1])
    );
    let mut overlapping = code;
    overlapping[15] = 0x02; // Case 0 falls through into the case 1 block; not a disjoint arm.
    assert!(
        decompile(&method(&overlapping, &["index"]))
            .source
            .is_none()
    );
    let untyped = [&code[..1], &code[2..]].concat(); // Remove convert_i: runtime switch requires an actual int.
    let result = decompile(&method(&untyped, &["index"]));
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "UNPROVEN_SWITCH_INDEX")
    );
}

#[test]
#[ignore = "Optional Apache Flex CLI SDK proves emitted namespace selectors against a real compiler; no GUI"]
fn qualified_source_compiles_back_to_the_same_public_namespace_uri() {
    let java = std::env::var_os("RUSTSWF_FLEX_JAVA").expect("Set RUSTSWF_FLEX_JAVA");
    let compiler = std::env::var_os("RUSTSWF_MXMLC_JAR").expect("Set RUSTSWF_MXMLC_JAR");
    let player =
        std::env::var_os("RUSTSWF_PLAYERGLOBAL_HOME").expect("Set RUSTSWF_PLAYERGLOBAL_HOME");
    let directory =
        std::env::temp_dir().join(format!("rustswf-namespace-sdk-{}", std::process::id()));
    std::fs::create_dir_all(&directory).unwrap();
    for (kind, uri, runtime) in [
        (0x08, "urn:example", false),
        (0x16, "example.pkg", false),
        (0x16, "", true),
    ] {
        let fixture = if runtime {
            let mut unit = namespaced(&[0xd1, 0x24, 3, 0x66, 2, 0x48], kind, uri);
            let mut abc = (*unit.context.take().unwrap()).clone();
            abc.multinames[2].kind = 0x1b;
            abc.multinames[2].namespace = 0;
            abc.ns_sets.push(vec![1]);
            abc.multinames[2].ns_set = 1;
            unit.context = Some(Arc::new(abc));
            unit
        } else {
            namespaced(&[0xd1, 0x66, 2, 0x48], kind, uri)
        };
        let text = source(&fixture);
        let source_file = directory.join("RustSWFNamespaceProbe.as");
        let output_file = directory.join(format!("namespace-{kind}-{runtime}.swf"));
        std::fs::write(&source_file, format!("package {{ import flash.display.Sprite; public class RustSWFNamespaceProbe extends Sprite {{\n{text}\n}} }}")).unwrap();
        let mut command = Command::new(&java);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let output = command
            .args(["-Djava.awt.headless=true", "-jar"])
            .arg(&compiler)
            .arg(format!(
                "+flexlib={}",
                std::path::Path::new(&compiler)
                    .parent()
                    .unwrap()
                    .parent()
                    .unwrap()
                    .join("frameworks")
                    .display()
            ))
            .arg(format!(
                "+playerglobalHome={}",
                std::path::Path::new(&player).display()
            ))
            .args([
                "-target-player=32.0",
                "-static-link-runtime-shared-libraries=true",
                "-debug=false",
            ])
            .arg(format!("-output={}", output_file.display()))
            .arg(&source_file)
            .env("PLAYERGLOBAL_HOME", &player)
            .current_dir(&directory)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let movie = swf_core::Movie::read(&output_file).unwrap();
        let units = swf_avm::inventory(&movie);
        let method = units
            .iter()
            .find(|u| u.name == "sample")
            .expect("compiled sample method");
        let instructions = swf_avm::disassemble(method).instructions;
        let getter = instructions
            .iter()
            .find(|i| i.name == "getproperty")
            .expect("compiled qualified getter");
        let swf_avm::Operand::Pool { index, .. } = getter.operands[0] else {
            panic!("getter must have multiname")
        };
        let abc = method.context.as_ref().unwrap();
        if let Some(property) = abc.public_property(index) {
            assert_eq!(property.namespace_uri, uri);
            assert_eq!(
                property.name.as_deref(),
                if runtime { None } else { Some("value") }
            );
        } else {
            assert!(runtime && abc.multinames[index as usize].kind == 0x11);
            let namespace = instructions
                .iter()
                .find(|i| i.name == "pushnamespace")
                .expect("compiler must preserve the constant namespace");
            let swf_avm::Operand::Pool { index, .. } = namespace.operands[0] else {
                panic!("namespace must have pool reference")
            };
            assert_eq!(abc.public_namespace_uri(index), Some(uri));
        }
        if runtime {
            assert!(instructions.iter().any(|i| i.name == "pushbyte"
                && matches!(i.operands.first(), Some(swf_avm::Operand::Signed(3)))));
        }
    }
    // Scratch sources remain inspectable when this optional semantic check is requested.
    eprintln!("Namespace compiler evidence: {}", directory.display());
}
