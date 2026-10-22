"""Browser contract checks with a controlled Tauri bridge; no SWF/player execution.

Run with the existing py_swf_editor .venv, while `npm run preview` serves port 4174.
This checks UI behavior only. Native worker/compiler tests are separate.
"""
import json
from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect

OUTPUT = Path(__file__).resolve().parents[2] / "reports"
OUTPUT.mkdir(exist_ok=True)
MOCK = r"""
window.isTauri = true;
window.__suiteCalls = [];
window.__failEdit = true;
const initial = {filename:'demo.swf',working_path:'C:/controlled/demo.swf',revision:'a'.repeat(64),dirty:false,script_count:2,history:{undo:false,redo:false}};
let state = {...initial};
const catalog = {default_engine:'auto',engines:[
 {id:'python',name:'Python nativo',available:true,version:'controlled',category:'native',path:null,detail:'Controlled bridge fixture',operations:['decompile','edit','assemble','compile_project','resource-bundle','scripts','xml','resources','resource_edit','timeline','benchmark']},
 {id:'rust',name:'Rust',available:true,version:'controlled',category:'native',path:null,detail:'Controlled bridge fixture',operations:['decompile']},
 ...['jpexs','haxe','haxeflixel','flex','mtasc','ruffle'].map(id=>({id,name:id,available:false,version:null,category:'external',path:null,detail:'Not configured in fixture',operations:id==='jpexs'?['decompile','edit','fla','xfl']:id==='ruffle'?['launch']:['compile_project']}))
]};
const units=[{id:'tag:0/body:0',name:'Demo.run',language:'as3',tag_path:[0],body_index:0},{id:'tag:0/body:1',name:'Demo.other',language:'as3',tag_path:[0],body_index:1}];
const source = engine=>({engine:engine==='auto'?'python':engine,source:engine==='rust'?null:'return 1;',pcode:'pushbyte 1\nreturnvalue',diagnostics:[],editable:engine!=='rust',revision:state.revision});
window.__TAURI_INTERNALS__ = {
 metadata:{currentWindow:{label:'main'},currentWebview:{label:'main'}},
 transformCallback:()=>1, unregisterCallback:()=>{}, convertFileSrc:path=>path,
 invoke:async(command,args={})=>{
  if(command.startsWith('plugin:'))return 1;
  if(command==='startup')return {path:'C:/controlled/demo.swf',smoke:false};
  if(command==='ui_verified')return;
  if(command==='open_document')return {session_id:'controlled',source_path:args.path,info:{filename:'demo.swf',sha256:state.revision,signature:'FWS',version:11,width:640,height:480,frame_rate:24,frame_count:2,tag_count:1},diagnostics:[],script_count:2};
  if(command==='rpc')return {items:[],total:0,offset:0};
  if(command==='suite_cancel')return;
  if(command==='suite_pick')return args.kind==='project_output'?'C:/controlled/project.pyswfproj':null;
  if(command!=='suite_rpc')throw new Error('Unexpected bridge command: '+command);
  window.__suiteCalls.push({operation:args.operation,args:args.args});
  const a=args.args??{};
  switch(args.operation){
   case 'engines':case 'configure_engine':return catalog;
   case 'open':state={...initial};return state;
   case 'state':return state;
   case 'scripts':return {items:units.filter(u=>u.name.toLowerCase().includes((a.query??'').toLowerCase())),total:2,offset:0};
   case 'decompile':return source(a.engine);
   case 'compare':return {results:[source('python'),source('rust')]};
   case 'edit':if(window.__failEdit)throw new Error('Controlled compiler rejection');state={...state,revision:'b'.repeat(64),dirty:true,working_path:'C:/controlled/staged.swf',history:{undo:true,redo:false}};return state;
   case 'undo':state={...state,revision:'a'.repeat(64),history:{undo:false,redo:true}};return state;
   case 'redo':state={...state,revision:'b'.repeat(64),history:{undo:true,redo:false}};return state;
   case 'save_project':return {...state,path:a.path};
   case 'export_swf':return {...state,path:a.path};
   case 'validate':return {valid:true,errors:[],warnings:[]};
   case 'resources':return {items:[{id:'tag:0',tag_index:0,index:0,name:'Controlled image',type:'image',formats:['png']}],total:1,offset:0};
   case 'resource_edit':state={...state,revision:'c'.repeat(64),dirty:true,history:{undo:true,redo:false}};return state;
   case 'timeline':return {frame:a.frame,frame_count:2,display_list:[{depth:1,character_id:1}],svg:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect width="640" height="480" fill="#133c35"/><circle cx="320" cy="240" r="100" fill="#79dbb6"/><script>window.__injected=1</script><foreignObject><div>unsafe</div></foreignObject></svg>',actionscript_executed:false};
   case 'compile_project':state={...state,filename:'compiled.swf',working_path:a.output,revision:'d'.repeat(64),dirty:false};return {...state,path:a.output,engine:'python',validation:{valid:true,errors:[],warnings:[]},log:'Controlled UI fixture; compiler not executed'};
   case 'benchmark':return {path:'C:/controlled/report.json',html_path:'C:/controlled/report.html',completed_files:2,scheduled_files:2,claims:{parity:false,superiority:false,performance_winner:null}};
   default:throw new Error('Unexpected suite operation: '+args.operation);
  }
 }
};
"""

with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page(viewport={"width":1440,"height":960})
    page.set_default_timeout(8000)
    errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.add_init_script(MOCK)
    page.goto("http://127.0.0.1:4174",wait_until="networkidle")
    expect(page.get_by_text("Flash Universal Studio",exact=True)).to_be_visible()
    page.get_by_role("button",name="Mesa de trabajo",exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    # Reconnaissance before behavior checks: actual rendered dialog and accessible editor.
    page.screenshot(path=str(OUTPUT/"ui-suite-controlled-editor-empty.png"))
    page.get_by_role("button",name=re.compile("Demo.run")).click()
    editor=page.get_by_role("textbox",name="Editor ActionScript de Demo.run")
    expect(editor).to_have_value("return 1;")
    editor.fill("return 2;")
    page.get_by_role("button",name="Aplicar y validar",exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Controlled compiler rejection")
    expect(editor).to_have_value("return 2;")
    page.get_by_role("button",name=re.compile("Demo.other")).click()
    expect(page.get_by_role("textbox",name="Editor ActionScript de Demo.other")).to_be_visible()
    page.get_by_role("button",name=re.compile("Demo.run")).click()
    expect(editor).to_have_value("return 2;")
    page.evaluate("window.__failEdit=false")
    page.get_by_role("button",name="Aplicar y validar",exact=True).click()
    expect(page.get_by_role("status").filter(has_text="Cambio validado")).to_be_visible()
    expect(page.get_by_role("button",name="Deshacer cambio aplicado")).to_be_enabled()
    assert page.evaluate("window.__suiteCalls.filter(c=>c.operation==='open').length")==1,"Rust refresh reopened the suite"
    page.get_by_role("button",name="Comparar motores",exact=True).click()
    expect(page.locator(".suite-comparison article")).to_have_count(2)
    page.screenshot(path=str(OUTPUT/"ui-suite-controlled-comparison.png"))
    page.get_by_role("button",name="Volver al editor",exact=True).click()
    page.get_by_role("button",name="Timeline",exact=True).click()
    page.get_by_role("button",name="Inspeccionar cuadro",exact=True).click()
    expect(page.get_by_role("img",name="Composición estática del cuadro seleccionado")).to_be_visible()
    assert page.evaluate("window.__injected") is None
    page.screenshot(path=str(OUTPUT/"ui-suite-controlled-timeline.png"))
    page.get_by_role("button",name="Recursos",exact=True).last.click()
    page.get_by_role("button",name=re.compile("Controlled image")).click()
    page.get_by_label("Imagen nueva",exact=True).fill("C:/controlled/replacement.png")
    page.get_by_role("button",name="Reemplazar y validar",exact=True).click()
    expect(page.get_by_role("status").filter(has_text="Recurso validado")).to_be_visible()
    page.get_by_role("button",name="Compilar",exact=True).click()
    page.screenshot(path=str(OUTPUT/"ui-suite-controlled-compile.png"))
    assert page.locator('option[value="haxeflixel"]').get_attribute("disabled") is not None
    page.get_by_label("Fuente o proyecto de entrada",exact=True).fill("C:/controlled/Main.as")
    page.get_by_label("Archivo SWF de salida",exact=True).fill("C:/controlled/compiled.swf")
    page.get_by_role("button",name="Compilar y validar",exact=True).click()
    expect(page.get_by_text("Controlled UI fixture; compiler not executed",exact=True)).to_be_visible()
    assert page.evaluate("window.__suiteCalls.filter(c=>c.operation==='open').length")==1
    page.get_by_role("button",name="Benchmarks",exact=True).click()
    page.get_by_label("Corpus local de SWF",exact=True).fill("C:/controlled/corpus")
    page.get_by_label("Carpeta de informes",exact=True).fill("C:/controlled/reports")
    page.get_by_role("button",name="Medir y generar informe",exact=True).click()
    expect(page.get_by_text("El informe no establece paridad semántica.",exact=True)).to_be_visible()
    page.screenshot(path=str(OUTPUT/"ui-suite-controlled-benchmark.png"))
    page.get_by_role("button",name="Herramientas",exact=True).click()
    expect(page.get_by_role("heading",name="Motores y herramientas")).to_be_visible()
    page.screenshot(path=str(OUTPUT/"ui-suite-controlled-tools.png"))
    page.get_by_role("button",name="Cerrar mesa de trabajo",exact=True).click()
    expect(page.get_by_role("dialog")).not_to_be_visible()
    assert not errors,errors
    evidence={"scope":"Controlled browser/Tauri contract UI only; no native worker/compiler/player executed","passed":True,"checks":["draft survives compiler rejection and unit switches","edit enables undo","Rust refresh does not reopen suite","independent engine comparison","SVG image preview without inline scripts","image replacement request and revision adoption","unavailable HaxeFlixel disabled","compile response updates working session","benchmark reports no unverified parity","tools visible","dialog focus/close"],"page_errors":errors,"calls":page.evaluate("window.__suiteCalls")}
    (OUTPUT/"ui-suite-controlled-contract.json").write_text(json.dumps(evidence,indent=2),encoding="utf-8")
    print(json.dumps({"passed":True,"scope":evidence["scope"],"checks":len(evidence["checks"]),"report":str(OUTPUT/"ui-suite-controlled-contract.json")}))
    browser.close()
