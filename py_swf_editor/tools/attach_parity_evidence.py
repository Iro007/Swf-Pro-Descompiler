"""Add reviewable frozen evidence and reproducibility tools to a verified release."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    root=Path(__file__).resolve().parents[2]
    evidence=root/'reports/parity-implementation-20261014'
    target=root/'RUST SWF/dist/FlashUniversalStudio-Parity-20261014'
    manifest=json.loads((target/'release-manifest.json').read_text())
    for item in manifest['artifacts']:
        if sha(target/item['path'])!=item['sha256']:raise ValueError('Existing release artifact changed')
    shutil.copy2(target/'release-manifest.json',evidence/'cli-smoke-package-manifest.json')
    review=target/'benchmark-evidence';review.mkdir(exist_ok=False)
    folders=('correctness-frozen','correctness-derived','source-semantics-frozen',
             'source-semantics-version2-dev-namespaces-final','version2-regressions',
             'performance-frozen','runtime-coerce-final','runtime-catch-final',
             'runtime-native-atomic-original','runtime-native-atomic-reconstructed',
             'rea-projection','frozen-source','rabcdasm-roundtrip')
    for name in folders:
        shutil.copytree(evidence/name,review/name)
    for path in evidence.glob('*.log'):
        shutil.copy2(path,review/path.name)
    for name in ('baseline-manifest.json','rea-final-rust.json','rea-final-catalog.json',
                 'catalog-imports.json','disassembly-references.json','portable-document-probe.json',
                 'portable-rust-document-probe.json','portable-shared-compile-probe.json'):
        shutil.copy2(evidence/name,review/name)
    shutil.copytree(root/'benchmark_inputs/parity-20261014/optimized',review/'corpus')
    scripts=review/'tools';scripts.mkdir()
    for name in ('build_parity_corpus.py','collect_parity_correctness.py','derive_parity_quality.py',
                 'verify_parity_source_semantics.py','run_bounded_parity_semantics.py',
                 'run_parity_campaign.py','run_ruffle_trace_probe.py','finalize_parity_delivery.py'):
        path=root/'py_swf_editor/tools'/name
        if path.is_file():shutil.copy2(path,scripts/name)
    notices=target/'licenses/benchmark-dependencies';notices.mkdir()
    for directory in (root/'toolpacks/haxe',root/'toolpacks/haxelib'):
        for path in directory.rglob('*'):
            relative=path.relative_to(directory)
            if len(relative.parts)<=4 and path.is_file() and path.name.lower().startswith(('license','copying')):
                destination=notices/directory.name/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
    (review/'README.txt').write_text(
        'Evidence belongs to the exact frozen revision identified by freeze.json.\n'
        'Version2 only adds development-derived body imports and corrects packaged SDK lookup.\n'
        'The original 12-family campaign is preserved; version2 reruns the nine historical SWFs and a development namespace fixture.\n'
        'Completed semantic jobs are executions of the evaluator, not claims that every project compiled.\n'
        'Full performance sessions were blocked by the CPU/RAM gate; all 864 scheduled entries remain not_run.\n'
        'REA refused the complete 366 MiB catalog under its 256 MiB snapshot guard. Its error and an exact small family projection remain included.\n'
        'Report paths retain original workspace provenance; matching subtrees are included here.\n'
        'Haxe/Flex/Flixel/Ruffle and other development tool installations are configured separately.\n',encoding='utf-8')
    manifest['artifacts']=[{'path':p.relative_to(target).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
                           for p in sorted(target.rglob('*')) if p.is_file() and p.name!='release-manifest.json']
    manifest['evidence_scope']='Frozen raw observations, exact unit comparisons, full generated-source/compile/runtime reports, explicit failures, corpus and source'
    (target/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=target.with_name(target.name+'-portable.zip')
    previous=archive.with_name(target.name+'-cli-smoke-portable.zip')
    if previous.exists():raise ValueError('Prior smoke archive already exists')
    archive.rename(previous)
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,strict_timestamps=False) as packed:
        for path in sorted(target.rglob('*')):
            if path.is_file():packed.write(path,path.relative_to(target).as_posix())
    with zipfile.ZipFile(archive) as packed:
        if packed.testzip() is not None:raise ValueError('Archive CRC mismatch')
    result={'path':str(target),'archive':str(archive),'sha256':sha(archive),'bytes':archive.stat().st_size,'files':len(manifest['artifacts'])}
    (evidence/'final-portable-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))


if __name__=='__main__':main()
