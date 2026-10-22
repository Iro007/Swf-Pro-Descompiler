use swf_avm::avm1::disassemble;

#[test]
fn avm1_action_call_is_named_and_keeps_its_empty_record() {
    // SWF 4 ActionCall followed by End. The long-action header is present,
    // while ActionCall itself has no payload or decoded operands.
    let result = disassemble(&[0x9e, 0x00, 0x00, 0x00]);

    assert_eq!(result.state, "decoded");
    assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
    let action = &result.instructions[0];
    assert_eq!(action.opcode, 0x9e);
    assert_eq!(action.name, "call");
    assert_eq!(action.offset, 0);
    assert_eq!(action.length, 3);
    assert!(action.operands.is_empty());
    assert!(action.branch_targets.is_empty());
    assert!(action.body.is_none());
    assert_eq!(result.pcode, "000000: call \n000003: end \n");
}

#[test]
fn action_call_rejects_truncated_headers_and_payloads() {
    for code in [&[0x9e, 0x00][..], &[0x9e, 0x02, 0x00, 0x04][..]] {
        let result = disassemble(code);
        assert!(result.instructions.is_empty());
        assert!(result.diagnostics.iter().any(|diagnostic| {
            diagnostic.code == "avm1_truncated" && diagnostic.offset == Some(0)
        }));
        assert!(result.pcode.contains("undecoded raw tail @000000"));
        let hex = code
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        assert_eq!(
            result.pcode,
            format!(
                "; undecoded raw tail @000000, {} bytes; first {} bytes: {hex}\n",
                code.len(),
                code.len()
            )
        );
    }
}

#[test]
fn action_call_rejects_nonempty_payload_without_decoding_an_instruction() {
    let result = disassemble(&[0x9e, 0x01, 0x00, 0x7f]);

    assert!(result.instructions.is_empty());
    assert!(result.diagnostics.iter().any(|diagnostic| {
        diagnostic.code == "avm1_call_payload"
            && diagnostic.offset == Some(0)
            && diagnostic.message.contains("empty payload")
    }));
    assert_eq!(
        result.pcode,
        "; undecoded raw tail @000000, 4 bytes; first 4 bytes: 9e01007f\n"
    );
}
