use std::process::Command;
use std::sync::Arc;
use swf_avm::{Abc, ScriptUnit};
use swf_decompile::decompile;

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
    let mut unit = ScriptUnit {
        id: "luna-avm2".into(),
        name: "sample".into(),
        language: "avm2".into(),
        kind: "method".into(),
        code: code.to_vec(),
        byte_len: code.len(),
        parameters: parameters.iter().map(|s| (*s).to_string()).collect(),
        ..Default::default()
    };
    unit.context = Some(abc(code, parameters.len()));
    unit.body_index = Some(0);
    unit.method_index = Some(0);
    unit
}

fn source(unit: &ScriptUnit) -> String {
    let result = decompile(unit);
    assert_eq!(
        result.state, "structured",
        "diagnostics: {:?}\n{}",
        result.diagnostics, result.pcode
    );
    result.source.unwrap()
}

fn node(script: String) -> serde_json::Value {
    let output = Command::new("node")
        .args(["-e", &script])
        .output()
        .expect("Node is required for the AVM2 semantic fixture");
    assert!(
        output.status.success(),
        "{}\n{script}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("semantic fixture must emit JSON")
}

fn converging_switch() -> [u8; 47] {
    [
        0xd1, 0x73, 0x1b, 33, 0, 0, 1, 11, 0, 0, 22, 0, 0, 0xd2, 0x46, 2, 0, 0x29, 0x24, 10, 0x10,
        22, 0, 0, 0xd2, 0x46, 2, 0, 0x29, 0x24, 20, 0x10, 11, 0, 0, 0xd2, 0x46, 2, 0, 0x29, 0x24,
        30, 0x10, 0, 0, 0, 0x48,
    ]
}

#[test]
fn lookup_switch_arms_merge_at_a_forward_join_in_order() {
    let code = converging_switch();
    let text = source(&method(&code, &["index", "target"]));
    assert!(text.contains("switch ("));
    assert!(text.contains("break;"));

    let result = node(format!(
        "let effects=[];let target={{value(){{effects.push('arm');return 99;}}}};let index={{valueOf(){{effects.push('selector');return 1;}}}};\n{text}\nconsole.log(JSON.stringify([sample(index,target),effects]));"
    ));
    assert_eq!(result, serde_json::json!([20, ["selector", "arm"]]));
}

#[test]
fn overlapping_switch_arms_still_fall_back_to_exact_pcode() {
    let mut code = converging_switch();
    code[21] = 0; // Case zero jumps into case one's block.
    let result = decompile(&method(&code, &["index", "target"]));
    assert!(result.source.is_none(), "{:?}", result.diagnostics);
    assert!(result.diagnostics.iter().any(|diagnostic| {
        matches!(
            diagnostic.code.as_str(),
            "UNSUPPORTED_CONTROL_FLOW" | "UNSUPPORTED_SWITCH_FLOW"
        )
    }));
}

#[test]
fn empty_stack_fallthrough_executes_next_case_and_keeps_direct_entries() {
    let code = [
        0xd1, 0x73, 0x1b, 27, 0, 0, 1, 11, 0, 0, 16, 0, 0, 0xd2, 0x46, 2, 0, 0x29, 0xd2, 0x46, 2,
        0, 0x29, 0x24, 20, 0x10, 11, 0, 0, 0xd2, 0x46, 2, 0, 0x29, 0x24, 30, 0x10, 0, 0, 0, 0x48,
    ];
    let text = source(&method(&code, &["index", "target"]));
    assert_eq!(
        node(format!(
            "let calls=0;let target={{value(){{calls++;}}}};\n{text}\nlet values=[sample(0,target),calls,sample(1,target),calls,sample(9,target),calls];console.log(JSON.stringify(values));"
        )),
        serde_json::json!([20, 2, 20, 3, 30, 4])
    );
}
