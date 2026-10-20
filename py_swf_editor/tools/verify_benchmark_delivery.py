"""Frozen worker/Rust/HTML verification from an unrelated cwd; no GUI launch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=root / 'RUST SWF/dist/FlashUniversalStudio-Benchmarks')
    args = parser.parse_args()
    folder = args.folder.resolve(strict=True)
    archive_path = folder.parent / 'FlashUniversalStudio-Benchmarks-portable.zip'
    archive_files_verified = 0
    if archive_path.is_file():
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            manifest = json.loads(archive.read('release-manifest.json'))
            for record in manifest['artifacts']:
                raw = archive.read(record['path'])
                assert len(raw) == record['bytes'] and hashlib.sha256(raw).hexdigest() == record['sha256']
                archive_files_verified += 1
    database = root / 'reports/benchmark-catalog/benchmark.sqlite'
    original_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    worker = folder / 'FlashSuiteWorker.exe'
    expected_totals = json.loads(folder.joinpath('catalog-summary.json').read_text(encoding='utf-8'))['totals']
    with tempfile.TemporaryDirectory(prefix='flash-catalog-portable-') as temporary:
        environment = {**os.environ, 'FLASH_SUITE_WORKSPACE': temporary, 'PYTHONIOENCODING': 'utf-8'}
        environment.pop('FLASH_BENCHMARK_CATALOG', None)
        result = subprocess.run([str(worker), 'cli', 'rpc', '--rpc-operation', 'benchmark_catalog'], cwd=temporary, env=environment,
                                capture_output=True, text=True, encoding='utf-8', timeout=60, check=True)
        catalog = json.loads(result.stdout)
        assert catalog['available'] and catalog['totals'] == expected_totals
        assert any(c['source_kind'] == 'cloud_bundle' and c['run_count'] == 338 for c in catalog['campaigns'])
        bundled_database = catalog['database']
        assert 'benchmark_data' in bundled_database and not str(root) in bundled_database
        result = subprocess.run([str(folder / 'rustswf.exe'), 'capabilities'], cwd=temporary, env=environment,
                                capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
        capabilities = json.loads(result.stdout)
        assert capabilities['display_name'] == 'RustSWF' and capabilities['version'] == '0.3.0'
        swf = root / 'benchmark_inputs/cloud-matched-micro-20261013/ruffle_avm2_closures.swf'
        input_hash = hashlib.sha256(swf.read_bytes()).hexdigest()
        subprocess.run([str(worker), 'cli', 'inspect', '--input', str(swf)], cwd=temporary, env=environment,
                       capture_output=True, text=True, encoding='utf-8', timeout=60, check=True)
        assert hashlib.sha256(swf.read_bytes()).hexdigest() == input_hash
    assert hashlib.sha256(database.read_bytes()).hexdigest() == original_hash
    page = folder.joinpath('benchmarks.html').read_text(encoding='utf-8')
    assert 'Catálogo de benchmarks Flash' in page and 'pyflashkit' in page and 'Codex cloud' in page
    evidence = {'scope': 'Frozen binaries from unrelated cwd; no GUI launched', 'totals': catalog['totals'],
                'bundled_catalog_used': True, 'cloud_run_count': 338, 'original_database_unchanged': True,
                'original_swf_unchanged': True, 'rust_version': capabilities['version'],
                'archive_files_verified': archive_files_verified,
                'files': [{'path': str(p.relative_to(folder)), 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                          for p in sorted(folder.iterdir()) if p.is_file()]}
    destination = root / 'reports/benchmark-catalog/portable-verification.json'
    destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in evidence.items() if key != 'files'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
