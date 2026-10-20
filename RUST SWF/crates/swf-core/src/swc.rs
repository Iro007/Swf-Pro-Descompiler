//! Read-only, SHA-verified SWC library extraction. GPL-3.0-or-later.
//! Original bounded ZIP reader; only the single `library.swf` member is read.
use crate::Movie;
use anyhow::{Context, Result, bail, ensure};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};
const MAX_SWC_BYTES: usize = 64 * 1024 * 1024;
const MAX_LIBRARY_BYTES: usize = 32 * 1024 * 1024;
pub struct VerifiedSwcLibrary {
    pub source_path: PathBuf,
    pub source_sha256: String,
    pub library_sha256: String,
    pub movie: Movie,
}
fn u16le(data: &[u8], at: usize) -> Result<u16> {
    Ok(u16::from_le_bytes(
        data.get(at..at + 2)
            .context("Truncated SWC word")?
            .try_into()?,
    ))
}
fn u32le(data: &[u8], at: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(
        data.get(at..at + 4)
            .context("Truncated SWC dword")?
            .try_into()?,
    ))
}
pub fn read_verified_swc_library(path: &Path, expected_sha256: &str) -> Result<VerifiedSwcLibrary> {
    ensure!(
        expected_sha256.len() == 64 && expected_sha256.bytes().all(|c| c.is_ascii_hexdigit()),
        "Expected a complete SHA-256 contract"
    );
    let file = std::fs::File::open(path).context("Open hierarchy SWC")?;
    ensure!(
        file.metadata()?.len() <= MAX_SWC_BYTES as u64,
        "SWC exceeds 64 MiB"
    );
    let mut data = vec![];
    file.take((MAX_SWC_BYTES + 1) as u64)
        .read_to_end(&mut data)?;
    ensure!(
        data.len() <= MAX_SWC_BYTES,
        "SWC grew beyond its read budget"
    );
    let source_sha256 = format!("{:x}", Sha256::digest(&data));
    ensure!(
        source_sha256.eq_ignore_ascii_case(expected_sha256),
        "Hierarchy SWC SHA-256 mismatch"
    );
    let first = data.len().saturating_sub(65_557);
    let end = (first..data.len().saturating_sub(21))
        .rev()
        .find(|at| {
            data.get(*at..*at + 4) == Some(&[0x50, 0x4b, 0x05, 0x06])
                && u16le(&data, *at + 20)
                    .ok()
                    .is_some_and(|n| *at + 22 + usize::from(n) == data.len())
        })
        .context("SWC has no complete ZIP central directory")?;
    ensure!(
        u16le(&data, end + 4)? == 0 && u16le(&data, end + 6)? == 0,
        "Multi-disk SWC is unsupported"
    );
    let entries = usize::from(u16le(&data, end + 10)?);
    ensure!(
        entries != 0xffff && u16le(&data, end + 8)? as usize == entries,
        "ZIP64/multi-disk directory is unsupported"
    );
    let directory = u32le(&data, end + 16)? as usize;
    let directory_len = u32le(&data, end + 12)? as usize;
    let directory_end = directory
        .checked_add(directory_len)
        .context("SWC directory overflow")?;
    ensure!(directory_end <= end, "SWC directory is outside the file");
    let mut cursor = directory;
    let mut member = None;
    for _ in 0..entries {
        ensure!(
            data.get(cursor..cursor + 4) == Some(&[0x50, 0x4b, 0x01, 0x02]),
            "Invalid SWC central record"
        );
        let flags = u16le(&data, cursor + 8)?;
        let method = u16le(&data, cursor + 10)?;
        let expected_crc = u32le(&data, cursor + 16)?;
        let compressed = u32le(&data, cursor + 20)? as usize;
        let uncompressed = u32le(&data, cursor + 24)? as usize;
        let name_len = usize::from(u16le(&data, cursor + 28)?);
        let extra_len = usize::from(u16le(&data, cursor + 30)?);
        let comment_len = usize::from(u16le(&data, cursor + 32)?);
        ensure!(
            u16le(&data, cursor + 34)? == 0,
            "SWC entry starts on another disk"
        );
        let local = u32le(&data, cursor + 42)? as usize;
        let next = cursor
            .checked_add(46)
            .and_then(|n| n.checked_add(name_len))
            .and_then(|n| n.checked_add(extra_len))
            .and_then(|n| n.checked_add(comment_len))
            .context("SWC record overflow")?;
        ensure!(
            next <= directory_end,
            "SWC record exceeds its central directory"
        );
        if data.get(cursor + 46..cursor + 46 + name_len) == Some(b"library.swf") {
            ensure!(
                member.is_none(),
                "Duplicate library.swf providers are ambiguous"
            );
            ensure!(
                flags & 1 == 0 && matches!(method, 0 | 8),
                "Encrypted or unsupported SWC library compression"
            );
            ensure!(
                uncompressed <= MAX_LIBRARY_BYTES && compressed <= MAX_SWC_BYTES,
                "SWC library exceeds its budget"
            );
            ensure!(
                data.get(local..local + 4) == Some(&[0x50, 0x4b, 0x03, 0x04]),
                "SWC library local header is invalid"
            );
            ensure!(
                u16le(&data, local + 6)? == flags && u16le(&data, local + 8)? == method,
                "SWC library headers disagree"
            );
            let local_name = usize::from(u16le(&data, local + 26)?);
            let local_extra = usize::from(u16le(&data, local + 28)?);
            ensure!(
                data.get(local + 30..local + 30 + local_name) == Some(b"library.swf"),
                "SWC library local name differs"
            );
            let start = local
                .checked_add(30)
                .and_then(|n| n.checked_add(local_name))
                .and_then(|n| n.checked_add(local_extra))
                .context("SWC local header overflow")?;
            let finish = start
                .checked_add(compressed)
                .context("SWC member overflow")?;
            ensure!(finish <= directory, "SWC library overlaps its directory");
            let raw = data.get(start..finish).context("Truncated SWC library")?;
            let mut library = vec![];
            if method == 0 {
                ensure!(
                    compressed == uncompressed,
                    "Stored SWC library sizes disagree"
                );
                library.extend(raw);
            } else {
                flate2::read::DeflateDecoder::new(raw)
                    .take((MAX_LIBRARY_BYTES + 1) as u64)
                    .read_to_end(&mut library)
                    .context("Decode SWC library")?;
            }
            ensure!(
                library.len() == uncompressed && library.len() <= MAX_LIBRARY_BYTES,
                "Decoded SWC library length differs or exceeds budget"
            );
            let mut crc = flate2::Crc::new();
            crc.update(&library);
            ensure!(crc.sum() == expected_crc, "SWC library CRC-32 mismatch");
            member = Some(library);
        }
        cursor = next;
    }
    ensure!(
        cursor == directory_end,
        "Trailing SWC central-directory records are unsupported"
    );
    let library = member.context("SWC has no library.swf")?;
    let library_sha256 = format!("{:x}", Sha256::digest(&library));
    let movie = Movie::from_bytes(&library, "verified SWC library.swf")?;
    if !movie.diagnostics.is_empty() {
        bail!("SWC movie has parse diagnostics");
    }
    Ok(VerifiedSwcLibrary {
        source_path: path.canonicalize()?,
        source_sha256,
        library_sha256,
        movie,
    })
}
