import hashlib

from fastapi.testclient import TestClient
from py_swf.avm2 import ABCFile
from server.app import app
from server.sessions import registry
from tests.test_class_compiler import SOURCE

client=TestClient(app)


def create():
    response=client.post("/api/files/from-source",json={"source":SOURCE,"filename":"counter.swf"})
    assert response.status_code==200,response.text
    return response.json()["session_id"]


def document(sid):
    result=client.get(f"/api/files/{sid}/tags/1/decompile").json()
    return next(section for section in result["sections"] if section.get("source_document",{}).get("kind")=="script")


def edit(sid,section,source):
    doc=section["source_document"]
    return client.post(f"/api/files/{sid}/tags/1/compile-document",json={"document_id":doc["document_id"],"base_swf_sha256":doc["base_swf_sha256"],"source":source})


def test_native_program_creation_edit_export_and_reopen_project():
    sid=create()
    initial=client.get(f"/api/files/{sid}/download").content
    section=document(sid)
    assert section["source"]==SOURCE and section["source_origin"]=="authored_verified"
    changed=SOURCE.replace("n; return","n + 4; return")
    response=edit(sid,section,changed)
    assert response.status_code==200,response.text
    assert client.get(f"/api/files/{sid}/download").content!=initial
    assert document(sid)["source"]==changed
    archive=client.get(f"/api/files/{sid}/project").content
    opened=client.post("/api/files/projects",files={"file":("counter.pyswfproj",archive)})
    assert opened.status_code==200,opened.text
    assert document(opened.json()["session_id"])["source"]==changed
    assert client.post(f"/api/files/{sid}/undo").status_code==200
    assert document(sid)["source"]==SOURCE
    assert client.post(f"/api/files/{sid}/redo").status_code==200
    assert document(sid)["source"]==changed


def test_program_stale_or_signature_change_does_not_mutate_bytes_history_or_sources():
    sid=create()
    before=document(sid)
    assert edit(sid,before,SOURCE.replace("2;","3;",1)).status_code==200
    after_bytes=client.get(f"/api/files/{sid}/download").content
    history=client.get(f"/api/files/{sid}/history").json()
    after=document(sid)
    assert edit(sid,before,SOURCE).status_code==409
    assert edit(sid,after,SOURCE.replace("add(n:int = 1)","add(n:int = 2)")).status_code==422
    assert client.get(f"/api/files/{sid}/download").content==after_bytes
    assert client.get(f"/api/files/{sid}/history").json()==history
    assert document(sid)["source"]==after["source"]


def test_forged_managed_source_metadata_cannot_overwrite_opaque_abc():
    from tests.native_fixtures import as3_tag,swf_with
    data=swf_with([as3_tag()])
    sid=client.post("/api/files",files={"file":("opaque.swf",data)}).json()["session_id"]
    session=registry.get(sid)
    session.source_documents["tag:0/script:0"]={"source":SOURCE,"native_class_program":True,"tag_sha256":hashlib.sha256(session.swf.tags[0].data).hexdigest()}
    response=client.post(f"/api/files/{sid}/tags/0/compile-document",json={"document_id":"tag:0/script:0","source":SOURCE,"base_swf_sha256":hashlib.sha256(session.swf.save_bytes()).hexdigest()})
    assert response.status_code==422
    assert client.get(f"/api/files/{sid}/download").content==data
    assert client.get(f"/api/files/{sid}/history").json()["undo"]==0
