use std::sync::Arc;
use swf_avm::{Abc, ScriptUnit};
use swf_decompile::decompile;

fn avm2_method(code: &[u8]) -> ScriptUnit {
    // Minimal valid ABC containing one method body with no handlers. Keeping
    // the real body metadata in this fixture exercises the same allow gate as
    // a decoded SWF method.
    let mut bytes = vec![16, 0, 46, 0, 1, 1, 1, 3, 7];
    bytes.extend(b"capture");
    bytes.push(5);
    bytes.extend(b"value");
    bytes.extend([2, 0x16, 0, 1, 3, 7, 1, 1, 7, 1, 2]);
    bytes.push(1);
    bytes.push(0);
    bytes.push(0);
    bytes.extend([0, 0]);
    bytes.extend([0, 0, 0, 1, 0, 8, 1, 0, 1, code.len() as u8]);
    bytes.extend(code);
    bytes.extend([0, 0]);
    let abc = Arc::new(Abc::parse(&bytes).expect("valid minimal ABC fixture"));
    ScriptUnit {
        id: "fixture".into(),
        name: "sample".into(),
        language: "avm2".into(),
        kind: "method".into(),
        method_index: Some(0),
        body_index: Some(0),
        code: code.to_vec(),
        byte_len: code.len(),
        context: Some(abc),
        ..Default::default()
    }
}

#[test]
fn retains_raw_pcode_while_omitting_verified_default_return_epilogue() {
    // getlocal_0; pushscope; return 11; pushundefined; coerce_i; returnvalue
    let code = [0xd0, 0x30, 0x24, 11, 0x83, 0x48, 0x21, 0x83, 0x48];
    let result = decompile(&avm2_method(&code));

    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    let source = result.source.as_deref().expect("structured source");
    assert!(source.contains("11"), "{source}");
    assert_eq!(source.matches("return ").count(), 1, "{source}");
    assert!(
        result.pcode.contains("000006: pushundefined"),
        "{}",
        result.pcode
    );
    assert!(
        result.pcode.contains("000007: coerce_i"),
        "{}",
        result.pcode
    );
    assert!(
        result.pcode.contains("000008: returnvalue"),
        "{}",
        result.pcode
    );
    assert!(result.diagnostics.iter().any(|diagnostic| {
        diagnostic.code == "COMPILER_EPILOGUE_RETAINED" && diagnostic.offset == Some(6)
    }));
}

#[test]
fn unreachable_call_keeps_the_existing_fail_closed_guard_and_full_pcode() {
    // return 11; pushundefined; pushundefined; call 0; returnvalue
    let code = [0x24, 11, 0x48, 0x21, 0x21, 0x41, 0, 0x48];
    let result = decompile(&avm2_method(&code));

    assert_eq!(result.state, "pcode_only");
    assert!(result.source.is_none());
    assert!(result.pcode.contains("000005: call 0"), "{}", result.pcode);
    assert!(result.diagnostics.iter().any(|diagnostic| {
        diagnostic.code == "UNREACHABLE_BYTECODE" && diagnostic.offset == Some(3)
    }));
    assert!(
        !result
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.code == "COMPILER_EPILOGUE_RETAINED")
    );
}

#[test]
fn reachable_branch_into_default_shaped_tail_prevents_epilogue_omission() {
    // pushbyte 0; iftrue tail; return 11; tail: pushundefined; coerce_i; returnvalue
    let code = [0x24, 0, 0x11, 3, 0, 0, 0x24, 11, 0x48, 0x21, 0x83, 0x48];
    let result = decompile(&avm2_method(&code));

    assert!(result.pcode.contains("targets=[9]"), "{}", result.pcode);
    assert!(
        !result
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.code == "COMPILER_EPILOGUE_RETAINED")
    );
}

#[test]
fn matching_bytes_without_valid_avm2_body_metadata_are_not_trimmed() {
    let code = [0xd0, 0x30, 0x24, 11, 0x83, 0x48, 0x21, 0x83, 0x48];
    let mut unit = avm2_method(&code);
    unit.context = None;
    unit.body_index = None;
    unit.method_index = None;
    let result = decompile(&unit);

    assert_eq!(result.state, "pcode_only");
    assert!(
        result.pcode.contains("000006: pushundefined"),
        "{}",
        result.pcode
    );
    assert!(result.diagnostics.iter().any(|diagnostic| {
        diagnostic.code == "UNREACHABLE_BYTECODE" && diagnostic.offset == Some(6)
    }));
    assert!(
        !result
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.code == "COMPILER_EPILOGUE_RETAINED")
    );
}
