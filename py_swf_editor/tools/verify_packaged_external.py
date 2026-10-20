"""Read back the frozen adapter using an isolated external runtime on Windows."""
import argparse
import base64
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from py_swf.benchmark_process import Worker


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    args = parser.parse_args()
    if len(args.round) != 8 or not args.round.isdigit():
        parser.error('round must be YYYYMMDD')
    evidence = root / ('reports/decompiler-improvements-' + args.round)
    worker = evidence / 'after-binaries/FlashSuiteWorker.exe'
    os.environ['FLASH_SUITE_WORKSPACE'] = str(root)
    data = (root / 'benchmark_inputs/as3_cross_compile.flex.swf').read_bytes()
    command = [str(worker), 'external-benchmark', '--engine', 'pyflashkit',
               '--config', str(root / 'py_swf_editor/.dev-references/extra-tools.json')]
    with Worker(command, cwd=worker.parent, timeout=60, memory_bytes=1024**3) as child:
        result = child.request({'operation': 'decompile', 'swf_base64': base64.b64encode(data).decode('ascii')})
    if not result.get('ok') or result.get('status') != 'output_unmapped':
        raise ValueError('Frozen external adapter failed: ' + json.dumps(result))
    if result.get('source_unit_coverage') != 'not_verified' or not result.get('artifacts'):
        raise ValueError('External output/coverage state differs')
    (evidence / 'packaged-external-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: result.get(key) for key in ('status', 'eligible_units', 'output_summary',
                     'source_unit_coverage', 'native_returncode', 'time_scope', 'tool_fingerprint')}, indent=2))


if __name__ == '__main__':
    main()
