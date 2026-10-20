import io
import json
import zipfile

from fastapi.testclient import TestClient
from py_swf.fla_export import export_resource_bundle
from py_swf.swf_parser import SWFFile,SWFTag
from server.app import app
from tests.native_fixtures import swf_with,as3_tag


def test_bundle_does_not_claim_fla_or_hide_extraction_errors():
    movie=SWFFile()
    movie.read_bytes(swf_with([SWFTag(21,b'\x01\x00not-an-image'),as3_tag()]))
    with zipfile.ZipFile(io.BytesIO(export_resource_bundle(movie))) as archive:
        manifest=json.loads(archive.read("manifest.json"))
        assert manifest["format"]=="pyswf-resource-bundle"
        assert manifest["animate_compatible"] is False
        assert manifest["diagnostics"] and manifest["diagnostics"][0]["tag_index"]==0
        assert "scripts/script_1.pcode" in archive.namelist()
        assert not any(name.endswith((".fla",".as")) for name in archive.namelist())


def test_resource_bundle_api_and_legacy_alias_have_truthful_names():
    client=TestClient(app)
    sid=client.post("/api/files",files={"file":("bundle.swf",swf_with([as3_tag()]))}).json()["session_id"]
    for path in ("resource-bundle","fla"):
        response=client.get(f"/api/files/{sid}/export/{path}")
        assert response.status_code==200,response.text
        assert ".resources.zip" in response.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "manifest.json" in archive.namelist()
        if path=="fla":
            assert "Not FLA/XFL" in response.headers["warning"]
