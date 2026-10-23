import json

from py_swf.bench_results import read_bench


def test_reads_latest_run_and_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASH_BENCH_DIR", str(tmp_path))
    assert read_bench()["results"] is None
    for run in ("a", "b"):
        (tmp_path / run).mkdir()
        (tmp_path / run / "results.json").write_text(json.dumps({"run_id": run}), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps([{"run_id": "a"}, {"run_id": "b"}]), encoding="utf-8")
    assert read_bench()["results"]["run_id"] == "b" and read_bench("a")["results"]["run_id"] == "a"
    try:
        read_bench("../x")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must be rejected")
