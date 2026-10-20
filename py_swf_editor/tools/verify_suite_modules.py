"""Exercise real optional modules and validate their actual output files."""
from pathlib import Path
import json
import sys
import tempfile
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from py_swf.universal import UniversalSuite


def main():
    output = PROJECT / 'reports/suite-modules'
    output.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'gui_started': False, 'browser_started': False, 'runtime_equivalence': False, 'parity': False}
    with tempfile.TemporaryDirectory(prefix='suite-modules-') as temporary:
        folder = Path(temporary)
        suite = UniversalSuite(folder / 'state', folder / 'tools.json')
        entry = folder / 'Frame.as'
        entry.write_text('var answer = 7; trace(answer);')
        for profile in ('native-as2', 'as2-jpexs'):
            try:
                result = suite.compile_project({'profile':profile,'entry':str(entry),'output':str(folder / (profile + '.swf'))})
                report['checks'].append({'operation':profile,'passed':result['validation']['valid'],'sha256':result['revision']})
            except Exception as error:
                report['checks'].append({'operation':profile,'passed':False,'error':str(error)})
        suite.open(PROJECT / 'reports/suite-integration/as3-flex-fixture.swf')
        unit = suite.units()[-1]
        try:
            result = suite.tool_operation({'id':'rabcdasm','operation':'disassemble','unit_id':unit.document_id,'path':str(folder)})
            main = next(Path(result['path']).rglob('*.main.asasm'))
            old = suite.state()['revision']
            result = suite.tool_operation({'id':'rabcdasm','operation':'assemble','unit_id':unit.document_id,'entry':str(main),'base_revision':old})
            report['checks'].append({'operation':'RABCDAsm roundtrip','passed':result['validation']['valid'],'same_swf':result['revision']==old})
        except Exception as error:
            report['checks'].append({'operation':'RABCDAsm roundtrip','passed':False,'error':str(error)})
        for format_ in ('resource-bundle','scripts','xml','fla','xfl'):
            suffix = {'resource-bundle':'.zip','xml':'.xml','fla':'.fla'}.get(format_,'')
            target = folder / ('output-' + format_ + suffix)
            try:
                result = suite.export({'format':format_,'path':str(target)})
                path = Path(result['path'])
                passed = path.is_file() and path.stat().st_size > 0 or path.is_dir() and any(path.rglob('*'))
                entries = None
                if format_ in ('resource-bundle','fla') and zipfile.is_zipfile(path):
                    with zipfile.ZipFile(path) as archive:
                        entries = archive.namelist()
                        passed = passed and archive.testzip() is None
                if format_ == 'xml':
                    from xml.etree import ElementTree
                    ElementTree.parse(path)
                report['checks'].append({'operation':'export-' + format_,'passed':bool(passed),'entries':entries,'engine':result['engine'],'animate_opened':False})
            except Exception as error:
                report['checks'].append({'operation':'export-' + format_,'passed':False,'error':str(error)})
        suite.close()
    report['passed'] = all(row['passed'] for row in report['checks'])
    (output / 'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    for row in report['checks']:
        print(json.dumps(row),flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
