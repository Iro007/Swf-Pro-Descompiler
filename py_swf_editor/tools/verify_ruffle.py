"""Execute authored fixtures in the real Ruffle WASM runtime and Chromium.

Only owned loopback pages and local SWFs are used. A passing fixture proves the
recorded trace/render scenario, not complete Flash compatibility.
"""
import argparse
import base64
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

PAGE='''<!doctype html><meta charset="utf-8"><title>Owned Ruffle Runtime Verification</title>
<style>body{margin:0;background:#18202e;font:16px sans-serif;color:white}#owned-stage{width:640px;height:480px}ruffle-player{width:100%;height:100%}pre{white-space:pre-wrap}</style>
<div id="owned-stage"></div><pre id="traces"></pre>
<script>window.RufflePlayer={config:{publicPath:'/ruffle/',polyfills:false}};window.observed=[];window.errors=[];</script>
<script src="/ruffle/ruffle.js"></script>
<script>window.runMovie=async function(data,name,url){
 window.observed=[];window.errors=[];
 const element=window.RufflePlayer.newest().createPlayer();document.getElementById('owned-stage').replaceChildren(element);window.player=element;
 const api=element.ruffle();
 const trace=message=>{window.observed.push(message);document.getElementById('traces').textContent=window.observed.join('\\n');};
 element.addEventListener('loadedmetadata',()=>{api.traceObserver=trace;},{once:true});
 const movie=url?{url}:{data:Uint8Array.from(atob(data),c=>c.charCodeAt(0)),swfFileName:name};
 await api.load({...movie,publicPath:'/ruffle/',polyfills:false,autoplay:'off',allowNetworking:'none',allowScriptAccess:false,openUrlMode:'deny',allowFullscreen:false,splashScreen:false,unmuteOverlay:'hidden',maxExecutionDuration:10});
 api.traceObserver=trace;api.resume();
 return {loaded:true};};</script>'''


def native_fixture(output):
    from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
    source='''package runtime { import flash.display.Sprite;
    public class NativeStage extends Sprite { public function NativeStage(){
      super(); this.graphics.beginFill(16711680); this.graphics.drawRect(40,40,160,100);
      this.graphics.endFill(); trace("SUITE_NATIVE_OK");
    } } }'''
    output.mkdir(parents=True,exist_ok=True)
    path=output/'native-sprite.swf'
    (output/'NativeStage.as').write_text(source,encoding='utf-8')
    path.write_bytes(class_program_swf(compile_class_program(source),document_class='runtime.NativeStage',width=640,height=480))
    return path


def linked_fixture(output):
    from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
    source='''package runtime { import flash.display.Sprite; import model.IValue; import model.Child;
    public class NativeLinked extends Sprite { public function NativeLinked(){
      super(); var c:IValue = new Child(); trace("SUITE_NATIVE_LINKED_VALUE"); trace(c.value());
      this.graphics.beginFill(255); this.graphics.drawRect(40,40,160,100); this.graphics.endFill();
      trace("SUITE_NATIVE_LINKED_OK");
    } } }'''
    extra={'model/Hierarchy.as':'''package model {
      public interface IValue { function value():int; }
      public class Base { protected var n:int=0; public function Base(value:int){super();n=value;}
        public function value():int{return n;} }
      public class Child extends Base implements IValue { public function Child(){super(3);}
        override public function value():int{return super.value()+1;} }
    }'''}
    output.mkdir(parents=True,exist_ok=True)
    (output/'NativeLinked.as').write_text(source,encoding='utf-8')
    (output/'Hierarchy.as').write_text(extra['model/Hierarchy.as'],encoding='utf-8')
    path=output/'native-linked.swf'
    path.write_bytes(class_program_swf(compile_class_program(source,sources=extra),document_class='runtime.NativeLinked',width=640,height=480))
    return path


def external_fixtures(output):
    """Authored debug/host-trace variants; existing integration SWFs stay immutable."""
    from py_swf.suite_tools import run_tool
    from py_swf.universal import UniversalSuite
    from py_swf.validation import validate_swf_bytes
    output.mkdir(parents=True,exist_ok=True)
    haxe_folder=output/'source-haxe';haxe_folder.mkdir(exist_ok=True)
    haxe=haxe_folder/'Main.hx'
    haxe.write_text('class Main { static function main(){ var sprite=new flash.display.Sprite(); sprite.graphics.beginFill(0x11aa33); sprite.graphics.drawRect(40,40,160,100); sprite.graphics.endFill(); flash.Lib.current.addChild(sprite); flash.Lib.trace("SUITE_HAXE_OK"); } }',encoding='utf-8')
    flex_folder=output/'source-flex';flex_folder.mkdir(exist_ok=True)
    flex=flex_folder/'Hello.as'
    flex.write_text('package { import flash.display.Sprite; public class Hello extends Sprite { public function Hello(){ super(); graphics.beginFill(0x33aa11); graphics.drawRect(40,40,160,100); graphics.endFill(); trace("SUITE_AS3_OK"); } } }',encoding='utf-8')
    haxe_result=output/'haxe-host-trace.swf';flex_result=output/'as3-flex-debug.swf'
    with __import__('tempfile').TemporaryDirectory(prefix='ruffle-authored-build-') as directory:
        state=Path(directory)
        suite=UniversalSuite(state/'state',state/'tools.json')
        try:
            suite.compile_project({'profile':'haxe','entry':str(haxe.resolve()),'output':str(haxe_result.resolve())})
            compiler=Path(suite.registry.executable('flex'));frameworks=compiler.parent.parent/'frameworks'
            command=[suite.registry.executable('java'),'-Xmx1024m','-jar',str(compiler),'+flexlib='+str(frameworks),'+playerglobalHome='+str(frameworks/'libs/player'),'-target-player=32.0','-debug=true','-omit-trace-statements=false','-output='+str(flex_result.resolve()),'-source-path='+str(flex_folder.resolve()),str(flex.resolve())]
            environment=suite.registry.environment();environment['PLAYERGLOBAL_HOME']=str(frameworks/'libs/player')
            log=run_tool(command,cwd=flex_folder.resolve(),env=environment)
            validation=validate_swf_bytes(flex_result.read_bytes())
            if not validation['valid']:raise ValueError('Invalid authored Flex fixture: '+str(validation))
            (output/'compiler-provenance.json').write_text(json.dumps({'haxe':suite.registry.tools.get('haxe'),'flex':suite.registry.tools.get('flex'),'flex_flags':['debug=true','omit-trace-statements=false'],'flex_log':log,'flash_behavior':'not_run_by_compilation'},indent=2),encoding='utf-8')
        finally:suite.close()
    return haxe_result,flex_result


def verify(fixtures,assets,output,timeout=90, *, load_mode="url"):
    from playwright.sync_api import sync_playwright
    from PIL import Image
    output.mkdir(parents=True,exist_ok=True)
    assets=assets.resolve()
    movies={name:path.read_bytes() for name,path,_ in fixtures}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/':
                body=PAGE.encode();mime='text/html; charset=utf-8'
            elif self.path.startswith('/ruffle/'):
                target=(assets/self.path[len('/ruffle/'):].split('?',1)[0]).resolve()
                if not target.is_relative_to(assets) or not target.is_file():
                    self.send_error(404);return
                body=target.read_bytes();mime='application/wasm' if target.suffix=='.wasm' else 'text/javascript' if target.suffix=='.js' else 'application/octet-stream'
            elif self.path.startswith('/fixtures/') and self.path[len('/fixtures/'):] in movies:
                body=movies[self.path[len('/fixtures/'):]];mime='application/x-shockwave-flash'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    report={'runtime':'Ruffle','runtime_version':'0.7.1','runtime_source':'https://registry.npmjs.org/@ruffle-rs/ruffle/-/ruffle-0.7.1.tgz','networking':'none','javascript_access':False,'load_mode':load_mode,'fixtures':[],'scope':'Authored fixture trace and rendered scene; not global Flash compatibility or decompiler equivalence'}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--enable-unsafe-swiftshader'])
            report['browser_version']=browser.version
            for name,path,expected in fixtures:
                page=browser.new_page(viewport={'width':800,'height':650});errors=[];logs=[];external=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('console',lambda message:logs.append({'type':message.type,'text':message.text}))
                def route(request):
                    if request.request.url.startswith(f'http://127.0.0.1:{server.server_port}/'):
                        request.continue_()
                    else:external.append(request.request.url);request.abort()
                page.route('**/*',route)
                started=time.monotonic();data=path.read_bytes();row={'name':name,'input':str(path.resolve()),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'expected_trace':expected,'passed':False}
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}/',wait_until='networkidle')
                    page.wait_for_function('window.RufflePlayer && typeof window.RufflePlayer.newest === "function"',timeout=30000)
                    movie_url=f'http://127.0.0.1:{server.server_port}/fixtures/{name}' if load_mode=='url' else None
                    page.evaluate('([data,name,url])=>Promise.race([window.runMovie(data,name,url),new Promise((_,reject)=>setTimeout(()=>reject(new Error("Ruffle load timeout")),60000))])',[base64.b64encode(data).decode(),path.name,movie_url])
                    page.wait_for_function('(expected)=>window.observed.some(line=>line.includes(expected))',arg=expected,timeout=timeout*1000)
                    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))')
                    screenshot=output/(name+'.png');page.locator('#owned-stage').screenshot(path=str(screenshot))
                    image=Image.open(screenshot).convert('RGB');colors=image.getcolors(image.width*image.height) or []
                    colored=sum(count for count,(r,g,b) in colors if max(r,g,b)-min(r,g,b)>60 and max(r,g,b)>120)
                    expected_colors={'haxe':(17,170,51),'flex':(51,170,17),'haxeflixel':(51,170,17),'native':(255,0,0),'native-linked':(0,0,255)}
                    target=expected_colors.get(name)
                    matching=sum(count for count,color in colors if target and all(abs(a-b)<=12 for a,b in zip(color,target))) if target else colored
                    value_verified=name!='native-linked' or page.evaluate('window.observed').index('4')>page.evaluate('window.observed').index('SUITE_NATIVE_LINKED_VALUE')
                    row.update(trace_verified=True,render_colored_pixels=colored,expected_scene_color=target,matching_scene_pixels=matching,render_verified=matching>100,value_verified=value_verified,screenshot=str(screenshot.resolve()),passed=not errors and matching>100 and value_verified)
                except Exception as error:
                    row['error']=str(error);page.screenshot(path=str(output/(name+'-failure.png')),full_page=True)
                row.update(traces=page.evaluate('window.observed || []'),page_errors=errors,console=logs[-80:],blocked_external_requests=external,elapsed_seconds=round(time.monotonic()-started,3))
                report['fixtures'].append(row);page.close()
                print(name,'PASS' if row['passed'] else 'FAIL',row.get('traces'),flush=True)
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
    report['passed']=all(row['passed'] for row in report['fixtures'])
    (output/'runtime-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'reports/ruffle-runtime-final');parser.add_argument('--timeout',type=int,default=90);parser.add_argument('--load-mode',choices=('data','url'),default='url');args=parser.parse_args()
    assets=ROOT.parent/'RUST SWF/web/public/ruffle'
    haxe,flex=external_fixtures(args.output)
    fixtures=[('haxe',haxe,'SUITE_HAXE_OK'),('flex',flex,'SUITE_AS3_OK'),('haxeflixel',ROOT/'reports/suite-integration/haxeflixel-fixture.swf','SUITE_FLIXEL_OK'),('native',native_fixture(args.output),'SUITE_NATIVE_OK'),('native-linked',linked_fixture(args.output),'SUITE_NATIVE_LINKED_OK')]
    result=verify(fixtures,assets,args.output,args.timeout,load_mode=args.load_mode)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
