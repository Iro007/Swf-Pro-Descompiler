"""Freeze and repeat a bounded headless engine campaign on immutable inputs."""
from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    parser.add_argument('--stage', choices=('before', 'after'), required=True)
    args = parser.parse_args()
    if len(args.round) != 8 or not args.round.isdigit():
        parser.error('round must be YYYYMMDD')
    evidence = root / ('reports/decompiler-improvements-' + args.round)
    snapshot = evidence / (args.stage + '-binaries')
    snapshot.mkdir(parents=True, exist_ok=True)
    release = root / 'rust_swf/dist/FlashUniversalStudio-Engines'
    for candidate in sorted(release.parent.glob('FlashUniversalStudio-Engines-*'), reverse=True):
        date = candidate.name.removeprefix('FlashUniversalStudio-Engines-')
        if candidate.is_dir() and len(date) == 8 and date.isdigit() and (candidate / 'release-manifest.json').is_file():
            release = candidate
            break
    sources = ({name: release / name for name in ('FlashSuiteWorker.exe', 'rustswf.exe')}
               if args.stage == 'before' else {
                   'FlashSuiteWorker.exe': root / 'py_swf_editor/dist/FlashSuiteWorker.exe',
                   'rustswf.exe': root / 'rust_swf/target/x86_64-pc-windows-gnu/release/rustswf.exe'})
    fingerprints = []
    saved_manifest_path = evidence / (args.stage + '-manifest.json')
    saved_manifest = json.loads(saved_manifest_path.read_text(encoding='utf-8')) if saved_manifest_path.exists() else None
    for name, source in sources.items():
        destination = snapshot / name
        if destination.exists():
            expected = (next(item['sha256'] for item in saved_manifest['engines'] if item['name'] == name)
                        if saved_manifest else sha(source))
            if sha(destination) != expected:
                raise ValueError('Snapshot differs; choose a new round: ' + name)
        if not destination.exists():
            shutil.copyfile(source, destination)
        fingerprints.append({'name': name, 'bytes': destination.stat().st_size, 'sha256': sha(destination)})
    corpus = evidence / 'corpus'
    previous = root / 'reports/decompiler-improvements-20261013/corpus'
    for source in sorted(previous.rglob('*.swf')):
        destination = corpus / source.relative_to(previous)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha(destination) != sha(source):
            raise ValueError('Immutable input changed: ' + str(destination))
        if not destination.exists():
            shutil.copyfile(source, destination)
    inputs = [{'path': str(path.relative_to(corpus)), 'bytes': path.stat().st_size, 'sha256': sha(path)}
              for path in sorted(corpus.rglob('*.swf'))]
    cohorts = {'original': corpus / 'original', 'micro': corpus / 'micro',
               'switch': corpus / 'micro/ruffle_avm2_op_lookupswitch.swf'}
    manifest = {'engines': fingerprints, 'inputs': inputs,
                'cohorts': {key: str(value) for key, value in cohorts.items()},
                'scope': 'Frozen engines; exact same nine SWF hashes; smoke 1 warmup, 3 hot, 1 cold'}
    manifest_path = evidence / (args.stage + '-manifest.json')
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2)
    if manifest_path.exists() and manifest_path.read_text(encoding='utf-8') != serialized:
        raise ValueError('Existing manifest differs; no evidence overwritten')
    manifest_path.write_text(serialized, encoding='utf-8')
    env = {**os.environ, 'FLASH_SUITE_WORKSPACE': str(root), 'PYTHONIOENCODING': 'utf-8',
           'FLASH_BENCHMARK_CATALOG': str(evidence / (args.stage + '-catalog.sqlite'))}
    results = []
    for cohort, source in cohorts.items():
        output = root / ('reports/benchmark-' + args.stage + '-' + cohort + '-' + args.round)
        report_path = output / 'benchmark.json'
        if not report_path.is_file():
            if output.exists():
                raise ValueError('Existing incomplete campaign will not be overwritten')
            child = subprocess.run([str(snapshot / 'FlashSuiteWorker.exe'), 'cli', 'benchmark',
                '--input', str(source), '--output', str(output), '--profile', 'smoke'], cwd=snapshot,
                env=env, capture_output=True, text=True, encoding='utf-8', timeout=900)
            for stream in ('stdout', 'stderr'):
                (evidence / (args.stage + '-' + cohort + '.' + stream + '.log')).write_text(
                    getattr(child, stream), encoding='utf-8')
            if child.returncode:
                raise RuntimeError(child.stderr[-4000:])
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if report['completed_files'] != report['scheduled_files']:
            raise ValueError('Incomplete campaign cannot be presented as complete')
        if not report['provenance']['implementation_unchanged']:
            raise ValueError('Implementation changed during measurement')
        expected_worker = next(item['sha256'] for item in fingerprints if item['name'] == 'FlashSuiteWorker.exe')
        if report['provenance']['implementation_sha256_before'] != expected_worker:
            raise ValueError('Saved campaign measured a different worker')
        result = {'cohort': cohort, 'path': str(report_path), 'completed_files': report['completed_files']}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if not all(sha(corpus / item['path']) == item['sha256'] for item in inputs):
        raise ValueError('Input hashes changed during measurement')
    (evidence / (args.stage + '-results.json')).write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
