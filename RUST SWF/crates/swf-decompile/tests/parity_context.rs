//! Regression tests for exact binding and typed AS3 reconstruction.
use std::process::Command;
use std::sync::Arc;
use swf_avm::abc::{
    Class, ExceptionInfo, InstanceInfo, MethodInfo, Multiname, Namespace, Script, TraitInfo,
};
use swf_avm::symbols::{AbcSymbolIndex, MethodContext, NamespaceId, TraitOwner};
use swf_avm::{Abc, ScriptUnit, disassemble};
use swf_decompile::{decompile, decompile_document, decompile_with_context};

fn method(code: &[u8], parameters: &[&str]) -> ScriptUnit {
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
        parameters.len() as u8,
        0,
    ]);
    bytes.extend(vec![0; parameters.len()]);
    bytes.extend([0, 0, 0, 0, 0, 1, 0, 16, 16, 0, 1, code.len() as u8]);
    bytes.extend(code);
    bytes.extend([0, 0]);
    ScriptUnit {
        id: "context/abc/method:0".into(),
        name: "sample".into(),
        language: "avm2".into(),
        kind: "method".into(),
        code: code.to_vec(),
        byte_len: code.len(),
        parameters: parameters.iter().map(|s| (*s).into()).collect(),
        context: Some(Arc::new(Abc::parse(&bytes).unwrap())),
        method_index: Some(0),
        body_index: Some(0),
        ..Default::default()
    }
}
fn trait_info(name: u32, kind: u8, method: Option<u32>) -> TraitInfo {
    TraitInfo {
        name,
        kind,
        attributes: 0,
        slot_id: 1,
        type_name: 0,
        value: 0,
        value_kind: 0,
        method,
        class: None,
        metadata: vec![],
    }
}
fn owned(unit: &mut ScriptUnit) {
    let a = Arc::make_mut(unit.context.as_mut().unwrap());
    a.methods.push(MethodInfo {
        return_type: 0,
        param_types: vec![],
        name: 0,
        flags: 0,
        options: vec![],
        param_names: vec![],
    });
    a.instances.push(InstanceInfo {
        name: 1,
        super_name: 0,
        flags: 1,
        protected_namespace: None,
        interfaces: vec![],
        init: 1,
        traits: vec![trait_info(1, 1, Some(0)), trait_info(2, 0, None)],
    });
}
fn source(unit: &ScriptUnit) -> String {
    let r = decompile(unit);
    assert_eq!(r.state, "structured", "{:?}\n{}", r.diagnostics, r.pcode);
    r.source.unwrap()
}
fn fallback(unit: &ScriptUnit) {
    let r = decompile(unit);
    assert!(r.source.is_none(), "{:?}", r.diagnostics);
    assert_eq!(r.pcode, disassemble(unit).pcode);
}
fn node(script: String) -> serde_json::Value {
    let r = Command::new("node").args(["-e", &script]).output().unwrap();
    assert!(
        r.status.success(),
        "{}\n{script}",
        String::from_utf8_lossy(&r.stderr)
    );
    serde_json::from_slice(&r.stdout).unwrap()
}

#[test]
fn owned_lexical_receiver_keeps_argument_getter_before_function_getter() {
    // Lookup this.value; read argument.value; fetch/call this.value.
    let mut u = method(&[0x5d, 2, 0xd1, 0x66, 2, 0x46, 2, 1, 0x48], &["argument"]);
    owned(&mut u);
    let text = source(&u);
    assert!(text.contains("(this).public::value("));
    // These fixture receivers have only ordinary public JS properties.
    let text = text.replace("public::", "");
    assert_eq!(
        node(format!(
            "let effects=[];let receiver={{get value(){{effects.push('function');return function(v){{effects.push(this===receiver?'receiver':'wrong');return v;}};}}}};let arg={{get value(){{effects.push('argument');return 7;}}}};\n{text}\nconsole.log(JSON.stringify([sample.call(receiver,arg),effects]));"
        )),
        serde_json::json!([7, ["argument", "function", "receiver"]])
    );
}

#[test]
fn unknown_lexical_receiver_and_ambiguous_ownership_keep_exact_pcode() {
    let mut u = method(&[0x5d, 2, 0xd1, 0x66, 2, 0x46, 2, 1, 0x48], &["argument"]);
    fallback(&u);
    owned(&mut u);
    Arc::make_mut(u.context.as_mut().unwrap()).instances[0]
        .traits
        .push(trait_info(2, 1, Some(0)));
    fallback(&u);
}

#[test]
fn typed_coerce_preserves_throwing_assignment_and_signature_defaults() {
    let mut u = method(&[0xd1, 0x80, 2, 0x48], &["argument"]);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.strings[2] = "Object".into();
    a.methods[0].param_types[0] = 2;
    a.methods[0].return_type = 2;
    a.methods[0].flags = 8;
    a.methods[0].options = vec![(0, 0x0c)];
    let text = source(&u);
    assert!(text.contains("argument:Object = null"));
    assert!(text.contains("):Object {"));
    assert!(text.contains(":Object ="));
    assert!(!text.contains(" as "));
}

#[test]
fn shadowed_type_and_forged_context_are_rejected() {
    let mut u = method(&[0xd1, 0x80, 2, 0x48], &["value"]);
    fallback(&u);
    u.parameters[0] = "argument".into();
    let mut context = MethodContext::from_unit(&u).unwrap();
    context.optional_defaults.push((0, 0x0c));
    let r = decompile_with_context(&u, &context);
    assert!(r.source.is_none());
    assert!(
        r.diagnostics
            .iter()
            .any(|d| d.code == "INVALID_METHOD_CONTEXT")
    );
}

#[test]
fn private_namespace_entries_and_separate_abcs_never_merge() {
    let p = NamespaceId {
        abc_identity: "abc1".into(),
        pool_index: 1,
        kind: 5,
        uri: "".into(),
    };
    let mut q = p.clone();
    q.pool_index = 2;
    assert!(!p.same_binding(&q));
    q.pool_index = 1;
    q.abc_identity = "abc2".into();
    assert!(!p.same_binding(&q));
    let mut public = p.clone();
    public.kind = 0x16;
    let mut alias = q;
    alias.kind = 0x08;
    assert!(public.same_binding(&alias));
}

#[test]
fn mixed_namespace_set_resolves_only_the_exact_owned_trait() {
    let mut u = method(&[0x47], &[]);
    owned(&mut u);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.namespaces.push(Namespace { kind: 5, name: 0 });
    a.ns_sets.push(vec![1, 2]);
    a.multinames.push(Multiname {
        kind: 9,
        name: 2,
        ns_set: 1,
        ..Default::default()
    });
    let index = AbcSymbolIndex::new(a, "abc".into());
    let binding = index.resolve(a, 3, TraitOwner::Instance(0)).unwrap();
    assert_eq!(binding.name.namespace.kind, 0x16);
    a.multinames.push(Multiname {
        kind: 7,
        name: 2,
        namespace: 2,
        ..Default::default()
    });
    a.instances[0].traits.push(trait_info(4, 0, None));
    assert!(
        AbcSymbolIndex::new(a, "abc".into())
            .resolve(a, 3, TraitOwner::Instance(0))
            .is_none()
    );
}

fn closure_unit() -> ScriptUnit {
    let mut u = method(
        &[
            0xd0, 0x30, 0x57, 0x2a, 0xd5, 0x30, 0xd1, 0x24, 0, 0x6d, 1, 0x40, 1, 0x48,
        ],
        &[],
    );
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.methods[0].flags = 2;
    a.namespaces.push(Namespace { kind: 5, name: 0 });
    a.multinames[2].namespace = 2;
    a.bodies[0].max_scope_depth = 2;
    a.bodies[0].traits.push(trait_info(2, 0, None));
    a.methods.push(MethodInfo {
        return_type: 0,
        param_types: vec![],
        name: 0,
        flags: 0,
        options: vec![],
        param_names: vec![],
    });
    let mut body = a.bodies[0].clone();
    body.method = 1;
    body.init_scope_depth = 2;
    body.max_scope_depth = 2;
    body.traits.clear();
    body.code = vec![
        0x67, 1, 0x2a, 0x6c, 1, 0xc0, 0x6d, 1, 0x67, 1, 0x6c, 1, 0x48,
    ];
    a.bodies.push(body);
    u
}
#[test]
fn returned_closure_shares_mutable_activation_cell_and_distinct_calls_isolate_it() {
    let u = closure_unit();
    let text = source(&u);
    assert!(!text.contains("value:"));
    assert_eq!(
        node(format!(
            "{text}\nlet first=sample();let second=sample();console.log(JSON.stringify([first(),first(),second(),first()]));"
        )),
        serde_json::json!([1, 2, 1, 3])
    );
}
#[test]
fn exact_private_activation_lexical_name_resolves_without_public_flattening() {
    let mut u = closure_unit();
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.bodies[1].code = vec![0x60, 2, 0x48];
    let text = source(&u);
    assert_eq!(
        node(format!("{text}\nconsole.log(JSON.stringify(sample()()));")),
        0
    );
}
#[test]
fn escaped_activation_and_inconsistent_capture_depth_retain_exact_pcode() {
    let mut u = closure_unit();
    u.code = vec![0x57, 0x48];
    Arc::make_mut(u.context.as_mut().unwrap()).bodies[0].code = u.code.clone();
    fallback(&u);
    let mut u = closure_unit();
    Arc::make_mut(u.context.as_mut().unwrap()).bodies[1].init_scope_depth = 1;
    fallback(&u);
    let mut u = closure_unit();
    Arc::make_mut(u.context.as_mut().unwrap()).bodies[0].traits[0].slot_id = 0;
    fallback(&u);
}

fn class_unit() -> ScriptUnit {
    let code = [
        0xd0, 0x30, 0x65, 0, 0x60, 2, 0x30, 0x60, 2, 0x58, 0, 0x1d, 0x68, 1, 0x47,
    ];
    let mut u = method(&code, &[]);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.strings[1] = "SampleClass".into();
    a.strings[2] = "Object".into();
    a.strings.extend(["secret".into(), "read".into()]);
    a.namespaces.push(Namespace { kind: 5, name: 0 });
    a.multinames.push(Multiname {
        kind: 7,
        name: 3,
        namespace: 2,
        ..Default::default()
    });
    a.multinames.push(Multiname {
        kind: 7,
        name: 4,
        namespace: 1,
        ..Default::default()
    });
    a.bodies[0].max_scope_depth = 2;
    for (n, code) in [
        (1, vec![0xd0, 0x30, 0xd0, 0x49, 0, 0x47]),
        (2, vec![0xd0, 0x30, 0x47]),
        (3, vec![0xd0, 0x66, 3, 0x48]),
    ] {
        a.methods.push(a.methods[0].clone());
        let mut b = a.bodies[0].clone();
        b.method = n;
        b.code = code;
        b.init_scope_depth = 2;
        b.max_scope_depth = 3;
        a.bodies.push(b);
    }
    a.instances.push(InstanceInfo {
        name: 1,
        super_name: 2,
        flags: 1,
        protected_namespace: None,
        interfaces: vec![],
        init: 1,
        traits: vec![trait_info(3, 0, None), trait_info(4, 1, Some(3))],
    });
    a.classes.push(Class {
        init: 2,
        traits: vec![],
    });
    let mut t = trait_info(1, 4, None);
    t.class = Some(0);
    a.scripts.push(Script {
        init: 0,
        traits: vec![t],
    });
    u
}
#[test]
fn complete_class_document_preserves_private_owner_and_bootstrap_order() {
    let u = class_unit();
    let r = decompile_document(&u);
    assert_eq!(r.state, "structured", "{:?}", r.diagnostics);
    assert_eq!(r.initialization_order, [0]);
    assert_eq!(r.documents[0].path, "SampleClass.as");
    let source = &r.documents[0].source;
    assert!(source.contains("private var secret:*;"));
    assert!(source.contains("public function SampleClass("));
    assert!(source.contains("(this).secret"));
    assert_eq!(r.pcode.len(), 4);
    assert!(r.pcode.iter().all(|r| r.source.is_none()));
    // The same private method is not published without its class namespace.
    let a = u.context.clone().unwrap();
    let mut private = u.clone();
    private.code = a.bodies[3].code.clone();
    private.method_index = Some(3);
    private.body_index = Some(3);
    fallback(&private);
}
#[test]
fn complete_document_rejects_unowned_body_or_noncanonical_bootstrap() {
    let mut u = class_unit();
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    let mut extra = a.bodies[2].clone();
    extra.method = 4;
    a.methods.push(a.methods[2].clone());
    a.bodies.push(extra);
    let r = decompile_document(&u);
    assert_eq!(r.state, "pcode_only");
    assert!(r.documents.is_empty());
    let mut u = class_unit();
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.bodies[0].code.insert(0, 0x20);
    let r = decompile_document(&u);
    assert_eq!(r.state, "pcode_only");
    assert!(r.documents.is_empty());
}

fn catch_unit() -> ScriptUnit {
    let code = [
        0xd0, 0x30, 0xd1, 0x66, 2, 0xd6, 0x10, 22, 0, 0, 0xd0, 0x30, 0x5a, 0, 0x2a, 0xd7, 0x2a,
        0x30, 0x2b, 0x6d, 1, 0xd3, 0x6c, 1, 0xd6, 0x1d, 0x08, 3, 0x10, 0, 0, 0, 0xd2, 0x48,
    ];
    let mut u = method(&code, &["argument"]);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.bodies[0].max_scope_depth = 2;
    a.bodies[0].exceptions.push(ExceptionInfo {
        from: 2,
        to: 6,
        target: 10,
        exception_type: 0,
        variable_name: 1,
    });
    u
}
#[test]
fn single_catch_preserves_throwing_getter_and_scope_reset() {
    let text = source(&catch_unit());
    assert!(text.contains("catch (__rustswf_caught0:*)"));
    // Erasing the catch-all AS3 annotation is valid for this ECMAScript subset.
    let js = text.replace("__rustswf_caught0:*)", "__rustswf_caught0)");
    assert_eq!(
        node(format!(
            "{js}\nconsole.log(JSON.stringify([sample({{value:7}}),sample({{get value(){{throw 13;}}}})]));"
        )),
        serde_json::json!([7, 13])
    );
}
#[test]
fn nested_handlers_overlapping_boundaries_and_catch_escape_keep_pcode() {
    let mut u = catch_unit();
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    let exception = a.bodies[0].exceptions[0].clone();
    a.bodies[0].exceptions.push(exception);
    fallback(&u);
    let mut u = catch_unit();
    Arc::make_mut(u.context.as_mut().unwrap()).bodies[0].exceptions[0].to = 10;
    fallback(&u);
    let mut u = catch_unit();
    u.code[24] = 0x48;
    Arc::make_mut(u.context.as_mut().unwrap()).bodies[0].code = u.code.clone();
    fallback(&u);
}

#[test]
fn activation_early_reads_use_declared_slot_defaults_before_any_assignment() {
    let mut code = vec![0x57, 0xd5];
    for slot in 1..=7 {
        code.extend([0xd1, 0x6c, slot]);
    }
    code.extend([0x56, 7, 0x48]);
    let mut u = method(&code, &[]);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.methods[0].flags = 2;
    a.namespaces.push(Namespace { kind: 5, name: 0 });
    for (n, ty) in ["int", "uint", "Boolean", "Object", "String", "*", "Number"]
        .iter()
        .enumerate()
    {
        a.strings.push(format!("cell{n}"));
        let name = a.strings.len() - 1;
        a.multinames.push(Multiname {
            kind: 7,
            name: name as u32,
            namespace: 2,
            ..Default::default()
        });
        let name_index = a.multinames.len() - 1;
        let type_name = if *ty == "*" {
            0
        } else {
            a.strings.push((*ty).into());
            let name = a.strings.len() - 1;
            a.multinames.push(Multiname {
                kind: 7,
                name: name as u32,
                namespace: 1,
                ..Default::default()
            });
            a.multinames.len() - 1
        };
        let mut t = trait_info(name_index as u32, 0, None);
        t.slot_id = (n + 1) as u32;
        t.type_name = type_name as u32;
        a.bodies[0].traits.push(t);
    }
    let text = source(&u);
    assert_eq!(
        node(format!(
            "{text}\nlet d=sample();console.log(JSON.stringify([d[0],d[1],d[2],d[3]===null,d[4]===null,typeof d[5],Number.isNaN(d[6])]));"
        )),
        serde_json::json!([0, 0, false, true, true, "undefined", true])
    );
}
#[test]
fn local_scope_indices_are_independent_of_inherited_depth_and_unknown_outer_stays_pcode() {
    let mut u = method(&[0xd0, 0x30, 0x65, 0, 0x66, 2, 0x48], &[]);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.bodies[0].init_scope_depth = 3;
    a.bodies[0].max_scope_depth = 4;
    let text = source(&u);
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify(sample.call({{value:9}})));"
        )),
        9
    );
    let mut u = method(&[0x67, 0, 0x48], &[]);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.bodies[0].init_scope_depth = 3;
    a.bodies[0].max_scope_depth = 3;
    fallback(&u);
}
#[test]
fn type_expansion_and_symbol_text_budgets_fail_closed() {
    let mut u = method(&[0xd1, 0x48], &["argument"]);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.multinames.push(Multiname {
        kind: 0x1d,
        name: 2,
        parameters: vec![2; 300],
        ..Default::default()
    });
    a.methods[0].param_types[0] = 3;
    fallback(&u);
    let mut u = method(&[0x47], &[]);
    owned(&mut u);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.strings[2] = "x".repeat(9 * 1024 * 1024);
    assert!(!AbcSymbolIndex::new(a, "budget".into()).complete);
    fallback(&u);
}

#[test]
fn nonstrict_missing_lookup_does_not_turn_undefined_into_reference_error() {
    let u = method(&[0x5e, 2, 0x66, 2, 0x48], &[]);
    fallback(&u);
    let r = decompile(&u);
    assert!(
        r.diagnostics
            .iter()
            .any(|d| d.code == "UNPROVEN_LOOKUP_BINDING")
    );
}
#[test]
fn private_member_type_shadow_removes_typed_signature_and_coercion_source() {
    let mut u = method(&[0xd1, 0x80, 2, 0x48], &["argument"]);
    owned(&mut u);
    let a = Arc::make_mut(u.context.as_mut().unwrap());
    a.strings[2] = "Number".into();
    a.namespaces.push(Namespace { kind: 5, name: 0 });
    a.multinames.push(Multiname {
        kind: 7,
        name: 2,
        namespace: 2,
        ..Default::default()
    });
    a.instances[0].traits[1].name = 3;
    a.methods[0].return_type = 2;
    fallback(&u);
    let r = decompile(&u);
    assert!(
        r.diagnostics
            .iter()
            .any(|d| d.code == "SHADOWED_TYPE_BINDING")
    );
}
