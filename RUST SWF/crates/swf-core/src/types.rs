use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    pub offset: Option<usize>,
}
impl Diagnostic {
    pub fn new(code: impl Into<String>, message: impl Into<String>, offset: Option<usize>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            offset,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DocumentInfo {
    pub filename: String,
    pub sha256: String,
    pub signature: String,
    pub version: u8,
    pub width: f64,
    pub height: f64,
    pub frame_rate: f64,
    pub frame_count: u16,
    pub tag_count: usize,
}

/// Offsets refer to `Movie::normalized` (FWS), including its eight-byte header.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TagRecord {
    pub id: String,
    pub path: Vec<usize>,
    pub code: u16,
    pub name: String,
    pub start: usize,
    pub data_start: usize,
    pub length: usize,
    pub character_id: Option<u16>,
    pub symbol: Option<String>,
    pub category: String,
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Clone, Debug)]
pub struct Asset {
    pub bytes: Vec<u8>,
    pub mime: String,
    pub extension: String,
    pub diagnostics: Vec<Diagnostic>,
}
