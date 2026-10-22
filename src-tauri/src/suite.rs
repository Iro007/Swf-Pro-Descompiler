//! Persistent Python integration. JSONL v1 is deliberately separate from the Rust worker.
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, AtomicUsize, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};
use tauri::State;

const MAX_JSONL_BYTES: u64 = 16 * 1024 * 1024;
const MAX_REQUEST_BYTES: usize = 8 * 1024 * 1024;
const MAX_STDERR_BYTES: usize = 8192;

#[derive(Clone)]
struct Launch {
    program: PathBuf,
    args: Vec<String>,
    cwd: Option<PathBuf>,
    workspace: PathBuf,
    kind: &'static str,
}

fn workspace_beside(executable_dir: &Path) -> PathBuf {
    executable_dir
        .ancestors()
        .take(8)
        .find(|ancestor| {
            ancestor
                .join("py_swf/py_swf/universal_worker.py")
                .is_file()
        })
        .unwrap_or(executable_dir)
        .to_owned()
}

impl Launch {
    fn resolve(executable: &Path, resource_dir: &Path) -> Result<Self> {
        let beside = executable.parent().context("Ejecutable sin directorio")?;
        for candidate in [
            beside.join("FlashSuiteWorker.exe"),
            resource_dir.join("FlashSuiteWorker.exe"),
            resource_dir.join("binaries/FlashSuiteWorker.exe"),
        ] {
            if candidate.is_file() {
                return Ok(Self {
                    program: candidate,
                    args: vec!["worker".into()],
                    cwd: Some(beside.to_owned()),
                    workspace: workspace_beside(beside),
                    kind: "packaged",
                });
            }
        }
        // Source fallback uses the actual sibling checkout, never the caller's cwd.
        let source = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .context("No se encontró el workspace fuente")?
            .join("py_swf");
        let python = source.join(".venv/Scripts/python.exe");
        ensure!(
            python.is_file() && source.join("py_swf/universal_worker.py").is_file(),
            "No se encuentra FlashSuiteWorker.exe. En desarrollo se requieren py_swf/.venv y py_swf/universal_worker.py"
        );
        let workspace = source
            .parent()
            .context("Workspace fuente sin directorio")?
            .to_owned();
        Ok(Self {
            program: python,
            args: vec![
                "-m".into(),
                "py_swf.universal_worker".into(),
                "worker".into(),
            ],
            cwd: Some(source),
            workspace,
            kind: "source",
        })
    }
}

#[derive(Clone, Copy)]
struct Limits {
    timeout: Duration,
    memory: usize,
}

impl Limits {
    fn for_operation(operation: &str) -> Self {
        let (variable, seconds, memory) = match operation {
            "compile_project" | "compile_saved" | "source_edit" | "tool_operation" => {
                ("FLASH_SUITE_COMPILE_TIMEOUT_SECONDS", 120, 2048)
            }
            "benchmark" => ("FLASH_SUITE_BENCHMARK_TIMEOUT_SECONDS", 600, 2048),
            _ => ("FLASH_SUITE_TIMEOUT_SECONDS", 30, 1024),
        };
        let timeout = bounded_env(variable, seconds, 1, 600);
        let memory_mib = bounded_env("FLASH_SUITE_MEMORY_MIB", memory, 256, 4096);
        Self {
            timeout: Duration::from_secs(timeout as u64),
            memory: memory_mib * 1024 * 1024,
        }
    }
}

fn bounded_env(name: &str, default: usize, min: usize, max: usize) -> usize {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(default)
        .clamp(min, max)
}

struct Worker {
    child: Mutex<Child>,
    input: Mutex<ChildStdin>,
    replies: Mutex<mpsc::Receiver<std::result::Result<Value, String>>>,
    stderr: Arc<Mutex<Vec<u8>>>,
    job: usize,
}

impl Worker {
    fn set_limit(&self, memory: usize) -> Result<()> {
        use windows_sys::Win32::System::JobObjects::*;
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        limits.BasicLimitInformation.LimitFlags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_JOB_MEMORY;
        limits.JobMemoryLimit = memory;
        ensure!(
            unsafe {
                SetInformationJobObject(
                    self.job as _,
                    JobObjectExtendedLimitInformation,
                    &limits as *const _ as _,
                    std::mem::size_of_val(&limits) as u32,
                )
            } != 0,
            "No se pudo establecer el límite de memoria de la suite: {}",
            std::io::Error::last_os_error()
        );
        Ok(())
    }

    fn terminate(&self) {
        // Terminate the Job, not just Python: compilers and tool subprocesses inherit it.
        unsafe {
            windows_sys::Win32::System::JobObjects::TerminateJobObject(self.job as _, 1);
        }
        if let Ok(mut child) = self.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    fn diagnostic_tail(&self) -> String {
        self.stderr
            .lock()
            .map(|bytes| String::from_utf8_lossy(&bytes).trim().to_owned())
            .unwrap_or_default()
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        self.terminate();
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.job as _);
        }
    }
}

fn spawn_worker(launch: &Launch, state_dir: &Path, limits: Limits) -> Result<Arc<Worker>> {
    use std::os::windows::{io::AsRawHandle, process::CommandExt};
    use windows_sys::Win32::System::JobObjects::*;
    let mut command = Command::new(&launch.program);
    command
        .args(&launch.args)
        .env("FLASH_SUITE_STATE_DIR", state_dir)
        .env("FLASH_SUITE_WORKSPACE", &launch.workspace)
        .env("FLASH_SUITE_RUFFLE_WEB", "1")
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(0x08000000);
    if let Some(cwd) = &launch.cwd {
        command.current_dir(cwd);
    }
    let mut child = command.spawn().with_context(|| {
        format!(
            "No se pudo iniciar el worker universal {}",
            launch.program.display()
        )
    })?;
    let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if job.is_null() {
        let _ = child.kill();
        let _ = child.wait();
        bail!("No se pudo crear el supervisor de la suite");
    }
    if unsafe { AssignProcessToJobObject(job, child.as_raw_handle() as _) } == 0 {
        let error = std::io::Error::last_os_error();
        let _ = child.kill();
        let _ = child.wait();
        unsafe { windows_sys::Win32::Foundation::CloseHandle(job) };
        bail!("No se pudo aislar el árbol del worker universal: {error}");
    }
    let input = child.stdin.take().context("Worker universal sin stdin")?;
    let output = child.stdout.take().context("Worker universal sin stdout")?;
    let mut stderr_pipe = child.stderr.take().context("Worker universal sin stderr")?;
    let stderr = Arc::new(Mutex::new(Vec::new()));
    let tail = stderr.clone();
    std::thread::spawn(move || {
        let mut block = [0_u8; 2048];
        loop {
            match stderr_pipe.read(&mut block) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if let Ok(mut bytes) = tail.lock() {
                        bytes.extend_from_slice(&block[..n]);
                        if bytes.len() > MAX_STDERR_BYTES {
                            let excess = bytes.len() - MAX_STDERR_BYTES;
                            bytes.drain(..excess);
                        }
                    }
                }
            }
        }
    });
    let (tx, rx) = mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let mut reader = BufReader::new(output);
        loop {
            let mut line = String::new();
            let parsed = match reader
                .by_ref()
                .take(MAX_JSONL_BYTES + 1)
                .read_line(&mut line)
            {
                Ok(0) => break,
                Ok(n) if n as u64 > MAX_JSONL_BYTES => Err("Respuesta JSONL mayor a 16 MiB".into()),
                Ok(_) if !line.ends_with('\n') => Err("Respuesta JSONL incompleta".into()),
                Ok(_) => serde_json::from_str(&line)
                    .map_err(|error| format!("Respuesta JSONL inválida: {error}")),
                Err(error) => Err(format!("No se pudo leer la respuesta: {error}")),
            };
            let broken = parsed.is_err();
            if tx.send(parsed).is_err() || broken {
                break;
            }
        }
    });
    let worker = Arc::new(Worker {
        child: Mutex::new(child),
        input: Mutex::new(input),
        replies: Mutex::new(rx),
        stderr,
        job: job as usize,
    });
    worker.set_limit(limits.memory)?;
    Ok(worker)
}

struct Inner {
    launch: std::result::Result<Launch, String>,
    state_dir: PathBuf,
    worker: Mutex<Option<Arc<Worker>>>,
    gate: Mutex<()>,
    next: AtomicU64,
    epoch: AtomicU64,
    pending: AtomicUsize,
}

#[derive(Clone)]
pub struct SuiteHost(Arc<Inner>);
struct Admission<'a>(&'a AtomicUsize);
impl Drop for Admission<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

impl SuiteHost {
    pub fn new(executable: &Path, resource_dir: &Path) -> Result<Self> {
        let state_dir = std::env::temp_dir()
            .join("flash-suite")
            .join(uuid::Uuid::new_v4().to_string());
        fs::create_dir_all(&state_dir)?;
        Ok(Self::with_launch(
            Launch::resolve(executable, resource_dir).map_err(|error| format!("{error:#}")),
            state_dir,
        ))
    }

    fn with_launch(launch: std::result::Result<Launch, String>, state_dir: PathBuf) -> Self {
        Self(Arc::new(Inner {
            launch,
            state_dir,
            worker: Mutex::new(None),
            gate: Mutex::new(()),
            next: AtomicU64::new(1),
            epoch: AtomicU64::new(0),
            pending: AtomicUsize::new(0),
        }))
    }

    pub fn startup(&self) -> Value {
        json!({
            "available": self.0.launch.is_ok(),
            "worker_kind": self.0.launch.as_ref().ok().map(|launch| launch.kind),
            "workspace": self.0.launch.as_ref().ok().map(|launch| &launch.workspace),
            "error": self.0.launch.as_ref().err(),
            "state_dir": self.0.state_dir,
            "protocol_version": 1,
        })
    }

    pub fn cancel(&self) {
        self.0.epoch.fetch_add(1, Ordering::SeqCst);
        self.stop();
    }

    pub fn stop(&self) {
        if let Ok(mut slot) = self.0.worker.lock()
            && let Some(worker) = slot.take()
        {
            worker.terminate();
        }
    }

    fn check_epoch(&self, epoch: u64) -> Result<()> {
        ensure!(
            epoch == self.0.epoch.load(Ordering::SeqCst),
            "Operación de suite cancelada"
        );
        Ok(())
    }

    fn request_at(
        &self,
        operation: &str,
        args: Value,
        epoch: u64,
        limits: Limits,
    ) -> Result<Value> {
        ensure!(
            !operation.is_empty()
                && operation.len() <= 64
                && operation
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_'),
            "Nombre de operación de suite inválido"
        );
        ensure!(
            args.is_object(),
            "Los argumentos de suite deben ser un objeto JSON"
        );
        if self.0.pending.fetch_add(1, Ordering::SeqCst) >= 8 {
            self.0.pending.fetch_sub(1, Ordering::SeqCst);
            bail!("La cola de suite está llena; espere a la operación activa");
        }
        let _admission = Admission(&self.0.pending);
        let _gate = self
            .0
            .gate
            .lock()
            .map_err(|_| anyhow::anyhow!("Supervisor de suite ocupado"))?;
        self.check_epoch(epoch)?;
        let started = Instant::now();
        let launch = self
            .0
            .launch
            .as_ref()
            .map_err(|error| anyhow::anyhow!("{error}"))?;
        let worker = {
            let mut slot = self
                .0
                .worker
                .lock()
                .map_err(|_| anyhow::anyhow!("Supervisor de suite ocupado"))?;
            if slot.is_none() {
                *slot = Some(spawn_worker(launch, &self.0.state_dir, limits)?);
            }
            slot.as_ref()
                .context("Worker universal no disponible")?
                .clone()
        };
        if let Err(error) = self
            .check_epoch(epoch)
            .and_then(|_| worker.set_limit(limits.memory))
        {
            self.stop();
            return Err(error);
        }
        let id = self.0.next.fetch_add(1, Ordering::Relaxed);
        let request = serde_json::to_vec(
            &json!({"protocol_version": 1, "id": id, "op": operation, "args": args}),
        )?;
        ensure!(
            request.len() <= MAX_REQUEST_BYTES,
            "Solicitud de suite mayor a 8 MiB"
        );
        // A worker that stops reading its pipe must not bypass the operation deadline.
        let writer = worker.clone();
        let (sent_tx, sent_rx) = mpsc::sync_channel(1);
        std::thread::spawn(move || {
            let sent: Result<()> = (|| {
                let mut input = writer
                    .input
                    .lock()
                    .map_err(|_| anyhow::anyhow!("Worker universal ocupado"))?;
                input.write_all(&request)?;
                input.write_all(b"\n")?;
                input.flush()?;
                Ok(())
            })();
            let _ = sent_tx.send(sent);
        });
        let sent = sent_rx.recv_timeout(limits.timeout.saturating_sub(started.elapsed()));
        self.check_epoch(epoch)?;
        if !matches!(sent, Ok(Ok(()))) {
            self.stop();
            let error = match sent {
                Ok(Err(error)) => error.to_string(),
                Err(error) => error.to_string(),
                Ok(Ok(())) => unreachable!(),
            };
            bail!(
                "No se pudo enviar la solicitud al worker universal dentro del límite: {error}. La próxima operación recuperará el proyecto"
            );
        }
        let reply = worker
            .replies
            .lock()
            .map_err(|_| anyhow::anyhow!("Worker universal ocupado"))?
            .recv_timeout(limits.timeout.saturating_sub(started.elapsed()));
        self.check_epoch(epoch)?;
        let reply = match reply {
            Ok(Ok(value)) => value,
            other => {
                self.stop();
                let detail = match other {
                    Ok(Err(error)) => error,
                    Err(mpsc::RecvTimeoutError::Timeout) => {
                        format!("Límite de {} segundos agotado", limits.timeout.as_secs())
                    }
                    Err(mpsc::RecvTimeoutError::Disconnected) => {
                        "El worker universal cerró su salida".into()
                    }
                    Ok(Ok(_)) => unreachable!(),
                };
                let tail = worker.diagnostic_tail();
                bail!(
                    "{detail}. El proyecto se recuperará en la próxima operación{}",
                    if tail.is_empty() {
                        String::new()
                    } else {
                        format!(": {tail}")
                    }
                );
            }
        };
        if reply["id"].as_u64() != Some(id)
            || !reply["ok"].is_boolean()
            || reply
                .get("protocol_version")
                .is_some_and(|version| version.as_u64() != Some(1))
        {
            self.stop();
            bail!("Respuesta fuera de secuencia o incompatible con JSONL v1");
        }
        if reply["ok"].as_bool() == Some(false) {
            let Some(error) = reply.get("error") else {
                self.stop();
                bail!("Respuesta de error sin diagnóstico");
            };
            bail!(
                "{}",
                error
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| error.to_string())
            );
        }
        match reply.get("result") {
            Some(result) => Ok(result.clone()),
            None => {
                self.stop();
                bail!("Respuesta exitosa sin resultado");
            }
        }
    }

    pub fn request(&self, operation: &str, args: Value) -> Result<Value> {
        self.request_at(
            operation,
            args,
            self.0.epoch.load(Ordering::SeqCst),
            Limits::for_operation(operation),
        )
    }

    pub fn smoke_report(&self, destination: &Path) -> Result<()> {
        let result = self.request("ping", json!({}));
        let report = json!({
            "startup": self.startup(),
            "success": result.is_ok(),
            "result": result.as_ref().ok(),
            "error": result.as_ref().err().map(|error| format!("{error:#}")),
        });
        // Verification output cannot overwrite user files.
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(destination)?;
        file.write_all(&serde_json::to_vec_pretty(&report)?)?;
        file.sync_all()?;
        Ok(())
    }
}

#[tauri::command]
pub async fn suite_rpc(
    operation: String,
    args: Value,
    host: State<'_, SuiteHost>,
) -> Result<Value, String> {
    let host = host.inner().clone();
    let epoch = host.0.epoch.load(Ordering::SeqCst);
    tauri::async_runtime::spawn_blocking(move || {
        host.request_at(&operation, args, epoch, Limits::for_operation(&operation))
            .map_err(|error| format!("{error:#}"))
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
pub fn suite_cancel(host: State<'_, SuiteHost>) {
    host.cancel();
}

#[tauri::command]
pub async fn suite_pick(kind: String) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let dialog = rfd::FileDialog::new();
        let path = match kind.as_str() {
            "swf" => dialog.add_filter("Flash SWF", &["swf"]).pick_file(),
            "project" => dialog
                .add_filter("Proyecto Flash Suite", &["pyswfproj"])
                .pick_file(),
            "project_output" => dialog
                .add_filter("Proyecto Flash Suite", &["pyswfproj"])
                .set_file_name("project.pyswfproj")
                .save_file(),
            "source" => dialog
                .add_filter(
                    "ActionScript / Haxe / ensamblado",
                    &["as", "mxml", "hx", "hxml", "xml", "asasm", "flm", "abc"],
                )
                .pick_file(),
            "image" => dialog
                .add_filter("Imagen", &["png", "jpg", "jpeg", "gif", "bmp", "webp"])
                .pick_file(),
            "audio" => dialog.add_filter("Audio", &["mp3", "wav"]).pick_file(),
            "output" => dialog
                .add_filter("Flash SWF", &["swf"])
                .set_file_name("compiled.swf")
                .save_file(),
            "folder" => dialog.pick_folder(),
            "tool" => dialog
                .add_filter("Programa", &["exe", "jar", "bat", "cmd"])
                .pick_file(),
            _ => return Err("Tipo de selector de suite desconocido".into()),
        };
        path.map(|value| {
            if value.is_absolute() {
                Ok(value)
            } else {
                std::env::current_dir().map(|cwd| cwd.join(value))
            }
            .map(|absolute| absolute.to_string_lossy().into_owned())
            .map_err(|error| error.to_string())
        })
        .transpose()
    })
    .await
    .map_err(|error| error.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    const MOCK: &str = r#"
const rl=require('node:readline').createInterface({input:process.stdin});
const fs=require('node:fs'),path=require('node:path');
const state=path.join(process.env.FLASH_SUITE_STATE_DIR,'mock.json');
rl.on('line',line=>{
 const r=JSON.parse(line); const reply=v=>process.stdout.write(JSON.stringify({id:r.id,ok:true,result:v})+'\n');
 if(r.op==='store'){fs.writeFileSync(state,JSON.stringify(r.args));reply(r.args);}
 else if(r.op==='read')reply(fs.existsSync(state)?JSON.parse(fs.readFileSync(state)):null);
 else if(r.op==='fail')process.stdout.write(JSON.stringify({id:r.id,ok:false,error:'expected operation error'})+'\n');
 else if(r.op==='malformed')process.stdout.write('not-json\n');
 else if(r.op==='wrong_id')process.stdout.write(JSON.stringify({id:r.id+1,ok:true,result:{}})+'\n');
 else if(r.op==='wrong_version')process.stdout.write(JSON.stringify({id:r.id,ok:true,protocol_version:2,result:{}})+'\n');
 else if(r.op==='hang'){fs.writeFileSync(path.join(process.env.FLASH_SUITE_STATE_DIR,'pending.marker'),'waiting');}
 else if(r.op==='child'){const c=require('node:child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore',windowsHide:true});c.unref();reply({pid:c.pid});}
 else reply({pid:process.pid,state_dir:process.env.FLASH_SUITE_STATE_DIR});
});
"#;

    fn host() -> SuiteHost {
        let state = std::env::temp_dir().join(format!("flash-suite-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&state).unwrap();
        SuiteHost::with_launch(
            Ok(Launch {
                program: PathBuf::from("node"),
                args: vec!["-e".into(), MOCK.into()],
                cwd: None,
                workspace: state.clone(),
                kind: "mock",
            }),
            state,
        )
    }

    fn cleanup(host: &SuiteHost) {
        host.stop();
        fs::remove_dir_all(&host.0.state_dir).unwrap();
    }

    #[test]
    fn state_persists_after_worker_restart_and_operation_errors() {
        let h = host();
        h.request("store", json!({"project":"saved state"}))
            .unwrap();
        assert!(
            h.request("fail", json!({}))
                .unwrap_err()
                .to_string()
                .contains("expected operation error")
        );
        let first = h.request("ping", json!({})).unwrap();
        assert_eq!(
            h.request("read", json!({})).unwrap()["project"],
            "saved state"
        );
        h.cancel();
        assert!(h.0.worker.lock().unwrap().is_none());
        let second = h.request("ping", json!({})).unwrap();
        assert_ne!(first["pid"], second["pid"]);
        assert_eq!(first["state_dir"], second["state_dir"]);
        assert_eq!(
            h.request("read", json!({})).unwrap()["project"],
            "saved state"
        );
        cleanup(&h);
    }

    #[test]
    fn invalid_protocol_retires_worker_then_recovers() {
        let h = host();
        for operation in ["malformed", "wrong_id", "wrong_version"] {
            assert!(h.request(operation, json!({})).is_err());
            assert!(h.0.worker.lock().unwrap().is_none());
            assert!(h.request("ping", json!({})).is_ok());
        }
        cleanup(&h);
    }

    #[test]
    fn timeout_retires_worker_and_stale_requests_are_cancelled() {
        let h = host();
        let epoch = h.0.epoch.load(Ordering::SeqCst);
        let short = Limits {
            timeout: Duration::from_millis(100),
            memory: 1024 * 1024 * 1024,
        };
        assert!(h.request_at("hang", json!({}), epoch, short).is_err());
        assert!(h.0.worker.lock().unwrap().is_none());
        h.cancel();
        assert!(
            h.request_at("ping", json!({}), epoch, short)
                .unwrap_err()
                .to_string()
                .contains("cancelada")
        );
        assert!(h.request("ping", json!({})).is_ok());
        cleanup(&h);
    }

    #[test]
    fn a_worker_that_never_reads_stdin_cannot_block_the_deadline() {
        let template = host();
        let directory = template.0.state_dir.clone();
        let h = SuiteHost::with_launch(
            Ok(Launch {
                program: PathBuf::from("node"),
                args: vec!["-e".into(), "setInterval(()=>{},1000)".into()],
                cwd: None,
                workspace: directory.clone(),
                kind: "mock",
            }),
            directory,
        );
        let short = Limits {
            timeout: Duration::from_millis(200),
            memory: 1024 * 1024 * 1024,
        };
        let error = h
            .request_at("ping", json!({"large": "x".repeat(512 * 1024)}), 0, short)
            .unwrap_err();
        assert!(error.to_string().contains("enviar la solicitud"));
        assert!(h.0.worker.lock().unwrap().is_none());
        cleanup(&h);
    }

    #[test]
    fn cancelling_a_pending_request_unblocks_it_and_keeps_recovery_state() {
        let h = host();
        h.request("store", json!({"project": "recover me"}))
            .unwrap();
        let pending = h.clone();
        let (tx, rx) = mpsc::channel();
        let request = std::thread::spawn(move || {
            let _ = tx.send(pending.request("hang", json!({})));
        });
        for _ in 0..40 {
            if h.0.state_dir.join("pending.marker").is_file() {
                break;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        assert!(h.0.state_dir.join("pending.marker").is_file());
        h.cancel();
        let error = rx
            .recv_timeout(Duration::from_secs(3))
            .unwrap()
            .unwrap_err();
        assert!(error.to_string().contains("cancelada"));
        request.join().unwrap();
        assert_eq!(
            h.request("read", json!({})).unwrap()["project"],
            "recover me"
        );
        cleanup(&h);
    }

    #[test]
    fn cancel_terminates_child_processes() {
        use windows_sys::Win32::System::Threading::*;
        let h = host();
        let pid = h.request("child", json!({})).unwrap()["pid"]
            .as_u64()
            .unwrap() as u32;
        let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        assert!(!handle.is_null());
        h.cancel();
        let mut active = true;
        for _ in 0..40 {
            let mut exit_code = 0;
            active = unsafe { GetExitCodeProcess(handle, &mut exit_code) } != 0 && exit_code == 259;
            if !active {
                break;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        unsafe { windows_sys::Win32::Foundation::CloseHandle(handle) };
        assert!(!active, "A compiler child survived suite cancellation");
        cleanup(&h);
    }

    #[test]
    #[ignore = "Requires the py_swf virtual environment and worker source"]
    fn source_worker_opens_and_restores_a_real_project() {
        let executable = std::env::current_exe().unwrap();
        let launch = Launch::resolve(&executable, executable.parent().unwrap()).unwrap();
        assert_eq!(launch.kind, "source");
        let directory =
            std::env::temp_dir().join(format!("flash-suite-integration-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&directory).unwrap();
        let source = directory.join("original.swf");
        let bytes = b"FWS\x06\x12\x00\x00\x00\x08\x00\x00\x18\x01\x00\x40\x00\x00\x00";
        fs::write(&source, bytes).unwrap();
        let state = directory.join("session");
        fs::create_dir(&state).unwrap();
        let h = SuiteHost::with_launch(Ok(launch), state);
        assert_eq!(h.request("ping", json!({})).unwrap()["protocol_version"], 1);
        let opened = h.request("open", json!({"path": source})).unwrap();
        assert_eq!(opened["frame_count"], 1);
        assert_eq!(opened["dirty"], false);
        assert_eq!(opened["validation"]["valid"], true);
        h.cancel();
        let restored = h.request("state", json!({})).unwrap();
        assert_eq!(opened["revision"], restored["revision"]);
        assert_eq!(opened["source_path"], restored["source_path"]);
        assert_eq!(fs::read(&source).unwrap(), bytes);
        h.stop();
        fs::remove_dir_all(directory).unwrap();
    }
}
