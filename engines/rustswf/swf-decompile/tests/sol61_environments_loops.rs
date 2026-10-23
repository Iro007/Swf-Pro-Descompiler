//! Entry-order and implicit-register regressions for real compiler patterns.
use std::process::Command;
use std::sync::Arc;
use swf_avm::{Abc, ScriptUnit, disassemble};
use swf_decompile::decompile;

fn method(code: &[u8], parameters: &[&str], flags: u8) -> ScriptUnit {
    // A parsed minimal ABC supplies two public names: capture and value.
    let mut bytes = vec![16, 0, 46, 0, 1, 1, 1, 3, 7];
    bytes.extend(b"capture");
    bytes.push(5);
    bytes.extend(b"value");
    bytes.extend([2, 0x16, 0, 1, 3, 7, 1, 1, 7, 1, 2]);
    bytes.push(1);
    bytes.push(parameters.len() as u8);
    bytes.push(0);
    bytes.extend(vec![0; parameters.len()]);
    bytes.extend([0, flags]);
    bytes.extend([0, 0, 0, 1, 0, 16, 16, 0, 1, code.len() as u8]);
    bytes.extend(code);
    bytes.extend([0, 0]);
    let abc = Arc::new(Abc::parse(&bytes).expect("valid minimal ABC fixture"));
    ScriptUnit {
        id: "sol61-regression".into(),
        name: "sample".into(),
        language: "avm2".into(),
        kind: "method".into(),
        code: code.to_vec(),
        byte_len: code.len(),
        parameters: parameters.iter().map(|p| (*p).into()).collect(),
        context: Some(abc),
        method_index: Some(0),
        body_index: Some(0),
        ..Default::default()
    }
}

fn source(unit: &ScriptUnit) -> String {
    let result = decompile(unit);
    assert_eq!(
        result.state, "structured",
        "{:?}\n{}",
        result.diagnostics, result.pcode
    );
    result.source.unwrap()
}

fn fallback(unit: &ScriptUnit) {
    let result = decompile(unit);
    assert_eq!(result.state, "pcode_only", "{:?}", result.diagnostics);
    assert!(result.source.is_none());
    assert_eq!(result.pcode, disassemble(unit).pcode);
}

fn node(script: String) -> serde_json::Value {
    let output = Command::new("node")
        .args(["-e", &script])
        .output()
        .expect("Node semantic fixture");
    assert!(
        output.status.success(),
        "{}\n{script}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

#[test]
fn arguments_register_is_an_entry_snapshot_and_is_independent_of_parameter_assignments() {
    // getlocal2(arguments), setlocal3; overwrite formal1; return saved arguments.
    let code = [0xd2, 0xd7, 0x24, 99, 0xd5, 0xd3, 0x48];
    let text = source(&method(&code, &["formal"], 1));
    // AVM2 arguments is an Array. JS's non-strict arguments aliases formals,
    // so execute this oracle in strict mode where that aliasing is absent.
    let value = node(format!(
        "'use strict';\n{text}\nlet values=sample(7,8,9);console.log(JSON.stringify(Array.from(values)));"
    ));
    assert_eq!(value, serde_json::json!([7, 8, 9]));
}

#[test]
fn rest_register_contains_only_extra_arguments_and_remains_assignable() {
    let text = source(&method(&[0xd2, 0x48], &["formal"], 4));
    assert!(text.contains("...__rustswf_rest"));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify(sample(7,8,9)));"
        )),
        serde_json::json!([8, 9])
    );
    let text = source(&method(&[0x24, 31, 0xd6, 0xd2, 0x48], &["formal"], 4));
    assert_eq!(
        node(format!(
            "{text}\nconsole.log(JSON.stringify(sample(7,8,9)));"
        )),
        31
    );
}

#[test]
fn ambiguous_or_missing_implicit_environment_preserves_complete_pcode() {
    fallback(&method(&[0xd2, 0x48], &["arguments"], 1));
    fallback(&method(&[0xd2, 0x48], &["formal"], 5));
    fallback(&method(&[0xd2, 0x48], &["formal"], 2));
    let mut unit = method(&[0xd2, 0x48], &["formal"], 1);
    Arc::make_mut(unit.context.as_mut().unwrap()).bodies[0].local_count = 2;
    fallback(&unit);
    let mut unit = method(&[0xd2, 0x48], &["formal"], 1);
    Arc::make_mut(unit.context.as_mut().unwrap()).bodies[0].code = vec![0x47];
    fallback(&unit);
}

fn rotated_loop() -> Vec<u8> {
    // Initial jump to predicate; body.value(); predicate.value(); iftrue body.
    vec![
        0x10, 5, 0, 0, 0xd2, 0x46, 2, 0, 0x29, 0xd1, 0x46, 2, 0, 0x11, 0xf3, 0xff, 0xff, 0x47,
    ]
}

#[test]
fn rotated_while_evaluates_predicate_before_body_and_again_on_each_back_edge() {
    let text = source(&method(&rotated_loop(), &["predicate", "body"], 0));
    assert_eq!(
        node(format!(
            "let effects=[];let count=0;let predicate={{value(){{effects.push('predicate');return count++<2;}}}};let body={{value(){{effects.push('body');}}}};\n{text}\nsample(predicate,body);console.log(JSON.stringify(effects));"
        )),
        serde_json::json!(["predicate", "body", "predicate", "body", "predicate"])
    );
}

#[test]
fn rotated_while_does_not_execute_body_when_first_predicate_is_false() {
    let text = source(&method(&rotated_loop(), &["predicate", "body"], 0));
    assert_eq!(
        node(format!(
            "let effects=[];\n{text}\nsample({{value(){{effects.push('predicate');return false;}}}},{{value(){{effects.push('body');}}}});console.log(JSON.stringify(effects));"
        )),
        serde_json::json!(["predicate"])
    );
}

#[test]
fn body_entry_or_unbalanced_rotated_loop_preserves_complete_pcode() {
    let mut code = rotated_loop();
    code[1] = 0; // A do/while entry is not the supported predicate-first shape.
    fallback(&method(&code, &["predicate", "body"], 0));
    let mut code = rotated_loop();
    code[8] = 0x02; // Nop instead of pop, leaving an unresolved loop stack value.
    fallback(&method(&code, &["predicate", "body"], 0));
}

fn enumeration() -> Vec<u8> {
    // Fresh iterator index3=0, object4=formal1; nextname(object4,index3)
    // feeds key2; hasnext2 resumes before each body and both hidden locals die.
    vec![
        0x24, 0, 0xd7, 0xd1, 0x63, 4, 0x10, 12, 0, 0, 0x62, 4, 0xd3, 0x1e, 0x82, 0xd6, 0x5d, 1,
        0xd2, 0x4f, 1, 1, 0x32, 4, 3, 0x11, 0xed, 0xff, 0xff, 0x08, 4, 0x08, 3, 0x47,
    ]
}

fn value_enumeration(cast: &[u8], extra_body: &[u8], exit: &[u8]) -> Vec<u8> {
    let mut code = vec![0x24, 0, 0xd7, 0xd1, 0x63, 4, 0x10, 0, 0, 0];
    let body = code.len();
    code.extend([0x62, 4, 0xd3, 0x23]); // nextvalue(object4, index3).
    code.extend(cast);
    code.extend([0xd6, 0x5d, 1, 0xd2, 0x4f, 1, 1]);
    code.extend(extra_body);
    let predicate = code.len();
    code.extend([0x32, 4, 3, 0x11, 0, 0, 0]);
    code.extend(exit);
    code.push(0x47);
    let forward = (predicate as i32 - 10).to_le_bytes();
    code[7..10].copy_from_slice(&forward[..3]);
    let backward = (body as i32 - (predicate as i32 + 7)).to_le_bytes();
    code[predicate + 4..predicate + 7].copy_from_slice(&backward[..3]);
    code
}

// JavaScript has no AS3 `for each`. For ordinary enumerable objects used in
// these semantic fixtures, lower ONLY its header to a key loop plus one eager
// value read. The emitted AS3 is separately checked by the real Flex compiler.
fn value_enumeration_oracle(text: &str) -> String {
    text.lines()
        .map(|line| {
            if let Some((indent, header)) = line.split_once("for each (var ") {
                let header = header.strip_suffix(") {").unwrap();
                let (value, object) = header.split_once(" in ").unwrap();
                format!("{indent}for (var __oracle_key in {object}) {{ var {value} = {object}[__oracle_key];")
            } else {
                line.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn canonical_nextvalue_emits_native_for_each_with_eager_inherited_value_reads() {
    let text = source(&method(
        &value_enumeration(&[0x82], &[], &[0x08, 4, 0x08, 3]),
        &["object"],
        0,
    ));
    assert!(text.contains("for each (var __rustswf_value"));
    assert!(!text.contains("Object.keys"));
    assert!(!text.contains("Object.values"));
    assert_eq!(
        node(format!(
            "let effects=[];let parent={{}};Object.defineProperty(parent,'inherited',{{enumerable:true,get(){{effects.push('inherited getter');return 3;}}}});let object=Object.create(parent);Object.defineProperty(object,'first',{{enumerable:true,get(){{effects.push('first getter');return 1;}}}});object.second=2;function capture(value){{effects.push(value);}}\n{}\nsample(object);console.log(JSON.stringify(effects));",
            value_enumeration_oracle(&text)
        )),
        serde_json::json!(["first getter", 1, 2, "inherited getter", 3])
    );
}

#[test]
fn nextvalue_preserves_reference_identity_and_does_not_snapshot_future_values() {
    let text = source(&method(
        &value_enumeration(&[0x82], &[], &[0x08, 4, 0x08, 3]),
        &["object"],
        0,
    ));
    assert_eq!(
        node(format!(
            "let first={{}},replacement={{}};let object={{first,second:2}};let effects=[];function capture(value){{effects.push(value===first?'first':value===replacement?'replacement':value);object.second=replacement;}}\n{}\nsample(object);console.log(JSON.stringify(effects));",
            value_enumeration_oracle(&text)
        )),
        serde_json::json!(["first", "replacement"])
    );
}

#[test]
fn nextvalue_empty_object_and_explicit_numeric_cast_keep_semantics() {
    let text = source(&method(
        &value_enumeration(&[0x73], &[], &[0x08, 4, 0x08, 3]),
        &["object"],
        0,
    ));
    assert!(text.contains(" | 0)"));
    assert_eq!(
        node(format!(
            "let effects=[];function capture(value){{effects.push(value);}}\n{}\nsample({{}});sample({{first:{{valueOf(){{effects.push('cast');return 4294967297.5;}}}}}});console.log(JSON.stringify(effects));",
            value_enumeration_oracle(&text)
        )),
        serde_json::json!(["cast", 1])
    );
}

#[test]
fn nextvalue_exposed_mutated_nonzero_or_live_hidden_registers_retain_exact_pcode() {
    for (position, replacement) in [(1, 1), (18, 0xd3), (15, 0xd7), (29, 0x02)] {
        let mut code = value_enumeration(&[0x82], &[], &[0x08, 4, 0x08, 3]);
        code[position] = replacement;
        fallback(&method(&code, &["object"], 0));
    }
    for extra in [vec![0x62, 4, 0x29], vec![0xd3, 0x29], vec![0x08, 3]] {
        fallback(&method(
            &value_enumeration(&[0x82], &extra, &[0x08, 4, 0x08, 3]),
            &["object"],
            0,
        ));
    }
    // A value opcode alone never establishes enumeration state.
    fallback(&method(&[0xd1, 0x24, 1, 0x23, 0x48], &["object"], 0));
}

#[test]
fn nextvalue_unhandled_cast_and_changed_scope_retain_exact_pcode() {
    // Coerce to an arbitrary named type cannot be omitted as a no-op.
    fallback(&method(
        &value_enumeration(&[0x80, 2], &[], &[0x08, 4, 0x08, 3]),
        &["object"],
        0,
    ));
    // A lexical scope pushed by the body survives into the next predicate.
    fallback(&method(
        &value_enumeration(&[0x82], &[0xd0, 0x30], &[0x08, 4, 0x08, 3]),
        &["object"],
        0,
    ));
    // The body must consume every value before the iterator resumes.
    fallback(&method(
        &value_enumeration(&[0x82], &[0x24, 1], &[0x08, 4, 0x08, 3]),
        &["object"],
        0,
    ));
}

#[test]
fn canonical_enumeration_uses_native_for_in_and_visits_inherited_keys() {
    let text = source(&method(&enumeration(), &["object"], 0));
    assert!(text.contains("for (var __rustswf_key"));
    assert!(!text.contains("Object.keys"));
    assert_eq!(
        node(format!(
            "let effects=[];function capture(key){{effects.push(key);}}let parent={{inherited:3}};let object=Object.create(parent);object.first=1;object.second=2;\n{text}\nsample(object);console.log(JSON.stringify(effects));"
        )),
        serde_json::json!(["first", "second", "inherited"])
    );
}

#[test]
fn enumeration_of_empty_object_does_not_run_the_body() {
    let text = source(&method(&enumeration(), &["object"], 0));
    assert_eq!(
        node(format!(
            "let effects=[];function capture(key){{effects.push(key);}}\n{text}\nsample({{}});console.log(JSON.stringify(effects));"
        )),
        serde_json::json!([])
    );
}

#[test]
fn nonzero_exposed_mutated_or_live_iterator_registers_preserve_complete_pcode() {
    for (position, replacement) in [(1, 1), (18, 0xd3), (15, 0xd7), (29, 0x02), (13, 0x1f)] {
        let mut code = enumeration();
        code[position] = replacement;
        fallback(&method(&code, &["object"], 0));
    }
}

#[test]
#[ignore = "Optional real Flex CLI compiler verifies arguments/rest flags and native enumeration opcodes"]
fn environments_and_enumeration_compile_to_native_avm2_constructs() {
    let java = std::env::var_os("RUSTSWF_FLEX_JAVA").expect("Set RUSTSWF_FLEX_JAVA");
    let compiler = std::env::var_os("RUSTSWF_MXMLC_JAR").expect("Set RUSTSWF_MXMLC_JAR");
    let player =
        std::env::var_os("RUSTSWF_PLAYERGLOBAL_HOME").expect("Set RUSTSWF_PLAYERGLOBAL_HOME");
    let directory =
        std::env::temp_dir().join(format!("rustswf-environments-sdk-{}", std::process::id()));
    std::fs::create_dir_all(&directory).unwrap();
    for (case, fixture, flag) in [
        ("arguments", method(&[0xd2, 0x48], &["formal"], 1), 1),
        ("rest", method(&[0xd2, 0x48], &["formal"], 4), 4),
        ("enumeration", method(&enumeration(), &["object"], 0), 0),
        (
            "value_enumeration",
            method(
                &value_enumeration(&[0x82], &[], &[0x08, 4, 0x08, 3]),
                &["object"],
                0,
            ),
            0,
        ),
    ] {
        let text = source(&fixture);
        let source_file = directory.join("RustSWFEnvironmentProbe.as");
        let output_file = directory.join(format!("{case}.swf"));
        std::fs::write(&source_file, format!("package {{ import flash.display.Sprite; public class RustSWFEnvironmentProbe extends Sprite {{ public function capture(key):void {{}}\n{text}\n}} }}")).unwrap();
        let mut command = Command::new(&java);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let output = command
            .args(["-Djava.awt.headless=true", "-jar"])
            .arg(&compiler)
            .arg(format!(
                "+flexlib={}",
                std::path::Path::new(&compiler)
                    .parent()
                    .unwrap()
                    .parent()
                    .unwrap()
                    .join("frameworks")
                    .display()
            ))
            .arg(format!(
                "+playerglobalHome={}",
                std::path::Path::new(&player).display()
            ))
            .args([
                "-target-player=32.0",
                "-static-link-runtime-shared-libraries=true",
                "-debug=false",
            ])
            .arg(format!("-output={}", output_file.display()))
            .arg(&source_file)
            .env("PLAYERGLOBAL_HOME", &player)
            .current_dir(&directory)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{case}\n{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let movie = swf_core::Movie::read(&output_file).unwrap();
        let units = swf_avm::inventory(&movie);
        let unit = units
            .iter()
            .find(|unit| unit.name == "sample")
            .expect("compiled sample method");
        let abc = unit.context.as_ref().unwrap();
        assert_eq!(
            abc.methods[unit.method_index.unwrap()].flags & 5,
            flag,
            "{case}"
        );
        if case == "enumeration" || case == "value_enumeration" {
            let instructions = disassemble(unit).instructions;
            assert_eq!(
                instructions.iter().filter(|i| i.name == "hasnext2").count(),
                1
            );
            if case == "value_enumeration" {
                assert!(source(unit).contains("for each (var "));
            }
            assert_eq!(
                instructions
                    .iter()
                    .filter(|i| i.name
                        == if case == "enumeration" {
                            "nextname"
                        } else {
                            "nextvalue"
                        })
                    .count(),
                1
            );
        }
    }
}

#[test]
#[ignore = "Optional functional readback of repository compiler corpus; no timing benchmark"]
fn repository_corpus_readback_retains_exact_pcode_for_every_unit() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../corpus");
    let mut files = std::fs::read_dir(&root)
        .unwrap()
        .chain(std::fs::read_dir(root.join("cloud-matched-micro-20261013")).unwrap())
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "swf"))
        .collect::<Vec<_>>();
    files.sort();
    for path in files {
        let movie = swf_core::Movie::read(&path).unwrap();
        let units = swf_avm::inventory(&movie);
        let mut structured = 0;
        let mut value_enumeration_units = 0;
        let mut structured_value_enumeration_units = 0;
        let mut diagnostics = std::collections::BTreeMap::new();
        for unit in &units {
            let result = decompile(unit);
            let bytecode = disassemble(unit);
            assert_eq!(result.pcode, bytecode.pcode);
            let has_nextvalue = bytecode.instructions.iter().any(|i| i.name == "nextvalue");
            if has_nextvalue {
                value_enumeration_units += 1;
                if result.source.is_some() {
                    structured_value_enumeration_units += 1;
                } else if std::env::var_os("RUSTSWF_INSPECT_NEXTVALUE").is_some() {
                    println!(
                        "nextvalue {} [{}] {}: {:?}",
                        unit.name,
                        unit.id,
                        result
                            .pcode
                            .lines()
                            .find(|line| line.contains("debugfile"))
                            .unwrap_or(""),
                        result.diagnostics
                    );
                }
            }
            if result.source.is_some() {
                structured += 1;
            } else if let Some(diagnostic) = result.diagnostics.last() {
                *diagnostics.entry(diagnostic.code.clone()).or_insert(0usize) += 1;
            }
        }
        println!(
            "{}: {structured}/{} structured; nextvalue {structured_value_enumeration_units}/{value_enumeration_units}; {:?}",
            path.file_name().unwrap().to_string_lossy(),
            units.len(),
            diagnostics
        );
    }
}
