#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        mpsc,
    },
    time::Duration,
};
use tauri::{Manager, State};
mod suite;
use suite::SuiteHost;

struct Worker {
    child: Mutex<Child>,
    input: Mutex<ChildStdin>,
    replies: Mutex<mpsc::Receiver<Value>>,
    job: usize,
}
impl Drop for Worker {
    fn drop(&mut self) {
        if let Ok(child) = self.child.get_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if self.job != 0 {
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(self.job as _);
            }
        }
    }
}
struct Session {
    id: String,
    original: PathBuf,
    cache: PathBuf,
    filename: String,
}
struct HostInner {
    worker: Mutex<Option<Arc<Worker>>>,
    session: Mutex<Option<Session>>,
    gate: Mutex<()>,
    next: AtomicU64,
    cancel_epoch: AtomicU64,
    pending: AtomicUsize,
    batch_active: AtomicBool,
    worker_path: PathBuf,
    startup: Option<String>,
    report: Option<PathBuf>,
}
#[derive(Clone)]
struct Host(Arc<HostInner>);
struct Admission<'a>(&'a AtomicUsize);
impl Drop for Admission<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}
struct BatchGuard(Host);
impl Drop for BatchGuard {
    fn drop(&mut self) {
        self.0.0.batch_active.store(false, Ordering::SeqCst);
    }
}

fn start_worker(path: &Path) -> Result<Arc<Worker>> {
    use std::os::windows::{io::AsRawHandle, process::CommandExt};
    use windows_sys::Win32::System::JobObjects::*;
    let mut child = Command::new(path)
        .arg("worker")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(0x08000000)
        .spawn()
        .context("No se pudo iniciar rustswf.exe")?;
    let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if job.is_null() {
        let _ = child.kill();
        bail!("No se pudo crear el límite de memoria del worker");
    }
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    limits.BasicLimitInformation.LimitFlags =
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY;
    limits.ProcessMemoryLimit = 1024 * 1024 * 1024;
    let limited = unsafe {
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as _,
            std::mem::size_of_val(&limits) as u32,
        ) != 0
            && AssignProcessToJobObject(job, child.as_raw_handle() as _) != 0
    };
    if !limited {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(job);
        }
        let _ = child.kill();
        bail!("No se pudo aplicar el límite de memoria del worker");
    }
    let input = child.stdin.take().context("Worker sin stdin")?;
    let output = child.stdout.take().context("Worker sin stdout")?;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(output);
        loop {
            let mut line = String::new();
            match reader
                .by_ref()
                .take(8 * 1024 * 1024 + 1)
                .read_line(&mut line)
            {
                Ok(0) | Err(_) => break,
                Ok(n) if n > 8 * 1024 * 1024 => break,
                Ok(_) => {
                    if let Ok(value) = serde_json::from_str(&line) {
                        if tx.send(value).is_err() {
                            break;
                        }
                    } else {
                        break;
                    }
                }
            }
        }
    });
    Ok(Arc::new(Worker {
        child: Mutex::new(child),
        input: Mutex::new(input),
        replies: Mutex::new(rx),
        job: job as usize,
    }))
}
impl Host {
    fn epoch(&self) -> u64 {
        self.0.cancel_epoch.load(Ordering::SeqCst)
    }
    fn check_epoch(&self, epoch: u64) -> Result<()> {
        if epoch != self.epoch() {
            bail!("Operación cancelada");
        }
        Ok(())
    }
    fn admit(&self) -> Result<Admission<'_>> {
        if self.0.pending.fetch_add(1, Ordering::SeqCst) >= 8 {
            self.0.pending.fetch_sub(1, Ordering::SeqCst);
            bail!("La cola está llena; espere a la operación activa");
        }
        Ok(Admission(&self.0.pending))
    }
    fn stop(&self) {
        if let Ok(mut slot) = self.0.worker.lock()
            && let Some(worker) = slot.take()
            && let Ok(mut child) = worker.child.lock()
        {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    fn request(&self, worker: &Worker, op: &str, args: Value) -> Result<Value> {
        let id = self.0.next.fetch_add(1, Ordering::Relaxed);
        let sent: Result<()> = (|| {
            let mut input = worker
                .input
                .lock()
                .map_err(|_| anyhow::anyhow!("Worker ocupado"))?;
            writeln!(
                input,
                "{}",
                json!({"request_id":id,"operation":op,"args":args})
            )?;
            input.flush()?;
            Ok(())
        })();
        if let Err(error) = sent {
            self.stop();
            bail!("Worker detenido; la siguiente operación recargará el documento: {error}");
        }
        let reply = worker
            .replies
            .lock()
            .map_err(|_| anyhow::anyhow!("Worker ocupado"))?
            .recv_timeout(Duration::from_secs(30));
        let reply = match reply {
            Ok(value) => value,
            Err(error) => {
                self.stop();
                bail!("Operación cancelada o límite de 30 segundos: {error}");
            }
        };
        if reply["request_id"].as_u64() != Some(id) {
            self.stop();
            bail!("Respuesta fuera de secuencia");
        }
        if let Some(error) = reply["error"].as_str() {
            bail!("{error}");
        }
        Ok(reply["result"].clone())
    }
    fn ensure_worker(&self) -> Result<Arc<Worker>> {
        if let Some(worker) = self.0.worker.lock().unwrap().clone() {
            return Ok(worker);
        }
        let path = self
            .0
            .session
            .lock()
            .unwrap()
            .as_ref()
            .context("No hay SWF abierto")?
            .original
            .clone();
        let worker = start_worker(&self.0.worker_path)?;
        *self.0.worker.lock().unwrap() = Some(worker.clone());
        if let Err(error) = self.request(&worker, "open", json!({"path":path})) {
            self.stop();
            return Err(error);
        }
        Ok(worker)
    }
    fn open(&self, path: PathBuf, epoch: u64) -> Result<Value> {
        let _admission = self.admit()?;
        let _guard = self.0.gate.lock().unwrap();
        self.check_epoch(epoch)?;
        let path = path
            .canonicalize()
            .context("No se pudo resolver el SWF original")?;
        let mut bytes = Vec::new();
        fs::File::open(&path)?
            .take(100 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() > 100 * 1024 * 1024 {
            bail!("El SWF supera 100 MiB");
        }
        let id = uuid::Uuid::new_v4().to_string();
        let cache = std::env::temp_dir().join("rustswf-studio").join(&id);
        fs::create_dir_all(&cache)?;
        let original = cache.join("original.swf");
        fs::write(&original, &bytes)?;
        if let Err(error) = self.check_epoch(epoch) {
            let _ = fs::remove_dir_all(&cache);
            return Err(error);
        }
        self.stop();
        let worker = start_worker(&self.0.worker_path)?;
        *self.0.worker.lock().unwrap() = Some(worker.clone());
        let result = self
            .request(&worker, "open", json!({"path":original}))
            .and_then(|value| {
                self.check_epoch(epoch)?;
                Ok(value)
            });
        let mut value = match result {
            Ok(value) => value,
            Err(error) => {
                self.stop();
                drop(worker);
                let _ = fs::remove_dir_all(&cache);
                return Err(error);
            }
        };
        let filename = path
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        value["info"]["filename"] = json!(filename);
        if let Some(old) = self.0.session.lock().unwrap().replace(Session {
            id: id.clone(),
            original,
            cache,
            filename,
        }) {
            let _ = fs::remove_dir_all(old.cache);
        }
        *self.0.worker.lock().unwrap() = Some(worker);
        value["session_id"] = json!(id);
        value["source_path"] = json!(path);
        Ok(value)
    }
    fn rpc(&self, sid: &str, op: &str, mut args: Value, epoch: u64) -> Result<Value> {
        let _admission = self.admit()?;
        let _guard = self.0.gate.lock().unwrap();
        self.check_epoch(epoch)?;
        let session = self.0.session.lock().unwrap();
        let current = session.as_ref().context("No hay SWF abierto")?;
        if current.id != sid {
            bail!("Sesión reemplazada; vuelva a seleccionar el recurso");
        }
        if op == "asset" {
            args["dir"] = json!(current.cache.join("assets"));
        }
        let filename = current.filename.clone();
        drop(session);
        let worker = self.ensure_worker()?;
        let mut value = self.request(&worker, op, args)?;
        self.check_epoch(epoch)?;
        if op == "info" {
            value["info"]["filename"] = json!(filename);
        }
        Ok(value)
    }
}

#[tauri::command]
async fn open_document(path: Option<String>, host: State<'_, Host>) -> Result<Value, String> {
    let host = host.inner().clone();
    let epoch = host.epoch();
    tauri::async_runtime::spawn_blocking(move || {
        let path = match path {
            Some(p) => PathBuf::from(p),
            None => match rfd::FileDialog::new()
                .add_filter("Flash SWF", &["swf"])
                .pick_file()
            {
                Some(p) => p,
                None => return Ok(Value::Null),
            },
        };
        host.open(path, epoch).map_err(|e| format!("{e:#}"))
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn rpc(
    session_id: String,
    operation: String,
    args: Value,
    host: State<'_, Host>,
) -> Result<Value, String> {
    if ![
        "info",
        "resources",
        "scripts",
        "outline",
        "metadata",
        "decompile",
        "disassemble",
        "raw",
        "asset",
    ]
    .contains(&operation.as_str())
    {
        return Err("Operación no autorizada en la interfaz".into());
    }
    let host = host.inner().clone();
    let epoch = host.epoch();
    tauri::async_runtime::spawn_blocking(move || {
        host.rpc(&session_id, &operation, args, epoch)
            .map_err(|e| format!("{e:#}"))
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
fn cancel(host: State<'_, Host>) {
    host.0.cancel_epoch.fetch_add(1, Ordering::SeqCst);
    host.stop();
}
#[tauri::command]
fn startup(host: State<'_, Host>, suite: State<'_, SuiteHost>) -> Value {
    json!({"path":host.0.startup,"smoke":host.0.report.is_some(),"suite":suite.startup()})
}
#[tauri::command]
fn ui_verified(evidence: Value, host: State<'_, Host>) -> Result<(), String> {
    if let Some(path) = &host.0.report {
        let mut report: Value = fs::read(path)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or(json!({"webview_loaded":true}));
        if evidence.get("verification").is_some() {
            report["verification"] = evidence["verification"].clone();
        } else {
            report["observation"] = evidence;
        }
        fs::write(path, serde_json::to_vec_pretty(&report).unwrap()).map_err(|e| e.to_string())?;
    }
    Ok(())
}
#[tauri::command]
async fn verify_export(
    session_id: String,
    id: String,
    format: String,
    kind: String,
    host: State<'_, Host>,
) -> Result<Value, String> {
    let host = host.inner().clone();
    let epoch = host.epoch();
    let report = host
        .0
        .report
        .clone()
        .ok_or("Esta operación solo está disponible en verificación local")?;
    tauri::async_runtime::spawn_blocking(move || {
        host.check_epoch(epoch).map_err(|e| e.to_string())?;
        let output = report
            .parent()
            .unwrap_or(Path::new("."))
            .join("smoke-exports");
        fs::create_dir_all(&output).map_err(|e| e.to_string())?;
        let extension = match format.as_str() {
            "as" => "as",
            "pcode" => "pcode.txt",
            "svg" => "svg",
            "png" => "png",
            "jpeg" => "jpg",
            "raw" => "bin",
            _ => return Err("Formato de prueba inválido".into()),
        };
        let path = output.join(format!("{}.{}", uuid::Uuid::new_v4(), extension));
        let operation = if kind == "script" {
            "export_script"
        } else {
            "export_resource"
        };
        let mut result = host
            .rpc(
                &session_id,
                operation,
                json!({"id":id,"format":format,"path":path}),
                epoch,
            )
            .map_err(|e| e.to_string())?;
        use sha2::Digest;
        let bytes = fs::read(&path).map_err(|e| e.to_string())?;
        result["readback_sha256"] = json!(format!("{:x}", sha2::Sha256::digest(&bytes)));
        result["readback_bytes"] = json!(bytes.len());
        Ok(result)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn export_item(
    session_id: String,
    id: String,
    format: String,
    kind: String,
    host: State<'_, Host>,
) -> Result<Value, String> {
    let host = host.inner().clone();
    let epoch = host.epoch();
    tauri::async_runtime::spawn_blocking(move || {
        let extension = if format == "raw" {
            "bin"
        } else if format == "pcode" {
            "pcode.txt"
        } else {
            &format
        };
        let name = format!(
            "{}.{}",
            id.chars()
                .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
                .collect::<String>(),
            extension
        );
        let Some(path) = rfd::FileDialog::new().set_file_name(name).save_file() else {
            return Ok(Value::Null);
        };
        if path.exists() {
            return Err("El archivo ya existe. Elija un nombre nuevo para conservarlo.".into());
        }
        let op = if kind == "script" {
            "export_script"
        } else {
            "export_resource"
        };
        host.rpc(
            &session_id,
            op,
            json!({"id":id,"format":format,"path":path}),
            epoch,
        )
        .map_err(|e| format!("{e:#}"))
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn export_batch(
    session_id: String,
    host: State<'_, Host>,
    app: tauri::AppHandle,
) -> Result<Value, String> {
    use tauri::Emitter;
    let host = host.inner().clone();
    let epoch = host.epoch();
    if host
        .0
        .batch_active
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return Err("Ya hay una exportación por lote activa".into());
    }
    let batch_guard = BatchGuard(host.clone());
    tauri::async_runtime::spawn_blocking(move ||->Result<Value,String>{
        let _batch_guard=batch_guard;
        host.check_epoch(epoch).map_err(|e|e.to_string())?;
        {let session=host.0.session.lock().unwrap();if session.as_ref().is_none_or(|s|s.id!=session_id){return Err("Sesión reemplazada".into());}}
        let Some(folder)=rfd::FileDialog::new().pick_folder()else{return Ok(Value::Null)};
        host.check_epoch(epoch).map_err(|e|e.to_string())?;
        let info=host.rpc(&session_id,"info",json!({}),epoch).map_err(|e|e.to_string())?;
        let destination=folder.join(format!("RustSWF-{}",uuid::Uuid::new_v4()));
        fs::create_dir(&destination).map_err(|e|e.to_string())?;
        let mut manifest=Vec::new();let mut offset=0;
        let mut terminal_error:Option<String>=None;let mut total=0;
        'batch: loop {
            if let Err(error)=host.check_epoch(epoch){terminal_error=Some(error.to_string());break;}
            let page=match host.rpc(&session_id,"resources",json!({"offset":offset,"limit":200}),epoch){Ok(page)=>page,Err(error)=>{terminal_error=Some(format!("{error:#}"));break;}};
            total=page["total"].as_u64().unwrap_or(0) as usize;
            let Some(items)=page["items"].as_array()else{terminal_error=Some("Listado inválido".into());break;};if items.is_empty(){break;}
            for item in items {
                if let Err(error)=host.check_epoch(epoch){terminal_error=Some(error.to_string());break 'batch;}
                let id=item["id"].as_str().unwrap_or("");let formats=item["formats"].as_array();
                let format=formats.and_then(|f|f.iter().find_map(|v|v.as_str().filter(|s|*s!="raw"))).unwrap_or("raw");
                let name=id.chars().map(|c|if c.is_ascii_alphanumeric(){c}else{'_'}).collect::<String>();
                let path=destination.join(format!("{name}.{}",if format=="raw"{"bin"}else{format}));
                let result=host.rpc(&session_id,"export_resource",json!({"id":id,"format":format,"path":path}),epoch);
                manifest.push(json!({"resource":item,"format":format,"result":result.as_ref().ok(),"error":result.as_ref().err().map(|e|format!("{e:#}"))}));
                offset+=1;
                let _=app.emit("export-progress",json!({"completed":manifest.len(),"total":page["total"]}));
                if result.as_ref().err().is_some_and(|e|e.to_string().contains("cancelada")||e.to_string().contains("30 segundos")||e.to_string().contains("Worker detenido")){terminal_error=result.err().map(|e|format!("{e:#}"));break 'batch;}
            }
            if offset>=total{break;}
        }
        let report=json!({"origin":info,"session_id":session_id,"complete":terminal_error.is_none()&&offset>=total,"processed":manifest.len(),"total":total,"terminal_error":terminal_error,"items":manifest});
        let mut file=fs::OpenOptions::new().write(true).create_new(true).open(destination.join("manifest.json")).map_err(|e|e.to_string())?;
        file.write_all(&serde_json::to_vec_pretty(&report).unwrap()).map_err(|e|e.to_string())?;file.sync_all().map_err(|e|e.to_string())?;
        Ok(json!({"path":destination,"items":manifest.len(),"complete":report["complete"],"warning":terminal_error}))
    }).await.map_err(|e|e.to_string())?
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let startup_path = args
        .windows(2)
        .find(|w| w[0] == "--open")
        .map(|w| w[1].clone());
    let report = args
        .windows(2)
        .find(|w| w[0] == "--smoke-report")
        .map(|w| PathBuf::from(&w[1]));
    let suite_report = args
        .windows(2)
        .find(|w| w[0] == "--suite-smoke-report")
        .map(|w| PathBuf::from(&w[1]));
    let builder = tauri::Builder::default()
        .setup(move |app| {
            let suite = SuiteHost::new(&std::env::current_exe()?, &app.path().resource_dir()?)?;
            if let Some(destination) = suite_report {
                let supervisor = suite.clone();
                std::thread::spawn(move || {
                    if let Err(error) = supervisor.smoke_report(&destination) {
                        eprintln!("No se pudo guardar el informe de la suite: {error:#}");
                    }
                });
            }
            app.manage(suite);
            let beside = std::env::current_exe()?
                .parent()
                .unwrap()
                .join("rustswf.exe");
            let worker_path = if beside.exists() {
                beside
            } else {
                app.path().resource_dir()?.join("binaries/rustswf.exe")
            };
            app.manage(Host(Arc::new(HostInner {
                worker: Mutex::new(None),
                session: Mutex::new(None),
                gate: Mutex::new(()),
                next: AtomicU64::new(1),
                cancel_epoch: AtomicU64::new(0),
                pending: AtomicUsize::new(0),
                batch_active: AtomicBool::new(false),
                worker_path,
                startup: startup_path,
                report,
            })));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            open_document,
            rpc,
            cancel,
            startup,
            ui_verified,
            verify_export,
            export_item,
            export_batch,
            suite::suite_rpc,
            suite::suite_pick,
            suite::suite_cancel
        ]);
    let application = builder
        .build(tauri::generate_context!())
        .expect("Error al iniciar Rust SWF Studio");
    application.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            app.state::<SuiteHost>().cancel();
            let host = app.state::<Host>();
            host.stop();
            if let Some(session) = host.0.session.lock().unwrap().take() {
                let _ = fs::remove_dir_all(session.cache);
            }
        }
    });
}

#[cfg(test)]
mod supervisor_tests {
    use super::*;
    fn host() -> Host {
        Host(Arc::new(HostInner {
            worker: Mutex::new(None),
            session: Mutex::new(None),
            gate: Mutex::new(()),
            next: AtomicU64::new(1),
            cancel_epoch: AtomicU64::new(0),
            pending: AtomicUsize::new(0),
            batch_active: AtomicBool::new(false),
            worker_path: PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/rustswf.exe"),
            startup: None,
            report: None,
        }))
    }
    fn fixture() -> PathBuf {
        let file =
            std::env::temp_dir().join(format!("rustswf-supervisor-{}.swf", uuid::Uuid::new_v4()));
        fs::write(
            &file,
            b"FWS\x06\x10\x00\x00\x00\x08\x00\x00\x18\x01\x00\x00\x00",
        )
        .unwrap();
        file
    }
    fn cleanup(host: &Host, file: &Path) {
        host.stop();
        if let Some(session) = host.0.session.lock().unwrap().take() {
            fs::remove_dir_all(session.cache).unwrap();
        }
        fs::remove_file(file).unwrap();
    }
    #[test]
    fn cancellation_rejects_stale_requests_and_recovers_original_snapshot() {
        let h = host();
        let file = fixture();
        let original = fs::read(&file).unwrap();
        let opened = h.open(file.clone(), 0).unwrap();
        let sid = opened["session_id"].as_str().unwrap();
        h.0.cancel_epoch.fetch_add(1, Ordering::SeqCst);
        h.stop();
        assert!(
            h.rpc(sid, "info", json!({}), 0)
                .unwrap_err()
                .to_string()
                .contains("cancelada")
        );
        assert!(h.0.worker.lock().unwrap().is_none());
        let reloaded = h.rpc(sid, "info", json!({}), 1).unwrap();
        assert_eq!(reloaded["info"]["sha256"], opened["info"]["sha256"]);
        assert_eq!(fs::read(&file).unwrap(), original);
        cleanup(&h, &file);
    }
    #[test]
    fn killed_worker_is_retired_then_reopened() {
        let h = host();
        let file = fixture();
        let opened = h.open(file.clone(), 0).unwrap();
        let sid = opened["session_id"].as_str().unwrap();
        {
            let worker = h.0.worker.lock().unwrap().as_ref().unwrap().clone();
            let mut child = worker.child.lock().unwrap();
            child.kill().unwrap();
            child.wait().unwrap();
        }
        assert!(h.rpc(sid, "info", json!({}), 0).is_err());
        assert!(h.0.worker.lock().unwrap().is_none());
        assert!(h.rpc(sid, "info", json!({}), 0).is_ok());
        cleanup(&h, &file);
    }
}
