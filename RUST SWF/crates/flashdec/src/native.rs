//! RustSWF as an in-process engine. Paths follow the JPEXS export layout so the
//! merger can compare engines file by file (see flashdec_py/flashdec/native.py).
use std::collections::{HashMap, HashSet};
use std::time::Instant;

use serde_json::{Value, json};
use swf_avm::{Abc, ScriptUnit, TraitInfo, disassemble, inventory};
use swf_core::{Movie, TagRecord};

pub struct NativeScript {
    pub path: String,
    pub source: String,
    pub pcode: String,
    pub pairable: bool,
    /// Canonical benchmark identities this file covers, with the evidence our
    /// own parse derived (action bytecode hash, or ABC hash + class name).
    pub units: Vec<Value>,
}

#[derive(Default)]
pub struct NativeResult {
    pub scripts: Vec<NativeScript>,
    pub header: Value,
    pub errors: Vec<String>,
    pub seconds: f64,
}

/// Mirror of JPEXS Helper.makeFileName for the characters that matter on disk.
pub fn make_file_name(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| if "\\/:*?\"<>|".contains(c) || (c as u32) < 0x20 { '_' } else { c })
        .collect();
    let cleaned = cleaned.trim().to_owned();
    if cleaned.is_empty() { "_".into() } else { cleaned }
}

#[derive(Default)]
struct Paths(HashSet<String>);
impl Paths {
    /// JPEXS duplicate rule: name.as, name_2.as, name_3.as ...
    fn unique(&mut self, path: String) -> String {
        let (stem, ext) = path.rsplit_once('.').unwrap_or((&path, ""));
        let mut candidate = path.clone();
        let mut i = 1;
        while self.0.contains(&candidate) {
            i += 1;
            candidate = format!("{stem}_{i}.{ext}");
        }
        self.0.insert(candidate.clone());
        candidate
    }
}

fn u16_at(data: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_le_bytes(data.get(at..at + 2)?.try_into().ok()?))
}

fn export_names(movie: &Movie) -> HashMap<u16, String> {
    let mut names = HashMap::new();
    for tag in movie.tags.iter().filter(|t| t.code == 56) {
        let data = movie.tag_data(tag);
        let Some(count) = u16_at(data, 0) else { continue };
        let mut pos = 2;
        for _ in 0..count {
            let Some(id) = u16_at(data, pos) else { break };
            let Some(end) = data[pos + 2..].iter().position(|b| *b == 0) else { break };
            let name = String::from_utf8_lossy(&data[pos + 2..pos + 2 + end]).into_owned();
            names.entry(id).or_insert(name);
            pos += 3 + end;
        }
    }
    names
}

fn avm1(unit: &ScriptUnit) -> (String, String) {
    let result = swf_decompile::decompile(unit);
    let source = match result.source {
        Some(source) if !source.trim().is_empty() => source,
        _ => {
            let reasons: Vec<_> = result.diagnostics.iter().map(|d| d.message.clone()).collect();
            format!(
                "// Native reconstruction incomplete: {}\n",
                if reasons.is_empty() { "empty output".into() } else { reasons.join("; ") }
            )
        }
    };
    (source, result.pcode)
}

fn event_container(tag: &TagRecord) -> Option<&'static str> {
    Some(match tag.code {
        7 => "DefineButton",
        34 => "DefineButton2",
        26 => "PlaceObject2",
        70 => "PlaceObject3",
        94 => "PlaceObject4",
        _ => return None,
    })
}

fn walk_avm1(movie: &Movie, units: &HashMap<&str, Vec<&ScriptUnit>>, paths: &mut Paths, out: &mut NativeResult) {
    let exports = export_names(movie);
    let by_path: HashMap<&[usize], &TagRecord> =
        movie.tags.iter().map(|t| (t.path.as_slice(), t)).collect();
    let mut frames: HashMap<Vec<usize>, usize> = HashMap::new();
    let prefix_of = |parent: &[usize]| -> String {
        if parent.is_empty() {
            return String::new();
        }
        let Some(sprite) = by_path.get(parent) else { return String::new() };
        let id = sprite.character_id.unwrap_or(0);
        let mut name = format!("DefineSprite_{id}");
        if let Some(export) = exports.get(&id) {
            name.push('_');
            name.push_str(export);
        }
        format!("{}/", make_file_name(&name))
    };
    for tag in &movie.tags {
        let parent = tag.path[..tag.path.len() - 1].to_vec();
        let frame = *frames.entry(parent.clone()).or_insert(1);
        let tag_units = units.get(tag.id.as_str());
        match tag.code {
            1 => {
                frames.insert(parent, frame + 1);
            }
            12 | 59 => {
                let Some(unit) = tag_units.and_then(|u| u.iter().find(|u| u.id.ends_with("/actions"))) else {
                    continue;
                };
                let (source, pcode) = avm1(unit);
                let export = (tag.code == 59)
                    .then(|| u16_at(movie.tag_data(tag), 0))
                    .flatten()
                    .and_then(|id| exports.get(&id));
                let path = match export {
                    Some(export) => {
                        let parts: Vec<_> = export.split('.').map(make_file_name).collect();
                        format!("{}.as", parts.join("/"))
                    }
                    None => format!(
                        "{}frame_{frame}/{}.as",
                        prefix_of(&parent),
                        if tag.code == 12 { "DoAction" } else { "DoInitAction" }
                    ),
                };
                let path = paths.unique(path);
                let units = vec![json!({"document_id": unit.id, "bytecode_sha256": unit.sha256})];
                out.scripts.push(NativeScript { path, source, pcode, pairable: false, units });
            }
            _ => {
                let (Some(kind), Some(tag_units)) = (event_container(tag), tag_units) else { continue };
                let (container, label) = if matches!(tag.code, 7 | 34) {
                    let id = tag.character_id.unwrap_or(0);
                    let mut name = format!("{kind}_{id}");
                    if let Some(export) = exports.get(&id) {
                        name.push('_');
                        name.push_str(export);
                    }
                    (format!("{}/", make_file_name(&name)), "BUTTONCONDACTION")
                } else {
                    let character = u16_at(movie.tag_data(tag), 3).unwrap_or(0);
                    (format!("{}frame_{frame}/{kind}_{character}/", prefix_of(&parent)), "CLIPACTIONRECORD")
                };
                let own = format!("{}/", tag.id);
                for (ordinal, unit) in tag_units
                    .iter()
                    .filter(|u| u.id.strip_prefix(&own).is_some_and(|rest| !rest.contains('/')))
                    .enumerate()
                {
                    let (source, pcode) = avm1(unit);
                    let path = paths.unique(format!("{container}{label}_{ordinal}.as"));
                    let units = vec![json!({"document_id": unit.id, "bytecode_sha256": unit.sha256})];
                    out.scripts.push(NativeScript { path, source, pcode, pairable: true, units });
                }
            }
        }
    }
}

fn split_name(abc: &Abc, index: u32) -> (String, String) {
    let display = abc.multiname_display(index);
    match display.rsplit_once("::") {
        Some((ns, name)) => (ns.to_owned(), name.to_owned()),
        None => (String::new(), display),
    }
}

fn short_type(abc: &Abc, index: u32) -> String {
    if index == 0 {
        return "*".into();
    }
    split_name(abc, index).1
}

fn visibility(abc: &Abc, index: u32) -> &'static str {
    let kind = abc
        .multinames
        .get(index as usize)
        .and_then(|m| abc.namespaces.get(m.namespace as usize))
        .map(|n| n.kind)
        .unwrap_or(0x16);
    match kind {
        0x05 => "private",
        0x17 => "internal",
        0x18 | 0x1a => "protected",
        _ => "public",
    }
}

fn signature(abc: &Abc, method: u32, name: &str, keyword: &str) -> String {
    let Some(info) = abc.methods.get(method as usize) else {
        return format!("function {keyword}{name}()");
    };
    let params: Vec<String> = info
        .param_types
        .iter()
        .enumerate()
        .map(|(i, t)| {
            let pname = info
                .param_names
                .get(i)
                .and_then(|n| abc.string(*n))
                .filter(|n| !n.is_empty())
                .map(str::to_owned)
                .unwrap_or_else(|| format!("param{}", i + 1));
            format!("{pname}:{}", short_type(abc, *t))
        })
        .collect();
    // keyword "new" marks a constructor: no return type, no keyword in the text.
    let (keyword, ret) = if keyword == "new" {
        ("", String::new())
    } else {
        (keyword, format!(":{}", short_type(abc, info.return_type)))
    };
    format!("function {keyword}{name}({}){ret}", params.join(", "))
}

struct AbcUnits<'a> {
    by_method: HashMap<usize, &'a ScriptUnit>,
    /// false: only collect method ownership (the verified document is used).
    bodies: bool,
}
impl AbcUnits<'_> {
    fn body(&self, method: u32, indent: &str) -> Option<String> {
        if !self.bodies {
            return Some(String::new());
        }
        let unit = self.by_method.get(&(method as usize))?;
        let source = swf_decompile::decompile(unit).source?;
        Some(
            source
                .lines()
                .map(|l| if l.trim().is_empty() { String::new() } else { format!("{indent}{l}") })
                .collect::<Vec<_>>()
                .join("\n"),
        )
    }
    fn pcode(&self, methods: &[u32]) -> String {
        let mut text = String::new();
        for m in methods {
            if let Some(unit) = self.by_method.get(&(*m as usize)) {
                text.push_str(&format!("; method {m}: {}\n{}\n", unit.name, disassemble(unit).pcode));
            }
        }
        text
    }
}

fn emit_trait(abc: &Abc, units: &AbcUnits, t: &TraitInfo, is_static: bool, text: &mut String, methods: &mut Vec<u32>) {
    let (_, name) = split_name(abc, t.name);
    let vis = visibility(abc, t.name);
    let stat = if is_static { "static " } else { "" };
    match t.kind {
        0 | 6 => {
            let keyword = if t.kind == 6 { "const" } else { "var" };
            text.push_str(&format!("        {vis} {stat}{keyword} {name}:{};\n", short_type(abc, t.type_name)));
        }
        1..=3 | 5 => {
            let Some(method) = t.method else { return };
            methods.push(method);
            let accessor = match t.kind {
                2 => "get ",
                3 => "set ",
                _ => "",
            };
            let flags = if t.attributes & 0x02 != 0 { "override " } else { "" };
            text.push_str(&format!("        {flags}{vis} {stat}{}\n", signature(abc, method, &name, accessor)));
            match units.body(method, "            ") {
                Some(body) => text.push_str(&format!("        {{\n{body}\n        }}\n")),
                None => text.push_str(&format!(
                    "        {{\n            // Incomplete function {name}(); original method:{method} retained as P-code\n        }}\n"
                )),
            }
        }
        _ => {}
    }
}

/// Class text assembled from per-method reconstruction when the verified
/// whole-ABC document is unavailable. Missing bodies are marked, not invented.
fn class_skeleton(abc: &Abc, units: &AbcUnits, ci: usize) -> (String, String, Vec<u32>) {
    let instance = &abc.instances[ci];
    let (package, name) = split_name(abc, instance.name);
    let mut methods = vec![];
    let mut text = format!("package {package}\n{{\n").replace("package \n", "package\n");
    let interface = instance.flags & 0x04 != 0;
    let mut decl = format!(
        "    public {}{} {name}",
        if instance.flags & 0x01 == 0 && !interface { "dynamic " } else { "" },
        if interface { "interface" } else { "class" }
    );
    if instance.flags & 0x02 != 0 {
        decl = decl.replacen("public ", "public final ", 1);
    }
    let parent = abc.multiname_display(instance.super_name);
    if instance.super_name != 0 && parent != "Object" {
        decl.push_str(&format!(" extends {}", parent.replace("::", ".")));
    }
    if !instance.interfaces.is_empty() {
        let list: Vec<_> = instance.interfaces.iter().map(|i| abc.multiname_display(*i).replace("::", ".")).collect();
        decl.push_str(&format!(" {} {}", if interface { "extends" } else { "implements" }, list.join(", ")));
    }
    text.push_str(&decl);
    text.push_str("\n    {\n");
    if let Some(class) = abc.classes.get(ci) {
        methods.push(class.init);
        for t in &class.traits {
            emit_trait(abc, units, t, true, &mut text, &mut methods);
        }
    }
    if !interface {
        methods.push(instance.init);
        text.push_str(&format!("        public {}\n", signature(abc, instance.init, &name, "new")));
        match units.body(instance.init, "            ") {
            Some(body) => text.push_str(&format!("        {{\n{body}\n        }}\n")),
            None => text.push_str(&format!(
                "        {{\n            // Incomplete function {name}(); original method:{} retained as P-code\n        }}\n",
                instance.init
            )),
        }
    }
    for t in &instance.traits {
        emit_trait(abc, units, t, false, &mut text, &mut methods);
    }
    text.push_str("    }\n}\n");
    (format!("{}{}.as", package_dir(&package), make_file_name(&name)), text, methods)
}

fn package_dir(package: &str) -> String {
    package
        .split('.')
        .filter(|p| !p.is_empty())
        .map(|p| format!("{}/", make_file_name(p)))
        .collect()
}

fn walk_avm2(movie: &Movie, units: &HashMap<&str, Vec<&ScriptUnit>>, paths: &mut Paths, out: &mut NativeResult) {
    for tag in movie.tags.iter().filter(|t| matches!(t.code, 72 | 82)) {
        let Some(tag_units) = units.get(tag.id.as_str()) else { continue };
        let Some(first) = tag_units.first() else { continue };
        let Some(abc) = first.context.clone() else {
            let reasons: Vec<_> = first.diagnostics.iter().map(|d| d.message.clone()).collect();
            out.errors.push(format!("{}: {}", tag.id, reasons.join("; ")));
            continue;
        };
        let mut abc_units = AbcUnits {
            by_method: tag_units.iter().filter_map(|u| Some((u.method_index?, *u))).collect(),
            bodies: false,
        };
        let document = swf_decompile::decompile_document(first);
        let mut documents: HashMap<usize, String> = HashMap::new();
        let mut document_paths: HashMap<usize, String> = HashMap::new();
        if document.state != "pcode_only" {
            for doc in document.documents {
                document_paths.insert(doc.class_index, doc.path);
                documents.insert(doc.class_index, doc.source);
            }
        }
        for ci in 0..abc.instances.len() {
            abc_units.bodies = !documents.contains_key(&ci);
            let (skeleton_path, skeleton, methods) = class_skeleton(&abc, &abc_units, ci);
            let (path, source) = match documents.remove(&ci) {
                Some(source) => (document_paths.remove(&ci).unwrap_or(skeleton_path), source),
                None => (skeleton_path, skeleton),
            };
            let path = paths.unique(path);
            let pcode = abc_units.pcode(&methods);
            let units = vec![json!({"document_id": format!("{}/class:{ci}", tag.id), "abc_sha256": first.abc_sha256,
                "name": abc.multiname_display(abc.instances[ci].name)})];
            out.scripts.push(NativeScript { path, source, pcode, pairable: false, units });
        }
    }
}

pub fn decompile(movie: &Movie) -> NativeResult {
    let started = Instant::now();
    let mut out = NativeResult::default();
    let info = &movie.info;
    out.header = json!({"signature": info.signature, "version": info.version, "frame_rate": info.frame_rate,
        "frame_count": info.frame_count, "width": info.width, "height": info.height, "tag_count": info.tag_count});
    let all_units = inventory(movie);
    let ids: HashMap<&[usize], &str> = movie.tags.iter().map(|t| (t.path.as_slice(), t.id.as_str())).collect();
    let mut units: HashMap<&str, Vec<&ScriptUnit>> = HashMap::new();
    for unit in &all_units {
        if let Some(id) = ids.get(unit.tag_path.as_slice()) {
            units.entry(id).or_default().push(unit);
        }
    }
    let mut paths = Paths::default();
    walk_avm2(movie, &units, &mut paths, &mut out);
    walk_avm1(movie, &units, &mut paths, &mut out);
    out.seconds = started.elapsed().as_secs_f64();
    out
}
