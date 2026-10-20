import json
from fastapi.testclient import TestClient

from py_swf.benchmark_overview import build_overview, render_overview
from server.app import app
from server.routers import benchmarks


def sample(*,kind="source",profile="smoke",date="2026-10-12T00:00:00Z",unchanged=True):
    return {"schema_version":1,"created_at":date,"profile":profile,"operation":kind,
            "provenance":{"implementation_unchanged":unchanged},"cases":[{"file":"C:/private/input.swf","relative":"sample.swf","sha256":"abc","engines":[{"engine":"native"}]}],
            "engine_catalog":[{"id":"native","name":"Python nativo","operations":["source"],"languages":["as3"],"category":"decompiler"}]}


def test_same_observations_deduplicate_release_copy_but_keep_distinct_profiles():
    a=sample()
    overview=build_overview([("original",a),("release",a),("full",sample(profile="full",date="2026-10-12T01:00:00Z"))])
    assert len(overview["campaigns"])==2
    assert overview["selected"]["source"]=="full"
    assert "C:/private/" not in json.dumps(overview)
    assert overview["claims"]["performance_winner"] is None


def test_source_audit_does_not_become_timing_profile_and_mixed_runs_not_default():
    audited=sample(profile="source_audit",date="2026-10-12T01:00:00Z")
    audited["measurement_context"]={"kind":"source_audit","suitable_for_comparison":False}
    overview=build_overview([("old",sample()),("audit",audited),("mixed",sample(date="2026-10-12T02:00:00Z",unchanged=False))])
    assert overview["selected"]["source"]=="audit"
    assert overview["warnings"]
    assert overview["campaigns"][1]["report"]["profile"]=="source_audit"


def test_in_progress_report_is_not_selected_as_completed_campaign():
    pending=sample(date="2026-10-12T03:00:00Z")
    pending["provenance"].pop("implementation_unchanged")
    overview=build_overview([("complete",sample()),("running",pending)])
    assert overview["selected"]["source"]=="complete"


def test_empty_overview_lists_candidates_but_no_fake_results():
    overview=build_overview([])
    assert len(overview["engine_catalog"])>=13 and not overview["campaigns"]
    assert not overview["selected"]
    page=render_overview(overview)
    assert "Comparación de todas las herramientas" in page


def test_overview_routes_auth_and_literal_script_escape(tmp_path,monkeypatch):
    monkeypatch.setattr(benchmarks,"REPORT_ROOT",tmp_path)
    monkeypatch.setattr(benchmarks,"BUNDLED",tmp_path / "missing.json")
    folder=tmp_path / "sample"
    folder.mkdir()
    report=sample()
    report["cases"][0]["relative"]="</script><script>bad()</script>"
    (folder / "benchmark.json").write_text(json.dumps(report))
    client=TestClient(app)
    page=client.get("/api/benchmarks/overview/dashboard")
    assert page.status_code==200 and "<script>bad()</script>" not in page.text
    assert "frame-ancestors 'self'" in page.headers["content-security-policy"]
    summary=client.get("/api/benchmarks/overview/summary").json()
    assert summary["kind"]=="comparison_overview"
    assert "file" not in summary["campaigns"][0]["report"]["cases"][0]
    monkeypatch.setenv("PY_SWF_API_TOKEN","test-only")
    assert client.get("/api/benchmarks/overview/dashboard").status_code==401
