from py_swf.benchmarks import match_units


def test_rust_missing_identity_does_not_count_as_matching_bytecode():
    canonical = {"document_id": "tag:0/frame", "bytecode_sha256": "a" * 64}
    native = {"engine": "native", "units": [canonical]}
    rust = {"engine": "rust", "status": "output_unmapped", "units": [
        {**canonical, "generation": "failed", "identity_verified": False}
    ], "unmapped_units": [{"rust_document_id": "different"}]}
    comparison = match_units([native, rust])
    assert comparison["matched"] == 0
    assert comparison["native_only"] == ["tag:0/frame"]
    assert comparison["status"] == "partial_identity_mapping"
    assert comparison["unmapped_external_units"] == 1
    assert comparison["quality_equivalent"] is False


def test_three_engines_retain_both_comparisons_without_semantic_parity():
    canonical = {"document_id": "tag:0/frame", "bytecode_sha256": "a" * 64}
    native = {"engine": "native", "units": [canonical]}
    jpexs = {"engine": "jpexs", "units": [canonical]}
    rust = {"engine": "rust", "status": "measured", "units": [
        {**canonical, "identity_verified": True}
    ]}
    comparison = match_units([native, rust, jpexs])
    pairs = {p["engines"][1]: p for p in comparison["pairs"]}
    assert set(pairs) == {"rust", "jpexs"}
    assert all(p["matched"] == 1 for p in pairs.values())
    assert all(p["quality_equivalent"] is False for p in pairs.values())


def test_absent_comparator_is_unavailable_without_verified_matches():
    native = {"engine": "native", "units": [
        {"document_id": "tag:0/frame", "bytecode_sha256": "a" * 64}
    ]}
    jpexs = {"engine": "jpexs", "status": "not_configured", "units": []}
    comparison = match_units([native, jpexs])
    assert comparison["status"] == "comparison_unavailable"
    assert comparison["matched"] == 0
    assert comparison["quality_equivalent"] is False


def test_equal_location_with_different_bytecode_is_not_a_verified_match():
    native = {"engine": "native", "units": [
        {"document_id": "tag:0/frame", "bytecode_sha256": "a" * 64}
    ]}
    rust = {"engine": "rust", "status": "measured", "units": [
        {"document_id": "tag:0/frame", "bytecode_sha256": "b" * 64}
    ]}
    comparison = match_units([native, rust])
    assert comparison["status"] == "bytecode_mismatch"
    assert comparison["matched"] == 0
    assert comparison["matched_locations"] == 1
    assert comparison["bytecode_mismatches"] == ["tag:0/frame"]
