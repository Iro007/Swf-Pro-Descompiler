//! JPEXS-style tag tree (`tag_tree`). Folder membership comes from
//! engines/protocol.json so the Rust and Node sides classify tags identically.
//!
//! Every tag of the movie appears at least once: definitions in their folder,
//! timeline control tags inside `frames` (or inside their sprite), everything
//! else in `others`. `scripts` is a second view of the code: AS3 as a
//! package -> class tree, AS1/2 grouped by frame, sprite and button. Empty
//! folders are omitted.
use crate::classes::{class_id, split_name};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::OnceLock;
use swf_avm::ScriptUnit;
use swf_core::{Movie, TagRecord};

const PROTOCOL: &str = include_str!("../../../protocol.json");

struct Table {
    order: Vec<String>,
    folder_by_code: HashMap<u16, String>,
    frame_control: HashSet<u16>,
    show_frame: u16,
    end: u16,
}

fn table() -> &'static Table {
    static TABLE: OnceLock<Table> = OnceLock::new();
    TABLE.get_or_init(|| {
        let protocol: Value = serde_json::from_str(PROTOCOL).expect("engines/protocol.json is JSON");
        let folders = &protocol["tag_folders"];
        let codes = |value: &Value| -> Vec<u16> {
            value
                .as_array()
                .expect("tag_folders lists are arrays")
                .iter()
                .map(|v| v.as_u64().expect("tag codes are integers") as u16)
                .collect()
        };
        let mut folder_by_code = HashMap::new();
        for (folder, list) in folders["by_code"].as_object().expect("by_code is an object") {
            for code in codes(list) {
                assert!(
                    folder_by_code.insert(code, folder.clone()).is_none(),
                    "tag {code} is in two folders"
                );
            }
        }
        Table {
            order: folders["order"]
                .as_array()
                .expect("order is an array")
                .iter()
                .map(|v| v.as_str().expect("folder names are strings").to_owned())
                .collect(),
            folder_by_code,
            frame_control: codes(&folders["frame_control"]).into_iter().collect(),
            show_frame: folders["show_frame"].as_u64().expect("show_frame") as u16,
            end: folders["end"].as_u64().expect("end") as u16,
        }
    })
}

/// JPEXS folder of a tag code: `frames` for timeline control tags, `others` for the rest.
pub fn folder_of(code: u16) -> &'static str {
    let table = table();
    if let Some(folder) = table.folder_by_code.get(&code) {
        folder
    } else if table.frame_control.contains(&code) {
        "frames"
    } else {
        "others"
    }
}

/// Kind reported by `resource_info` for a tag.
pub fn resource_kind(code: u16) -> &'static str {
    match folder_of(code) {
        "shapes" => "shape",
        "morphshapes" => "morph",
        "sprites" => "sprite",
        "texts" => "text",
        "images" => "image",
        "movies" => "video",
        "sounds" => "sound",
        "buttons" => "button",
        "fonts" => "font",
        "binaryData" => "binary",
        _ => "raw",
    }
}

fn tag_label(movie: &Movie, tag: &TagRecord) -> String {
    let mut label = match tag.character_id {
        Some(id) => format!("{id}: {}", tag.name),
        None => tag.name.clone(),
    };
    if tag.code == 43 {
        let data = movie.tag_data(tag);
        let end = data.iter().position(|b| *b == 0).unwrap_or(data.len());
        label = format!("{} \"{}\"", tag.name, String::from_utf8_lossy(&data[..end]));
    }
    if let Some(symbol) = &tag.symbol {
        label.push_str(&format!(" ({symbol})"));
    }
    label
}

fn tag_node(movie: &Movie, tag: &TagRecord, parent: &str, folder: &str) -> Map<String, Value> {
    let mut node = Map::new();
    node.insert("id".into(), json!(format!("{parent}/{}", tag.id)));
    node.insert("label".into(), json!(tag_label(movie, tag)));
    node.insert("kind".into(), json!("tag"));
    node.insert("folder".into(), json!(folder));
    node.insert("tag_path".into(), json!(tag.path));
    if tag.path.len() == 1 {
        node.insert("tag_index".into(), json!(tag.path[0]));
    }
    node.insert("code".into(), json!(tag.code));
    node.insert("name".into(), json!(tag.name));
    node.insert("length".into(), json!(tag.length));
    if let Some(id) = tag.character_id {
        node.insert("character_id".into(), json!(id));
    }
    if let Some(symbol) = &tag.symbol {
        node.insert("symbol".into(), json!(symbol));
    }
    node
}

/// Split one timeline into frames. `End` and tags after the last ShowFrame join
/// the last frame, so no tag is dropped and no empty trailing frame appears.
fn frame_groups<'a>(tags: &[&'a TagRecord]) -> Vec<Vec<&'a TagRecord>> {
    let table = table();
    let mut frames: Vec<Vec<&TagRecord>> = vec![];
    let mut pending: Vec<&TagRecord> = vec![];
    let mut trailing: Vec<&TagRecord> = vec![];
    for tag in tags {
        if tag.code == table.end {
            trailing.push(tag);
            continue;
        }
        pending.push(tag);
        if tag.code == table.show_frame {
            frames.push(std::mem::take(&mut pending));
        }
    }
    pending.extend(trailing);
    if !pending.is_empty() {
        match frames.last_mut() {
            Some(last) => last.extend(pending),
            None => frames.push(pending),
        }
    }
    frames
}

fn frame_nodes(movie: &Movie, tags: &[&TagRecord], parent: &str, folder: &str) -> Vec<Value> {
    frame_groups(tags)
        .into_iter()
        .enumerate()
        .map(|(n, group)| {
            let id = format!("{parent}/frame:{}", n + 1);
            let children: Vec<Value> = group
                .iter()
                .map(|tag| Value::Object(tag_node(movie, tag, &id, folder)))
                .collect();
            json!({"id":id,"label":format!("frame {}",n + 1),"kind":"frame","folder":folder,
                "frame":n + 1,"count":children.len(),"children":children})
        })
        .collect()
}

fn folder_node(name: &str, children: Vec<Value>, count: usize) -> Value {
    json!({"id":format!("folder:{name}"),"label":format!("{name} ({count})"),"kind":"folder",
        "folder":name,"count":count,"children":children})
}

fn count_leaves(nodes: &[Value]) -> usize {
    nodes
        .iter()
        .map(|node| {
            let own = usize::from(matches!(node["kind"].as_str(), Some("class" | "script")));
            own + node["children"].as_array().map_or(0, |c| count_leaves(c))
        })
        .sum()
}

#[derive(Default)]
struct Packages {
    children: BTreeMap<String, Packages>,
    classes: Vec<Value>,
}
impl Packages {
    fn insert(&mut self, parts: &[&str], class: Value) {
        match parts.split_first() {
            None => self.classes.push(class),
            Some((head, rest)) => self
                .children
                .entry((*head).to_owned())
                .or_default()
                .insert(rest, class),
        }
    }
    fn emit(self, prefix: &str, parent: &str) -> Vec<Value> {
        let mut out = vec![];
        for (name, child) in self.children {
            let package = if prefix.is_empty() {
                name.clone()
            } else {
                format!("{prefix}.{name}")
            };
            let id = format!("{parent}/pkg:{package}");
            let children = child.emit(&package, parent);
            out.push(json!({"id":id,"label":name,"kind":"package","folder":"scripts",
                "language":"as3","package":package,"children":children}));
        }
        let mut classes = self.classes;
        classes.sort_by(|a, b| a["label"].as_str().cmp(&b["label"].as_str()));
        out.extend(classes);
        out
    }
}

/// AS3: classes per ABC tag, merged by package across tags.
fn as3_nodes(movie: &Movie, units: &[ScriptUnit], parent: &str) -> Vec<Value> {
    let mut seen = HashSet::new();
    let mut packages = Packages::default();
    let mut loose = vec![];
    for unit in units {
        if unit.language != "avm2" || !seen.insert(unit.tag_path.clone()) {
            continue;
        }
        let Some(abc) = &unit.context else {
            // An ABC that does not parse stays visible as one opaque script.
            if unit.kind == "abc_raw" || unit.kind == "diagnostic" {
                loose.push(json!({"id":format!("{parent}/{}",unit.id),"label":format!("{} (ABC no legible)",unit.name),
                    "kind":"script","folder":"scripts","language":"as3","unit_id":unit.id,
                    "tag_path":unit.tag_path,"diagnostics":unit.diagnostics}));
            }
            continue;
        };
        for (index, instance) in abc.instances.iter().enumerate() {
            let (package, name) = split_name(abc, instance.name);
            let unit_id = class_id(&unit.tag_path, index);
            let qname = if package.is_empty() {
                name.clone()
            } else {
                format!("{package}.{name}")
            };
            let node = json!({"id":format!("{parent}/{unit_id}"),"label":name,"kind":"class","folder":"scripts",
                "language":"as3","tag_path":unit.tag_path,"tag_index":unit.tag_path[0],
                "unit_id":unit_id,"class_index":index,"package":package,"qname":qname,
                "superclass":abc.multiname_display(instance.super_name),
                "interface":instance.flags & 0x04 != 0});
            let parts: Vec<&str> = if package.is_empty() {
                vec![]
            } else {
                package.split('.').collect()
            };
            packages.insert(&parts, node);
        }
        // Script-level code that no class owns (global functions, constants).
        let tag_name = movie
            .tags
            .iter()
            .find(|t| t.path == unit.tag_path)
            .map_or("ABC", |t| t.name.as_str());
        for (index, script) in abc.scripts.iter().enumerate() {
            if script.traits.iter().any(|t| t.class.is_some()) {
                continue;
            }
            let init = script.init as usize;
            if let Some(body) = units
                .iter()
                .find(|u| u.tag_path == unit.tag_path && u.method_index == Some(init))
            {
                loose.push(json!({"id":format!("{parent}/{}",body.id),"label":format!("script {index} ({tag_name})"),
                    "kind":"script","folder":"scripts","language":"as3","unit_id":body.id,
                    "tag_path":unit.tag_path,"tag_index":unit.tag_path[0]}));
            }
        }
    }
    let mut out = packages.emit("", parent);
    out.extend(loose);
    out
}

fn script_node(unit: &ScriptUnit, parent: &str, nested: &HashMap<&str, Vec<&ScriptUnit>>) -> Value {
    let children: Vec<Value> = nested
        .get(unit.id.as_str())
        .map(|list| list.iter().map(|child| script_node(child, parent, nested)).collect())
        .unwrap_or_default();
    let mut node = json!({"id":format!("{parent}/{}",unit.id),"label":unit.name,"kind":"script",
        "folder":"scripts","language":"as2","unit_id":unit.id,"tag_path":unit.tag_path,
        "script_kind":unit.kind});
    if unit.tag_path.len() == 1 {
        node["tag_index"] = json!(unit.tag_path[0]);
    }
    if !children.is_empty() {
        node["children"] = Value::Array(children);
    }
    node
}

/// AS1/2: grouped by the frame that holds the script, by sprite, and by button.
fn as2_nodes(movie: &Movie, units: &[ScriptUnit], parent: &str) -> Vec<Value> {
    let table = table();
    let by_path: HashMap<&[usize], &TagRecord> =
        movie.tags.iter().map(|t| (t.path.as_slice(), t)).collect();
    // 1-based frame of every tag inside its own timeline.
    let mut shown: HashMap<Vec<usize>, usize> = HashMap::new();
    let mut frame_of: HashMap<&[usize], usize> = HashMap::new();
    for tag in &movie.tags {
        let timeline = tag.path[..tag.path.len() - 1].to_vec();
        let count = shown.entry(timeline).or_insert(0);
        frame_of.insert(tag.path.as_slice(), *count + 1);
        if tag.code == table.show_frame {
            *count += 1;
        }
    }
    let avm1: Vec<&ScriptUnit> = units
        .iter()
        .filter(|u| u.language == "avm1" && u.kind != "diagnostic")
        .collect();
    let mut nested: HashMap<&str, Vec<&ScriptUnit>> = HashMap::new();
    for unit in avm1.iter().filter(|u| u.kind == "function") {
        if let Some((owner, _)) = unit.id.rsplit_once("/function:") {
            nested.entry(owner).or_default().push(unit);
        }
    }
    let mut timelines: BTreeMap<Vec<usize>, BTreeMap<usize, Vec<Value>>> = BTreeMap::new();
    let mut buttons: BTreeMap<Vec<usize>, Vec<Value>> = BTreeMap::new();
    for unit in avm1.iter().filter(|u| u.kind != "function") {
        let node = script_node(unit, parent, &nested);
        if unit.kind == "button" {
            buttons.entry(unit.tag_path.clone()).or_default().push(node);
        } else {
            let timeline = unit.tag_path[..unit.tag_path.len().saturating_sub(1)].to_vec();
            let frame = frame_of.get(unit.tag_path.as_slice()).copied().unwrap_or(1);
            timelines
                .entry(timeline)
                .or_default()
                .entry(frame)
                .or_default()
                .push(node);
        }
    }
    let frames = |timeline: &[usize], by_frame: BTreeMap<usize, Vec<Value>>, owner: &str| -> Vec<Value> {
        by_frame
            .into_iter()
            .map(|(frame, children)| {
                json!({"id":format!("{owner}/frame:{frame}"),"label":format!("frame {frame}"),"kind":"frame",
                    "folder":"scripts","language":"as2","frame":frame,"tag_path":timeline,"count":children.len(),"children":children})
            })
            .collect()
    };
    let mut out = vec![];
    for (timeline, by_frame) in timelines {
        if timeline.is_empty() {
            out.extend(frames(&timeline, by_frame, parent));
        } else if let Some(sprite) = by_path.get(timeline.as_slice()) {
            let mut node = tag_node(movie, sprite, parent, "scripts");
            let id = node["id"].as_str().unwrap_or_default().to_owned();
            node.insert("language".into(), json!("as2"));
            node.insert("children".into(), Value::Array(frames(&timeline, by_frame, &id)));
            out.push(Value::Object(node));
        }
    }
    for (path, children) in buttons {
        if let Some(button) = by_path.get(path.as_slice()) {
            let mut node = tag_node(movie, button, parent, "scripts");
            node.insert("language".into(), json!("as2"));
            node.insert("children".into(), Value::Array(children));
            out.push(Value::Object(node));
        }
    }
    out
}

/// Build the whole tree. `units` is the script inventory of the same movie.
pub fn tag_tree(movie: &Movie, units: &[ScriptUnit]) -> Value {
    let table = table();
    let mut top_level: Vec<&TagRecord> = vec![];
    let mut inner: HashMap<&[usize], Vec<&TagRecord>> = HashMap::new();
    for tag in &movie.tags {
        if tag.path.len() == 1 {
            top_level.push(tag);
        } else {
            inner
                .entry(&tag.path[..tag.path.len() - 1])
                .or_default()
                .push(tag);
        }
    }
    let mut nodes = vec![];
    for folder in &table.order {
        let folder = folder.as_str();
        match folder {
            "header" => nodes.push(json!({"id":"header","label":"header","kind":"header","folder":"header"})),
            "frames" => {
                let tags: Vec<&TagRecord> = top_level
                    .iter()
                    .copied()
                    .filter(|t| folder_of(t.code) == "frames")
                    .collect();
                let frames = frame_nodes(movie, &tags, &format!("folder:{folder}"), folder);
                if !frames.is_empty() {
                    let count = frames.len();
                    nodes.push(folder_node(folder, frames, count));
                }
            }
            "scripts" => {
                let parent = "folder:scripts";
                let mut children = as3_nodes(movie, units, parent);
                children.extend(as2_nodes(movie, units, parent));
                if !children.is_empty() {
                    let count = count_leaves(&children);
                    nodes.push(folder_node(folder, children, count));
                }
            }
            _ => {
                let parent = format!("folder:{folder}");
                let children: Vec<Value> = top_level
                    .iter()
                    .filter(|t| folder_of(t.code) == folder)
                    .map(|tag| {
                        let mut node = tag_node(movie, tag, &parent, folder);
                        if tag.code == 39 {
                            let id = node["id"].as_str().unwrap_or_default().to_owned();
                            let content = inner.get(tag.path.as_slice()).cloned().unwrap_or_default();
                            let frames = frame_nodes(movie, &content, &id, folder);
                            if !frames.is_empty() {
                                node.insert("children".into(), Value::Array(frames));
                            }
                        }
                        Value::Object(node)
                    })
                    .collect();
                if !children.is_empty() {
                    let count = children.len();
                    nodes.push(folder_node(folder, children, count));
                }
            }
        }
    }
    json!({"total_tags":movie.tags.len(),"nodes":nodes})
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use swf_avm::inventory;

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
        if payload.len() < 63 {
            bytes.extend_from_slice(&((code << 6) | payload.len() as u16).to_le_bytes());
        } else {
            bytes.extend_from_slice(&((code << 6) | 63).to_le_bytes());
            bytes.extend_from_slice(&(payload.len() as u32).to_le_bytes());
        }
        bytes.extend_from_slice(payload);
        bytes
    }
    fn movie(tags: &[Vec<u8>]) -> Movie {
        let mut bytes = b"FWS\x09\0\0\0\0".to_vec();
        bytes.extend(bits(&[(12, 5), (0, 12), (2000, 12), (0, 12), (1000, 12)]));
        bytes.extend_from_slice(&(24u16 * 256).to_le_bytes());
        bytes.extend_from_slice(&2u16.to_le_bytes());
        for t in tags {
            bytes.extend_from_slice(t);
        }
        bytes.extend(tag(0, &[]));
        let len = bytes.len() as u32;
        bytes[4..8].copy_from_slice(&len.to_le_bytes());
        Movie::from_bytes(&bytes, "synthetic.swf").unwrap()
    }
    fn corpus(relative: &str) -> Movie {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../corpus")
            .join(relative);
        Movie::read(path).unwrap()
    }
    fn all<'a>(nodes: &'a [Value], out: &mut Vec<&'a Value>) {
        for node in nodes {
            out.push(node);
            if let Some(children) = node["children"].as_array() {
                all(children, out);
            }
        }
    }
    fn nodes(tree: &Value) -> Vec<&Value> {
        let mut out = vec![];
        all(tree["nodes"].as_array().unwrap(), &mut out);
        out
    }
    fn folder<'a>(tree: &'a Value, name: &str) -> Option<&'a Value> {
        tree["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["folder"] == name && n["kind"] == "folder")
    }

    #[test]
    fn folder_membership_comes_from_the_protocol_table() {
        for (code, expected) in [
            (2, "shapes"),
            (83, "shapes"),
            (46, "morphshapes"),
            (39, "sprites"),
            (37, "texts"),
            (36, "images"),
            (60, "movies"),
            (14, "sounds"),
            (45, "sounds"),
            (34, "buttons"),
            (75, "fonts"),
            (87, "binaryData"),
            (1, "frames"),
            (26, "frames"),
            (12, "frames"),
            (69, "others"),
            (82, "others"),
            (511, "others"),
        ] {
            assert_eq!(folder_of(code), expected, "tag {code}");
        }
        assert_eq!(
            table().order,
            [
                "header", "shapes", "morphshapes", "sprites", "texts", "images", "movies",
                "sounds", "buttons", "fonts", "binaryData", "frames", "others", "scripts",
                "sources"
            ]
        );
        assert_eq!(resource_kind(2), "shape");
        assert_eq!(resource_kind(87), "binary");
        assert_eq!(resource_kind(76), "raw");
    }

    #[test]
    fn every_tag_is_listed_once_in_its_folder_and_empty_folders_are_omitted() {
        let mut sprite = vec![2, 0, 2, 0];
        for _ in 0..2 {
            sprite.extend(tag(26, &[0, 1, 0, 1, 0]));
            sprite.extend(tag(1, &[]));
        }
        sprite.extend(tag(0, &[]));
        let mut symbols = vec![1, 0, 2, 0];
        symbols.extend_from_slice(b"Hero\0");
        let movie = movie(&[
            tag(69, &[0x08, 0, 0, 0]),
            tag(2, &[1, 0, 0, 0, 0, 0]),
            tag(39, &sprite),
            tag(11, &[3, 0, 0, 0]),
            tag(21, &[4, 0, 0xff, 0xd8]),
            tag(14, &[5, 0, 0, 0, 0, 0, 0, 0, 0]),
            tag(34, &[6, 0, 0, 0, 0]),
            tag(48, &[7, 0, 0, 0]),
            tag(87, &[8, 0, 0, 0, 0, 0]),
            tag(60, &[9, 0, 0, 0]),
            tag(46, &[10, 0, 0, 0]),
            tag(76, &symbols),
            tag(26, &[0, 1, 0, 1, 0]),
            tag(43, b"intro\0"),
            tag(12, &[0]),
            tag(1, &[]),
            tag(26, &[0, 1, 0, 1, 0]),
            tag(12, &[0]),
            tag(1, &[]),
        ]);
        let tree = tag_tree(&movie, &inventory(&movie));
        let order: Vec<&str> = tree["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n["folder"].as_str().unwrap())
            .collect();
        // `sources` are the project's authored files: only PySWF has any, so there is none here.
        assert_eq!(
            order,
            [
                "header", "shapes", "morphshapes", "sprites", "texts", "images", "movies",
                "sounds", "buttons", "fonts", "binaryData", "frames", "others", "scripts"
            ]
        );
        assert_eq!(folder(&tree, "frames").unwrap()["count"], 2);
        assert_eq!(folder(&tree, "scripts").unwrap()["count"], 2);
        assert_eq!(folder(&tree, "frames").unwrap()["label"], "frames (2)");
        // The frame list keeps document order and the End tag joins the last frame.
        let frames = folder(&tree, "frames").unwrap()["children"].as_array().unwrap();
        let names = |frame: &Value| -> Vec<String> {
            frame["children"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| t["name"].as_str().unwrap().to_owned())
                .collect()
        };
        assert_eq!(
            names(&frames[0]),
            ["PlaceObject2", "FrameLabel", "DoAction", "ShowFrame"]
        );
        assert_eq!(
            names(&frames[1]),
            ["PlaceObject2", "DoAction", "ShowFrame", "End"]
        );
        assert_eq!(frames[0]["children"][1]["label"], "FrameLabel \"intro\"");
        // The sprite shows its symbol and its own frames.
        let sprite_node = &folder(&tree, "sprites").unwrap()["children"][0];
        assert_eq!(sprite_node["label"], "2: DefineSprite (Hero)");
        assert_eq!(sprite_node["character_id"], 2);
        assert_eq!(sprite_node["symbol"], "Hero");
        let sprite_frames = sprite_node["children"].as_array().unwrap();
        assert_eq!(sprite_frames.len(), 2);
        assert_eq!(names(&sprite_frames[1]), ["PlaceObject2", "ShowFrame", "End"]);
        assert_eq!(sprite_frames[0]["children"][0]["tag_path"], json!([2, 0]));
        assert!(sprite_frames[0]["children"][0].get("tag_index").is_none());
        // Every tag of the movie is reachable at least once; node ids are unique.
        let all_nodes = nodes(&tree);
        let listed: HashSet<Vec<usize>> = all_nodes
            .iter()
            .filter(|n| n["kind"] == "tag")
            .map(|n| {
                n["tag_path"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_u64().unwrap() as usize)
                    .collect()
            })
            .collect();
        for tag in &movie.tags {
            assert!(listed.contains(&tag.path), "{} is missing", tag.id);
        }
        let ids: HashSet<&str> = all_nodes.iter().map(|n| n["id"].as_str().unwrap()).collect();
        assert_eq!(ids.len(), all_nodes.len(), "node ids must be unique");
        assert_eq!(tree["total_tags"], movie.tags.len());
    }

    #[test]
    fn folders_without_tags_do_not_appear() {
        let movie = movie(&[tag(1, &[])]);
        let tree = tag_tree(&movie, &inventory(&movie));
        let folders: Vec<&str> = tree["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n["folder"].as_str().unwrap())
            .collect();
        assert_eq!(folders, ["header", "frames"]);
    }

    #[test]
    fn as3_scripts_are_a_package_to_class_tree_on_the_namespaces_sample() {
        let movie = corpus("parity-20261014/optimized/swf/dev-as3-classes-namespaces/release.fws.swf");
        let tree = tag_tree(&movie, &inventory(&movie));
        let scripts = folder(&tree, "scripts").unwrap();
        let children = scripts["children"].as_array().unwrap();
        let package = children.iter().find(|n| n["kind"] == "package").unwrap();
        assert_eq!(package["label"], "app");
        assert_eq!(package["package"], "app");
        let model = &package["children"][0];
        assert_eq!(
            (model["kind"].as_str(), model["label"].as_str(), model["qname"].as_str()),
            (Some("class"), Some("Model"), Some("app.Model"))
        );
        assert_eq!(model["unit_id"], "tag:6/class:1");
        assert_eq!(model["class_index"], 1);
        assert_eq!(model["interface"], false);
        let main = children.iter().find(|n| n["label"] == "Main").unwrap();
        assert_eq!(main["kind"], "class");
        assert_eq!(main["superclass"], "flash.display::Sprite");
        assert_eq!(main["package"], "");
        // Script-level code that no class owns stays reachable.
        assert!(children.iter().any(|n| n["kind"] == "script" && n["unit_id"].is_string()));
        assert_eq!(scripts["count"], 3);
    }

    #[test]
    fn as2_scripts_are_grouped_by_frame_with_nested_functions_below_their_owner() {
        let movie = corpus("as2_definefunc_regs.swf");
        let tree = tag_tree(&movie, &inventory(&movie));
        let scripts = folder(&tree, "scripts").unwrap();
        let frame = &scripts["children"][0];
        assert_eq!((frame["kind"].as_str(), frame["frame"].as_u64()), (Some("frame"), Some(1)));
        assert_eq!(frame["language"], "as2");
        let units: Vec<&str> = frame["children"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n["unit_id"].as_str().unwrap())
            .collect();
        assert_eq!(units, ["tag:1/actions", "tag:4/actions"]);
        let init = &frame["children"][1];
        let functions = init["children"].as_array().unwrap();
        assert!(!functions.is_empty());
        assert!(functions.iter().all(|f| f["unit_id"].as_str().unwrap().starts_with("tag:4/actions/function:")));
        assert_eq!(scripts["count"], 2 + functions.len());
    }

    #[test]
    fn file_private_classes_group_under_their_file() {
        let movie = corpus("as3_new.flex.swf");
        let tree = tag_tree(&movie, &inventory(&movie));
        let all_nodes = nodes(&tree);
        assert!(
            !all_nodes
                .iter()
                .any(|n| n["kind"] == "package" && n["label"].as_str().is_some_and(|l| l.starts_with("as$"))),
            "compiler-made as$N namespaces must not become packages"
        );
        let chained = all_nodes
            .iter()
            .find(|n| n["kind"] == "package" && n["package"] == "TestChainedAssignments2")
            .unwrap();
        assert_eq!(chained["children"][0]["label"], "TestClass");
    }
}
