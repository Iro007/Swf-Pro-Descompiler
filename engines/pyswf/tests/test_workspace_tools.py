from pathlib import Path
import hashlib

import pytest

from py_swf.workspace_tools import discover_workspace_tools


def _write(root, relative, data=b'tool'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _tools(report):
    return {tool['id']: tool for tool in report['tools']}


def test_project_source_and_command_candidates_are_distinct(tmp_path):
    _write(tmp_path, 'engines/pyswf/py_swf/cli.py', b'# fixture')
    _write(tmp_path, 'engines/pyswf/.venv/Scripts/python.exe')
    _write(tmp_path, 'Cargo.toml')
    rust = _write(tmp_path, 'dist/FlashUniversalStudio/rustswf.exe', b'rust')
    _write(tmp_path, 'vendor/jpexs-source/build.xml')
    report = discover_workspace_tools(tmp_path)
    tools = _tools(report)
    assert tools['native']['configured'] is True
    assert {c['kind'] for c in tools['native']['candidates']} == {'source_checkout', 'portable_cli'}
    assert tools['rust']['configured'] is True
    candidate = tools['rust']['candidates'][0]
    assert candidate['path'] == str(rust)
    assert candidate['sha256'] == hashlib.sha256(b'rust').hexdigest()
    assert candidate['execution_verified'] is False
    assert tools['jpexs']['status'] == 'source_checkout'
    assert tools['jpexs']['configured'] is False
    assert all(tool['measured'] is False for tool in tools.values())


def test_installers_and_asv_companions_are_not_configured(tmp_path):
    _write(tmp_path, 'vendor/installers/as3sorcerer_setup/as3sorcerer_setup.exe')
    _write(tmp_path, 'vendor/installers/flash_decompiler.exe')
    _write(tmp_path, 'vendor/installers/swfdec/Setup.exe')
    _write(tmp_path, 'vendor/installers/asv202201_other/asvpc501/asvpc.exe')
    tools = _tools(discover_workspace_tools(tmp_path))
    for engine in ('as3_sorcerer', 'trillix', 'swfdec'):
        assert tools[engine]['status'] == 'installer'
        assert tools[engine]['configured'] is False
    assert tools['asv']['status'] == 'support_files'
    assert tools['asv']['configured'] is False
    assert tools['sothink']['status'] == 'missing'


def test_multiple_portable_candidates_retain_identity_and_require_configuration(tmp_path):
    first = _write(tmp_path, 'Competing_tools/ffdec/ffdec.jar', b'first')
    second = _write(tmp_path, 'vendor/references/ffdec/ffdec.jar', b'second')
    _write(tmp_path, 'vendor/jpexs-source/build.xml')
    report = discover_workspace_tools(tmp_path)
    tool = _tools(report)['jpexs']
    assert tool['status'] == 'portable_cli'
    assert tool['configured'] is False
    jars = [candidate for candidate in tool['candidates'] if candidate['kind'] == 'portable_cli']
    assert {candidate['path'] for candidate in jars} == {str(first), str(second)}
    assert len({candidate['sha256'] for candidate in jars}) == 2
    assert str(tmp_path / 'Competing_tools') in report['external_tools_search_roots']


def test_symlink_to_outside_workspace_is_not_discovered(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    _write(outside, 'ffdec.jar')
    try:
        (workspace / 'Competing_tools').symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('Creating symlinks is not allowed on this host')
    report = discover_workspace_tools(workspace)
    assert _tools(report)['jpexs']['candidates'] == []


def test_skipped_build_trees_and_missing_workspace(tmp_path):
    _write(tmp_path, 'target/debug/ffdec.jar')
    _write(tmp_path, 'node_modules/ffdec.jar')
    tools = _tools(discover_workspace_tools(tmp_path))
    assert tools['jpexs']['status'] == 'missing'
    missing = discover_workspace_tools(tmp_path / 'missing')
    assert 'scan_diagnostic' in missing
    assert all(tool['status'] == 'missing' for tool in missing['tools'])


def test_source_launchers_without_compiled_jar_are_support_files(tmp_path):
    _write(tmp_path, 'vendor/jpexs-source/build.xml')
    _write(tmp_path, 'vendor/jpexs-source/resources/ffdec-cli.exe')
    _write(tmp_path, 'vendor/jpexs-source/resources/ffdec.bat')
    _write(tmp_path, 'vendor/jpexs-source/resources/ffdec.exe')
    tool = _tools(discover_workspace_tools(tmp_path))['jpexs']
    assert tool['status'] == 'source_checkout'
    assert tool['configured'] is False
    launchers = [c for c in tool['candidates'] if Path(c['path']).is_file()]
    assert launchers and all(c['kind'] == 'support_files' for c in launchers)


def test_distribution_folder_name_does_not_imply_source_checkout(tmp_path):
    _write(tmp_path, 'jpexs/ffdec.jar')
    _write(tmp_path, 'jpexs/ffdec.bat')
    tool = _tools(discover_workspace_tools(tmp_path))['jpexs']
    assert tool['status'] == 'portable_cli'
    assert all(c['kind'] != 'source_checkout' for c in tool['candidates'])


def test_apache_flex_dump_is_separate_from_swftools_and_not_auto_configured(tmp_path):
    jar = _write(tmp_path, 'vendor/toolpacks/flex/lib/swfdump.jar', b'Apache Flex dump')
    _write(tmp_path, 'Competing_tools/swftools/swfextract.exe')
    tools = _tools(discover_workspace_tools(tmp_path))
    assert tools['flex_swfdump']['status']=='portable_cli'
    assert tools['flex_swfdump']['candidates'][0]['path']==str(jar)
    assert not tools['flex_swfdump']['configured']
    assert tools['swfextract']['status']=='portable_cli'


def test_isolated_pyflashkit_and_ruffle_assets_are_discovered_without_running(tmp_path):
    _write(tmp_path, 'vendor/references/extra-tools/pyflashkit-1.3.0/.venv/Scripts/python.exe')
    _write(tmp_path, 'vendor/references/extra-tools/pyflashkit-1.3.0/.venv/Lib/site-packages/flashkit/workspace/__init__.py')
    _write(tmp_path, 'vendor/references/extra-tools/ruffle-source-test/Cargo.toml')
    _write(tmp_path, 'ui/public/ruffle/ruffle.js')
    tools = _tools(discover_workspace_tools(tmp_path))
    assert tools['pyflashkit']['status']=='portable_cli' and not tools['pyflashkit']['configured']
    assert {c['kind'] for c in tools['ruffle']['candidates']}=={'source_checkout','support_files'}
    assert all(not c['execution_verified'] for c in tools['pyflashkit']['candidates']+tools['ruffle']['candidates'])
