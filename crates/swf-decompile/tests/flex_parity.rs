//! Optional real AS3 compiler fixtures. Runtime validation belongs to the
//! Ruffle/Rascal harness; these tests do not claim full AVM2 behavior.
use std::path::{Path, PathBuf};
use std::process::Command;
use swf_avm::{disassemble, inventory};
use swf_core::Movie;
use swf_decompile::decompile;

const METHOD: &str = "public function coerceValue(value:*):RustCoerceBox {\n            var typed:RustCoerceBox = value;\n            return typed;\n        }";
const BOX: &str = "package { public class RustCoerceBox { public var value:int; public function RustCoerceBox(value:int = 7) { this.value = value; } } }\n";
fn probe() -> String {
    format!(
        "package {{\n    import flash.display.Sprite;\n    public class RustCoerceProbe extends Sprite {{\n        public function RustCoerceProbe() {{\n            run(\"match\", new RustCoerceBox(7));\n            run(\"null\", null);\n            run(\"undefined\", undefined);\n            run(\"mismatch\", {{}});\n        }}\n        {METHOD}\n        private function run(label:String, value:*):void {{\n            trace(\"input:\" + label);\n            try {{\n                var actual:RustCoerceBox = coerceValue(value);\n                trace(\"output:\" + label + \":\" + (actual == null ? \"null\" : actual.value));\n            }} catch (error:Error) {{ trace(\"error:\" + label + \":\" + error.errorID); }}\n        }}\n    }}\n}}\n"
    )
}
fn compile(source: &Path, output: &Path) -> serde_json::Value {
    let java = std::env::var_os("RUSTSWF_FLEX_JAVA").expect("RUSTSWF_FLEX_JAVA");
    let jar = PathBuf::from(std::env::var_os("RUSTSWF_MXMLC_JAR").expect("RUSTSWF_MXMLC_JAR"));
    let flex = jar.parent().unwrap().parent().unwrap().join("frameworks");
    let player = PathBuf::from(
        std::env::var_os("RUSTSWF_PLAYERGLOBAL_HOME").expect("RUSTSWF_PLAYERGLOBAL_HOME"),
    );
    let args = vec![
        "-Xmx1024m".into(),
        "-jar".into(),
        jar.display().to_string(),
        format!("+flexlib={}", flex.display()),
        format!("+playerglobalHome={}", player.display()),
        "-target-player=32.0".into(),
        "-debug=false".into(),
        "-omit-trace-statements=false".into(),
        format!("-output={}", output.display()),
        format!("-source-path={}", source.parent().unwrap().display()),
        source.display().to_string(),
    ];
    let mut command = Command::new(&java);
    command.args(&args);
    command.env("PLAYERGLOBAL_HOME", &player);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let result = command.output().expect("Flex compiler process");
    let stdout = String::from_utf8_lossy(&result.stdout).to_string();
    let stderr = String::from_utf8_lossy(&result.stderr).to_string();
    assert!(result.status.success(), "{stdout}\n{stderr}");
    serde_json::json!({"java":java,"arguments":args,"status":"compiled","stdout":stdout,"stderr":stderr,"runtime":"pending"})
}
#[test]
#[ignore = "Requires the local Apache Flex SDK. Emits original/reconstructed SWFs for the parent runtime harness."]
fn class_coercion_compiles_as_typed_assignment_and_retains_error_fixture() {
    let dir = std::env::var_os("RUSTSWF_RUNTIME_FIXTURE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::temp_dir().join("rustswf-coerce-parity"));
    let original_dir = dir.join("original");
    let reconstructed_dir = dir.join("reconstructed");
    std::fs::create_dir_all(&original_dir).unwrap();
    std::fs::create_dir_all(&reconstructed_dir).unwrap();
    let original_source = original_dir.join("RustCoerceProbe.as");
    let reconstructed_source = reconstructed_dir.join("RustCoerceProbe.as");
    let original_swf = dir.join("original.swf");
    let reconstructed_swf = dir.join("reconstructed.swf");
    let original = probe();
    std::fs::write(&original_source, &original).unwrap();
    std::fs::write(original_dir.join("RustCoerceBox.as"), BOX).unwrap();
    let original_compile = compile(&original_source, &original_swf);
    let original_movie = Movie::read(&original_swf).unwrap();
    let units = inventory(&original_movie);
    let matches = units
        .iter()
        .filter(|u| u.name.ends_with("coerceValue"))
        .collect::<Vec<_>>();
    assert_eq!(matches.len(), 1);
    let unit = matches[0];
    let result = decompile(unit);
    let text = result
        .source
        .clone()
        .unwrap_or_else(|| panic!("{:?}\n{}", result.diagnostics, result.pcode));
    assert!(text.contains(":RustCoerceBox ="));
    assert!(!text.contains(" as "));
    let replacement = format!("public {text}");
    let reconstructed = original.replace(METHOD, &replacement);
    assert_ne!(original, reconstructed);
    std::fs::write(&reconstructed_source, reconstructed).unwrap();
    std::fs::write(reconstructed_dir.join("RustCoerceBox.as"), BOX).unwrap();
    let reconstructed_compile = compile(&reconstructed_source, &reconstructed_swf);
    let rebuilt = Movie::read(&reconstructed_swf).unwrap();
    let rebuilt_units = inventory(&rebuilt);
    let rebuilt_unit = rebuilt_units
        .iter()
        .find(|u| u.name.ends_with("coerceValue"))
        .unwrap();
    let original_coerce = disassemble(unit)
        .instructions
        .iter()
        .filter(|i| i.name == "coerce")
        .count();
    let rebuilt_coerce = disassemble(rebuilt_unit)
        .instructions
        .iter()
        .filter(|i| i.name == "coerce")
        .count();
    assert!(original_coerce > 0 && rebuilt_coerce > 0);
    let sidecar = serde_json::json!({"fixture":"rust_class_coerce","behavior":"pending_full_AVM2_runtime","original_swf":original_swf,"reconstructed_swf":reconstructed_swf,
        "original_source":original_source,"reconstructed_source":reconstructed_source,"original_movie_sha256":original_movie.info.sha256,"reconstructed_movie_sha256":rebuilt.info.sha256,
        "original_unit":{"id":unit.id,"method_index":unit.method_index,"body_index":unit.body_index,"sha256":unit.sha256,"byte_start":unit.byte_start,"byte_len":unit.byte_len,"coerce_opcodes":original_coerce},
        "reconstructed_unit":{"id":rebuilt_unit.id,"method_index":rebuilt_unit.method_index,"body_index":rebuilt_unit.body_index,"sha256":rebuilt_unit.sha256,"coerce_opcodes":rebuilt_coerce},
        "decompiled_method":text,"original_pcode":result.pcode,"reconstructed_pcode":disassemble(rebuilt_unit).pcode,"compile_original":original_compile,"compile_reconstructed":reconstructed_compile,
        "expected_trace":["input:match","output:match:7","input:null","output:null:null","input:undefined","output:undefined:null","input:mismatch","error:mismatch:1034"]});
    std::fs::write(
        dir.join("fixture.json"),
        serde_json::to_vec_pretty(&sidecar).unwrap(),
    )
    .unwrap();
}

#[test]
#[ignore = "Requires local Apache Flex SDK. Single-catch SWFs are passed to the full AVM2 runtime harness."]
fn single_catch_compiles_and_preserves_getter_throw_fixture() {
    let base = std::env::var_os("RUSTSWF_RUNTIME_FIXTURE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::temp_dir().join("rustswf-coerce-parity"));
    let dir = base.join("catch");
    let original_dir = dir.join("original");
    let reconstructed_dir = dir.join("reconstructed");
    std::fs::create_dir_all(&original_dir).unwrap();
    std::fs::create_dir_all(&reconstructed_dir).unwrap();
    let method = "public function readValue(value:*):* {\n            var result:*;\n            try { result = value.public::value; } catch (error:*) { result = error; }\n            return result;\n        }";
    let original = format!(
        "package {{ import flash.display.Sprite; public class RustCatchProbe extends Sprite {{ public function RustCatchProbe() {{ trace(\"normal:\"+readValue({{value:7}})); trace(\"throw:\"+readValue(new RustCatchThrower())); }} {method} }} }}"
    );
    let thrower =
        "package { public class RustCatchThrower { public function get value():* { throw 13; } } }";
    let original_source = original_dir.join("RustCatchProbe.as");
    let reconstructed_source = reconstructed_dir.join("RustCatchProbe.as");
    let original_swf = dir.join("original.swf");
    let reconstructed_swf = dir.join("reconstructed.swf");
    std::fs::write(&original_source, &original).unwrap();
    std::fs::write(original_dir.join("RustCatchThrower.as"), thrower).unwrap();
    let original_compile = compile(&original_source, &original_swf);
    let original_movie = Movie::read(&original_swf).unwrap();
    let units = inventory(&original_movie);
    let unit = units
        .iter()
        .find(|u| u.name.ends_with("readValue"))
        .unwrap();
    let result = decompile(unit);
    std::fs::write(
        dir.join("generation.json"),
        serde_json::to_vec_pretty(&result).unwrap(),
    )
    .unwrap();
    let source = result
        .source
        .clone()
        .unwrap_or_else(|| panic!("{:?}\n{}", result.diagnostics, result.pcode));
    assert!(source.contains("catch ("));
    let reconstructed = original.replace(method, &format!("public {source}"));
    assert_ne!(original, reconstructed);
    std::fs::write(&reconstructed_source, reconstructed).unwrap();
    std::fs::write(reconstructed_dir.join("RustCatchThrower.as"), thrower).unwrap();
    let reconstructed_compile = compile(&reconstructed_source, &reconstructed_swf);
    let rebuilt = Movie::read(&reconstructed_swf).unwrap();
    let rebuilt_units = inventory(&rebuilt);
    let rebuilt_unit = rebuilt_units
        .iter()
        .find(|u| u.name.ends_with("readValue"))
        .unwrap();
    let sidecar = serde_json::json!({"fixture":"rust_single_catch","behavior":"pending_full_AVM2_runtime","original_swf":original_swf,"reconstructed_swf":reconstructed_swf,"original_source":original_source,"reconstructed_source":reconstructed_source,
        "original_movie_sha256":original_movie.info.sha256,"reconstructed_movie_sha256":rebuilt.info.sha256,"original_unit":{"id":unit.id,"method_index":unit.method_index,"body_index":unit.body_index,"sha256":unit.sha256,"byte_start":unit.byte_start,"byte_len":unit.byte_len},
        "reconstructed_unit":{"id":rebuilt_unit.id,"method_index":rebuilt_unit.method_index,"body_index":rebuilt_unit.body_index,"sha256":rebuilt_unit.sha256},"decompiled_method":source,"original_pcode":result.pcode,"reconstructed_pcode":disassemble(rebuilt_unit).pcode,"compile_original":original_compile,"compile_reconstructed":reconstructed_compile,
        "expected_trace":["normal:7","throw:13"]});
    std::fs::write(
        dir.join("fixture.json"),
        serde_json::to_vec_pretty(&sidecar).unwrap(),
    )
    .unwrap();
}

#[test]
#[ignore = "Requires the SHA-pinned local playerglobal SWC and Flex compiler. Checks exact external Sprite capture metadata."]
fn complete_sprite_document_requires_verified_swc_ancestor_contract() {
    let base = std::env::var_os("RUSTSWF_RUNTIME_FIXTURE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::temp_dir().join("rustswf-coerce-parity"));
    let dir = base.join("hierarchy");
    std::fs::create_dir_all(&dir).unwrap();
    let original_dir = dir.join("original");
    let reconstructed_dir = dir.join("reconstructed");
    std::fs::create_dir_all(&original_dir).unwrap();
    std::fs::create_dir_all(&reconstructed_dir).unwrap();
    let original = "package { import flash.display.Sprite; public class RustHierarchyProbe extends Sprite { public function RustHierarchyProbe() { super(); } public function echo(value:*):* { return value; } } }";
    let original_source = original_dir.join("RustHierarchyProbe.as");
    std::fs::write(&original_source, original).unwrap();
    let original_swf = dir.join("original.swf");
    let _ = compile(&original_source, &original_swf);
    let movie = Movie::read(&original_swf).unwrap();
    let units = inventory(&movie);
    let unit = units.iter().find(|u| u.name.ends_with("echo")).unwrap();
    let missing = swf_decompile::decompile_document(unit);
    assert!(missing.documents.is_empty());
    let swc = PathBuf::from(std::env::var_os("RUSTSWF_PLAYERGLOBAL_HOME").unwrap())
        .join("32.0/playerglobal.swc");
    let sha = "7d4d6168d27603cfb3b750302448e354e0bbc1bdd58f5d101c3dcf6891e9bb65";
    assert!(swf_decompile::VerifiedHierarchy::load_swc(&swc, &"0".repeat(64)).is_err());
    let hierarchy = swf_decompile::VerifiedHierarchy::load_swc(&swc, sha).unwrap();
    assert!(hierarchy.provenance.class_records > 500);
    let result = swf_decompile::decompile_document_with_hierarchy(unit, &hierarchy);
    assert_eq!(result.state, "structured", "{:?}", result.diagnostics);
    assert_eq!(result.documents.len(), 1);
    let source = reconstructed_dir.join("RustHierarchyProbe.as");
    std::fs::write(&source, &result.documents[0].source).unwrap();
    let rebuilt_swf = dir.join("reconstructed.swf");
    let rebuilt_compile = compile(&source, &rebuilt_swf);
    let rebuilt = Movie::read(&rebuilt_swf).unwrap();
    assert!(!inventory(&rebuilt).is_empty());
    std::fs::write(dir.join("fixture.json"),serde_json::to_vec_pretty(&serde_json::json!({"fixture":"rust_verified_sprite_hierarchy","behavior":"not_run","hierarchy":hierarchy.provenance,"document":result,"compile_reconstructed":rebuilt_compile,"original_swf":original_swf,"reconstructed_swf":rebuilt_swf})).unwrap()).unwrap();
}

#[test]
#[ignore = "Requires local Apache Flex. Emits whole original/reconstructed static-initializer SWFs for full AVM2 runtime and reflection checks."]
fn ordered_static_slots_compile_without_auxiliary_traits() {
    let base = std::env::var_os("RUSTSWF_RUNTIME_FIXTURE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::temp_dir().join("rustswf-static-parity"));
    let dir = base.join("static-init");
    let original_dir = dir.join("original");
    let reconstructed_dir = dir.join("reconstructed");
    std::fs::create_dir_all(&original_dir).unwrap();
    std::fs::create_dir_all(&reconstructed_dir).unwrap();
    let state = r#"package {
public class RustStaticInitState {
    public static var later:int;
    public static var early:int;
    public static var signed:int;
    public static var arr:*;
    public static var alias:*;
    public static var zero:Number;
    public static var text:String;
    public static var readNumber:Number;
    public static var unset:*;
    public static var readUndefined:*;
    {
        RustStaticInitState.readNumber = RustStaticInitState.zero;
        RustStaticInitState.readUndefined = RustStaticInitState.unset;
        RustStaticInitState.early = RustStaticInitState.later + 7;
        RustStaticInitState.later = 4;
        RustStaticInitState.signed = 4294967295;
        RustStaticInitState.arr = [1, 2];
        RustStaticInitState.alias = RustStaticInitState.arr;
        RustStaticInitState.zero = -0.0;
        RustStaticInitState.text = 42 + "";
    }
    public function RustStaticInitState() { super(); }
}}
"#;
    let main = r#"package {
import flash.display.Sprite;
import flash.utils.describeType;
public class RustStaticInitProbe extends Sprite {
    public function RustStaticInitProbe() {
        trace("defaults:" + RustStaticInitState.early + ":" + RustStaticInitState.readNumber + ":" + RustStaticInitState.readUndefined);
        trace("values:" + RustStaticInitState.later + ":" + RustStaticInitState.signed + ":" + (1 / RustStaticInitState.zero) + ":" + RustStaticInitState.text);
        trace("alias:" + (RustStaticInitState.arr === RustStaticInitState.alias) + ":" + RustStaticInitState.arr.length);
        var fields:Array = [];
        var reflected:XML = describeType(RustStaticInitState);
        for each (var field:XML in reflected.variable) { fields.push(field.@name.toString()); }
        fields.sort();
        trace("traits:" + fields.join(","));
    }
}}
"#;
    let original_source = original_dir.join("RustStaticInitProbe.as");
    let original_state = original_dir.join("RustStaticInitState.as");
    std::fs::write(&original_source, main).unwrap();
    std::fs::write(&original_state, state).unwrap();
    // Compile the complete class as its own source-generation ABC. The runtime
    // movie links an unchanged Sprite harness around this same original class.
    let generation_swf = dir.join("source-generation.swf");
    let generation_compile = compile(&original_state, &generation_swf);
    let generation_movie = Movie::read(&generation_swf).unwrap();
    let generation_units = inventory(&generation_movie);
    let unit = generation_units
        .iter()
        .find(|u| u.name == "RustStaticInitState static initializer")
        .unwrap();
    let original_swf = dir.join("original.swf");
    let original_compile = compile(&original_source, &original_swf);
    let original_movie = Movie::read(&original_swf).unwrap();
    let result = swf_decompile::decompile_document(unit);
    std::fs::write(
        dir.join("generation.json"),
        serde_json::to_vec_pretty(&result).unwrap(),
    )
    .unwrap();
    assert_eq!(
        result.state,
        "structured",
        "{:?}\n{}",
        result.diagnostics,
        disassemble(unit).pcode
    );
    assert_eq!(result.documents.len(), 1);
    let reconstructed_state = reconstructed_dir.join("RustStaticInitState.as");
    std::fs::write(&reconstructed_state, &result.documents[0].source).unwrap();
    let reconstructed_source = reconstructed_dir.join("RustStaticInitProbe.as");
    std::fs::write(&reconstructed_source, main).unwrap();
    let reconstructed_swf = dir.join("reconstructed.swf");
    let reconstructed_compile = compile(&reconstructed_source, &reconstructed_swf);
    let rebuilt = Movie::read(&reconstructed_swf).unwrap();
    let rebuilt_units = inventory(&rebuilt);
    let rebuilt_unit = rebuilt_units
        .iter()
        .find(|u| u.name == "RustStaticInitState static initializer")
        .unwrap();
    let original_abc = unit.context.as_ref().unwrap();
    let rebuilt_abc = rebuilt_unit.context.as_ref().unwrap();
    let rebuilt_class = rebuilt_abc
        .instances
        .iter()
        .position(|i| rebuilt_abc.multiname_display(i.name) == "RustStaticInitState")
        .unwrap();
    let original_traits = original_abc.classes[0]
        .traits
        .iter()
        .map(|t| original_abc.multiname_display(t.name))
        .collect::<Vec<_>>();
    let rebuilt_traits = rebuilt_abc.classes[rebuilt_class]
        .traits
        .iter()
        .map(|t| rebuilt_abc.multiname_display(t.name))
        .collect::<Vec<_>>();
    assert_eq!(
        original_traits, rebuilt_traits,
        "Static reconstruction introduced or reordered class traits"
    );
    assert_eq!(original_traits.len(), 10);
    let expected = [
        "defaults:7:NaN:undefined",
        "values:4:-1:-Infinity:42",
        "alias:true:2",
        "traits:alias,arr,early,later,readNumber,readUndefined,signed,text,unset,zero",
    ];
    std::fs::write(dir.join("fixture.json"), serde_json::to_vec_pretty(&serde_json::json!({
        "fixture":"rust_ordered_static_slots", "behavior":"pending_full_AVM2_runtime", "scope":"ordered owned var slots, primitive expressions, array identity, exact class trait order",
        "original_swf":original_swf, "reconstructed_swf":reconstructed_swf,
        "source_generation_swf":generation_swf,"source_generation_movie_sha256":generation_movie.info.sha256,"compile_source_generation":generation_compile,
        "original_source":original_state, "reconstructed_source":reconstructed_state,
        "original_movie_sha256":original_movie.info.sha256, "reconstructed_movie_sha256":rebuilt.info.sha256,
        "original_unit":{"id":unit.id,"method_index":unit.method_index,"body_index":unit.body_index,"sha256":unit.sha256,"byte_start":unit.byte_start,"byte_len":unit.byte_len},
        "reconstructed_unit":{"id":rebuilt_unit.id,"method_index":rebuilt_unit.method_index,"body_index":rebuilt_unit.body_index,"sha256":rebuilt_unit.sha256},
        "original_class_traits":original_traits,"reconstructed_class_traits":rebuilt_traits,
        "original_pcode":disassemble(unit).pcode,"reconstructed_pcode":disassemble(rebuilt_unit).pcode,
        "document":result,"compile_original":original_compile,"compile_reconstructed":reconstructed_compile,"expected_trace":expected
    })).unwrap()).unwrap();
}
