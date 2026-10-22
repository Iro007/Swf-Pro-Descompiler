use sha2::{Digest, Sha256};
use swf_avm::inventory;
use swf_core::Movie;

#[test]
fn matched_real_avm1_functions_export_the_actual_captured_pool_digest() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../benchmark_inputs/cloud-matched-micro-20261013");
    for (name, expected_functions) in [
        ("timeline_function_def", 2),
        ("closure_scope", 2),
        ("try_catch_finally", 8),
    ] {
        let path = root.join(format!("ruffle_avm1_{name}.swf"));
        // Corpus inputs are local benchmark evidence, not vendored artifacts.
        if !path.is_file() {
            eprintln!("Matched corpus unavailable: {}", path.display());
            return;
        }
        let bytes = std::fs::read(&path).unwrap();
        let movie = Movie::from_bytes(&bytes, "matched-corpus.swf").unwrap();
        let units = inventory(&movie);
        let functions = units
            .iter()
            .filter(|u| u.kind == "function")
            .collect::<Vec<_>>();
        assert_eq!(functions.len(), expected_functions, "{name}");
        for unit in functions {
            let digest = format!(
                "{:x}",
                Sha256::digest(serde_json::to_vec(&unit.avm1_constants).unwrap())
            );
            assert_eq!(unit.avm1_constants_sha256.as_deref(), Some(digest.as_str()));
            let serialized = serde_json::to_value(unit).unwrap();
            assert_eq!(serialized["avm1_constants_sha256"], digest);
            assert_eq!(
                &movie.normalized[unit.byte_start..unit.byte_start + unit.byte_len],
                unit.code
            );
        }
    }
}
