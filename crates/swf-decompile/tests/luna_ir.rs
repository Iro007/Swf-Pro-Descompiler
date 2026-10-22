use std::process::Command;
use swf_avm::ScriptUnit;
use swf_decompile::decompile;

fn function_unit(
    name: &str,
    parameters: &[&str],
    parameter_registers: &[u8],
    flags: u16,
    register_count: u8,
    code: &[u8],
) -> ScriptUnit {
    ScriptUnit {
        id: "function2-fixture".into(),
        name: name.into(),
        language: "avm1".into(),
        kind: "function".into(),
        code: code.to_vec(),
        byte_len: code.len(),
        parameters: parameters.iter().map(|s| (*s).to_string()).collect(),
        avm1_flags: Some(flags),
        avm1_registers: parameter_registers.to_vec(),
        avm1_register_count: Some(register_count),
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
    result.source.expect("structured output has source")
}

fn node(script: String) -> serde_json::Value {
    let output = Command::new("node")
        .args(["-e", &script])
        .output()
        .expect("Node is required for semantic Function2 checks");
    assert!(
        output.status.success(),
        "{}\n{script}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("semantic check must emit JSON")
}

fn push_register(register: u8) -> [u8; 5] {
    [0x96, 2, 0, 4, register]
}

fn push_string(value: &str) -> Vec<u8> {
    let mut payload = vec![0];
    payload.extend(value.as_bytes());
    payload.push(0);
    let mut result = vec![0x96];
    result.extend((payload.len() as u16).to_le_bytes());
    result.extend(payload);
    result
}

#[test]
fn function2_parameter_register_receives_the_call_argument() {
    let mut body = push_register(1).to_vec();
    body.push(0x3e); // ActionReturn
    let text = source(&function_unit("sample", &["value"], &[1], 0, 1, &body));

    assert_eq!(
        node(format!("{text}\nconsole.log(JSON.stringify(sample(37)));")),
        37
    );
}

#[test]
fn register_bound_parameter_name_does_not_shadow_getvariable() {
    // A Function2 parameter assigned to a register has no activation-object
    // variable. ActionGetVariable("value") must keep resolving the outer
    // binding instead of the function's positional argument.
    let mut body = push_string("value");
    body.extend([0x1c, 0x3e]); // ActionGetVariable; ActionReturn
    let text = source(&function_unit("sample", &["value"], &[1], 0, 1, &body));

    assert_eq!(
        node(format!(
            "let value = 91;\n{text}\nconsole.log(JSON.stringify(sample(37)));"
        )),
        91
    );
}

#[test]
fn function2_preloads_this_then_root_into_registers_in_spec_order() {
    // PreloadThis and PreloadRoot use consecutive registers starting at R1.
    // Trace observes both values in bytecode order so reversing the bindings
    // cannot pass this check.
    let mut body = push_register(1).to_vec();
    body.push(0x26); // ActionTrace
    body.extend(push_register(2));
    body.extend([0x26, 0x00]); // ActionTrace; ActionEnd
    let text = source(&function_unit("sample", &[], &[], 0x01 | 0x40, 2, &body));

    assert_eq!(
        node(format!(
            "let observed=[];let _root={{kind:'root'}};let receiver={{kind:'this',sample}};function trace(value){{observed.push(value.kind);}}\n{text}\nreceiver.sample();console.log(JSON.stringify(observed));"
        )),
        serde_json::json!(["this", "root"])
    );
}

#[test]
fn preload_super_used_as_a_register_keeps_exact_pcode() {
    // `super` is context-sensitive in ActionScript and cannot safely become
    // an ordinary JavaScript object temporary. Keep the method intact until
    // a source form can preserve its call receiver semantics.
    let mut body = push_register(1).to_vec();
    body.push(0x3e);
    let result = decompile(&function_unit("sample", &[], &[], 0x10, 1, &body));
    assert!(result.source.is_none());
    assert!(
        result
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.code == "UNSUPPORTED_REGISTER_ENVIRONMENT")
    );
}
