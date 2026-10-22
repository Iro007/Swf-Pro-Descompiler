use crate::{Diagnostic, DocumentInfo, TagRecord};
use anyhow::{Context, Result, bail, ensure};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    io::{self, Read, Write},
    path::Path,
};

pub const MAX_INPUT_BYTES: usize = 100 * 1024 * 1024;
pub const MAX_NORMALIZED_BYTES: usize = 256 * 1024 * 1024;
const MAX_DICTIONARY_BYTES: usize = 64 * 1024 * 1024;
const MAX_TAGS: usize = 100_000;
const MAX_DEPTH: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ContainerCompression {
    Original,
    Fws,
    Cws,
    Zws,
}

impl ContainerCompression {
    pub fn parse(name: &str) -> Result<Self> {
        match name.to_ascii_lowercase().as_str() {
            "original" => Ok(Self::Original),
            "fws" => Ok(Self::Fws),
            "cws" => Ok(Self::Cws),
            "zws" => Ok(Self::Zws),
            _ => bail!("Expected original, fws, cws or zws compression"),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Movie {
    pub info: DocumentInfo,
    pub normalized: Vec<u8>,
    pub original: Vec<u8>,
    pub tags: Vec<TagRecord>,
    pub diagnostics: Vec<Diagnostic>,
}

pub(crate) struct LimitedWriter {
    pub data: Vec<u8>,
    pub limit: usize,
}
impl Write for LimitedWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        if buf.len() > self.limit.saturating_sub(self.data.len()) {
            return Err(io::Error::other("Decompressed data exceeds limit"));
        }
        self.data.extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl Movie {
    /// Re-encode only the SWF container. Tag bytes, ABC metadata and nested offsets
    /// remain byte-for-byte identical in the normalized FWS image.
    pub fn encoded(&self, compression: ContainerCompression) -> Result<Vec<u8>> {
        let mut output = match compression {
            ContainerCompression::Original => self.original.clone(),
            ContainerCompression::Fws => self.normalized.clone(),
            ContainerCompression::Cws => {
                ensure!(
                    self.info.version >= 6,
                    "CWS requires SWF version 6; the writer never changes version semantics"
                );
                let sink = LimitedWriter {
                    data: Vec::new(),
                    limit: MAX_INPUT_BYTES - 8,
                };
                let mut encoder =
                    flate2::write::ZlibEncoder::new(sink, flate2::Compression::default());
                encoder.write_all(&self.normalized[8..])?;
                let sink = encoder.finish()?;
                let mut output = self.normalized[..8].to_vec();
                output[..3].copy_from_slice(b"CWS");
                output.extend(sink.data);
                output
            }
            ContainerCompression::Zws => {
                ensure!(
                    self.info.version >= 13,
                    "ZWS requires SWF version 13; the writer never changes version semantics"
                );
                let mut compressed = LimitedWriter {
                    data: Vec::new(),
                    limit: MAX_INPUT_BYTES - 4,
                };
                lzma_rs::lzma_compress(
                    &mut io::Cursor::new(&self.normalized[8..]),
                    &mut compressed,
                )?;
                ensure!(compressed.data.len() >= 13, "Invalid LZMA encoder header");
                let payload_len = compressed.data.len() - 13;
                let mut output = self.normalized[..8].to_vec();
                output[..3].copy_from_slice(b"ZWS");
                output.extend_from_slice(&(payload_len as u32).to_le_bytes());
                output.extend_from_slice(&compressed.data[..5]);
                output.extend_from_slice(&compressed.data[13..]);
                output
            }
        };
        ensure!(
            output.len() <= MAX_INPUT_BYTES,
            "Encoded SWF exceeds the reader's 100 MiB input budget"
        );
        let reloaded = Self::from_bytes(&output, &self.info.filename)?;
        ensure!(
            reloaded.normalized == self.normalized,
            "Container writer changed normalized SWF bytes"
        );
        output.shrink_to_fit();
        Ok(output)
    }
    pub fn read(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let mut file =
            std::fs::File::open(path).with_context(|| format!("Cannot open {}", path.display()))?;
        ensure!(
            file.metadata()?.len() <= MAX_INPUT_BYTES as u64,
            "SWF exceeds 100 MiB input limit"
        );
        let mut bytes = Vec::new();
        (&mut file)
            .take(MAX_INPUT_BYTES as u64 + 1)
            .read_to_end(&mut bytes)?;
        Self::from_bytes(
            &bytes,
            &path.file_name().unwrap_or_default().to_string_lossy(),
        )
    }

    pub fn from_bytes(input: &[u8], filename: &str) -> Result<Self> {
        ensure!(
            input.len() <= MAX_INPUT_BYTES,
            "SWF exceeds 100 MiB input limit"
        );
        ensure!(input.len() >= 8, "Truncated SWF header");
        let signature = std::str::from_utf8(&input[..3]).context("Invalid SWF signature")?;
        let version = input[3];
        ensure!(version != 0, "SWF version 0 is invalid");
        let declared = u32::from_le_bytes(input[4..8].try_into()?) as usize;
        ensure!(
            (12..=MAX_NORMALIZED_BYTES).contains(&declared),
            "Invalid or oversized declared SWF length"
        );
        let expected = declared - 8;
        let body = match signature {
            "FWS" => {
                ensure!(
                    input.len() == declared,
                    "FWS length differs from declared length"
                );
                input[8..].to_vec()
            }
            "CWS" => {
                ensure!(version >= 6, "CWS requires SWF version 6 or newer");
                let mut decoder = flate2::read::ZlibDecoder::new(&input[8..]);
                let mut body = Vec::new();
                decoder
                    .by_ref()
                    .take(expected as u64 + 1)
                    .read_to_end(&mut body)
                    .context("Invalid CWS zlib stream")?;
                ensure!(
                    body.len() == expected,
                    "CWS expanded length differs from declared length"
                );
                ensure!(
                    decoder.total_in() as usize == input.len() - 8,
                    "Trailing bytes after CWS zlib stream"
                );
                body
            }
            "ZWS" => {
                ensure!(version >= 13, "ZWS requires SWF version 13 or newer");
                ensure!(input.len() >= 17, "Truncated ZWS properties");
                let compressed = u32::from_le_bytes(input[8..12].try_into()?) as usize;
                ensure!(
                    compressed == input.len() - 17,
                    "ZWS compressed length differs from payload length"
                );
                ensure!(input[12] < 225, "Invalid LZMA properties");
                let dictionary = u32::from_le_bytes(input[13..17].try_into()?) as usize;
                ensure!(
                    dictionary <= MAX_DICTIONARY_BYTES,
                    "LZMA dictionary exceeds 64 MiB"
                );
                let mut output = LimitedWriter {
                    data: Vec::new(),
                    limit: expected,
                };
                let options = lzma_rs::decompress::Options {
                    unpacked_size: lzma_rs::decompress::UnpackedSize::UseProvided(None),
                    allow_incomplete: false,
                    memlimit: Some(MAX_DICTIONARY_BYTES),
                };
                let mut stream = io::BufReader::new(&input[12..]);
                lzma_rs::lzma_decompress_with_options(&mut stream, &mut output, &options)
                    .context("Invalid ZWS LZMA stream")?;
                ensure!(
                    output.data.len() == expected,
                    "ZWS expanded length differs from declared length"
                );
                output.data
            }
            _ => bail!("Expected FWS, CWS or ZWS signature"),
        };
        let mut normalized = input[..8].to_vec();
        normalized[..3].copy_from_slice(b"FWS");
        normalized.extend_from_slice(&body);
        let mut bits = Bits::new(&normalized[8..]);
        let n = bits.unsigned(5)? as usize;
        let x0 = bits.signed(n)?;
        let x1 = bits.signed(n)?;
        let y0 = bits.signed(n)?;
        let y1 = bits.signed(n)?;
        ensure!(x1 >= x0 && y1 >= y0, "Invalid SWF stage rectangle");
        let header_end = 8 + bits.byte_len();
        ensure!(
            header_end + 4 <= normalized.len(),
            "Truncated frame metadata"
        );
        let frame_rate =
            u16::from_le_bytes(normalized[header_end..header_end + 2].try_into()?) as f64 / 256.0;
        let frame_count =
            u16::from_le_bytes(normalized[header_end + 2..header_end + 4].try_into()?);
        let mut tags = Vec::new();
        let mut diagnostics = Vec::new();
        scan(
            &normalized,
            header_end + 4,
            normalized.len(),
            &[],
            &mut tags,
            &mut diagnostics,
        )?;
        let mut symbols = HashMap::new();
        for tag in &tags {
            if matches!(tag.code, 56 | 76) {
                let data = &normalized[tag.data_start..tag.data_start + tag.length];
                if let Err(error) = read_symbols(data, &mut symbols) {
                    diagnostics.push(Diagnostic::new(
                        "symbol_table",
                        error.to_string(),
                        Some(tag.data_start),
                    ));
                }
            }
        }
        for tag in &mut tags {
            tag.symbol = tag.character_id.and_then(|id| symbols.get(&id).cloned());
        }
        let info = DocumentInfo {
            filename: filename.into(),
            sha256: format!("{:x}", Sha256::digest(input)),
            signature: signature.into(),
            version,
            width: (i64::from(x1) - i64::from(x0)) as f64 / 20.0,
            height: (i64::from(y1) - i64::from(y0)) as f64 / 20.0,
            frame_rate,
            frame_count,
            tag_count: tags.len(),
        };
        Ok(Self {
            info,
            normalized,
            original: input.to_vec(),
            tags,
            diagnostics,
        })
    }

    pub fn tag_data<'a>(&'a self, tag: &TagRecord) -> &'a [u8] {
        self.normalized
            .get(tag.data_start..tag.data_start.saturating_add(tag.length))
            .unwrap_or(&[])
    }

    pub(crate) fn parsed<'a>(&'a self, tag: &TagRecord) -> Result<swf::Tag<'a>> {
        ensure!(tag.code != 39, "Sprite uses the bounded raw inventory");
        let end = tag
            .data_start
            .checked_add(tag.length)
            .context("Tag range overflow")?;
        let bytes = self
            .normalized
            .get(tag.start..end)
            .context("Invalid tag range")?;
        swf::read::Reader::new(bytes, self.info.version)
            .read_tag()
            .map_err(|error| anyhow::anyhow!("Ruffle tag parser: {error}"))
    }
}

fn scan(
    data: &[u8],
    mut pos: usize,
    end: usize,
    parent: &[usize],
    tags: &mut Vec<TagRecord>,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<()> {
    ensure!(parent.len() < MAX_DEPTH, "Nested sprite depth exceeds 64");
    let mut index = 0;
    let mut ended = false;
    while pos < end {
        ensure!(tags.len() < MAX_TAGS, "SWF exceeds 100000 tags");
        ensure!(pos + 2 <= end, "Truncated tag header at offset {pos}");
        let start = pos;
        let header = u16::from_le_bytes(data[pos..pos + 2].try_into()?);
        pos += 2;
        let code = header >> 6;
        let mut length = (header & 63) as usize;
        if length == 63 {
            ensure!(
                pos + 4 <= end,
                "Truncated long tag header at offset {start}"
            );
            length = u32::from_le_bytes(data[pos..pos + 4].try_into()?) as usize;
            pos += 4;
        }
        let tag_end = pos.checked_add(length).context("Tag length overflow")?;
        ensure!(
            tag_end <= end,
            "Tag {code} exceeds its container at offset {start}"
        );
        let mut path = parent.to_vec();
        path.push(index);
        let mut notes = Vec::new();
        if swf::TagCode::from_u16(code).is_none() {
            notes.push(Diagnostic::new(
                "unknown_tag",
                format!("Unknown tag {code}; bytes preserved"),
                Some(start),
            ));
        }
        let character_id = if defines_character(code) && length >= 2 {
            Some(u16::from_le_bytes(data[pos..pos + 2].try_into()?))
        } else {
            None
        };
        let record = TagRecord {
            id: format!(
                "tag:{}",
                path.iter()
                    .map(usize::to_string)
                    .collect::<Vec<_>>()
                    .join("/")
            ),
            path: path.clone(),
            code,
            name: tag_name(code),
            start,
            data_start: pos,
            length,
            character_id,
            symbol: None,
            category: category(code).into(),
            diagnostics: notes,
        };
        tags.push(record);
        if code == 39 {
            ensure!(length >= 4, "Truncated sprite at offset {pos}");
            scan(data, pos + 4, tag_end, &path, tags, diagnostics)?;
        }
        if code == 0 {
            ensure!(length == 0, "End tag cannot have a payload");
            ended = true;
            if tag_end < end {
                diagnostics.push(Diagnostic::new(
                    "trailing_after_end",
                    format!("{} bytes after End are retained", end - tag_end),
                    Some(tag_end),
                ));
            }
            break;
        }
        pos = tag_end;
        index += 1;
    }
    if !ended {
        diagnostics.push(Diagnostic::new(
            "missing_end",
            "Timeline has no End tag",
            Some(end),
        ));
    }
    Ok(())
}

fn read_symbols(data: &[u8], symbols: &mut HashMap<u16, String>) -> Result<()> {
    ensure!(data.len() >= 2, "Truncated symbol table");
    let count = u16::from_le_bytes(data[..2].try_into()?) as usize;
    let mut pos = 2;
    for _ in 0..count {
        ensure!(pos + 2 <= data.len(), "Truncated symbol ID");
        let id = u16::from_le_bytes(data[pos..pos + 2].try_into()?);
        pos += 2;
        let len = data[pos..]
            .iter()
            .position(|v| *v == 0)
            .context("Unterminated symbol name")?;
        symbols
            .entry(id)
            .or_insert_with(|| String::from_utf8_lossy(&data[pos..pos + len]).into_owned());
        pos += len + 1;
    }
    Ok(())
}
fn defines_character(code: u16) -> bool {
    matches!(
        code,
        2 | 6
            | 7
            | 10
            | 11
            | 14
            | 20
            | 21
            | 22
            | 32
            | 33
            | 34
            | 35
            | 36
            | 37
            | 39
            | 46
            | 48
            | 60
            | 75
            | 83
            | 84
            | 87
            | 90
            | 91
    )
}
fn category(code: u16) -> &'static str {
    match code {
        2 | 22 | 32 | 83 => "shapes",
        46 | 84 => "morphs",
        6 | 20 | 21 | 35 | 36 | 90 => "images",
        10 | 13 | 48 | 62 | 75 | 88 | 91 => "fonts",
        11 | 33 | 37 => "texts",
        14 | 15 | 18 | 19 | 45 | 89 => "sounds",
        7 | 34 => "buttons",
        39 => "sprites",
        12 | 59 | 72 | 82 => "scripts",
        60 | 61 => "videos",
        87 => "binary",
        0 | 1 | 4 | 5 | 26 | 28 | 43 | 70 | 94 => "frames",
        _ => "others",
    }
}
fn tag_name(code: u16) -> String {
    match code {
        72 => "DoABC1".into(),
        82 => "DoABC".into(),
        21 => "DefineBitsJPEG2".into(),
        35 => "DefineBitsJPEG3".into(),
        90 => "DefineBitsJPEG4".into(),
        _ => swf::TagCode::from_u16(code)
            .map(|v| format!("{v:?}"))
            .unwrap_or_else(|| format!("Unknown_{code}")),
    }
}

pub(crate) struct Bits<'a> {
    data: &'a [u8],
    pos: usize,
}
impl<'a> Bits<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self { data, pos: 0 }
    }
    pub fn unsigned(&mut self, n: usize) -> Result<u32> {
        ensure!(
            n <= 32 && self.pos + n <= self.data.len() * 8,
            "Truncated bit field"
        );
        let mut v = 0;
        for _ in 0..n {
            v = (v << 1) | u32::from((self.data[self.pos / 8] >> (7 - self.pos % 8)) & 1);
            self.pos += 1;
        }
        Ok(v)
    }
    pub fn signed(&mut self, n: usize) -> Result<i32> {
        let v = self.unsigned(n)?;
        Ok(if n == 0 {
            0
        } else {
            ((v << (32 - n)) as i32) >> (32 - n)
        })
    }
    pub fn byte_len(&self) -> usize {
        self.pos.div_ceil(8)
    }
}
