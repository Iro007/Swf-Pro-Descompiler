use anyhow::{Context, Result, bail};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use proto::{bad_request, error_kind, kind, not_found, not_open, op_error, unsupported};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    fs,
    io::{self, BufRead, Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Instant,
};
use swf_avm::{Abc, ScriptUnit, class_outline, disassemble, inventory};
use swf_core::{
    ContainerCompression, Movie, TagRecord, export_resource, resource_metadata, supported_formats,
};
use swf_decompile::DocumentResult;
mod atomic;
mod classes;
mod job;
mod proto;
mod supervise;
mod tree;
use atomic::{atomic_write, write_with_sidecar};

/// Operations of engines/protocol.json that this worker implements. The test
/// at the bottom keeps this list identical to `worker.ops` in engine.json.
const CANONICAL_OPS: &[&str] = &[
    "capabilities",
    "open",
    "header",
    "tag_tree",
    "raw",
    "resources",
    "resource_info",
    "extract_image",
    "extract_sound",
    "extract_font",
    "extract_text",
    "shape_svg",
    "scripts",
    "disassemble",
    "decompile",
];
/// RustSWF-specific operations kept for the benchmarks and earlier hosts.
const NATIVE_OPS: &[&str] = &[
    "info",
    "outline",
    "metadata",
    "abc_metadata",
    "method_context",
    "decompile_document",
    "source_document",
    "roundtrip",
    "asset",
    "export_resource",
    "export_script",
];
const MAX_INLINE_ASSET_BYTES: usize = 4 * 1024 * 1024;
const PCODE_PAGE_CHARS: usize = 65536;

struct Session {
    movie: Movie,
    units: Vec<ScriptUnit>,
    /// Class documents are decompiled once per ABC tag, not once per class.
    documents: Mutex<HashMap<String, Arc<DocumentResult>>>,
    /// Verified player-SDK hierarchies, parsed once per (path, sha256).
    hierarchies: Mutex<HashMap<String, Arc<swf_decompile::VerifiedHierarchy>>>,
}
impl Session {
    fn open(path: &Path) -> Result<Self> {
        let movie = Movie::read(path)?;
        let units = inventory(&movie);
        Ok(Self {
            movie,
            units,
            documents: Mutex::new(HashMap::new()),
            hierarchies: Mutex::new(HashMap::new()),
        })
    }
    /// A tag is addressed by `tag_path`, by `tag_index` (top level) or by `id` ("tag:7/3").
    fn tag(&self, args: &Value) -> Result<&TagRecord> {
        if let Some(path) = args.get("tag_path").filter(|v| !v.is_null()) {
            let path: Option<Vec<usize>> = path
                .as_array()
                .and_then(|list| list.iter().map(|v| v.as_u64().map(|n| n as usize)).collect());
            let path = path.ok_or_else(|| bad_request("tag_path debe ser una lista de enteros"))?;
            return self
                .movie
                .tags
                .iter()
                .find(|t| t.path == path)
                .ok_or_else(|| not_found("Recurso desconocido"));
        }
        if let Some(index) = args["tag_index"].as_u64() {
            return self
                .movie
                .tags
                .iter()
                .find(|t| t.path.len() == 1 && t.path[0] == index as usize)
                .ok_or_else(|| not_found("Recurso desconocido"));
        }
        let id = args["id"].as_str().unwrap_or("");
        self.movie
            .tags
            .iter()
            .find(|t| t.id == id)
            .ok_or_else(|| not_found("Recurso desconocido"))
    }
    fn unit(&self, id: &str) -> Result<&ScriptUnit> {
        self.units
            .iter()
            .find(|u| u.id == id)
            .ok_or_else(|| not_found("Unidad de script desconocida"))
    }
    /// Methods of an AS3 class and the ABC they belong to.
    fn class_parts(&self, id: &str) -> Result<(classes::ClassRef, Arc<Abc>, Vec<&ScriptUnit>)> {
        let class = classes::parse(id).ok_or_else(|| not_found("Unidad de script desconocida"))?;
        let abc = self
            .units
            .iter()
            .find(|u| u.language == "avm2" && u.tag_path == class.tag_path)
            .and_then(|u| u.context.clone())
            .filter(|abc| class.index < abc.instances.len())
            .ok_or_else(|| not_found("La clase no existe en este SWF"))?;
        let methods = classes::method_indices(&abc, class.index);
        let units = methods
            .iter()
            .filter_map(|m| {
                self.units.iter().find(|u| {
                    u.language == "avm2" && u.tag_path == class.tag_path && u.method_index == Some(*m)
                })
            })
            .collect();
        Ok((class, abc, units))
    }
    /// P-code of every method a class declares, with a heading per method.
    fn class_pcode(&self, id: &str) -> Result<(String, String, Vec<swf_core::Diagnostic>)> {
        let (_, _, units) = self.class_parts(id)?;
        let mut text = String::new();
        let mut state = "decoded".to_owned();
        let mut diagnostics = vec![];
        for unit in units {
            let listing = disassemble(unit);
            if listing.state != "decoded" {
                state = listing.state.clone();
            }
            diagnostics.extend(listing.diagnostics.clone());
            text.push_str(&format!("; ---- {} ({}) ----\n{}\n", unit.name, unit.id, listing.pcode));
        }
        Ok((text, state, diagnostics))
    }
    /// The optional `hierarchy_swc` + `hierarchy_sha256` contract lets class
    /// documents prove their external ancestors (the player SDK's playerglobal).
    fn hierarchy(&self, args: &Value) -> Result<Option<Arc<swf_decompile::VerifiedHierarchy>>> {
        let Some(path) = args["hierarchy_swc"].as_str() else {
            return Ok(None);
        };
        let sha = args["hierarchy_sha256"]
            .as_str()
            .ok_or_else(|| bad_request("Falta SHA-256 del contrato SWC"))?;
        let key = format!("{path}|{sha}");
        if let Ok(cache) = self.hierarchies.lock()
            && let Some(found) = cache.get(&key)
        {
            return Ok(Some(found.clone()));
        }
        let loaded = Arc::new(swf_decompile::VerifiedHierarchy::load_swc(
            Path::new(path),
            sha,
        )?);
        if let Ok(mut cache) = self.hierarchies.lock() {
            cache.insert(key, loaded.clone());
        }
        Ok(Some(loaded))
    }
    fn decompile_class(&self, id: &str, args: &Value) -> Result<Value> {
        let (class, _, _) = self.class_parts(id)?;
        let first = self
            .units
            .iter()
            .find(|u| u.language == "avm2" && u.tag_path == class.tag_path && u.context.is_some())
            .ok_or_else(|| not_found("La clase no existe en este SWF"))?;
        let key = format!(
            "{}|{}",
            classes::class_id(&class.tag_path, 0),
            args["hierarchy_sha256"].as_str().unwrap_or("")
        );
        let cached = self
            .documents
            .lock()
            .ok()
            .and_then(|cache| cache.get(&key).cloned());
        let document = match cached {
            Some(document) => document,
            None => {
                let hierarchy = self.hierarchy(args)?;
                let computed = Arc::new(match hierarchy.as_deref() {
                    Some(hierarchy) => {
                        swf_decompile::decompile_document_with_hierarchy(first, hierarchy)
                    }
                    None => swf_decompile::decompile_document(first),
                });
                if let Ok(mut cache) = self.documents.lock() {
                    cache.insert(key, computed.clone());
                }
                computed
            }
        };
        let found = document
            .documents
            .iter()
            .find(|d| d.class_index == class.index);
        let (pcode, listing_state, mut diagnostics) = self.class_pcode(id)?;
        diagnostics.extend(document.diagnostics.clone());
        let mut source = found.map(|d| d.source.clone());
        let mut state = if source.is_some() { "structured" } else { "pcode_only" };
        if source.as_ref().is_some_and(|s| s.len() > 2 * 1024 * 1024) {
            source = None;
            state = "pcode_only";
            diagnostics.push(swf_core::Diagnostic::new(
                "source_output_limit",
                "La fuente excede el límite de 2 MiB de la vista",
                None,
            ));
        }
        let full_chars = pcode.chars().count();
        let pcode_view = if full_chars > PCODE_PAGE_CHARS {
            let mut view: String = pcode.chars().take(PCODE_PAGE_CHARS).collect();
            view.push_str("\n; Vista parcial: pida el P-code con disassemble y offset\n");
            view
        } else {
            pcode
        };
        Ok(json!({"unit_id":id,"engine":"rust","state":state,"source":source,"pcode":pcode_view,
            "pcode_total":full_chars,"pcode_state":listing_state,"diagnostics":diagnostics,
            "document":found.map(|d| json!({"path":d.path,"script_index":d.script_index,"class_index":d.class_index})),
            "source_tier":document.source_tier,"scope":"class"}))
    }
    fn extract(&self, op: &str, args: &Value) -> Result<Value> {
        let tag = self.tag(args)?;
        let (allowed, preferred): (&[&str], &str) = match op {
            "extract_image" => (&["images"], "png"),
            "extract_sound" => (&["sounds"], ""),
            "extract_font" => (&["fonts"], "svg"),
            "extract_text" => (&["texts"], "txt"),
            _ => (&["shapes", "morphshapes"], "svg"),
        };
        if !allowed.contains(&tree::folder_of(tag.code)) {
            return Err(bad_request(format!(
                "{} ({}) no sirve para {op}",
                tag.name, tag.id
            )));
        }
        let formats = supported_formats(&self.movie, tag);
        let format = match args["format"].as_str() {
            Some(format) => format.to_owned(),
            None if formats.iter().any(|f| f == preferred) => preferred.to_owned(),
            None => formats
                .iter()
                .find(|f| f.as_str() != "raw")
                .cloned()
                .unwrap_or_else(|| "raw".into()),
        };
        if op == "shape_svg" && format != "svg" {
            return Err(bad_request("shape_svg solo produce SVG"));
        }
        let asset = export_resource(&self.movie, tag, &format)?;
        let mut result = json!({"id":tag.id,"format":format,"mime":asset.mime,"extension":asset.extension,
            "bytes":asset.bytes.len(),"diagnostics":asset.diagnostics});
        let mut stored = false;
        if let Some(path) = args["path"].as_str() {
            atomic_write(Path::new(path), &asset.bytes)?;
            result["path"] = json!(path);
            stored = true;
        } else if let Some(dir) = args["dir"].as_str() {
            let dir = PathBuf::from(dir);
            fs::create_dir_all(&dir)?;
            let path = dir.join(format!(
                "{}-{}.{}",
                safe_name(&tag.id),
                safe_name(&format),
                asset.extension
            ));
            if !path.exists() {
                atomic_write(&path, &asset.bytes)?;
            }
            result["path"] = json!(path);
            stored = true;
        }
        if args["inline"].as_bool().unwrap_or(false) || !stored {
            if asset.bytes.len() > MAX_INLINE_ASSET_BYTES {
                return Err(op_error(
                    kind::LIMIT,
                    "El recurso supera 4 MiB; indique path o dir para escribirlo",
                ));
            }
            result["data_base64"] = json!(STANDARD.encode(&asset.bytes));
        }
        Ok(result)
    }
    /// One page of a tag's payload (`tag_index`, `tag_path` or `id`) or, with `scope: "file"`, of
    /// the whole file. The answer is PySWF's: `hex` or `base64`, `total`, `length`, `limit`.
    fn raw(&self, args: &Value) -> Result<Value> {
        let offset = integer_argument(args, "offset", 0, 0, 1 << 40)? as usize;
        let limit = integer_argument(args, "limit", 4096, 1, 65536)? as usize;
        let encoding = match args.get("encoding") {
            None | Some(Value::Null) => "hex",
            Some(value) if *value == "hex" => "hex",
            Some(value) if *value == "base64" => "base64",
            Some(_) => return Err(bad_request("Codificación desconocida: usa hex o base64")),
        };
        let (payload, mut result): (&[u8], Value) = if args["scope"].as_str() == Some("file") {
            (&self.movie.original, json!({"scope":"file","name":"Archivo SWF"}))
        } else {
            let tag = self.tag(args)?;
            let mut meta = json!({"scope":"tag","tag_index":tag.path[0],"name":tag.name,"type":tag.code});
            if tag.path.len() > 1 {
                meta["tag_path"] = json!(tag.path);
            }
            (self.movie.tag_data(tag), meta)
        };
        if offset > payload.len() {
            return Err(bad_request("Desplazamiento fuera del contenido"));
        }
        let page = &payload[offset..offset.saturating_add(limit).min(payload.len())];
        result["offset"] = json!(offset);
        result["limit"] = json!(limit);
        result["encoding"] = json!(encoding);
        result["total"] = json!(payload.len());
        result["length"] = json!(page.len());
        if encoding == "hex" {
            result["hex"] = json!(hex(page));
        } else {
            result["base64"] = json!(STANDARD.encode(page));
        }
        result["revision"] = json!(self.movie.info.sha256);
        Ok(result)
    }
    fn dispatch(&self, op: &str, args: &Value) -> Result<Value> {
        let id = args["unit_id"]
            .as_str()
            .or_else(|| args["id"].as_str())
            .unwrap_or("");
        let offset = args["offset"].as_u64().unwrap_or(0) as usize;
        let limit = (args["limit"].as_u64().unwrap_or(200) as usize).min(500);
        let query = args["query"].as_str().unwrap_or("").to_lowercase();
        match op {
            "capabilities" => Ok(capabilities()),
            "info" => Ok(
                json!({"info":self.movie.info,"diagnostics":self.movie.diagnostics,"script_count":self.units.len()}),
            ),
            "header" => {
                let mut header = self.movie.header()?;
                // `scripts` also lists the AVM1 functions nested in an action; the count of code units
                // that the header shows (PySWF's) leaves them out.
                header.script_count = Some(self.units.iter().filter(|u| u.kind != "function").count());
                Ok(serde_json::to_value(header)?)
            }
            "tag_tree" => Ok(tree::tag_tree(&self.movie, &self.units)),
            "resource_info" => {
                let tag = self.tag(args)?;
                let mut result = json!({"id":tag.id,"tag_path":tag.path,"kind":tree::resource_kind(tag.code),
                    "code":tag.code,"name":tag.name,"character_id":tag.character_id,
                    "formats":supported_formats(&self.movie,tag),"parsed":resource_metadata(&self.movie,tag)});
                if tag.path.len() == 1 {
                    result["tag_index"] = json!(tag.path[0]);
                }
                Ok(result)
            }
            "extract_image" | "extract_sound" | "extract_font" | "extract_text" | "shape_svg" => {
                self.extract(op, args)
            }
            "resources" => {
                let codes = args["codes"].as_array();
                let other = args["other"].as_bool().unwrap_or(false);
                let known_categories = [
                    6, 20, 21, 35, 36, 90, 2, 22, 32, 83, 46, 84, 14, 15, 18, 19, 45, 89, 10, 11,
                    13, 33, 37, 48, 62, 73, 74, 75, 88, 91, 39, 7, 17, 23, 34, 1, 4, 5, 26, 28, 43,
                    70, 86, 94, 12, 59, 72, 82,
                ];
                let items: Vec<_> = self
                    .movie
                    .tags
                    .iter()
                    .filter(|t| {
                        (query.is_empty()
                            || format!(
                                "{} {} {} {:?}",
                                t.id,
                                t.name,
                                t.symbol.as_deref().unwrap_or(""),
                                t.character_id
                            )
                            .to_lowercase()
                            .contains(&query))
                            && (if other {
                                !known_categories.contains(&t.code)
                            } else {
                                codes.is_none_or(|list| {
                                    list.iter().any(|c| c.as_u64() == Some(t.code as u64))
                                })
                            })
                    })
                    .collect();
                Ok(
                    json!({"total":items.len(),"offset":offset,"items":items.iter().skip(offset).take(limit).map(|t| {
                    let mut value = serde_json::to_value(t).unwrap();
                    value["formats"] = json!(supported_formats(&self.movie,t)); value
                }).collect::<Vec<_>>()}),
                )
            }
            "scripts" => {
                let items: Vec<_> = self
                    .units
                    .iter()
                    .filter(|u| {
                        query.is_empty()
                            || format!("{} {} {}", u.id, u.name, u.language)
                                .to_lowercase()
                                .contains(&query)
                    })
                    .collect();
                Ok(
                    json!({"total":items.len(),"offset":offset,"items":items.iter().skip(offset).take(limit).collect::<Vec<_>>() }),
                )
            }
            "outline" => {
                let classes = class_outline(&self.units);
                Ok(
                    json!({"total":classes.len(),"offset":offset,"items":classes.iter().skip(offset).take(limit).collect::<Vec<_>>()}),
                )
            }
            "metadata" => Ok(resource_metadata(&self.movie, self.tag(args)?)),
            "abc_metadata" => {
                let unit = self.unit(id)?;
                let abc = unit
                    .context
                    .as_ref()
                    .ok_or_else(|| bad_request("La unidad no tiene un ABC válido"))?;
                use sha2::{Digest, Sha256};
                Ok(
                    json!({"unit_id":id,"abc_version":unit.abc_version,"total":abc.metadata.len(),"offset":offset,
                    "items":abc.metadata.iter().skip(offset).take(limit).map(|m|json!({
                        "name_index":m.name,"name":abc.string(m.name),"keys":m.keys,"values":m.values,
                        "entries":m.keys.iter().zip(&m.values).map(|(k,v)|json!({"key":abc.string(*k),"value":abc.string(*v)})).collect::<Vec<_>>(),
                        "raw_bytes":m.raw.len(),"raw_sha256":format!("{:x}",Sha256::digest(&m.raw)),"original_encoding_preserved":true
                    })).collect::<Vec<_>>() }),
                )
            }
            "roundtrip" => {
                let compression = ContainerCompression::parse(
                    args["compression"].as_str().unwrap_or("original"),
                )?;
                let output = self.movie.encoded(compression)?;
                let reloaded = Movie::from_bytes(&output, &self.movie.info.filename)?;
                let path = PathBuf::from(
                    args["path"]
                        .as_str()
                        .ok_or_else(|| bad_request("Falta destino"))?,
                );
                atomic_write(&path, &output)?;
                use sha2::{Digest, Sha256};
                Ok(
                    json!({"path":path,"signature":reloaded.info.signature,"version":reloaded.info.version,"bytes":output.len(),
                    "input_sha256":self.movie.info.sha256,"output_sha256":format!("{:x}",Sha256::digest(&output)),
                    "normalized_sha256":format!("{:x}",Sha256::digest(&self.movie.normalized)),"normalized_bytes_identical":reloaded.normalized==self.movie.normalized,
                    "writer_scope":"SWF container only; tags, bytecode and metadata retained as original raw bytes"}),
                )
            }
            "disassemble" if classes::parse(id).is_some() => {
                let (text, state, diagnostics) = self.class_pcode(id)?;
                let total = text.chars().count();
                let page: String = text.chars().skip(offset).take(PCODE_PAGE_CHARS).collect();
                Ok(json!({"unit_id":id,"state":state,"diagnostics":diagnostics,"pcode":page,
                    "pcode_total":total,"offset":offset,"scope":"class"}))
            }
            "disassemble" => {
                let listing = disassemble(self.unit(id)?);
                let page_text = listing
                    .pcode
                    .chars()
                    .skip(offset)
                    .take(PCODE_PAGE_CHARS)
                    .collect::<String>();
                Ok(
                    json!({"unit_id":id,"state":listing.state,"diagnostics":listing.diagnostics,"instructions":listing.instructions.iter().skip(args["instruction_offset"].as_u64().unwrap_or(0) as usize).take(limit).collect::<Vec<_>>(),"instruction_total":listing.instructions.len(),"pcode":page_text,"pcode_total":listing.pcode.chars().count(),"offset":offset}),
                )
            }
            "decompile" if classes::parse(id).is_some() => self.decompile_class(id, args),
            "decompile" => {
                let mut result = swf_decompile::decompile(self.unit(id)?);
                if result
                    .source
                    .as_ref()
                    .is_some_and(|s| s.len() > 2 * 1024 * 1024)
                {
                    result.source = None;
                    result.state = "pcode_only".into();
                    result.diagnostics.push(swf_core::Diagnostic::new(
                        "source_output_limit",
                        "La fuente excede el límite de 2 MiB de la vista",
                        None,
                    ));
                }
                let full_chars = result.pcode.chars().count();
                if full_chars > 65536 {
                    result.pcode = result.pcode.chars().take(65536).collect();
                    result.pcode.push_str(
                        "\n; Vista parcial: exporte el listing o consulte la siguiente página\n",
                    );
                }
                let mut value = serde_json::to_value(result)?;
                value["pcode_total"] = json!(full_chars);
                value["engine"] = json!("rust");
                Ok(value)
            }
            "method_context" => {
                let context = swf_avm::symbols::MethodContext::from_unit(self.unit(id)?)
                    .context("La unidad no tiene contexto ABC verificable")?;
                Ok(serde_json::to_value(context)?)
            }
            "decompile_document" | "source_document" => {
                let hierarchy = self.hierarchy(args)?;
                let mut result = if let Some(hierarchy) = hierarchy.as_deref() {
                    swf_decompile::decompile_document_with_hierarchy(self.unit(id)?, hierarchy)
                } else {
                    swf_decompile::decompile_document(self.unit(id)?)
                };
                if result
                    .documents
                    .iter()
                    .any(|document| document.source.len() > 2 * 1024 * 1024)
                {
                    result.documents.clear();
                    result.state = "pcode_only".into();
                    result.diagnostics.push(swf_core::Diagnostic::new(
                        "source_output_limit",
                        "Un documento excede el límite de 2 MiB",
                        None,
                    ));
                }
                Ok(serde_json::to_value(result)?)
            }
            "raw" => self.raw(args),
            "asset" => {
                let format = args["format"].as_str().unwrap_or("raw");
                let dir = PathBuf::from(
                    args["dir"]
                        .as_str()
                        .ok_or_else(|| bad_request("Falta directorio"))?,
                );
                fs::create_dir_all(&dir)?;
                let tag = self.tag(args)?;
                let asset = export_resource(&self.movie, tag, format)?;
                let filename = format!(
                    "{}-{}.{}",
                    safe_name(id),
                    safe_name(format),
                    asset.extension
                );
                let path = dir.join(filename);
                if !path.exists() {
                    atomic_write(&path, &asset.bytes)?;
                }
                Ok(
                    json!({"path":path,"mime":asset.mime,"extension":asset.extension,"bytes":asset.bytes.len(),"diagnostics":asset.diagnostics}),
                )
            }
            "export_resource" => {
                let format = args["format"].as_str().unwrap_or("raw");
                let path = PathBuf::from(
                    args["path"]
                        .as_str()
                        .ok_or_else(|| bad_request("Falta destino"))?,
                );
                let asset = export_resource(&self.movie, self.tag(args)?, format)?;
                atomic_write(&path, &asset.bytes)?;
                Ok(json!({"path":path,"bytes":asset.bytes.len(),"diagnostics":asset.diagnostics}))
            }
            "export_script" => {
                let path = PathBuf::from(
                    args["path"]
                        .as_str()
                        .ok_or_else(|| bad_request("Falta destino"))?,
                );
                let unit = self.unit(id)?;
                let format = args["format"].as_str().unwrap_or("pcode");
                let sidecar = path.with_extension(format!(
                    "{}.json",
                    path.extension().unwrap_or_default().to_string_lossy()
                ));
                if format == "raw" {
                    let warning = write_with_sidecar(
                        &path,
                        &unit.code,
                        &sidecar,
                        &serde_json::to_vec_pretty(
                            &json!({"origin":self.movie.info.sha256,"unit":unit}),
                        )?,
                    )?;
                    return Ok(
                        json!({"path":path,"sidecar":if warning.is_none(){Some(sidecar)}else{None},"warning":warning}),
                    );
                }
                let result = swf_decompile::decompile(unit);
                let text = if format == "as" {
                    format!(
                        "// Fragmento de lectura; no es un archivo de clase compilable.\n// Unidad: {}\n{}",
                        unit.id,
                        result
                            .source
                            .as_deref()
                            .context("Fuente no reconstruida: use P-code")?
                    )
                } else {
                    result.pcode.clone()
                };
                let sidecar = path.with_extension(format!(
                    "{}.json",
                    path.extension().unwrap_or_default().to_string_lossy()
                ));
                let warning = write_with_sidecar(
                    &path,
                    text.as_bytes(),
                    &sidecar,
                    &serde_json::to_vec_pretty(
                        &json!({"origin":self.movie.info.sha256,"unit":unit,"reconstruction":result}),
                    )?,
                )?;
                Ok(
                    json!({"path":path,"sidecar":if warning.is_none(){Some(sidecar)}else{None},"warning":warning}),
                )
            }
            _ => Err(unsupported(op)),
        }
    }
}

/// An integer argument within bounds; a missing one is the default, a wrong one a bad request.
fn integer_argument(args: &Value, name: &str, default: u64, min: u64, max: u64) -> Result<u64> {
    match args.get(name) {
        None | Some(Value::Null) => Ok(default),
        Some(value) => value
            .as_u64()
            .filter(|n| (min..=max).contains(n))
            .ok_or_else(|| bad_request(format!("Argumento inválido: {name}"))),
    }
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut text = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        text.push(DIGITS[usize::from(byte >> 4)] as char);
        text.push(DIGITS[usize::from(byte & 15)] as char);
    }
    text
}

fn safe_name(id: &str) -> String {
    id.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// One JSONL session: `open` replaces the document; EOF on stdin ends it.
fn worker() -> Result<()> {
    let stdin = io::stdin();
    let mut output = io::stdout().lock();
    let mut session: Option<Session> = None;
    loop {
        let mut line = String::new();
        let count = stdin.lock().take(1_048_577).read_line(&mut line)?;
        if count == 0 {
            break;
        }
        if count > 1_048_576 {
            let reply = proto::reply_error(
                proto::Framing::V2,
                &Value::Null,
                kind::LIMIT,
                "Solicitud IPC demasiado grande",
                0,
            );
            writeln!(output, "{reply}")?;
            output.flush()?;
            bail!("Solicitud IPC demasiado grande");
        }
        if line.trim().is_empty() {
            continue;
        }
        let request = match proto::parse(&line) {
            Ok(request) => request,
            Err((framing, id, message)) => {
                let reply = proto::reply_error(framing, &id, kind::BAD_REQUEST, &message, 0);
                writeln!(output, "{reply}")?;
                output.flush()?;
                continue;
            }
        };
        let start = Instant::now();
        let result: Result<Value> = (|| match request.op.as_str() {
            "capabilities" => Ok(capabilities()),
            "open" => {
                let path = request.args["path"]
                    .as_str()
                    .ok_or_else(|| bad_request("Falta ruta"))?;
                let path = Path::new(path);
                if !path.is_file() {
                    return Err(not_found(format!("No existe el archivo {}", path.display())));
                }
                let opened = Session::open(path)?;
                let info = opened.dispatch("info", &json!({}))?;
                session = Some(opened);
                Ok(info)
            }
            op => session
                .as_ref()
                .ok_or_else(not_open)?
                .dispatch(op, &request.args),
        })();
        let elapsed = start.elapsed().as_millis();
        let mut reply = match result {
            Ok(value) => proto::reply_ok(&request, value, elapsed),
            Err(error) => proto::reply_error(
                request.framing,
                &request.id,
                error_kind(&error),
                &format!("{error:#}"),
                elapsed,
            ),
        };
        let mut encoded = serde_json::to_string(&reply)?;
        if encoded.len() > 8 * 1024 * 1024 {
            reply = proto::reply_error(
                request.framing,
                &request.id,
                kind::LIMIT,
                "Respuesta demasiado grande; use paginación",
                elapsed,
            );
            encoded = serde_json::to_string(&reply)?;
        }
        writeln!(output, "{encoded}")?;
        output.flush()?;
    }
    Ok(())
}

fn audit_case(path: &Path) -> Result<Value> {
    let start = Instant::now();
    let session = Session::open(path)?;
    let mut structured = 0;
    let mut fallback = 0;
    let mut diagnostics = Vec::new();
    for unit in &session.units {
        let result = swf_decompile::decompile(unit);
        if result.source.is_some() {
            structured += 1;
        } else {
            fallback += 1;
        }
        if !result.diagnostics.is_empty() {
            diagnostics.push(json!({"id":unit.id,"diagnostics":result.diagnostics}));
        }
    }
    Ok(
        json!({"info":session.movie.info,"units":session.units.len(),"units_metadata":session.units,"structured":structured,"pcode_only":fallback,"diagnostics":diagnostics,"container_diagnostics":session.movie.diagnostics,"elapsed_ms":start.elapsed().as_millis()}),
    )
}

fn capabilities() -> Value {
    json!({"schema_version":1,"engine":"rust","display_name":"RustSWF","version":env!("CARGO_PKG_VERSION"),
        "protocol":{"name":"flash-engine-jsonl","version":2,"framings":["v2","legacy"]},
        "ops":CANONICAL_OPS,"native_ops":NATIVE_OPS,"languages":["as2","as3"],
        "operations":["inspect","disassemble","decompile","decompile_document","method_context","extract","abc_metadata","roundtrip","audit"],
        "containers":{"read":["FWS","CWS","ZWS"],"write":["original","FWS","CWS","ZWS"],"write_scope":"lossless container wrapper; no ActionScript compiler"},
        "reconstruction":{"public_namespaces":"URI identity for Namespace/PackageNamespace; generated AS3 namespace selectors",
            "runtime_names":"primitive literal keys and known public namespace constants only",
            "control_flow":"if/else, reducible header loops with proven break/continue/return, rotated loops, integer switches with empty-stack fallthrough",
            "lexical_lookup":"exact class/script trait ownership; unknown receiver effects retain P-code",
            "coercion":"verified builtin/ABC classes via typed assignment; coerce is never replaced by as",
            "activations":"explicit mutable slots and proven nested captures; unknown scope/receiver escape retains P-code",
            "private_internal_protected":"retained exact in owned class documents; standalone unsupported bindings keep P-code",
            "source_forms":["method_fragment","AS3 qualified method plus namespace declarations","complete bounded class documents with canonical bootstrap and trivial static initialization"],
            "exception_handlers":"one verified disjoint typed catch region; nested/finally/ambiguous completions retain P-code"},
        "preservation":{"original":true,"unknown_tags":true,"nested_tags":true,"abc_metadata_raw_encoding":true,"roundtrip_normalized_byte_identity":true},
        "limits":{"input_bytes":swf_core::MAX_INPUT_BYTES,"normalized_bytes":swf_core::MAX_NORMALIZED_BYTES,"instructions":swf_decompile::MAX_INSTRUCTIONS,"blocks":swf_decompile::MAX_BLOCKS}})
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let command = args.get(1).map(String::as_str).unwrap_or("help");
    if command == "worker" {
        return worker();
    }
    if command == "supervise" {
        match supervise::run(&args[2..]) {
            Ok(code) => std::process::exit(code),
            Err(error) => {
                eprintln!("rustswf supervise: {error:#}");
                std::process::exit(127);
            }
        }
    }
    if command == "hierarchy_contract" {
        let hierarchy = swf_decompile::VerifiedHierarchy::load_swc(
            Path::new(args.get(2).context("Falta ruta SWC")?),
            args.get(3).context("Falta SHA-256 SWC")?,
        )?;
        println!("{}", serde_json::to_string_pretty(&hierarchy.provenance)?);
        return Ok(());
    }
    if command == "capabilities" || command == "--capabilities" {
        println!("{}", serde_json::to_string_pretty(&capabilities())?);
        return Ok(());
    }
    if command == "help" || command == "--help" {
        println!(
            "RustSWF {}\n rustswf capabilities\n rustswf inspect <file.swf>\n rustswf disassemble|decompile|abc_metadata <file.swf> <unit-id>\n rustswf roundtrip <file.swf> <new-output.swf> [original|fws|cws|zws]\n rustswf extract <file.swf> <resource-id> <format> <destination>\n rustswf audit <corpus-manifest.json> <report.json>\n rustswf header|tag_tree <file.swf>
 rustswf worker (JSONL, protocol v2; see engines/PROTOCOL.md)
 rustswf supervise [--memory-mib N] -- <program> [args...]",
            env!("CARGO_PKG_VERSION")
        );
        return Ok(());
    }
    if command == "audit" {
        let manifest: Value =
            serde_json::from_slice(&fs::read(args.get(2).context("Falta manifiesto")?)?)?;
        let output_path = PathBuf::from(args.get(3).context("Falta destino informe")?);
        let mut cases = Vec::new();
        for case in manifest["cases"]
            .as_array()
            .context("Manifiesto sin cases")?
        {
            let file = case["file"].as_str().context("Entrada sin file")?;
            use sha2::{Digest, Sha256};
            let actual_sha256 = (|| -> Result<String> {
                let mut input = fs::File::open(file)?;
                let mut hash = Sha256::new();
                let mut block = [0_u8; 65536];
                loop {
                    let n = input.read(&mut block)?;
                    if n == 0 {
                        break;
                    }
                    hash.update(&block[..n]);
                }
                Ok(format!("{:x}", hash.finalize()))
            })()
            .ok();
            let mut command = std::process::Command::new(std::env::current_exe()?);
            command
                .args(["audit-case", file])
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped());
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x08000000);
            }
            let mut child = command.spawn()?;
            let memory_job = match job::Job::attach(&child) {
                Ok(job) => job,
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    cases.push(json!({"relative":case["relative"],"actual_sha256":actual_sha256,"expected_sha256":case["sha256"],"result":{"status":"failed","error":error.to_string()}}));
                    continue;
                }
            };
            let stdout = child.stdout.take().unwrap();
            let reader = std::thread::spawn(move || {
                let mut bytes = Vec::new();
                let _ = stdout.take(32 * 1024 * 1024).read_to_end(&mut bytes);
                bytes
            });
            let start = Instant::now();
            let mut timed_out = false;
            loop {
                if child.try_wait()?.is_some() {
                    break;
                }
                if start.elapsed().as_secs() >= 30 {
                    let _ = child.kill();
                    let _ = child.wait();
                    timed_out = true;
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            let bytes = reader.join().unwrap_or_default();
            let mut result: Value = if timed_out {
                json!({"status":"timeout"})
            } else {
                serde_json::from_slice(&bytes).unwrap_or_else(
                    |_| json!({"status":"failed","error":"Proceso no devolvió JSON válido"}),
                )
            };
            if let Some(actual) = result["info"]["sha256"].as_str()
                && Some(actual) != case["sha256"].as_str()
            {
                result["status"] = json!("hash_mismatch");
            }
            eprintln!(
                "{}: {}",
                case["relative"].as_str().unwrap_or(file),
                result["status"].as_str().unwrap_or("ok")
            );
            drop(memory_job);
            cases.push(json!({"relative":case["relative"],"split":case["split"],"actual_sha256":actual_sha256,"expected_sha256":case["sha256"],"result":result}));
        }
        let report = json!({"schema_version":1,"engine":format!("RustSWF {}",env!("CARGO_PKG_VERSION")),"scope":"read, inventory, partial method reconstruction; no Flash runtime equivalence","measurement_context":{"suitable_for_speed_comparison":false,"runs_per_file":1,"worker_memory_limit_bytes":1073741824,"timeout_seconds":30},"cases":cases});
        atomic_write(&output_path, &serde_json::to_vec_pretty(&report)?)?;
        return Ok(());
    }
    let path = Path::new(args.get(2).context("Falta archivo SWF")?);
    if command == "audit-case" {
        let result = match audit_case(path) {
            Ok(v) => v,
            Err(e) => json!({"status":"failed","error":format!("{e:#}")}),
        };
        println!("{}", serde_json::to_string(&result)?);
        return Ok(());
    }
    let session = Session::open(path)?;
    let result=match command {
        "header"|"tag_tree"=>session.dispatch(command,&json!({}))?,
        "inspect"=>json!({"document":session.dispatch("info",&json!({}))?,"resources":session.dispatch("resources",&json!({"limit":500}))?,"scripts":session.dispatch("scripts",&json!({"limit":500}))?}),
        "disassemble"|"decompile"|"abc_metadata"|"method_context"|"decompile_document"=>{
            let mut request=json!({"id":args.get(3).context("Falta unit-id")?});
            if let Some(p)=args.iter().position(|arg|arg=="--hierarchy-swc"){request["hierarchy_swc"]=json!(args.get(p+1).context("Falta ruta SWC")?);}
            if let Some(p)=args.iter().position(|arg|arg=="--hierarchy-sha256"){request["hierarchy_sha256"]=json!(args.get(p+1).context("Falta SHA SWC")?);}
            session.dispatch(command,&request)?
        },
        "roundtrip"=>session.dispatch(command,&json!({"path":args.get(3).context("Falta destino nuevo")?,"compression":args.get(4).map(String::as_str).unwrap_or("original")}))?,
        "extract"=>session.dispatch("export_resource",&json!({"id":args.get(3).context("Falta resource-id")?,"format":args.get(4).context("Falta formato")?,"path":args.get(5).context("Falta destino")?}))?,
        _=>bail!("Comando desconocido")
    };
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}
