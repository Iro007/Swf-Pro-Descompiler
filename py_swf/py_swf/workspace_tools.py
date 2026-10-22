"""Read-only discovery of local Flash tools; candidates are never executed.

Presence, file identity, benchmark configuration and measured results are distinct.
No package is installed, downloaded, licensed or auto-configured by this module.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import stat

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
_SKIP = {'.git', 'node_modules', 'target', '__pycache__', '.venv', 'web', 'ui', 'reports',
         'testdata', 'benchmark_inputs', 'third_party', 'src', 'crates', 'libsrc',
         'docs', 'tests', 'build', '.cargo', 'jdk', 'downloads', 'remote-results',
         'haxelib', 'frameworks', 'licenseparts', 'licenses', 'std', 'legal',
         'locale', 'templates', 'benchmark-evidence', '_internal'}
_CLI_NAMES = {
    'rustswf.exe': 'rust', 'rustswf': 'rust', 'ffdec.jar': 'jpexs',
    'ffdec.bat': 'jpexs', 'ffdec.sh': 'jpexs', 'ffdec-cli.exe': 'jpexs',
    'flare.exe': 'flare', 'flare': 'flare', 'flasm.exe': 'flasm', 'flasm': 'flasm',
    'rabcdasm.exe': 'rabcdasm', 'rabcdasm': 'rabcdasm',
    'swfdump.jar': 'flex_swfdump',
    'swfmill.exe': 'swfmill', 'swfmill': 'swfmill',
    'swfextract.exe': 'swfextract', 'swfextract': 'swfextract',
    'ruffle.exe': 'ruffle', 'ruffle': 'ruffle',
}
_GUI_NAMES = {'pyswfstudio.exe': 'native', 'ffdec.exe': 'jpexs',
              'as3sorcerer.exe': 'as3_sorcerer', 'asv.exe': 'asv',
              'swfdecompiler.exe': 'sothink', 'flashdecompiler.exe': 'trillix'}


def _safe(path: Path, root: Path) -> bool:
    """Reject links, junctions and resolved paths outside the chosen workspace."""
    try:
        if not path.resolve().is_relative_to(root):
            return False
        current = path
        while current != root:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return False
            current = current.parent
        return True
    except (OSError, ValueError):
        return False


def _identity(path: Path) -> dict:
    record = {}
    try:
        size = path.stat().st_size
        record['size_bytes'] = size
        if size <= 256 * 1024 * 1024:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            record['sha256'] = digest.hexdigest()
        else:
            record['identity_diagnostic'] = 'Hash omitted: file exceeds 256 MiB discovery limit'
    except OSError as error:
        record['identity_diagnostic'] = str(error)
    return record


def discover_workspace_tools(root=None) -> dict:
    """Return bounded local candidates without running or configuring any tool.

    A portable_cli status describes a recognized command artifact, not successful
    execution. configured only identifies our known Python/Rust command paths;
    external benchmark configuration is checked separately by the registry.
    """
    from .benchmark_engines import ENGINES

    workspace = Path(root).expanduser().resolve() if root is not None else DEFAULT_ROOT.resolve()
    tools = {key: {'id': key, 'name': engine.name, 'status': 'missing',
                   'configured': False, 'candidates': []}
             for key, engine in ENGINES.items()}
    tools.setdefault('swfdec', {'id': 'swfdec', 'name': 'Local swfdec package',
                               'status': 'missing', 'configured': False, 'candidates': []})
    roots = [workspace, workspace / 'Competing_tools',
             workspace / 'py_swf' / 'Competing_tools',
             workspace / 'py_swf' / '.dev-references']
    report = {'workspace_root': str(workspace),
              'external_tools_search_roots': [str(path) for path in roots],
              'tools': list(tools.values()), 'scan_limit': 4000, 'scan_truncated': False,
              'identity_note': 'SHA-256 identifies bytes observed locally; it does not verify provenance, safety, version or license.',
              'measurement_note': 'Discovery does not execute tools or establish decompilation quality, CLI compatibility, installation or measured results.'}
    seen = set()

    def add(engine, path, kind, configured=False, diagnostic=None):
        if not _safe(path, workspace) or not path.exists():
            return
        key = (engine, str(path))
        if key in seen:
            return
        seen.add(key)
        candidate = {'path': str(path), 'kind': kind, 'configured': configured,
                     'execution_verified': False}
        if path.is_file():
            candidate.update(_identity(path))
        if diagnostic:
            candidate['diagnostic'] = diagnostic
        tools[engine]['candidates'].append(candidate)

    # Exact project markers and runtime paths remain useful without walking source trees.
    pyroot = workspace / 'py_swf'
    if _safe(pyroot / 'py_swf' / 'cli.py', workspace) and (pyroot / 'py_swf' / 'cli.py').is_file():
        add('native', pyroot, 'source_checkout')
        runtime = pyroot / '.venv' / 'Scripts' / 'python.exe'
        if runtime.is_file():
            add('native', runtime, 'portable_cli', True,
                'Known project Python interpreter; command: python -m py_swf.cli. Runtime not launched by discovery.')
    rustroot = workspace
    if _safe(rustroot / 'Cargo.toml', workspace) and (rustroot / 'Cargo.toml').is_file():
        add('rust', rustroot, 'source_checkout')
    # Known isolated development runtimes live under intentionally skipped
    # package/build trees. Detect exact entry points without traversing them.
    extras = pyroot / '.dev-references' / 'extra-tools'
    if _safe(extras, workspace) and extras.is_dir():
        for package in sorted(extras.glob('pyflashkit-*'))[:16]:
            runtime = package / '.venv/Scripts/python.exe'
            module = package / '.venv/Lib/site-packages/flashkit/workspace/__init__.py'
            if _safe(module, workspace) and module.is_file() and _safe(runtime, workspace) and runtime.is_file():
                add('pyflashkit', runtime, 'portable_cli', diagnostic='Isolated Python runtime and flashkit workspace module detected; artifact hashes and operation compatibility are checked separately.')
        for source in sorted(extras.glob('ruffle-source-*'))[:16]:
            if _safe(source / 'Cargo.toml', workspace) and (source / 'Cargo.toml').is_file():
                add('ruffle', source, 'source_checkout', diagnostic='Ruffle source checkout; an independent built executable is required for execution.')
    web_runtime = workspace / 'ui/public/ruffle/ruffle.js'
    if _safe(web_runtime, workspace) and web_runtime.is_file():
        add('ruffle', web_runtime, 'support_files', diagnostic='Bundled web runtime assets; browser execution is a separate operation, not source recovery.')
    # Source checkout names identify candidates; no compiled FFDec CLI is implied.
    try:
        top = sorted(workspace.iterdir(), key=lambda p: p.name.casefold())
    except OSError as error:
        report['scan_diagnostic'] = str(error)
        return report
    for path in top:
        if 'jpexs' in path.name.casefold() and path.is_dir():
            markers = (path / 'build.xml', path / 'libsrc')
            if any(_safe(marker, workspace) and marker.exists() for marker in markers):
                add('jpexs', path, 'source_checkout', diagnostic='Source markers detected; compiled JAR/CLI must be discovered separately.')
        if path.name.casefold().startswith('asv') and path.is_dir():
            add('asv', path, 'support_files', diagnostic='Package folder; companion tools alone do not establish the ASV decompiler is installed.')
    queue = [(workspace, 0)]
    visited = 0
    while queue:
        folder, depth = queue.pop(0)
        if not _safe(folder, workspace):
            continue
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for path in entries:
            visited += 1
            if visited > report['scan_limit']:
                report['scan_truncated'] = True
                queue.clear()
                break
            if not _safe(path, workspace):
                continue
            low = path.name.casefold()
            if path.is_dir():
                if depth < 5 and low not in _SKIP:
                    queue.append((path, depth + 1))
                continue
            if not path.is_file():
                continue
            if low in _CLI_NAMES:
                engine = _CLI_NAMES[low]
                rust_cli = workspace / 'dist' / 'FlashUniversalStudio' / 'rustswf.exe'
                configured = engine == 'rust' and path == rust_cli
                jar = path.parent / 'ffdec.jar'
                if engine == 'jpexs' and low != 'ffdec.jar' and not (_safe(jar, workspace) and jar.is_file()):
                    add(engine, path, 'support_files', diagnostic='JPEXS launcher without adjacent compiled ffdec.jar; not a usable distribution.')
                else:
                    add(engine, path, 'portable_cli', configured)
            elif low in _GUI_NAMES:
                engine = _GUI_NAMES[low]
                jar = path.parent / 'ffdec.jar'
                if engine == 'jpexs' and not (_safe(jar, workspace) and jar.is_file()):
                    add(engine, path, 'support_files', diagnostic='JPEXS launcher resource without adjacent compiled ffdec.jar.')
                else:
                    add(engine, path, 'executable', diagnostic='Executable candidate; automation and installation not verified.')
            elif low == 'flash_decompiler.exe':
                add('trillix', path, 'installer', diagnostic='Downloaded installer candidate; not an installed or measured CLI.')
            elif 'setup' in low and path.suffix.casefold() in {'.exe', '.msi'}:
                context = str(path.relative_to(workspace)).casefold()
                engine = ('as3_sorcerer' if 'sorcerer' in context else
                          'asv' if 'asv' in context else
                          'swfdec' if 'swfdec' in context else
                          'sothink' if 'sothink' in context else
                          'trillix' if 'trillix' in context else
                          'jpexs' if 'jpexs' in context or 'ffdec' in context else None)
                if engine:
                    add(engine, path, 'installer', diagnostic='Installer candidate; installation and license state not observed.')
    order = {'portable_cli': 0, 'executable': 1, 'installer': 2,
             'source_checkout': 3, 'support_files': 4}
    for tool in tools.values():
        tool['candidates'].sort(key=lambda c: (order[c['kind']], c['path'].casefold()))
        if tool['candidates']:
            tool['status'] = tool['candidates'][0]['kind']
            tool['configured'] = any(c['configured'] for c in tool['candidates'])
        tool['measured'] = False
    report['scanned_entries'] = visited
    return report
