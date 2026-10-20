"""Measure an authored Flex feature fixture separately from the fixed corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.compare_engine_progress import compare


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    args = parser.parse_args()
    if len(args.round) != 8 or not args.round.isdigit():
        parser.error('round must be YYYYMMDD')
    evidence = root / ('reports/decompiler-improvements-' + args.round)
    source = evidence / 'feature-fixtures/RustForEach.swf'
    input_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    reports = {}
    for stage in ('before', 'after'):
        snapshot = evidence / (stage + '-binaries')
        worker = snapshot / 'FlashSuiteWorker.exe'
        manifest = json.loads((evidence / (stage + '-manifest.json')).read_text(encoding='utf-8'))
        expected = next(item['sha256'] for item in manifest['engines'] if item['name'] == worker.name)
        if hashlib.sha256(worker.read_bytes()).hexdigest() != expected:
            raise ValueError('Feature probe snapshot hash differs')
        output = root / ('reports/benchmark-' + stage + '-feature-' + args.round)
        report_path = output / 'benchmark.json'
        if not report_path.is_file():
            if output.exists():
                raise ValueError('Incomplete feature campaign will not be overwritten')
            env = {**os.environ, 'FLASH_SUITE_WORKSPACE': str(root), 'PYTHONIOENCODING': 'utf-8',
                   'FLASH_BENCHMARK_CATALOG': str(evidence / 'feature-catalog.sqlite')}
            child = subprocess.run([str(worker), 'cli', 'benchmark', '--input', str(source),
                '--output', str(output), '--profile', 'smoke'], cwd=snapshot,
                env=env, capture_output=True, text=True, encoding='utf-8', timeout=900)
            for stream in ('stdout', 'stderr'):
                (evidence / ('feature-' + stage + '.' + stream + '.log')).write_text(
                    getattr(child, stream), encoding='utf-8')
            if child.returncode:
                raise RuntimeError(child.stderr[-4000:])
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if report['completed_files'] != 1 or report['cases'][0]['sha256'] != input_sha:
            raise ValueError('Feature campaign input differs or is incomplete')
        if report['provenance']['implementation_sha256_before'] != expected:
            raise ValueError('Feature campaign measured different code')
        reports[stage] = report
    result = {'scope': 'Separate authored Flex fixture; not added to the fixed nine-SWF improvement denominator',
              'input_sha256': input_sha, 'claims': {'semantic_parity': False, 'superiority': False},
              'rows': compare(reports['before'], reports['after'], 'feature_for_each')}
    (evidence / 'feature-comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
