"""Repeat a bounded common-corpus campaign on Windows using the current Rust CLI."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from py_swf.universal import UniversalSuite


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('smoke', 'full'), default='smoke')
    parser.add_argument('--output', type=Path, default=root / 'reports/benchmark-cloud-matched-windows-20261013')
    parser.add_argument('--corpus', type=Path, default=root / 'benchmark_inputs/cloud-matched-micro-20261013')
    parser.add_argument('--rust-cli', type=Path, default=root / 'RUST SWF/target/x86_64-pc-windows-gnu/release/rustswf.exe')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Selecciona un destino nuevo para conservar la campaña anterior')
    rust = args.rust_cli.resolve(strict=True)
    capabilities = json.loads(subprocess.run([str(rust), 'capabilities'], check=True, capture_output=True, text=True, timeout=15).stdout)
    originals = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(args.corpus.glob('*.swf'))}
    if not originals:
        raise ValueError('Ejecuta primero tools/verify_cloud_transfer.py para preparar el corpus común')
    with tempfile.TemporaryDirectory(prefix='flash-matched-benchmark-') as directory:
        suite = UniversalSuite(state_dir=Path(directory) / 'state', tools_path=Path(directory) / 'tools.json')
        try:
            suite.registry.tools['rust'] = {'path': str(rust), 'sha256': hashlib.sha256(rust.read_bytes()).hexdigest(), 'version': capabilities['version']}
            result = suite.benchmark({'corpus': str(args.corpus), 'output': str(args.output), 'profile': args.profile,
                                      'operation': 'source', 'engines': ['native', 'rust', 'jpexs']})
        finally:
            suite.close()
    after = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in originals}
    if after != originals:
        raise ValueError('El corpus original cambió durante la campaña')
    evidence = {'original_hashes_before': originals, 'original_hashes_after': after,
                'originals_preserved': True, 'rust_capabilities': capabilities, 'result': result,
                'scope': 'Windows common micro-fixtures; independent campaign, no pooled Linux/Windows ranking'}
    args.output.joinpath('matched-corpus-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
