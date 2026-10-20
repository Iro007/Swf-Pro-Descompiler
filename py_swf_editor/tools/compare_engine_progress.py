"""Compare frozen campaigns by exact common bytecode units, not pooled scores."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path


def unit_key(unit):
    return (unit.get('document_id'), unit.get('language'), unit.get('bytecode_sha256'),
            unit.get('bytecode_bytes'), tuple(unit.get('tag_path', [])))


def source_available(unit):
    return unit.get('generation') == 'available'


def verify_unit_key(unit):
    return isinstance(unit.get('document_id'), str) and isinstance(unit.get('bytecode_sha256'), str) and len(unit['bytecode_sha256']) == 64


def compare(before, after, cohort):
    old = {case['sha256']: case for case in before['cases']}
    new = {case['sha256']: case for case in after['cases']}
    if set(old) != set(new):
        raise ValueError('Before/after input hashes differ')
    rows = []
    for digest, case in old.items():
        previous = {item['engine']: item for item in case['engines']}
        current = {item['engine']: item for item in new[digest]['engines']}
        for engine in sorted(previous.keys() & current.keys()):
            left, right = previous[engine], current[engine]
            units_before = {unit_key(u): u for u in left.get('units', []) if verify_unit_key(u)}
            units_after = {unit_key(u): u for u in right.get('units', []) if verify_unit_key(u)}
            if len(units_before) != sum(verify_unit_key(u) for u in left.get('units', [])) or len(units_after) != sum(verify_unit_key(u) for u in right.get('units', [])):
                raise ValueError('Duplicate bytecode unit identities cannot be collapsed')
            common = units_before.keys() & units_after.keys()
            matched_common = [key for key in common if units_before[key].get('identity_verified', True) and units_after[key].get('identity_verified', True)]
            gained = [units_after[key] for key in matched_common if not source_available(units_before[key]) and source_available(units_after[key])]
            lost = [units_before[key] for key in matched_common if source_available(units_before[key]) and not source_available(units_after[key])]
            new_keys = units_after.keys() - units_before.keys()
            new_units = [units_after[key] for key in new_keys]
            old_units = [units_before[key] for key in units_before.keys() - units_after.keys()]
            rows.append({'cohort': cohort, 'file': case['relative'], 'input_sha256': digest, 'engine': engine,
                'status_before': left.get('status'), 'status_after': right.get('status'),
                'expected_before': left['quality']['expected_units_reported'], 'expected_after': right['quality']['expected_units_reported'],
                'reported_source_before': left['quality']['source_available'], 'reported_source_after': right['quality']['source_available'],
                'common_verified_units': len(matched_common),
                'source_on_common_before': sum(source_available(units_before[k]) for k in matched_common),
                'source_on_common_after': sum(source_available(units_after[k]) for k in matched_common),
                'gained_source_units': [{'id': u['document_id'], 'name': u.get('name'), 'bytecode_sha256': u['bytecode_sha256']} for u in gained],
                'lost_source_units': [{'id': u['document_id'], 'name': u.get('name'), 'bytecode_sha256': u['bytecode_sha256']} for u in lost],
                'new_inventory_units': len(new_units), 'new_inventory_source': sum(source_available(u) for u in new_units),
                'removed_inventory_units': len(old_units),
                'recovered_identity_units': sum(not units_before[k].get('identity_verified', True) and units_after[k].get('identity_verified', True) for k in common),
                'hot_before_ms': left['hot'].get('median_ms'), 'hot_after_ms': right['hot'].get('median_ms'),
                'rss_before_bytes': left['hot'].get('peak_tree_rss_bytes'), 'rss_after_bytes': right['hot'].get('peak_tree_rss_bytes'),
                'failures_before': len(left.get('failures', [])), 'failures_after': len(right.get('failures', []))})
    return rows


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', default='20261013')
    args = parser.parse_args()
    if len(args.round) != 8 or not args.round.isdigit():
        parser.error('round must be YYYYMMDD')
    rows, provenance = [], []
    for cohort in ('original', 'micro', 'switch'):
        before_path = root / ('reports/benchmark-before-' + cohort + '-' + args.round + '/benchmark.json')
        after_path = root / ('reports/benchmark-after-' + cohort + '-' + args.round + '/benchmark.json')
        left, right = json.loads(before_path.read_text(encoding='utf-8')), json.loads(after_path.read_text(encoding='utf-8'))
        if left['completed_files'] != left['scheduled_files'] or right['completed_files'] != right['scheduled_files']:
            raise ValueError('Incomplete campaign cannot be presented as a complete comparison')
        rows.extend(compare(left, right, cohort))
        provenance.append({'cohort': cohort, 'before': str(before_path), 'after': str(after_path),
            'before_sha256': hashlib.sha256(before_path.read_bytes()).hexdigest(), 'after_sha256': hashlib.sha256(after_path.read_bytes()).hexdigest(),
            'environment_before': left['environment'], 'environment_after': right['environment'],
            'configuration_before': left['configuration'], 'configuration_after': right['configuration']})
    engines = {}
    for engine in ('native', 'rust', 'jpexs'):
        subset = [r for r in rows if r['engine'] == engine]
        engines[engine] = {key: sum(r[key] for r in subset) for key in ('common_verified_units', 'source_on_common_before', 'source_on_common_after', 'new_inventory_units', 'new_inventory_source', 'recovered_identity_units', 'failures_before', 'failures_after')}
        engines[engine]['gained_source_units'] = sum(len(r['gained_source_units']) for r in subset)
        engines[engine]['lost_source_units'] = sum(len(r['lost_source_units']) for r in subset)
    result = {'schema_version': 1, 'kind': 'engine_progress', 'cohorts': provenance, 'rows': rows, 'engines': engines,
        'scope': 'Exact common bytecode identities; discovered units and mapping changes reported separately',
        'claims': {'semantic_parity': False, 'superiority': False, 'performance_winner': None},
        'timing_note': 'Different background load and expanded inventory can affect time and RSS; no isolated algorithm speed claim'}
    output = root / ('reports/decompiler-improvements-' + args.round)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    body = []
    labels = {'native': 'PySWF', 'rust': 'RustSWF', 'jpexs': 'JPEXS'}
    for row in rows:
        cells = [row['file'], labels.get(row['engine'], row['engine']), str(row['common_verified_units']),
                 str(row['source_on_common_before']), str(row['source_on_common_after']),
                 str(len(row['gained_source_units'])), str(len(row['lost_source_units'])),
                 str(row['new_inventory_units']), str(row['new_inventory_source']),
                 f"{row['status_before']} → {row['status_after']}"]
        body.append('<tr>' + ''.join('<td>' + html.escape(cell) + '</td>' for cell in cells) + '</tr>')
    charts = []
    for engine, stats in engines.items():
        maximum = max(1, stats['common_verified_units'])
        charts.append('<section><h2>' + labels[engine] + ': fuente en unidades comunes</h2><p>Eje horizontal: unidades con fuente; eje vertical: versión antes/después.</p>' +
            ''.join('<div><span>' + label + '</span><i style="width:' + str(stats[key] / maximum * 100) + '%"></i><b>' + str(stats[key]) + '/' + str(maximum) + '</b></div>' for label, key in [('Antes', 'source_on_common_before'), ('Después', 'source_on_common_after')]) + '</section>')
    page = '<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Progreso PySWF y RustSWF</title><style>body{font:15px system-ui;color:#d8dde3;background:#171b20;margin:0}main{max-width:1300px;padding:28px;margin:auto}h1{font-size:24px}h2{font-size:18px}section{padding:12px 0;border-bottom:1px solid #39424c}section div{display:flex;align-items:center;gap:12px;margin:10px 0}section span{width:75px}i{background:#559aa6;height:16px;max-width:75%;display:inline-block}table{border-collapse:collapse;min-width:900px;width:100%;font-size:13px}td,th{text-align:left;padding:9px;border-bottom:1px solid #39424c}.scroll{overflow:auto}.note{padding:14px;background:#222c33;line-height:1.5}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><main><h1>Progreso de PySWF y RustSWF</h1><p class="note">Comparación sobre los mismos nueve SWF, con hashes y unidades de bytecode coincidentes. Las funciones recién identificadas y la corrección de correspondencia se contabilizan aparte. La presencia de fuente no demuestra equivalencia semántica. Los tiempos no establecen un ganador.</p>' + ''.join(charts) + '<p>Fuente: campañas before/after identificadas en la procedencia, Windows local. Barras: recuentos, sin mezclar denominadores nuevos.</p><div class="scroll"><table><thead><tr>' + ''.join('<th>'+title+'</th>' for title in ['Archivo','Motor','Unidades comunes','Fuente antes','Fuente después','Ganadas','Retiradas','Unidades nuevas','Fuente nueva','Correspondencia']) + '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div><details><summary>Unidades modificadas y procedencia</summary><pre>' + html.escape(json.dumps(result, ensure_ascii=False, indent=2)) + '</pre></details></main></html>'
    (output / 'comparison.html').write_text(page, encoding='utf-8')
    print(json.dumps({'engines': engines, 'files': len({row['input_sha256'] for row in rows}), 'html': str(output / 'comparison.html')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
