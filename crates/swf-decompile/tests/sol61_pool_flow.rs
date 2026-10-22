//! Do not turn the inventory's linear pool snapshot into a wrong literal.
use std::process::Command;
use swf_avm::{ScriptUnit, disassemble, inventory};
use swf_decompile::decompile;

fn action(opcode: u8, payload: &[u8]) -> Vec<u8> {
    let mut bytes = vec![opcode];
    if opcode >= 0x80 {
        bytes.extend((payload.len() as u16).to_le_bytes());
        bytes.extend(payload);
    }
    bytes
}
fn pool(text: &str) -> Vec<u8> {
    let mut payload = vec![1, 0];
    payload.extend(text.as_bytes());
    payload.push(0);
    action(0x88, &payload)
}
fn function() -> Vec<u8> {
    let body = [0x96, 2, 0, 8, 0, 0x3e, 0];
    let mut payload = b"f\0\0\0".to_vec();
    payload.extend((body.len() as u16).to_le_bytes());
    let mut bytes = action(0x9b, &payload);
    bytes.extend(body);
    bytes
}
fn units(code: &[u8]) -> Vec<ScriptUnit> {
    let mut bytes = vec![b'F', b'W', b'S', 10, 0, 0, 0, 0, 0x08, 0, 0, 12, 1, 0];
    bytes.extend((12u16 << 6 | 63).to_le_bytes());
    bytes.extend((code.len() as u32).to_le_bytes());
    bytes.extend(code);
    bytes.extend([0, 0]);
    let length = bytes.len() as u32;
    bytes[4..8].copy_from_slice(&length.to_le_bytes());
    inventory(&swf_core::Movie::from_bytes(&bytes, "pool-flow.swf").unwrap())
}
fn fallback(unit: &ScriptUnit) {
    let result = decompile(unit);
    assert_eq!(result.state, "pcode_only", "{:?}", result.diagnostics);
    assert!(result.source.is_none());
    assert_eq!(result.pcode, disassemble(unit).pcode);
}
fn check_source(unit: &ScriptUnit, expected: &str) {
    let result = decompile(unit);
    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    let text = result.source.unwrap();
    let script = format!("var f;\n{text}\nconsole.log(JSON.stringify(f()));");
    let output = Command::new("node").args(["-e", &script]).output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap(),
        expected
    );
}

#[test]
fn branch_skipping_second_pool_preserves_parent_and_child_pcode() {
    let mut code = pool("first");
    code.extend(action(0x96, &[5, 1]));
    let second = pool("second");
    code.extend(action(0x9d, &(second.len() as i16).to_le_bytes()));
    code.extend(second);
    code.extend(function());
    code.push(0);
    let units = units(&code);
    assert_eq!(units.len(), 2);
    fallback(&units[0]);
    fallback(&units[1]);
    assert!(
        units[1]
            .diagnostics
            .iter()
            .any(|d| d.code == "AVM1_POOL_CONTEXT_UNPROVEN")
    );
}

#[test]
fn two_straight_line_pools_capture_the_last_pool() {
    let mut code = pool("first");
    code.extend(pool("second"));
    code.extend(function());
    code.push(0);
    let units = units(&code);
    assert_eq!(units.len(), 2);
    check_source(&units[0], "second");
    check_source(&units[1], "second");
}

#[test]
fn single_entry_pool_remains_proven_across_a_loop() {
    let mut code = pool("first");
    let header = code.len();
    code.extend(action(0x96, &[5, 1]));
    let mut body = action(0x96, &[0, b'x', 0]);
    body.push(0x26);
    code.extend(action(0x9d, &((body.len() + 5) as i16).to_le_bytes()));
    code.extend(body);
    let back = header as i16 - (code.len() + 5) as i16;
    code.extend(action(0x99, &back.to_le_bytes()));
    code.extend(function());
    code.push(0);
    let units = units(&code);
    assert_eq!(units.len(), 2);
    check_source(&units[0], "first");
    check_source(&units[1], "first");
}
