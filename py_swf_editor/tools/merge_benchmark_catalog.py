"""Import saved local reports and cloud evidence, without starting an engine."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from py_swf.benchmark_catalog import catalog_summary, export_catalog_html, import_cloud_bundle, import_local_report


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=root)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--cloud', type=Path)
    parser.add_argument('--cloud-sha256')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    output = args.output or workspace / 'reports/benchmark-catalog'
    output.mkdir(parents=True, exist_ok=True)
    database = args.database or output / 'benchmark.sqlite'
    sources = []
    for directory in (workspace / 'reports', workspace / 'py_swf_editor/reports'):
        if directory.is_dir():
            for candidate in sorted(directory.glob('*/benchmark.json')):
                if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size <= 32 * 1024**2:
                    sources.append(candidate)
    imports, failures = [], []
    for source in sources:
        try:
            imports.append({'path': str(source), **import_local_report(database, source)})
        except (OSError, ValueError, TypeError, KeyError) as error:
            failures.append({'path': str(source), 'error': str(error)})
    if args.cloud:
        imports.append({'path': str(args.cloud.resolve()), **import_cloud_bundle(database, args.cloud, expected_sha256=args.cloud_sha256)})
    if not database.is_file():
        raise ValueError('No se importó ningún informe válido')
    summary = catalog_summary(database)
    html = output / 'benchmark.html'
    export_catalog_html(database, html)
    (output / 'catalog-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    evidence = {'schema_version': 1, 'database': str(database.resolve()), 'database_sha256': hashlib.sha256(database.read_bytes()).hexdigest(),
                'html_path': str(html.resolve()), 'imports': imports, 'failures': failures, 'totals': summary['totals'],
                'claims': summary['claims'], 'scope': 'Imported saved measurements; no engines or imported commands executed'}
    (output / 'integration.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'totals': summary['totals'], 'imports': len(imports), 'failures': failures, 'database': str(database), 'html': str(html)}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
