"""Serialize frozen family/engine semantic jobs with explicit process budgets."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
from run_ruffle_trace_probe import bounded_run
from verify_parity_source_semantics import verify_freeze, select_cases
from py_swf.corpus import read_immutable_manifest
from py_swf.benchmarks import implementation_digest


def run(corpus, output, frozen, timeout=180):
    corpus, output, frozen = Path(corpus).resolve(), Path(output).resolve(), Path(frozen).resolve()
    freeze = verify_freeze(frozen, corpus); manifest = read_immutable_manifest(corpus)
    cases = select_cases(manifest, include_holdout=True)
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'freeze': freeze, 'scheduled_jobs': len(cases) * 2, 'entries': [],
              'limits': {'job_wall_seconds': timeout, 'job_memory_bytes': 2 * 1024**3,
                         'runtime_ticks': 1, 'runtime_execution_seconds': 15, 'runtime_wall_seconds': 30},
              'claims': {'semantic_parity': False, 'superiority': False},
              'scope': 'Whole generated projects; one declared trace scenario per family; no authored-source repair'}
    script = Path(__file__).with_name('verify_parity_source_semantics.py')
    report['evaluator_sha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
    for case in cases:
        for engine in ('native', 'rust'):
            folder = output / (case['family_id'] + '-' + engine)
            command = [sys.executable, str(script), '--corpus', str(corpus), '--output', str(folder),
                       '--families', case['family_id'], '--engines', engine,
                       '--frozen-build', str(frozen), '--include-holdout']
            result = bounded_run(command, Path(__file__).resolve().parents[1], timeout=timeout)
            log = output / (case['family_id'] + '-' + engine + '.log')
            log.write_text(result['stdout'] + '\n' + result['stderr'], encoding='utf-8')
            evidence = folder / 'source-semantics.json'
            entry = {'family_id': case['family_id'], 'split': case['split'], 'engine': engine,
                     'status': 'completed' if result['returncode'] == 0 and not result['timed_out'] and evidence.is_file()
                               else 'timeout' if result['timed_out'] else 'failed',
                     'command': command, 'returncode': result['returncode'], 'wall_seconds': result['wall_seconds'],
                     'log': str(log), 'evidence': str(evidence) if evidence.is_file() else None,
                     'evidence_sha256': hashlib.sha256(evidence.read_bytes()).hexdigest() if evidence.is_file() else None}
            report['entries'].append(entry)
            if implementation_digest() != freeze['implementation_sha256']:
                raise ValueError('Frozen engine source changed; observations retained')
            (output / 'semantic-index.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(case['family_id'], engine, entry['status'], round(result['wall_seconds'], 2), flush=True)
    report['completed_jobs'] = sum(e['status'] == 'completed' for e in report['entries'])
    report['implementation_unchanged'] = implementation_digest() == freeze['implementation_sha256']
    (output / 'semantic-index.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--corpus', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--frozen-build', type=Path, required=True)
    p.add_argument('--timeout', type=int, default=180)
    a = p.parse_args(); result = run(a.corpus, a.output, a.frozen_build, a.timeout)
    print(json.dumps({'completed_jobs': result['completed_jobs'], 'scheduled_jobs': result['scheduled_jobs']}))
