//! The `rustswf worker` JSONL session as the Electron harness and the previous
//! desktop host see it: both framings, error kinds, and the protocol v2 operations.
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
};

const MANIFEST: &str = include_str!("../../engine.json");
const PROTOCOL: &str = include_str!("../../../protocol.json");

struct Worker {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    next: u64,
}
impl Worker {
    fn start() -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_rustswf"))
            .arg("worker")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("rustswf worker starts");
        let input = child.stdin.take().unwrap();
        let output = BufReader::new(child.stdout.take().unwrap());
        Self {
            child,
            input,
            output,
            next: 1,
        }
    }
    fn exchange(&mut self, request: Value) -> Value {
        writeln!(self.input, "{request}").unwrap();
        self.input.flush().unwrap();
        let mut line = String::new();
        self.output.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap_or_else(|e| panic!("reply is not JSON ({e}): {line}"))
    }
    fn reply(&mut self, op: &str, args: Value) -> Value {
        let id = self.next;
        self.next += 1;
        let reply = self.exchange(json!({"id":id,"op":op,"args":args}));
        assert_eq!(reply["id"], id, "reply carries the request id");
        reply
    }
    fn ok(&mut self, op: &str, args: Value) -> Value {
        let reply = self.reply(op, args);
        assert_eq!(reply["ok"], true, "{op} failed: {reply}");
        reply["result"].clone()
    }
    fn fail(&mut self, op: &str, args: Value) -> (String, String) {
        let reply = self.reply(op, args);
        assert_eq!(reply["ok"], false, "{op} unexpectedly succeeded");
        (
            reply["error"]["kind"].as_str().unwrap().to_owned(),
            reply["error"]["message"].as_str().unwrap().to_owned(),
        )
    }
    fn open(&mut self, relative: &str) -> Value {
        self.ok("open", json!({"path":corpus(relative)}))
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn corpus(relative: &str) -> String {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../corpus")
        .join(relative)
        .to_string_lossy()
        .into_owned()
}
const CLOSURES: &str = "parity-20261014/optimized/swf/dev-as3-closures/release.fws.swf";
const NAMESPACES: &str = "parity-20261014/optimized/swf/dev-as3-classes-namespaces/release.fws.swf";
const AS2_TIMELINE: &str = "parity-20261014/optimized/swf/dev-as2-timeline/release.fws.swf";

fn strings(value: &Value) -> BTreeSet<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_owned())
        .collect()
}

fn bits(fields: &[(u32, usize)]) -> Vec<u8> {
    let mut result = Vec::new();
    let mut position = 0;
    for &(value, width) in fields {
        for bit in (0..width).rev() {
            if position % 8 == 0 {
                result.push(0);
            }
            let last = result.len() - 1;
            result[last] |= (((value >> bit) & 1) as u8) << (7 - position % 8);
            position += 1;
        }
    }
    result
}
fn tag(code: u16, payload: &[u8]) -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&((code << 6) | 63).to_le_bytes());
    bytes.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    bytes.extend_from_slice(payload);
    bytes
}
/// zlib container around stored (uncompressed) deflate blocks.
fn zlib_stored(data: &[u8]) -> Vec<u8> {
    let mut out = vec![0x78, 0x01, 0x01];
    out.extend_from_slice(&(data.len() as u16).to_le_bytes());
    out.extend_from_slice(&(!(data.len() as u16)).to_le_bytes());
    out.extend_from_slice(data);
    let (mut a, mut b) = (1u32, 0u32);
    for byte in data {
        a = (a + u32::from(*byte)) % 65521;
        b = (b + a) % 65521;
    }
    out.extend_from_slice(&((b << 16) | a).to_be_bytes());
    out
}
fn swf_file(name: &str, tags: &[Vec<u8>]) -> String {
    let mut bytes = b"FWS\x09\0\0\0\0".to_vec();
    bytes.extend(bits(&[(12, 5), (0, 12), (2000, 12), (0, 12), (1000, 12)]));
    bytes.extend_from_slice(&(24u16 * 256).to_le_bytes());
    bytes.extend_from_slice(&1u16.to_le_bytes());
    for t in tags {
        bytes.extend_from_slice(t);
    }
    bytes.extend(tag(0, &[]));
    let len = bytes.len() as u32;
    bytes[4..8].copy_from_slice(&len.to_le_bytes());
    let dir = std::env::temp_dir().join(format!("rustswf-worker-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    std::fs::write(&path, bytes).unwrap();
    path.to_string_lossy().into_owned()
}

#[test]
fn v2_and_legacy_framings_are_both_answered_in_kind() {
    let mut worker = Worker::start();
    let v2 = worker.exchange(json!({"id":"a","op":"capabilities","args":{}}));
    assert_eq!((v2["ok"].clone(), v2["id"].clone()), (json!(true), json!("a")));
    assert_eq!(v2["result"]["protocol"]["version"], 2);
    let legacy = worker.exchange(json!({"request_id":9,"operation":"open","args":{"path":corpus(CLOSURES)}}));
    assert_eq!(legacy["request_id"], 9);
    assert!(legacy.get("ok").is_none(), "legacy replies have no ok flag");
    assert_eq!(legacy["result"]["info"]["frame_count"], 1);
    let legacy_error = worker.exchange(json!({"request_id":10,"operation":"nope","args":{}}));
    assert!(legacy_error["error"].is_string(), "legacy errors stay plain text");
    // Nothing a client did before makes the next v2 request fail.
    assert_eq!(worker.ok("header", json!({}))["frame_count"], 1);
}

#[test]
fn errors_carry_a_kind_and_never_end_the_session() {
    let mut worker = Worker::start();
    assert_eq!(worker.fail("header", json!({})).0, "not_open");
    assert_eq!(worker.fail("open", json!({})).0, "bad_request");
    let (kind, message) = worker.fail("open", json!({"path":corpus("no-such-file.swf")}));
    assert_eq!(kind, "not_found");
    assert!(message.contains("no-such-file.swf"));
    worker.open(CLOSURES);
    assert_eq!(worker.fail("frobnicate", json!({})).0, "unsupported");
    assert_eq!(worker.fail("decompile", json!({"unit_id":"tag:99/method:1"})).0, "not_found");
    assert_eq!(worker.fail("raw", json!({"tag_index":999})).0, "not_found");
    assert_eq!(worker.fail("raw", json!({"tag_path":"x"})).0, "bad_request");
    let reply = worker.exchange(json!({"id":5,"op":"header","args":[]}));
    assert_eq!(reply["error"]["kind"], "bad_request");
    let reply = worker.exchange(json!({"id":6,"op":"header","protocol_version":3}));
    assert_eq!(reply["error"]["kind"], "bad_request");
    let reply = worker.exchange(json!("just a string"));
    assert_eq!(reply["ok"], false);
    assert_eq!(worker.ok("header", json!({}))["signature"], "FWS");
}

#[test]
fn capabilities_match_the_manifest_and_the_protocol_table() {
    let mut worker = Worker::start();
    let capabilities = worker.ok("capabilities", json!({}));
    let manifest: Value = serde_json::from_str(MANIFEST).unwrap();
    let protocol: Value = serde_json::from_str(PROTOCOL).unwrap();
    assert_eq!(strings(&capabilities["ops"]), strings(&manifest["worker"]["ops"]));
    let native: BTreeSet<String> = manifest["worker"]["native_ops"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();
    assert_eq!(strings(&capabilities["native_ops"]), native);
    let defined: BTreeSet<String> = protocol["ops"].as_object().unwrap().keys().cloned().collect();
    for op in strings(&capabilities["ops"]) {
        assert!(defined.contains(&op), "{op} is not an operation of engines/protocol.json");
    }
    // Every operation of every level has an entry in the table.
    for (_, level) in protocol["levels"].as_object().unwrap() {
        for op in strings(&level["ops"]) {
            assert!(defined.contains(&op), "level lists {op} but ops has no entry");
        }
    }
    assert_eq!(manifest["worker"]["framing"], "v2");
    assert_eq!(capabilities["display_name"], "RustSWF");
    // Every declared operation is answered by the session, none is "unsupported".
    worker.open(CLOSURES);
    for op in strings(&capabilities["ops"]).into_iter().chain(native) {
        let reply = worker.reply(&op, json!({"unit_id":"tag:6/method:1","id":"tag:7","tag_index":7}));
        let kind = reply["error"]["kind"].as_str().unwrap_or("");
        assert_ne!(kind, "unsupported", "{op} is declared but not implemented");
    }
}

#[test]
fn header_and_tag_tree_describe_the_closures_sample() {
    let mut worker = Worker::start();
    let opened = worker.open(CLOSURES);
    assert_eq!(opened["info"]["tag_count"], 10);
    let header = worker.ok("header", json!({}));
    assert_eq!(header["signature"], "FWS");
    assert_eq!(header["compression"], "none");
    assert_eq!(header["version"], 38);
    assert_eq!(header["file_length"], 997);
    assert_eq!(header["file_size"], std::fs::metadata(corpus(CLOSURES)).unwrap().len());
    assert_eq!(
        header["frame_size"],
        json!({"xmin":0,"xmax":10000,"ymin":0,"ymax":7500,"width":500.0,"height":375.0})
    );
    assert_eq!(header["frame_rate"], 24.0);
    assert_eq!(
        [&header["frame_count"], &header["shown_frames"], &header["tag_count"]],
        [&json!(1), &json!(1), &json!(10)]
    );
    assert_eq!((&header["abc_blocks"], &header["script_count"]), (&json!(1), &json!(5)));
    assert_eq!((&header["has_as3"], &header["has_metadata"]), (&json!(true), &json!(true)));
    assert_eq!(header["file_attributes"]["actionscript3"], true);
    assert!(header["metadata"].as_str().unwrap().contains("Apache Flex"));
    assert_eq!(header["background_color"], "#ffffff");
    assert_eq!(header["script_limits"]["max_recursion_depth"], 1000);
    assert_eq!(header["sha256"], header["revision"]);
    assert_eq!(header["sha256"], opened["info"]["sha256"]);

    let tree = worker.ok("tag_tree", json!({}));
    assert_eq!(tree["total_tags"], 10);
    let nodes = tree["nodes"].as_array().unwrap();
    let folders: Vec<&str> = nodes.iter().map(|n| n["folder"].as_str().unwrap()).collect();
    assert_eq!(folders, ["header", "frames", "others", "scripts"]);
    let main = &nodes[3]["children"][0];
    assert_eq!(main["kind"], "class");
    assert_eq!(main["unit_id"], "tag:6/class:0");
    assert_eq!(main["superclass"], "flash.display::Sprite");
}

#[test]
fn tags_are_addressed_by_index_path_or_id_and_pages_come_as_hex_or_base64() {
    let mut worker = Worker::start();
    let opened = worker.open(CLOSURES);
    let by_index = worker.ok("raw", json!({"tag_index":7,"offset":0,"limit":16}));
    let by_path = worker.ok("raw", json!({"tag_path":[7]}));
    let by_id = worker.ok("raw", json!({"id":"tag:7"}));
    for reply in [&by_index, &by_path, &by_id] {
        assert_eq!((&reply["scope"], &reply["name"], &reply["type"]), (&json!("tag"), &json!("SymbolClass"), &json!(76)));
        assert_eq!(reply["tag_index"], 7);
        assert_eq!((&reply["total"], &reply["length"], &reply["offset"]), (&json!(9), &json!(9), &json!(0)));
        assert_eq!(reply["encoding"], "hex");
        assert_eq!(reply["hex"], "010000004d61696e00", "count, id and the name Main");
        assert!(reply.get("base64").is_none());
        assert_eq!(reply["revision"], opened["info"]["sha256"]);
    }
    assert_eq!((&by_index["limit"], &by_path["limit"]), (&json!(16), &json!(4096)));
    let slice = worker.ok("raw", json!({"tag_index":7,"offset":4,"limit":4,"encoding":"base64"}));
    assert_eq!((&slice["length"], &slice["total"], &slice["limit"]), (&json!(4), &json!(9), &json!(4)));
    assert_eq!(slice["base64"], "TWFpbg==");
    assert!(slice.get("hex").is_none());
    // A page at the very end is empty, one past it is refused.
    assert_eq!(worker.ok("raw", json!({"tag_index":7,"offset":9}))["length"], 0);
    assert_eq!(worker.fail("raw", json!({"tag_index":7,"offset":10})).0, "bad_request");
    // The whole file, as received.
    let file = worker.ok("raw", json!({"scope":"file","limit":8}));
    assert_eq!((&file["scope"], &file["name"]), (&json!("file"), &json!("Archivo SWF")));
    let bytes = std::fs::read(corpus(CLOSURES)).unwrap();
    assert_eq!(file["total"], bytes.len());
    assert_eq!(file["hex"], bytes[..8].iter().map(|b| format!("{b:02x}")).collect::<String>());
    // Bad paging arguments are refused, not clamped.
    let limits = [("limit", json!(0)), ("limit", json!(65537)), ("offset", json!(-1)), ("limit", json!("4"))];
    for (name, value) in limits {
        assert_eq!(worker.fail("raw", json!({"tag_index":1, name: value})).0, "bad_request", "{name}");
    }
    assert_eq!(worker.fail("raw", json!({"tag_index":1,"encoding":"utf8"})).0, "bad_request");
    let big = worker.ok("raw", json!({"tag_index":1,"limit":65536}));
    assert!(big["length"].as_u64().unwrap() <= 65536);
    let info = worker.ok("resource_info", json!({"tag_index":7}));
    assert_eq!((info["kind"].as_str(), info["name"].as_str()), (Some("raw"), Some("SymbolClass")));
    assert_eq!(info["tag_path"], json!([7]));
}

#[test]
fn class_units_decompile_and_disassemble_like_a_class() {
    let mut worker = Worker::start();
    worker.open(NAMESPACES);
    let listing = worker.ok("disassemble", json!({"unit_id":"tag:6/class:1"}));
    assert_eq!(listing["scope"], "class");
    assert_eq!(listing["state"], "decoded");
    let pcode = listing["pcode"].as_str().unwrap();
    assert!(pcode.contains("; ---- app::Model constructor (tag:6/method:4) ----"));
    assert!(pcode.contains("returnvalue"));
    // A page past the end is empty, not an error.
    assert_eq!(worker.ok("disassemble", json!({"unit_id":"tag:6/class:1","offset":100000}))["pcode"], "");
    let class = worker.ok("decompile", json!({"unit_id":"tag:6/class:1"}));
    assert_eq!((class["scope"].as_str(), class["engine"].as_str()), (Some("class"), Some("rust")));
    assert_eq!(class["unit_id"], "tag:6/class:1");
    assert!(class["pcode"].as_str().unwrap().contains("app::Model"));
    assert!(class["state"] == "structured" || class["source"].is_null());
    // Method units keep working and name their engine.
    let method = worker.ok("decompile", json!({"unit_id":"tag:6/method:5"}));
    assert_eq!(method["engine"], "rust");
    assert_eq!(method["unit_id"], "tag:6/method:5");
    assert_eq!(worker.fail("decompile", json!({"unit_id":"tag:6/class:99"})).0, "not_found");
    assert_eq!(worker.fail("disassemble", json!({"unit_id":"tag:99/class:0"})).0, "not_found");
}

#[test]
fn as2_sample_lists_units_and_decompiles_them() {
    let mut worker = Worker::start();
    worker.open(AS2_TIMELINE);
    let scripts = worker.ok("scripts", json!({}));
    assert!(scripts["total"].as_u64().unwrap() >= 1);
    let unit = scripts["items"][0]["id"].as_str().unwrap().to_owned();
    assert_eq!(unit, "tag:2/actions");
    let result = worker.ok("decompile", json!({"unit_id":unit}));
    assert_eq!(result["unit_id"], "tag:2/actions");
    assert!(!result["pcode"].as_str().unwrap().is_empty());
    let tree = worker.ok("tag_tree", json!({}));
    let scripts_folder = tree["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|n| n["folder"] == "scripts")
        .unwrap();
    assert_eq!(scripts_folder["children"][0]["kind"], "frame");
    assert_eq!(scripts_folder["children"][0]["children"][0]["unit_id"], "tag:2/actions");
}

#[test]
fn extract_operations_check_the_kind_of_resource_and_write_or_inline() {
    let argb = {
        let mut payload = vec![1, 0, 5, 1, 0, 1, 0];
        payload.extend(zlib_stored(&[128, 32, 64, 0]));
        payload
    };
    let pcm = {
        let mut payload = vec![2, 0, 0x3e];
        payload.extend_from_slice(&2u32.to_le_bytes());
        payload.extend_from_slice(&[0, 0, 0xff, 0x7f]);
        payload
    };
    let shape = vec![3, 0, 0x08, 0x00, 0, 0, 0, 0];
    let path = swf_file(
        "extract.swf",
        &[tag(36, &argb), tag(14, &pcm), tag(2, &shape), tag(87, &[4, 0, 0, 0, 0, 0, 80, 75])],
    );
    let mut worker = Worker::start();
    worker.ok("open", json!({"path":path}));

    let png = worker.ok("extract_image", json!({"tag_index":0}));
    assert_eq!((png["format"].as_str(), png["mime"].as_str()), (Some("png"), Some("image/png")));
    assert!(png["data_base64"].as_str().unwrap().starts_with("iVBORw0KGgo"), "PNG signature");
    assert!(png.get("path").is_none());

    let dir = std::env::temp_dir().join(format!("rustswf-extract-{}", std::process::id()));
    let written = worker.ok("extract_sound", json!({"tag_index":1,"dir":dir}));
    assert_eq!(written["format"], "wav");
    let file = PathBuf::from(written["path"].as_str().unwrap());
    assert_eq!(&std::fs::read(&file).unwrap()[..4], b"RIFF");
    assert!(written.get("data_base64").is_none(), "written files are not also inlined");
    let target = dir.join("explicit.wav");
    let explicit = worker.ok("extract_sound", json!({"tag_index":1,"format":"wav","path":target,"inline":true}));
    assert_eq!(explicit["path"], json!(target));
    assert!(explicit["data_base64"].is_string());
    // The destination is never overwritten.
    assert_eq!(worker.fail("extract_sound", json!({"tag_index":1,"path":target})).0, "failed");

    let svg = worker.ok("shape_svg", json!({"tag_index":2}));
    assert_eq!(svg["extension"], "svg");
    assert_eq!(worker.fail("shape_svg", json!({"tag_index":2,"format":"png"})).0, "bad_request");

    // Wrong kind of resource for the operation.
    assert_eq!(worker.fail("extract_image", json!({"tag_index":1})).0, "bad_request");
    assert_eq!(worker.fail("extract_sound", json!({"tag_index":0})).0, "bad_request");
    assert_eq!(worker.fail("extract_font", json!({"tag_index":0})).0, "bad_request");
    assert_eq!(worker.fail("extract_text", json!({"tag_index":2})).0, "bad_request");
    assert_eq!(worker.fail("shape_svg", json!({"tag_index":0})).0, "bad_request");
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn supervise_runs_a_program_with_inherited_stdio_and_returns_its_exit_code() {
    let program = std::env::var("COMSPEC").unwrap_or_else(|_| "cmd.exe".into());
    let output = Command::new(env!("CARGO_BIN_EXE_rustswf"))
        .args(["supervise", "--memory-mib", "256", "--", &program, "/c", "echo supervised & exit 7"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(7));
    assert!(String::from_utf8_lossy(&output.stdout).contains("supervised"));
    let missing = Command::new(env!("CARGO_BIN_EXE_rustswf"))
        .args(["supervise", "--", "definitely-not-a-program.exe"])
        .output()
        .unwrap();
    assert_eq!(missing.status.code(), Some(127));
    let bad = Command::new(env!("CARGO_BIN_EXE_rustswf"))
        .args(["supervise", "--memory-mib", "1"])
        .output()
        .unwrap();
    assert_eq!(bad.status.code(), Some(127));
}

#[test]
fn supervise_stops_a_runaway_tree_when_the_supervisor_is_killed() {
    // A child that outlives its parent would leak after the app quits.
    let mut supervisor = Command::new(env!("CARGO_BIN_EXE_rustswf"))
        .args(["supervise", "--", "powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 247"])
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    std::thread::sleep(std::time::Duration::from_millis(1500));
    supervisor.kill().unwrap();
    supervisor.wait().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(800));
    let alive = Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -like '*Start-Sleep -Seconds 247*' } | Measure-Object).Count",
        ])
        .output()
        .unwrap();
    let count: u32 = String::from_utf8_lossy(&alive.stdout).trim().parse().unwrap_or(0);
    // The probe's own command line also contains the text, so one match is the probe itself.
    assert!(count <= 1, "the supervised tree outlived its supervisor ({count} matches)");
}
