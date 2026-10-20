use swf_avm::avm1::disassemble;

#[test]
fn flash_lite_fscommand2_is_a_named_short_action_without_operands() {
    // Local JPEXS ActionFSCommand2 uses action code 0x2D and a zero payload.
    // It is a short action record, so the next byte is another action.
    let result = disassemble(&[0x2d, 0x06, 0x00]);

    assert_eq!(result.state, "decoded");
    assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
    assert_eq!(result.instructions.len(), 3);
    let action = &result.instructions[0];
    assert_eq!(action.opcode, 0x2d);
    assert_eq!(action.name, "fs_command2");
    assert_eq!(action.offset, 0);
    assert_eq!(action.length, 1);
    assert!(action.operands.is_empty());
    assert!(action.parameters.is_empty());
    assert!(action.branch_targets.is_empty());
    assert!(action.body.is_none());
    assert_eq!(result.instructions[1].offset, 1);
    assert_eq!(result.instructions[1].name, "play");
    assert_eq!(result.instructions[2].offset, 2);
    assert_eq!(
        result.pcode,
        "000000: fs_command2 \n000001: play \n000002: end \n"
    );
}

#[test]
fn fscommand2_decode_does_not_relax_unknown_neighbor_opcodes() {
    let result = disassemble(&[0x2d, 0x2e, 0x00]);

    assert_eq!(result.state, "partial");
    assert_eq!(result.instructions.len(), 1);
    assert_eq!(result.instructions[0].name, "fs_command2");
    assert!(result.diagnostics.iter().any(|diagnostic| {
        diagnostic.code == "avm1_unknown_opcode" && diagnostic.offset == Some(1)
    }));
    assert_eq!(
        result.pcode,
        "000000: fs_command2 \n; undecoded raw tail @000001, 2 bytes; first 2 bytes: 2e00\n"
    );
}
