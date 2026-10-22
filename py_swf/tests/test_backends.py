from pathlib import Path

from fastapi.testclient import TestClient

from py_swf.decompile.backends import JPEXSBackend, decompile_with_fallback
from py_swf.swf_parser import SWFTag
from server.app import app
from tests.conftest import make_synthetic_swf
from tests.test_api import _doaction_tag


client = TestClient(app)


def test_native_backend_is_always_available():
    response = client.get("/api/files/engines/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["default"] == "native"
    assert any(item["name"] == "native" and item["available"] for item in payload["backends"])


def test_auto_decompile_reports_native_when_jpexs_is_not_configured(monkeypatch):
    monkeypatch.delenv("PY_SWF_FFDEC", raising=False)
    monkeypatch.delenv("PY_SWF_FFDEC_JAR", raising=False)
    tag = _doaction_tag()
    result = decompile_with_fallback(tag, make_synthetic_swf([tag]), "test.swf")
    assert result.engine == "native"
    assert result.kind == "avm1"
    assert 'trace("hola")' in result.sections[0]["source"]


def test_explicit_jpexs_without_configuration_is_clear_error(monkeypatch):
    monkeypatch.delenv("PY_SWF_FFDEC", raising=False)
    monkeypatch.delenv("PY_SWF_FFDEC_JAR", raising=False)
    backend = JPEXSBackend(bridge=Path("does-not-exist.py"))
    assert backend.available is False
    try:
        backend.decompile(SWFTag(12, b"\x00"), b"FWS", "test.swf")
    except RuntimeError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("expected an unavailable JPEXS backend to fail")


def test_persistent_jsonl_bridge_roundtrip(tmp_path, monkeypatch):
    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    print(json.dumps({'request_id': request.get('request_id'), 'ok': True, 'kind': 'fake', 'sections': []}), flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PY_SWF_FFDEC", "fake")
    backend = JPEXSBackend(bridge=bridge, timeout=2)
    tag = SWFTag(12, b"\x00")
    try:
        first = backend.decompile(tag, b"FWS", "one.swf")
        second = backend.decompile(tag, b"FWS", "two.swf")
        assert first.engine == second.engine == "jpexs"
        assert first.kind == "fake"
    finally:
        backend.close()


def test_decompile_endpoint_accepts_engine_native():
    swf_bytes = make_synthetic_swf([_doaction_tag()])
    upload = client.post("/api/files", files={"file": ("test.swf", swf_bytes)})
    sid = upload.json()["session_id"]
    response = client.get(f"/api/files/{sid}/tags/0/decompile?engine=native")
    assert response.status_code == 200
    assert response.json()["engine"] == "native"


def test_decompile_endpoint_rejects_unknown_engine():
    swf_bytes = make_synthetic_swf([_doaction_tag()])
    sid = client.post("/api/files", files={"file": ("test.swf", swf_bytes)}).json()["session_id"]
    response = client.get(f"/api/files/{sid}/tags/0/decompile?engine=other")
    assert response.status_code == 422
