"""Browser review of the unified, offline comparison surface."""
import argparse
import json
from pathlib import Path


def verify(folder):
    from playwright.sync_api import sync_playwright,expect
    folder=Path(folder).resolve()
    data=json.loads((folder / "comparison.json").read_text(encoding="utf-8"))
    # Source/controls are inspected from the saved template before interacting.
    source=(folder / "comparison.html").read_text(encoding="utf-8")
    assert 'id="matrix"' in source and 'id="channel"' in source
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":1500,"height":1050})
        errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.on("console",lambda m:errors.append(m.text) if m.type=="error" else None)
        try:
            page.goto((folder / "comparison.html").as_uri(),wait_until="networkidle")
            expect(page.locator("#matrix tr")).to_have_count(len(data["engine_catalog"]))
            expect(page.locator('#matrix tr[data-engine="asv"]')).to_contain_text("Bloqueado por seguridad")
            expect(page.locator('#matrix tr[data-engine="asv"]')).to_contain_text("La descarga no se completó")
            for engine, version in (("sothink", "7.4"), ("trillix", "5.3.1400"), ("as3_sorcerer", "6.78")):
                row=page.locator(f'#matrix tr[data-engine="{engine}"]')
                expect(row).to_contain_text("Instalado · sin medición")
                expect(row).to_contain_text(version)
            for kind in ("source","disassemble","remote"):
                page.get_by_label("Operación de la comparación").select_option(kind)
                campaign=next((c for c in data["campaigns"] if c["id"]==data["selected"].get(kind)),None)
                rows=sum(len(c["engines"]) for c in campaign["report"]["cases"]) if campaign else 0
                expect(page.locator("#details-body tr")).to_have_count(rows)
                page.get_by_label("Buscar en la comparación").fill("missing-file-123")
                expect(page.locator("#empty")).to_be_visible()
                page.get_by_label("Buscar en la comparación").fill("")
            page.get_by_label("Operación de la comparación").select_option("source")
            page.get_by_label("Métrica de la comparación").select_option("coverage")
            assert "NaN" not in page.locator("#bars").inner_text()
            with page.expect_download() as download:
                page.get_by_role("button",name="Descargar comparación JSON").click()
            downloaded=json.loads(Path(download.value.path()).read_text(encoding="utf-8"))
            assert downloaded["kind"]=="comparison_overview"
            page.locator("#comparison").screenshot(path=str(folder / "comparison-matrix.png"))
            page.screenshot(path=str(folder / "comparison-desktop.png"),full_page=True)
            page.set_viewport_size({"width":390,"height":844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(folder / "comparison-mobile.png"),full_page=True)
            assert not errors,errors
            return {"passed":True,"tools":len(data["engine_catalog"]),"operations":["source","disassemble","remote"],"errors":errors,"folder":str(folder)}
        except Exception:
            page.screenshot(path=str(folder / "failure.png"),full_page=True)
            raise
        finally:
            browser.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("folder",type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(args.folder),indent=2))
