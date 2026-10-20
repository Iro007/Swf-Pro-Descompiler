"""Browser regression check for the local workspace and editing history.

Run against a built frontend served by FastAPI:
    .venv/Scripts/python.exe tools/smoke_workspace.py --url http://127.0.0.1:8766
"""

import argparse
from pathlib import Path
import sys
import tempfile
import re

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect, sync_playwright
from py_swf.avm1 import assemble_avm1
from py_swf.swf_parser import SWFFile, SWFTag
from tests.test_swf import _build_triangle_shape_tag


def fixture():
    swf = SWFFile()
    swf.rect = {"xmin": 0, "xmax": 8000, "ymin": 0, "ymax": 5000}
    swf.frame_count = 2
    swf.frame_rate = 12
    swf.tags = [
        _build_triangle_shape_tag(),
        SWFTag(12, assemble_avm1('push "before"\ntrace\nend')),
        SWFTag(26, b"\x06\x01\x00\x01\x00\x00"),
        SWFTag(1, b""), SWFTag(1, b""),
        SWFTag(87, b"\x05\x00\x00\x00\x00\x00" + b"A" * 4096),
    ]
    return swf.save_bytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--screenshots", type=Path, default=Path(tempfile.gettempdir()) / "pyswf-workspace-ui")
    parser.add_argument("--real-swf", type=Path)
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1536, "height": 960}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        try:
            page.goto(args.url, wait_until="networkidle")
            expect(page.get_by_role("heading", name="Abre el archivo.")).to_be_visible()
            expect(page.get_by_role("button", name="Guardar proyecto")).to_be_disabled()
            page.screenshot(path=str(args.screenshots / "01-welcome.png"))
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files({"name": "studio-demo.swf", "mimeType": "application/x-shockwave-flash", "buffer": fixture()})
            expect(page.get_by_role("heading", name="studio-demo.swf")).to_be_visible()
            expect(page.get_by_role("button", name="Deshacer", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="Rehacer", exact=True)).to_be_disabled()
            page.get_by_role("button", name="Play timeline").click()
            page.get_by_role("button", name="Pause timeline").click()
            expect(page.get_by_label("Nivel de zoom")).to_have_text("100%")
            page.get_by_role("button", name="Ampliar zoom", exact=True).click()
            expect(page.get_by_label("Nivel de zoom")).to_have_text("125%")
            page.get_by_role("button", name="Restablecer zoom", exact=True).click()
            page.screenshot(path=str(args.screenshots / "02-workspace.png"))
            page.get_by_role("button", name="Historial y snapshots").click()
            page.get_by_label("Nuevo snapshot").fill("Original")
            page.get_by_role("button", name="Crear snapshot", exact=True).click()
            expect(page.locator(".snapshot-card").get_by_text("Original", exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("DoAction")
            page.locator(".tree-tag").filter(has_text="DoAction").click()
            expect(page.locator(".code-lines")).to_contain_text('trace("before")')
            page.get_by_role("button", name="P-code", exact=True).click()
            editor = page.get_by_label("Editor P-code", exact=True)
            expect(editor).to_have_value(re.compile("before"))
            editor.fill(editor.input_value().replace("before", "after"))
            page.get_by_role("button", name="Ensamblar", exact=True).click()
            expect(page.get_by_role("button", name="Deshacer", exact=True)).to_be_enabled()
            expect(page.locator(".code-lines")).to_contain_text('trace("after")')
            page.get_by_role("button", name="Comparar", exact=True).click()
            expect(page.get_by_role("region", name="Comparación de snapshot")).to_contain_text("1 tags modificados")
            page.get_by_role("button", name="Deshacer", exact=True).click()
            expect(page.locator(".code-lines")).to_contain_text('trace("before")')
            expect(page.get_by_role("button", name="Deshacer", exact=True)).to_be_disabled()
            page.get_by_role("button", name="Rehacer", exact=True).click()
            expect(page.locator(".code-lines")).to_contain_text('trace("after")')
            page.get_by_role("button", name="Editar fuente", exact=True).click()
            page.get_by_label("Editor ActionScript", exact=True).fill('trace("source");')
            page.get_by_role("button", name="Recompilar", exact=True).click()
            expect(page.locator(".code-lines")).to_contain_text('trace("source")')
            page.get_by_label("Motor de decompilación", exact=True).select_option("compare")
            expect(page.get_by_role("region", name="Python nativo")).to_contain_text('trace("source")')
            expect(page.get_by_role("region", name="JPEXS")).to_be_visible()
            page.screenshot(path=str(args.screenshots / "03-engine-comparison.png"))
            page.get_by_label("Buscar recursos", exact=True).fill("DefineBinaryData")
            page.locator(".tree-tag").filter(has_text="DefineBinaryData").click()
            page.get_by_role("button", name="Hexadecimal", exact=True).click()
            page.get_by_role("button", name="Página siguiente", exact=True).click()
            byte = page.get_by_role("button", name="Editar byte 1024", exact=True)
            byte.click()
            value = page.get_by_label("Valor hexadecimal del byte 1024", exact=True)
            value.fill("42")
            value.press("Enter")
            page.get_by_role("button", name="Aplicar bytes", exact=True).click()
            expect(page.get_by_role("button", name="Deshacer", exact=True)).to_be_enabled()
            expect(page.get_by_role("button", name="Página siguiente", exact=True)).to_be_visible()
            page.get_by_role("button", name="Restaurar", exact=True).click()
            page.get_by_role("button", name="Confirmar restauración", exact=True).click()
            page.get_by_label("Buscar recursos", exact=True).fill("DoAction")
            page.locator(".tree-tag").filter(has_text="DoAction").click()
            expect(page.locator(".code-lines")).to_contain_text('trace("before")')
            with page.expect_download() as pending:
                page.get_by_role("button", name="Guardar proyecto", exact=True).click()
            project = pending.value
            project_path = args.screenshots / project.suggested_filename
            project.save_as(project_path)
            page.get_by_label("Abrir proyecto", exact=True).set_input_files(str(project_path))
            expect(page.get_by_role("heading", name="studio-demo.swf")).to_be_visible()
            page.get_by_role("button", name="Historial y snapshots").click()
            expect(page.locator(".snapshot-card").get_by_text("Original", exact=True)).to_be_visible()
            page.get_by_role("button", name="Exportar recursos", exact=True).click()
            expect(page.get_by_role("dialog", name="Exportar recursos", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog")).to_have_count(0)
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="Ocultar inspector", exact=True).click()
            page.get_by_role("button", name="Biblioteca de recursos", exact=True).click()
            expect(page.get_by_label("Buscar recursos", exact=True)).to_be_visible()
            page.screenshot(path=str(args.screenshots / "04-mobile.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Horizontal page overflow"
            if args.real_swf:
                page.set_viewport_size({"width": 1536, "height": 960})
                page.get_by_label("Abrir archivo SWF", exact=True).set_input_files(str(args.real_swf))
                expect(page.get_by_role("heading", name=args.real_swf.name, exact=True)).to_be_visible()
                page.get_by_label("Buscar recursos", exact=True).fill("DoABC")
                page.locator(".tree-tag").filter(has_text="DoABC").first.click()
                expect(page.locator(".code-lines")).to_contain_text("package", timeout=20000)
                expect(page.get_by_role("button", name="Editar fuente", exact=True)).to_be_disabled()  # class outline
                page.screenshot(path=str(args.screenshots / "05-real-as3.png"))
                page.get_by_label("Sección de fuente", exact=True).select_option("1")
                expect(page.locator(".code-lines")).to_be_visible()
                page.get_by_role("button", name="P-code", exact=True).click()
                expect(page.get_by_label("Editor P-code", exact=True)).to_be_visible()
                page.get_by_label("Buscar recursos", exact=True).fill("TestClass")
                page.get_by_role("button", name="Contenido AS / texto", exact=True).click()
                expect(page.locator(".search-result").first).to_be_visible(timeout=20000)
            assert not errors, f"Browser errors: {errors}"
            print("PASS: open, search, playback, edit, snapshots, compare, undo/redo, restore, project, export dialog, mobile")
            print(f"Screenshots: {args.screenshots}")
        except Exception:
            page.screenshot(path=str(args.screenshots / "failure.png"))
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
