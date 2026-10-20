"""Verify native open/edit/save/reopen through the actual HTTP API and UI.

Optionally launches the frozen exe with Java disabled and a minimal PATH.
The output report describes what was tested, not general JPEXS parity.
"""

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from PIL import Image

from py_swf.avm1 import assemble_avm1, disassemble_avm1
from py_swf.avm2 import ABCFile
from py_swf.bytecode_validation import analyze_method
from py_swf.project import SwfProject
from py_swf.resources import replace_image
from py_swf.swf_parser import SWFFile, SWFTag
from tests.native_fixtures import as3_tag, swf_with
from tests.native_control_vm import run_bytes


def image_bytes(color):
    output = io.BytesIO()
    Image.new("RGBA", (4, 3), color).save(output, "PNG")
    return output.getvalue()


def payload(language, signature):
    script = SWFTag(12, assemble_avm1('push "original"\ntrace\nend')) if language == "as2" else as3_tag()
    image = replace_image(SWFTag(36, b"\x07\x00\x05" + b"\x00" * 5), image_bytes((255, 0, 0, 255)), "png")
    tags = [script, image]
    if language == "as3":
        tags.insert(0, SWFTag(69, b"\x08\x00\x00\x00"))  # AS3 FileAttributes first
    return swf_with(tags, signature)


def require(response):
    assert response.is_success, f"{response.status_code}: {response.text[:1000]}"
    return response


def run_http(url, folder):
    reports = []
    with httpx.Client(base_url=url, timeout=30) as client:
        assert require(client.get("/api/files/engines/status")).json()["default"] == "native"
        for language in ("as2", "as3"):
            for signature in ("FWS", "CWS", "ZWS"):
                start = time.perf_counter()
                original = payload(language, signature)
                name = f"native-{language}-{signature}.swf"
                sid = require(client.post("/api/files", files={"file": (name, original)})).json()["session_id"]
                endpoint = f"/api/files/{sid}"
                script_index, image_index = (0, 1) if language == 'as2' else (1, 2)
                require(client.post(endpoint + "/snapshot", json={"name": "Original"}))
                source = 'var n:int = 0; for(var i:int = 0; i < 3; i++) { try { switch(i) { case 0: n += 1; break; default: n += 2; } trace("native-saved 🌍"); } catch(e) { trace(e); } finally { n++; } }'
                if language == "as3":
                    source += " return score + n;"
                edit = require(client.post(endpoint + f"/tags/{script_index}/compile-source", json={"source": source, "body_index": 0 if language == "as2" else 1})).json()
                assert edit["engine"] == "native"
                require(client.post(endpoint + f"/tags/{image_index}/replace", files={"file": ("new.png", image_bytes((0, 128, 255, 160)))}))
                saved = require(client.get(endpoint + "/download")).content
                path = folder / name
                path.write_bytes(saved)
                reopened_sid = require(client.post("/api/files", files={"file": (name, path.read_bytes())})).json()["session_id"]
                reopened_endpoint = f"/api/files/{reopened_sid}"
                source_result = require(client.get(reopened_endpoint + f"/tags/{script_index}/decompile")).json()
                assert source_result["engine"] == "native"
                result_image = require(client.get(reopened_endpoint + f"/tags/{image_index}/export/image"))
                pixel = Image.open(io.BytesIO(result_image.content)).getpixel((0, 0))
                assert pixel[2] == 255 and pixel[3] == 160 and abs(pixel[1] - 128) <= 1
                swf = SWFFile()
                swf.read_bytes(path.read_bytes())
                assert swf.signature == signature
                limits = None
                if language == "as3":
                    abc = ABCFile()
                    abc.parse(swf.tags[script_index].parse_doabc()[2])
                    limits = analyze_method(abc, abc.method_bodies[1])
                    assert "native-saved 🌍" in abc.constant_pool.strings
                    assert abc.method_bodies[1].exceptions
                    execution = run_bytes(abc.method_bodies[1].code, language, abc=abc, body=abc.method_bodies[1])
                    assert execution.value == 18
                else:
                    assert "native-saved 🌍" in disassemble_avm1(swf.tags[0].data)
                    execution = run_bytes(swf.tags[0].data, language)
                assert not execution.raised and execution.traces == ["native-saved 🌍"] * 3
                archive = require(client.get(endpoint + "/project")).content
                project_path = folder / (name + ".pyswfproj")
                project_path.write_bytes(archive)
                project = SwfProject.from_archive(project_path)
                assert project.original == original and project.working == saved
                require(client.post(endpoint + "/undo"))
                require(client.post(endpoint + "/redo"))
                assert require(client.get(endpoint + "/download")).content == saved
                bad = client.post(endpoint + f"/tags/{script_index}/compile-source", json={"source": "function nested() {}", "body_index": 0 if language == "as2" else 1})
                assert bad.status_code == 422
                assert require(client.get(endpoint + "/download")).content == saved
                reports.append({"language": language, "signature": signature, "open_edit_save_reopen": True,
                                "image_replacement": True, "project_original_preserved": True, "undo_redo": True,
                                "failure_rollback": True, "max_stack": limits.max_stack if limits else None,
                                "syntax": ["for", "switch", "try", "catch", "finally"], "reference_execution": True,
                                "elapsed_ms": round((time.perf_counter() - start) * 1000)})
                print(f"PASS HTTP {language}/{signature}", flush=True)
        legacy = payload("as2", "FWS")
        legacy = legacy[:3] + bytes([6]) + legacy[4:]
        sid = require(client.post("/api/files", files={"file": ("legacy.swf", legacy)})).json()["session_id"]
        endpoint = f"/api/files/{sid}"
        rejected = client.post(endpoint + "/tags/0/compile-source", json={"source": "try {} finally {}"})
        assert rejected.status_code == 422 and "version 7" in rejected.text
        assert require(client.get(endpoint + "/download")).content == legacy
        assert require(client.get(endpoint + "/history")).json()["undo"] == 0
        reports.append({"language": "as2", "signature": "FWS", "version": 6,
                        "incompatible_ActionTry_rejected": True, "original_preserved": True, "history_unchanged": True})
        print("PASS HTTP legacy SWF 6 exception edit rejected without mutation", flush=True)
    return reports


def run_browser(url, folder):
    from playwright.sync_api import sync_playwright, expect
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        expect.set_options(timeout=30000)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        try:
            page.goto(url, wait_until="networkidle")
            expect(page.get_by_label("Motor predeterminado")).to_have_value("native")
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files({"name": "browser-native.swf", "mimeType": "application/x-shockwave-flash", "buffer": payload("as3", "CWS")})
            expect(page.get_by_role("heading", name="browser-native.swf", exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("DoABC")
            page.locator(".tree-tag").filter(has_text="DoABC").first.click()
            page.get_by_label("Sección de fuente", exact=True).select_option("2")
            expect(page.get_by_role("button", name="Editar fuente", exact=True)).to_be_enabled()
            page.get_by_role("button", name="Editar fuente", exact=True).click()
            page.get_by_label("Editor ActionScript", exact=True).fill('var n:int=0; for(var i:int=0; i<2; i++) {try {switch(i) {case 0: n++; break; default: throw "browser-saved";}} catch(e) {trace(e);} finally {n++;}} return n + score;')
            page.get_by_role("button", name="Recompilar", exact=True).click()
            expect(page.get_by_role("button", name="Deshacer", exact=True)).to_be_enabled()
            page.get_by_label("Sección de fuente", exact=True).select_option("2")
            expect(page.get_by_role("button", name="Editar fuente", exact=True)).to_be_enabled()
            expect(page.locator(".code-lines")).to_contain_text("for (")
            expect(page.locator(".code-lines")).to_contain_text("switch (")
            expect(page.locator(".code-lines")).to_contain_text("finally {")
            page.screenshot(path=str(folder / "native-as3-reconstructed.png"))
            page.get_by_role("button", name="P-code", exact=True).click()
            expect(page.get_by_label("Editor P-code", exact=True)).to_contain_text(".exception")
            assert "browser-saved" in page.get_by_label("Editor P-code", exact=True).input_value()
            page.screenshot(path=str(folder / "native-as3-editor.png"))
            with page.expect_download() as pending:
                page.get_by_role("button", name="Exportar SWF", exact=True).click()
            path = folder / "browser-native.saved.swf"
            pending.value.save_as(path)
            swf = SWFFile()
            swf.read_bytes(path.read_bytes())
            abc = ABCFile()
            abc.parse(swf.tags[1].parse_doabc()[2])
            execution = run_bytes(abc.method_bodies[1].code, "as3", abc=abc, body=abc.method_bodies[1])
            assert not execution.raised and execution.value == 13 and execution.traces == ["browser-saved"]
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files(str(path))
            expect(page.get_by_role("heading", name=path.name, exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("DoABC")
            page.locator(".tree-tag").filter(has_text="DoABC").first.click()
            page.get_by_label("Sección de fuente", exact=True).select_option("2")
            page.get_by_role("button", name="Editar fuente", exact=True).click()
            assert "try {" in page.get_by_label("Editor ActionScript", exact=True).input_value()
            page.get_by_label("Editor ActionScript", exact=True).fill('try {return score + 5;} finally {trace("second-source");}')
            with page.expect_response(lambda response: response.url.endswith(("/compile-source","/compile-document")) and response.request.method == "POST") as response:
                page.get_by_role("button", name="Recompilar", exact=True).click()
            assert response.value.status == 200
            page.get_by_label("Sección de fuente", exact=True).select_option("2")
            page.get_by_role("button", name="P-code", exact=True).click()
            expect(page.get_by_label("Editor P-code", exact=True)).to_be_visible()
            assert "#" in page.get_by_label("Editor P-code", exact=True).input_value()
            assert ".exception" in page.get_by_label("Editor P-code", exact=True).input_value()
            assert "second-source" in page.get_by_label("Editor P-code", exact=True).input_value()
            with page.expect_download() as pending:
                page.get_by_role("button", name="Exportar SWF", exact=True).click()
            second_path = folder / "browser-as3.second-edit.swf"
            pending.value.save_as(second_path)
            swf.read_bytes(second_path.read_bytes())
            abc = ABCFile()
            abc.parse(swf.tags[1].parse_doabc()[2])
            execution = run_bytes(abc.method_bodies[1].code, "as3", abc=abc, body=abc.method_bodies[1])
            assert not execution.raised and execution.value == 15 and execution.traces == ["second-source"]

            # Exercise the same visible open/edit/export/reopen flow for AVM1.
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files({"name": "browser-as2.swf", "mimeType": "application/x-shockwave-flash", "buffer": payload("as2", "CWS")})
            expect(page.get_by_role("heading", name="browser-as2.swf", exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("DoAction")
            page.locator(".tree-tag").filter(has_text="DoAction").first.click()
            page.get_by_role("button", name="Editar fuente", exact=True).click()
            page.get_by_label("Editor ActionScript", exact=True).fill('var n=0; for(var i=0; i<2; i++) {try {switch(i) {case 0: n++; break; default: throw "browser-as2-saved";}} catch(e) {trace(e);} finally {n++;}} return n;')
            with page.expect_response(lambda response: response.url.endswith(("/compile-source","/compile-document")) and response.request.method == "POST") as response:
                page.get_by_role("button", name="Recompilar", exact=True).click()
            assert response.value.status == 200
            expect(page.get_by_role("button", name="Editar fuente", exact=True)).to_be_enabled()
            expect(page.locator(".code-lines")).to_contain_text("for (")
            expect(page.locator(".code-lines")).to_contain_text("finally {")
            page.screenshot(path=str(folder / "native-as2-reconstructed.png"))
            page.get_by_role("button", name="P-code", exact=True).click()
            expect(page.get_by_label("Editor P-code", exact=True)).to_contain_text("try 1")
            with page.expect_download() as pending:
                page.get_by_role("button", name="Exportar SWF", exact=True).click()
            as2_path = folder / "browser-as2.saved.swf"
            pending.value.save_as(as2_path)
            swf = SWFFile()
            swf.read_bytes(as2_path.read_bytes())
            execution = run_bytes(swf.tags[0].data, "as2")
            assert not execution.raised and execution.value == 3 and execution.traces == ["browser-as2-saved"]
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files(str(as2_path))
            expect(page.get_by_role("heading", name=as2_path.name, exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("DoAction")
            page.locator(".tree-tag").filter(has_text="DoAction").first.click()
            page.get_by_role("button", name="P-code", exact=True).click()
            expect(page.get_by_label("Editor P-code", exact=True)).to_contain_text("browser-as2-saved")
            page.screenshot(path=str(folder / "native-as2-editor.png"))
            assert not errors, errors
            print("PASS BROWSER native AS2/AS3 edit/export/reopen and AS3 source replacement", flush=True)
            return True
        except Exception:
            page.screenshot(path=str(folder / "native-failure.png"))
            raise
        finally:
            browser.close()


def run_abc47_http(url,folder):
    """Real-file open/export/reopen plus one bounded edit retaining raw float bits."""
    from tests.test_abc_extended_pools import float_edit_tag
    from tools.diagnose_swf_roundtrip import uncompressed_payload
    import zipfile
    reports=[]
    from py_swf.corpus import corpus_root
    corpus=corpus_root()
    with httpx.Client(base_url=url,timeout=30) as client:
        for relative,expected in (("as3_ctor_field_init/bin/as3_ctor_field_init.swf",22),
                                  ("as3_ctor_init_after_local/bin/as3_ctor_init_after_local.swf",6),
                                  ("as3_foreach_convert_i/bin/as3_foreach_convert_i.swf",11),
                                  ("float/bin/float.air_harman.swf",4)):
            original=(corpus / relative).read_bytes()
            swf=SWFFile()
            swf.read_bytes(original)
            sid=require(client.post("/api/files",files={"file":(Path(relative).name,original)})).json()["session_id"]
            total=0
            for index,tag in enumerate(swf.tags):
                if tag.tag_type not in (72,82):
                    continue
                listing=require(client.get(f"/api/files/{sid}/tags/{index}/disassemble")).json()
                total+=len(listing["scripts"])
                abc=ABCFile()
                abc.parse(tag.parse_doabc()[2])
                assert (abc.major_version,abc.minor_version)==(47,16)
            assert total==expected,(relative,total)
            saved=require(client.get(f"/api/files/{sid}/download")).content
            assert saved[:8]==original[:8],relative
            assert uncompressed_payload(saved)==uncompressed_payload(original),relative
            reopened=require(client.post("/api/files",files={"file":("reopened.swf",saved)})).json()["session_id"]
            assert require(client.get(f"/api/files/{reopened}/download")).content==saved
            archive=require(client.get(f"/api/files/{sid}/project")).content
            with zipfile.ZipFile(io.BytesIO(archive)) as project:
                assert project.read("original.swf")==original
            reports.append({"case":relative,"units":total,"open_export_reopen":True,"original_preserved":True,
                            "container_identical":saved==original,"uncompressed_payload_identical":True,"behavior":"not_run"})

        original=swf_with([float_edit_tag()],"CWS")
        sid=require(client.post("/api/files",files={"file":("float-edit.swf",original)})).json()["session_id"]
        endpoint=f"/api/files/{sid}"
        require(client.post(endpoint+"/tags/0/compile-source",json={"body_index":1,"source":"return score + 7;"}))
        saved=require(client.get(endpoint+"/download")).content
        swf=SWFFile()
        swf.read_bytes(saved)
        abc=ABCFile()
        abc.parse(swf.tags[0].parse_doabc()[2])
        assert (abc.major_version,abc.minor_version)==(47,16)
        assert abc.constant_pool.float_raw[2]==bytes.fromhex("0100807f")
        assert abc.serialize()==swf.tags[0].parse_doabc()[2]
        reopened=require(client.post("/api/files",files={"file":("float-edit.saved.swf",saved)})).json()["session_id"]
        assert require(client.get(f"/api/files/{reopened}/download")).content==saved
        require(client.post(endpoint+"/undo"))
        assert require(client.get(endpoint+"/download")).content==original
        require(client.post(endpoint+"/redo"))
        assert require(client.get(endpoint+"/download")).content==saved
        invalid=client.post(endpoint+"/tags/0/assemble",json={"body_index":1,"code":"pop\nreturnvoid"})
        assert invalid.status_code==422
        assert require(client.get(endpoint+"/download")).content==saved
        (folder / "abc47-float-edit.saved.swf").write_bytes(saved)
        reports.append({"case":"synthetic_abc47_raw_nan","source_edit_save_reopen":True,"float_raw_bits_preserved":True,"undo_redo":True,"failure_rollback":True,"behavior":"not_run"})
    print("PASS HTTP ABC 47.16 real files and raw float edit preservation",flush=True)
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--real-functions", action="store_true", help="Verify trusted local corpus closures and with in the packaged UI")
    parser.add_argument("--abc47",action="store_true",help="Verify real ABC 47.16 files and raw-pool preservation after an edit")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    process = None
    folder = Path(tempfile.mkdtemp(prefix="pyswf-native-verification-"))
    try:
        if args.exe:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            args.url = f"http://127.0.0.1:{port}"
            env = dict(os.environ)
            env.update({"JAVA": "nonexistent-java", "PY_SWF_FFDEC": "nonexistent-ffdec", "PY_SWF_FFDEC_JAR": "nonexistent.jar", "PATH": os.path.join(env.get("SystemRoot", "C:/Windows"), "System32")})
            for key in ("PY_SWF_API_TOKEN", "PY_SWF_JPEXS_JAVA_BRIDGE", "PY_SWF_FFDEC_LIB_JAR", "PY_SWF_JPEXS_BRIDGE"):
                env.pop(key, None)
            log = (folder / "exe.log").open("w", encoding="utf-8")
            process = subprocess.Popen([str(args.exe.resolve()), "--headless", "--port", str(port)], env=env, cwd=folder, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.flush()
                    raise RuntimeError("Executable exited: " + (folder / "exe.log").read_text(encoding="utf-8", errors="replace"))
                try:
                    if httpx.get(args.url + "/api/files/engines/status", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.15)
            else:
                raise RuntimeError("Executable startup timed out")
        report = {"engine": "native", "java_required": False, "executable": str(args.exe.resolve()) if args.exe else None,
                  "executable_sha256": hashlib.sha256(args.exe.read_bytes()).hexdigest() if args.exe else None,
                  "cases": run_http(args.url, folder), "browser": run_browser(args.url, folder) if args.browser else False,
                  "artifacts": str(folder)}
        if args.real_functions:
            from tools.verify_native_functions import browser_verify
            from py_swf.corpus import corpus_root
            report["real_functions_browser"] = browser_verify(args.url, corpus_root(), folder)
        if args.abc47:
            report["abc47_http"]=run_abc47_http(args.url,folder)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if process:
            if os.name == "nt":
                # The one-file bootloader starts a child process. Stop that
                # exact tree too so a verification run cannot lock the exe.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            log.close()


if __name__ == "__main__":
    main()
