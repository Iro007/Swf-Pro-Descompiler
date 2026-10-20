use sha2::{Digest, Sha256};
use swf_core::read_verified_swc_library;
fn archive(library: &[u8], duplicate: bool, oversize: bool) -> Vec<u8> {
    let name = b"library.swf";
    let mut crc = flate2::Crc::new();
    crc.update(library);
    let crc = crc.sum();
    let mut zip = vec![];
    let mut central = vec![];
    for _ in 0..if duplicate { 2 } else { 1 } {
        let offset = zip.len() as u32;
        zip.extend([0x50, 0x4b, 0x03, 0x04]);
        zip.extend(20u16.to_le_bytes());
        zip.extend([0; 8]);
        zip.extend(crc.to_le_bytes());
        zip.extend((library.len() as u32).to_le_bytes());
        zip.extend((library.len() as u32).to_le_bytes());
        zip.extend((name.len() as u16).to_le_bytes());
        zip.extend(0u16.to_le_bytes());
        zip.extend(name);
        zip.extend(library);
        central.extend([0x50, 0x4b, 0x01, 0x02]);
        central.extend([20, 0, 20, 0]);
        central.extend([0; 8]);
        central.extend(crc.to_le_bytes());
        central.extend((library.len() as u32).to_le_bytes());
        central.extend(
            (if oversize {
                33 * 1024 * 1024
            } else {
                library.len() as u32
            })
            .to_le_bytes(),
        );
        central.extend((name.len() as u16).to_le_bytes());
        central.extend([0; 12]);
        central.extend(offset.to_le_bytes());
        central.extend(name);
    }
    let start = zip.len() as u32;
    let size = central.len() as u32;
    zip.extend(central);
    zip.extend([0x50, 0x4b, 0x05, 0x06]);
    zip.extend([0; 4]);
    let count = if duplicate { 2u16 } else { 1 };
    zip.extend(count.to_le_bytes());
    zip.extend(count.to_le_bytes());
    zip.extend(size.to_le_bytes());
    zip.extend(start.to_le_bytes());
    zip.extend([0; 2]);
    zip
}
fn path(label: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!("rustswf-swc-{}-{label}.swc", std::process::id()))
}
fn raw_movie() -> Vec<u8> {
    let mut b = b"FWS\x0d\0\0\0\0".to_vec();
    b.extend([8, 0, 0, 24, 1, 0, 0, 0]);
    let len = b.len() as u32;
    b[4..8].copy_from_slice(&len.to_le_bytes());
    b
}
#[test]
fn verified_stored_library_is_read_without_extraction() {
    let zip = archive(&raw_movie(), false, false);
    let p = path("ok");
    std::fs::write(&p, &zip).unwrap();
    let sha = format!("{:x}", Sha256::digest(&zip));
    let r = read_verified_swc_library(&p, &sha).unwrap();
    assert_eq!(r.source_sha256, sha);
    assert_eq!(r.movie.info.signature, "FWS");
    assert!(read_verified_swc_library(&p, &"0".repeat(64)).is_err());
}
#[test]
fn duplicates_and_oversized_members_are_rejected() {
    for (label, duplicate, oversize) in [("duplicate", true, false), ("oversize", false, true)] {
        let zip = archive(&raw_movie(), duplicate, oversize);
        let p = path(label);
        std::fs::write(&p, &zip).unwrap();
        let sha = format!("{:x}", Sha256::digest(&zip));
        assert!(read_verified_swc_library(&p, &sha).is_err());
    }
}
