//! SWF header (signature, version, frame rectangle, rate, count) plus the
//! header-level tags JPEXS shows beside it: FileAttributes, SetBackgroundColor,
//! ScriptLimits and Metadata. Nothing here changes the movie.
//!
//! The JSON is the header every engine answers with (PROTOCOL.md, `header`): the
//! field names and the counting rules (`tag_count` and `shown_frames` are top-level
//! tags only) are PySWF's, so the single-window interface reads either engine's answer.
use crate::{Movie, container::Bits};
use anyhow::{Result, ensure};
use serde::Serialize;

const MAX_METADATA_CHARS: usize = 64 * 1024;

/// Stage rectangle in twips (1/20 px) and in pixels.
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct FrameSize {
    pub xmin: i32,
    pub xmax: i32,
    pub ymin: i32,
    pub ymax: i32,
    pub width: f64,
    pub height: f64,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct FileAttributes {
    pub use_network: bool,
    pub actionscript3: bool,
    pub has_metadata: bool,
    pub use_gpu: bool,
    pub use_direct_blit: bool,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct ScriptLimits {
    pub max_recursion_depth: u16,
    pub script_timeout_seconds: u16,
}

#[derive(Clone, Debug, Serialize)]
pub struct SwfHeader {
    pub filename: String,
    pub signature: String,
    /// `none` (FWS), `zlib` (CWS) or `lzma` (ZWS).
    pub compression: &'static str,
    pub version: u8,
    /// Uncompressed length declared by the header.
    pub file_length: u32,
    /// Bytes of the file as received.
    pub file_size: usize,
    pub frame_size: FrameSize,
    pub frame_rate: f64,
    pub frame_count: u16,
    /// ShowFrame tags of the main timeline.
    pub shown_frames: usize,
    /// Tags of the main timeline (sprite contents are not counted).
    pub tag_count: usize,
    /// DoABC and DoABC1 tags.
    pub abc_blocks: usize,
    /// FileAttributes says ActionScript 3, or the file carries ABC code.
    pub has_as3: bool,
    /// A Metadata tag exists or FileAttributes announces one.
    pub has_metadata: bool,
    pub file_attributes: Option<FileAttributes>,
    /// Text of the Metadata tag, up to 64 K characters.
    pub metadata: Option<String>,
    pub background_color: Option<String>,
    pub script_limits: Option<ScriptLimits>,
    pub sha256: String,
    /// The same hash: the revision of the bytes this answer describes.
    pub revision: String,
    /// Code units of the movie; the worker fills it in, the container does not know.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub script_count: Option<usize>,
}

impl Movie {
    /// Decode the header from the normalized (uncompressed) image.
    pub fn header(&self) -> Result<SwfHeader> {
        ensure!(self.normalized.len() >= 12, "Truncated SWF header");
        let mut bits = Bits::new(&self.normalized[8..]);
        let width_bits = bits.unsigned(5)? as usize;
        let xmin = bits.signed(width_bits)?;
        let xmax = bits.signed(width_bits)?;
        let ymin = bits.signed(width_bits)?;
        let ymax = bits.signed(width_bits)?;
        let compression = match self.info.signature.as_str() {
            "CWS" => "zlib",
            "ZWS" => "lzma",
            _ => "none",
        };
        let top_level = || self.tags.iter().filter(|tag| tag.path.len() == 1);
        let first = |code: u16| top_level().find(|tag| tag.code == code);
        let background_color = first(9).and_then(|tag| {
            let data = self.tag_data(tag);
            (data.len() >= 3).then(|| format!("#{:02x}{:02x}{:02x}", data[0], data[1], data[2]))
        });
        let flags = first(69).and_then(|tag| self.tag_data(tag).first().copied());
        let file_attributes = flags.map(|flags| FileAttributes {
            use_network: flags & 0x01 != 0,
            actionscript3: flags & 0x08 != 0,
            has_metadata: flags & 0x10 != 0,
            use_gpu: flags & 0x20 != 0,
            use_direct_blit: flags & 0x40 != 0,
        });
        let script_limits = first(65).and_then(|tag| {
            let data = self.tag_data(tag);
            (data.len() >= 4).then(|| ScriptLimits {
                max_recursion_depth: u16::from_le_bytes([data[0], data[1]]),
                script_timeout_seconds: u16::from_le_bytes([data[2], data[3]]),
            })
        });
        let metadata_tag = first(77);
        let metadata = metadata_tag.map(|tag| {
            let data = self.tag_data(tag);
            let end = data.iter().position(|b| *b == 0).unwrap_or(data.len());
            String::from_utf8_lossy(&data[..end])
                .chars()
                .take(MAX_METADATA_CHARS)
                .collect()
        });
        let abc_blocks = top_level().filter(|tag| matches!(tag.code, 72 | 82)).count();
        Ok(SwfHeader {
            filename: self.info.filename.clone(),
            signature: self.info.signature.clone(),
            compression,
            version: self.info.version,
            file_length: u32::from_le_bytes(self.normalized[4..8].try_into()?),
            file_size: self.original.len(),
            frame_size: FrameSize {
                xmin,
                xmax,
                ymin,
                ymax,
                width: self.info.width,
                height: self.info.height,
            },
            frame_rate: self.info.frame_rate,
            frame_count: self.info.frame_count,
            shown_frames: top_level().filter(|tag| tag.code == 1).count(),
            tag_count: top_level().count(),
            abc_blocks,
            has_as3: flags.is_some_and(|flags| flags & 0x08 != 0) || abc_blocks > 0,
            has_metadata: metadata_tag.is_some() || flags.is_some_and(|flags| flags & 0x10 != 0),
            file_attributes,
            metadata,
            background_color,
            script_limits,
            sha256: self.info.sha256.clone(),
            revision: self.info.sha256.clone(),
            script_count: None,
        })
    }
}
