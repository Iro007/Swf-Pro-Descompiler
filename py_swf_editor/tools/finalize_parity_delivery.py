"""Preserve frozen evidence, draw scoped charts and make a separate portable release."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import shutil
import sys
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
sys.path.insert(0, str(PROJECT))


def sha(path):
    with Path(path).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256')
    return digest.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def freeze(evidence, rust, worker, runtime, rascal, corpus):
    from py_swf.benchmarks import implementation_digest
    record = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
              'implementation_sha256': implementation_digest(),
              'corpus_manifest_sha256': (corpus / 'manifest.sha256').read_text().strip(),
              'agents_quiescent': True, 'builds_and_tests_finished': True,
              'binaries': {key: {'path': str(path.resolve()), 'sha256': sha(path), 'bytes': path.stat().st_size}
                           for key, path in [('rust', rust), ('python', worker), ('ruffle', runtime), ('rascal', rascal)]}}
    save(evidence / 'freeze.json', record)
    return record


def regressions(evidence, tools):
    from py_swf.benchmarks import run_engine
    from compare_engine_progress import compare
    rows, inputs = [], []
    for cohort in ('original', 'micro', 'switch'):
        old_path = ROOT / f'reports/benchmark-after-{cohort}-20261014/benchmark.json'
        old = load(old_path)
        current = {'schema_version': 1, 'cases': [], 'profile': 'correctness_one_operation',
                   'claims': {'performance_winner': None, 'semantic_parity': False},
                   'note': 'One current correctness operation; saved before timings are not performance comparisons'}
        for case in old['cases']:
            path = Path(case['file'])
            if not path.is_file():
                raise FileNotFoundError('Original input missing; relocation must be verified: ' + str(path))
            if sha(path) != case['sha256']:
                raise ValueError('Historical original changed: ' + str(path))
            results = [run_engine(engine, path.read_bytes(), {'warmups': 0, 'hot_runs': 1, 'cold_runs': 0},
                                  tool_config=tools, timeout=180) for engine in ('native', 'rust')]
            current['cases'].append({**{key: value for key, value in case.items() if key != 'engines'}, 'engines': results})
            print('Observed regression', cohort, case['relative'], flush=True)
        current_path = evidence / f'regression-{cohort}.json'
        save(current_path, current)
        compared = compare(old, current, cohort)
        # Declaration views previously counted as method source need a separate
        # explanation; do not pretend their stricter classification is a gain.
        for row in compared:
            row.pop('hot_before_ms', None); row.pop('hot_after_ms', None)
            row.pop('rss_before_bytes', None); row.pop('rss_after_bytes', None)
            observed = next(c for c in current['cases'] if c['sha256'] == row['input_sha256'])
            observed_engine = next(e for e in observed['engines'] if e['engine'] == row['engine'])
            by_id = {u.get('document_id'): u for u in observed_engine.get('units', [])}
            for lost in row['lost_source_units']:
                after = by_id.get(lost['id'], {})
                lost.update(generation_after=after.get('generation'), source_kind_after=after.get('source_kind'),
                            represented_by_document=after.get('represented_by_document'),
                            diagnostic_after=after.get('diagnostic') or after.get('diagnostics'))
        rows.extend(compared)
        inputs.append({'before': str(old_path), 'before_sha256': sha(old_path),
                       'after': str(current_path), 'after_sha256': sha(current_path)})
    result = {'schema_version': 1, 'rows': rows, 'inputs': inputs,
              'scope': 'Exact common original bytecode; method/action availability, separate complete documents',
              'claims': {'parity': False, 'performance_winner': None}}
    save(evidence / 'regressions.json', result)
    return result


def report(evidence):
    index_path = evidence / 'correctness-derived/correctness-index.json'
    if not index_path.is_file():
        index_path = evidence / 'correctness-frozen/correctness-index.json'
    index = load(index_path) if index_path.is_file() else {'entries': []}
    rows = []
    for entry in index['entries']:
        if not entry.get('path'):
            continue
        data = load(entry['path']); engine = data['cases'][0]['engines'][0]
        quality = engine.get('quality', {})
        rows.append({key: entry.get(key) for key in ('family_id', 'split', 'engine', 'status', 'path', 'sha256')} |
                    {'adapter_mode': engine.get('adapter_mode'), 'inventory_status': engine.get('inventory_status'),
                     'quality': quality, 'scope_metrics': quality.get('scope_metrics', {}),
                     'origin_scope_metrics': quality.get('origin_scope_metrics', {})})
    result = {'schema_version': 1, 'rows': rows, 'freeze': load(evidence / 'freeze.json'),
              'regressions': load(evidence / 'regressions.json') if (evidence / 'regressions.json').is_file() else None,
              'claims': {'parity': False, 'superiority': False, 'performance_winner': None},
              'limitations': ['La fuente disponible no acredita recompilación ni equivalencia de comportamiento.',
                 'Los inicializadores representados por documentos se cuentan por separado de cuerpos de método.',
                 'General finally, documentación completa de proyectos complejos y varias formas dinámicas siguen pendientes.',
                 'Las referencias de desarrollo no acreditan capacidades nativas.']}
    current_regression = evidence / 'version2-regressions/regressions.json'
    if current_regression.is_file():
        result['regressions'] = load(current_regression)
    result['revision_note'] = ('La campaña de 12 familias conserva la revisión congelada inicial. La entrega añade imports de clases usados en cuerpos y corrige la ruta del SDK empaquetado; se repitió la regresión de nueve SWF y el fixture de desarrollo con Flex/Ruffle. No se atribuye a la entrega una nueva validación reservada completa.')
    result['progress'] = {}
    for engine in ('native', 'rust'):
        subset = [r for r in (result.get('regressions') or {}).get('rows', []) if r['engine'] == engine]
        result['progress'][engine] = {key: sum(r[key] for r in subset) for key in
            ('common_verified_units', 'source_on_common_before', 'source_on_common_after', 'new_inventory_units', 'new_inventory_source')}
        result['progress'][engine].update(gained=sum(len(r['gained_source_units']) for r in subset),
                                          lost=sum(len(r['lost_source_units']) for r in subset))
    semantics = evidence / 'source-semantics-frozen/semantic-index.json'
    performance = evidence / 'performance-frozen/campaign.json'
    result['semantic_jobs'] = load(semantics) if semantics.is_file() else None
    if performance.is_file():
        campaign = load(performance)
        result['performance'] = {key: campaign.get(key) for key in ('status', 'completed_sessions', 'profile', 'claims')}
        result['performance']['sessions'] = [{key: session.get(key) for key in ('ordinal', 'status', 'preflight')} for session in campaign['sessions']]
    save(evidence / 'delivery-summary.json', result)
    embedded = json.dumps(result, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    page = r'''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PySWF y RustSWF: código y recompilación</title>
<style>body{font:16px system-ui;background:#151c24;color:#e8edf3;margin:0}main{max-width:1250px;margin:auto;padding:28px}label{display:inline-flex;flex-direction:column;margin:12px 18px 12px 0;gap:6px}select{font:inherit;padding:8px;background:#263443;color:white}section{background:#202b37;padding:18px;margin:18px 0;border-radius:8px}.note{line-height:1.6;color:#d0dce7}.row{display:grid;grid-template-columns:minmax(240px,1fr) 2fr;gap:12px;margin:16px 0}.bar{display:inline-block;height:18px;background:#4aa4c8;vertical-align:middle;margin-right:12px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;border-bottom:1px solid #425363;padding:10px}.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#8cdaff}</style>
<main><h1>Código y recompilación de PySWF / RustSWF</h1><p class="note">Resultados sobre los bytes y motores congelados. Cada familia conserva su partición; aplicación y bibliotecas tienen denominadores separados. Las barras muestran fuente reconstruida con identidad verificada. Compilación y ejecución se consultan en la evidencia independiente. La paridad con JPEXS sigue sin acreditarse.</p><p id="revision" class="note"></p>
<section id="progress"></section><label>Familia<select id="family"></select></label><label>Alcance<select id="scope"></select></label><label>Código<select id="origin"><option value="all">Todo el proyecto</option><option value="application">Aplicación</option><option value="dependency">Bibliotecas</option><option value="mixed">Mixto</option><option value="unknown">Sin atribuir</option></select></label><section id="chart"></section><div class="scroll"><table><thead><tr><th>Familia / partición</th><th>Motor / modo</th><th>Inventario</th><th>Fuente / denominador</th><th>Estado</th></tr></thead><tbody id="rows"></tbody></table></div><details><summary>Hashes, diferencias y límites</summary><pre id="evidence"></pre></details></main>
<script type="application/json" id="data">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent),byId=id=>document.getElementById(id),el=(tag,v)=>{const n=document.createElement(tag);n.textContent=v;return n};
byId('revision').textContent=data.revision_note;
byId('progress').append(el('h2','Antes y después: nueve SWF originales'));for(const [engine,p] of Object.entries(data.progress)){byId('progress').append(el('p',`${engine==='native'?'PySWF':'RustSWF'}: ${p.source_on_common_before} → ${p.source_on_common_after} de ${p.common_verified_units} cuerpos idénticos; ${p.gained} recuperados, ${p.lost} perdidos. ${p.new_inventory_units} documentos nuevos se contabilizan por separado.`))}byId('progress').append(el('p','Rendimiento: '+(data.performance?.status||'sin medición elegible')+'; la presencia de fuente y los documentos nuevos no acreditan paridad de recompilación.'));
byId('family').append(new Option('Todas las familias',''));for(const v of [...new Set(data.rows.map(r=>r.family_id))].sort())byId('family').append(new Option(v,v));for(const v of ['method','action','function','class','script'])byId('scope').append(new Option(v,v));
function metric(r){const scope=byId('scope').value,origin=byId('origin').value;return origin==='all'?r.scope_metrics?.[scope]:r.origin_scope_metrics?.[origin]?.[scope]}
function draw(){const rows=data.rows.filter(r=>!byId('family').value||r.family_id===byId('family').value),chart=byId('chart');chart.replaceChildren(el('h2','Fuente reconstruida por identidad equivalente'));byId('rows').replaceChildren();for(const r of rows){const m=metric(r),den=m?.canonical_denominator??m?.expected_units,verified=m&&m.identity_verified===den&&den>0,source=verified?m.source_available:null,label=source==null?'Sin correspondencia verificable':`${source} / ${den} (${(100*source/den).toFixed(1)}%)`;const line=el('div','');line.className='row';line.append(el('span',r.family_id+' · '+r.engine));const v=el('div','');if(source!=null){const bar=el('i','');bar.className='bar';bar.style.width=(100*source/den*.75)+'%';v.append(bar)}v.append(el('span',label));line.append(v);chart.append(line);const tr=el('tr','');for(const s of [r.family_id+' / '+r.split,r.engine+' / '+(r.adapter_mode||'native'),r.inventory_status||'no registrado',label,r.status])tr.append(el('td',s));byId('rows').append(tr)}if(!rows.length)chart.append(el('p','No hay observaciones disponibles.'))}
for(const id of ['family','scope','origin'])byId(id).addEventListener('change',draw);byId('evidence').textContent=JSON.stringify(data,null,2);draw();</script></html>'''
    (evidence / 'parity-report.html').write_text(page.replace('__DATA__', embedded), encoding='utf-8')
    return {'rows': len(rows), 'report': str(evidence / 'parity-report.html')}


def package(evidence, target):
    from py_swf.benchmarks import implementation_digest
    pins_path = evidence / 'package-freeze.json'
    pins = load(pins_path if pins_path.is_file() else evidence / 'freeze.json')
    if pins['implementation_sha256'] != implementation_digest():
        raise ValueError('Source changed after freeze; refusing package')
    for binary in pins['binaries'].values():
        if sha(binary['path']) != binary['sha256']:
            raise ValueError('Frozen binary changed')
    if target.exists():
        raise ValueError('Portable release already exists; no overwrite')
    previous = ROOT / 'rust_swf/dist/FlashUniversalStudio-Engines-20261014'
    target.mkdir(parents=True)
    for name in ('WebView2Loader.dll', 'LICENSE', 'README.md', 'SUITE_CLI.md', 'THIRD_PARTY_NOTICES.md', 'pyswf.ps1'):
        shutil.copy2(previous / name, target / name)
    shutil.copytree(previous / 'licenses', target / 'licenses')
    shutil.copy2(pins['binaries']['python']['path'], target / 'FlashSuiteWorker.exe')
    shutil.copy2(pins['binaries']['python']['path'], target / 'PySWF.exe')
    shutil.copy2(pins['binaries']['rust']['path'], target / 'rustswf.exe')
    shutil.copy2(ROOT / 'rust_swf/target/x86_64-pc-windows-gnu/release/rust-swf-studio.exe', target / 'FlashUniversalStudio.exe')
    for name in ('freeze.json', 'package-freeze.json', 'delivery-summary.json', 'regressions.json', 'parity-report.html'):
        if (evidence / name).is_file():
            shutil.copy2(evidence / name, target / name)
    for name in ('benchmark.html', 'catalog-summary.json', 'benchmark.sqlite'):
        shutil.copy2(ROOT / 'reports/benchmark-catalog' / name, target / ('benchmarks.html' if name == 'benchmark.html' else name))
    # Ship the actual corresponding engine source and build manifests.
    source = target / 'source'; source.mkdir()
    shutil.copytree(PROJECT / 'py_swf', source / 'py_swf', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / 'rust_swf/crates', source / 'rust/crates', ignore=shutil.ignore_patterns('target'))
    shutil.copytree(ROOT / 'rust_swf/desktop', source / 'rust/desktop', ignore=shutil.ignore_patterns('target'))
    shutil.copytree(ROOT / 'rust_swf/web', source / 'rust/web', ignore=shutil.ignore_patterns('node_modules', 'dist', '.cache', '.build-staging'))
    shutil.copytree(ROOT / 'rust_swf/.cargo', source / 'rust/.cargo')
    shutil.copytree(PROJECT / 'packaging', source / 'python-packaging', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(PROJECT / 'tests', source / 'python-tests', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('requirements.txt', 'requirements-dev.txt', 'pyproject.toml'):
        if (PROJECT / name).is_file():
            shutil.copy2(PROJECT / name, source / name)
    for name in ('Cargo.toml', 'Cargo.lock', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'build.ps1'):
        path = ROOT / 'rust_swf' / name
        if path.is_file():
            shutil.copy2(path, source / 'rust' / name)
    shutil.copytree(PROJECT / 'tools/jpexs_java_bridge', source / 'jpexs_java_bridge')
    shutil.copytree(ROOT / 'rust_swf/tools', source / 'rust/tools', ignore=shutil.ignore_patterns('bootstrap', '__pycache__'))
    for name in ('CODE_PARITY_SUITE.md', 'native-code-parity-contract.md', 'jpexs-identity-bridge.md'):
        if (PROJECT / 'docs' / name).is_file():
            shutil.copy2(PROJECT / 'docs' / name, target / name)
    (target / 'README-PARITY.txt').write_text(
        'Flash Universal Studio — código y recompilación, 2026-10-14\n\n'
        'FlashUniversalStudio.exe abre la suite; PySWF.exe cli engines usa sólo CLI.\n'
        'parity-report.html muestra calidad por familia y alcance; benchmarks.html conserva campañas históricas.\n'
        'RustSWF reconstruye código; la recompilación compartida utiliza PySWF y se identifica como delegada.\n'
        'Hay patrones pendientes y no se declara paridad universal ni ganador de rendimiento.\n'
        'Pruebas locales sin GUI. Las referencias JPEXS/Flex/Rascal/Haxe/Ruffle se configuran aparte.\n'
        'Los informes íntegros permanecen en el workspace indicado por freeze.json; no se truncaron las unidades.\n'
        'La versión anterior se conserva en su propia carpeta.\n', encoding='utf-8')
    artifacts = [{'path': p.relative_to(target).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                 for p in sorted(target.rglob('*')) if p.is_file()]
    save(target / 'release-manifest.json', {'schema_version': 1, 'build': 'code-parity-20261014',
         'freeze': pins, 'artifacts': artifacts, 'claims': {'parity': False, 'superiority': False}})
    archive = target.with_name(target.name + '-portable.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as packed:
        for path in sorted(target.rglob('*')):
            if path.is_file():
                packed.write(path, path.relative_to(target).as_posix())
    with zipfile.ZipFile(archive) as packed:
        if packed.testzip() is not None:
            raise ValueError('Portable archive CRC mismatch')
    result = {'path': str(target), 'archive': str(archive), 'sha256': sha(archive), 'files': len(artifacts)}
    save(evidence / 'portable-verification.json', result)
    return result


def main():
    p = argparse.ArgumentParser(); p.add_argument('operation', choices=['freeze', 'regressions', 'report', 'package'])
    p.add_argument('--evidence', type=Path, required=True); p.add_argument('--tools', type=Path)
    p.add_argument('--rust', type=Path); p.add_argument('--worker', type=Path); p.add_argument('--runtime', type=Path)
    p.add_argument('--rascal', type=Path); p.add_argument('--corpus', type=Path); p.add_argument('--target', type=Path)
    a = p.parse_args()
    if a.operation == 'freeze': result = freeze(a.evidence, a.rust, a.worker, a.runtime, a.rascal, a.corpus)
    elif a.operation == 'regressions': result = regressions(a.evidence, a.tools)
    elif a.operation == 'report': result = report(a.evidence)
    else: result = package(a.evidence, a.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
