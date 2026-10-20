//! Ordered static initialization is proved from ABC slots, never method text.
use std::sync::Arc;
use swf_avm::abc::{
    Class, ExceptionInfo, InstanceInfo, MethodInfo, Multiname, Namespace, Script, TraitInfo,
};
use swf_avm::{Abc, ScriptUnit};
use swf_decompile::decompile_document;

fn field(name: u32, slot_id: u32, type_name: u32) -> TraitInfo {
    TraitInfo {
        name,
        kind: 0,
        attributes: 0,
        slot_id,
        type_name,
        value: 0,
        value_kind: 0,
        method: None,
        class: None,
        metadata: vec![],
    }
}
fn fixture(code: Vec<u8>) -> ScriptUnit {
    let bootstrap = vec![
        0xd0, 0x30, 0x65, 0, 0x60, 2, 0x30, 0x60, 2, 0x58, 0, 0x1d, 0x68, 1, 0x47,
    ];
    let mut bytes = vec![16, 0, 46, 0, 1, 1, 1, 3, 7];
    bytes.extend(b"capture");
    bytes.push(5);
    bytes.extend(b"value");
    bytes.extend([
        2,
        0x16,
        0,
        1,
        3,
        7,
        1,
        1,
        7,
        1,
        2,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        16,
        16,
        0,
        1,
        bootstrap.len() as u8,
    ]);
    bytes.extend(&bootstrap);
    bytes.extend([0, 0]);
    let mut abc = Abc::parse(&bytes).unwrap();
    abc.strings = [
        "",
        "StaticProof",
        "Object",
        "int",
        "late",
        "early",
        "arr",
        "alias",
        "readLate",
        "Number",
        "negativeZero",
        "String",
        "text",
    ]
    .iter()
    .map(|s| (*s).into())
    .collect();
    abc.multinames = std::iter::once(Multiname::default())
        .chain((1..=12).map(|name| Multiname {
            kind: 7,
            name,
            namespace: 1,
            ..Default::default()
        }))
        .collect();
    abc.doubles.push((-0.0f64).to_le_bytes());
    abc.bodies[0].max_scope_depth = 2;
    for (method, code) in [(1, vec![0xd0, 0x30, 0xd0, 0x49, 0, 0x47]), (2, code)] {
        abc.methods.push(MethodInfo {
            return_type: 0,
            param_types: vec![],
            name: 0,
            flags: 0,
            options: vec![],
            param_names: vec![],
        });
        let mut body = abc.bodies[0].clone();
        body.method = method;
        body.code = code;
        body.init_scope_depth = 2;
        body.max_scope_depth = 3;
        abc.bodies.push(body);
    }
    abc.instances.push(InstanceInfo {
        name: 1,
        super_name: 2,
        flags: 1,
        protected_namespace: None,
        interfaces: vec![],
        init: 1,
        traits: vec![],
    });
    // Deliberately different from the order of the writes below.
    abc.classes.push(Class {
        init: 2,
        traits: vec![
            field(4, 1, 3),
            field(5, 2, 3),
            field(6, 3, 0),
            field(7, 4, 0),
            field(8, 5, 3),
            field(10, 6, 9),
            field(12, 7, 11),
        ],
    });
    let mut declaration = field(1, 1, 0);
    declaration.kind = 4;
    declaration.class = Some(0);
    abc.scripts.push(Script {
        init: 0,
        traits: vec![declaration],
    });
    ScriptUnit {
        id: "static-proof/abc/method:0".into(),
        name: "bootstrap".into(),
        language: "avm2".into(),
        kind: "method".into(),
        code: bootstrap.clone(),
        byte_len: bootstrap.len(),
        method_index: Some(0),
        body_index: Some(0),
        context: Some(Arc::new(abc)),
        ..Default::default()
    }
}
fn ordered_code() -> Vec<u8> {
    vec![
        0xd0, 0x30, 0xd0, 0xd0, 0x66, 4, 0x24, 7, 0xa0, 0x61, 5, 0xd0, 0x24, 4, 0x61, 4, 0xd0,
        0xd0, 0x66, 4, 0x61, 8, 0xd0, 0x24, 1, 0x24, 2, 0x56, 2, 0x61, 6, 0xd0, 0xd0, 0x66, 6,
        0x61, 7, 0xd0, 0x2f, 1, 0x61, 10, 0xd0, 0x24, 42, 0x70, 0x61, 12, 0x47,
    ]
}
fn assert_fallback(unit: &ScriptUnit) {
    let result = decompile_document(unit);
    assert_eq!(result.state, "pcode_only", "{:?}", result.documents);
    assert!(result.documents.is_empty());
    let abc = unit.context.as_ref().unwrap();
    let mut body = unit.clone();
    body.code = abc.bodies[2].code.clone();
    body.method_index = Some(2);
    body.body_index = Some(2);
    assert_eq!(result.pcode[2].pcode, swf_avm::disassemble(&body).pcode);
}

#[test]
fn static_assignments_preserve_order_defaults_coercions_and_array_identity_without_new_traits() {
    let result = decompile_document(&fixture(ordered_code()));
    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    let source = &result.documents[0].source;
    let block = source.split("// Static initializer").nth(1).unwrap();
    assert!(block.find("public::early =").unwrap() < block.find("public::late =").unwrap());
    assert!(block.contains("public::early = (((StaticProof).public::late) + (7));"));
    assert!(block.contains("public::alias = (StaticProof).public::arr;"));
    assert!(block.contains("public::negativeZero = -0.0;"));
    assert!(block.contains("public::text = ((42) + \"\");"));
    assert_eq!(source.matches("static var ").count(), 7);
    assert!(!block.contains("var "));
    assert_eq!(result.pcode.len(), 3);
}

#[test]
fn repeated_static_writes_are_kept_in_their_original_order() {
    let code = vec![
        0xd0, 0x30, 0xd0, 0x24, 1, 0x61, 4, 0xd0, 0x24, 2, 0x61, 4, 0x47,
    ];
    let result = decompile_document(&fixture(code));
    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    let source = &result.documents[0].source;
    assert!(source.find("public::late = 1;").unwrap() < source.find("public::late = 2;").unwrap());
}

#[test]
fn empty_initializer_does_not_require_an_unused_class_scope() {
    let result = decompile_document(&fixture(vec![0x47]));
    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    assert!(!result.documents[0].source.contains("// Static initializer"));
}

#[test]
fn explicit_slot_ids_and_lexical_owned_slots_are_supported() {
    let code = vec![
        0xd0, 0x30, 0x5e, 4, 0x24, 5, 0x61, 4, 0xd0, 0xd0, 0x6c, 1, 0x6d, 2, 0x47,
    ];
    let result = decompile_document(&fixture(code));
    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    assert!(
        result.documents[0]
            .source
            .contains("public::early = (StaticProof).public::late;")
    );
}

#[test]
fn static_slot_coercions_and_numeric_operators_without_valid_source_types_keep_pcode() {
    // AVM2 coerces a String into an int/Number slot, but AS3 source assignment
    // would reject it. Publishing raw assignment text is therefore insufficient.
    for code in [
        vec![0xd0, 0x30, 0xd0, 0x2c, 4, 0x6d, 1, 0x47],
        vec![0xd0, 0x30, 0xd0, 0x2c, 4, 0x61, 10, 0x47],
        vec![0xd0, 0x30, 0xd0, 0x24, 7, 0x68, 12, 0x47],
    ] {
        assert_fallback(&fixture(code));
    }
    for conversion in [0x73, 0x74, 0x75, 0x90, 0x97] {
        assert_fallback(&fixture(vec![
            0xd0, 0x30, 0xd0, 0xd0, 0x66, 12, conversion, 0x61, 4, 0x47,
        ]));
    }
    for operator in [0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa] {
        assert_fallback(&fixture(vec![
            0xd0, 0x30, 0xd0, 0xd0, 0x66, 12, 0x24, 1, operator, 0x61, 4, 0x47,
        ]));
    }
}

#[test]
fn string_to_int_setslot_coercion_retains_exact_pcode() {
    assert_fallback(&fixture(vec![0xd0, 0x30, 0xd0, 0x2c, 4, 0x6d, 1, 0x47]));
}
#[test]
fn string_to_number_setproperty_coercion_retains_exact_pcode() {
    assert_fallback(&fixture(vec![0xd0, 0x30, 0xd0, 0x2c, 4, 0x61, 10, 0x47]));
}
#[test]
fn number_to_string_initproperty_coercion_retains_exact_pcode() {
    assert_fallback(&fixture(vec![0xd0, 0x30, 0xd0, 0x24, 7, 0x68, 12, 0x47]));
}

#[test]
fn local_scope_zero_is_class_receiver_and_outer_or_uri_names_require_pcode() {
    let code = vec![0xd0, 0x30, 0x65, 0, 0x24, 5, 0x61, 4, 0x47];
    let result = decompile_document(&fixture(code));
    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    assert_fallback(&fixture(vec![0xd0, 0x30, 0x65, 2, 0x24, 5, 0x61, 4, 0x47]));
    assert_fallback(&fixture(vec![0xd0, 0x30, 0x67, 0, 0x24, 5, 0x61, 4, 0x47]));
    let mut unit = fixture(ordered_code());
    let abc = Arc::make_mut(unit.context.as_mut().unwrap());
    abc.strings.push("urn:explicit".into());
    abc.namespaces.push(Namespace { kind: 8, name: 13 });
    abc.multinames[5].namespace = 2;
    assert_fallback(&unit);
    let mut unit = fixture(ordered_code());
    let abc = Arc::make_mut(unit.context.as_mut().unwrap());
    abc.strings.push("urn:unproven-static-protected".into());
    abc.namespaces.push(Namespace {
        kind: 0x1a,
        name: 13,
    });
    abc.multinames[5].namespace = 2;
    assert_fallback(&unit);
}

#[test]
fn static_calls_control_flow_handlers_locals_and_const_writes_keep_exact_pcode() {
    for code in [
        vec![0xd0, 0x30, 0xd0, 0x4f, 4, 0, 0x47],
        vec![0xd0, 0x30, 0x10, 0, 0, 0, 0x47],
        vec![0xd0, 0x30, 0x24, 1, 0xd5, 0x47],
        vec![0xd0, 0x30, 0x47, 0xd0, 0x24, 1, 0x61, 4],
        vec![0xd0, 0x30, 0x24, 1, 0xd0, 0x24, 2, 0x61, 4, 0x29, 0x47],
    ] {
        assert_fallback(&fixture(code));
    }
    let mut unit = fixture(ordered_code());
    Arc::make_mut(unit.context.as_mut().unwrap()).bodies[2]
        .exceptions
        .push(ExceptionInfo {
            from: 2,
            to: 4,
            target: 4,
            exception_type: 0,
            variable_name: 0,
        });
    assert_fallback(&unit);
    let mut unit = fixture(ordered_code());
    Arc::make_mut(unit.context.as_mut().unwrap()).classes[0].traits[1].kind = 6;
    assert_fallback(&unit);
}

#[test]
fn foreign_receivers_unknown_names_ambiguous_names_and_runtime_names_keep_pcode() {
    assert_fallback(&fixture(vec![0xd0, 0x30, 0x20, 0x24, 1, 0x61, 4, 0x47]));
    assert_fallback(&fixture(vec![0xd0, 0x30, 0xd0, 0x24, 1, 0x61, 2, 0x47]));
    let mut unit = fixture(ordered_code());
    let abc = Arc::make_mut(unit.context.as_mut().unwrap());
    abc.namespaces.push(Namespace { kind: 5, name: 0 });
    abc.multinames.push(Multiname {
        kind: 7,
        name: 5,
        namespace: 2,
        ..Default::default()
    });
    abc.classes[0].traits.push(field(13, 8, 3));
    assert_fallback(&unit);
    let mut unit = fixture(ordered_code());
    Arc::make_mut(unit.context.as_mut().unwrap()).multinames[5].kind = 0x1b;
    assert_fallback(&unit);
}

#[test]
fn unproven_array_coercion_and_duplicated_allocations_keep_pcode() {
    assert_fallback(&fixture(vec![
        0xd0, 0x30, 0xd0, 0x56, 0, 0x2a, 0x56, 2, 0x61, 6, 0x47,
    ]));
    assert_fallback(&fixture(vec![
        0xd0, 0x30, 0xd0, 0x56, 0, 0x24, 1, 0xa0, 0x61, 6, 0x47,
    ]));
    // An array stored in an untyped slot must not become a primitive for a
    // later arithmetic expression, which could call an overridden valueOf.
    assert_fallback(&fixture(vec![
        0xd0, 0x30, 0xd0, 0x56, 0, 0x61, 6, 0xd0, 0xd0, 0x66, 6, 0x24, 1, 0xa0, 0x61, 5, 0x47,
    ]));
}

#[test]
fn slot_aliases_scope_metadata_and_expression_growth_are_bounded() {
    let mut unit = fixture(vec![0xd0, 0x30, 0xd0, 0x24, 1, 0x6d, 1, 0x47]);
    Arc::make_mut(unit.context.as_mut().unwrap()).classes[0].traits[1].slot_id = 1;
    assert_fallback(&unit);
    let mut unit = fixture(ordered_code());
    Arc::make_mut(unit.context.as_mut().unwrap()).bodies[2].max_scope_depth = 2;
    assert_fallback(&unit);
    let mut code = vec![0xd0, 0x30, 0xd0, 0x24, 1];
    for _ in 0..20 {
        code.extend([0x2a, 0xa0]);
    }
    code.extend([0x61, 4, 0x47]);
    assert_fallback(&fixture(code));
    let mut unit = fixture(ordered_code());
    Arc::make_mut(unit.context.as_mut().unwrap()).methods[2].return_type = 3;
    assert_fallback(&unit);
    let mut unit = fixture(ordered_code());
    Arc::make_mut(unit.context.as_mut().unwrap()).bodies[2].local_count = 0;
    assert_fallback(&unit);
    assert_fallback(&fixture(vec![0xd0, 0x30, 0xd0, 0x2f, 0, 0x61, 10, 0x47]));
}
