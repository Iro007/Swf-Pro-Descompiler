"""Verify exact private cloud evidence and stage immutable SWF inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def read_zip(path):
    files = {}
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC failed')
        for entry in archive.infolist():
            name = entry.filename.replace('\\', '/')
            parts = PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or ':' in name or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Unsafe archive path')
            if entry.is_dir():
                continue
            if name in files or entry.file_size > 32 * 1024**2:
                raise ValueError('Unexpected duplicate or large archive entry')
            files[name] = archive.read(entry)
    return files


def main():
    root = Path(__file__).resolve().parents[2]
    transfer = root / 'reports/cloud-benchmark-20261013'
    manifest = json.loads((transfer / 'transfer_manifest.json').read_text(encoding='utf-8'))
    packages = {}
    for name, entry in manifest['packages'].items():
        path = transfer / entry['file_name']
        data = path.read_bytes()
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Transfer package hash mismatch')
        packages[name] = read_zip(path)
    integrity = json.loads(packages['data']['transfer/integrity.json'])
    for entry in integrity['original_files']:
        data = packages['data'][entry['path']]
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Original evidence hash mismatch')
    corpus = json.loads(packages['data']['corpus/manifest.json'])
    destination = root / 'benchmark_inputs/cloud-20261013'
    selected = root / 'benchmark_inputs/cloud-matched-micro-20261013'
    staged = []
    wanted = {'ruffle_avm1_closure_scope', 'ruffle_avm1_try_catch_finally', 'ruffle_avm1_timeline_function_def',
              'ruffle_avm2_closures', 'ruffle_avm2_op_lookupswitch', 'ruffle_avm2_function_call_arguments_enumerate'}
    for artifact in corpus['artifacts']:
        raw = packages['swf'][artifact['path']]
        if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
            raise ValueError('Original SWF hash mismatch')
        path = destination / artifact['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('Existing corpus differs from imported source')
        else:
            path.write_bytes(raw)
        if artifact['id'] in wanted:
            target = selected / (artifact['id'] + '.swf')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != raw:
                raise ValueError('Selected fixture was changed')
            if not target.exists():
                target.write_bytes(raw)
        staged.append({'id': artifact['id'], 'path': str(path), 'sha256': artifact['sha256']})
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'manifest.json').write_bytes(packages['data']['corpus/manifest.json'])
    result = {'packages_verified': len(packages), 'evidence_files_verified': len(integrity['original_files']),
              'swf_files_verified': len(staged), 'main_runs': len(packages['data']['results/runs.jsonl'].splitlines()),
              'heap_probe_runs': len(packages['data']['heap-probe/results/runs.jsonl'].splitlines()),
              'selected_micro_cases': len(list(selected.glob('*.swf'))), 'artifacts': staged,
              'full_archive_reference': 'https://chatgpt.com/space/page_451b96fa21c481919fad97e41cf78347',
              'scope': 'Exact cloud transfers and original SWF hashes verified; no new measurements'}
    (transfer / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'artifacts'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
