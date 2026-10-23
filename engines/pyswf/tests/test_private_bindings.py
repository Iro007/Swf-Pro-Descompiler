import pytest
from fastapi.testclient import TestClient

from py_swf.avm2 import ABCFile, ClassInfo, InstanceInfo, TraitsInfo, assemble_instructions, search_or_add_multiname, search_or_add_string
from py_swf.bytecode_validation import decode_avm2
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.editing import edit_script, source_edit_reason
from py_swf.symbols import MethodBindings, name_identity
from py_swf.swf_parser import SWFFile, SWFTag
from server.app import app
from tests.native_fixtures import as3_tag, swf_with


def private_fixture(*, collision=False):
    seed = as3_tag()
    abc = ABCFile()
    abc.parse(seed.parse_doabc()[2])
    pool = abc.constant_pool
    string = search_or_add_string(pool, "secret")
    pool.namespaces.append({"kind":5,"name":search_or_add_string(pool,"Owner")})
    pool.multinames.append({"kind":7,"ns":len(pool.namespaces)-1,"name":string})
    private = len(pool.multinames)-1
    field = TraitsInfo()
    field.name, field.slot_id = private, 1
    method = TraitsInfo()
    method.name, method.kind_flags, method.method = search_or_add_multiname(pool,"run"), 1, 1
    owner = InstanceInfo()
    owner.name, owner.super_name = search_or_add_multiname(pool,"Owner"), search_or_add_multiname(pool,"Object")
    owner.traits = [field,method]
    if collision:
        public = TraitsInfo()
        public.name, public.slot_id = search_or_add_multiname(pool,"secret"), 2
        owner.traits.append(public)
    abc.instances, abc.classes = [owner], [ClassInfo()]
    abc.scripts[0].traits = []
    # Only method-body editing is under test, not whole class compilation.
    abc.method_bodies[1].code = assemble_instructions(pool,f"getlocal_0\npushscope\ngetlocal_0\ngetproperty #{private}\nreturnvalue")
    abc.method_bodies[1].max_stack = 1
    abc.method_bodies[1].max_scope_depth = 1
    return abc, private


def tag_for(abc):
    return SWFTag(82,b"\0\0\0\0bindings\0"+abc.serialize())


def test_equal_private_uris_are_different_identities():
    abc, first = private_fixture()
    pool = abc.constant_pool
    existing = pool.multinames[first]
    pool.namespaces.append(dict(pool.namespaces[existing["ns"]]))
    pool.multinames.append({**existing,"ns":len(pool.namespaces)-1})
    second = len(pool.multinames)-1
    assert name_identity(pool,first) != name_identity(pool,second)
    with pytest.raises(ValueError,match="declaring-class"):
        MethodBindings(abc,abc.method_bodies[1]).require_private(second)
    other,_ = private_fixture()
    assert name_identity(pool,first) != name_identity(other.constant_pool,first)


def test_method_referenced_by_script_and_class_has_no_unique_owner():
    abc,_ = private_fixture()
    trait=TraitsInfo()
    trait.name,trait.kind_flags,trait.function=search_or_add_multiname(abc.constant_pool,"shared"),5,1
    abc.scripts[0].traits=[trait]
    assert MethodBindings(abc,abc.method_bodies[1]).owner is None
    assert source_edit_reason(abc,abc.method_bodies[1])


@pytest.mark.parametrize("code",["getlex #{index}\npushnull\ncall 0","getlocal_0\ncallproplex #{index} 0"])
def test_private_function_null_receiver_is_not_silently_changed(code):
    abc,index=private_fixture()
    abc.method_bodies[1].code=assemble_instructions(abc.constant_pool,"getlocal_0\npushscope\n"+code.format(index=index)+"\nreturnvalue")
    source,error=decompile_method(abc,abc.method_bodies[1])
    assert source is None and "receiver" in error


@pytest.mark.parametrize("operation",["read","write","call","lexical"])
def test_private_operand_survives_source_compilation(operation):
    abc, index = private_fixture()
    source = {"read":"return this.secret;", "write":"this.secret = 21; return this.secret;",
              "call":"this.secret(); return this.secret;", "lexical":"secret = 21; return secret;"}[operation]
    original = tag_for(abc)
    modified, _ = edit_script(original,source,1,source=True)
    reopened = ABCFile()
    reopened.parse(modified.parse_doabc()[2])
    operands = [ins.args[0] for ins in decode_avm2(reopened.method_bodies[1].code)
                if ins.name in ("getproperty","setproperty","findpropstrict","findproperty","callproperty")]
    assert operands and set(operands) == {index}
    assert reopened.constant_pool.namespaces == abc.constant_pool.namespaces
    assert reopened.constant_pool.multinames == abc.constant_pool.multinames
    assert reopened.method_bodies[0].code == abc.method_bodies[0].code
    assert original.data == tag_for(abc).data


def test_source_reconstruction_keeps_identity_and_this_receiver():
    abc, index = private_fixture()
    source,error = decompile_method(abc,abc.method_bodies[1])
    assert error is None, error
    assert "this.secret" in source
    assert source_edit_reason(abc,abc.method_bodies[1]) is None
    abc.method_bodies[1].code = assemble_instructions(abc.constant_pool,f"getlocal_0\npushscope\ngetlex #{index}\nreturnvalue")
    source,error = decompile_method(abc,abc.method_bodies[1])
    assert error is None and "this.secret" in source


def test_ambiguous_members_unproven_receivers_and_owner_are_rejected():
    abc,_ = private_fixture(collision=True)
    assert "Ambiguous" in source_edit_reason(abc,abc.method_bodies[1])
    _,error = decompile_method(abc,abc.method_bodies[1])
    assert error
    abc,index = private_fixture()
    with pytest.raises(ValueError,match="proven this"):
        edit_script(tag_for(abc),"return score.secret;",1,source=True)
    abc.method_bodies[1].code = assemble_instructions(abc.constant_pool,f"getlocal_1\ngetproperty #{index}\nreturnvalue")
    _,error = decompile_method(abc,abc.method_bodies[1])
    assert error and "receiver" in error
    abc.instances = []
    assert source_edit_reason(abc,abc.method_bodies[1])


def test_private_edit_save_reopen_undo_and_stale_failure_rollback():
    abc,index = private_fixture()
    original = swf_with([tag_for(abc)],"CWS")
    client = TestClient(app)
    sid = client.post("/api/files",files={"file":("private.swf",original)}).json()["session_id"]
    endpoint = f"/api/files/{sid}/tags/0/compile-source"
    response = client.post(endpoint,json={"body_index":1,"source":"this.secret = 42; return this.secret;"})
    assert response.status_code == 200,response.text
    saved = client.get(f"/api/files/{sid}/download").content
    movie = SWFFile()
    movie.read_bytes(saved)
    reopened = ABCFile()
    reopened.parse(movie.tags[0].parse_doabc()[2])
    assert reopened.constant_pool.multinames[index] == abc.constant_pool.multinames[index]
    assert reopened.constant_pool.namespaces == abc.constant_pool.namespaces
    reopened_sid = client.post("/api/files",files={"file":("saved.swf",saved)}).json()["session_id"]
    assert client.get(f"/api/files/{reopened_sid}/tags/0/decompile").status_code == 200
    before = client.get(f"/api/files/{sid}/history").json()
    assert client.post(endpoint,json={"body_index":1,"source":"return score.secret;"}).status_code == 422
    assert client.get(f"/api/files/{sid}/download").content == saved
    assert client.get(f"/api/files/{sid}/history").json()["undo"] == before["undo"]
    assert client.post(f"/api/files/{sid}/undo").status_code == 200
    assert client.get(f"/api/files/{sid}/download").content == original
    assert client.post(f"/api/files/{sid}/redo").status_code == 200
    assert client.get(f"/api/files/{sid}/download").content == saved
