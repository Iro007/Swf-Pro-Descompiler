"""Public imports come from typed bytecode uses, including closure bodies."""
from pathlib import Path
import sys

import pytest

from py_swf.avm2 import assemble_instructions
from py_swf.class_documents import reconstruct_abc_documents, reconstruct_class_sections
from py_swf.compiler.class_compiler import compile_class_program
from py_swf.decompile import hierarchy_contract


MODEL = 'package app {public class Model {public function Model(n:int){}}}'


@pytest.mark.parametrize("body", [
    'public function make():* {return new app.Model(4);}',
    'public function make():Function {return function():* {return new app.Model(4);};}',
    'public function make():Function {return function(value:app.Model):* {return value;};}',
])
def test_body_and_closure_only_class_uses_have_exact_public_imports(body):
    abc = compile_class_program('package {public class Main {' + body + '}}' + MODEL).abc
    main = reconstruct_class_sections(abc)[0]
    assert main['completeness'] == 'complete', main['diagnostics']
    assert 'import app.Model;' in main['source']
    assert main['source'].count('import app.Model;') == 1


def test_unused_pool_class_and_unproven_package_property_do_not_create_imports():
    abc = compile_class_program('package {public class Main {public function Main(){}}}' + MODEL).abc
    init = next(body for body in abc.method_bodies if body.method == abc.instances[0].iinit)
    init.code = assemble_instructions(abc.constant_pool, 'getlocal_0\npushscope\ngetlocal_0\nconstructsuper 0\ngetlex opaque.Factory\npop\nreturnvoid')
    source = reconstruct_class_sections(abc)[0]['source']
    assert 'import app.Model;' not in source
    assert 'import opaque.Factory;' not in source


@pytest.mark.parametrize('member', ['public var app:*;', 'public var Model:*;'])
def test_body_import_shadow_keeps_document_partial(member):
    abc = compile_class_program('package {public class Main {' + member + 'public function make():*{return new app.Model(4);}}}' + MODEL).abc
    main = reconstruct_class_sections(abc)[0]
    assert main['completeness'] == 'partial'
    assert any('import is shadowed' in message for message in main['diagnostics'])
    assert not main['editable_source'] and main['unit_ids']


def test_two_body_only_imports_with_same_leaf_remain_partial():
    abc = compile_class_program('package {public class Main {public function make():* {new app.Model(4);return new other.Model();}}}' + MODEL + 'package other {public class Model{}}').abc
    main = reconstruct_class_sections(abc)[0]
    assert main['completeness'] == 'partial'
    assert any('same-name collision' in message for message in main['diagnostics'])


def test_source_sdk_location_ignores_frozen_workspace_selector(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    monkeypatch.setenv('FLASH_SUITE_WORKSPACE', str(tmp_path))
    paths = []
    monkeypatch.setattr(hierarchy_contract, '_external_hierarchy', lambda path: paths.append(path) or {})
    hierarchy_contract.external_hierarchy()
    assert paths == [Path(hierarchy_contract.__file__).resolve().parents[3] / 'toolpacks/flex/frameworks/libs/player/32.0/playerglobal.swc']


def test_frozen_sdk_uses_explicit_workspace_instead_of_module_extraction(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(hierarchy_contract, '__file__', str(tmp_path / '_MEI/module/py_swf/decompile/hierarchy_contract.py'))
    workspace = tmp_path / 'configured-workspace'
    monkeypatch.setenv('FLASH_SUITE_WORKSPACE', str(workspace))
    paths = []
    monkeypatch.setattr(hierarchy_contract, '_external_hierarchy', lambda path: paths.append(path) or {})
    hierarchy_contract.external_hierarchy()
    assert paths == [workspace / 'toolpacks/flex/frameworks/libs/player/32.0/playerglobal.swc']


def test_frozen_missing_workspace_and_changed_sdk_cannot_supply_a_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.delenv('FLASH_SUITE_WORKSPACE', raising=False)
    assert not hierarchy_contract.external_hierarchy()
    monkeypatch.setenv('FLASH_SUITE_WORKSPACE', str(tmp_path))
    sdk = tmp_path / 'toolpacks/flex/frameworks/libs/player/32.0/playerglobal.swc'
    sdk.parent.mkdir(parents=True)
    sdk.write_bytes(b'changed SDK must never be parsed as trusted declarations')
    assert not hierarchy_contract.external_hierarchy()


def test_frozen_configured_verified_sdk_preserves_native_sprite_script_admission(monkeypatch):
    workspace = Path(__file__).resolve().parents[2]
    if not (workspace / 'toolpacks/flex/frameworks/libs/player/32.0/playerglobal.swc').is_file():
        pytest.skip('Local SDK unavailable')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('FLASH_SUITE_WORKSPACE', str(workspace))
    abc = compile_class_program('import flash.display.Sprite; public class Main extends Sprite {public function Main(){super();trace(7);}}').abc
    script = reconstruct_abc_documents(abc)[-1]
    assert script['completeness'] == 'complete', script['diagnostics']
    assert script['compiler_eligibility']['eligible']
