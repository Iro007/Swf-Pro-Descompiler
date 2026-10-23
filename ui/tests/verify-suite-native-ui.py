"""Packaged application + real Python worker, driven through the explicitly enabled DevTools (CDP) port.

No bridge mocks. Start the application with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9335
and `--open <small AS3 fixture>`, then run this file with the engines/pyswf .venv (Playwright installed).
It uses the same selectors as verify-suite-ui.py (the mocked-host contract test of the single-window shell).

The bridge calls below (`__TAURI_INTERNALS__.invoke`) belong to the Tauri host; when the host changes only
`invoke` here has to change, never the interface selectors.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument("--cdp", default="http://127.0.0.1:9335")
parser.add_argument("--original", required=True, type=Path)
args = parser.parse_args()
output = Path(__file__).resolve().parents[2] / "reports"
output.mkdir(exist_ok=True)
original_hash = hashlib.sha256(args.original.read_bytes()).hexdigest()
NUMERIC_RETURN = r"\breturn\s+(?:(?:int|uint|Number)\s*\(\s*|\(\s*)?([0-9]+)\s*\)?\s*;"


def rpc(page, operation, arguments=None):
    return page.evaluate("async([operation,args])=>await window.__TAURI_INTERNALS__.invoke('suite_rpc',{operation,args})", [operation, arguments or {}])


def modified_source(source):
    numeric = re.search(NUMERIC_RETURN, source)
    if numeric:
        start, end = numeric.span(1)
        return source[:start] + str(int(numeric.group(1)) + 1) + source[end:]
    raise RuntimeError("The selected source has no numeric-return literal to mutate")


def classes(nodes):
    for node in nodes:
        if node["kind"] == "class" and node.get("members"):
            yield node
        yield from classes(node.get("children", []))


with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp(args.cdp)
    pages = [page for context in browser.contexts for page in context.pages]
    if not pages:
        raise RuntimeError("No WebView2 page is exposed by the packaged application")
    page = next((candidate for candidate in pages if "localhost" in candidate.url), pages[0])
    page.set_default_timeout(15000)
    console, errors = [], []
    page.on("console", lambda message: console.append({"type": message.type, "text": message.text}))
    page.on("pageerror", lambda error: errors.append(str(error)))
    evidence = {"scope": "Actual packaged application + Python worker, no bridge mocks", "passed": False, "original": str(args.original),
                "original_sha256": original_hash, "page_url": page.url, "checks": [], "console": console, "page_errors": errors}
    try:
        expect(page.get_by_role("tree", name="Elementos del SWF")).to_be_visible()
        expect(page.get_by_text("Longitud declarada")).to_be_visible()
        page.screenshot(path=str(output / "ui-shell-native-startup.png"))
        evidence["checks"].append("opened file shows its header and tree")

        tree = rpc(page, "tag_tree")
        target = None
        for node in classes(tree["nodes"]):
            for member in node["members"]:
                observed = rpc(page, "decompile", {"unit_id": member["unit_id"], "engine": "python"})
                if observed.get("editable") and re.search(NUMERIC_RETURN, observed.get("source") or ""):
                    target = (node, member, observed)
                    break
            if target:
                break
        if target is None:
            raise RuntimeError("The small fixture has no editable numeric-return PySWF method")
        node, member, observed = target

        page.get_by_label("Motor de descompilación", exact=True).select_option("python")
        page.get_by_label("Filtrar el árbol").fill(node["label"])
        page.locator("[role=treeitem]").filter(has=page.locator(".tree-label", has_text=re.compile("^" + re.escape(node["label"]) + "$"))).first.click()
        page.get_by_role("button", name=member["name"], exact=False).first.click()
        editor = page.locator("[data-testid=script-source] .cm-content")
        expect(editor).to_have_text(observed["source"])
        expect(page.get_by_test_id("script-status")).to_have_text("Listo para editar")
        before = rpc(page, "state")
        assert before["filename"] == args.original.name, "The visible session is not the authorized small fixture"
        assert before["revision"] == original_hash, "The fixture must start at its original revision"
        evidence["before"] = before

        editor.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("function {")
        page.get_by_role("button", name="Aplicar y validar").click()
        expect(page.locator(".toast.error")).to_be_visible()
        expect(editor).to_contain_text("function {")
        assert rpc(page, "state")["revision"] == before["revision"], "Rejected edit changed the session"
        evidence["checks"].append("compiler rejection keeps the draft and the committed revision")

        page.keyboard.press("Control+A")
        page.keyboard.type(modified_source(observed["source"]).replace("\n", "\n"))
        page.get_by_role("button", name="Aplicar y validar").click()
        expect(page.locator(".toast.ok")).to_contain_text("Cambio validado")
        expect(page.get_by_role("button", name="Deshacer").first).to_be_enabled()
        edited = rpc(page, "state")
        assert edited["history"]["undo"], edited
        evidence["edited"] = edited
        evidence["checks"].append("source edit applies through the real worker")
        page.screenshot(path=str(output / "ui-shell-native-editor.png"))

        page.get_by_role("button", name="Deshacer").first.click()
        expect(page.get_by_role("button", name="Rehacer").first).to_be_enabled()
        assert rpc(page, "state")["revision"] == before["revision"]
        page.get_by_role("button", name="Rehacer").first.click()
        expect(page.get_by_role("button", name="Deshacer").first).to_be_enabled()
        assert rpc(page, "state")["revision"] == edited["revision"]
        evidence["checks"].append("undo and redo recover exact revision hashes without a session reset")

        page.get_by_role("menuitem", name="Herramientas").click()
        page.get_by_role("menuitem", name="Motores…").click()
        expect(page.get_by_role("heading", name="Motores y herramientas")).to_be_visible()
        evidence["engines"] = rpc(page, "engines")
        page.screenshot(path=str(output / "ui-shell-native-engines.png"))
        page.keyboard.press("Escape")
        evidence["checks"].append("real engine catalogue renders in the packaged interface")

        assert hashlib.sha256(args.original.read_bytes()).hexdigest() == original_hash
        assert not errors, errors
        evidence["checks"].append("original SWF remains byte-identical")
        evidence["passed"] = True
    except Exception as error:
        evidence["error"] = str(error)
        page.screenshot(path=str(output / "ui-shell-native-failure.png"))
        raise
    finally:
        (output / "ui-shell-native-verification.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"passed": evidence["passed"], "checks": evidence["checks"], "report": str(output / "ui-shell-native-verification.json")}, ensure_ascii=False))
        # The context manager stops the driver connection. Do not send Browser.close
        # to the user-owned WebView2 instance.
