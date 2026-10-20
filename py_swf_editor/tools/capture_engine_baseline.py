"""Freeze previous engines and measure immutable source reconstruction cohorts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[2]
    evidence = root / 'reports/decompiler-improvements-20261013'
    snapshot = evidence / 'baseline-binaries'
    snapshot.mkdir(parents=True, exist_ok=True)
    release = root / 'RUST SWF/dist/FlashUniversalStudio-Benchmarks'
    fingerprints = []
    for name in ('FlashSuiteWorker.exe', 'rustswf.exe'):
        if not (snapshot / name).exists():
            shutil.copyfile(release / name, snapshot / name)
        assert sha(snapshot / name) == sha(release / name)
        fingerprints.append({'name': name, 'bytes': (snapshot / name).stat().st_size, 'sha256': sha(snapshot / name)})
    corpus = evidence / 'corpus'
    original = corpus / 'original'
    micro = corpus / 'micro'
    original.mkdir(parents=True, exist_ok=True)
    micro.mkdir(exist_ok=True)
    for path in sorted((root / 'benchmark_inputs').glob('*.swf')):
        shutil.copyfile(path, original / path.name)
    for path in sorted((root / 'benchmark_inputs/cloud-matched-micro-20261013').glob('*.swf')):
        shutil.copyfile(path, micro / path.name)
    cohorts = {'original': original, 'micro': micro,
               'switch': micro / 'ruffle_avm2_op_lookupswitch.swf'}
    inputs = [{'path': str(path.relative_to(corpus)), 'bytes': path.stat().st_size, 'sha256': sha(path)}
              for path in sorted(corpus.rglob('*.swf'))]
    (evidence / 'baseline-manifest.json').write_text(json.dumps({'engines': fingerprints, 'inputs': inputs,
        'cohorts': {key: str(value) for key, value in cohorts.items()},
        'scope': 'Frozen previous engines, exact corpus; warmups and failed observations retained'}, ensure_ascii=False, indent=2), encoding='utf-8')
    environment = {**os.environ, 'FLASH_SUITE_WORKSPACE': str(root), 'PYTHONIOENCODING': 'utf-8',
                   'FLASH_BENCHMARK_CATALOG': str(evidence / 'baseline-catalog.sqlite')}
    results = []
    for label, source in cohorts.items():
        output = root / ('reports/benchmark-before-' + label + '-20261013')
        if (output / 'benchmark.json').is_file():
            report = json.loads((output / 'benchmark.json').read_text(encoding='utf-8'))
            if report['completed_files'] != report['scheduled_files']:
                raise ValueError('Existing baseline campaign is incomplete')
            result = {'path': str(output / 'benchmark.json'), 'completed_files': report['completed_files'], 'claims': report['claims']}
            results.append(result)
            print(json.dumps({'cohort': label, 'resumed_saved_campaign': True, **result}), flush=True)
            continue
        if output.exists():
            raise ValueError('Existing incomplete baseline will not be overwritten')
        command = [str(snapshot / 'FlashSuiteWorker.exe'), 'cli', 'benchmark', '--input', str(source),
                   '--output', str(output), '--profile', 'smoke']
        child = subprocess.run(command, cwd=snapshot, env=environment, capture_output=True, text=True,
                               encoding='utf-8', timeout=900)
        (evidence / ('before-' + label + '.stdout.log')).write_text(child.stdout, encoding='utf-8')
        (evidence / ('before-' + label + '.stderr.log')).write_text(child.stderr, encoding='utf-8')
        if child.returncode:
            raise RuntimeError(child.stderr[-4000:])
        # This human CLI emits BENCH progress before its final JSON. The saved
        # report is the measured machine-readable artifact and remains exact.
        report = json.loads((output / 'benchmark.json').read_text(encoding='utf-8'))
        result = {'path': str(output / 'benchmark.json'), 'completed_files': report['completed_files'], 'claims': report['claims']}
        print(json.dumps({'cohort': label, 'completed_files': result['completed_files'], 'path': result['path']}, ensure_ascii=False), flush=True)
        results.append(result)
    assert all(sha(corpus / record['path']) == record['sha256'] for record in inputs)
    (evidence / 'baseline-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
