"""The damaged corpus is deterministic, says what it did to each file and matches what is committed."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zlib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_damaged_corpus as generator  # noqa: E402

from py_swf import paths  # noqa: E402
from py_swf.corpus import read_damaged_manifest  # noqa: E402
from py_swf.robustness import CODE_TAGS, decompressed, tag_spans  # noqa: E402

pytestmark = pytest.mark.skipif(not (paths.CORPUS / generator.SOURCE_ROOT / "manifest.json").is_file(), reason="sealed parity corpus unavailable")


@pytest.fixture(scope="module")
def built():
    return generator.build()


def test_the_prng_is_sha256_in_counter_mode_so_no_python_version_can_change_it():
    stream = generator.Stream(42, "case")
    assert stream.take(4).hex() == hashlib.sha256(b"42:case#0").digest()[:4].hex()
    one, two = generator.Stream(7), generator.Stream(7)
    assert [one.below(10) for _ in range(20)] == [two.below(10) for _ in range(20)]
    assert all(0 <= generator.Stream(seed).below(6) < 6 for seed in range(50))
    with pytest.raises(ValueError):
        generator.Stream(1).below(0)


def test_the_same_plan_and_seed_give_the_same_bytes_and_manifest(built):
    files, manifest = built
    again_files, again_manifest = generator.build()
    assert files == again_files
    assert generator.encode(manifest) == generator.encode(again_manifest)


def test_the_plan_has_between_thirty_and_sixty_small_cases_of_every_kind(built):
    files, manifest = built
    cases = manifest["cases"]
    assert 30 <= len(cases) <= 60 and len(files) == len(cases)
    assert {case["kind"] for case in cases} == set(generator.KINDS)
    assert len({case["id"] for case in cases}) == len(cases)
    assert max(len(data) for data in files.values()) < 16 * 1024, "small files"


def test_the_manifest_gives_source_kind_parameters_seed_and_sha256_of_every_case(built):
    files, manifest = built
    assert manifest["seed"] == generator.SEED and manifest["prng"] == "sha256-counter"
    root = paths.CORPUS / generator.SOURCE_ROOT
    for case in manifest["cases"]:
        assert {"id", "relative", "kind", "family_id", "language", "source", "source_sha256", "options", "params", "seed", "bytes", "sha256", "damage"} <= case.keys()
        source = (paths.CORPUS / case["source"]).read_bytes()
        assert hashlib.sha256(source).hexdigest() == case["source_sha256"]
        data = files[case["relative"]]
        assert hashlib.sha256(data).hexdigest() == case["sha256"] and len(data) == case["bytes"]
        assert data != source, "every case is damaged"
        assert case["damage"]["basis"] == generator.OFFSET_BASIS
        assert case["seed"] == int.from_bytes(hashlib.sha256(("%d:%s" % (generator.SEED, case["id"])).encode()).digest()[:8], "big")
    assert manifest["source_manifest_sha256"] == hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()


def by_kind(built, kind):
    files, manifest = built
    return [(case, files[case["relative"]], (paths.CORPUS / case["source"]).read_bytes()) for case in manifest["cases"] if case["kind"] == kind]


def test_truncations_cut_inside_the_header_inside_a_tag_and_inside_the_code(built):
    for case, data, source in by_kind(built, "truncate_header"):
        first = case["params"]["header_bytes"]
        assert 0 < len(data) < first and data == source[:len(data)]
    spans = lambda source: {path[0]: span for path, span in tag_spans(decompressed(source))[0].items() if len(path) == 1}  # noqa: E731
    for case, data, source in by_kind(built, "truncate_tag"):
        record, payload, end, code = spans(source)[case["params"]["tag_index"]]
        assert code not in CODE_TAGS and payload < len(data) < end and data == source[:len(data)]
    for case, data, source in by_kind(built, "truncate_code"):
        assert data == source[:len(data)] and case["params"]["cut"] == len(data)
        record, payload, end, code = spans(source)[next(i for i, span in spans(source).items() if span[3] in CODE_TAGS)]
        assert payload < len(data) < end, "the cut falls inside the code tag"
    for case, data, source in by_kind(built, "truncate_header") + by_kind(built, "truncate_tag") + by_kind(built, "truncate_code"):
        assert case["damage"]["ranges"] == [[len(data), len(source)]], "everything from the cut on is lost"


def test_a_wrong_length_changes_only_the_length_field_of_the_header(built):
    seen = set()
    for case, data, source in by_kind(built, "bad_length"):
        assert data[:4] == source[:4] and data[8:] == source[8:] and data[4:8] != source[4:8]
        assert int.from_bytes(data[4:8], "little") == case["params"]["declared"] != case["params"]["real"]
        assert case["damage"]["ranges"] == [[4, 8]]
        seen.add(case["params"]["length"])
    assert seen == {"larger", "smaller", "zero", "huge"}


def test_a_broken_compression_stream_only_happens_in_cws_files_and_never_recompresses(built):
    hows = set()
    for case, data, source in by_kind(built, "bad_compression"):
        assert source[:3] == b"CWS" and data[:8] == source[:8] and len(data) == len(source)
        hows.add(case["params"]["how"])
        if case["params"]["how"] == "bad_zlib_header":
            with pytest.raises(zlib.error):
                zlib.decompress(data[8:])
        if case["params"]["how"] == "flip_stream":
            assert sum(a != b for a, b in zip(data, source)) == len(case["params"]["offsets"])
            kept = case["params"]["intact_decompressed"]
            assert zlib.decompressobj().decompress(source[8:8 + (case["params"]["offsets"][0] - 8)])[:kept] == zlib.decompress(source[8:])[:kept]
            assert case["damage"]["ranges"][0][0] == 8 + kept
    assert hows == {"flip_stream", "bad_checksum", "bad_zlib_header"}


def test_a_wrong_tag_length_changes_the_length_field_of_one_record(built):
    for case, data, source in by_kind(built, "bad_tag_length"):
        params = case["params"]
        assert params["declared"] != params["length"] and len(data) == len(source)
        changed = [i for i, (a, b) in enumerate(zip(data, source)) if a != b]
        spans = tag_spans(decompressed(source))[0]
        record, payload, end, code = spans[(params["tag_index"],)]
        assert changed and all(record <= i < payload for i in changed), "only the record header changed"
        assert case["damage"]["ranges"] == [[record, len(source)]]


def test_flipped_bytes_stay_in_the_tag_they_were_aimed_at_and_are_all_listed(built):
    for kind, wants_code in (("flip_code", True), ("flip_noncode", False)):
        for case, data, source in by_kind(built, kind):
            spans = tag_spans(decompressed(source))[0]
            record, payload, end, code = spans[(case["params"]["tag_index"],)]
            changed = [i for i, (a, b) in enumerate(zip(data, source)) if a != b]
            assert changed == case["params"]["offsets"] and all(payload <= i < end for i in changed)
            assert (code in CODE_TAGS) == wants_code
            assert case["damage"]["ranges"] == generator.merged([[i, i + 1] for i in changed])


def test_what_is_committed_in_corpus_damaged_is_what_the_plan_generates_and_is_sealed():
    root = paths.CORPUS / "damaged"
    assert generator.check(root) == []
    manifest = read_damaged_manifest(root)
    assert manifest["corpus_id"] == generator.CORPUS_ID and len(manifest["cases"]) == len(generator.PLAN)


def test_a_damaged_file_or_manifest_that_changed_is_refused(tmp_path):
    root = tmp_path / "corpus" / "damaged"
    shutil.copytree(paths.CORPUS / "damaged", root)
    shutil.copytree(paths.CORPUS / generator.SOURCE_ROOT.split("/")[0], tmp_path / "corpus" / generator.SOURCE_ROOT.split("/")[0])
    assert read_damaged_manifest(root)["cases"]
    victim = next(root.glob("*.swf"))
    victim.write_bytes(victim.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="changed"):
        read_damaged_manifest(root)
    shutil.copyfile(paths.CORPUS / "damaged" / victim.name, victim)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["seed"] += 1
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest changed"):
        read_damaged_manifest(root)


def test_writing_removes_stale_files_and_check_reports_differences(tmp_path):
    out = tmp_path / "damaged"
    generator.write(out)
    (out / "stale.swf").write_bytes(b"FWS")
    first = next(out.glob("truncate-*.swf"))
    first.write_bytes(first.read_bytes() + b"x")
    problems = generator.check(out)
    assert "sobra stale.swf" in problems and "difiere " + first.name in problems
    generator.write(out)
    assert generator.check(out) == [] and not (out / "stale.swf").exists()


def test_a_header_cut_must_fall_inside_the_header():
    source = (paths.CORPUS / generator.SOURCE_ROOT / "swf/dev-as2-timeline/release.fws.swf").read_bytes()
    with pytest.raises(ValueError, match="inside the header"):
        generator.truncate_header(source, {"cut": 10 ** 6}, generator.Stream(1))
    assert len(generator.truncate_header(source, {"cut": "first-1"}, generator.Stream(1))[0]) == 18
