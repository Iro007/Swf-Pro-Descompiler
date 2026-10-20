"""Measure local external adapters beside frozen engines on identical SWFs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


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
    manifest = json.loads((evidence / 'after-manifest.json').read_text(encoding='utf-8'))
    snapshot = evidence / 'after-binaries'
    worker = snapshot / 'FlashSuiteWorker.exe'
    expected_worker = next(item['sha256'] for item in manifest['engines'] if item['name'] == worker.name)
    if sha(worker) != expected_worker:
        raise ValueError('Frozen worker hash differs')
    corpus = evidence / 'corpus'
    if not all(sha(corpus / item['path']) == item['sha256'] for item in manifest['inputs']):
        raise ValueError('Input hashes changed')
    config = root / 'py_swf_editor/.dev-references/extra-tools.json'
    tools = json.loads(config.read_text(encoding='utf-8'))
    if tools.get('development_only') is not True or 'pyflashkit' not in tools.get('tools', {}):
        raise ValueError('A hash-pinned isolated pyflashkit adapter is required')
    source_engines = ['native', 'rust', 'jpexs', 'pyflashkit']
    if 'flare' in tools['tools']:
        source_engines.append('flare')
    assembly_engines = ['native', 'rust', 'pyflashkit', 'rabcdasm']
    if 'flasm' in tools['tools']:
        assembly_engines.append('flasm')
    env = {**os.environ, 'FLASH_SUITE_WORKSPACE': str(root), 'PYTHONIOENCODING': 'utf-8',
           'FLASH_BENCHMARK_CATALOG': str(evidence / 'external-catalog.sqlite')}
    results = []
    for operation, engines in [('disassemble', assembly_engines), ('source', source_engines)]:
        arguments = evidence / ('external-' + operation + '-args.json')
        arguments.write_text(json.dumps({'engines': engines, 'operation': operation,
                                         'tool_config': str(config)}, indent=2), encoding='utf-8')
        for cohort in ('switch', 'micro', 'original'):
            source = manifest['cohorts'][cohort]
            output = root / ('reports/benchmark-external-' + operation + '-' + cohort + '-' + args.round)
            report_path = output / 'benchmark.json'
            if not report_path.is_file():
                if output.exists():
                    raise ValueError('Existing incomplete campaign will not be overwritten')
                child = subprocess.run([str(worker), 'cli', 'benchmark', '--input', source,
                    '--output', str(output), '--profile', 'smoke', '--args-file', str(arguments)],
                    cwd=snapshot, env=env, capture_output=True, text=True, encoding='utf-8', timeout=1500)
                for stream in ('stdout', 'stderr'):
                    (evidence / ('external-' + operation + '-' + cohort + '.' + stream + '.log')).write_text(
                        getattr(child, stream), encoding='utf-8')
                if child.returncode:
                    raise RuntimeError(child.stderr[-4000:])
            report = json.loads(report_path.read_text(encoding='utf-8'))
            if report['completed_files'] != report['scheduled_files'] or report['operation'] != operation:
                raise ValueError('Incomplete or mismatched external campaign')
            if not report['provenance']['implementation_unchanged'] or report['provenance']['implementation_sha256_before'] != expected_worker:
                raise ValueError('External campaign measured different code')
            result = {'operation': operation, 'cohort': cohort, 'path': str(report_path),
                      'completed_files': report['completed_files'], 'engines': engines,
                      'observations': [{'file': case['relative'], 'engine': engine['engine'],
                          'status': engine['status'], 'hot_successful': engine['hot']['successful'],
                          'failures': len(engine['failures']), 'output_artifacts': len(engine.get('artifacts', []))}
                          for case in report['cases'] for engine in case['engines']]}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if not all(sha(corpus / item['path']) == item['sha256'] for item in manifest['inputs']):
        raise ValueError('Input hashes changed during external measurement')
    (evidence / 'external-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
