"""Browser contract checks of the single-window shell with a controlled host bridge.

Run with the engines/pyswf .venv (Playwright installed) while `npm run preview -- --port 4174` serves the
production build. The bridge mock replaces what ui/src/api.ts calls (window.flash, the Electron preload); no SWF is parsed and
no player or compiler runs here. Native worker, compiler and packaged-app checks are separate.
"""
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUTPUT = Path(__file__).resolve().parents[2] / "reports"
OUTPUT.mkdir(exist_ok=True)
URL = "http://127.0.0.1:4174"
MOCK = r"""
window.__calls = [];
window.__failEdit = true;
const sha = (c) => c.repeat(64);
let state = {filename:'demo.swf',working_path:'C:/controlled/demo.swf',source_path:'C:/controlled/demo.swf',revision:sha('a'),dirty:false,script_count:2,history:{undo:false,redo:false},signature:'FWS',frame_count:2,validation:{valid:true,errors:[],warnings:[],version:11,tag_count:6,frame_count:2}};
const node = (id, label, kind, extra={}) => ({id, label, kind, ...extra});
const tree = () => ({revision: state.revision, filename: 'demo.swf', node_count: 9, truncated: false, limit: 20000, nodes: [
  node('header','Cabecera','header'),
  node('folder:shapes','Formas','folder',{folder:'shapes',child_count:1,children:[node('tag:2','DefineShape (1)','tag',{tag_index:2,tag_type:2,tag_name:'DefineShape',character_id:1,bytes:90})]}),
  node('folder:images','Imágenes','folder',{folder:'images',child_count:1,children:[node('tag:3','DefineBitsLossless (2)','tag',{tag_index:3,tag_type:20,tag_name:'DefineBitsLossless',character_id:2,bytes:80,detail:'logo'})]}),
  node('folder:scripts','Scripts','folder',{folder:'scripts',detail:'1 clases · 2 métodos',child_count:1,children:[
    node('pkg:demo','demo','package',{child_count:1,children:[node('class:tag:0/class:0','Demo','class',{language:'as3',tag_index:0,document_id:'tag:0/class:0',detail:'demo',child_count:2,
      members:[{unit_id:'tag:0/method:1',name:'run',role:'method'},{unit_id:'tag:0/method:2',name:'other',role:'method'}]})]})]}),
]});
const decompile = (a) => {
  const engine = a.engine === 'auto' ? 'python' : a.engine;
  const body = a.unit_id.endsWith('1') ? 'return 1;' : 'return 2;';
  return {engine, source: engine === 'rust' ? null : body, pcode: 'pushbyte 1\nreturnvalue', diagnostics: [], editable: engine !== 'rust', revision: state.revision, unit_id: a.unit_id};
};
const engines = {default_engine:'auto',engines:[
  {id:'python',name:'PySWF',available:true,version:'controlled',category:'native',path:null,detail:'Controlled bridge fixture',operations:['decompile','edit','assemble','compile_project','compile_saved','resource-bundle','scripts','timeline','resources','resource_edit']},
  {id:'rust',name:'RustSWF',available:true,version:'controlled',category:'native',path:null,detail:'Controlled bridge fixture',operations:['decompile']},
  {id:'jpexs',name:'JPEXS',available:false,version:null,category:'decompiler',path:null,detail:'Not configured in fixture',operations:['decompile','edit','fla','xfl']},
  ...['haxe','haxeflixel','flex','mtasc'].map(id=>({id,name:id,available:false,version:null,category:'compiler',path:null,detail:'Not configured in fixture',operations:['compile_project']})),
]};
const benchRuns = [{run_id:'run-3',mode:'smoke',cases:6,seconds:900,created_utc:'2026-10-25T01:00:00Z',categories:['decompilers','robustness','unit_fidelity','compilers','extractors']},
  {run_id:'run-2',mode:'smoke',cases:6,seconds:20,created_utc:'2026-10-24T01:00:00Z',categories:['decompilers','compilers','extractors']},{run_id:'run-1',mode:'smoke',cases:6}];
// A run written before schema 2: no times, no `comparable`, no Indice Flash.
const benchOld = {runs:benchRuns,folder:'C:/controlled/bench',results:{run_id:'run-1',mode:'smoke',created_utc:'2026-10-23T00:00:00Z',seconds:12,corpus:'controlled',cases:6,tables:{
  decompilers:{unit:'casos que recompilan',winner:null,note:'Sin ganador: la diferencia es de un caso.',engines:[
    {engine:'native',name:'PySWF',cases:6,source:4,recompiles:4,same_trace:4,jpexs_closeness:0.874,jpexs_coverage:0.64,reasons:[]},
    {engine:'rust',name:'RustSWF',cases:6,source:1,recompiles:1,same_trace:1,reasons:['no lee AS1/AS2']},
    {engine:'jpexs',name:'JPEXS',cases:6,source:5,recompiles:5,same_trace:5,jpexs_closeness:1,jpexs_coverage:1,reference:true,reasons:[]},
    {engine:'go',name:'GoSWF',cases:6,source:2,recompiles:2,same_trace:2,jpexs_closeness:null,jpexs_coverage:null,reasons:[]}]}}}};
// Schema 2: PySWF, GoSWF and JPEXS (which competes like the others and is only the reference of the closeness) are ranked together;
// Flare read only 2 of the 6 cases; pyflashkit is not installed.
const benchTwo = {runs:benchRuns,folder:'C:/controlled/bench',results:{schema_version:2,run_id:'run-2',mode:'smoke',created_utc:'2026-10-24T01:00:00Z',seconds:20,corpus:'controlled',cases:6,
  oracle:{runtime:'ruffle-tests.exe',runtime_sha256:'ab'.repeat(32)},tables:{
  decompilers:{unit:'casos que recompilan',winner:null,note:'sin ganador claro: la ventaja es de 1 caso(s) o menos',corpus_cases:6,engines:[
    {engine:'native',name:'PySWF',cases:6,source:4,recompiles:4,same_trace:4,units_total:20,units_with_source:16,unit_coverage:0.8,jpexs_closeness:0.8,jpexs_coverage:0.6,median_seconds:0.15,timed_cases:6,comparable:true,flash_index:70,reasons:[]},
    {engine:'jpexs',name:'JPEXS',cases:6,source:5,recompiles:5,same_trace:5,units_total:20,units_with_source:18,unit_coverage:0.9,jpexs_closeness:1,jpexs_coverage:1,reference:true,median_seconds:2,timed_cases:6,comparable:true,flash_index:85,reasons:[]},
    {engine:'go',name:'GoSWF',cases:6,source:2,recompiles:2,same_trace:2,units_total:20,units_with_source:8,unit_coverage:0.4,jpexs_closeness:0.9,jpexs_coverage:0.3,median_seconds:0.07,timed_cases:6,comparable:true,flash_index:35,reasons:['faltan clases']},
    {engine:'flare',name:'Flare 0.6',cases:2,source:2,recompiles:2,same_trace:2,units_total:4,units_with_source:4,unit_coverage:1,jpexs_closeness:0.99,jpexs_coverage:1,median_seconds:0.13,timed_cases:2,comparable:false,flash_index:100,reasons:['no lee AS3']},
    {engine:'pyflashkit',name:'pyflashkit',cases:0,source:0,recompiles:0,same_trace:0,units_total:null,units_with_source:null,unit_coverage:null,jpexs_closeness:null,jpexs_coverage:null,median_seconds:null,timed_cases:0,comparable:false,flash_index:null,reasons:['no instalado']}]},
  compilers:{unit:'proyectos que compilan',winner:null,note:'sin ganador claro: la ventaja es de 1 caso(s) o menos',corpus_cases:3,engines:[
    {engine:'flex',name:'flex',cases:3,ok:3,median_seconds:2,timed_cases:3,comparable:true,details:[],reasons:[]},
    {engine:'native',name:'PySWF',cases:3,ok:3,median_seconds:0.02,timed_cases:3,comparable:true,details:[],reasons:[]}]},
  extractors:{unit:'SWF cuyos recursos se extraen todos',winner:null,note:'un solo motor medido: no hay con que compararlo',corpus_cases:1,engines:[
    {engine:'swfextract',name:'swfextract',cases:1,ok:1,median_seconds:2.3,timed_cases:1,comparable:false,details:[],reasons:[]}]}}}};
// Schema 3: the same decompilers plus the robustness and per-unit fidelity tables, whose figures join the index.
const outcomes = (r,p,c,k,h) => ({recovered:r,partial:p,clean_error:c,crash:k,hang:h});
const damage = {truncate_header:'Truncado en la cabecera',truncate_tag:'Truncado en un tag',truncate_code:'Truncado en el código',bad_length:'Longitud de archivo errónea',bad_compression:'Flujo zlib roto',bad_tag_length:'Longitud de tag errónea',flip_code:'Bytes alterados en el código',flip_noncode:'Bytes alterados fuera del código'};
const perKind = (scores) => Object.fromEntries(Object.keys(damage).map((kind, i) => [kind, {cases:6, outcomes:outcomes(1,2,2,1,0), score:scores[i]}]));
const counts = (same, different, no_compile, not_run) => ({same, different, no_compile, not_run, evaluated: same + different + no_compile + not_run, judged: same + different + no_compile, fidelity: same + different + no_compile ? same / (same + different + no_compile) : null});
const unitKinds = (c, m, a) => ({class: counts(...c), method: counts(...m), action: counts(...a)});
const decompilers3 = benchTwo.results.tables.decompilers.engines.map(line => ({...line,
  unit_fidelity: {native:0.74, jpexs:0.33, go:0.6}[line.engine] ?? null, unit_fidelity_judged: {native:23, jpexs:12, go:15}[line.engine] ?? null, unit_fidelity_cases: line.cases ? 6 : 0,
  robustness: {native:54.9, jpexs:52.9, go:58.8}[line.engine] ?? null, robustness_cases: {native:51, jpexs:51, go:51, flare:12}[line.engine] ?? 0}));
const benchNew = {runs:benchRuns,folder:'C:/controlled/bench',results:{...benchTwo.results,schema_version:3,run_id:'run-3',created_utc:'2026-10-25T01:00:00Z',seconds:900,
  options:{unit_cap:20,robustness_timeout:30,workers:4},tables:{
  ...benchTwo.results.tables,
  decompilers:{...benchTwo.results.tables.decompilers,index_components:['source','recompiles','same_trace','units','unit_fidelity','robustness'],engines:decompilers3},
  robustness:{unit:'archivos dañados en que el motor recupera lo intacto y avisa del daño (nota de 0 a 100)',winner:null,note:'sin ganador claro: la ventaja es de 1 caso(s) o menos',corpus_cases:51,kinds:damage,engines:[
    {engine:'go',name:'GoSWF',cases:51,outcomes:outcomes(12,12,27,0,0),robustness:58.8,by_kind:perKind([40,70,50,80,40,60,60,60]),recoverable_cases:26,intact_units:60,recovered_units:21,unit_recovery:0.34,median_seconds:0.05,timed_cases:51,peak_memory_mib:40,comparable:true,reasons:[]},
    {engine:'native',name:'PySWF',cases:51,outcomes:outcomes(14,10,20,7,0),robustness:54.9,by_kind:perKind([40,70,50,0,0,60,60,60]),recoverable_cases:26,intact_units:60,recovered_units:33,unit_recovery:0.55,median_seconds:0.06,timed_cases:51,peak_memory_mib:134,comparable:true,reasons:[]},
    {engine:'jpexs',name:'JPEXS',cases:51,outcomes:outcomes(2,27,22,0,0),robustness:52.9,by_kind:perKind([40,60,60,60,40,60,60,60]),recoverable_cases:26,intact_units:60,recovered_units:40,unit_recovery:0.66,median_seconds:2.1,timed_cases:51,peak_memory_mib:239,comparable:true,reasons:[]},
    {engine:'flare',name:'Flare 0.6',cases:12,outcomes:outcomes(1,5,4,2,0),robustness:40,by_kind:perKind([0,40,40,40,40,40,40,40]),recoverable_cases:5,intact_units:5,recovered_units:2,unit_recovery:0.4,median_seconds:0.4,timed_cases:12,peak_memory_mib:60,comparable:false,reasons:['no lee AS3']},
    {engine:'pyflashkit',name:'pyflashkit',cases:0,outcomes:outcomes(0,0,0,0,0),robustness:null,by_kind:{},median_seconds:null,timed_cases:0,comparable:false,reasons:['no instalado']}]},
  unit_fidelity:{unit:'unidades cuyo fuente, compilado solo y empalmado en el SWF original, imprime la misma traza',winner:'native',note:null,corpus_cases:6,unit_cap:20,kinds:{class:'Clases',method:'Métodos',action:'Acciones (AS2)'},
    out_of_scope:{units:66,reasons:{'espacios de nombres privados o explícitos':60,'cierres con activación':6}},engines:[
    {engine:'native',name:'PySWF',cases:6,...counts(17,0,6,13),by_kind:unitKinds([6,0,2,3],[10,0,4,10],[1,0,0,0]),comparable:true,reasons:[]},
    {engine:'go',name:'GoSWF',cases:6,...counts(9,0,6,9),by_kind:unitKinds([0,0,0,0],[8,0,6,9],[1,0,0,0]),comparable:true,reasons:[]},
    {engine:'jpexs',name:'JPEXS',cases:6,...counts(4,0,8,4),by_kind:unitKinds([1,0,3,1],[2,0,5,3],[1,0,0,0]),comparable:true,reasons:['no emitió fuente verificable de ninguna unidad evaluable']},
    {engine:'flare',name:'Flare 0.6',cases:2,...counts(2,0,0,0),by_kind:unitKinds([0,0,0,0],[0,0,0,0],[2,0,0,0]),comparable:false,reasons:['no lee AS3']}]}},
  rows:{robustness:[
    ...['truncate-header-a','flip-code-b'].flatMap((id, i) => ['native','go','jpexs'].map((engine, j) => ({engine,family:'f',case:id,kind:i ? 'flip_code' : 'truncate_header',reason:'motivo '+engine,measured:true,outcome:['clean_error','recovered','partial'][(i + j) % 3],seconds:0.1,intact_units:i ? 2 : 0,recovered_units:0})))],
    unit_fidelity:[{engine:'native',family:'f',case:'swf/f/release.fws.swf',measured:true,units:{same:1,different:0,no_compile:1,not_run:0},details:[{unit:'tag:6/method:5',kind:'method',outcome:'no_compile',reason:'SyntaxError: Compound member assignments'}],reason:''}]}}};
const bridge = {
 invoke:async(command,args={})=>{
  if(command==='startup')return {path:'C:/controlled/demo.swf',smoke:false};
  if(command==='ui_verified')return;
  if(command==='suite_cancel')return;
  if(command==='suite_pick')return {swf:'C:/controlled/demo.swf',output:'C:/controlled/out.swf',project_output:'C:/controlled/p.pyswfproj',source:'C:/controlled/Main.as'}[args.kind]??null;
  if(command!=='suite_rpc')throw new Error('Unexpected bridge command: '+command);
  const a = args.args ?? {};
  window.__calls.push({operation:args.operation,args:a});
  switch(args.operation){
   case 'engines': case 'configure_engine': return engines;
   case 'open': return {...state};
   case 'state': return {...state};
   case 'tag_tree': return tree();
   case 'header': return {filename:'demo.swf',signature:'FWS',compression:'none',version:11,file_length:200,file_size:200,frame_size:{xmin:0,xmax:12800,ymin:0,ymax:9600,width:640,height:480},frame_rate:24,frame_count:2,shown_frames:2,tag_count:6,abc_blocks:1,has_as3:true,has_metadata:false,file_attributes:null,metadata:null,sha256:state.revision,revision:state.revision,script_count:2};
   case 'decompile': return decompile(a);
   case 'compare': return {results:[decompile({...a,engine:'python'}),decompile({...a,engine:'rust'})],parity:false,superiority:false};
   case 'edit': if(window.__failEdit)throw new Error('Controlled compiler rejection'); state={...state,revision:sha('b'),dirty:true,history:{undo:true,redo:false}}; return {...state};
   case 'undo': state={...state,revision:sha('a'),dirty:false,history:{undo:false,redo:true}}; return {...state};
   case 'redo': state={...state,revision:sha('b'),dirty:true,history:{undo:true,redo:false}}; return {...state};
   case 'validate': return {valid:true,errors:[],warnings:[]};
   case 'raw': return {scope:'tag',name:'DefineBitsLossless',tag_index:a.tag_index,type:20,offset:a.offset??0,limit:a.limit??4096,total:20,length:20,encoding:'hex',hex:'0200050400040000789c63f8cfc000000401000100',revision:state.revision};
   case 'resource_preview': return a.tag_index===2
     ? {kind:'shape',mime:'image/svg+xml',encoding:'text',data:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48" viewBox="0 0 64 48"><rect width="64" height="48" fill="#133c35"/><circle cx="32" cy="24" r="14" fill="#79dbb6"/><script>window.__injected=1</script><foreignObject><div>unsafe</div></foreignObject></svg>',tag_index:2,name:'DefineShape',revision:state.revision}
     : {kind:'image',mime:'image/png',encoding:'base64',data:'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAFklEQVR4nGP8z8Dwn4EIwESMolGFxQEAXm0CBPDfEmQAAAAASUVORK5CYII=',width:4,height:4,tag_index:3,name:'DefineBitsLossless',revision:state.revision};
   case 'resource_info': return {kind:'image',parsed:{character_id:2,bytes:80,type:20},revision:state.revision,coordinate_units:'pixels',tag_index:a.tag_index};
   case 'timeline': return {frame:0,frame_count:2,display_list:[],svg:null,actionscript_executed:false};
   case 'compile_project': state={...state,filename:'compiled.swf',working_path:a.output,revision:sha('d'),dirty:false}; return {...state,path:a.output,engine:'python',validation:{valid:true,errors:[],warnings:[]},log:'Controlled UI fixture; compiler not executed'};
   case 'export': return {path:a.path,engine:'python',diagnostics:[]};
   case 'bench_results': return a.run==='run-1'?benchOld:a.run==='run-2'?benchTwo:benchNew;
   case 'benchmark_tools': return {engines:[{id:'native',name:'PySWF',category:'decompiler',configured:true,operations:['source']},{id:'flare',name:'Flare',category:'decompiler',configured:false,operations:['source'],setup_diagnostic:'sin configurar en el fixture'}]};
   default: throw new Error('Unexpected suite operation: '+args.operation);
  }
 }
};
// window.flash as the preload exposes it: call() resolves with the envelope, failures as {ok:false,error}.
window.flash = {
 call: async (op, args = {}) => {
  try { return {ok:true, op, result: await bridge.invoke('suite_rpc', {operation:op, args})}; }
  catch (error) { return {ok:false, error:{message:String(error && error.message || error), kind:'error'}}; }
 },
 cancel: () => bridge.invoke('suite_cancel'),
 openDialog: (kind) => bridge.invoke('suite_pick', {kind}),
 startup: () => bridge.invoke('startup'),
 reportVerification: (evidence) => bridge.invoke('ui_verified', {evidence:{verification:evidence}}),
 onFileDrop: () => () => undefined,
};
"""


def item(page, label):
    """A tree row by its exact label (names like "Demo" also occur inside "demo" packages)."""
    return page.locator("[role=treeitem]").filter(has=page.locator(".tree-label", has_text=re.compile("^" + re.escape(label) + "$")))


def opens(page):
    return page.evaluate("window.__calls.filter(c=>c.operation==='open').length")


def open_table(dialog):
    """The full table of the first category is behind a toggle: open it unless it already is."""
    details = dialog.locator("details.bench-details").first
    if not details.evaluate("element => element.open"):
        details.locator("summary").click()


def wait_text(page, selector):
    page.wait_for_function("sel => (document.querySelector(sel + ' .cm-content')?.textContent || '').trim().length > 0", arg=selector)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(MOCK)
    page.goto(URL, wait_until="networkidle")
    tree = page.get_by_role("tree", name="Elementos del SWF")
    expect(item(page, "Cabecera")).to_be_visible()
    expect(page.get_by_text("Longitud declarada")).to_be_visible()
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-header.png"))

    # Keyboard model of the tree: arrows move and open, Home/End jump.
    tree.focus()
    page.keyboard.press("End")
    expect(page.locator("[role=treeitem][aria-selected=true]")).to_contain_text("Demo")
    page.keyboard.press("Home")
    expect(page.locator("[role=treeitem][aria-selected=true]")).to_contain_text("Cabecera")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowRight")
    expect(item(page, "DefineShape (1)")).to_be_visible()
    page.keyboard.press("ArrowLeft")
    expect(item(page, "DefineShape (1)")).to_have_count(0)

    # Quick filter finds a class by one of its members, and Escape restores the tree.
    page.get_by_label("Filtrar el árbol").fill("other")
    expect(item(page, "Demo")).to_be_visible()
    expect(item(page, "Imágenes")).to_have_count(0)
    page.get_by_label("Filtrar el árbol").press("Escape")
    expect(item(page, "Imágenes")).to_be_visible()

    # Script: class view, then one method; a rejected edit keeps the draft across selections.
    item(page, "Demo").click()
    wait_text(page, "[data-testid=script-source]")
    wait_text(page, "[data-testid=script-pcode]")
    expect(page.locator("[data-testid=script-source] .cm-content")).to_contain_text("return 1;")
    page.get_by_role("button", name="run", exact=False).first.click()
    editor = page.locator("[data-testid=script-source] .cm-content")
    expect(editor).to_have_text("return 1;")  # wait until the member itself is loaded and editable
    expect(page.get_by_test_id("script-status")).to_have_text("Listo para editar")
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("return 2;")
    expect(page.get_by_test_id("script-status")).to_contain_text("Borrador")
    page.get_by_role("button", name="Aplicar y validar").click()
    expect(page.locator(".toast.error")).to_contain_text("Controlled compiler rejection")
    expect(editor).to_contain_text("return 2;")
    page.get_by_role("button", name="other", exact=False).first.click()
    expect(page.locator("[data-testid=script-source] .cm-content")).to_contain_text("return 2;")
    page.get_by_role("button", name="run", exact=False).first.click()
    expect(page.locator("[data-testid=script-source] .cm-content")).to_contain_text("return 2;")
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-script.png"))
    page.evaluate("window.__failEdit=false")
    page.get_by_role("button", name="Aplicar y validar").click()
    expect(page.locator(".toast.ok")).to_contain_text("Cambio validado")
    expect(page.get_by_role("button", name="Deshacer").first).to_be_enabled()
    assert opens(page) == 1, "Editing reopened the document"

    # Engine comparison dialog: each engine keeps its own output.
    page.get_by_role("button", name="Comparar motores").click()
    expect(page.locator(".compare-card")).to_have_count(2)
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-compare.png"))
    page.keyboard.press("Escape")

    # Preview: vector SVG is shown without running its script; image previews load.
    item(page, "Formas").click()
    page.keyboard.press("ArrowRight")
    item(page, "DefineShape (1)").click()
    page.wait_for_function("() => { const i = document.querySelector('.preview-surface img'); return i && i.complete && i.naturalWidth > 0; }")
    assert page.evaluate("window.__injected") is None
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-shape.png"))
    item(page, "Imágenes").click()
    page.keyboard.press("ArrowRight")
    item(page, "DefineBitsLossless (2)").click()
    page.wait_for_function("() => { const i = document.querySelector('.preview-surface img'); return i && i.complete && i.naturalWidth > 0; }")
    page.get_by_role("tab", name="Hex").click()
    expect(page.get_by_test_id("hex-dump")).to_contain_text("00000000")
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-hex.png"))

    # Dialogs: compile (unavailable profile disabled), benchmarks columns, engines.
    page.get_by_role("menuitem", name="Herramientas").click()
    page.get_by_role("menuitem", name="Compilar…").click()
    assert page.locator('option[value="haxeflixel"]').get_attribute("disabled") is not None
    page.get_by_label("Fuente o proyecto de entrada", exact=True).fill("C:/controlled/Main.as")
    page.get_by_label("Archivo SWF de salida", exact=True).fill("C:/controlled/compiled.swf")
    page.get_by_role("button", name="Compilar y validar").click()
    expect(page.get_by_text("Controlled UI fixture; compiler not executed")).to_be_visible()
    assert opens(page) == 1
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-compile.png"))
    page.keyboard.press("Escape")

    page.get_by_role("menuitem", name="Herramientas").click()
    page.get_by_role("menuitem", name="Benchmarks").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", name="Clasificación de motores")).to_be_visible()
    # Highlights: only engines measured on the same cases compete. JPEXS competes too, except for being the closest to itself.
    cards = dialog.get_by_label("Destacados").locator(".bench-stat")
    expect(cards.nth(0)).to_contain_text("Mejor Índice Flash")
    expect(cards.nth(0)).to_contain_text("85,0")
    expect(cards.nth(0)).to_contain_text("JPEXS")
    expect(cards.nth(0)).to_contain_text("15,0 puntos sobre PySWF")
    expect(cards.nth(1)).to_contain_text("90 %")
    expect(cards.nth(1)).to_contain_text("GoSWF")
    expect(cards.nth(2)).to_contain_text("0,070 s")
    expect(cards.nth(3)).to_contain_text("5 de 6")
    expect(cards.nth(3)).to_contain_text("Ventaja de 1 caso sobre PySWF: dentro del ruido")
    # Schema 3 adds two cards: the most robust against damaged files and the best per-unit fidelity.
    expect(cards).to_have_count(6)
    expect(cards.nth(4)).to_contain_text("Más robusto ante archivos dañados")
    expect(cards.nth(4)).to_contain_text("58,8")
    expect(cards.nth(4)).to_contain_text("GoSWF")
    expect(cards.nth(5)).to_contain_text("Mayor fidelidad por unidad")
    expect(cards.nth(5)).to_contain_text("74 %")
    expect(cards.nth(5)).to_contain_text("PySWF")
    # The page says which terms this run's index averages.
    expect(dialog.get_by_test_id("index-terms")).to_contain_text("Esta corrida incluye: fuente, recompila, misma traza, unidades con fuente, fidelidad por unidad, robustez.")
    # The Índice Flash chart: sorted with JPEXS in it, the engine measured on other cases apart and marked.
    index_marks = dialog.locator('svg:has(> title:text-is("Índice Flash por motor")) [data-mark]')
    assert [mark.get_attribute("data-engine") for mark in index_marks.all()] == ["jpexs", "native", "go", "flare"]
    assert "muted" in index_marks.nth(3).get_attribute("class")
    expect(index_marks.nth(0)).to_have_attribute("aria-label", re.compile(r"^JPEXS: 85,0\."))
    expect(index_marks.nth(1)).to_have_attribute("aria-label", re.compile(r"fuente 67\s%\s·\srecompila 67\s%\s·\smisma traza 67\s%\s·\sunidades 80\s%\s·\sfidelidad por unidad 74\s%\s·\srobustez 55\s%"))
    expect(dialog.get_by_text("Medidos sobre otros casos").first).to_be_visible()
    # Closeness and coverage: JPEXS is the reference bar (100 % by definition, hatched, not ranked), nobody else is above it.
    close_marks = dialog.locator('svg:has(> title:text-is("Cercanía a JPEXS por motor")) [data-mark]')
    assert [mark.get_attribute("data-engine") for mark in close_marks.all()] == ["jpexs", "go", "native", "flare"]
    expect(close_marks.nth(0)).to_have_attribute("aria-label", re.compile(r"^JPEXS: 100,0\s%\. .*Referencia"))
    assert dialog.locator('svg:has(> title:text-is("Cercanía a JPEXS por motor")) path.bc-bar.ref').count() == 1
    assert dialog.locator('svg:has(> title:text-is("Misma traza por motor")) path.bc-bar.ref').count() == 0, "elsewhere JPEXS is a plain bar in its own colour"
    # Hover shows the exact value and the number of cases, focus shows the same, arrows move between bars.
    index_marks.nth(1).hover()
    expect(dialog.locator(".bench-tip")).to_contain_text("70,0")
    expect(dialog.locator(".bench-tip")).to_contain_text("6 casos")
    index_marks.nth(0).focus()
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.dataset.engine") == "native"
    # Speed is drawn for every engine of the table; a category with one engine is a tile, not a one-bar chart.
    expect(dialog.locator('svg:has(> title:text-is("Velocidad por motor")) [data-mark]').first).to_be_visible()
    expect(dialog.get_by_text("Un solo motor medido").first).to_be_visible()
    expect(dialog.locator(".bench-unmeasured").first).to_contain_text("pyflashkit (no instalado)")
    # The table is behind a toggle; opened, it carries the same numbers, the closeness columns and the times.
    open_table(dialog)
    expect(page.get_by_role("columnheader", name="% cerca de JPEXS")).to_be_visible()
    expect(page.get_by_role("columnheader", name="% de unidades de JPEXS")).to_be_visible()
    expect(page.get_by_test_id("index-native")).to_have_text("70,0")
    expect(page.get_by_test_id("index-jpexs")).to_have_text("85,0")
    expect(page.get_by_test_id("units-native")).to_have_text("80%")
    expect(page.get_by_test_id("seconds-go")).to_have_text("0,070 s")
    expect(page.get_by_test_id("closeness-native")).to_have_text("80%")
    # Robustness: a stacked bar per engine (best score first), the score bars, the files recovered and one chart per kind of damage.
    expect(dialog.get_by_role("heading", name="Robustez", exact=True)).to_be_visible()
    stacked = dialog.locator('svg:has(> title:text-is("Desenlace de cada archivo dañado, por motor")) [data-mark]')
    assert [mark.get_attribute("data-engine") for mark in stacked.all()] == ["go", "native", "jpexs", "flare"]
    assert "muted" in stacked.nth(3).get_attribute("class")
    expect(stacked.nth(1)).to_have_attribute("aria-label", re.compile(r"PySWF: recuperó 14, parcial 10, error claro 20, caída 7, cuelgue 0\. 51 archivos dañados"))
    assert dialog.locator('svg:has(> title:text-is("Desenlace de cada archivo dañado, por motor")) rect.sb-seg.seg-crash').count() == 1, "only PySWF crashed"
    stacked.nth(1).hover()
    expect(dialog.locator(".bench-tip")).to_contain_text("Robustez 54,9")
    expect(dialog.locator(".bench-tip")).to_contain_text("caída: 7 (14 %)")
    robust_marks = dialog.locator('svg:has(> title:text-is("Robustez por motor")) [data-mark]')
    assert [mark.get_attribute("data-engine") for mark in robust_marks.all()] == ["go", "native", "jpexs", "flare"]
    expect(dialog.locator('svg:has(> title:text-is("Unidades recuperadas por motor")) [data-mark]').first).to_be_visible()
    for kind_title in ("Truncado en la cabecera", "Flujo zlib roto", "Bytes alterados fuera del código"):
        expect(dialog.locator('svg:has(> title:text-is("%s por motor")) [data-mark]' % kind_title).first).to_be_visible()
    expect(dialog.get_by_role("heading", name="Por tipo de daño")).to_be_visible()
    expect(dialog.locator(".stack-legend").first).to_contain_text("Recuperó lo intacto y avisó del daño")
    # Per-unit fidelity: the bars, the outcome of every unit, and classes against methods.
    expect(dialog.get_by_role("heading", name="Fidelidad por unidad", exact=True)).to_be_visible()
    fidelity_marks = dialog.locator('svg:has(> title:text-is("Fidelidad por unidad por motor")) [data-mark]')
    assert [mark.get_attribute("data-engine") for mark in fidelity_marks.all()] == ["native", "go", "jpexs", "flare"]
    expect(fidelity_marks.nth(0)).to_have_attribute("aria-label", re.compile(r"^PySWF: 73,9\s%\. .*23 unidades juzgadas"))
    expect(dialog.locator('svg:has(> title:text-is("Desenlace de cada unidad, por motor")) [data-mark]').first).to_be_visible()
    expect(dialog.locator('svg:has(> title:text-is("Clases y acciones por motor")) [data-mark]').first).to_be_visible()
    expect(dialog.locator('svg:has(> title:text-is("Métodos por motor")) [data-mark]').first).to_be_visible()
    expect(dialog.get_by_role("heading", name="Clases frente a métodos")).to_be_visible()
    expect(dialog.get_by_text("Fuera de alcance:")).to_be_visible()
    # The tables of the two sections are behind their own toggles.
    for summary in dialog.locator("details.bench-details > summary").all():
        if "Tabla completa" in summary.inner_text() and not summary.evaluate("element => element.parentElement.open"):
            summary.click()
    expect(page.get_by_test_id("robustness-native").first).to_have_text("54,9")
    expect(page.get_by_test_id("crash-native")).to_have_text("7")
    expect(page.get_by_test_id("recovery-native")).to_have_text("55%")
    expect(page.get_by_test_id("fidelity-native")).to_have_text("74%")
    expect(page.get_by_test_id("unit-fidelity-native")).to_have_text("74%")
    expect(page.get_by_role("columnheader", name="Fidelidad por unidad").first).to_be_visible()
    expect(dialog.get_by_text("Desenlace de cada archivo dañado ·")).to_be_visible()
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-benchmarks.png"))

    # A run written with schema 2 still reads: four terms in the index, and the two new sections say they have no data.
    dialog.get_by_role("combobox", name="Ejecución").select_option("run-2")
    expect(dialog.get_by_test_id("index-terms")).to_contain_text("Esta corrida incluye: fuente, recompila, misma traza, unidades con fuente.")
    expect(dialog.get_by_test_id("index-terms")).to_contain_text("No incluye: fidelidad por unidad, robustez")
    expect(dialog.get_by_role("heading", name="Robustez", exact=True)).to_be_visible()
    expect(dialog.get_by_text("Sin datos en esta corrida. La corrida es anterior a la medición de robustez")).to_be_visible()
    expect(dialog.get_by_text("Sin datos en esta corrida. La corrida es anterior a la medición de fidelidad por unidad")).to_be_visible()
    expect(dialog.locator('svg:has(> title:text-is("Índice Flash por motor")) [data-mark]').first).to_be_visible()
    expect(dialog.get_by_label("Destacados").locator(".bench-stat").nth(4)).to_contain_text("Sin datos en esta corrida")
    expect(dialog.get_by_label("Destacados").locator(".bench-stat").nth(5)).to_contain_text("Sin datos en esta corrida")

    # A run written before schema 2 still reads: no times and no index, said instead of drawn.
    dialog.get_by_role("combobox", name="Ejecución").select_option("run-1")
    expect(dialog.get_by_text("Sin datos en esta corrida").first).to_be_visible()
    expect(dialog.locator('svg:has(> title:text-is("Velocidad por motor"))')).to_have_count(0)
    open_table(dialog)
    expect(page.get_by_role("columnheader", name="% cerca de JPEXS")).to_be_visible()
    expect(page.get_by_role("columnheader", name="% de unidades de JPEXS")).to_be_visible()
    expect(page.get_by_test_id("closeness-native")).to_have_text("87%")
    expect(page.get_by_test_id("coverage-native")).to_have_text("64%")
    expect(page.get_by_test_id("closeness-rust")).to_have_text("—")
    expect(page.get_by_test_id("coverage-go")).to_have_text("—")
    expect(page.get_by_test_id("closeness-jpexs")).to_have_text("100%")
    expect(page.get_by_test_id("index-native")).to_have_text("—")
    expect(page.get_by_test_id("units-native")).to_have_text("—")
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-benchmarks-old-run.png"))
    page.keyboard.press("Escape")

    page.get_by_role("menuitem", name="Herramientas").click()
    page.get_by_role("menuitem", name="Motores…").click()
    expect(page.get_by_role("heading", name="Motores y herramientas")).to_be_visible()
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-engines.png"))
    page.keyboard.press("Escape")

    # Theme and splitter persistence.
    page.get_by_role("menuitem", name="Ver").click()
    page.get_by_role("menuitemradio", name="Tema claro").click()
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    page.screenshot(path=str(OUTPUT / "ui-shell-controlled-light.png"))
    box = page.get_by_role("separator", name="Ancho del árbol").bounding_box()
    page.mouse.move(box["x"] + 2, box["y"] + 100)
    page.mouse.down()
    page.mouse.move(box["x"] + 102, box["y"] + 100, steps=5)
    page.mouse.up()
    assert int(page.evaluate("localStorage.getItem('flash-studio.tree-width')")) > 380

    assert not errors, errors
    evidence = {
        "scope": "Controlled browser/host-bridge contract of the single-window shell; no native worker, compiler or player executed",
        "passed": True,
        "checks": ["tree keyboard model and quick filter", "class view with source and P-code", "draft survives a rejected edit and member switches",
                   "edit enables undo without reopening the document", "engine comparison dialog", "SVG preview without inline scripts",
                   "hex view pages through raw", "unavailable HaxeFlixel profile disabled", "compile adopts the new document",
                   "benchmark page: highlights, sorted Índice Flash, tooltip, keyboard, table toggle, engines on other cases apart, old run without data",
                   "theme switch and remembered tree width"],
        "page_errors": errors, "calls": page.evaluate("window.__calls"),
    }
    (OUTPUT / "ui-shell-controlled-contract.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "scope": evidence["scope"], "checks": len(evidence["checks"]), "report": str(OUTPUT / "ui-shell-controlled-contract.json")}))
    browser.close()
