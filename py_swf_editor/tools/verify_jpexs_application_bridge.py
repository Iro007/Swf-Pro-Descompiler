"""Headless regression for application JPEXS method-source offsets.

Uses an owned edit of the SuiteEditable fixture and the actual suite call site.
No browser, player, network request or benchmark bridge is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from py_swf.universal import UniversalSuite


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def neighbor_cases(run: Path) -> list[dict]:
    source = '''package {
    public class SpanNeighbors {
        public static const NOTE:String = "línea 🚀";
        private var total:int = 0;
        public function SpanNeighbors() {
            super();
            this.total = 5;
        }
        public function answer():int {
            return 11;
        }
        public function add(n:int):int {
            this.total = this.total + n;
            return 37;
        }
    }
}
'''
    cases = []
    for newline, separator in [('LF', '\n'), ('CRLF', '\r\n')]:
        folder = run / newline
        folder.mkdir()
        entry = folder / 'SpanNeighbors.as'
        entry.write_bytes(source.replace('\n', separator).encode('utf-8'))
        suite = UniversalSuite(state_dir=folder / 'recovery')
        try:
            suite.compile_project({'entry': str(entry), 'profile': 'native-as3', 'output': str(folder / 'neighbors.swf')})
            units = suite.scripts({'offset': 0, 'limit': 100})['items']
            names = {'constructor': 'SpanNeighbors', 'answer': 'answer', 'add': 'add'}
            method_cases = []
            for label, name in names.items():
                inventory_name = 'constructor' if label == 'constructor' else name
                matching = [u for u in units if u['name'].split('.')[-1] == inventory_name]
                if len(matching) != 1:
                    raise AssertionError(f'{newline}: {label} inventory is {matching}')
                unit = matching[0]
                result = suite.decompile({'unit_id': unit['id'], 'engine': 'jpexs'})
                fragment = (result['source'] or '').strip()
                document = result.get('document_source') or ''
                own_signature = bool(re.match(r'public\s+function\s+' + re.escape(name) + r'\s*\(', fragment))
                no_neighbors = all(not re.search(r'function\s+' + re.escape(other) + r'\s*\(', fragment)
                                   for other in names.values() if other != name)
                sentinel = r'return\s+11\s*;' if label == 'answer' else r'return\s+37\s*;' if label == 'add' else r'=\s*5\s*;'
                complete = bool(re.search(sentinel, fragment)) and fragment.endswith('}')
                method_cases.append({'method': label, 'unit_id': unit['id'], 'source': fragment,
                                     'own_signature': own_signature, 'no_neighbor_code': no_neighbors,
                                     'complete': complete, 'passed': own_signature and no_neighbors and complete})
                if label == 'answer':
                    # The original document keeps CRLF. Its UTF-16 span view
                    # must also remain correct after a supplementary character.
                    doc_unicode = '🚀' in document
                    doc_crlf = '\r\n' in document
            cases.append({'input_newlines': newline, 'source_sha256': sha256(entry),
                          'document_unicode_surrogate_pair': doc_unicode, 'document_crlf_preserved': doc_crlf,
                          'methods': method_cases, 'passed': all(c['passed'] for c in method_cases) and doc_unicode and doc_crlf})
        finally:
            suite.close()
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=PROJECT / 'reports/suite-integration/suite-editable.swf')
    parser.add_argument('--output', type=Path, default=PROJECT / 'reports/jpexs-snippet-cli')
    parser.add_argument('--phase', choices=['baseline', 'fixed'], default='fixed')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=f'{args.phase}-', dir=args.output))
    report = {'scope': 'JPEXS application bridge, real CLI/suite, UTF-16 method spans',
              'gui_started': False, 'browser_started': False, 'flash_runtime_verified': False,
              'fixture': str(args.fixture.resolve()), 'original_sha256': sha256(args.fixture), 'checks': []}
    lock = json.loads((PROJECT.parent / 'toolpacks/lock.json').read_text(encoding='utf-8'))
    report['application_bridge'] = lock['tools']['jpexs-bridge']
    suite = None
    try:
        suite = UniversalSuite(state_dir=run / 'recovery')
        state = suite.open(str(args.fixture))
        units = suite.scripts({'query': 'answer', 'offset': 0, 'limit': 100})['items']
        if len(units) != 1:
            raise AssertionError(f'Expected one answer method, found {len(units)}')
        unit = units[0]['id']
        suite.edit({'unit_id': unit, 'mode': 'source', 'code': 'return 11;', 'base_revision': state['revision']})
        result = suite.dispatch('export_swf', {'path': str(run / 'edited.swf')})
        report['unit_id'] = unit
        report['edited_swf'] = result['path']
        report['edited_sha256'] = sha256(Path(result['path']))
        native = suite.decompile({'unit_id': unit, 'engine': 'python'})
        jpexs = suite.decompile({'unit_id': unit, 'engine': 'jpexs'})
        report['native_source'] = native['source']
        report['jpexs_source'] = jpexs['source']
        report['source_document'] = jpexs.get('source_document')
        report['document_source'] = jpexs.get('document_source')
        pattern = r'public\s+function\s+answer\s*\(\s*\)\s*:\s*int\s*\{\s*return\s+11\s*;\s*\}'
        checks = [
            ('native edited value', bool(re.search(r'return\s+(?:int\s*\(\s*)?11', native['source'] or ''))),
            ('complete document contains answer return 11', bool(re.search(pattern, jpexs.get('document_source') or ''))),
            ('method snippet has complete signature and return 11', bool(re.fullmatch(pattern, (jpexs['source'] or '').strip()))),
            ('original unchanged', sha256(args.fixture) == report['original_sha256']),
        ]
        baseline_path = args.output / 'baseline.json'
        if args.phase == 'fixed' and baseline_path.is_file():
            baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
            checks.append(('complete document unchanged from baseline',
                           jpexs.get('document_source') == baseline.get('document_source')))
        report['checks'] = [{'name': name, 'passed': passed} for name, passed in checks]
        report['passed'] = all(passed for _, passed in checks)
        if report['passed']:
            report['neighbor_cases'] = neighbor_cases(run)
            report['passed'] = all(case['passed'] for case in report['neighbor_cases'])
    except Exception as error:
        report['passed'] = False
        report['error'] = str(error)
    finally:
        if suite is not None:
            suite.close()
    path = args.output / f'{args.phase}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'report': str(path), 'checks': report['checks'],
                      'error': report.get('error')}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
