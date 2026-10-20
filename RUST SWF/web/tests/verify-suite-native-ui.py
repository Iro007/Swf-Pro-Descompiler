"""Actual packaged Tauri/WebView2 UI verification through the explicitly enabled CDP port.

No bridge mocks. Start the app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
--remote-debugging-port=9335 and --open a small AS3 fixture before running.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser()
parser.add_argument("--cdp",default="http://127.0.0.1:9335")
parser.add_argument("--original",required=True,type=Path)
args=parser.parse_args()
output=Path(__file__).resolve().parents[2]/"reports"
original_hash=hashlib.sha256(args.original.read_bytes()).hexdigest()
NUMERIC_RETURN=r"\breturn\s+(?:(?:int|uint|Number)\s*\(\s*|\(\s*)?([0-9]+)\s*\)?\s*;"

def state_of(page):
    return page.evaluate("async()=>await window.__TAURI_INTERNALS__.invoke('suite_rpc',{operation:'state',args:{}})")

def modified_source(source):
    numeric=re.search(NUMERIC_RETURN,source)
    if numeric:
        start,end=numeric.span(1)
        return source[:start]+str(int(numeric.group(1))+1)+source[end:]
    raise RuntimeError("The selected source has no numeric-return literal to mutate")

with sync_playwright() as playwright:
    browser=playwright.chromium.connect_over_cdp(args.cdp)
    pages=[page for context in browser.contexts for page in context.pages]
    if not pages:
        raise RuntimeError("No WebView2 page is exposed by the packaged application")
    page=next((candidate for candidate in pages if "localhost" in candidate.url),pages[0])
    page.set_default_timeout(15000)
    console=[]
    errors=[]
    page.on("console",lambda message:console.append({"type":message.type,"text":message.text}))
    page.on("pageerror",lambda error:errors.append(str(error)))
    evidence={"scope":"Actual packaged Tauri/WebView2 + Python worker, no bridge mocks","passed":False,"original":str(args.original),"original_sha256":original_hash,"page_url":page.url,"checks":[],"console":console,"page_errors":errors}
    try:
        # Capture live DOM before selecting or submitting anything.
        evidence["initial_dom"]=page.locator("body").inner_text()
        page.screenshot(path=str(output/"ui-suite-native-startup.png"))
        expect(page.get_by_role("button",name="Mesa de trabajo",exact=True)).to_be_visible()
        if not page.get_by_role("dialog").is_visible():
            page.get_by_role("button",name="Mesa de trabajo",exact=True).click()
        expect(page.get_by_role("dialog")).to_be_visible()
        page.get_by_label("Motor de descompilación",exact=True).select_option("python")
        expect(page.locator(".suite-unit-list .suite-scroll>button").first).to_be_visible()
        metadata=page.evaluate("async()=>await window.__TAURI_INTERNALS__.invoke('suite_rpc',{operation:'scripts',args:{query:'',offset:0,limit:10}})")
        source=None
        for unit in metadata["items"]:
            observed=page.evaluate("async(unit)=>await window.__TAURI_INTERNALS__.invoke('suite_rpc',{operation:'decompile',args:{unit_id:unit.id,engine:'python'}})",unit)
            if not observed.get("editable") or not re.search(NUMERIC_RETURN,observed.get("source") or ""):
                continue
            page.locator(".suite-unit-list .suite-scroll>button").filter(has_text=unit["id"]).first.click()
            source_button=page.get_by_role("button",name="Fuente",exact=True)
            expect(source_button).to_be_enabled()
            source_button.click()
            editor=page.get_by_role("textbox",name=f"Editor ActionScript de {unit['name']}")
            expect(editor).to_have_value(observed["source"])
            assert editor.get_attribute("readonly") is None
            source=editor.input_value()
            break
        if source is None:
            raise RuntimeError("The small fixture has no editable numeric-return Python unit in the first 10 units")
        before=state_of(page)
        assert before["filename"]==args.original.name,"The visible session is not the authorized small fixture"
        assert before["revision"]==original_hash,"The fixture must start at its original revision"
        evidence["before"]=before
        editor.fill("function {")
        page.get_by_role("button",name="Aplicar y validar",exact=True).click()
        expect(page.locator(".suite-message.error")).to_be_visible()
        expect(editor).to_have_value("function {")
        assert state_of(page)["revision"]==before["revision"],"Rejected edit changed the session"
        evidence["checks"].append("compiler rejection preserves draft and committed revision")
        editor.fill(modified_source(source))
        page.get_by_role("button",name="Aplicar y validar",exact=True).click()
        expect(page.locator(".suite-message").filter(has_text="Cambio validado")).to_be_visible()
        expect(page.get_by_role("button",name="Deshacer cambio aplicado")).to_be_enabled()
        edited=state_of(page)
        assert edited["history"]["undo"],edited
        evidence["edited"]=edited
        evidence["checks"].append("source edit applies through the actual native worker")
        page.screenshot(path=str(output/"ui-suite-native-editor.png"))
        page.get_by_role("button",name="Deshacer cambio aplicado").click()
        expect(page.get_by_role("button",name="Rehacer cambio aplicado")).to_be_enabled()
        restored=state_of(page)
        assert restored["revision"]==before["revision"],(restored,before)
        page.get_by_role("button",name="Rehacer cambio aplicado").click()
        expect(page.get_by_role("button",name="Deshacer cambio aplicado")).to_be_enabled()
        assert state_of(page)["revision"]==edited["revision"]
        evidence["checks"].append("undo and redo recover exact revision hashes without session reset")
        page.get_by_role("button",name="Herramientas",exact=True).click()
        expect(page.get_by_role("heading",name="Motores y herramientas")).to_be_visible()
        evidence["engines"]=page.evaluate("async()=>await window.__TAURI_INTERNALS__.invoke('suite_rpc',{operation:'engines',args:{}})")
        page.screenshot(path=str(output/"ui-suite-native-tools.png"))
        evidence["checks"].append("actual engine catalog renders in the packaged UI")
        page.get_by_role("button",name="Timeline",exact=True).click()
        page.get_by_role("button",name="Inspeccionar cuadro",exact=True).click()
        expect(page.get_by_text(re.compile("objetos en la lista de visualización"))).to_be_visible()
        page.screenshot(path=str(output/"ui-suite-native-timeline.png"))
        evidence["checks"].append("static frame inspection completes without ActionScript execution")
        page.get_by_role("button",name="Editor y comparación",exact=True).click()
        page.get_by_role("button",name="Deshacer cambio aplicado").click()
        expect(page.get_by_role("button",name="Rehacer cambio aplicado")).to_be_enabled()
        assert hashlib.sha256(args.original.read_bytes()).hexdigest()==original_hash
        assert not errors,errors
        evidence["checks"].append("original SWF remains byte-identical")
        evidence["passed"]=True
    except Exception as error:
        evidence["error"]=str(error)
        page.screenshot(path=str(output/"ui-suite-native-failure.png"))
        raise
    finally:
        (output/"ui-suite-native-verification.json").write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding="utf-8")
        print(json.dumps({"passed":evidence["passed"],"checks":evidence["checks"],"report":str(output/"ui-suite-native-verification.json")},ensure_ascii=False))
        # The context manager stops the driver connection. Do not send Browser.close
        # to the user-owned WebView2 instance.
