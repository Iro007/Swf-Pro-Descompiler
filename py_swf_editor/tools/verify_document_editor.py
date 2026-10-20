"""Native UI -> document API -> SWF/history/project -> reopen verification."""
import argparse
import hashlib
import io
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
from tools.verify_native_flow import payload


def verify(exe=None):
    folder=Path(tempfile.mkdtemp(prefix="pyswf-document-editor-"))
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
    with (folder / "server.log").open("w",encoding="utf-8") as log:
        process=subprocess.Popen(command,cwd=folder if exe else ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError("Native server exited: "+(folder / "server.log").read_text(encoding="utf-8",errors="replace"))
                try:
                    if httpx.get(url+"/api/files/engines/status",timeout=1).status_code==200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Native server startup timed out")
            errors=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={"width":1440,"height":1000})
                expect.set_options(timeout=30000)
                page.on("pageerror",lambda error:errors.append(str(error)))
                try:
                    page.goto(url,wait_until="networkidle")
                    with page.expect_response(lambda r:r.request.method=="POST" and r.url==url+"/api/files") as uploaded:
                        page.get_by_label("Abrir archivo SWF",exact=True).set_input_files({"name":"documents.swf","mimeType":"application/octet-stream","buffer":payload("as3","CWS")})
                    sid=uploaded.value.json()["session_id"]
                    page.get_by_label("Buscar recursos",exact=True).fill("DoABC")
                    page.locator(".tree-tag").filter(has_text="DoABC").first.click()
                    select=page.get_by_label("Sección de fuente",exact=True)
                    select.select_option("2")
                    expect(page.get_by_test_id("source-document-identity")).to_contain_text("tag:1/method:1")
                    first="var values = [1,2]; values[0] += 4; return values[0];"
                    for source in (first,"return 9;"):
                        page.get_by_role("button",name="Editar fuente",exact=True).click()
                        page.get_by_label("Editor ActionScript",exact=True).fill(source)
                        with page.expect_response(lambda r:r.url.endswith("/compile-document") and r.request.method=="POST") as compiled:
                            page.get_by_role("button",name="Recompilar",exact=True).click()
                        assert compiled.value.status==200,compiled.value.text()
                        assert compiled.value.request.post_data_json["document_id"]=="tag:1/method:1"
                        select.select_option("2")
                        expect(page.get_by_role("button",name="Recuperar fuente guardada",exact=True)).to_be_visible()
                    page.get_by_role("button",name="Deshacer",exact=True).click()
                    select.select_option("2")
                    page.get_by_role("button",name="Recuperar fuente guardada",exact=True).click()
                    expect(page.get_by_label("Editor ActionScript",exact=True)).to_have_value(first)
                    page.get_by_role("button",name="Descartar",exact=True).click()
                    with page.expect_download() as downloaded:
                        page.get_by_role("button",name="Guardar proyecto",exact=True).click()
                    path=folder / "documents.pyswfproj"
                    downloaded.value.save_as(path)
                    with zipfile.ZipFile(path) as archive:
                        from py_swf.project import FORMAT_VERSION
                        assert json.loads(archive.read("manifest.json"))["format_version"]==FORMAT_VERSION
                        assert archive.read("original.swf")==payload("as3","CWS")
                    with page.expect_response(lambda r:r.request.method=="POST" and r.url==url+"/api/files/projects") as reopened:
                        page.get_by_label("Abrir archivo SWF",exact=True).set_input_files(str(path))
                    sid=reopened.value.json()["session_id"]
                    page.get_by_label("Buscar recursos",exact=True).fill("DoABC")
                    page.locator(".tree-tag").filter(has_text="DoABC").first.click()
                    select.select_option("2")
                    page.get_by_role("button",name="Recuperar fuente guardada",exact=True).click()
                    expect(page.get_by_label("Editor ActionScript",exact=True)).to_have_value(first)
                    draft="return 101;"
                    page.get_by_label("Editor ActionScript",exact=True).fill(draft)
                    mutation=page.request.post(url+f"/api/files/{sid}/tags/1/compile-source",data={"body_index":1,"source":"return 99;"})
                    assert mutation.status==200,mutation.text()
                    before=page.request.get(url+f"/api/files/{sid}/history").json()
                    before_bytes=page.request.get(url+f"/api/files/{sid}/download").body()
                    with page.expect_response(lambda r:r.url.endswith("/compile-document") and r.request.method=="POST") as rejected:
                        page.get_by_role("button",name="Recompilar",exact=True).click()
                    assert rejected.value.status==409
                    expect(page.get_by_label("Editor ActionScript",exact=True)).to_have_value(draft)
                    expect(page.get_by_role("button",name="Recompilar",exact=True)).to_be_disabled()
                    assert page.request.get(url+f"/api/files/{sid}/history").json()==before
                    assert page.request.get(url+f"/api/files/{sid}/download").body()==before_bytes
                    page.screenshot(path=str(folder / "stale-draft-protected.png"),full_page=True)
                    page.get_by_role("button",name="Descartar borrador y recargar",exact=True).click()
                    expect(page.locator(".code-lines")).to_contain_text("99")
                    page.screenshot(path=str(folder / "native-document-editor.png"),full_page=True)
                    assert not errors,errors
                except Exception:
                    page.screenshot(path=str(folder / "failure.png"),full_page=True)
                    raise
                finally:
                    browser.close()
            return {"passed":True,"java_required":False,"packaged":bool(exe),"executable_sha256":hashlib.sha256(Path(exe).read_bytes()).hexdigest() if exe else None,
                    "native_document_compile":True,"repeat_edit":True,"authored_source_undo":True,"project_v2_reopen":True,
                    "stale_edit_rejected":True,"failed_edit_preserved_draft_and_history":True,"errors":errors,"artifacts":str(folder)}
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
