import base64
import json
from pathlib import Path
import sys

import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.benchmark_worker import decompile
from py_swf.benchmarks import aggregate, build_manifest, match_units, reference_command
from py_swf.benchmark_process import Worker, WorkerError
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf, JPEXS_TESTDATA


def test_percentile_and_failures_are_not_hidden():
    samples = [{"ok": True, "wall_ms": n, "total_with_startup_ms": n+100, "peak_tree_rss_bytes": None} for n in range(1,31)] + [{"ok": False, "diagnostic": "timeout"}]
    result = aggregate(samples)
    assert result["attempted"] == 31 and result["successful"] == 30
    assert result["median_ms"] == 15.5 and result["p95_ms"] == 29
    assert result["peak_tree_rss_bytes"] is None


def test_worker_never_calls_source_available_behavior_verified():
    report = decompile(make_synthetic_swf([SWFTag(12, assemble_avm1('push 1\ntrace\nend'))]))
    unit = report["units"][0]
    assert unit["generation"] == "available"
    assert unit["behavior"] == unit["compilation"] == "not_run"
    assert unit["source_fidelity"] == "not_verified"


def test_matching_uses_identity_not_line_order_or_source_similarity():
    left = [{"document_id":"a", "bytecode_sha256":"123"}, {"document_id":"b", "bytecode_sha256":"456"}]
    match = match_units([{"engine":"native","units":left}, {"engine":"jpexs","units":list(reversed(left))}])
    assert match["matched"] == 2 and not match["bytecode_mismatches"]
    assert match["quality_equivalent"] is False


def test_manifest_keeps_compiler_variants_in_same_split(tmp_path):
    parent = tmp_path / "as3_new/bin"
    parent.mkdir(parents=True)
    paths = [parent / ("movie." + variant + ".swf") for variant in ("flex", "air")]
    for path in paths:
        path.write_bytes(make_synthetic_swf())
    rows = build_manifest(paths, tmp_path, 1729)["cases"]
    assert rows[0]["source_family"] == rows[1]["source_family"]
    assert rows[0]["split"] == rows[1]["split"]


def test_timeout_cleans_only_the_owned_worker():
    pytest.importorskip("psutil")
    command = [sys.executable, "-u", "-c", "import sys,time; time.sleep(10)"]
    with pytest.raises(WorkerError, match="timed out"):
        Worker(command, cwd=Path.cwd(), timeout=.1)




@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / '.dev-references/bridge/DevelopmentReferenceBridge.class').exists(), reason='Development reference toolchain not installed')
def test_reference_bridge_jsonl_source_compile_and_reopen():
    pytest.importorskip("psutil")
    root = Path(__file__).resolve().parents[1]
    data = make_synthetic_swf([SWFTag(12, assemble_avm1('push 1\ntrace\nend'))])
    payload = base64.b64encode(data).decode()
    with Worker(reference_command(), cwd=root) as worker:
        source = worker.request({"operation":"decompile", "swf_base64":payload, "include_source":True})
        assert source["ok"] and source["units"][0]["document_id"] == 'tag:0/actions'
        edited = worker.request({"operation":"compile", "swf_base64":payload, "document_id":"tag:0/actions", "source":'trace("changed");'})
        assert edited["ok"] and edited["compilation"] == 'passed' and edited["swf_reopened"]
        reopened = worker.request({"operation":"decompile", "swf_base64":edited["swf_base64"], "include_source":True})
        assert '"changed"' in reopened["units"][0]["source"]
        assert edited["behavior"] == 'not_run'


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / '.dev-references/bridge/DevelopmentReferenceBridge.class').exists(), reason='Development reference toolchain not installed')
def test_reference_as3_units_match_native_bytecode_identity():
    pytest.importorskip("psutil")
    from tests.native_fixtures import as3_tag
    root = Path(__file__).resolve().parents[1]
    data = make_synthetic_swf([as3_tag()])
    native = decompile(data)
    with Worker(reference_command(), cwd=root) as worker:
        payload = base64.b64encode(data).decode()
        java = worker.request({"operation":"decompile", "swf_base64":payload, "include_source":True})
        document = java["documents"][0]
        edited = worker.request({"operation":"compile", "swf_base64":payload, "document_id":document["document_id"], "source":document["source"].replace('"original"', '"edited"')})
        assert edited["ok"] and edited["swf_reopened"], edited
        reopened = worker.request({"operation":"decompile", "swf_base64":edited["swf_base64"], "include_source":True})
        assert '"edited"' in reopened["documents"][0]["source"]
    assert java["ok"], java
    matches = match_units([native, java])
    assert matches["scope_metrics"]["method"]["matched"] == sum(u.get('scope','method') == 'method' for u in native["units"])
    assert matches["bytecode_mismatches"] == []
