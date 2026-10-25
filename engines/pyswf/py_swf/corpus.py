"""One discoverable, configurable source of SWF corpus paths."""
import os
import hashlib
import json
from pathlib import Path


def read_immutable_manifest(path, *, verify_sources=True):
    """Check the sealed source-family corpus without replacing anything."""
    root = Path(path).resolve()
    source = root / 'manifest.json'
    data = source.read_bytes()
    manifest = json.loads(data)
    if manifest.get('immutable') is not True:
        raise ValueError('Corpus manifest is not immutable')
    expected = (root / 'manifest.sha256').read_text(encoding='utf-8').strip()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Sealed corpus manifest changed')
    family_splits = {}
    seen_paths=set()
    for case in manifest.get('cases', []):
        relative = Path(case['relative'])
        if case['relative'] in seen_paths:raise ValueError('Duplicate corpus case path: '+case['relative'])
        seen_paths.add(case['relative'])
        file = (root / relative).resolve()
        if relative.is_absolute() or not file.is_relative_to(root):
            raise ValueError('Corpus file escapes declared root')
        if hashlib.sha256(file.read_bytes()).hexdigest() != case['sha256']:
            raise ValueError('Immutable corpus file changed: ' + case['relative'])
        family = case['family_id']
        if family in family_splits and family_splits[family] != case['split']:
            raise ValueError('Family leaks across corpus splits: ' + family)
        family_splits[family] = case['split']
    if verify_sources:
        for family in manifest.get('families', []):
            for source in family.get('source_files', []):
                file = (root / source['relative']).resolve()
                if not file.is_relative_to(root) or hashlib.sha256(file.read_bytes()).hexdigest() != source['sha256']:
                    raise ValueError('Immutable corpus source changed: ' + source['relative'])
    return manifest


def read_damaged_manifest(path):
    """Check the damaged-file corpus (corpus/damaged, written by tools/make_damaged_corpus.py): its manifest against the
    seal beside it, every damaged file against its sha256 and every source against the sealed parity corpus."""
    root = Path(path).resolve()
    data = (root / 'manifest.json').read_bytes()
    manifest = json.loads(data)
    if hashlib.sha256(data).hexdigest() != (root / 'manifest.sha256').read_text(encoding='utf-8').strip():
        raise ValueError('Damaged corpus manifest changed')
    sources = root.parent
    seen = set()
    for case in manifest.get('cases', []):
        if case['id'] in seen:
            raise ValueError('Duplicate damaged case: ' + case['id'])
        seen.add(case['id'])
        file = (root / case['relative']).resolve()
        if not file.is_relative_to(root) or hashlib.sha256(file.read_bytes()).hexdigest() != case['sha256']:
            raise ValueError('Damaged corpus file changed: ' + case['relative'])
        source = (sources / case['source']).resolve()
        if not source.is_relative_to(sources) or hashlib.sha256(source.read_bytes()).hexdigest() != case['source_sha256']:
            raise ValueError('Damaged corpus source changed: ' + case['source'])
    return manifest


def classify_application_units(inventory, application_classes, *, language=None):
    """Ownership-based app/dependency labels; never infer from output length."""
    names = set(application_classes)
    by_id = {u['unit_id']:u for u in inventory['units']}
    classes={(tuple(u['tag_path']),u.get('class_index')):u for u in inventory['units'] if u['scope']=='class'}
    blocks={tuple(b['tag_path']):b for b in inventory.get('abc_blocks',[])}
    def combine(labels):
        known=set(labels)-{'unknown',None}
        return 'mixed' if len(known)>1 else next(iter(known)) if known else 'unknown'
    for unit in inventory['units']:
        if unit['language'] == 'as2':
            unit['code_origin'] = 'application'
        elif unit['scope'] == 'class':
            unit['code_origin'] = 'application' if unit.get('name') in names else 'dependency' if names else 'unknown'
        elif unit['scope'] == 'script':
            indices=blocks.get(tuple(unit['tag_path']),{}).get('script_class_indices',{}).get(str(unit['script_index']),[])
            owned=[classes[(tuple(unit['tag_path']),index)] for index in indices if (tuple(unit['tag_path']),index) in classes]
            unit['code_origin']=combine(c.get('code_origin') for c in owned)
        elif unit['scope'] == 'method':
            owners = [by_id[k] for k in unit.get('context', {}).get('owner_unit_ids', []) if k in by_id]
            unit['code_origin'] = combine(owner.get('code_origin') for owner in owners)
        else:
            unit['code_origin'] = 'unknown'
    return inventory


def corpus_root(vendor=None):
    """JPEXS test corpus: PY_SWF_CORPUS_ROOT, else the JPEXS sources kept under vendor/."""
    from . import paths
    vendor = Path(vendor or paths.VENDOR).resolve()
    configured = os.environ.get("PY_SWF_CORPUS_ROOT")
    if configured:
        base = Path(configured).expanduser().resolve()
        choices = (base, base / "libsrc/ffdec_lib/testdata")
        for candidate in choices:
            if (candidate / "as2").is_dir() and (candidate / "as3").is_dir():
                return candidate
        raise FileNotFoundError("PY_SWF_CORPUS_ROOT does not contain the SWF test corpus: " + str(base))
    repositories = (vendor / "third_party/references/jpexs-26.3.0", vendor / "third_party/jpexs-decompiler",
                    vendor / "jpexs-source", vendor / "jpexs-decompiler")
    for repository in repositories:
        candidate = repository / "libsrc/ffdec_lib/testdata"
        if candidate.is_dir():
            return candidate
    # Callers can explicitly skip optional fixtures; a declared path never falls back.
    return repositories[0] / "libsrc/ffdec_lib/testdata"
