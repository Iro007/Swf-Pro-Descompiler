"""Package measured engines and catalog without changing the previous release."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from PyInstaller.archive.readers import CArchiveReader


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    args = parser.parse_args()
    if len(args.round) != 8 or not args.round.isdigit():
        parser.error('round must be YYYYMMDD')
    evidence = root / ('reports/decompiler-improvements-' + args.round)
    old = root / 'rust_swf/dist/FlashUniversalStudio-Engines'
    target = old.parent / (old.name + '-' + args.round)
    if target.exists():
        raise ValueError('Release already exists; no package overwritten')
    old_manifest = json.loads((old / 'release-manifest.json').read_text(encoding='utf-8'))
    previous_date = old_manifest.get('build', '').removeprefix('engine-improvements-')
    if len(previous_date) == 8 and previous_date.isdigit() and previous_date != args.round:
        previous_release = old.parent / (old.name + '-' + previous_date)
        if not previous_release.exists():
            shutil.copytree(old, previous_release)
    payload = json.loads((evidence / 'after-code-payload.json').read_text(encoding='utf-8'))
    worker = root / 'py_swf_editor/dist/FlashSuiteWorker.exe'
    if hashlib.sha256(CArchiveReader(str(worker)).extract(payload['archive'])).hexdigest() != payload['pyz_sha256']:
        raise ValueError('Final Python code differs from the measured payload')
    rust = evidence / 'after-binaries/rustswf.exe'
    summary = root / 'reports/benchmark-catalog/catalog-summary.json'
    comparison = json.loads((evidence / 'comparison.json').read_text(encoding='utf-8'))
    shutil.copytree(old, target)
    for destination in ('FlashSuiteWorker.exe', 'PySWF.exe'):
        shutil.copyfile(worker, target / destination)
    shutil.copyfile(rust, target / 'rustswf.exe')
    for source, destination in [(root / 'reports/benchmark-catalog/benchmark.html', 'benchmarks.html'),
                                (summary, 'catalog-summary.json'),
                                (evidence / 'comparison.html', 'engine-progress.html'),
                                (evidence / 'comparison.json', 'engine-progress.json'),
                                (evidence / 'external-results.json', 'external-results.json')]:
        shutil.copyfile(source, target / destination)
    shutil.copyfile(evidence / 'feature-comparison.json', target / 'feature-comparison.json')
    shutil.copyfile(evidence / 'comparison-details.json', target / 'comparison-details.json')
    shutil.copytree(evidence / 'feature-fixtures', target / 'benchmark-evidence/feature-fixtures')
    external_evidence = root / ('reports/external-source-evidence-' + args.round)
    if external_evidence.is_dir():
        shutil.copytree(external_evidence, target / 'benchmark-evidence/external-source')
    totals = json.loads(summary.read_text(encoding='utf-8'))['totals']
    (target / 'README-ENGINES.txt').write_text(
        'Flash Universal Studio — ronda ' + args.round + '\n\n'
        'Ejecuta FlashUniversalStudio.exe para abrir la suite.\n'
        'PySWF.exe cli engines muestra los módulos; rustswf.exe capabilities muestra RustSWF.\n'
        'benchmarks.html contiene campañas locales y de nube filtrables por operación y entorno.\n'
        'engine-progress.html compara unidades de bytecode idénticas antes/después.\n'
        'external-results.json registra pyflashkit, RABCDAsm y los demás adaptadores disponibles.\n'
        'Las herramientas externas se configuran aparte en el workspace: no se distribuyen sus entornos.\n'
        'La fuente disponible y la salida de archivos no prueban equivalencia semántica.\n'
        'Validación: CLI, pruebas de regresión y hashes; no se ejecutaron pruebas de GUI.\n', encoding='utf-8')
    artifacts = [{'path': path.relative_to(target).as_posix(), 'bytes': path.stat().st_size, 'sha256': sha(path)}
                 for path in sorted(target.rglob('*')) if path.is_file() and path.name != 'release-manifest.json']
    manifest = {'schema_version': 1, 'application': 'Flash Universal Studio', 'version': '0.3.0',
                'build': 'engine-improvements-' + args.round, 'catalog': totals,
                'comparison': comparison['engines'], 'artifacts': artifacts,
                'verification_scope': 'Measured code payload and Rust binary; CLI regression tests; no GUI tests'}
    (target / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = target.parent / (target.name + '-portable.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as packed:
        for path in sorted(target.rglob('*')):
            if path.is_file():
                packed.write(path, path.relative_to(target).as_posix())
    config_path = root / 'py_swf_editor/.dev-references/extra-tools.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    rust_path = str((target / 'rustswf.exe').resolve())
    config['tools']['rust'] = {'version': '0.3.0', 'executable': rust_path,
                              'artifact_sha256': {rust_path: sha(target / 'rustswf.exe')},
                              'scope': 'Measured engine-improvements-' + args.round}
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'release': str(target), 'archive': str(archive), 'archive_bytes': archive.stat().st_size,
                      'archive_sha256': sha(archive), 'files': len(artifacts), 'catalog': totals}, indent=2))


if __name__ == '__main__':
    main()
