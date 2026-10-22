//! flashdec-rs: hybrid SWF decompiler (RustSWF + JPEXS, best file wins).
mod jpexs;
mod merge;
mod native;
mod scoring;

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use swf_core::{Movie, export_resource, supported_formats};

const VERSION: &str = env!("CARGO_PKG_VERSION");
const USAGE: &str = "flashdec-rs: descompilador SWF híbrido (RustSWF + JPEXS, gana el mejor archivo)

Uso: flashdec-rs <entrada.swf|.swc|carpeta> [opciones]

  -o, --output DIR      carpeta de salida (por defecto <entrada>_flashdec)
  -e, --engine MODO     best (defecto) | native | jpexs
      --prefer MOTOR    jpexs (defecto) | native: gana en empate
      --no-resources    solo scripts
      --no-pcode        no escribir listados P-code
      --keep-engines    conservar también la salida cruda de cada motor
      --deobfuscate     activar la desofuscación automática de JPEXS
      --timeout SEG     límite de JPEXS por SWF (defecto 900)
      --java RUTA       java para JPEXS
      --jpexs-jar RUTA  ffdec-cli.jar / ffdec.jar
      --java-memory M   heap de JPEXS (defecto 2g)
  -j, --jobs N          SWF en paralelo al procesar carpetas
      --json            imprimir el resumen en JSON
  -q, --quiet           sin mensajes de progreso
  -V, --version";

#[derive(Clone)]
struct Args {
    input: PathBuf,
    output: Option<PathBuf>,
    engine: String,
    prefer: &'static str,
    resources: bool,
    pcode: bool,
    keep_engines: bool,
    deobfuscate: bool,
    timeout: u64,
    java: Option<PathBuf>,
    jar: Option<PathBuf>,
    memory: String,
    jobs: usize,
    json: bool,
    quiet: bool,
}

fn parse_args() -> Result<Args> {
    let mut args = Args {
        input: PathBuf::new(),
        output: None,
        engine: "best".into(),
        prefer: "jpexs",
        resources: true,
        pcode: true,
        keep_engines: false,
        deobfuscate: false,
        timeout: 900,
        java: None,
        jar: None,
        memory: "2g".into(),
        jobs: 1,
        json: false,
        quiet: false,
    };
    let mut input = None;
    let mut it = std::env::args().skip(1);
    while let Some(arg) = it.next() {
        let mut value = |name: &str| it.next().with_context(|| format!("{name} necesita un valor"));
        match arg.as_str() {
            "-h" | "--help" => {
                println!("{USAGE}");
                std::process::exit(0);
            }
            "-V" | "--version" => {
                println!("flashdec-rs {VERSION}");
                std::process::exit(0);
            }
            "-o" | "--output" => args.output = Some(value(&arg)?.into()),
            "-e" | "--engine" => {
                args.engine = value(&arg)?;
                if !["best", "native", "jpexs"].contains(&args.engine.as_str()) {
                    bail!("--engine debe ser best, native o jpexs");
                }
            }
            "--prefer" => {
                args.prefer = match value(&arg)?.as_str() {
                    "jpexs" => "jpexs",
                    "native" => "native",
                    _ => bail!("--prefer debe ser jpexs o native"),
                }
            }
            "--no-resources" => args.resources = false,
            "--no-pcode" => args.pcode = false,
            "--keep-engines" => args.keep_engines = true,
            "--deobfuscate" => args.deobfuscate = true,
            "--timeout" => args.timeout = value(&arg)?.parse().context("--timeout")?,
            "--java" => args.java = Some(value(&arg)?.into()),
            "--jpexs-jar" => args.jar = Some(value(&arg)?.into()),
            "--java-memory" => args.memory = value(&arg)?,
            "-j" | "--jobs" => args.jobs = value(&arg)?.parse::<usize>().context("--jobs")?.max(1),
            "--json" => args.json = true,
            "-q" | "--quiet" => args.quiet = true,
            other if other.starts_with('-') => bail!("opción desconocida: {other}\n\n{USAGE}"),
            other => {
                if input.replace(PathBuf::from(other)).is_some() {
                    bail!("solo se admite una entrada");
                }
            }
        }
    }
    args.input = input.with_context(|| USAGE.to_owned())?;
    Ok(args)
}

fn write(path: &Path, text: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, text.replace("\r\n", "\n")).with_context(|| format!("escribir {}", path.display()))
}

/// Damaged containers return the bytes anyway: JPEXS tolerates more damage.
fn load(path: &Path) -> Result<(Vec<u8>, Result<Movie, String>)> {
    let data = fs::read(path).with_context(|| format!("leer {}", path.display()))?;
    let is_swc = path.extension().is_some_and(|e| e.eq_ignore_ascii_case("swc")) || data.starts_with(b"PK");
    if is_swc {
        let sha = format!("{:x}", Sha256::digest(&data));
        let library = swf_core::read_verified_swc_library(path, &sha)?;
        return Ok((library.movie.original.clone(), Ok(library.movie)));
    }
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let movie = Movie::from_bytes(&data, &name).map_err(|e| format!("{e:#}"));
    Ok((data, movie))
}

fn native_resources(movie: &Movie, dir: &Path) -> Value {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut errors = vec![];
    let mut symbols = vec![];
    for tag in &movie.tags {
        if tag.code == 76 {
            let data = movie.tag_data(tag);
            let mut pos = 2;
            let count = data.get(0..2).map_or(0, |b| u16::from_le_bytes([b[0], b[1]]));
            for _ in 0..count {
                let Some(id) = data.get(pos..pos + 2).map(|b| u16::from_le_bytes([b[0], b[1]])) else { break };
                let Some(end) = data[pos + 2..].iter().position(|b| *b == 0) else { break };
                symbols.push(format!("{id};{}", String::from_utf8_lossy(&data[pos + 2..pos + 2 + end])));
                pos += 3 + end;
            }
            continue;
        }
        let formats = supported_formats(movie, tag);
        let format = if formats.iter().any(|f| f == "jpeg") {
            "jpeg"
        } else if let Some(f) = formats.iter().find(|f| *f != "raw") {
            f.as_str()
        } else {
            continue;
        };
        match export_resource(movie, tag, format) {
            Ok(asset) => {
                let name = tag.character_id.map_or_else(|| tag.id.replace([':', '/'], "_"), |id| id.to_string());
                let path = dir.join(&tag.category).join(format!("{name}.{}", asset.extension));
                if let Some(parent) = path.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                match fs::write(&path, &asset.bytes) {
                    Ok(()) => *counts.entry(tag.category.clone()).or_default() += 1,
                    Err(error) => errors.push(json!({"tag": tag.id, "error": error.to_string()})),
                }
            }
            Err(error) => errors.push(json!({"tag": tag.id, "type": tag.name, "error": format!("{error:#}")})),
        }
    }
    if !symbols.is_empty() {
        let _ = write(&dir.join("symbolClass/symbols.csv"), &(symbols.join("\n") + "\n"));
        counts.insert("symbolClass".into(), 1);
    }
    json!({"engine": "native", "counts": counts, "errors": errors})
}

fn copy_tree(from: &Path, to: &Path) -> usize {
    let mut copied = 0;
    let Ok(entries) = fs::read_dir(from) else { return 0 };
    for entry in entries.flatten() {
        let path = entry.path();
        let target = to.join(entry.file_name());
        if path.is_dir() {
            copied += copy_tree(&path, &target);
        } else if fs::create_dir_all(to).is_ok() && fs::copy(&path, &target).is_ok() {
            copied += 1;
        }
    }
    copied
}

static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

fn decompile_file(source: &Path, out_dir: &Path, args: &Args, tool: Option<&jpexs::Tool>, log: &dyn Fn(String)) -> Result<Value> {
    let started = Instant::now();
    let (data, movie) = load(source)?;
    let mut report = json!({"input": source, "sha256": format!("{:x}", Sha256::digest(&data)), "bytes": data.len(),
        "flashdec": format!("flashdec-rs {VERSION}"), "engine_mode": args.engine, "engines": {}});
    let _ = fs::remove_dir_all(out_dir);
    fs::create_dir_all(out_dir)?;
    let temp = std::env::temp_dir().join(format!(
        "flashdec-{}-{}",
        std::process::id(),
        TEMP_COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&temp)?;
    let swf_copy = temp.join("input.swf"); // engines work on a copy, never on the original
    fs::write(&swf_copy, &data)?;

    if args.engine == "jpexs" && tool.is_none() {
        bail!("JPEXS no encontrado: use --java/--jpexs-jar o FLASHDEC_JAVA/FLASHDEC_JPEXS_JAR");
    }
    let jpexs_handle = (args.engine != "native").then_some(tool).flatten().map(|tool| {
        let (tool, swf, dir) = (tool.clone(), swf_copy.clone(), temp.join("jpexs"));
        let options = jpexs::Options {
            resources: args.resources,
            timeout: args.timeout,
            deobfuscate: args.deobfuscate,
            memory: args.memory.clone(),
        };
        std::thread::spawn(move || jpexs::run(&tool, &swf, &dir, &options))
    });

    let native_result = if args.engine != "jpexs" {
        if let Ok(movie) = &movie {
            let result = native::decompile(movie);
            report["header"] = result.header.clone();
            report["engines"]["native"] = json!({"seconds": (result.seconds * 1000.0).round() / 1000.0,
                "scripts": result.scripts.len(), "errors": result.errors});
            Some(result)
        } else {
            let error = movie.as_ref().err().cloned().unwrap_or_default();
            log(format!("  native: {error}"));
            report["engines"]["native"] = json!({"error": error});
            None
        }
    } else {
        None
    };
    let jpexs_result = jpexs_handle.map(|h| h.join().unwrap_or_default());
    if let (Some(result), Some(tool)) = (&jpexs_result, tool) {
        report["engines"]["jpexs"] = json!({"tool": tool.describe(), "seconds": (result.seconds * 1000.0).round() / 1000.0,
            "scripts": result.scripts.len(), "error": result.error});
        if let Some(error) = &result.error {
            report["engines"]["jpexs"]["log_tail"] = json!(result.log_tail);
            log(format!("  jpexs: {error}"));
        }
    } else if args.engine == "best" {
        report["engines"]["jpexs"] = json!({"error": "not available"});
    }

    let prefer: Vec<&'static str> = if args.prefer == "native" { vec!["native", "jpexs"] } else { vec!["jpexs", "native"] };
    let empty = BTreeMap::new();
    let native_scripts = native_result.as_ref().map_or(&[][..], |r| &r.scripts[..]);
    let jpexs_scripts = jpexs_result.as_ref().map_or(&empty, |r| &r.scripts);
    let choices = merge::merge(native_scripts, jpexs_scripts, &prefer);
    // When JPEXS covered the movie, files only RustSWF produced are usually
    // file-internal classes JPEXS already writes inside their host class.
    let jpexs_ok = jpexs_result.as_ref().is_some_and(|r| r.error.is_none() && !r.scripts.is_empty());
    for choice in &choices {
        let folder = if choice.only_native && jpexs_ok { "extra/native_only" } else { "scripts" };
        write(&out_dir.join(folder).join(&choice.path), &choice.text)?;
    }
    if args.pcode {
        for script in native_scripts.iter().filter(|s| !s.pcode.is_empty()) {
            let path = format!("{}.pcode", script.path.trim_end_matches(".as"));
            write(&out_dir.join("pcode").join(path), &script.pcode)?;
        }
    }
    if args.keep_engines {
        for script in native_scripts {
            write(&out_dir.join("engines/native").join(&script.path), &script.source)?;
        }
        for (path, text) in jpexs_scripts {
            write(&out_dir.join("engines/jpexs").join(path), text)?;
        }
    }
    if args.resources {
        match &jpexs_result {
            Some(result) if result.error.is_none() && args.engine != "native" => {
                let mut counts = serde_json::Map::new();
                if let Ok(entries) = fs::read_dir(&result.out_dir) {
                    for entry in entries.flatten().filter(|e| e.path().is_dir() && e.file_name() != "scripts") {
                        let copied = copy_tree(&entry.path(), &out_dir.join("resources").join(entry.file_name()));
                        if copied > 0 {
                            counts.insert(entry.file_name().to_string_lossy().into(), json!(copied));
                        }
                    }
                }
                report["resources"] = json!({"engine": "jpexs", "counts": counts});
            }
            _ if args.engine != "jpexs" => {
                if let Ok(movie) = &movie {
                    report["resources"] = native_resources(movie, &out_dir.join("resources"));
                }
            }
            _ => {}
        }
    }
    let _ = fs::remove_dir_all(&temp);

    let main: Vec<&merge::Choice> = choices.iter().filter(|c| !(c.only_native && jpexs_ok)).collect();
    let mut by_engine: BTreeMap<&str, usize> = BTreeMap::new();
    for choice in &main {
        *by_engine.entry(choice.engine).or_default() += 1;
    }
    let clean = main.iter().filter(|c| c.chosen_value().is_some_and(|v| v >= 100)).count();
    report["summary"] = json!({"scripts": main.len(), "chosen_by_engine": by_engine, "clean_scripts": clean,
        "extra_native_only": choices.len() - main.len(),
        "seconds": (started.elapsed().as_secs_f64() * 1000.0).round() / 1000.0});
    report["scripts"] = choices.iter().map(merge::Choice::to_json).collect();
    fs::write(out_dir.join("report.json"), serde_json::to_vec_pretty(&report)?)?;
    write(&out_dir.join("REPORT.md"), &markdown(&report))?;
    Ok(report)
}

fn markdown(report: &Value) -> String {
    let s = &report["summary"];
    let name = Path::new(report["input"].as_str().unwrap_or("")).file_name().unwrap_or_default().to_string_lossy().into_owned();
    let engines: Vec<String> = s["chosen_by_engine"]
        .as_object()
        .map(|m| m.iter().map(|(k, v)| format!("{k}={v}")).collect())
        .unwrap_or_default();
    let mut lines = vec![
        format!("# flashdec: {name}"),
        String::new(),
        format!("- SHA-256: `{}`", report["sha256"].as_str().unwrap_or("")),
        format!("- Scripts: {} ({} without error markers)", s["scripts"], s["clean_scripts"]),
        format!("- Chosen by engine: {}", if engines.is_empty() { "none".into() } else { engines.join(", ") }),
        format!("- Time: {} s", s["seconds"]),
        String::new(),
        "| Script | Engine | native | jpexs |".into(),
        "|---|---|---|---|".into(),
    ];
    let extra = s["extra_native_only"].as_u64().unwrap_or(0) > 0;
    for item in report["scripts"].as_array().into_iter().flatten() {
        let cell = |engine: &str| match &item["scores"][engine] {
            Value::Object(o) => o["value"].to_string(),
            _ => "—".into(),
        };
        let mut engine = item["engine"].as_str().unwrap_or("").to_owned();
        if extra && item["only_native"].as_bool() == Some(true) {
            engine.push_str(" (extra)");
        }
        lines.push(format!("| `{}` | {engine} | {} | {} |", item["path"].as_str().unwrap_or(""), cell("native"), cell("jpexs")));
    }
    lines.join("\n") + "\n"
}

fn collect_inputs(path: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(path) else { return };
    let mut entries: Vec<_> = entries.flatten().map(|e| e.path()).collect();
    entries.sort();
    for entry in entries {
        if entry.is_dir() {
            collect_inputs(&entry, out);
        } else if entry
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|e| ["swf", "swc", "spl"].contains(&e.to_ascii_lowercase().as_str()))
        {
            out.push(entry);
        }
    }
}

fn main() {
    let args = match parse_args() {
        Ok(args) => args,
        Err(error) => {
            eprintln!("{error:#}");
            std::process::exit(2);
        }
    };
    let quiet = args.quiet;
    let log = move |message: String| {
        if !quiet {
            eprintln!("{message}");
        }
    };
    if !args.input.exists() {
        eprintln!("no existe: {}", args.input.display());
        std::process::exit(2);
    }
    let is_dir = args.input.is_dir();
    let mut files = vec![];
    if is_dir {
        collect_inputs(&args.input, &mut files);
    } else {
        files.push(args.input.clone());
    }
    if files.is_empty() {
        eprintln!("no se encontraron SWF");
        std::process::exit(2);
    }
    let tool = if args.engine == "native" { None } else { jpexs::locate(args.java.clone(), args.jar.clone()) };
    match &tool {
        Some(tool) => log(format!("JPEXS: {}", tool.describe())),
        None if args.engine == "best" => log("JPEXS no encontrado: solo motor nativo RustSWF".into()),
        None => {}
    }
    let output = args.output.clone().unwrap_or_else(|| {
        let stem = args.input.file_stem().unwrap_or_default().to_string_lossy();
        args.input.with_file_name(format!("{stem}_flashdec"))
    });
    let queue = Arc::new(Mutex::new(files.into_iter().enumerate().collect::<Vec<_>>()));
    let results = Arc::new(Mutex::new(Vec::new()));
    let workers: Vec<_> = (0..args.jobs)
        .map(|_| {
            let (queue, results, args, tool, output) =
                (queue.clone(), results.clone(), args.clone(), tool.clone(), output.clone());
            std::thread::spawn(move || {
                loop {
                    let Some((index, path)) = queue.lock().unwrap().pop() else { break };
                    let target = if is_dir {
                        output.join(path.strip_prefix(&args.input).unwrap_or(&path).with_extension(""))
                    } else {
                        output.clone()
                    };
                    log(format!("> {}", path.display()));
                    let summary = match decompile_file(&path, &target, &args, tool.as_ref(), &log) {
                        Ok(report) => {
                            let s = &report["summary"];
                            log(format!(
                                "  {} scripts ({} limpios) {} en {} s -> {}",
                                s["scripts"], s["clean_scripts"], s["chosen_by_engine"], s["seconds"], target.display()
                            ));
                            let mut summary = json!({"input": path, "output": target});
                            for (k, v) in s.as_object().unwrap() {
                                summary[k] = v.clone();
                            }
                            summary
                        }
                        Err(error) => {
                            log(format!("  ERROR: {error:#}"));
                            json!({"input": path, "error": format!("{error:#}")})
                        }
                    };
                    results.lock().unwrap().push((index, summary));
                }
            })
        })
        .collect();
    for worker in workers {
        let _ = worker.join();
    }
    let mut results = Arc::try_unwrap(results).map(|m| m.into_inner().unwrap()).unwrap_or_default();
    results.sort_by_key(|(index, _)| *index);
    let failed = results.iter().any(|(_, r)| r.get("error").is_some());
    if args.json {
        let list: Vec<_> = results.into_iter().map(|(_, r)| r).collect();
        println!("{}", serde_json::to_string_pretty(&list).unwrap_or_default());
    }
    std::process::exit(i32::from(failed));
}
