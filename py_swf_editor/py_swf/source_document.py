"""Stable native source-document identities and decompilation metadata."""

from __future__ import annotations

import hashlib

from .avm2 import ABCFile
from .decompile.backends import NativeBackend
from .editing import source_edit_reason


def swf_sha256(swf_bytes: bytes) -> str:
    return hashlib.sha256(swf_bytes).hexdigest()


def native_source_documents(tag, tag_index: int, swf_bytes: bytes, *, result=None) -> list[dict]:
    """Describe native source sections without changing their original text."""
    if result is None:
        result = NativeBackend().decompile(tag, swf_bytes, "untitled.swf").as_dict()
    kind = result["kind"]
    abc = None
    if kind == "avm2":
        parsed = tag.parse_doabc()
        if parsed is None:
            raise ValueError("Malformed DoABC tag")
        abc = ABCFile()
        abc.parse(parsed[2])

    documents = []
    for section in result["sections"]:
        body_index = section.get("body_index")
        method_index = None
        if kind == "avm1":
            document_id = f"tag:{tag_index}/action"
            document_kind = "action"
        elif section.get("scope") in ("class", "script"):
            document_kind = section["scope"]
            index = section[document_kind + "_index"]
            document_id = f"tag:{tag_index}/{document_kind}:{index}"
        elif body_index is None:
            document_id = f"tag:{tag_index}/outline"
            document_kind = "outline"
        else:
            if not 0 <= body_index < len(abc.method_bodies):
                continue
            method_index = abc.method_bodies[body_index].method
            document_id = f"tag:{tag_index}/method:{method_index}"
            document_kind = "method"

        reason = section.get("source_edit_reason")
        # Replacing a complete method body is an existing explicit capability,
        # even when the old reconstruction is incomplete. Binding protections
        # remain enforced by edit_script before candidate bytes are committed.
        editable = bool(section.get("editable_source") or section.get("replaceable_source"))
        diagnostics = [str(item) for item in (section.get("error"), reason) if item]
        symbol_context = {
            "body_index": body_index,
            "method_index": method_index,
            "abc_major": None,
            "abc_minor": None,
        }
        if kind == "avm2":
            symbol_context["abc_major"] = abc.major_version
            symbol_context["abc_minor"] = abc.minor_version
        if document_kind in ("class", "script"):
            symbol_context[document_kind + "_index"] = section[document_kind + "_index"]
            symbol_context["method_indices"] = sorted({int(unit.split(":", 1)[1]) for unit in section.get("unit_ids", []) if unit.startswith("method:")})
        documents.append({
            "document_id": document_id,
            "kind": document_kind,
            "scope": document_kind,
            "source_kind": section.get("source_kind", document_kind),
            "represented_by_document": f"tag:{tag_index}/" + section["represented_by_document"] if section.get("represented_by_document") else None,
            "completeness": section.get("completeness", "outline" if document_kind == "outline" else "complete" if not section.get("error") else "partial"),
            "unit_ids": [f"tag:{tag_index}/" + unit for unit in section.get("unit_ids", [f"method:{method_index}"] if method_index is not None else ["action"] if document_kind == "action" else [])],
            "compiler_eligibility": section.get("compiler_eligibility", {"eligible": editable, "reason": reason}),
            "original_source_sha256": hashlib.sha256(section.get("source", "").encode("utf-8")).hexdigest(),
            "tag_sha256": hashlib.sha256(tag.data).hexdigest(),
            "base_swf_sha256": swf_sha256(swf_bytes),
            "editable": editable,
            "diagnostics": list(dict.fromkeys([*section.get("diagnostics", []), *diagnostics])),
            "symbol_context": symbol_context,
            "source_bytecode_map": [],
            "map_status": "not_available",
        })
    return documents


def find_native_source_document(tag, tag_index: int, swf_bytes: bytes, document_id: str):
    # Resolve the requested unit, not every source in a large ABC. Compilation
    # performs its own binding checks and must not depend on decompiler success.
    if tag.tag_type in (12,59):
        return next((d for d in native_source_documents(tag,tag_index,swf_bytes,result={"kind":"avm1","sections":[{"body_index":0,"editable_source":True}]}) if d["document_id"]==document_id),None)
    if not tag.is_doabc:
        return None
    parsed=tag.parse_doabc()
    if parsed is None:
        return None
    abc=ABCFile()
    abc.parse(parsed[2])
    expected=f"tag:{tag_index}/method:"
    if document_id.startswith((f"tag:{tag_index}/class:", f"tag:{tag_index}/script:")):
        return next((d for d in native_source_documents(tag, tag_index, swf_bytes) if d["document_id"] == document_id), None)
    if document_id==f"tag:{tag_index}/outline":
        section={"editable_source":False,"source_edit_reason":"Class outlines are read-only"}
    elif document_id.startswith(expected) and document_id[len(expected):].isdigit():
        method_index=int(document_id[len(expected):])
        candidates=[(i,b) for i,b in enumerate(abc.method_bodies) if b.method==method_index]
        if len(candidates)!=1:
            return None
        body_index,body=candidates[0]
        reason=source_edit_reason(abc,body)
        section={"body_index":body_index,"editable_source":reason is None,"source_edit_reason":reason}
    else:
        return None
    documents=native_source_documents(tag,tag_index,swf_bytes,result={"kind":"avm2","sections":[section]})
    return next((d for d in documents if d["document_id"]==document_id),None)
