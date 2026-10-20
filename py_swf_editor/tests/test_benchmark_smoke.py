"""The smoke profile must select real, valid inputs from the repository corpus."""
from pathlib import Path

from py_swf.avm1 import assemble_avm1
from py_swf.benchmarks import REPOSITORY_SMOKE_FILES, collect_paths, readable_swf
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf

CORPUS = Path(__file__).resolve().parents[2] / "benchmark_inputs"


def test_every_repository_smoke_id_exists_and_is_valid():
    for relative in REPOSITORY_SMOKE_FILES:
        path = CORPUS / relative
        assert path.is_file(), f"smoke id missing from benchmark_inputs: {relative}"
        assert readable_swf(path.read_bytes()), relative


def test_repository_corpus_smoke_selection_is_the_fixed_list():
    selected = collect_paths(CORPUS, "smoke")
    assert [p.relative_to(CORPUS).as_posix() for p in selected] == list(REPOSITORY_SMOKE_FILES)


def test_smoke_fallback_skips_invalid_swfs(tmp_path):
    (tmp_path / "a_broken.swf").write_bytes(b"BAD!")
    good = make_synthetic_swf([SWFTag(12, assemble_avm1("push 1\ntrace\nend"))])
    (tmp_path / "b_good.swf").write_bytes(good)
    assert collect_paths(tmp_path, "smoke") == [tmp_path / "b_good.swf"]
