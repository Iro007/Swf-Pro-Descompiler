use anyhow::{Result, bail};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};
static SEQUENCE: AtomicU64 = AtomicU64::new(0);
struct Staged {
    temporary: PathBuf,
    destination: PathBuf,
}
impl Drop for Staged {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.temporary);
    }
}
fn stage_at(path: &Path, temporary: PathBuf, bytes: &[u8]) -> Result<Staged> {
    // If create_new fails there is no owned temporary to remove.
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    let staged = Staged {
        temporary,
        destination: path.to_owned(),
    };
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    Ok(staged)
}
fn stage(path: &Path, bytes: &[u8]) -> Result<Staged> {
    if path.exists() {
        bail!("El destino ya existe: {}", path.display());
    }
    let parent = path.parent().unwrap_or(Path::new("."));
    fs::create_dir_all(parent)?;
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_nanos();
    let name = format!(
        ".rustswf-{}-{nonce}-{}.tmp",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    );
    stage_at(path, parent.join(name), bytes)
}
impl Staged {
    fn publish(self) -> Result<()> {
        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;
            let from: Vec<u16> = self
                .temporary
                .as_os_str()
                .encode_wide()
                .chain(Some(0))
                .collect();
            let to: Vec<u16> = self
                .destination
                .as_os_str()
                .encode_wide()
                .chain(Some(0))
                .collect();
            // MoveFileW has no REPLACE_EXISTING behavior, including races.
            if unsafe {
                windows_sys::Win32::Storage::FileSystem::MoveFileW(from.as_ptr(), to.as_ptr())
            } == 0
            {
                return Err(std::io::Error::last_os_error().into());
            }
        }
        #[cfg(not(windows))]
        {
            fs::hard_link(&self.temporary, &self.destination)?;
        }
        Ok(())
    }
}
pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    stage(path, bytes)?.publish()
}
pub fn write_with_sidecar(
    path: &Path,
    bytes: &[u8],
    sidecar: &Path,
    metadata: &[u8],
) -> Result<Option<String>> {
    if path.exists() || sidecar.exists() {
        bail!("El archivo o su manifiesto ya existe; elija otro nombre");
    }
    let data = stage(path, bytes)?;
    let meta = stage(sidecar, metadata)?;
    data.publish()?;
    // A concurrently created sidecar must never be replaced. The valid source
    // remains published and its partial metadata result is explicitly reported.
    match meta.publish() {
        Ok(()) => Ok(None),
        Err(e) => Ok(Some(format!(
            "Fuente guardada, pero no se publicó su manifiesto: {e}"
        ))),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn folder() -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "rustswf-atomic-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        p
    }
    #[test]
    fn concurrent_writers_publish_exactly_one_without_replacing() {
        let dir = folder();
        let destination = dir.join("salida.bin");
        let handles: Vec<_> = (0..8)
            .map(|n| {
                let path = destination.clone();
                std::thread::spawn(move || atomic_write(&path, &[n]).is_ok())
            })
            .collect();
        assert_eq!(
            handles
                .into_iter()
                .map(|h| h.join().unwrap())
                .filter(|success| *success)
                .count(),
            1
        );
        assert_eq!(fs::read(&destination).unwrap().len(), 1);
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 1);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn existing_temporary_is_never_deleted() {
        let dir = folder();
        let temp = dir.join("ocupado.tmp");
        fs::write(&temp, b"keep").unwrap();
        assert!(stage_at(&dir.join("out"), temp.clone(), b"new").is_err());
        assert_eq!(fs::read(temp).unwrap(), b"keep");
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn existing_sidecar_blocks_source_publication() {
        let dir = folder();
        let source = dir.join("method.as");
        let meta = dir.join("method.as.json");
        fs::write(&meta, b"keep").unwrap();
        assert!(write_with_sidecar(&source, b"new", &meta, b"new").is_err());
        assert!(!source.exists());
        assert_eq!(fs::read(meta).unwrap(), b"keep");
        fs::remove_dir_all(dir).unwrap();
    }
}
