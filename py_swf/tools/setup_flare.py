"""Register a locally downloaded Flare distribution, preserving every ZIP file.

The caller supplies the expected local executable digest. This is an opt-in
development tool; no installer is run and no application release is modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from py_swf.benchmark_engines import checked_tool


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def register(distribution, archive, expected_sha256, evidence):
    distribution, archive, evidence = map(lambda p:Path(p).resolve(), (distribution,archive,evidence))
    if fingerprint((distribution/'flare.exe').read_bytes()) != expected_sha256:
        raise ValueError('Local Flare executable does not match the caller supplied digest')
    with zipfile.ZipFile(archive) as bundle:
        files = {}
        for item in bundle.infolist():
            name = PurePosixPath(item.filename)
            if item.is_dir() or name.is_absolute() or '..' in name.parts or '\\' in item.filename:
                raise ValueError('Flare ZIP must contain only safe relative distribution files')
            payload = bundle.read(item)
            if (distribution/str(name)).read_bytes() != payload:
                raise ValueError('Local distribution differs from the supplied archive: '+str(name))
            files[str(name)] = payload
    if not {'flare.exe','LICENSE.TXT','flare.html'}.issubset(files):
        raise ValueError('Flare distribution is incomplete')
    target = PROJECT/'.dev-references/extra-tools/flare-0.6'
    target.mkdir(parents=True,exist_ok=True)
    for relative,payload in files.items():
        path=target/relative
        if path.exists() and path.read_bytes()!=payload:
            raise ValueError('Refuse to overwrite a different existing development artifact: '+str(path))
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(payload)
    banner = subprocess.run([str(target/'flare.exe')],cwd=target,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                            timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    version_text = (banner.stdout+banner.stderr).decode('latin-1')
    if 'Flare 0.6 build Feb 10 2006' not in version_text:
        raise ValueError('Flare CLI does not identify the expected version/build')
    prefix='.dev-references/extra-tools/flare-0.6/'
    config={'version':'0.6','build':'Feb 10 2006','adapter_mode':'consolidated_legacy_as_source',
        'executable':prefix+'flare.exe','development_only':True,
        'license':'Copyrighted freeware; non-commercial redistribution only; unmodified complete distribution with LICENSE.TXT',
        'artifact_sha256':{prefix+name:fingerprint(payload) for name,payload in files.items()},
        'archive':{'url':'http://www.nowrap.de/download/flare06doswin.zip','sha256':fingerprint(archive.read_bytes())},
        'publisher_page':'http://www.nowrap.de/flare.html',
        'hash_authority':'Locally measured official upstream HTTP archive on 2026-10-14; no publisher digest; transport not authenticated',
        'capabilities':{'source':['as2']},'artifact_unit_scope':'file','unit_mapping':'not_established',
        'source_validation':'Exact single root DoAction frame-1 wrapper extraction supported; compilation and behavior evaluated separately'}
    manifest=PROJECT/'.dev-references/extra-tools.json'
    record=json.loads(manifest.read_text(encoding='utf-8'))
    if record.get('development_only') is not True:
        raise ValueError('External tools manifest must remain development-only')
    record.setdefault('tools',{})['flare']=config
    manifest.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    checked=checked_tool('flare',manifest)
    evidence.mkdir(parents=True,exist_ok=True)
    provenance={'recorded_at':datetime.now(timezone.utc).isoformat(),'configuration':config,
        'local_distribution':str(distribution),'archive_path':str(archive),'archive_matches_local_distribution':True,
        'complete_distribution_files':list(files),'version_probe':{'returncode':banner.returncode,'output':version_text},
        'registered_executable':checked['executable'],'registered_artifacts_verified':True,
        'transport_limit':'Official HTTP download used; HTTPS certificate expired; no TLS verification bypass used',
        'jina_reader_limit':'HTTP 401 AuthenticationRequiredError; anonymous network reputation block',
        'redistributed_with_application':False}
    (evidence/'flare-provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return provenance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distribution',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args()
    result=register(args.distribution,args.archive,args.expected_sha256,args.evidence)
    print(json.dumps({'registered_executable':result['registered_executable'],
        'registered_artifacts_verified':result['registered_artifacts_verified'],'evidence':str(args.evidence)},indent=2))


if __name__=='__main__':main()
