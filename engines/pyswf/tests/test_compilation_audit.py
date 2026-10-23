import base64

import pytest

from py_swf.compilation_audit import audit_compilation,targets_for
from py_swf.corpus_audit import run_audit
from py_swf.avm1 import assemble_avm1
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf


def unit(identity,**changes):
    return {"document_id":identity,"language":"as3","generation":"available","compiler_eligible":True,"source":"return 1;",**changes}


def test_all_targets_after_eight_attempted_and_failure_does_not_hide_later_units():
    data=make_synthetic_swf()
    payload={"operation":"decompile","swf_base64":base64.b64encode(data).decode()}
    calls=[]
    def request(item):
        assert item["swf_base64"]==payload["swf_base64"]
        calls.append(item["document_id"])
        if item["document_id"]=="m:2":
            raise RuntimeError("worker timeout")
        return {"ok":True,"swf_base64":payload["swf_base64"],"swf_reopened":True}
    response={"units":[unit(f"m:{index}") for index in range(12)]+[unit("protected",compiler_eligible=False,source_edit_reason="activation closure")],"documents":[]}
    result=audit_compilation(response,"native",payload,request)
    assert calls==[f"m:{index}" for index in range(12)]
    assert result["summary"]=={"targets":13,"eligible":12,"attempted":12,"compiled":11,"validated_reopened":11,"failed":1,"not_run":1,"behavior_verified":0}
    assert result["records"][-1]["diagnostic"]=="activation closure"
    assert all("source" not in record and record["behavior"]=="not_run" for record in result["records"])


def test_budget_retains_unattempted_targets_without_approval():
    values=iter([0,4,5,6])
    response={"units":[unit(str(i)) for i in range(3)]}
    result=audit_compilation(response,"native",{"swf_base64":base64.b64encode(make_synthetic_swf()).decode()},lambda r:pytest.fail("budget-limited worker must not run"),file_budget_seconds=1,clock=lambda:next(values))
    assert result["summary"]["targets"]==3 and result["summary"]["eligible"]==3
    assert result["summary"]["not_run"]==3 and not result["summary"]["compiled"]


def test_invalid_candidate_is_not_validated_or_reopened():
    result=audit_compilation({"units":[unit("test")]},"native",{"swf_base64":base64.b64encode(make_synthetic_swf()).decode()},lambda r:{"ok":True,"swf_base64":base64.b64encode(b"invalid").decode(),"swf_reopened":True})
    assert result["summary"]["validated_reopened"]==0
    assert result["records"][0]["swf_validation"]=="failed"


def test_jpexs_class_targets_do_not_become_method_denominators():
    response={"documents":[unit("class:One")],"units":[unit("method:1"),unit("action:1",language="as2"),unit("action:2",language="as2",generation="failed",diagnostic="reference error")]}
    targets=list(targets_for(response,"jpexs"))
    assert [u["document_id"] for u,_ in targets]==["class:One","action:1","action:2"]
    assert targets[-1][1]=="reference error"


def test_duplicate_identities_are_retained_and_protected():
    targets=list(targets_for({"units":[unit("same"),unit("same")]},"native"))
    assert len(targets)==2 and all(reason=="Missing or ambiguous document identity" for _,reason in targets)


@pytest.mark.parametrize("budget",[0,-1,float("inf"),float("nan")])
def test_invalid_budget_is_rejected(budget):
    with pytest.raises(ValueError,match="budget"):
        audit_compilation({},"native",{},lambda _:None,file_budget_seconds=budget)




def test_audit_recompiles_original_without_mutating_file_or_inventing_behavior(tmp_path):
    path=tmp_path / "public.swf"
    data=make_synthetic_swf([SWFTag(12,assemble_avm1("push 1\ntrace\nend"))])
    path.write_bytes(data)
    report=run_audit(path,engines=("native",),output=tmp_path / "report",compile_documents=True)
    result=report["cases"][0]["engines"][0]
    assert result["compilation_audit"]["summary"]["validated_reopened"]==1
    assert all("source" not in u for u in result["units"])
    assert path.read_bytes()==data
    assert not report["audit_summary"]["behavior_verified"]
    assert result["hot"]["median_ms"] is None
