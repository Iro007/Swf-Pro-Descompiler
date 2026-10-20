"""Repeat the frozen baseline corpus with final engines, keeping source identities."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[2]
    evidence = root / 'reports/decompiler-improvements-20261013'
    manifest = json.loads((evidence / 'baseline-manifest.json').read_text(encoding='utf-8'))
    snapshot = evidence / 'after-binaries'
    snapshot.mkdir(parents=True, exist_ok=True)
    inputs = evidence / 'corpus'
    assert all(sha(inputs / record['path']) == record['sha256'] for record in manifest['inputs'])
    fingerprints = []
    sources = {'FlashSuiteWorker.exe': root / 'py_swf_editor/dist/FlashSuiteWorker.exe',
               'rustswf.exe': root / 'RUST SWF/target/x86_64-pc-windows-gnu/release/rustswf.exe'}
    for name, source in sources.items():
        destination = snapshot / name
        if destination.exists() and sha(destination) != sha(source):
            raise ValueError('The final-engine snapshot differs; select a new campaign')
        if not destination.exists():
            shutil.copyfile(source, destination)
        fingerprints.append({'name': name, 'bytes': destination.stat().st_size, 'sha256': sha(destination)})
    (evidence / 'after-manifest.json').write_text(json.dumps({'engines': fingerprints, 'inputs': manifest['inputs']}, ensure_ascii=False, indent=2), encoding='utf-8')
    environment = {**os.environ, 'FLASH_SUITE_WORKSPACE': str(root), 'PYTHONIOENCODING': 'utf-8',
                   'FLASH_BENCHMARK_CATALOG': str(evidence / 'after-catalog.sqlite')}
    results = []
    for label, source in manifest['cohorts'].items():
        output = root / ('reports/benchmark-after-' + label + '-20261013')
        if not (output / 'benchmark.json').is_file():
            if output.exists():
                raise ValueError('Existing incomplete campaign will not be overwritten')
            child = subprocess.run([str(snapshot / 'FlashSuiteWorker.exe'), 'cli', 'benchmark', '--input', source,
                '--output', str(output), '--profile', 'smoke'], cwd=snapshot, env=environment,
                capture_output=True, text=True, encoding='utf-8', timeout=900)
            (evidence / ('after-' + label + '.stdout.log')).write_text(child.stdout, encoding='utf-8')
            (evidence / ('after-' + label + '.stderr.log')).write_text(child.stderr, encoding='utf-8')
            if child.returncode:
                raise RuntimeError(child.stderr[-4000:])
        report = json.loads((output / 'benchmark.json').read_text(encoding='utf-8'))
        assert report['completed_files'] == report['scheduled_files']
        assert report['provenance']['implementation_unchanged']
        result = {'cohort': label, 'completed_files': report['completed_files'], 'path': str(output / 'benchmark.json')}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        results.append(result)
    assert all(sha(inputs / record['path']) == record['sha256'] for record in manifest['inputs'])
    (evidence / 'after-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
