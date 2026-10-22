import json

from py_swf.avm1 import assemble_avm1
from py_swf.corpus_audit import run_audit
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf


def test_audit_preserves_every_case_and_records_failure_not_speed(tmp_path):
    good=tmp_path / "good.swf"
    data=make_synthetic_swf([SWFTag(12,assemble_avm1("push 1\ntrace\nend"))])
    good.write_bytes(data)
    (tmp_path / "broken.swf").write_bytes(b"invalid")
    report=run_audit(tmp_path,engines=("native",),output=tmp_path / "report")
    assert report["completed_files"]==report["scheduled_files"]==2
    assert good.read_bytes()==data
    assert report["audit_summary"]["operation_failure_files"]==["broken.swf"]
    assert all(e["hot"]["median_ms"] is None for c in report["cases"] for e in c["engines"])
    assert not report["audit_summary"]["behavior_verified"]
    disk=json.loads((tmp_path / "report/benchmark.json").read_text(encoding="utf-8"))
    assert disk["profile"]=="source_audit"
    assert disk["provenance"]["implementation_unchanged"]
