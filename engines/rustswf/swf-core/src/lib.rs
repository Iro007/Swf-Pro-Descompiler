mod container;
mod resources;
mod swc;
mod types;
pub use swc::{VerifiedSwcLibrary, read_verified_swc_library};

pub use container::{ContainerCompression, MAX_INPUT_BYTES, MAX_NORMALIZED_BYTES, Movie};
pub use resources::{export_resource, resource_metadata, supported_formats};
pub use types::{Asset, Diagnostic, DocumentInfo, TagRecord};
