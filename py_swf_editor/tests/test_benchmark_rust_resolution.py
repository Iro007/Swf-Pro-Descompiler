"""Rust discovery priorities and boundaries; fixture executables never run."""
import hashlib
import json
from pathlib import Path

import pytest

import py_swf.benchmark_engines as registry


NAMES=('FlashUniversalStudio-Analysis-20261014','FlashUniversalStudio-Parity-20261014',
       'FlashUniversalStudio-Engines','RustSWFStudio')


def write(path,contents=b'not an executable; resolution fixture only'):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(contents)
    return path.resolve()


@pytest.mark.parametrize('first',range(4))
def test_source_prefers_current_delivery_then_legacy_fallbacks(tmp_path,monkeypatch,first):
    project=tmp_path/'workspace/py_swf_editor';project.mkdir(parents=True)
    monkeypatch.setattr(registry,'ROOT',project)
    monkeypatch.setattr(registry.sys,'frozen',False,raising=False)
    candidates=[write(project.parent/'RUST SWF/dist'/name/'rustswf.exe') for name in NAMES[first:]]
    assert registry.default_rust_executable()==candidates[0]


def test_portable_prefers_peer_outside_the_development_workspace(tmp_path,monkeypatch):
    project=tmp_path/'workspace/py_swf_editor';project.mkdir(parents=True)
    preferred=write(tmp_path/'portable/rustswf.exe')
    write(project.parent/'RUST SWF/dist'/NAMES[0]/'rustswf.exe')
    monkeypatch.setattr(registry,'ROOT',project)
    monkeypatch.setattr(registry.sys,'frozen',True,raising=False)
    monkeypatch.setattr(registry.sys,'executable',str(tmp_path/'portable/FlashUniversalStudio.exe'))
    assert registry.default_rust_executable()==preferred


def test_source_does_not_pick_an_unrelated_interpreter_peer(tmp_path,monkeypatch):
    project=tmp_path/'workspace/py_swf_editor';project.mkdir(parents=True)
    write(tmp_path/'interpreter/rustswf.exe')
    expected=write(project.parent/'RUST SWF/dist'/NAMES[1]/'rustswf.exe')
    monkeypatch.setattr(registry,'ROOT',project)
    monkeypatch.setattr(registry.sys,'frozen',False,raising=False)
    monkeypatch.setattr(registry.sys,'executable',str(tmp_path/'interpreter/python.exe'))
    assert registry.default_rust_executable()==expected


@pytest.mark.parametrize('frozen',[False,True])
def test_resolved_candidate_cannot_escape_its_allowed_root(tmp_path,monkeypatch,frozen):
    project=tmp_path/'workspace/py_swf_editor';project.mkdir(parents=True)
    escaped=write(tmp_path/'outside/rustswf.exe')
    candidate=(tmp_path/'portable/rustswf.exe' if frozen else project.parent/'RUST SWF/dist'/NAMES[0]/'rustswf.exe')
    write(candidate)
    expected=write(project.parent/'RUST SWF/dist'/NAMES[1]/'rustswf.exe')
    monkeypatch.setattr(registry,'ROOT',project)
    monkeypatch.setattr(registry.sys,'frozen',frozen,raising=False)
    monkeypatch.setattr(registry.sys,'executable',str(tmp_path/'portable/FlashUniversalStudio.exe'))
    original=Path.resolve
    def resolve(path,*args,**kwargs):
        return escaped if path==candidate else original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',resolve)
    assert registry.default_rust_executable()==expected


def test_explicit_checked_tool_keeps_precedence_over_default_discovery(tmp_path,monkeypatch):
    from py_swf.benchmarks import command_for
    project=tmp_path/'workspace/py_swf_editor';project.mkdir(parents=True)
    write(project.parent/'RUST SWF/dist'/NAMES[0]/'rustswf.exe')
    explicit=write(tmp_path/'pins/explicit.exe',b'explicit pinned bytes')
    digest=hashlib.sha256(explicit.read_bytes()).hexdigest()
    manifest=tmp_path/'pins/tools.json'
    manifest.write_text(json.dumps({'development_only':True,'tools':{'rust':{
        'executable':str(explicit),'artifact_sha256':{str(explicit):digest}}}}),encoding='utf-8')
    monkeypatch.setattr(registry,'ROOT',project)
    monkeypatch.setattr(registry.sys,'frozen',False,raising=False)
    command=command_for('rust',tool_config=manifest)
    assert command[command.index('--executable')+1]==str(explicit)
    assert command[command.index('--sha256')+1]==digest


def test_missing_candidates_remain_unavailable(tmp_path,monkeypatch):
    project=tmp_path/'workspace/py_swf_editor';project.mkdir(parents=True)
    monkeypatch.setattr(registry,'ROOT',project)
    monkeypatch.setattr(registry.sys,'frozen',False,raising=False)
    with pytest.raises(ValueError,match='Rust CLI unavailable'):
        registry.default_rust_executable()
