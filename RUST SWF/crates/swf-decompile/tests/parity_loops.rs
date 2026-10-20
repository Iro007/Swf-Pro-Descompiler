//! Reducible loop completion tests. ECMAScript oracle excludes AS3-only types.
use std::collections::BTreeMap;
use std::process::Command;
use std::sync::Arc;
use swf_avm::{Abc, ScriptUnit, disassemble};
use swf_decompile::decompile;
#[derive(Default)]
struct Code {
    bytes: Vec<u8>,
    labels: BTreeMap<&'static str, usize>,
    jumps: Vec<(usize, &'static str)>,
}
impl Code {
    fn emit(&mut self, b: &[u8]) {
        self.bytes.extend(b);
    }
    fn label(&mut self, s: &'static str) {
        self.labels.insert(s, self.bytes.len());
    }
    fn jump(&mut self, op: u8, s: &'static str) {
        self.emit(&[op, 0, 0, 0]);
        self.jumps.push((self.bytes.len() - 3, s));
    }
    fn finish(mut self) -> Vec<u8> {
        for (p, s) in self.jumps {
            let rel = self.labels[s] as i32 - (p + 3) as i32;
            self.bytes[p..p + 3].copy_from_slice(&rel.to_le_bytes()[..3]);
        }
        self.bytes
    }
}
fn unit(code: &[u8]) -> ScriptUnit {
    let mut b = vec![
        16, 0, 46, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 16, 16, 0, 0,
    ];
    b.push(code.len() as u8);
    b.extend(code);
    b.extend([0, 0]);
    ScriptUnit {
        id: "loops/method:0".into(),
        name: "sample".into(),
        language: "avm2".into(),
        kind: "method".into(),
        code: code.to_vec(),
        byte_len: code.len(),
        context: Some(Arc::new(Abc::parse(&b).unwrap())),
        method_index: Some(0),
        body_index: Some(0),
        ..Default::default()
    }
}
fn source(code: &[u8]) -> String {
    let r = decompile(&unit(code));
    assert_eq!(r.state, "structured", "{:?}\n{}", r.diagnostics, r.pcode);
    r.source.unwrap()
}
fn execute(text: &str) -> serde_json::Value {
    let script = format!("{text}\nconsole.log(JSON.stringify(sample()));");
    let r = Command::new("node").args(["-e", &script]).output().unwrap();
    assert!(
        r.status.success(),
        "{}\n{script}",
        String::from_utf8_lossy(&r.stderr)
    );
    serde_json::from_slice(&r.stdout).unwrap()
}
fn loop_code(returns: bool, invalid: bool) -> Vec<u8> {
    let mut c = Code::default();
    c.emit(&[0x24, 0, 0xd5]);
    c.label("head");
    c.emit(&[0xd1, 0x24, 5]);
    c.jump(0x18, "exit");
    c.emit(&[0x92, 1]); // inclocal 1
    if invalid {
        c.emit(&[0x20]);
    } // hidden unconsumed stack value with valid branch boundaries
    c.emit(&[0xd1, 0x24, 2]);
    c.jump(0x13, "head"); // conditional continue
    c.emit(&[0xd1, 0x24, 4]);
    c.jump(0x13, "exit"); // conditional break
    if returns {
        c.emit(&[0xd1, 0x24, 3]);
        c.jump(0x14, "tail");
        c.emit(&[0x24, 30, 0x48]);
    }
    c.label("tail");
    c.jump(0x10, "head");
    c.label("exit");
    c.emit(&[0xd1, 0x48]);
    c.finish()
}
#[test]
fn conditional_continue_and_break_preserve_repeated_predicate() {
    let text = source(&loop_code(false, false));
    assert!(text.contains("continue;"));
    assert!(text.matches("break;").count() >= 2);
    assert_eq!(execute(&text), 4);
}
#[test]
fn early_return_inside_reducible_loop_preserves_completion() {
    let text = source(&loop_code(true, false));
    assert!(text.contains("return 30;"));
    assert_eq!(execute(&text), 30);
}
#[test]
fn exit_with_unconsumed_stack_preserves_whole_pcode() {
    let code = loop_code(false, true);
    let u = unit(&code);
    let r = decompile(&u);
    assert!(r.source.is_none());
    assert_eq!(r.pcode, disassemble(&u).pcode);
}
