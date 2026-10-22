"""Verify an existing dated release and create its portable archive."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    args = parser.parse_args()
    if len(args.round) != 8 or not args.round.isdigit():
        parser.error('round must be YYYYMMDD')
    target = root / ('rust_swf/dist/FlashUniversalStudio-Engines-' + args.round)
    manifest = json.loads((target / 'release-manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['artifacts']:
        file = (target / entry['path']).resolve()
        if not file.is_relative_to(target.resolve()) or file.stat().st_size != entry['bytes'] or sha(file) != entry['sha256']:
            raise ValueError('Release artifact differs: ' + entry['path'])
    archive = target.parent / (target.name + '-portable.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as packed:
        for file in sorted(target.rglob('*')):
            if file.is_file():
                packed.write(file, file.relative_to(target).as_posix())
    config_path = root / 'py_swf_editor/.dev-references/extra-tools.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    rust = str((target / 'rustswf.exe').resolve())
    config['tools']['rust'] = {'version': '0.3.0', 'executable': rust, 'artifact_sha256': {rust: sha(rust)},
                              'scope': 'Measured engine-improvements-' + args.round}
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    result = {'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': sha(archive),
              'files': len(manifest['artifacts']), 'catalog': manifest['catalog']}
    (root / ('reports/decompiler-improvements-' + args.round) / 'package-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
