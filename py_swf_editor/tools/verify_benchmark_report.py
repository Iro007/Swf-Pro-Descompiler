"""Read-back and browser QA for the generated, local benchmark HTML."""
import argparse
import json
from pathlib import Path


def verify(folder, refresh=False):
    from playwright.sync_api import sync_playwright, expect
    folder = Path(folder).resolve()
    data = json.loads((folder / "benchmark.json").read_text(encoding="utf-8"))
    if refresh:
        from py_swf.benchmark_report import render_dashboard
        (folder / "benchmark.html").write_text(render_dashboard(data), encoding="utf-8")
    source = (folder / "benchmark.html").read_text(encoding="utf-8")
    assert 'id="report"' in source and 'id="filter"' in source
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width":1600,"height":1000})
        errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.on("console",lambda m:errors.append(m.text) if m.type=="error" else None)
        try:
            page.goto((folder / "benchmark.html").as_uri(),wait_until="networkidle")
            # Inspect the loaded document before relying on table/filter state.
            readback = page.locator('main').inner_text()
            assert 'No se ha establecido paridad semántica' in readback
            rows=sum(len(c['engines']) for c in data['cases'])
            expect(page.locator('tbody tr')).to_have_count(rows)
            expect(page.locator('thead th')).to_have_count(12)
            assert all(len(row)==12 for row in page.locator('tbody tr').evaluate_all('rows=>rows.map(r=>Array.from(r.children))'))
            page.screenshot(path=str(folder / 'benchmark-preview.png'),full_page=True)
            chosen='native' if any(r['engine']=='native' for c in data['cases'] for r in c['engines']) else data['cases'][0]['engines'][0]['engine']
            page.get_by_label('Filtrar resultados').fill(chosen)
            expected=sum(r['engine']==chosen for c in data['cases'] for r in c['engines'])
            expect(page.locator('tbody tr:not([hidden])')).to_have_count(expected)
            page.get_by_label('Métrica del gráfico').select_option('hot')
            expect(page.locator('#bar-heading')).to_have_text('Latencia caliente')
            page.get_by_label('Filtrar resultados').fill('')
            page.get_by_label('Filtrar motor').select_option(chosen)
            expect(page.locator('tbody tr:not([hidden])')).to_have_count(expected)
            page.get_by_label('Filtrar archivo').select_option(data['cases'][0]['relative'])
            expect(page.locator('tbody tr:not([hidden])')).to_have_count(sum(e['engine']==chosen for e in data['cases'][0]['engines']))
            page.get_by_label('Filtrar resultados').fill('no-such-file-987')
            expect(page.locator('#table-empty')).to_be_visible()
            expect(page.locator('#scatter')).not_to_contain_text('NaN')
            page.get_by_label('Filtrar resultados').fill('')
            page.get_by_label('Filtrar archivo').select_option('all')
            page.get_by_label('Filtrar motor').select_option('all')
            page.get_by_label('Métrica del gráfico').select_option('source')
            with page.expect_download() as download:
                page.get_by_role('button',name='Descargar JSON resumido').click()
            summary=json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
            assert all('file' not in c for c in summary['cases'])
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(folder / 'benchmark-mobile.png'),full_page=True)
            assert not errors,errors
            return {'passed':True,'rows':rows,'console_errors':errors,'screenshot':str(folder / 'benchmark-preview.png')}
        finally:
            browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('folder',type=Path)
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    print(json.dumps(verify(args.folder,args.refresh),ensure_ascii=False,indent=2))
