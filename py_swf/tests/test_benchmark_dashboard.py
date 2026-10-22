import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from server.app import app
from server.routers import benchmarks
from py_swf.benchmark_report import public_report, render_dashboard


@pytest.fixture
def report_root(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmarks, "REPORT_ROOT", tmp_path)
    monkeypatch.setattr(benchmarks, "BUNDLED", tmp_path / "missing.json")
    folder = tmp_path / "sample"
    folder.mkdir()
    report = {"schema_version": 1, "profile": "smoke", "created_at": "2026-10-12T00:00:00Z",
              "configuration": {}, "provenance": {}, "cases": [], "claims": {"parity": False}}
    (folder / "benchmark.json").write_text(json.dumps(report), encoding="utf-8")
    return tmp_path, report


def test_routes_are_read_only_and_reject_unknown_ids(report_root):
    client = TestClient(app)
    assert client.get("/api/benchmarks").json()["reports"][0]["id"] == "sample"
    page = client.get("/api/benchmarks/sample/dashboard")
    assert page.status_code == 200
    assert "No se ha establecido paridad" in page.text
    assert "frame-ancestors 'self'" in page.headers["content-security-policy"]
    assert client.get("/api/benchmarks/missing/dashboard").status_code == 404
    assert client.post("/api/benchmarks/sample/dashboard").status_code == 405


def test_bad_report_and_large_report_not_listed(report_root, monkeypatch):
    folder, _ = report_root
    (folder / "sample/benchmark.json").write_text("not json", encoding="utf-8")
    client = TestClient(app)
    assert client.get("/api/benchmarks").json() == {"reports": []}
    assert client.get("/api/benchmarks/sample/dashboard").status_code == 422
    (folder / "sample/benchmark.json").write_text('{"schema_version":1,"cases":[]}', encoding="utf-8")
    monkeypatch.setattr(benchmarks, "MAX_REPORT_BYTES", 10)
    assert client.get("/api/benchmarks").json() == {"reports": []}


def test_existing_api_auth_protects_benchmark_data(report_root, monkeypatch):
    monkeypatch.setenv("PY_SWF_API_TOKEN", "test-only-token")
    client = TestClient(app)
    assert client.get("/api/benchmarks/sample/dashboard").status_code == 401
    assert client.get("/api/benchmarks", headers={"X-PySWF-Token": "test-only-token"}).status_code == 200


def test_presentation_does_not_leak_source_or_absolute_paths(report_root):
    _, report = report_root
    report["cases"] = [{"file": "C:/private/user/movie.swf", "relative": '</script><script>bad()</script>',
                        "engines": [], "comparison": {}}]
    assert "file" not in public_report(report)["cases"][0]
    page = render_dashboard(report)
    assert "C:/private/user/" not in page
    assert "<script>bad()</script>" not in page
    assert "Métrica del gráfico" in page
