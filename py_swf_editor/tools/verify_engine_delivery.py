"""Verify the final engines against the measured payload and bundled catalog."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from PyInstaller.archive.readers import CArchiveReader


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', default='20261013')
    parser.add_argument('--release', type=Path)
    args = parser.parse_args()
    if len(args.round) != 8 or not args.round.isdigit():
        parser.error('round must be YYYYMMDD')
    folder = args.release or root / 'RUST SWF/dist/FlashUniversalStudio-Engines'
    evidence = root / ('reports/decompiler-improvements-' + args.round)
    payload = json.loads((evidence / 'after-code-payload.json').read_text(encoding='utf-8'))
    worker = folder / 'FlashSuiteWorker.exe'
    assert hashlib.sha256(CArchiveReader(str(worker)).extract(payload['archive'])).hexdigest() == payload['pyz_sha256']
    assert sha(folder / 'rustswf.exe') == sha(evidence / 'after-binaries/rustswf.exe')
    assert sha(worker) == sha(folder / 'PySWF.exe')
    expected = json.loads((folder / 'catalog-summary.json').read_text(encoding='utf-8'))['totals']
    with tempfile.TemporaryDirectory(prefix='flash-engine-delivery-') as directory:
        environment = {**os.environ, 'FLASH_SUITE_WORKSPACE': directory, 'PYTHONIOENCODING': 'utf-8'}
        environment.pop('FLASH_BENCHMARK_CATALOG', None)
        result = subprocess.run([str(worker), 'cli', 'rpc', '--rpc-operation', 'benchmark_catalog'], cwd=directory,
                                env=environment, capture_output=True, text=True, encoding='utf-8', timeout=90, check=True)
        catalog = json.loads(result.stdout)
        assert catalog['available'] and catalog['totals'] == expected
        assert 'benchmark_data' in catalog['database']
        assert any('benchmark-after-' in c['label'] for c in catalog['campaigns'])
        result = subprocess.run([str(worker), 'cli', 'engines'], cwd=directory, env=environment,
                                capture_output=True, text=True, encoding='utf-8', timeout=90, check=True)
        catalog_response = json.loads(result.stdout)
        engine_rows = catalog_response.get('engines', []) if isinstance(catalog_response, dict) else catalog_response
        engine = next(row for row in engine_rows if row['id'] == 'rust')
        assert Path(engine['path']).resolve() == (folder / 'rustswf.exe').resolve()
    archive = folder.parent / (folder.name + '-portable.zip')
    verified_files = 0
    if archive.is_file():
        with zipfile.ZipFile(archive) as packed:
            assert packed.testzip() is None
            manifest = json.loads(packed.read('release-manifest.json'))
            for entry in manifest['artifacts']:
                data = packed.read(entry['path'])
                assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
                verified_files += 1
    result = {'scope': 'Measured code payload and Rust binary preserved; unrelated cwd; no GUI tests',
              'code_payload_sha256': payload['pyz_sha256'], 'code_payload_matches_measured_after': True,
              'rust_binary_matches_measured_after': True, 'bundled_catalog_totals': expected,
              'selected_rust_is_delivery_binary': True, 'archive_files_verified': verified_files}
    (evidence / 'delivery-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
