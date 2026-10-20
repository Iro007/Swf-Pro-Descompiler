use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::{
    fs,
    io::{self, BufRead, Read, Write},
    path::{Path, PathBuf},
    time::Instant,
};
use swf_avm::{ScriptUnit, class_outline, disassemble, inventory};
use swf_core::{
    ContainerCompression, Movie, export_resource, resource_metadata, supported_formats,
};
mod atomic;
mod job;
use atomic::{atomic_write, write_with_sidecar};

struct Session {
    movie: Movie,
    units: Vec<ScriptUnit>,
}
impl Session {
    fn open(path: &Path) -> Result<Self> {
        let movie = Movie::read(path)?;
        let units = inventory(&movie);
        Ok(Self { movie, units })
    }
    fn tag(&self, id: &str) -> Result<&swf_core::TagRecord> {
        self.movie
            .tags
            .iter()
            .find(|t| t.id == id)
            .context("Recurso desconocido")
    }
    fn unit(&self, id: &str) -> Result<&ScriptUnit> {
        self.units
            .iter()
            .find(|u| u.id == id)
            .context("Unidad de script desconocida")
    }
    fn dispatch(&self, op: &str, args: &Value) -> Result<Value> {
        let id = args["id"].as_str().unwrap_or("");
        let offset = args["offset"].as_u64().unwrap_or(0) as usize;
        let limit = (args["limit"].as_u64().unwrap_or(200) as usize).min(500);
        let query = args["query"].as_str().unwrap_or("").to_lowercase();
        match op {
            "capabilities" => Ok(capabilities()),
            "info" => Ok(
                json!({"info":self.movie.info,"diagnostics":self.movie.diagnostics,"script_count":self.units.len()}),
            ),
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
            "metadata" => Ok(resource_metadata(&self.movie, self.tag(id)?)),
            "abc_metadata" => {
                let unit = self.unit(id)?;
                let abc = unit
                    .context
                    .as_ref()
                    .context("La unidad no tiene un ABC válido")?;
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
                let path = PathBuf::from(args["path"].as_str().context("Falta destino")?);
                atomic_write(&path, &output)?;
                use sha2::{Digest, Sha256};
                Ok(
                    json!({"path":path,"signature":reloaded.info.signature,"version":reloaded.info.version,"bytes":output.len(),
                    "input_sha256":self.movie.info.sha256,"output_sha256":format!("{:x}",Sha256::digest(&output)),
                    "normalized_sha256":format!("{:x}",Sha256::digest(&self.movie.normalized)),"normalized_bytes_identical":reloaded.normalized==self.movie.normalized,
                    "writer_scope":"SWF container only; tags, bytecode and metadata retained as original raw bytes"}),
                )
            }
            "disassemble" => {
                let listing = disassemble(self.unit(id)?);
                let page_text = listing
                    .pcode
                    .chars()
                    .skip(offset)
                    .take(65536)
                    .collect::<String>();
                Ok(
                    json!({"state":listing.state,"diagnostics":listing.diagnostics,"instructions":listing.instructions.iter().skip(args["instruction_offset"].as_u64().unwrap_or(0) as usize).take(limit).collect::<Vec<_>>(),"instruction_total":listing.instructions.len(),"pcode":page_text,"pcode_total":listing.pcode.chars().count(),"offset":offset}),
                )
            }
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
                Ok(value)
            }
            "method_context" => {
                let context = swf_avm::symbols::MethodContext::from_unit(self.unit(id)?)
                    .context("La unidad no tiene contexto ABC verificable")?;
                Ok(serde_json::to_value(context)?)
            }
            "decompile_document" | "source_document" => {
                let hierarchy = if let Some(path) = args["hierarchy_swc"].as_str() {
                    Some(swf_decompile::VerifiedHierarchy::load_swc(
                        Path::new(path),
                        args["hierarchy_sha256"]
                            .as_str()
                            .context("Falta SHA-256 del contrato SWC")?,
                    )?)
                } else {
                    None
                };
                let mut result = if let Some(hierarchy) = &hierarchy {
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
            "raw" => {
                let data = self.movie.tag_data(self.tag(id)?);
                let length = args["length"].as_u64().unwrap_or(4096).min(65536) as usize;
                let end = offset.saturating_add(length).min(data.len());
                let bytes = if offset < data.len() {
                    &data[offset..end]
                } else {
                    &[]
                };
                let mut text = String::new();
                for (i, chunk) in bytes.chunks(16).enumerate() {
                    text.push_str(&format!("{:08x}  ", offset + i * 16));
                    for n in 0..16 {
                        if let Some(byte) = chunk.get(n) {
                            text.push_str(&format!("{byte:02x} "));
                        } else {
                            text.push_str("   ");
                        }
                    }
                    text.push(' ');
                    for &byte in chunk {
                        text.push(if (32..127).contains(&byte) {
                            byte as char
                        } else {
                            '.'
                        });
                    }
                    text.push('\n');
                }
                Ok(json!({"total":data.len(),"offset":offset,"text":text}))
            }
            "asset" => {
                let format = args["format"].as_str().unwrap_or("raw");
                let dir = PathBuf::from(args["dir"].as_str().context("Falta directorio")?);
                fs::create_dir_all(&dir)?;
                let tag = self.tag(id)?;
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
                let path = PathBuf::from(args["path"].as_str().context("Falta destino")?);
                let asset = export_resource(&self.movie, self.tag(id)?, format)?;
                atomic_write(&path, &asset.bytes)?;
                Ok(json!({"path":path,"bytes":asset.bytes.len(),"diagnostics":asset.diagnostics}))
            }
            "export_script" => {
                let path = PathBuf::from(args["path"].as_str().context("Falta destino")?);
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
            _ => bail!("Operación no soportada: {op}"),
        }
    }
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
            bail!("Solicitud IPC demasiado grande");
        }
        let request: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                writeln!(output, "{}", json!({"error":e.to_string()}))?;
                output.flush()?;
                continue;
            }
        };
        let start = Instant::now();
        let result: Result<Value> = (|| {
            let op = request["operation"].as_str().context("Falta operación")?;
            if op == "capabilities" {
                Ok(capabilities())
            } else if op == "open" {
                let path = Path::new(request["args"]["path"].as_str().context("Falta ruta")?);
                let new_session = Session::open(path)?;
                let info = new_session.dispatch("info", &json!({}))?;
                session = Some(new_session);
                Ok(info)
            } else {
                session
                    .as_ref()
                    .context("No hay SWF abierto")?
                    .dispatch(op, &request["args"])
            }
        })();
        let reply = match result {
            Ok(value) => {
                json!({"request_id":request["request_id"],"result":value,"elapsed_ms":start.elapsed().as_millis()})
            }
            Err(error) => {
                json!({"request_id":request["request_id"],"error":format!("{error:#}"),"elapsed_ms":start.elapsed().as_millis()})
            }
        };
        let encoded = serde_json::to_string(&reply)?;
        if encoded.len() > 8 * 1024 * 1024 {
            writeln!(
                output,
                "{}",
                json!({"request_id":request["request_id"],"error":"Respuesta demasiado grande; use paginación"})
            )?;
        } else {
            writeln!(output, "{encoded}")?;
        }
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
            "RustSWF {}\n rustswf capabilities\n rustswf inspect <file.swf>\n rustswf disassemble|decompile|abc_metadata <file.swf> <unit-id>\n rustswf roundtrip <file.swf> <new-output.swf> [original|fws|cws|zws]\n rustswf extract <file.swf> <resource-id> <format> <destination>\n rustswf audit <corpus-manifest.json> <report.json>\n rustswf worker (JSONL)",
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
