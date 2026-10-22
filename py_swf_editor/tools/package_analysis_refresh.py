"""Stage and verify a separate portable release without replacing prior deliveries."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
sys.path.insert(0, str(PROJECT))
EVIDENCE = ROOT / 'reports/benchmark-refresh-20261014'
TARGET = ROOT / 'rust_swf/dist/FlashUniversalStudio-Analysis-20261014'

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def snapshot():
    from py_swf.benchmarks import implementation_digest
    inputs = [*list((ROOT/'rust_swf/crates').rglob('*.rs')),
              *list((ROOT/'rust_swf/web/src').rglob('*')),
              *list((ROOT/'rust_swf/web/dist').rglob('*'))]
    return {'python_implementation_sha256': implementation_digest(),
            'source_files': {p.relative_to(ROOT).as_posix(): sha(p) for p in inputs if p.is_file()}}

def stage():
    if TARGET.exists():
        raise ValueError('New release already exists; previous or staged output is never overwritten')
    previous = ROOT/'rust_swf/dist/FlashUniversalStudio-Parity-20261014'
    TARGET.mkdir(parents=True)
    for name in ('WebView2Loader.dll','LICENSE','THIRD_PARTY_NOTICES.md','pyswf.ps1','SUITE_CLI.md'):
        shutil.copy2(previous/name, TARGET/name)
    shutil.copytree(previous/'licenses', TARGET/'licenses')
    binaries = {'FlashSuiteWorker.exe': EVIDENCE/'portable-binaries/FlashSuiteWorker.exe',
                'PySWF.exe': EVIDENCE/'portable-binaries/FlashSuiteWorker.exe',
                'rustswf.exe': ROOT/'rust_swf/target/x86_64-pc-windows-gnu/release/rustswf.exe',
                'FlashUniversalStudio.exe': EVIDENCE/'portable-binaries/FlashUniversalStudio.exe'}
    for name, source in binaries.items():
        shutil.copy2(source, TARGET/name)
    database = ROOT/'reports/benchmark-catalog/benchmark.sqlite'
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as source:
        with sqlite3.connect(TARGET/'benchmark.sqlite') as destination:
            source.backup(destination)
    source_root = TARGET/'source'
    source_root.mkdir()
    for source, destination in ((PROJECT/'py_swf',source_root/'py_swf'),
            (PROJECT/'tests',source_root/'python-tests'),(PROJECT/'tools',source_root/'python-tools'),
            (PROJECT/'packaging',source_root/'python-packaging'),
            (ROOT/'rust_swf/crates',source_root/'rust/crates'),
            (ROOT/'rust_swf/desktop',source_root/'rust/desktop'),
            (ROOT/'rust_swf/web',source_root/'rust/web'),
            (ROOT/'rust_swf/.cargo',source_root/'rust/.cargo')):
        shutil.copytree(source,destination,ignore=shutil.ignore_patterns('__pycache__','*.pyc','node_modules','target','dist','binaries','.cache','.build-staging'))
    for name in ('pyproject.toml','requirements.txt','requirements-dev.txt'):
        if (PROJECT/name).is_file(): shutil.copy2(PROJECT/name,source_root/name)
    for name in ('Cargo.toml','Cargo.lock','LICENSE','THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT/'rust_swf'/name,source_root/'rust'/name)
    pins={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),
          'snapshot':snapshot(),'binaries':{name:{'sha256':sha(TARGET/name),'bytes':(TARGET/name).stat().st_size} for name in binaries},
          'status':'staged_for_GUI_validation','claims':{'parity':False,'performance_winner':None}}
    save(EVIDENCE/'analysis-freeze.json',pins)
    return {'target':str(TARGET),'status':pins['status']}

def finish():
    pins=json.loads((EVIDENCE/'analysis-freeze.json').read_text(encoding='utf-8'))
    if pins['snapshot'] != snapshot(): raise ValueError('Sources changed after staging; cannot claim tested release')
    for name, record in pins['binaries'].items():
        if sha(TARGET/name)!=record['sha256']:raise ValueError('Staged binary changed: '+name)
    full_gui=json.loads((EVIDENCE/'gui/native-full-before-theme.json').read_text(encoding='utf-8'))
    final_gui=json.loads((EVIDENCE/'gui/native-final-theme-verification.json').read_text(encoding='utf-8'))
    tests=json.loads((EVIDENCE/'final-pytest-validation.json').read_text(encoding='utf-8'))
    frozen=json.loads((EVIDENCE/'frozen-worker-verification/verification.json').read_text(encoding='utf-8'))
    if full_gui.get('passed') is not True or final_gui.get('passed') is not True:
        raise ValueError('Actual GUI validation has not passed')
    if not tests.get('implementation_unchanged') or tests.get('passed',0)<1161 or tests.get('implementation_sha256_after')!=pins['snapshot']['python_implementation_sha256']:
        raise ValueError('Python validation does not match the frozen implementation')
    if not frozen.get('status','').startswith('passed_') or frozen.get('worker_sha256_after')!=pins['binaries']['FlashSuiteWorker.exe']['sha256']:
        raise ValueError('Frozen compiler verification does not match the packaged worker')
    # GUI correctness campaigns may have joined the catalog after staging.
    with sqlite3.connect((ROOT/'reports/benchmark-catalog/benchmark.sqlite').as_uri()+'?mode=ro',uri=True) as source:
        with sqlite3.connect(TARGET/'benchmark.sqlite') as destination:
            source.backup(destination)
    (TARGET/'README.txt').write_text(
        'Flash Universal Studio / Flash Analysis — 2026-10-14\n\n'
        'Abra FlashUniversalStudio.exe. Benchmarks contiene barras, dispersion, filtros y evidencia exportable.\n'
        'PySWF.exe cli y rustswf.exe ofrecen las interfaces de terminal.\n'
        'RustSWF reconstruye codigo; la compilacion compartida utiliza PySWF dentro de su subconjunto.\n'
        'Hay siete herramientas medidas en roles distintos. JPEXS/Flex/Flasm/RABCDAsm/pyflashkit son referencias configuradas aparte.\n'
        'La disponibilidad de fuente no demuestra compilacion ni equivalencia. No se declara paridad universal ni ganador de velocidad.\n'
        'Se incluyen fuentes, licencias y evidencia; el paquete anterior permanece en su carpeta.\n',encoding='utf-8')
    evidence = TARGET/'evidence';evidence.mkdir(exist_ok=True)
    for folder in ('benchmark-refresh-20261014','catalog-summary-20261014','rust-static-init-20261014','error-constructors-20261014','static-init-review-20261014'):
        src=ROOT/'reports'/folder;dst=evidence/folder;dst.mkdir(exist_ok=True)
        for p in src.rglob('*'):
            if p.is_file() and p.suffix.lower() in ('.json','.jpg','.png','.svg','.md','.as','.swf','.log','.csv','.cjs') and not any(part in ('worker-build','portable-binaries') for part in p.relative_to(src).parts) and p.stat().st_size<16*1024**2:
                target=dst/p.relative_to(src);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    artifacts=[{'path':p.relative_to(TARGET).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(TARGET.rglob('*')) if p.is_file()]
    save(TARGET/'release-manifest.json',{'schema_version':1,'freeze':pins,'artifacts':artifacts,'status':'verified','claims':pins['claims']})
    archive=TARGET.with_name(TARGET.name+'-portable.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3,strict_timestamps=False) as packed:
        for p in sorted(TARGET.rglob('*')):
            if p.is_file():packed.write(p,p.relative_to(TARGET).as_posix())
    with zipfile.ZipFile(archive) as packed:
        if packed.testzip() is not None:raise ValueError('Portable archive failed CRC verification')
    result={'target':str(TARGET),'archive':str(archive),'sha256':sha(archive),'bytes':archive.stat().st_size,'files':len(artifacts)}
    save(EVIDENCE/'portable-verification.json',result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['stage','finish']);args=parser.parse_args()
    print(json.dumps(stage() if args.operation=='stage' else finish(),ensure_ascii=False,indent=2))
