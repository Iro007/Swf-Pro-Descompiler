"""Collect delivery evidence without launching the application or runtimes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
RUST = WORKSPACE / 'rust_swf'


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    tests = ElementTree.parse(PROJECT/'reports/headless-pytest.xml').getroot().findall('testsuite')
    totals = {name:sum(int(suite.attrib.get(name,0)) for suite in tests) for name in ('tests','failures','errors','skipped')}
    totals['passed'] = totals['tests'] - totals['failures'] - totals['errors'] - totals['skipped']
    totals['skip_reasons'] = sorted({node.attrib.get('message','') for suite in tests for node in suite.iter('skipped')})
    compiler = load(PROJECT/'reports/native-compiler-cli/verification.json')
    sdks = load(PROJECT/'reports/suite-integration/verification.json')
    modules = load(PROJECT/'reports/suite-modules/verification.json')
    frozen = load(PROJECT/'reports/suite-integration/frozen-worker.json')
    package = load(RUST/'reports/release-headless-verification.json')
    artifacts = load(RUST/'reports/release-artifact-hashes.json')
    native = load(RUST/'reports/rustswf-source-only-summary.json')
    supervisor = load(RUST/'reports/rustswf-desktop-source-only-summary.json')
    real_methods = load(PROJECT/'reports/native-functions-cli-review/verification.json')
    tool_lock = WORKSPACE/'toolpacks/lock.json'
    snippet = load(PROJECT/'reports/jpexs-snippet-cli/fixed.json')
    benchmark_path = WORKSPACE/'reports/benchmark-headless-0.3.0-final/benchmark.json'
    benchmark = load(benchmark_path)
    report = {'schema_version':1,'version':'0.3.0','created_at':datetime.now(timezone.utc).isoformat(),
              'gui_started':False,'browser_started':False,'flash_runtime_verified':False,
              'python_tests':totals,
              'compiler_vm':{'passed':compiler['passed'],'cases':len(compiler['cases']),'scope':compiler['verification']},
              'sdk_builds':{'passed':sdks['passed'],'checks':[{key:row[key] for key in ('profile','passed','sha256') if key in row} for row in sdks['checks']]},
              'optional_modules':{'passed':modules['passed'],'checks':[{key:row[key] for key in ('operation','passed','animate_opened') if key in row} for row in modules['checks']]},
              'frozen_worker':{'passed':frozen['passed'],'checks':frozen['checks'],'sha256':frozen['worker_sha256']},
              'application_bridge':{'lock_sha256':hashlib.sha256(tool_lock.read_bytes()).hexdigest(),
                                    'provenance':load(tool_lock)['tools']['jpexs-bridge'],
                                    'snippet_verification':snippet},
              'rust_source_checks':{'tests':native['native_directed_tests'],'clippy':native['clippy'],'flex':native['optional_flex_cli_namespace_proof'],
                                    'report_version':native['version'],'scope':'Core source verified before packaging version increment'},
              'supervisor':supervisor,
              'real_method_edits':{'semantic_cases':len(real_methods['semantic_cases']),
                                  'edits':[{key:row[key] for key in ('swf','saved_reopened','other_tags_preserved','other_tags_scope','source_snapshot_isolated',
                                           'original_preserved','baseline_status','full_source_export_rejected','baseline_errors') if key in row} for row in real_methods['real_pcode_edits']]},
              'package':package,'artifact_hashes':artifacts,
              'packaged_benchmark':{'report':str(benchmark_path),'completed_files':benchmark['completed_files'],
                                    'scheduled_files':benchmark['scheduled_files'],'html_generated':benchmark_path.with_suffix('.html').is_file(),
                                    'worker_sha256':benchmark['provenance']['implementation_sha256_before'],
                                    'cases':[{'file':case['relative'],'engines':[{'engine':item['engine'],'status':item['status'],
                                                'source_available':item['quality']['source_available'],
                                                'expected_units':item['quality']['expected_units_reported'],'operation_failures':len(item['failures'])}
                                                for item in case['engines']]} for case in benchmark['cases']],
                                    'claims':benchmark['claims']},
              'limitations':['One pinned public corpus fixture unavailable; local SWF corpus tests ran',
                             'VM checks cover authored bounded scenarios, not complete Flash runtime equivalence',
                             'FLA/XFL archives exported and inspected; Adobe Animate was not opened',
                             'Commercial providers require a user installation/license and have no new comparative campaign',
                             'Rust wrapper roundtrip retains raw tags/bytecode; it is not an ActionScript compiler',
                             'Current strict validation rejects two other methods in a real AS3 corpus SWF; that whole-file export is blocked, while its complete selected ABC is edited in an isolated library fixture',
                             'No new visual/GUI verification; TypeScript and application builds verified separately']}
    report['passed'] = not (totals['failures'] or totals['errors'] or supervisor['failed']) and all(result['passed'] for result in (compiler,sdks,modules,frozen)) and package['all_hashes_verified']
    assert frozen['worker_sha256']==artifacts['worker_sha256'], 'Frozen worker differs from the delivered artifact'
    assert benchmark['provenance']['implementation_sha256_before']==artifacts['worker_sha256'], 'Benchmark worker differs from the delivered artifact'
    assert benchmark['completed_files']==benchmark['scheduled_files']==3 and benchmark_path.with_suffix('.html').is_file()
    assert package['version']==report['version'], 'Release version mismatch'
    output = WORKSPACE/'reports/headless-verification-0.3.0.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'report':str(output),'python_tests':totals,
                      'frozen_checks':len(frozen['checks']),'sdk_checks':len(sdks['checks']),
                      'module_checks':len(modules['checks'])},ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
