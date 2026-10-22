import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from py_swf.corpus import corpus_root

JPEXS_TESTDATA = corpus_root(ROOT)


def pytest_addoption(parser):
    parser.addoption("--require-corpus", action="store_true", help="Fail rather than skip when the real SWF corpus is unavailable")


def pytest_sessionstart(session):
    if session.config.getoption("--require-corpus") and not JPEXS_TESTDATA.is_dir():
        raise pytest.UsageError("Real SWF corpus unavailable; set PY_SWF_CORPUS_ROOT")


def pytest_report_header(config):
    return "SWF corpus: " + str(JPEXS_TESTDATA) + (" (available)" if JPEXS_TESTDATA.is_dir() else " (unavailable; optional tests explicitly skipped)")

@pytest.fixture(scope="session")
def jpexs_testdata():
    """Local-only corpus of real SWFs (git-ignored, GPL — never vendored)."""
    if not JPEXS_TESTDATA.is_dir():
        pytest.skip("JPEXS testdata not available (third_party/ not cloned)")
    return JPEXS_TESTDATA

def make_synthetic_swf(extra_tags=(), signature="FWS"):
    """Minimal valid SWF (bytes) with the given extra SWFTag objects."""
    from py_swf.swf_parser import SWFFile

    swf = SWFFile()
    swf.signature = signature
    swf.version = 10
    swf.rect = {"xmin": 0, "xmax": 2000, "ymin": 0, "ymax": 2000}
    swf.frame_rate = 24.0
    swf.frame_count = 1
    swf.tags = list(extra_tags)
    return swf.save_bytes()
