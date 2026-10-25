"""Run exact bounded trace comparisons in the upstream headless Ruffle harness."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from py_swf.benchmark_process import WindowsJob


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bounded_run(command, cwd, timeout=30, memory=2 * 1024**3):
    import psutil
    environment = {key: value for key, value in os.environ.items() if key.upper() in
                   ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'PATHEXT', 'COMSPEC')}
    environment['RUFFLE_TEST_OPTS'] = '--compile-mode use-precompiled'
    environment['RUST_LOG'] = 'error'
    job = None
    start = time.perf_counter()
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if os.name == 'nt':
        flags |= 4  # CREATE_SUSPENDED: enforce the job before target evaluation.
    with subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags) as process:
        try:
            if os.name == 'nt':
                job = WindowsJob(process.pid, memory)
                resume = ctypes.WinDLL('ntdll').NtResumeProcess
                resume.argtypes = [ctypes.c_void_p]
                resume.restype = ctypes.c_long
                if resume(int(process._handle)) != 0:
                    raise OSError('Could not resume bounded runtime process')
            stdout, stderr = process.communicate(timeout=timeout)
            return {'returncode': process.returncode, 'stdout': stdout.decode('utf-8', errors='replace'),
                    'stderr': stderr.decode('utf-8', errors='replace'),
                    'wall_seconds': time.perf_counter() - start, 'memory_limit_bytes': memory,
                    'peak_memory_bytes': job.peak_bytes() if job else None,
                    'timeout_seconds': timeout, 'timed_out': False}
        except subprocess.TimeoutExpired:
            peak = job.peak_bytes() if job else None
            if job:
                job.close(); job = None
            else:
                for child in psutil.Process(process.pid).children(recursive=True):
                    child.kill()
                process.kill()
            stdout, stderr = process.communicate()
            return {'returncode': process.returncode, 'stdout': stdout.decode('utf-8', errors='replace'),
                    'stderr': stderr.decode('utf-8', errors='replace'), 'timed_out': True,
                    'wall_seconds': time.perf_counter() - start, 'memory_limit_bytes': memory,
                    'peak_memory_bytes': peak, 'timeout_seconds': timeout}
        finally:
            if job:
                job.close()


def probe(fixture, executable, output):
    fixture = Path(fixture).resolve(strict=True)
    manifest = json.loads(fixture.read_text(encoding='utf-8'))
    expected = manifest.get('expected_trace')
    if not isinstance(expected, list) or not all(isinstance(line, str) for line in expected):
        raise ValueError('Trace fixture must declare exact expected_trace lines')
    ticks = manifest.get('ticks', 1)
    if not isinstance(ticks, int) or isinstance(ticks, bool) or not 1 <= ticks <= 120:
        raise ValueError('Runtime scenario tick budget must be 1..120')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for label in ('original', 'reconstructed'):
        source = Path(manifest[label + '_swf'])
        if not source.is_absolute():
            source = fixture.parent / source
        source = source.resolve(strict=True)
        input_sha = sha(source)
        name = 'trace_' + label
        folder = output / 'harness/tests/swfs' / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'test.swf').write_bytes(source.read_bytes())
        (folder / 'output.txt').write_text('\n'.join(expected) + '\n', encoding='utf-8')
        (folder / 'test.toml').write_text('num_ticks = ' + str(ticks) + '\nlog_warnings = false\n'
            '[player_options]\nversion = 32\nwith_audio = false\nwith_video = false\n'
            'max_execution_duration = { secs = 15, nanos = 0 }\n', encoding='utf-8')
        command = [str(executable), name, '--exact', '--test-threads', '1', '--nocapture']
        result = bounded_run(command, output / 'harness')
        (output / (label + '.stdout.log')).write_text(result['stdout'], encoding='utf-8')
        (output / (label + '.stderr.log')).write_text(result['stderr'], encoding='utf-8')
        # The upstream harness must report one executed test, not an empty
        # selector, before its exact output comparison can count as evidence.
        passed = result['returncode'] == 0 and '1 passed' in result['stdout'] and '0 failed' in result['stdout'] and not result['timed_out']
        if sha(source) != input_sha:
            raise ValueError('Original runtime input changed')
        records.append({'label': label, 'input_sha256': input_sha, 'exact_expected_trace_passed': passed,
                        'command': command, 'run': result})
    original_ok, reconstructed_ok = [record['exact_expected_trace_passed'] for record in records]
    status = 'passed_bounded_trace' if original_ok and reconstructed_ok else 'runtime_reference_unsupported' if not original_ok else 'behavior_mismatch'
    result = {'status': status, 'fixture_sha256': sha(fixture), 'expected_trace': expected,
              'records': records, 'runtime_sha256': sha(executable), 'runtime_commit': '8715aeec59d1178b21f9135b05861e12269e8207',
              'scope': 'Exact trace at declared ticks; no renderer/audio/video/network; not general Flash equivalence',
              'limits': {'ticks': ticks, 'execution_seconds': 15, 'wall_seconds': 30, 'job_memory_bytes': 2 * 1024**3},
              'claims': {'semantic_parity': False, 'superiority': False}}
    (output / 'runtime-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, default=Path(os.environ['LOCALAPPDATA']) /
                        'FlashParityRuntime/ruffle-target/x86_64-pc-windows-gnu/debug/deps/tests-470feeee72990987.exe')
    args = parser.parse_args()
    result = probe(args.fixture, args.runtime.resolve(strict=True), args.output)
    print(json.dumps({'status': result['status'], 'runtime_commit': result['runtime_commit'],
                      'records': [{key: record[key] for key in ('label', 'input_sha256', 'exact_expected_trace_passed')} for record in result['records']]}, indent=2))
    return 0 if result['status'] == 'passed_bounded_trace' else 1


if __name__ == '__main__':
    raise SystemExit(main())
