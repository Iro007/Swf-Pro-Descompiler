import json
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args, ok=True):
    result = subprocess.run([sys.executable,'-m','py_swf.universal_worker','cli',*map(str,args)],
                            cwd=PROJECT_ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=30,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    return json.loads(result.stdout if ok else result.stderr)


def test_console_build_edit_archive_rebuild_and_protected_destination(tmp_path):
    entry = tmp_path/'Counter.as'
    text = 'package { public class Counter { public function answer():int { return 4; } } }'
    entry.write_text(text)
    original = tmp_path/'original.swf'
    project = tmp_path/'original.flashproj'
    built = run_cli('compile','--input',entry,'--profile','native-as3','--output',original,'--project-output',project)
    assert built['result']['validation']['valid']
    original_bytes = original.read_bytes()
    changed = tmp_path/'edited.as'
    changed.write_text(text.replace('return 4','return 14'))
    edited_project = tmp_path/'draft.flashproj'
    edited = run_cli('source-edit','--input',project,'--source',changed,'--project-output',edited_project)
    assert edited['result']['source_pending']
    assert edited['result']['revision']==built['result']['revision']
    rebuilt = run_cli('compile-saved','--input',edited_project,'--output',tmp_path/'new.swf',
                      '--project-output',tmp_path/'rebuilt.flashproj')
    assert rebuilt['result']['validation']['valid'] and not rebuilt['result']['source_pending']
    failed = run_cli('compile-saved','--input',edited_project,'--output',original,ok=False)
    assert 'original' in failed['error']
    assert original.read_bytes()==original_bytes and entry.read_text()==text


def test_console_resource_inspection_and_edits_persist_across_processes(tmp_path):
    from py_swf.swf_parser import SWFFile, SWFTag, make_rect
    movie = SWFFile()
    movie.tags=[SWFTag(37,b'\x09\x00'+make_rect(0,2000,0,1000)+b'\x80\x00value\x00Old\x00'),SWFTag(1,b''),SWFTag(0,b'')]
    swf = tmp_path/'text.swf'
    swf.write_bytes(movie.save_bytes())
    state = tmp_path/'state'
    info = run_cli('resource-info','--input',swf,'--tag-index',0)
    assert info['parsed']['initial_text']=='Old'
    args_file = tmp_path/'edit.json'
    args_file.write_text(json.dumps({'operation':'text','text':'Nuevo texto'}))
    edited = run_cli('resource-edit','--input',swf,'--tag-index',0,'--args-file',args_file,'--state-dir',state)
    current = run_cli('resource-info','--tag-index',0,'--state-dir',state)
    assert current['parsed']['initial_text']=='Nuevo texto'
    undone = run_cli('undo','--state-dir',state)
    assert undone['revision']!=edited['revision']
    assert run_cli('resource-info','--tag-index',0,'--state-dir',state)['parsed']['initial_text']=='Old'


def test_console_catalog_names_and_rpc_errors(tmp_path):
    catalog = run_cli('engines')
    names = {record['id']:record['name'] for record in catalog['engines']}
    assert names['python']=='PySWF' and names['rust']=='RustSWF'
    failed = run_cli('rpc','--rpc-operation','nonexistent',ok=False)
    assert not failed['ok'] and 'desconocida' in failed['error']
    assert run_cli('rpc','--rpc-operation','ping')['protocol_version']==1


def test_console_utf8_under_cp1252_locale(tmp_path):
    import os
    env = {**os.environ, 'PYTHONIOENCODING': 'cp1252'}
    result = subprocess.run(
        [sys.executable, '-m', 'py_swf.universal_worker', 'cli', 'rpc', '--rpc-operation', 'nonexistent'],
        cwd=PROJECT_ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=30, env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    assert result.returncode != 0
    payload = json.loads(result.stderr)
    assert not payload['ok'] and 'Operación' in payload['error']
