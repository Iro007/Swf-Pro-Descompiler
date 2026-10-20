import hashlib
import json

import pytest

from py_swf.benchmark_import import import_exports
from py_swf.benchmark_overview import build_overview
from tests.conftest import make_synthetic_swf


@pytest.fixture(autouse=True)
def trusted_corpus(tmp_path,monkeypatch):
    import py_swf.benchmark_import as importer
    root=tmp_path / "trusted-public"
    root.mkdir()
    monkeypatch.setattr(importer,"PUBLIC_CORPUS_ROOT",root)


def fixtures(tmp_path):
    from py_swf.benchmark_import import PUBLIC_CORPUS_ROOT,PUBLIC_REVISION
    data=make_synthetic_swf()
    swf=tmp_path / "public.swf"
    swf.write_bytes(data)
    (PUBLIC_CORPUS_ROOT / "public.swf").write_bytes(data)
    manifest=tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_revision":PUBLIC_REVISION,"cases":[{"file":"public.swf","source":"public.swf","sha256":hashlib.sha256(data).hexdigest(),"public_corpus":True}]}))
    exports=tmp_path / "exports"
    exports.mkdir()
    return swf,manifest,exports


def test_import_records_artifacts_but_never_invents_time_coverage_or_observed_execution(tmp_path):
    swf,manifest,exports=fixtures(tmp_path)
    (exports / "Script.as").write_text("class Script {}")
    result=import_exports("sothink",swf,exports,tmp_path / "report",manifest,edition="trial")
    engine=result["cases"][0]["engines"][0]
    assert engine["manual_export_observation"]["actionscript_files"]==1
    assert not engine["manual_export_observation"]["execution_observed"]
    assert engine["quality"]["source_available"] is None
    assert engine["hot"]["median_ms"] is None and engine["cold"]["median_ms"] is None
    assert result["cases"][0]["original_preserved"]
    assert not result["claims"]["parity"]
    assert not build_overview([("manual",result)])["selected"]
    assert "class Script {}" not in (tmp_path / "report/benchmark.html").read_text(encoding="utf-8")


def test_private_or_modified_input_is_rejected(tmp_path):
    swf,manifest,exports=fixtures(tmp_path)
    swf.write_bytes(b"modified")
    with pytest.raises(ValueError,match="approved public"):
        import_exports("sothink",swf,exports,tmp_path / "report",manifest)
    assert not (tmp_path / "report").exists()


def test_trial_without_source_is_not_recorded_as_engine_failure(tmp_path):
    swf,manifest,exports=fixtures(tmp_path)
    result=import_exports("trillix",swf,exports,tmp_path / "report",manifest,edition="trial")
    engine=result["cases"][0]["engines"][0]
    assert engine["status"]=="not_applicable" and not engine["failures"]
    assert engine["manual_export_observation"]["actionscript_files"]==0
    assert engine["quality"]["failed"] is None


def test_public_label_in_manifest_is_not_sufficient_evidence(tmp_path):
    swf,manifest,exports=fixtures(tmp_path)
    swf.write_bytes(b"private bytes")
    claimed=json.loads(manifest.read_text())
    claimed["cases"][0]["sha256"]=hashlib.sha256(swf.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(claimed))
    with pytest.raises(ValueError,match="pinned public corpus"):
        import_exports("sothink",swf,exports,tmp_path / "report",manifest)


def test_import_never_overwrites_evidence_and_disallows_other_engines(tmp_path):
    swf,manifest,exports=fixtures(tmp_path)
    import_exports("as3_sorcerer",swf,exports,tmp_path / "report",manifest)
    before=(tmp_path / "report/benchmark.json").read_bytes()
    with pytest.raises(FileExistsError):
        import_exports("as3_sorcerer",swf,exports,tmp_path / "report",manifest)
    assert (tmp_path / "report/benchmark.json").read_bytes()==before
    with pytest.raises(ValueError):
        import_exports("asv",swf,exports,tmp_path / "other",manifest)
