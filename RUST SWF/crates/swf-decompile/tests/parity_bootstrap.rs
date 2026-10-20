//! Independent review: a declaration document must preserve captured scope.
use std::sync::Arc;
use swf_avm::abc::{Class, InstanceInfo, MethodInfo, Multiname, Script, TraitInfo};
use swf_avm::{Abc, ScriptUnit};
use swf_decompile::decompile_document;

fn method(code: &[u8]) -> ScriptUnit {
    let mut bytes = vec![16, 0, 46, 0, 1, 1, 1, 3, 7];
    bytes.extend(b"capture");
    bytes.push(5);
    bytes.extend(b"value");
    bytes.extend([2, 0x16, 0, 1, 3, 7, 1, 1, 7, 1, 2, 1, 0, 0]);
    bytes.extend([0, 0, 0, 0, 0, 1, 0, 16, 16, 0, 1, code.len() as u8]);
    bytes.extend(code);
    bytes.extend([0, 0]);
    ScriptUnit { id: "review/abc/method:0".into(), name: "sample".into(), language: "avm2".into(), kind: "method".into(),
        code: code.to_vec(), byte_len: code.len(), context: Some(Arc::new(Abc::parse(&bytes).unwrap())),
        method_index: Some(0), body_index: Some(0), ..Default::default() }
}

fn declaration(extra_scope: bool) -> ScriptUnit {
    // Object-derived class. The optional String scope is genuinely extra;
    // captured method scope metadata is adjusted so depth alone cannot reject it.
    let mut code = vec![0xd0, 0x30, 0x65, 0];
    if extra_scope { code.extend([0x60, 3, 0x30]); }
    code.extend([0x60, 2, 0x30, 0x60, 2, 0x58, 0, 0x1d]);
    if extra_scope { code.push(0x1d); }
    code.extend([0x68, 1, 0x47]);
    let mut unit = method(&code);
    let abc = Arc::make_mut(unit.context.as_mut().unwrap());
    abc.strings[1] = "ReviewClass".into(); abc.strings[2] = "Object".into();
    abc.strings.push("String".into());
    abc.multinames.push(Multiname { kind:7, name:3, namespace:1, ..Default::default() });
    abc.bodies[0].max_scope_depth = if extra_scope {3} else {2};
    for (index, body_code) in [(1, vec![0xd0,0x30,0xd0,0x49,0,0x47]),(2,vec![0xd0,0x30,0x47])] {
        abc.methods.push(MethodInfo { return_type:0,param_types:vec![],name:0,flags:0,options:vec![],param_names:vec![] });
        let mut body = abc.bodies[0].clone();body.method=index;body.code=body_code;
        body.init_scope_depth=if extra_scope {3} else {2};body.max_scope_depth=body.init_scope_depth+1;
        abc.bodies.push(body);
    }
    abc.instances.push(InstanceInfo { name:1,super_name:2,flags:1,protected_namespace:None,interfaces:vec![],init:1,traits:vec![] });
    abc.classes.push(Class { init:2,traits:vec![] });
    abc.scripts.push(Script { init:0,traits:vec![TraitInfo { name:1,kind:4,attributes:0,slot_id:1,type_name:0,value:0,value_kind:0,
        method:None,class:Some(0),metadata:vec![] }] });
    unit
}

#[test]
fn canonical_bootstrap_still_produces_a_complete_document() {
    let result = decompile_document(&declaration(false));
    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    assert_eq!(result.initialization_order, [0]);
}

#[test]
fn unrelated_middle_scope_is_retained_as_pcode_even_with_consistent_depths() {
    let result = decompile_document(&declaration(true));
    assert_eq!(result.state, "pcode_only", "Extra captured String scope was silently omitted: {:?}", result.documents);
    assert!(result.documents.is_empty());
    assert_eq!(result.pcode.len(), 3);
    assert!(result.pcode[0].pcode.contains("String"));
}
