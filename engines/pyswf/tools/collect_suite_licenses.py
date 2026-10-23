"""Copy notices for the frozen worker's import roots, not the whole venv."""
import ast
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from py_swf import paths
WORKSPACE = paths.WORKSPACE


def main():
    toc = ast.literal_eval((PROJECT / 'build/suite_worker/PYZ-00.toc').read_text(encoding='utf-8'))
    roots = {row[0].split('.')[0] for row in toc[-1] if isinstance(row,tuple) and row and isinstance(row[0],str)}
    mapping = metadata.packages_distributions()
    distributions = sorted({name for root in roots for name in mapping.get(root,[])})
    destination = WORKSPACE / 'dist/licenses/flash-suite-python/dependencies'
    destination.mkdir(parents=True,exist_ok=True)
    rows = []
    for name in distributions:
        dist = metadata.distribution(name)
        folder = destination / name.replace('/','_')
        copied = []
        for file in dist.files or []:
            if file.name.lower().startswith(('license','copying','notice')):
                source = Path(dist.locate_file(file)).resolve()
                if source.is_file():
                    folder.mkdir(exist_ok=True)
                    target = folder / file.name
                    shutil.copyfile(source,target)
                    copied.append(str(target.relative_to(destination)))
        rows.append({'name':name,'version':dist.version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License'),'notices':copied})
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license,destination/'CPython-LICENSE.txt')
    shutil.copyfile(PROJECT/'LICENSE',destination/'FlashUniversalStudio-GPL.txt')
    inventory = {'scope':'Installed distributions matching roots in final frozen PYZ; standard library covered by CPython notice','python':sys.version,'packages':rows,'module_roots':sorted(roots)}
    (destination/'inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
    print(json.dumps({'packages':len(rows),'output':str(destination)}))


if __name__ == '__main__':
    main()
