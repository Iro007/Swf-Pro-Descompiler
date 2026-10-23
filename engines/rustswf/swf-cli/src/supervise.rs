//! `rustswf supervise [--memory-mib N] -- <program> [args...]`
//!
//! Runs a worker inside a Windows Job Object (kill-on-close and a memory cap
//! for the whole process tree) while the child keeps this process's stdin,
//! stdout and stderr. Node cannot create Job Objects, so the Electron harness
//! launches workers through this command: when the harness (or this process)
//! dies, the whole tree dies with it, and a runaway worker is stopped by the
//! operating system instead of exhausting memory. Same limits as the previous
//! desktop host.
use anyhow::{Context, Result, bail, ensure};
use std::process::{Command, Stdio};

const DEFAULT_MEMORY_MIB: usize = 1024;
const MIN_MEMORY_MIB: usize = 64;
const MAX_MEMORY_MIB: usize = 16 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub struct Plan {
    pub memory_mib: usize,
    pub program: String,
    pub args: Vec<String>,
}

pub fn parse(args: &[String]) -> Result<Plan> {
    let mut memory_mib = DEFAULT_MEMORY_MIB;
    let mut rest = args;
    while let Some((head, tail)) = rest.split_first() {
        match head.as_str() {
            "--memory-mib" => {
                let (value, after) = tail.split_first().context("Falta el valor de --memory-mib")?;
                memory_mib = value
                    .parse()
                    .with_context(|| format!("--memory-mib inválido: {value}"))?;
                ensure!(
                    (MIN_MEMORY_MIB..=MAX_MEMORY_MIB).contains(&memory_mib),
                    "--memory-mib debe estar entre {MIN_MEMORY_MIB} y {MAX_MEMORY_MIB}"
                );
                rest = after;
            }
            "--" => {
                rest = tail;
                break;
            }
            other => bail!("Opción desconocida: {other}"),
        }
    }
    let (program, args) = rest.split_first().context("Falta el programa a supervisar")?;
    Ok(Plan {
        memory_mib,
        program: program.clone(),
        args: args.to_vec(),
    })
}

struct Job(usize);
impl Job {
    fn create(memory_mib: usize) -> Result<Self> {
        use windows_sys::Win32::System::JobObjects::*;
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(std::io::Error::last_os_error().into());
        }
        let job = Self(handle as usize);
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        limits.BasicLimitInformation.LimitFlags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_JOB_MEMORY;
        limits.JobMemoryLimit = memory_mib * 1024 * 1024;
        ensure!(
            unsafe {
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &limits as *const _ as _,
                    std::mem::size_of_val(&limits) as u32,
                )
            } != 0,
            "No se pudo fijar el límite de memoria: {}",
            std::io::Error::last_os_error()
        );
        Ok(job)
    }
    fn assign(&self, child: &std::process::Child) -> Result<()> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;
        ensure!(
            unsafe { AssignProcessToJobObject(self.0 as _, child.as_raw_handle() as _) } != 0,
            "No se pudo aislar el árbol del worker: {}",
            std::io::Error::last_os_error()
        );
        Ok(())
    }
}
impl Drop for Job {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0 as _);
        }
    }
}

/// Exit code of the supervised program (or 127 when it cannot be started).
pub fn run(args: &[String]) -> Result<i32> {
    use std::os::windows::process::CommandExt;
    let plan = parse(args)?;
    let job = Job::create(plan.memory_mib)?;
    let mut child = Command::new(&plan.program)
        .args(&plan.args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .creation_flags(0x08000000)
        .spawn()
        .with_context(|| format!("No se pudo iniciar {}", plan.program))?;
    if let Err(error) = job.assign(&child) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    let status = child.wait()?;
    drop(job);
    Ok(status.code().unwrap_or(1))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn strings(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| (*s).to_owned()).collect()
    }

    #[test]
    fn parses_options_and_the_program_after_the_separator() {
        let plan = parse(&strings(&["--memory-mib", "512", "--", "node", "-e", "1"])).unwrap();
        assert_eq!(
            plan,
            Plan {
                memory_mib: 512,
                program: "node".into(),
                args: strings(&["-e", "1"])
            }
        );
        let plan = parse(&strings(&["--", "py.exe"])).unwrap();
        assert_eq!((plan.memory_mib, plan.program.as_str()), (1024, "py.exe"));
    }

    #[test]
    fn rejects_bad_limits_unknown_options_and_a_missing_program() {
        assert!(parse(&strings(&["--memory-mib", "5", "--", "x"])).is_err());
        assert!(parse(&strings(&["--memory-mib", "abc", "--", "x"])).is_err());
        assert!(parse(&strings(&["--memory-mib"])).is_err());
        assert!(parse(&strings(&["--bogus", "--", "x"])).is_err());
        assert!(parse(&strings(&["--memory-mib", "256", "--"])).is_err());
        assert!(parse(&[]).is_err());
    }
}
