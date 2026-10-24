"""Screenshots of the Benchmarks window in the real application: Electron started from source, the real PySWF worker
answering `bench_results` from reports/bench, no mocks. Light and dark, wide and narrow, with a tooltip and with the table open.

    engines/pyswf/.venv/Scripts/python ui/tests/capture-bench.py [--out reports/ui-bench] [--run <run id>] [--old-run <run id>]

Needs ui/dist (npm run build --prefix ui), desktop/node_modules (npm ci --prefix desktop) and Playwright in the .venv. The
window is driven through the Chromium DevTools port (the same way verify-suite-native-ui.py drives it); the page is resized
and its colour scheme emulated, so the application's own responsive layout and theme switch are what is photographed.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, default=ROOT / "reports" / "ui-bench")
parser.add_argument("--run", help="run id to show (default: the latest)")
parser.add_argument("--old-run", help="a run written before schema 2, photographed last to show how it reads")
parser.add_argument("--port", type=int, default=9341)
parser.add_argument("--electron", type=Path, default=ROOT / "desktop" / "node_modules" / "electron" / "dist" / "electron.exe")
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)

log = tempfile.TemporaryFile()
application = subprocess.Popen([str(args.electron), str(ROOT / "desktop"), f"--remote-debugging-port={args.port}"], cwd=ROOT / "desktop",
                               env={**os.environ, "FLASH_ALLOW_MULTIPLE": "1"}, stdout=log, stderr=log)
try:
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{args.port}/json/version", timeout=1).read()
            break
        except OSError:
            time.sleep(0.3)
    else:
        raise RuntimeError("Electron did not open its DevTools port")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        page = next(candidate for context in browser.contexts for candidate in context.pages if "app://ui" in candidate.url)
        page.set_default_timeout(20000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_viewport_size({"width": 1280, "height": 900})
        page.get_by_role("menuitem", name="Herramientas").click()
        page.get_by_role("menuitem", name="Benchmarks").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_role("heading", name="Clasificación de motores")).to_be_visible()
        if args.run:
            dialog.get_by_role("combobox", name="Ejecución").select_option(args.run)
        body = dialog.locator(".modal-body")

        def show(width, scheme, name, prepare=None):
            """Resize the page, wait for the charts to follow, grow the window to the whole page and photograph it."""
            page.emulate_media(color_scheme=scheme)
            page.set_viewport_size({"width": width, "height": 1200})
            page.wait_for_timeout(500)
            if prepare:
                prepare()
                page.wait_for_timeout(300)
            height = body.evaluate("element => element.scrollHeight") + 120
            page.set_viewport_size({"width": width, "height": min(height, 16000)})
            page.wait_for_timeout(500)
            path = args.out / name
            dialog.screenshot(path=str(path))
            print(path)

        show(1280, "dark", "benchmarks-dark.png")
        show(1280, "light", "benchmarks-light.png")
        show(480, "dark", "benchmarks-narrow-dark.png")
        show(480, "light", "benchmarks-narrow-light.png")
        show(760, "light", "benchmarks-medium-light.png")

        def open_tables():
            for summary in dialog.locator("summary").all():
                if "Tabla completa" in summary.inner_text() or "Cómo se calcula" in summary.inner_text():
                    summary.click()
        show(1280, "dark", "benchmarks-table-dark.png", open_tables)

        # A tooltip: the exact value and the number of cases of one bar, with the other engines dimmed everywhere.
        dialog.evaluate("element => element.querySelectorAll('details[open]').forEach(details => { details.open = false; })")
        page.emulate_media(color_scheme="light")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_timeout(400)
        mark = dialog.locator("[data-mark]").first
        mark.scroll_into_view_if_needed()
        mark.hover()
        page.wait_for_timeout(300)
        page.screenshot(path=str(args.out / "benchmarks-tooltip-light.png"))
        print(args.out / "benchmarks-tooltip-light.png")
        if args.old_run:
            mark.blur()
            page.mouse.move(0, 0)
            dialog.get_by_role("combobox", name="Ejecución").select_option(args.old_run)
            expect(dialog.get_by_text("Sin datos en esta corrida").first).to_be_visible()
            show(1280, "light", "benchmarks-old-run-light.png")
        assert not errors, errors
        print(json.dumps({"errors": errors}))
finally:
    application.terminate()
    try:
        application.wait(timeout=15)
    except subprocess.TimeoutExpired:
        application.kill()
    log.seek(0)
    output = log.read().decode("utf-8", errors="replace")
    if output.strip():
        print(output[-2000:])
