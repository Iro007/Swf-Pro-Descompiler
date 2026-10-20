"""Keep complete unit comparisons in SQLite while bounding the chart snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from py_swf.benchmark_catalog import catalog_summary, export_catalog_html


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    args = parser.parse_args()
    if len(args.round) != 8 or not args.round.isdigit():
        parser.error('round must be YYYYMMDD')
    evidence = root / ('reports/decompiler-improvements-' + args.round)
    database = root / 'reports/benchmark-catalog/benchmark.sqlite'
    connection = sqlite3.connect(database)
    backup_path = evidence / 'catalog-before-compaction.sqlite'
    if not backup_path.exists():
        with sqlite3.connect(backup_path) as backup:
            connection.backup(backup)
    before_totals = catalog_summary(database)['totals']
    with connection:
        connection.execute('CREATE TABLE IF NOT EXISTS catalog_comparison_evidence (validation_id TEXT PRIMARY KEY, original_metadata_json TEXT NOT NULL, original_sha256 TEXT NOT NULL)')
        for identity, raw in connection.execute('SELECT id, metadata_json FROM validations').fetchall():
            metadata = json.loads(raw)
            comparison = metadata.get('comparison')
            if not comparison or comparison.get('detail_storage') == 'catalog_comparison_evidence':
                continue
            if len(encoded(comparison).encode('utf-8')) < 1000:
                continue
            digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
            existing = connection.execute('SELECT original_sha256 FROM catalog_comparison_evidence WHERE validation_id=?', (identity,)).fetchone()
            if existing and existing[0] != digest:
                raise ValueError('Stored comparison differs; refusing to replace evidence')
            connection.execute('INSERT OR IGNORE INTO catalog_comparison_evidence VALUES (?,?,?)', (identity, raw, digest))
            metadata['comparison'] = {key: value for key, value in comparison.items() if not isinstance(value, (list, dict))}
            metadata['comparison'].update(detail_storage='catalog_comparison_evidence', validation_id=identity,
                                          original_metadata_sha256=digest)
            connection.execute('UPDATE validations SET metadata_json=? WHERE id=?', (encoded(metadata), identity))
    details = []
    for identity, raw, digest in connection.execute('SELECT * FROM catalog_comparison_evidence').fetchall():
        if hashlib.sha256(raw.encode('utf-8')).hexdigest() != digest:
            raise ValueError('Comparison evidence hash mismatch')
        original = json.loads(raw)
        current = json.loads(connection.execute('SELECT metadata_json FROM validations WHERE id=?', (identity,)).fetchone()[0])
        current['comparison'] = original['comparison']
        if encoded(current) != encoded(original):
            raise ValueError('Compaction changed fields other than presentation comparison')
        details.append({'validation_id': identity, 'original_sha256': digest, 'metadata': original})
    connection.close()
    summary = catalog_summary(database)
    if summary['totals'] != before_totals:
        raise ValueError('Compaction changed campaign/artifact/run/tool totals')
    output = root / 'reports/benchmark-catalog'
    (output / 'catalog-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    export_catalog_html(database, output / 'benchmark.html')
    (evidence / 'comparison-details.json').write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    proof = {'scope': 'Chart snapshot summaries; complete original metadata retained in SQLite and comparison-details.json',
             'verified_original_metadata_records': len(details), 'totals_unchanged': summary['totals'],
             'snapshot_bytes': len(encoded(summary).encode('utf-8')), 'backup': str(backup_path),
             'database_sha256': hashlib.sha256(database.read_bytes()).hexdigest()}
    (evidence / 'catalog-compaction-verification.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
