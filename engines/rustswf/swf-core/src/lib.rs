mod container;
mod header;
mod resources;
mod swc;
mod types;
pub use swc::{VerifiedSwcLibrary, read_verified_swc_library};

pub use container::{ContainerCompression, MAX_INPUT_BYTES, MAX_NORMALIZED_BYTES, Movie};
pub use header::{FileAttributes, FrameSize, ScriptLimits, SwfHeader};
pub use resources::{export_resource, resource_metadata, supported_formats};
pub use types::{Asset, Diagnostic, DocumentInfo, TagRecord};
