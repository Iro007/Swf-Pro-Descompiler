import json

from tests.conftest import make_synthetic_swf
from tools.corpus_report import build_report, main


def test_corpus_report_records_parse_and_timing(tmp_path):
    path = tmp_path / "sample.swf"
    path.write_bytes(make_synthetic_swf())
    report = build_report(tmp_path)
    assert report["file_count"] == 1
    assert report["valid_count"] == 1
    assert report["files"][0]["tags"] >= 1
    assert report["files"][0]["elapsed_ms"] >= 0


def test_corpus_report_cli_writes_json(tmp_path):
    path = tmp_path / "sample.swf"
    path.write_bytes(make_synthetic_swf())
    output = tmp_path / "report.json"
    assert main([str(tmp_path), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["valid_count"] == 1
