"""Untimed per-document recompilation evidence; never a semantic-parity claim."""
import base64
from collections import Counter
import math
import time

from .benchmarks import sha256
from .validation import validate_swf_bytes


def targets_for(response, engine):
    if engine == "native":
        targets=list(response.get("units", []))
    elif engine == "jpexs":
        targets=list(response.get("documents", []))
        targets.extend(u for u in response.get("units", []) if u.get("language")=="as2")
    else:
        raise ValueError("Only native and pinned JPEXS compilation audits are supported")
    duplicates={key for key,count in Counter(u.get("document_id") for u in targets).items() if count>1}
    for unit in targets:
        reason=None
        if not unit.get("document_id") or unit["document_id"] in duplicates:
            reason="Missing or ambiguous document identity"
        elif unit.get("generation")!="available" or "source" not in unit:
            reason=unit.get("diagnostic") or "Structured source is unavailable"
        elif engine=="native" and not unit.get("compiler_eligible"):
            reason=unit.get("source_edit_reason") or "Document is protected by the native compiler"
        yield unit,reason


def summarize(records):
    attempted=[r for r in records if r["compilation"] in ("passed","failed")]
    return {"targets":len(records),"eligible":sum(r["eligible"] for r in records),"attempted":len(attempted),
            "compiled":sum(r["compilation"]=="passed" for r in records),
            "validated_reopened":sum(r["compilation"]=="passed" and r["swf_validation"]=="passed" and r["swf_reopened"] for r in records),
            "failed":sum(r["compilation"]=="failed" for r in records),
            "not_run":sum(r["compilation"]=="not_run" for r in records),"behavior_verified":0}


def audit_compilation(response, engine, payload, request, *, file_budget_seconds=300, clock=time.monotonic):
    """All identities retained, including protected, failed and budget-limited ones.

    `request` owns worker restart/deadline enforcement. Every candidate is based
    on the original payload, never the previous compilation's modified SWF.
    """
    if not math.isfinite(file_budget_seconds) or file_budget_seconds<=0:
        raise ValueError("Compilation file budget must be positive")
    deadline=clock()+file_budget_seconds
    records=[]
    for unit,reason in targets_for(response,engine):
        record={"document_id":unit.get("document_id"),"scope":"method/action" if engine=="native" else "class/script" if unit.get("language")!="as2" else "action",
                "source_sha256":unit.get("source_sha256"),"eligible":reason is None,"compilation":"not_run",
                "swf_validation":"not_run","swf_reopened":False,"behavior":"not_run","diagnostic":reason}
        if reason is None and clock()>=deadline:
            record["diagnostic"]="Compilation file work budget exceeded; target retained, not approved"
        elif reason is None:
            record["compilation"]="failed"
            try:
                candidate=request({**payload,"operation":"compile","document_id":unit["document_id"],"source":unit["source"]})
                if not candidate.get("ok"):
                    record["diagnostic"]=candidate.get("diagnostics") or candidate.get("diagnostic") or "Compilation failed"
                else:
                    data=base64.b64decode(candidate["swf_base64"],validate=True)
                    validation=validate_swf_bytes(data)
                    record.update(compilation="passed",swf_validation="passed" if validation["valid"] else "failed",
                                  swf_reopened=bool(validation["valid"] and candidate.get("swf_reopened")),
                                  candidate_sha256=sha256(data),diagnostic=validation.get("errors"))
            except Exception as error:
                record["diagnostic"]=str(error)
        records.append(record)
    return {"policy":"Every enumerated target; protected and resource-limited cases retained. Native methods/actions and JPEXS classes/scripts have different denominators.",
            "input_swf_sha256":sha256(base64.b64decode(payload["swf_base64"],validate=True)),
            "file_budget_seconds":file_budget_seconds,"records":records,"summary":summarize(records)}
