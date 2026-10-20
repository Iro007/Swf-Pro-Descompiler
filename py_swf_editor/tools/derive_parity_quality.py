"""Derive scoped counters from frozen full observations, preserving raw reports."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from py_swf.benchmarks import quality, source_available_in_scope


def derive(source, output):
    source, output = Path(source), Path(output)
    index = json.loads((source / 'correctness-index.json').read_text())
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    for entry in index['entries']:
        if not entry.get('path'):
            entries.append(dict(entry)); continue
        raw_path = Path(entry['path']); raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('Frozen observation changed')
        report = json.loads(raw); case = report['cases'][0]
        inventory = case['canonical_inventory']; result = case['engines'][0]
        observations = result['units']
        blocked = result.get('status') in ('not_configured', 'not_applicable', 'failed', 'output_unmapped')
        blocked = blocked or result.get('inventory_status') == 'disagreement'
        scopes = {}
        for scope, count in inventory['scope_denominators'].items():
            selected = [u for u in observations if u.get('scope') == scope]
            verified = [u for u in selected if u.get('identity_verified') is True]
            scopes[scope] = {'canonical_denominator': count, 'reported': len(selected),
                'identity_verified': len(verified),
                'source_available': None if blocked else sum(source_available_in_scope(u) for u in verified)}
        origins = {}
        for origin in ('application', 'dependency', 'mixed', 'unknown'):
            origins[origin] = {}
            for scope in scopes:
                ids = {u['document_id'] for u in inventory['units'] if u['scope'] == scope and u.get('code_origin') == origin}
                selected = [u for u in observations if u.get('scope') == scope and u.get('document_id') in ids and u.get('identity_verified') is True]
                origins[origin][scope] = {'canonical_denominator': len(ids), 'identity_verified': len(selected),
                    'source_available': None if blocked else sum(source_available_in_scope(u) for u in selected)}
        result['quality'] = {**quality(result), 'scope_metrics': scopes, 'origin_scope_metrics': origins}
        result['include_scope'] = sorted(scopes)
        result['valid_performance_summary'] = False
        report['derived_from'] = {'path': str(raw_path), 'sha256': entry['sha256'],
            'operation': 'scoped counters from exact full unit observations; no new engine execution',
            'derivation_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        report['label'] = 'Código congelado: ' + entry['family_id'] + ' / ' + entry['engine']
        path = output / entry['family_id'] / entry['engine'] / 'benchmark.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(report, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        path.write_bytes(data)
        entries.append({**entry, 'path': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                        'bytes': len(data), 'raw_path': str(raw_path), 'raw_sha256': entry['sha256']})
    derived = {**index, 'entries': entries, 'derived_from_index_sha256': hashlib.sha256((source / 'correctness-index.json').read_bytes()).hexdigest()}
    (output / 'correctness-index.json').write_text(json.dumps(derived, indent=2))
    return {'entries': len(entries), 'output': str(output)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--source', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(); print(json.dumps(derive(args.source, args.output)))
