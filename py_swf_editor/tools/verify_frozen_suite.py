"""Frozen-worker integration from another cwd, without a system Python PATH."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import queue
import threading
import traceback

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
sys.path.insert(0,str(PROJECT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path, default=PROJECT / 'dist/FlashSuiteWorker.exe')
    parser.add_argument('--output', type=Path, default=PROJECT / 'reports/suite-integration/frozen-worker.json')
    options = parser.parse_args()
    executable = options.worker.resolve(strict=True)
    fixture = PROJECT / 'reports/suite-integration/suite-editable.swf'
    original = fixture.read_bytes()
    report = {'scope':'Frozen PySWF, real JSONL and CLI, other cwd, no global Python PATH',
              'gui_started':False,'browser_started':False,'flash_runtime_verified':False,
              'worker_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),'checks':[],'passed':False}
    with tempfile.TemporaryDirectory(prefix='suite-frozen-') as temporary:
        folder = Path(temporary)
        env = {**os.environ,'FLASH_SUITE_WORKSPACE':str(ROOT),'FLASH_SUITE_STATE_DIR':str(folder / 'state'),'PYTHONIOENCODING':'utf-8','PATH':os.environ.get('SystemRoot','C:/Windows') + '/System32'}
        env.pop('PYTHONPATH',None)
        env.pop('PYTHONHOME',None)
        env['LOCALAPPDATA'] = str(folder/'profile')
        child = subprocess.Popen([str(executable),'worker'],cwd=folder,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        replies = queue.Queue()
        def read_replies():
            for line in child.stdout:
                replies.put(line)
            replies.put(None)
        threading.Thread(target=read_replies,daemon=True).start()
        sequence = 0
        def call(operation,args=None,ok=True):
            nonlocal sequence
            sequence += 1
            child.stdin.write(json.dumps({'id':sequence,'op':operation,'args':args or {}})+'\n');child.stdin.flush()
            line = replies.get(timeout=120)
            assert line is not None, 'Worker exited before replying'
            response = json.loads(line)
            assert response['id']==sequence,response
            assert response['ok'] is ok,response
            return response.get('result',response)
        try:
            ping = call('ping')
            assert ping['version']=='0.3.0' and ping['engines']['python']=='PySWF'
            catalog = call('engines')
            report['catalog'] = catalog
            opened = call('open',{'path':str(fixture)})
            units = call('scripts')['items']
            unit = next(u for u in units if 'answer' in u['name'])
            call('decompile',{'unit_id':unit['id'],'engine':'python'})
            call('edit',{'unit_id':unit['id'],'code':'if (','mode':'source','base_revision':opened['revision']},ok=False)
            assert call('state')['revision']==opened['revision']
            edited = call('edit',{'unit_id':unit['id'],'code':'return 11;','mode':'source','base_revision':opened['revision']})
            assert edited['revision']!=opened['revision']
            assert call('undo')['revision']==opened['revision']
            assert call('redo')['revision']==edited['revision']
            project = call('save_project',{'path':str(folder/'saved.pyswfproj')})
            assert project['format_version']==3
            assert call('load_project',{'path':project['path']})['revision']==edited['revision']
            call('export_swf',{'path':str(folder/'output.swf')})
            assert call('validate')['valid']
            comparison = call('compare',{'unit_id':unit['id']})
            assert {item['engine'] for item in comparison['results']}=={'python','rust','jpexs'}
            assert all(item.get('pcode') and not item.get('status') for item in comparison['results']), comparison
            assert all(item.get('source') or item['diagnostics'] for item in comparison['results']), comparison
            report['comparison'] = [{'engine':item['engine'],'source_sha256':hashlib.sha256(item['source'].encode('utf-8')).hexdigest() if item.get('source') else None,
                                      'diagnostics':item['diagnostics']} for item in comparison['results']]
            capabilities = call('tool_operation',{'id':'rust','operation':'capabilities'})
            assert capabilities['display_name']=='RustSWF'
            wrapped = call('tool_operation',{'id':'rust','operation':'roundtrip','path':str(folder/'rust-roundtrip.swf'),'compression':'zws'})
            assert wrapped['normalized_bytes_identical']
            metadata = call('tool_operation',{'id':'rust','operation':'abc_metadata','unit_id':unit['id']})
            assert 'items' in metadata
            report['checks'] += ['ping and catalog','open and inventory','native source','invalid source rejected without mutation','edit and validated SWF','exact undo/redo revision hashes','project v3 save/load','SWF export and independent validation','real Python/Rust/JPEXS comparison']
            assert fixture.read_bytes()==original
            report['checks'].append('original unchanged')
            report['checks'].append('suite RustSWF capabilities, metadata and native wrapper writer')
            entry = folder/'FrozenCounter.as'
            first_source = 'package { public class FrozenCounter { public function answer():int { return 7; } } }'
            entry.write_text(first_source,encoding='utf-8')
            built = call('compile_project',{'profile':'native-as3','entry':str(entry),'output':str(folder/'first.swf'),'width':640,'height':360,'frame_rate':25})
            document = call('source_files',{'id':'project:entry'})
            closure_source = 'package { public class FrozenCounter { public function answer():int { function make(n:int):Function { return function():int { n++; return n; }; } var a:Function=make(1); var b:Function=make(10); return a()+b()+a(); } } }'
            draft = call('source_edit',{'id':'project:entry','source':closure_source,'base_revision':built['revision'],'base_source_sha256':document['source_sha256']})
            assert draft['source_pending'] and draft['revision']==built['revision']
            call('source_edit',{'id':'project:entry','source':'bad','base_revision':built['revision'],'base_source_sha256':document['source_sha256']},ok=False)
            assert call('undo')['source_revision']==built['source_revision']
            assert call('redo')['source_revision']==draft['source_revision']
            rebuilt = call('compile_saved',{'output':str(folder/'closures.swf')})
            assert rebuilt['validation']['valid'] and not rebuilt['source_pending']
            from py_swf.script_inventory import enumerate_scripts
            from tests.native_control_vm import run_bytes
            answer = next(unit for unit in enumerate_scripts((folder/'closures.swf').read_bytes()) if 'answer' in unit.name)
            # The owned case uses only method-local cells. Supply the outer
            # scope count declared by the real class method's ABC metadata;
            # running a method without that class scope is not a valid frame.
            initial_scope = tuple({} for _ in range(answer.body.init_scope_depth))
            execution = run_bytes(answer.code,'as3',abc=answer.abc,body=answer.body,captured=initial_scope)
            assert execution.value==16 and not execution.raised, execution
            assert entry.read_text(encoding='utf-8')==first_source
            report['checks'] += ['source-only edit with stale text guard','source-only undo and redo','compile saved closures using frozen compiler','independent bounded bytecode VM returns 16','external source unchanged']
            report['vm_scope'] = {'init_scope_depth':answer.body.init_scope_depth,'outer_scopes':'empty owned scope records; fixture uses only local cells','class_initializers_executed':False}

            from py_swf.swf_parser import SWFFile,SWFTag,make_rect
            from tests.test_suite_font_documents import fixture as font_fixture
            resource_movie = SWFFile()
            font = font_fixture()
            resource_movie.tags=[font,SWFTag(37,b'\x09\x00'+make_rect(0,2000,0,1000)+b'\x80\x00value\x00Old\x00'),SWFTag(1,b''),SWFTag(0,b'')]
            resource_path = folder/'resources.swf'
            resource_path.write_bytes(resource_movie.save_bytes())
            resources = call('open',{'path':str(resource_path)})
            info = call('resource_info',{'tag_index':0})
            assert info['kind']=='font'
            font_edit = call('resource_edit',{'operation':'font-glyph','tag_index':0,'glyph_index':0,'point_index':1,'x':80,'y':20,'base_revision':resources['revision']})
            assert call('resource_info',{'tag_index':0})['glyphs'][0]['points'][1]['x']==80
            call('resource_edit',{'operation':'text','tag_index':1,'text':'Texto nuevo','base_revision':font_edit['revision']})
            assert call('resource_info',{'tag_index':1})['parsed']['initial_text']=='Texto nuevo'
            call('export_swf',{'path':str(folder/'edited-resources.swf')})
            from py_swf.font_documents import read_font
            final_movie = SWFFile();final_movie.read_bytes((folder/'edited-resources.swf').read_bytes())
            final_font = read_font(final_movie.tags[0])
            assert final_font['_raw'][1]==read_font(font)['_raw'][1]
            assert final_movie.tags[0].data[3]==font.data[3]
            assert resource_path.read_bytes()==resource_movie.save_bytes()
            report['checks'] += ['frozen glyph and text resource edits','unedited glyph and font language retained','resource original unchanged']

            cli = subprocess.run([str(executable),'cli','inspect','--input',str(folder/'closures.swf')],
                                 cwd=folder,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            assert cli.returncode==0,cli.stderr
            assert json.loads(cli.stdout)['validation']['valid']
            bad_cli = subprocess.run([str(executable),'cli','rpc','--rpc-operation','missing'],
                                     cwd=folder,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            assert bad_cli.returncode==1 and not json.loads(bad_cli.stderr)['ok']
            report['checks'] += ['frozen console mode without system Python','frozen console error exit code']
            report['passed'] = True
        except Exception as error:
            report['error'] = str(error)
            report['traceback'] = traceback.format_exc()[-6000:]
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill();child.wait()
            report['stderr'] = child.stderr.read()[-8000:]
    target = options.output
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('catalog','stderr')},ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
