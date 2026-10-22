use sha2::{Digest, Sha256};
use swf_avm::{Operand, ScriptUnit, disassemble, inventory};
use swf_core::Movie;

fn action(opcode: u8, payload: &[u8], body: &[u8]) -> Vec<u8> {
    let mut result = vec![opcode];
    result.extend(u16::try_from(payload.len()).unwrap().to_le_bytes());
    result.extend(payload);
    result.extend(body);
    result
}

fn function2(name: &str, count: u8, flags: u16, params: &[(u8, &str)], body: &[u8]) -> Vec<u8> {
    let mut header = name.as_bytes().to_vec();
    header.push(0);
    header.extend(u16::try_from(params.len()).unwrap().to_le_bytes());
    header.push(count);
    header.extend(flags.to_le_bytes());
    for (register, name) in params {
        header.push(*register);
        header.extend(name.as_bytes());
        header.push(0);
    }
    header.extend(u16::try_from(body.len()).unwrap().to_le_bytes());
    action(0x8e, &header, body)
}

fn function(name: &str, body: &[u8]) -> Vec<u8> {
    let mut header = name.as_bytes().to_vec();
    header.push(0);
    header.extend(0_u16.to_le_bytes());
    header.extend(u16::try_from(body.len()).unwrap().to_le_bytes());
    action(0x9b, &header, body)
}

fn body_offset(bytes: &[u8]) -> usize {
    3 + u16::from_le_bytes(bytes[1..3].try_into().unwrap()) as usize
}

fn pool(value: &str) -> Vec<u8> {
    let mut payload = vec![1, 0];
    payload.extend(value.as_bytes());
    payload.push(0);
    action(0x88, &payload, &[])
}

fn tag(code: u16, data: &[u8]) -> Vec<u8> {
    let mut result = Vec::new();
    if data.len() < 63 {
        result.extend((code << 6 | data.len() as u16).to_le_bytes());
    } else {
        result.extend((code << 6 | 63).to_le_bytes());
        result.extend(u32::try_from(data.len()).unwrap().to_le_bytes());
    }
    result.extend(data);
    result
}

fn movie(tags: &[(u16, Vec<u8>)]) -> Movie {
    let mut bytes = vec![b'F', b'W', b'S', 10, 0, 0, 0, 0, 0x08, 0, 0, 12, 1, 0];
    for (code, data) in tags {
        bytes.extend(tag(*code, data));
    }
    bytes.extend([0, 0]);
    let size = u32::try_from(bytes.len()).unwrap();
    bytes[4..8].copy_from_slice(&size.to_le_bytes());
    Movie::from_bytes(&bytes, "inventory-context.swf").unwrap()
}

fn check_body(movie: &Movie, child: &ScriptUnit, parent: &ScriptUnit, start: usize, body: &[u8]) {
    assert_eq!(child.id, format!("{}/function:{start:x}", parent.id));
    assert_eq!(child.tag_path, parent.tag_path);
    assert_eq!(child.byte_start, start);
    assert_eq!(child.byte_len, body.len());
    assert_eq!(child.code, body);
    assert_eq!(&movie.normalized[start..start + body.len()], body);
    assert_eq!(child.sha256, format!("{:x}", Sha256::digest(body)));
    assert_eq!(child.event_flags, parent.event_flags);
}

#[test]
fn function2_inventory_preserves_declared_context_offsets_and_order() {
    let inherited = pool("captured");
    let local = pool("local");
    let leaf_body = [0x96, 2, 0, 8, 0, 0x3e, 0];
    let inner = function2("innerName", 0, 0, &[], &leaf_body);
    let classic = function("classicName", &[0]);
    let mut outer_body = local.clone();
    outer_body.extend(&inner);
    outer_body.extend(&classic);
    outer_body.push(0);
    let outer = function2("outerName", 7, 0x141, &[(3, "keptName")], &outer_body);
    let sibling = function2("siblingName", 9, 0, &[], &[0]);
    let mut code = inherited.clone();
    code.extend(&outer);
    code.extend(&sibling);
    code.push(0);
    let movie = movie(&[(12, code.clone())]);
    let original = movie.original.clone();
    let normalized = movie.normalized.clone();
    let units = inventory(&movie);
    assert_eq!(units.len(), 5);
    assert_eq!(
        units
            .iter()
            .map(|u| u.name.as_str())
            .skip(1)
            .collect::<Vec<_>>(),
        ["outerName", "innerName", "classicName", "siblingName"]
    );
    assert_eq!(units[0].code, code);
    let outer_start = units[0].byte_start + inherited.len() + body_offset(&outer);
    check_body(&movie, &units[1], &units[0], outer_start, &outer_body);
    check_body(
        &movie,
        &units[2],
        &units[1],
        outer_start + local.len() + body_offset(&inner),
        &leaf_body,
    );
    check_body(
        &movie,
        &units[3],
        &units[1],
        outer_start + local.len() + inner.len() + body_offset(&classic),
        &[0],
    );
    check_body(
        &movie,
        &units[4],
        &units[0],
        units[0].byte_start + inherited.len() + outer.len() + body_offset(&sibling),
        &[0],
    );
    assert_eq!(units[1].parameters, ["keptName"]);
    assert_eq!(units[1].avm1_registers, [3]);
    assert_eq!(units[1].avm1_register_count, Some(7));
    assert_eq!(units[1].avm1_flags, Some(0x141));
    assert_eq!(units[1].avm1_constants, ["captured"]);
    assert_eq!(units[2].avm1_constants, ["local"]);
    assert_eq!(units[3].avm1_constants, ["local"]);
    assert_eq!(units[4].avm1_constants, ["captured"]);
    assert_eq!(units[2].avm1_register_count, Some(0));
    assert_eq!(units[3].avm1_register_count, None);
    assert_eq!(units[3].avm1_flags, Some(0));
    assert_eq!(units[4].avm1_register_count, Some(9));
    let decoded = disassemble(&units[2]);
    assert!(decoded.diagnostics.is_empty(), "{:?}", decoded.diagnostics);
    assert!(
        matches!(&decoded.instructions[0].operands[0], Operand::Values(v) if v == &["\"local\""])
    );
    assert_eq!(movie.original, original);
    assert_eq!(movie.normalized, normalized);
}

fn button2(records: &[(u16, &[u8])]) -> Vec<u8> {
    // Empty button records, followed by ButtonCondAction records.
    let mut result = vec![1, 0, 0, 3, 0, 0];
    for (index, (flags, code)) in records.iter().enumerate() {
        let size = if index + 1 == records.len() {
            0
        } else {
            u16::try_from(code.len() + 4).unwrap()
        };
        result.extend(size.to_le_bytes());
        result.extend(flags.to_le_bytes());
        result.extend(*code);
    }
    result
}

fn clip(records: &[(u32, &[u8])]) -> Vec<u8> {
    let all_flags = records.iter().fold(0, |all, (flags, _)| all | flags);
    let mut result = vec![0x80, 1, 0, 0, 0];
    result.extend(all_flags.to_le_bytes());
    for (flags, code) in records {
        result.extend(flags.to_le_bytes());
        result.extend(
            u32::try_from(code.len() + usize::from(flags & 0x20000 != 0))
                .unwrap()
                .to_le_bytes(),
        );
        if flags & 0x20000 != 0 {
            result.push(65);
        }
        result.extend(*code);
    }
    result.extend(0_u32.to_le_bytes());
    result
}

#[test]
fn button2_and_clip_events_keep_flags_through_nested_functions_and_containers() {
    let leaf = function2("leafName", 2, 0x100, &[(1, "eventParam")], &[0]);
    let with = action(
        0x94,
        &u16::try_from(leaf.len()).unwrap().to_le_bytes(),
        &leaf,
    );
    let mut try_header = vec![1];
    try_header.extend(u16::try_from(with.len()).unwrap().to_le_bytes());
    try_header.extend([0, 0, 0, 0, b'e', 0]);
    let try_body = action(0x8f, &try_header, &with);
    let mut outer_body = try_body.clone();
    outer_body.push(0);
    let outer = function2("handlerName", 4, 0, &[], &outer_body);
    let mut code = pool("eventValue");
    let outer_offset = code.len();
    code.extend(&outer);
    code.push(0);
    let button = button2(&[(0x8101, &code), (0x0202, &code)]);
    let clip = clip(&[(0x20001, &code), (0x80, &code)]);
    let mut sprite = vec![7, 0, 1, 0];
    sprite.extend(tag(34, &button));
    sprite.extend(tag(26, &clip));
    sprite.extend([0, 0]);
    let movie = movie(&[(39, sprite)]);
    let original = movie.original.clone();
    let normalized = movie.normalized.clone();
    let units = inventory(&movie);
    assert_eq!(units.len(), 12);
    for (group, (kind, path, flags)) in units.chunks_exact(3).zip([
        ("button2", vec![0, 0], 0x8101),
        ("button2", vec![0, 0], 0x0202),
        ("clip", vec![0, 1], 0x20001),
        ("clip", vec![0, 1], 0x80),
    ]) {
        let parent = &group[0];
        assert_eq!(parent.kind, kind);
        assert_eq!(parent.tag_path, path);
        assert_eq!(parent.event_flags, Some(flags));
        let outer_start = parent.byte_start + outer_offset + body_offset(&outer);
        check_body(&movie, &group[1], parent, outer_start, &outer_body);
        let leaf_start =
            outer_start + body_offset(&try_body) + body_offset(&with) + body_offset(&leaf);
        check_body(&movie, &group[2], &group[1], leaf_start, &[0]);
        assert_eq!(group[1].name, "handlerName");
        assert_eq!(group[2].name, "leafName");
        assert_eq!(group[1].avm1_register_count, Some(4));
        assert_eq!(group[2].avm1_register_count, Some(2));
        assert_eq!(group[2].parameters, ["eventParam"]);
        assert_eq!(group[2].avm1_registers, [1]);
        assert_eq!(group[2].avm1_flags, Some(0x100));
        assert_eq!(group[1].avm1_constants, ["eventValue"]);
        assert_eq!(group[2].avm1_constants, ["eventValue"]);
    }
    assert_eq!(movie.original, original);
    assert_eq!(movie.normalized, normalized);
}

#[test]
fn absent_register_count_does_not_change_legacy_serialized_shape() {
    let unit = ScriptUnit::default();
    assert_eq!(unit.avm1_register_count, None);
    assert!(
        serde_json::to_value(&unit)
            .unwrap()
            .get("avm1_register_count")
            .is_none()
    );
    let declared = ScriptUnit {
        avm1_register_count: Some(0),
        ..unit
    };
    assert_eq!(
        serde_json::to_value(&declared).unwrap()["avm1_register_count"],
        0
    );
}

#[test]
fn partial_function_scan_keeps_event_context_and_parent_bytes() {
    let mut code = vec![0x06; swf_avm::MAX_DECODED_INSTRUCTIONS];
    code.extend(function2("pastBudget", 1, 0, &[], &[0]));
    code.push(0);
    let movie = movie(&[(34, button2(&[(0x100, &code)]))]);
    let units = inventory(&movie);
    assert_eq!(units.len(), 2);
    assert_eq!(units[0].code, code);
    assert_eq!(units[1].kind, "diagnostic");
    assert_eq!(units[1].event_flags, Some(0x100));
    assert_eq!(units[1].tag_path, units[0].tag_path);
    assert!(
        units[1]
            .diagnostics
            .iter()
            .any(|d| d.code == "script_function_scan_limit")
    );
    assert!(!units.iter().any(|u| u.kind == "function"));
}

#[test]
fn invalid_or_truncated_functions_preserve_input_and_do_not_invent_children() {
    let valid = function2("broken", 1, 0, &[(1, "param")], &[0]);
    let invalid = function2("badRegister", 1, 0, &[(2, "param")], &[0]);
    for code in [&valid[..valid.len() - 1], &valid[..2], &invalid[..]] {
        let movie = movie(&[(12, code.to_vec())]);
        let original = movie.original.clone();
        let normalized = movie.normalized.clone();
        let units = inventory(&movie);
        assert_eq!(units.len(), 1);
        assert_eq!(units[0].code, code);
        assert_eq!(units[0].avm1_register_count, None);
        let decoded = disassemble(&units[0]);
        assert!(decoded.instructions.is_empty());
        assert!(!decoded.diagnostics.is_empty());
        assert_eq!(movie.original, original);
        assert_eq!(movie.normalized, normalized);
    }
}

#[test]
fn truncated_event_records_preserve_the_complete_tag_payload() {
    let code = function2("validButUnreachable", 1, 0, &[], &[0]);
    let mut button = button2(&[(1, &code)]);
    button[6..8].copy_from_slice(&u16::MAX.to_le_bytes());
    let mut clip = clip(&[(1, &code)]);
    clip[13..17].copy_from_slice(&u32::MAX.to_le_bytes());
    for (tag_code, data) in [(34, button), (26, clip)] {
        let movie = movie(&[(tag_code, data.clone())]);
        let original = movie.original.clone();
        let normalized = movie.normalized.clone();
        let units = inventory(&movie);
        assert_eq!(units.len(), 1);
        assert_eq!(units[0].kind, "diagnostic");
        assert_eq!(units[0].code, data);
        assert_eq!(units[0].byte_start, movie.tags[0].data_start);
        assert!(!units[0].diagnostics.is_empty());
        assert_eq!(movie.original, original);
        assert_eq!(movie.normalized, normalized);
    }
}
