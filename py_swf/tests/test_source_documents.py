from fastapi.testclient import TestClient

from py_swf.avm1 import assemble_avm1
from py_swf.swf_parser import SWFTag
from server.app import app
from tests.conftest import make_synthetic_swf
from tests.native_fixtures import as3_tag, swf_with


client = TestClient(app)


def open_swf(data, filename="source.swf"):
    response = client.post("/api/files", files={"file": (filename, data)})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def document_for(sid, tag_index, document_id):
    response = client.get(f"/api/files/{sid}/tags/{tag_index}/decompile?engine=native")
    assert response.status_code == 200, response.text
    for section in response.json()["sections"]:
        document = section.get("source_document")
        if document and document["document_id"] == document_id:
            return section, document
    raise AssertionError(f"Missing source document {document_id}")


def compile_document(sid, tag_index, document, source):
    return client.post(
        f"/api/files/{sid}/tags/{tag_index}/compile-document",
        json={
            "document_id": document["document_id"],
            "source": source,
            "base_swf_sha256": document["base_swf_sha256"],
        },
    )


def test_native_avm1_action_document_compiles_and_saved_source_is_returned():
    data = make_synthetic_swf([SWFTag(12, assemble_avm1('push "old"\ntrace\nend'))])
    sid = open_swf(data, "action.swf")

    section, document = document_for(sid, 0, "tag:0/action")
    assert section["source"]  # Existing decompiler output remains unmodified.
    assert document["kind"] == "action"
    assert document["editable"] is True
    assert document["source_bytecode_map"] == []
    assert document["map_status"] == "not_available"

    response = compile_document(sid, 0, document, 'trace("saved AS2");')
    assert response.status_code == 200, response.text
    assert response.json()["engine"] == "native"
    assert response.json()["document_id"] == "tag:0/action"
    assert response.json()["base_swf_sha256"] != document["base_swf_sha256"]
    reopened_section, _ = document_for(sid, 0, "tag:0/action")
    assert reopened_section["saved_source"] == 'trace("saved AS2");'
    assert 'saved AS2' in reopened_section["source"]


def test_native_avm2_method_document_compiles_and_has_stable_method_identity():
    sid = open_swf(swf_with([as3_tag()]), "method.swf")
    section, document = document_for(sid, 0, "tag:0/method:1")

    assert section["body_index"] == 1
    assert document["kind"] == "method"
    assert document["symbol_context"] == {
        "body_index": 1,
        "method_index": 1,
        "abc_major": 46,
        "abc_minor": 16,
    }
    assert document["editable"] is True

    response = compile_document(sid, 0, document, 'return "edited AS3";')
    assert response.status_code == 200, response.text
    assert response.json()["validation"]
    new_section, _ = document_for(sid, 0, "tag:0/method:1")
    assert new_section["saved_source"] == 'return "edited AS3";'


def test_stale_document_and_unknown_or_protected_ids_do_not_mutate_session():
    sid = open_swf(swf_with([as3_tag()]), "protected.swf")
    _section, method = document_for(sid, 0, "tag:0/method:1")
    before_bytes = client.get(f"/api/files/{sid}/download").content

    stale = compile_document(sid, 0, method, 'return "first";')
    assert stale.status_code == 200, stale.text
    after_first = client.get(f"/api/files/{sid}/download").content
    history_after_first = client.get(f"/api/files/{sid}/history").json()

    stale_again = compile_document(sid, 0, method, 'return "stale";')
    assert stale_again.status_code == 409
    assert client.get(f"/api/files/{sid}/download").content == after_first
    assert client.get(f"/api/files/{sid}/history").json() == history_after_first

    current = document_for(sid, 0, "tag:0/method:1")[1]
    unknown = {**current, "document_id": "tag:0/method:999"}
    unknown_response = compile_document(sid, 0, unknown, 'return "unknown";')
    assert unknown_response.status_code == 422

    # Method 0 is the script initializer and is protected from source recompilation.
    protected = document_for(sid, 0, "tag:0/method:0")[1]
    assert protected["editable"] is False
    protected_response = compile_document(sid, 0, protected, "return;")
    assert protected_response.status_code == 422

    assert client.get(f"/api/files/{sid}/download").content == after_first
    assert client.get(f"/api/files/{sid}/history").json() == history_after_first
    assert before_bytes != after_first


def test_saved_source_is_visible_only_for_matching_working_hash_across_undo_redo():
    sid = open_swf(swf_with([as3_tag()]), "undo.swf")
    _section, document = document_for(sid, 0, "tag:0/method:1")
    compiled = compile_document(sid, 0, document, 'return "persistent";')
    assert compiled.status_code == 200, compiled.text

    edited_section, _ = document_for(sid, 0, "tag:0/method:1")
    assert edited_section["saved_source"] == 'return "persistent";'

    assert client.post(f"/api/files/{sid}/undo").status_code == 200
    undone_section, _ = document_for(sid, 0, "tag:0/method:1")
    assert "saved_source" not in undone_section

    assert client.post(f"/api/files/{sid}/redo").status_code == 200
    redone_section, _ = document_for(sid, 0, "tag:0/method:1")
    assert redone_section["saved_source"] == 'return "persistent";'


def test_saved_source_round_trips_in_native_project_archive():
    sid = open_swf(swf_with([as3_tag()]), "project.swf")
    document = document_for(sid, 0, "tag:0/method:1")[1]
    source = 'return "project save";'
    response = compile_document(sid, 0, document, source)
    assert response.status_code == 200, response.text

    archive = client.get(f"/api/files/{sid}/project")
    assert archive.status_code == 200, archive.text
    reopened = client.post(
        "/api/files/projects",
        files={"file": ("project.pyswfproj", archive.content)},
    )
    assert reopened.status_code == 200, reopened.text
    reopened_sid = reopened.json()["session_id"]
    section, _ = document_for(reopened_sid, 0, "tag:0/method:1")
    assert section["saved_source"] == source


def test_undo_restores_previous_authored_source_not_only_the_swf():
    sid=open_swf(swf_with([as3_tag()]),"source-history.swf")
    document=document_for(sid,0,"tag:0/method:1")[1]
    first='return "first authored";'
    second='return "second authored";'
    assert compile_document(sid,0,document,first).status_code==200
    document=document_for(sid,0,"tag:0/method:1")[1]
    assert compile_document(sid,0,document,second).status_code==200
    assert client.post(f"/api/files/{sid}/undo").status_code==200
    assert document_for(sid,0,"tag:0/method:1")[0]["saved_source"]==first
    assert client.post(f"/api/files/{sid}/redo").status_code==200
    assert document_for(sid,0,"tag:0/method:1")[0]["saved_source"]==second


def test_document_lookup_does_not_require_decompiling_an_entire_abc(monkeypatch):
    from py_swf.source_document import find_native_source_document
    from py_swf.decompile.backends import NativeBackend
    tag=as3_tag()
    monkeypatch.setattr(NativeBackend,"decompile",lambda *a:(_ for _ in ()).throw(AssertionError("must not decompile")))
    document=find_native_source_document(tag,0,swf_with([tag]),"tag:0/method:1")
    assert document and document["editable"]
