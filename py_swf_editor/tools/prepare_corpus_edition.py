"""Reuse verified AS3 builds in a fresh immutable corpus edition.

The original sealed corpus is retained. AS2 outputs are deliberately omitted
so an independent optimizing compiler wrapper must actually build them.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from py_swf.corpus import read_immutable_manifest


def prepare(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if source==destination or source.is_relative_to(destination):
        raise ValueError('Corpus editions must have different roots')
    if destination.exists():raise ValueError('Edition destination already exists; refuse replacement')
    manifest=read_immutable_manifest(source);destination.mkdir(parents=True,exist_ok=False)
    def copy(relative):
        original=(source/relative).resolve();target=(destination/relative).resolve()
        if not original.is_relative_to(source) or not target.is_relative_to(destination):raise ValueError('Edition path escapes root')
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
    for family in manifest['families']:
        for file in family['source_files']:copy(file['relative'])
        license='projects/'+family['family_id']+'/LICENSE.txt'
        if (source/license).is_file():copy(license)
        if family['compiler']!='rascal':
            for profile in ('debug','release'):
                for filename in ('compiler.swf','compiler-provenance.json'):
                    copy('projects/'+family['family_id']+'/builds/'+profile+'/'+filename)
    for case in manifest['cases']:
        if case['compiler']!='rascal':copy(case['relative'])
    for retired in manifest.get('retired_families',[]):
        for field in ('preserved_sources','preserved_variants'):
            folder=source/retired[field]
            if folder.is_dir():
                for file in folder.rglob('*'):
                    if file.is_file():copy(file.relative_to(source))
    state={'dependencies':manifest['dependencies'],'origin_manifest_sha256':(source/'manifest.sha256').read_text().strip(),
           'origin_edition':str(source),'family_policy':'same independent family split; AS2 variants require new real compiler execution',
           'families':[],'cases':[],'sealed':False}
    (destination/'build-state.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
    return state


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(prepare(args.source,args.output),indent=2))


if __name__=='__main__':main()
