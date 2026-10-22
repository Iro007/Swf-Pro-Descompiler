//! JPEXS Free Flash Decompiler (ffdec) as an out-of-process engine.
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const SCRIPT_FORMATS: &str = "script:as";
const RESOURCE_FORMATS: &str =
    "shape:svg,morphshape:svg,image:png_gif_jpeg,text:plain,sound:mp3_wav,font:ttf";
const RESOURCE_TYPES: &str = "image,shape,morphshape,sound,font,binaryData,text,symbolClass";

#[derive(Clone, Debug)]
pub struct Tool {
    pub java: PathBuf,
    pub jar: PathBuf,
}

impl Tool {
    pub fn describe(&self) -> String {
        format!("{} (java: {})", self.jar.display(), self.java.display())
    }
}

#[derive(Default)]
pub struct JpexsResult {
    pub scripts: BTreeMap<String, String>,
    pub out_dir: PathBuf,
    pub seconds: f64,
    pub error: Option<String>,
    pub log_tail: String,
}

pub struct Options {
    pub resources: bool,
    pub timeout: u64,
    pub deobfuscate: bool,
    pub memory: String,
}

/// The workspace root is the folder holding py_swf_editor next to rust_swf.
pub fn find_workspace() -> Option<PathBuf> {
    if let Ok(root) = std::env::var("FLASHDEC_WORKSPACE") {
        return Some(PathBuf::from(root));
    }
    let mut starts = vec![];
    if let Ok(exe) = std::env::current_exe() {
        starts.push(exe);
    }
    if let Ok(dir) = std::env::current_dir() {
        starts.push(dir);
    }
    starts.into_iter().find_map(|start| {
        start
            .ancestors()
            .find(|dir| dir.join("py_swf_editor").is_dir())
            .map(Path::to_path_buf)
    })
}

fn java_name() -> &'static str {
    if cfg!(windows) { "java.exe" } else { "java" }
}

fn on_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|candidate| candidate.is_file())
}

fn java_candidates(root: Option<&Path>) -> Vec<PathBuf> {
    let mut list = vec![];
    if let Ok(java) = std::env::var("FLASHDEC_JAVA") {
        list.push(PathBuf::from(java));
    }
    if let Some(root) = root {
        let jdks = root.join("py_swf_editor/.dev-references/jdk");
        if let Ok(entries) = fs::read_dir(&jdks) {
            let mut dirs: Vec<_> = entries.flatten().map(|e| e.path()).collect();
            dirs.sort();
            dirs.reverse();
            list.extend(dirs.into_iter().map(|d| d.join("bin").join(java_name())));
        }
    }
    if let Ok(home) = std::env::var("JAVA_HOME") {
        list.push(Path::new(&home).join("bin").join(java_name()));
    }
    list.extend(on_path(java_name()));
    list
}

fn jar_candidates(root: Option<&Path>) -> Vec<PathBuf> {
    let mut list = vec![];
    if let Ok(jar) = std::env::var("FLASHDEC_JPEXS_JAR") {
        list.push(PathBuf::from(jar));
    }
    if let Some(root) = root {
        for rel in [
            "py_swf_editor/.dev-references/ffdec/ffdec-cli.jar",
            "py_swf_editor/.dev-references/ffdec/ffdec.jar",
            "jpexs-decompiler-master/dist/ffdec-cli.jar",
            "jpexs-decompiler-master/dist/ffdec.jar",
        ] {
            list.push(root.join(rel));
        }
    }
    for var in ["ProgramFiles", "ProgramFiles(x86)"] {
        if let Ok(base) = std::env::var(var) {
            list.push(Path::new(&base).join("FFDec/ffdec-cli.jar"));
            list.push(Path::new(&base).join("FFDec/ffdec.jar"));
        }
    }
    list
}

pub fn locate(java: Option<PathBuf>, jar: Option<PathBuf>) -> Option<Tool> {
    let root = find_workspace();
    let java = java.or_else(|| java_candidates(root.as_deref()).into_iter().find(|p| p.is_file()))?;
    let jar = jar.or_else(|| jar_candidates(root.as_deref()).into_iter().find(|p| p.is_file()))?;
    Some(Tool { java, jar })
}

fn collect_scripts(dir: &Path, base: &Path, out: &mut BTreeMap<String, String>) {
    let Ok(entries) = fs::read_dir(dir) else { return };
    let mut entries: Vec<_> = entries.flatten().map(|e| e.path()).collect();
    entries.sort();
    for path in entries {
        if path.is_dir() {
            collect_scripts(&path, base, out);
        } else if path.extension().is_some_and(|e| e == "as") {
            let relative = path.strip_prefix(base).unwrap_or(&path);
            let key = relative.to_string_lossy().replace('\\', "/");
            let text = fs::read(&path).map(|b| String::from_utf8_lossy(&b).into_owned());
            out.insert(key, text.unwrap_or_default());
        }
    }
}

pub fn run(tool: &Tool, swf: &Path, out_dir: &Path, options: &Options) -> JpexsResult {
    let mut result = JpexsResult { out_dir: out_dir.to_path_buf(), ..Default::default() };
    let _ = fs::remove_dir_all(out_dir);
    if let Err(error) = fs::create_dir_all(out_dir) {
        result.error = Some(format!("cannot create {}: {error}", out_dir.display()));
        return result;
    }
    let config = format!(
        "parallelSpeedUp=1,decompilationTimeoutFile={},exportTimeout={},autoDeobfuscate={}",
        (options.timeout / 2).max(30),
        options.timeout,
        u8::from(options.deobfuscate)
    );
    let (formats, types) = if options.resources {
        (format!("{SCRIPT_FORMATS},{RESOURCE_FORMATS}"), format!("script,{RESOURCE_TYPES}"))
    } else {
        (SCRIPT_FORMATS.to_owned(), "script".to_owned())
    };
    let log_path = out_dir.with_extension("log");
    let started = Instant::now();
    let spawned = fs::File::create(&log_path).and_then(|log| {
        Command::new(&tool.java)
            .arg(format!("-Xmx{}", options.memory))
            .args(["-Djava.awt.headless=true", "-Dfile.encoding=UTF-8", "-jar"])
            .arg(&tool.jar)
            .args(["-config", &config, "-format", &formats, "-export", &types])
            .arg(out_dir)
            .arg(swf)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .spawn()
    });
    match spawned {
        Err(error) => result.error = Some(format!("could not start ffdec: {error}")),
        Ok(mut child) => {
            let limit = Duration::from_secs(options.timeout + 30);
            loop {
                match child.try_wait() {
                    Ok(Some(status)) => {
                        if !status.success() {
                            result.error = Some(format!("ffdec exit status {status}"));
                        }
                        break;
                    }
                    Ok(None) if started.elapsed() > limit => {
                        let _ = child.kill();
                        let _ = child.wait();
                        result.error = Some(format!("ffdec timed out after {}s", limit.as_secs()));
                        break;
                    }
                    Ok(None) => std::thread::sleep(Duration::from_millis(100)),
                    Err(error) => {
                        result.error = Some(format!("ffdec wait failed: {error}"));
                        break;
                    }
                }
            }
        }
    }
    result.seconds = started.elapsed().as_secs_f64();
    if let Ok(log) = fs::read(&log_path) {
        let text = String::from_utf8_lossy(&log);
        let skip = text.chars().count().saturating_sub(4000);
        result.log_tail = text.chars().skip(skip).collect();
    }
    let scripts = out_dir.join("scripts");
    collect_scripts(&scripts, &scripts, &mut result.scripts);
    result
}
