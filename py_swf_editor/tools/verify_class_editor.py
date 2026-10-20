"""Verify native class creation/edit/export/project round-trip in the real UI.

Structural/editor verification only, not a Flash runtime-equivalence test.
The frozen executable is started outside the checkout with Java unavailable.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from playwright.sync_api import sync_playwright,expect
from py_swf.avm2 import ABCFile
from py_swf.swf_parser import SWFFile
from tests.test_class_compiler import SOURCE
from tools.verify_native_flow import payload


def verify(exe=None):
    folder=Path(tempfile.mkdtemp(prefix="pyswf-class-editor-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port=sock.getsockname()[1]
    url=f"http://127.0.0.1:{port}"
    env=dict(os.environ,JAVA="nonexistent-java",PY_SWF_FFDEC="nonexistent-ffdec",PY_SWF_FFDEC_JAR="nonexistent.jar")
    for key in ("PY_SWF_API_TOKEN","PY_SWF_JPEXS_BRIDGE","PY_SWF_JPEXS_JAVA_BRIDGE"):
        env.pop(key,None)
    if exe:
        env["PATH"]=os.path.join(env.get("SystemRoot","C:/Windows"),"System32")
    command=[str(Path(exe).resolve()),"--headless","--port",str(port)] if exe else [sys.executable,"-m","uvicorn","server.app:app","--host","127.0.0.1","--port",str(port)]
    with (folder/"server.log").open("w",encoding="utf-8") as log:
        process=subprocess.Popen(command,cwd=folder if exe else ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError("Server exited: "+(folder/"server.log").read_text(encoding="utf-8",errors="replace"))
                try:
                    if httpx.get(url+"/api/files/engines/status",timeout=1).status_code==200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Server startup timed out")
            errors=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={"width":1440,"height":1000})
                expect.set_options(timeout=30000)
                page.on("pageerror",lambda error:errors.append(str(error)))
                try:
                    page.goto(url,wait_until="networkidle")
                    original=payload("as3","CWS")
                    with page.expect_response(lambda r:r.request.method=="POST" and r.url==url+"/api/files") as opened:
                        page.get_by_label("Abrir archivo SWF",exact=True).set_input_files({"name":"original.swf","mimeType":"application/octet-stream","buffer":original})
                    old_sid=opened.value.json()["session_id"]
                    page.get_by_role("button",name="Nuevo desde código",exact=True).click()
                    source_input=page.get_by_label("Programa AS3 nuevo",exact=True)
                    source_input.fill("public class Bad extends Unknown {}")
                    with page.expect_response(lambda r:r.url.endswith("/from-source") and r.request.method=="POST") as invalid:
                        page.get_by_role("button",name="Crear SWF",exact=True).click()
                    assert invalid.value.status==422
                    expect(source_input).to_have_value("public class Bad extends Unknown {}")
                    source_input.fill(SOURCE)
                    page.get_by_label("Nombre del nuevo SWF",exact=True).fill("counter.swf")
                    with page.expect_response(lambda r:r.url.endswith("/from-source") and r.request.method=="POST") as created:
                        page.get_by_role("button",name="Crear SWF",exact=True).click()
                    assert created.value.status==200,created.value.text()
                    sid=created.value.json()["session_id"]
                    endpoint=url+f"/api/files/{sid}"
                    assert page.request.get(url+f"/api/files/{old_sid}/download").body()==original
                    expect(page.get_by_test_id("source-document-identity")).to_contain_text("Programa AS3")
                    expect(page.get_by_test_id("source-document-identity")).to_contain_text("tag:1/script:0")
                    initial=page.request.get(endpoint+"/download").body()
                    changed=SOURCE.replace("n; return","n + 4; return")
                    page.get_by_role("button",name="Editar fuente",exact=True).click()
                    editor=page.get_by_label("Editor ActionScript",exact=True)
                    expect(editor).to_have_value(SOURCE)
                    editor.fill(changed)
                    with page.expect_response(lambda r:r.url.endswith("/compile-document") and r.request.method=="POST") as compiled:
                        page.get_by_role("button",name="Recompilar",exact=True).click()
                    assert compiled.value.status==200,compiled.value.text()
                    assert compiled.value.request.post_data_json["document_id"]=="tag:1/script:0"
                    saved=page.request.get(endpoint+"/download").body()
                    assert saved!=initial
                    page.get_by_role("button",name="Deshacer",exact=True).click()
                    page.get_by_role("button",name="Editar fuente",exact=True).click()
                    expect(editor).to_have_value(SOURCE)
                    page.get_by_role("button",name="Descartar",exact=True).click()
                    page.get_by_role("button",name="Rehacer",exact=True).click()
                    page.get_by_role("button",name="Editar fuente",exact=True).click()
                    expect(editor).to_have_value(changed)
                    incompatible=changed.replace("add(n:int = 1)","add(n:int = 2)")
                    editor.fill(incompatible)
                    history=page.request.get(endpoint+"/history").json()
                    with page.expect_response(lambda r:r.url.endswith("/compile-document") and r.request.method=="POST") as rejected:
                        page.get_by_role("button",name="Recompilar",exact=True).click()
                    assert rejected.value.status==422
                    expect(editor).to_have_value(incompatible)
                    assert page.request.get(endpoint+"/history").json()==history
                    assert page.request.get(endpoint+"/download").body()==saved
                    page.screenshot(path=str(folder/"protected-signature-change.png"),full_page=True)
                    page.get_by_role("button",name="Descartar",exact=True).click()
                    with page.expect_download() as downloaded:
                        page.get_by_role("button",name="Guardar proyecto",exact=True).click()
                    project_path=folder/"counter.pyswfproj"
                    downloaded.value.save_as(project_path)
                    with zipfile.ZipFile(project_path) as archive:
                        assert archive.read("original.swf")==initial
                        assert archive.read("working.swf")==saved
                    page.get_by_label("Abrir archivo SWF",exact=True).set_input_files(str(project_path))
                    page.get_by_label("Buscar recursos",exact=True).fill("DoABC")
                    page.locator(".tree-tag").filter(has_text="DoABC").first.click()
                    page.get_by_role("button",name="Editar fuente",exact=True).click()
                    expect(editor).to_have_value(changed)
                    page.get_by_role("button",name="Descartar",exact=True).click()
                    page.screenshot(path=str(folder/"native-class-program.png"),full_page=True)
                    with page.expect_download() as exported:
                        page.get_by_role("button",name="Exportar SWF",exact=True).click()
                    swf_path=folder/"counter.saved.swf"
                    exported.value.save_as(swf_path)
                    assert swf_path.read_bytes()==saved
                    movie=SWFFile()
                    movie.read_bytes(saved)
                    abc=ABCFile()
                    abc.parse(movie.tags[1].parse_doabc()[2])
                    assert len(abc.instances)==1 and len(abc.instances[0].traits)==4
                    page.get_by_label("Abrir archivo SWF",exact=True).set_input_files(str(swf_path))
                    page.get_by_label("Buscar recursos",exact=True).fill("DoABC")
                    page.locator(".tree-tag").filter(has_text="DoABC").first.click()
                    expect(page.get_by_test_id("source-document-identity")).to_contain_text("Esquema de lectura")
                    assert "Programa AS3" not in page.get_by_label("Sección de fuente",exact=True).inner_text()
                    assert not errors,errors
                except Exception:
                    page.screenshot(path=str(folder/"failure.png"),full_page=True)
                    raise
                finally:
                    browser.close()
            return {"passed":True,"packaged":bool(exe),"java_required":False,
                    "executable_sha256":hashlib.sha256(Path(exe).read_bytes()).hexdigest() if exe else None,
                    "create_from_source":True,"edit_managed_program":True,"undo_redo_source":True,
                    "original_session_preserved":True,"project_v2_source_reopen":True,"swf_export_reopen":True,
                    "rejected_edit_preserves_draft_bytes_history":True,"opaque_abc_protected":True,
                    "flash_runtime_behavior_verified":False,"errors":errors,"artifacts":str(folder)}
        finally:
            if os.name=="nt":
                subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],capture_output=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            else:
                process.terminate()
            process.wait(timeout=5)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--exe",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    result=verify(args.exe)
    if args.output:
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
