"""Verify local/frozen app navigation and the benchmark iframe without Java."""
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
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(exe=None, output=None):
    from playwright.sync_api import sync_playwright, expect
    from tests.conftest import make_synthetic_swf
    folder = Path(tempfile.mkdtemp(prefix="pyswf-benchmark-ui-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ)
    env.update(JAVA="nonexistent-java", PY_SWF_FFDEC="nonexistent-ffdec", PY_SWF_FFDEC_JAR="nonexistent.jar")
    for key in ("PY_SWF_API_TOKEN", "PY_SWF_JPEXS_BRIDGE", "PY_SWF_JPEXS_JAVA_BRIDGE"):
        env.pop(key, None)
    if exe:
        env["PATH"] = os.path.join(env.get("SystemRoot", "C:/Windows"), "System32")
    command = [str(Path(exe).resolve()), "--headless", "--port", str(port)] if exe else [sys.executable, "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", str(port)]
    url = f"http://127.0.0.1:{port}"
    with (folder / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=folder if exe else ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Server exited: " + (folder / "server.log").read_text(encoding="utf-8",errors="replace"))
                try:
                    with urllib.request.urlopen(url + "/api/benchmarks", timeout=15) as response:
                        reports = json.load(response)["reports"]
                    if reports:
                        break
                except OSError:
                    pass
                time.sleep(.15)
            else:
                raise RuntimeError("No benchmark snapshots available")
            errors = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width":1500,"height":1000})
                    page.on("pageerror",lambda e:errors.append(str(e)))
                    page.on("console",lambda m:errors.append(m.text) if m.type=="error" else None)
                    page.goto(url,wait_until="networkidle")
                    page.get_by_label("Abrir archivo SWF",exact=True).set_input_files({"name":"benchmark-qa.swf","mimeType":"application/octet-stream","buffer":make_synthetic_swf()})
                    expect(page.locator(".document-title")).to_contain_text("benchmark-qa.swf")
                    page.get_by_role("button",name="Benchmarks de decompilación",exact=True).click()
                    frame = page.frame_locator('iframe[title="Resultados de benchmarks de decompilación"]')
                    expect(frame.locator("h1")).to_contain_text("La misma evidencia")
                    expect(frame.locator(".notice").first).to_contain_text("No se ha establecido paridad")
                    expect(frame.locator("#matrix tr")).to_have_count(13)
                    expect(frame.locator('#matrix tr[data-engine="asv"]')).to_contain_text("Bloqueado por seguridad")
                    page.get_by_label("Elegir informe de benchmarks").select_option(reports[0]["id"])
                    expect(frame.locator("h1")).to_contain_text("Más evidencia")
                    frame.get_by_label("Filtrar motor").select_option("native")
                    frame.get_by_label("Métrica del gráfico").select_option("memory")
                    expect(frame.locator("#bar-heading")).to_have_text("Memoria pico")
                    frame.get_by_label("Filtrar resultados").fill("no-such-file-987")
                    expect(frame.locator("#table-empty")).to_be_visible()
                    frame.get_by_label("Filtrar resultados").fill("")
                    frame.get_by_label("Filtrar motor").select_option("all")
                    frame.get_by_label("Métrica del gráfico").select_option("source")
                    with page.expect_download() as download:
                        frame.get_by_role("button",name="Descargar JSON resumido").click()
                    assert json.loads(Path(download.value.path()).read_text(encoding="utf-8"))["cases"]
                    checked_operations=set()
                    for report in reports:
                        if exe or report["id"] in ("benchmark-release","benchmark-release-disassembly","benchmark-release-remote"):
                            page.get_by_label("Elegir informe de benchmarks").select_option(report["id"])
                            expect(frame.locator("h1")).to_contain_text("Más evidencia")
                            if report.get("operation")=="disassemble":
                                expect(frame.locator("thead")).to_contain_text("Desensamblado")
                            elif report["profile"]=="remote_probe":
                                expect(frame.locator("#metric")).to_have_value("request")
                                expect(frame.locator("thead")).to_contain_text("Solicitud remota")
                            else:
                                expect(frame.locator("thead")).to_contain_text("Fuente disponible")
                            checked_operations.add(report["id"])
                    page.screenshot(path=str(folder / "benchmarks-desktop.png"),full_page=True)
                    page.set_viewport_size({"width":390,"height":844})
                    page.screenshot(path=str(folder / "benchmarks-mobile.png"),full_page=True)
                    assert frame.locator("html").evaluate("n=>n.scrollWidth <= innerWidth + 1")
                    page.get_by_role("button",name="Volver al editor").click()
                    expect(page.locator(".document-title")).to_contain_text("benchmark-qa.swf")
                    page.goto(url + "/#benchmarks",wait_until="commit")
                    expect(frame.locator("h1")).to_contain_text("La misma evidencia")
                    assert not errors, errors
                except Exception:
                    page.screenshot(path=str(folder / "failure.png"),full_page=True)
                    raise
                finally:
                    browser.close()
            result={"passed":True,"java_required":False,"packaged":bool(exe),"executable_sha256":hashlib.sha256(Path(exe).read_bytes()).hexdigest() if exe else None,"reports":len(reports),"reports_checked":sorted(checked_operations),"preserved_editor":True,"download":True,"responsive":True,"errors":errors,"artifacts":str(folder)}
            if output:
                Path(output).parent.mkdir(parents=True,exist_ok=True)
                Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            return result
        finally:
            if os.name=="nt":
                subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],capture_output=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--exe",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(args.exe,args.output),ensure_ascii=False,indent=2))
