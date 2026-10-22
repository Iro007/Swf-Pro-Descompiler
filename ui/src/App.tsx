import {useCallback,useEffect,useRef,useState} from 'react';
import {FolderOpen,Code2,Layers,FileCode2,Search,ArrowDownToLine,ChevronRight,AlertTriangle,Loader2,X,Info,Box,Save} from 'lucide-react';
import {getCurrentWebview} from '@tauri-apps/api/webview';
import {listen} from '@tauri-apps/api/event';
import * as api from './api';
import {Asset,ClassOutline,Diagnostic,OpenResult,Page,Reconstruction,Resource,Script} from './types';
import PreviewSurface from './components/PreviewSurface';
import Dialog from './components/Dialog';
import SuiteWorkbench, {SuiteView} from './components/SuiteWorkbench';
import './classic.css';

const empty=<T,>():Page<T>=>({items:[],total:0,offset:0});
const size=(n:number)=>n<1024?`${n} B`:n<1048576?`${(n/1024).toFixed(1)} KiB`:`${(n/1048576).toFixed(1)} MiB`;
const errorText=(e:unknown)=>e instanceof Error?e.message:String(e);
const categories=[['all','Todos'],['images','Imágenes'],['shapes','Formas'],['sounds','Audio'],['texts','Texto y fuentes'],['sprites','Sprites'],['buttons','Botones'],['frames','Timeline'],['other','Otros']];
const workspacePanels:{id:SuiteView;label:string}[]=[{id:'editor',label:'Editar código'},{id:'resources',label:'Editar recursos'},{id:'projects',label:'Proyecto y guardado'},{id:'compile',label:'Compilar SWF'},{id:'export',label:'Exportar'},{id:'timeline',label:'Cuadros'},{id:'preview',label:'Reproducir con Ruffle'},{id:'benchmarks',label:'Benchmarks'},{id:'tools',label:'Configurar motores'}];
function explorerGroups(items:(Resource|Script)[]) {
  const groups=new Map<string,(Resource|Script)[]>();
  for(const item of items){
    const split=item.name.lastIndexOf('.');
    const group='language'in item?(split>0?item.name.slice(0,split):item.language==='avm1'?'ActionScript 2':'Scripts e inicializadores'):(categories.find(([id])=>id===item.category)?.[1]??'Otros');
    const list=groups.get(group)??[];list.push(item);groups.set(group,list);
  }
  return Array.from(groups,([name,items])=>({name,items}));
}
const codes:Record<string,number[]>={images:[6,20,21,35,36,90],shapes:[2,22,32,83,46,84],sounds:[14,15,18,19,45,89],texts:[10,11,13,33,37,48,62,73,74,75,88,91],sprites:[39],buttons:[7,17,23,34],frames:[1,4,5,26,28,43,70,86,94]};
function Diagnostics({items}:{items:Diagnostic[]}){return items.length?<div className="diagnostics">{items.map((d,i)=><div key={`${d.code}-${i}`}><AlertTriangle size={14}/><span><strong>{d.code}</strong> · {d.message}{d.offset!==null?` · offset ${d.offset}`:''}</span></div>)}</div>:null;}

export default function App(){
  const [document,setDocument]=useState<OpenResult|null>(null);
  const [resources,setResources]=useState<Page<Resource>>(empty);
  const [scripts,setScripts]=useState<Page<Script>>(empty);
  const [scope,setScope]=useState('resources');const [category,setCategory]=useState('all');const [query,setQuery]=useState('');
  const [selected,setSelected]=useState<Resource|Script|null>(null);const [kind,setKind]=useState('resource');
  const [tab,setTab]=useState('preview');const [source,setSource]=useState<Reconstruction|null>(null);
  const [asset,setAsset]=useState<Asset|null>(null);const [metadata,setMetadata]=useState<unknown>(null);
  const [hex,setHex]=useState<{text:string;total:number;offset:number}|null>(null);
  const [busy,setBusy]=useState('');const [suiteBusy,setSuiteBusy]=useState('');const suiteBusyRef=useRef('');const [notice,setNotice]=useState<{text:string;error:boolean}|null>(null);
  const reportSuiteBusy=useCallback((label:string)=>{suiteBusyRef.current=label;setSuiteBusy(label);},[]);
  const [about,setAbout]=useState(false);const [outline,setOutline]=useState<Page<ClassOutline>|null>(null);const [classesOpen,setClassesOpen]=useState(false);
  const [workbenchOpen,setWorkbenchOpen]=useState(false);const [workbenchView,setWorkbenchView]=useState<SuiteView>('editor');const [inspectorOpen,setInspectorOpen]=useState(false);const [suiteSourcePath,setSuiteSourcePath]=useState<string|null>(null);const [suiteSourceVersion,setSuiteSourceVersion]=useState(0);
  const documentEpoch=useRef(0);const [documentVersion,setDocumentVersion]=useState(0);
  const [pcodeOffset,setPcodeOffset]=useState(0);const [pcodePage,setPcodePage]=useState<string|null>(null);
  const smoke=useRef(false);const smokeStarted=useRef(false);
  const [progress,setProgress]=useState('');const requestVersion=useRef(0);const initial=useRef(false);
  const run=useCallback(async<T,>(label:string,operation:()=>Promise<T>):Promise<T|undefined>=>{setBusy(label);try{return await operation();}catch(e){setNotice({text:errorText(e),error:true});return undefined;}finally{setBusy('');}},[]);
  const clearPreview=()=>{setAsset(null);setSource(null);setHex(null);setMetadata(null);};
  const load=useCallback(async(path?:string,syncSuite=true)=>{
    if(syncSuite&&suiteBusyRef.current){setNotice({text:'Espera a que termine la operación de edición o cancélala antes de abrir otro archivo.',error:true});return;}
    if(syncSuite){documentEpoch.current++;setDocumentVersion(documentEpoch.current);}
    const epoch=documentEpoch.current;
    const opened=await run('Abriendo SWF…',async()=>{const result=await api.openDocument(path);if(!result||epoch!==documentEpoch.current)return;requestVersion.current++;setDocument(result);if(syncSuite){setSuiteSourcePath(result.source_path??path??null);setSuiteSourceVersion(previous=>previous+1);}setSelected(null);setAsset(null);setSource(null);setHex(null);setMetadata(null);setOutline(null);setQuery('');setResources(empty());setScripts(empty());setNotice({text:`${result.info.filename} · ${result.info.tag_count} tags y ${result.script_count} unidades de script`,error:false});return result;});
    return opened;
  },[run]);
  const refreshSuiteDocument=useCallback(async(path:string)=>{if(documentVersion!==documentEpoch.current)throw new Error('El archivo activo cambió; se conserva la edición anterior sin reemplazar el explorador.');const opened=await load(path,false);if(!opened)throw new Error('El explorador no pudo abrir la copia de trabajo.');},[load,documentVersion]);
  const inspectTimeline=()=>{setWorkbenchOpen(false);setScope('resources');setCategory('frames');setQuery('');setSelected(null);clearPreview();};
  const showWorkbench=(view:SuiteView)=>{setWorkbenchView(view);setWorkbenchOpen(true);};
  useEffect(()=>{if(initial.current)return;initial.current=true;api.startup().then(s=>{smoke.current=s.smoke;if(s.path)void load(s.path);}).catch(e=>setNotice({text:errorText(e),error:true}));
    if(!api.native)return;
    const drag=getCurrentWebview().onDragDropEvent(event=>{if(event.payload.type==='drop'&&event.payload.paths[0])void load(event.payload.paths[0]);});
    const updates=listen<{completed:number;total:number}>('export-progress',event=>setProgress(`${event.payload.completed} / ${event.payload.total}`));
    return()=>{void drag.then(unlisten=>unlisten());void updates.then(unlisten=>unlisten());};
  },[load]);
  useEffect(()=>{if(!document)return;let active=true;const timer=setTimeout(()=>{
    const arguments_={query,offset:0,limit:200,...(scope==='resources'&&category!=='all'?{codes:codes[category]??[],other:category==='other'}:{})};
    api.rpc<Page<Resource|Script>>(document.session_id,scope,arguments_).then(page=>{if(active){if(scope==='resources')setResources(page as Page<Resource>);else setScripts(page as Page<Script>);}}).catch(e=>{if(active)setNotice({text:errorText(e),error:true});});
  },200);return()=>{active=false;clearTimeout(timer);};},[document,query,scope,category]);
  useEffect(()=>{if(!document)return;void api.verified({filename:document.info.filename,tag_count:document.info.tag_count,script_count:document.script_count,resource_rows:resources.items.length,script_rows:scripts.items.length,selected:selected?.id??null,source_state:source?.state??null,asset_mime:asset?.mime??null});},[document,resources,scripts,selected,source,asset]);
  const choose=async(item:Resource|Script,type:string)=>{if(!document)return;requestVersion.current++;const version=requestVersion.current;setSelected(item);setKind(type);setTab(type==='script'?'source':'preview');setPcodeOffset(0);setPcodePage(null);clearPreview();await run('Inspeccionando…',async()=>{
    if(type==='script'){const result=await api.rpc<Reconstruction>(document.session_id,'decompile',{id:item.id});if(version===requestVersion.current)setSource(result);}
    else {const resource=item as Resource;const meta=await api.rpc(document.session_id,'metadata',{id:item.id});if(version===requestVersion.current)setMetadata(meta);
      const format=resource.formats.find(f=>['png','jpeg','jpg','svg','mp3','wav','txt'].includes(f));
      if(format){const generated=await api.rpc<Asset>(document.session_id,'asset',{id:item.id,format});if(version===requestVersion.current)setAsset(generated);}
      else {const raw=await api.rpc<{text:string;total:number;offset:number}>(document.session_id,'raw',{id:item.id});if(version===requestVersion.current){setHex(raw);setTab('hex');}}
    }
  });};
  const getHex=async(offset=0)=>{if(!document||!selected||kind==='script')return;await run('Leyendo bytes…',async()=>setHex(await api.rpc(document.session_id,'raw',{id:selected.id,offset,length:4096})));};
  const more=async()=>{if(!document)return;await run('Cargando más…',async()=>{const page=scope==='scripts'?scripts:resources;const result=await api.rpc<Page<Resource|Script>>(document.session_id,scope,{query,offset:page.items.length,limit:200,...(scope==='resources'&&category!=='all'?{codes:codes[category]??[],other:category==='other'}:{})});if(scope==='scripts')setScripts(s=>({...result,items:[...s.items,...result.items]} as Page<Script>));else setResources(r=>({...result,items:[...r.items,...result.items]} as Page<Resource>));});};
  const exportSelected=async(format:string)=>{if(!document||!selected)return;await run('Exportando…',async()=>{const result=await api.exportItem(document.session_id,selected.id,format,kind);if(result)setNotice({text:`Exportado: ${result.path}${result.warning?` · ${result.warning}`:''}`,error:!!result.warning});});};
  const showClasses=async()=>{if(!document)return;setClassesOpen(true);if(outline===null)await run('Leyendo clases…',async()=>setOutline(await api.rpc(document.session_id,'outline')));};
  const pagePcode=async(offset:number)=>{if(!document||!selected)return;await run('Leyendo P-code…',async()=>{const page=await api.rpc<{pcode:string}>(document.session_id,'disassemble',{id:selected.id,offset});setPcodePage(page.pcode);setPcodeOffset(offset);});};
  useEffect(()=>{
    if(!smoke.current||smokeStarted.current||!document||!resources.items.length)return;smokeStarted.current=true;
    // Hidden native verification windows may suspend animation frames.
    const render=()=>new Promise<void>(resolve=>setTimeout(resolve,150));
    void(async()=>{
      const evidence:Record<string,unknown>={native:true,filename:document.info.filename,read:true};
      try{
        const units=await api.rpc<Page<Script>>(document.session_id,'scripts',{limit:40,query:'TestFor'});
        const candidates=units.items.length?units:await api.rpc<Page<Script>>(document.session_id,'scripts',{limit:40});
        setScope('scripts');setScripts(candidates);
        let chosen:Script|undefined=candidates.items[0];let chosenResult:Reconstruction|undefined;
        for(const unit of candidates.items){const result=await api.rpc<Reconstruction>(document.session_id,'decompile',{id:unit.id});if(result.source){chosen=unit;break;}}
        if(chosen){chosenResult=await api.rpc<Reconstruction>(document.session_id,'decompile',{id:chosen.id});await choose(chosen,'script');await render();evidence.source_visible=chosenResult.source!==null&&!!window.document.querySelector('[data-testid="script-code"]')?.textContent?.trim();evidence.source_state=chosenResult.state;evidence.script_id=chosen.id;setTab('pcode');await render();evidence.pcode_visible=!!window.document.querySelector('[data-testid="script-code"]')?.textContent?.trim();evidence.script_export=await api.verifyExport(document.session_id,chosen.id,'pcode','script');}
        const images=await api.rpc<Page<Resource>>(document.session_id,'resources',{limit:200,codes:[2,22,32,83,6,20,21,35,36,90,10,48,75]});
        const resource=images.items.find(r=>r.formats.some(f=>['svg','png','jpeg'].includes(f)));
        if(resource){setScope('resources');await choose(resource,'resource');await render();const image=window.document.querySelector<HTMLImageElement>('.preview-surface img');if(!image)throw new Error('El visor no creó la imagen');await image.decode();evidence.resource_visible=image.naturalWidth>0;evidence.resource_id=resource.id;evidence.resource_export=await api.verifyExport(document.session_id,resource.id,resource.formats.find(f=>['png','svg','jpeg'].includes(f))!,'resource');setTab('hex');setHex(await api.rpc(document.session_id,'raw',{id:resource.id}));await render();evidence.hex_visible=!!window.document.querySelector('.rust-code')?.textContent?.includes('00000000');}
        evidence.success=(candidates.total===0||evidence.pcode_visible===true)&&(resource?evidence.resource_visible===true&&evidence.hex_visible===true:true);
      }catch(error){evidence.success=false;evidence.error=errorText(error);setNotice({text:`Verificación: ${errorText(error)}`,error:true});}
      await api.verified({verification:evidence});
    })();
  },[document,resources]);
  const current=scope==='scripts'?scripts:resources;const info=document?.info;
  const interfaceBusy=Boolean(busy||suiteBusy);
  const groups=explorerGroups(current.items);
  const explorer=()=>{
    setWorkbenchOpen(false);
  };
  const menuAction=(event:React.MouseEvent<HTMLDetailsElement>)=>{
    if((event.target as HTMLElement).closest('button'))event.currentTarget.removeAttribute('open');
  };
  return <div className="studio simple-studio">
    <header className="classic-titlebar">
      <Code2 size={18}/><strong>Flash Universal Studio</strong>
      <span className="classic-document-name">{info?.filename??'Sin archivo'}</span>
      {info?<span className="rust-badge">{info.signature} · SWF {info.version}</span>:null}
    </header>
    <nav className="classic-menubar" aria-label="Menú principal">
      <details onClick={menuAction}><summary>Archivo</summary><div className="classic-menu">
        <button disabled={interfaceBusy} onClick={()=>load()}>Abrir SWF…</button>
        <button disabled={interfaceBusy} onClick={()=>showWorkbench('projects')}>Abrir o guardar proyecto…</button>
        <button disabled={!document||interfaceBusy} onClick={()=>showWorkbench('projects')}>Guardar copia SWF…</button>
        <button disabled={!document||interfaceBusy} onClick={()=>showWorkbench('export')}>Exportar…</button>
      </div></details>
      <details onClick={menuAction}><summary>Código</summary><div className="classic-menu">
        <button disabled={!document||interfaceBusy} onClick={()=>{explorer();setScope('scripts');}}>Explorar ActionScript</button>
        <button disabled={interfaceBusy} onClick={()=>showWorkbench('editor')}>Editar fuente o P-code</button>
        <button disabled={!document||interfaceBusy} onClick={showClasses}>Esquema de clases AS3</button>
      </div></details>
      <details onClick={menuAction}><summary>Recursos</summary><div className="classic-menu">
        <button disabled={!document||interfaceBusy} onClick={()=>{explorer();setScope('resources');setCategory('all');}}>Explorar recursos</button>
        <button disabled={interfaceBusy} onClick={()=>showWorkbench('resources')}>Editar recursos</button>
        <button disabled={!document||interfaceBusy} onClick={()=>document&&run('Exportando recursos…',async()=>{setProgress('');const result=await api.exportBatch(document.session_id);if(result)setNotice({text:result.items+' recursos: '+result.path+(result.warning?' · '+result.warning:''),error:!result.complete});})}>Exportar todos los recursos…</button>
      </div></details>
      <details onClick={menuAction}><summary>Herramientas</summary><div className="classic-menu">
        <button disabled={interfaceBusy} onClick={()=>showWorkbench('compile')}>Compilar AS / Haxe / HaxeFlixel</button>
        <button disabled={interfaceBusy} onClick={()=>showWorkbench('timeline')}>Cuadros del documento</button>
        <button disabled={interfaceBusy} onClick={()=>showWorkbench('preview')}>Reproducir con Ruffle</button>
        <button disabled={interfaceBusy} onClick={()=>showWorkbench('benchmarks')}>Benchmarks</button>
        <button disabled={interfaceBusy} onClick={()=>showWorkbench('tools')}>Configurar motores</button>
      </div></details>
      <button className="classic-help" onClick={()=>setAbout(true)}>Ayuda</button>
    </nav>
    <div className="classic-toolbar">
      <button className="studio-button secondary" disabled={interfaceBusy} onClick={()=>load()}><FolderOpen size={16}/>Abrir</button>
      <button className="studio-button secondary" disabled={!document||interfaceBusy} onClick={()=>showWorkbench('projects')}><Save size={15}/>Guardar</button>
      <button className="studio-button secondary" disabled={!document||interfaceBusy} onClick={()=>showWorkbench('export')}><ArrowDownToLine size={15}/>Exportar</button>
      <span className="classic-toolbar-divider"/>
      <label className="classic-view-choice"><span>Vista</span><select aria-label="Vista de trabajo" value={workbenchOpen?workbenchView:'explorer'} disabled={interfaceBusy} onChange={event=>event.target.value==='explorer'?explorer():showWorkbench(event.target.value as SuiteView)}>
        <option value="explorer">Explorador</option>{workspacePanels.map(panel=><option key={panel.id} value={panel.id}>{panel.label}</option>)}
      </select></label>
      <button className="studio-button secondary" disabled={interfaceBusy} onClick={()=>showWorkbench('editor')}><Code2 size={15}/>Editar código</button>
      <div className="classic-toolbar-right">
        {!workbenchOpen?<button className="studio-button secondary" aria-pressed={inspectorOpen} onClick={()=>setInspectorOpen(value=>!value)}><Info size={15}/>Propiedades</button>:<button className="studio-button secondary" onClick={explorer}>Volver al explorador</button>}
      </div>
    </div>
    <div className={'studio-body classic-explorer'+(inspectorOpen?' with-properties':'')} hidden={workbenchOpen}>
      <aside className="resource-explorer">
        <div className="classic-explorer-tabs" role="tablist" aria-label="Contenido del SWF">
          <button role="tab" aria-selected={scope==='resources'} onClick={()=>setScope('resources')}><Layers size={14}/>Recursos</button>
          <button role="tab" aria-selected={scope==='scripts'} onClick={()=>setScope('scripts')}><Code2 size={14}/>Scripts</button>
        </div>
        <label className="explorer-search"><Search size={14}/><input aria-label="Buscar recursos o scripts" placeholder="Buscar…" value={query} onChange={event=>setQuery(event.target.value)}/>{query?<button className="icon-button" aria-label="Limpiar búsqueda" onClick={()=>setQuery('')}><X size={13}/></button>:null}</label>
        {scope==='resources'?<select className="rust-category" aria-label="Categoría de recursos" value={category} onChange={event=>setCategory(event.target.value)}>{categories.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select>:null}
        <div className="classic-tree-root"><FileCode2 size={15}/><strong>{info?.filename??'Sin archivo'}</strong><span>{current.total}</span></div>
        <div className="rust-tree" role="list" aria-label={scope==='scripts'?'Unidades de script':'Lista de recursos'}>
          {groups.map(group=><details className="classic-tree-group" open key={group.name}>
            <summary><ChevronRight size={13}/><FolderOpen size={14}/><span>{group.name}</span><small>{group.items.length}</small></summary>
            {group.items.map(item=><button role="listitem" key={item.id} title={item.id+' · '+item.name} aria-current={selected?.id===item.id?'true':undefined} disabled={interfaceBusy} className={'rust-tree-item '+(selected?.id===item.id?'selected':'')} onClick={()=>choose(item,scope==='scripts'?'script':'resource')}>
              {scope==='scripts'?<Code2 size={13}/>:<Layers size={13}/>}
              <span><strong>{'symbol'in item&&item.symbol?item.symbol:item.name}</strong><small>{item.id}{'language'in item?' · '+item.language.toUpperCase():' · '+size(item.length)}</small></span>
              {item.diagnostics.length>0?<AlertTriangle size={12}/>:null}
            </button>)}
          </details>)}
          {document&&current.items.length===0?<p className="rust-muted rust-padding">No hay resultados.</p>:null}
          {current.items.length<current.total?<button className="studio-button secondary rust-more" disabled={interfaceBusy} onClick={more}>Cargar más ({current.items.length} / {current.total})</button>:null}
        </div>
      </aside>
      <main className="studio-main">
        {!document?<section className="classic-start">
          <Code2 size={34}/><h1>Flash Universal Studio</h1><p>Descompilación, edición y compilación de SWF.</p>
          <button className="studio-button primary" disabled={interfaceBusy} onClick={()=>load()}><FolderOpen size={17}/>Abrir SWF</button>
          <p className="rust-muted">También puedes arrastrar un archivo aquí. FWS · CWS · ZWS</p>
          <button className="text-button" onClick={()=>showWorkbench('compile')}>Compilar desde AS, Haxe o HaxeFlixel</button>
        </section>:!selected?<section className="classic-document-summary">
          <h1>{info?.filename}</h1>
          <dl><dt>Formato</dt><dd>{info?.signature} · SWF {info?.version}</dd><dt>Contenido</dt><dd>{info?.tag_count} tags · {document.script_count} unidades de código</dd><dt>Escenario</dt><dd>{info?.width.toFixed(0)} × {info?.height.toFixed(0)} px · {info?.frame_rate.toFixed(2)} fps</dd></dl>
          <p>Selecciona un elemento del árbol para ver su contenido.</p>
          <Diagnostics items={document.diagnostics}/>
        </section>:<div className="main-content">
          <div className="resource-heading"><h1>{selected.name}</h1><span>{selected.id}</span></div>
          <div className="rust-tabs" role="tablist">
            {(kind==='script'?['source','pcode']:['preview','hex','metadata']).map(mode=><button role="tab" aria-selected={tab===mode} key={mode} onClick={()=>{setTab(mode);if(mode==='hex'&&!hex)void getHex();}}>
              {({source:'ActionScript',pcode:'P-code',preview:'Vista previa',hex:'Hexadecimal',metadata:'Propiedades'} as Record<string,string>)[mode]}
            </button>)}
            <div className="classic-content-actions">
              {kind==='script'?<button className="studio-button secondary" disabled={interfaceBusy} onClick={()=>showWorkbench('editor')}>Editar</button>:null}
              <button className="studio-button secondary" disabled={interfaceBusy} onClick={()=>exportSelected(kind==='script'?(tab==='source'?'as':'pcode'):'raw')}><ArrowDownToLine size={13}/>Exportar</button>
            </div>
          </div>
          {kind==='script'?<>
            <div className="rust-script-status"><span className={'rust-badge '+(source?.source?'good':'')}>{source?.source?'Fuente reconstruida':'P-code / diagnóstico'}</span><span className="rust-muted">RustSWF · lectura</span><button className="text-button" disabled={interfaceBusy} onClick={()=>exportSelected('raw')}>Bytecode original</button></div>
            <Diagnostics items={source?.diagnostics??selected.diagnostics}/>
            {source?<pre className="rust-code" data-testid="script-code">{tab==='pcode'?(pcodePage??source.pcode):source.source??'La fuente no está disponible. Consulta P-code o abre el editor para probar otro motor.'}</pre>:<p className="rust-muted rust-padding">{busy||'Selecciona un método.'}</p>}
            {source&&tab==='pcode'&&(source.pcode_total??0)>65536?<div className="hex-pagination"><span>{pcodeOffset} / {source.pcode_total} caracteres</span><button disabled={interfaceBusy||pcodeOffset===0} onClick={()=>pagePcode(Math.max(0,pcodeOffset-65536))}>Anterior</button><button disabled={interfaceBusy||pcodeOffset+65536>=(source.pcode_total??0)} onClick={()=>pagePcode(pcodeOffset+65536)}>Siguiente</button></div>:null}
          </>:<>
            <div className="rust-export-formats">{(selected as Resource).formats.map(format=><button key={format} className="studio-button secondary" disabled={interfaceBusy} onClick={()=>exportSelected(format)}>{format==='raw'?'Bytes originales':format.toUpperCase()}</button>)}</div>
            <Diagnostics items={[...selected.diagnostics,...(asset?.diagnostics??[])]}/>
            {tab==='metadata'?<pre className="rust-code">{JSON.stringify(metadata,null,2)}</pre>:tab==='hex'?<>
              <pre className="rust-code">{hex?.text??busy}</pre>
              {hex?<div className="hex-pagination"><span>{hex.offset} / {hex.total} bytes</span><div><button disabled={hex.offset===0||interfaceBusy} onClick={()=>getHex(Math.max(0,hex.offset-4096))}>Anterior</button><button disabled={hex.offset+4096>=hex.total||interfaceBusy} onClick={()=>getHex(hex.offset+4096)}>Siguiente</button></div></div>:null}
            </>:asset?asset.mime.startsWith('image/')?<PreviewSurface src={api.assetUrl(asset.path)} alt={selected.name} onError={()=>setNotice({text:'No se pudo cargar la vista previa. Puedes exportar los bytes originales.',error:true})}/>:asset.mime.startsWith('audio/')?<div className="rust-padding"><audio aria-label="Audio extraído" controls src={api.assetUrl(asset.path)}/><p>{size(asset.bytes)} · {asset.mime}</p></div>:<TextAsset asset={asset}/>:<p className="rust-muted rust-padding">{busy||'Consulta las propiedades o exporta los bytes originales.'}</p>}
          </>}
        </div>}
      </main>
      <aside className="studio-inspector" hidden={!inspectorOpen}>
        <div className="panel-heading">Propiedades<button className="icon-button" aria-label="Cerrar propiedades" onClick={()=>setInspectorOpen(false)}><X size={14}/></button></div>
        <div className="inspector-content">{info?<><div className="inspector-title"><h3>{info.filename}</h3></div><dl className="rust-details"><dt>Versión SWF</dt><dd>{info.version}</dd><dt>Dimensiones</dt><dd>{info.width.toFixed(1)} × {info.height.toFixed(1)} px</dd><dt>Cuadros por segundo</dt><dd>{info.frame_rate.toFixed(2)}</dd><dt>Cuadros</dt><dd>{info.frame_count}</dd><dt>SHA-256</dt><dd className="rust-hash">{info.sha256}</dd>{selected?<><dt>Identidad</dt><dd className="rust-hash">{selected.id}</dd><dt>Ruta de tags</dt><dd>{('tag_path'in selected?selected.tag_path:selected.path).join(' / ')}</dd>{'abc_version'in selected?<><dt>ABC</dt><dd>{selected.abc_version??'AVM1'}</dd></>:null}</>:null}</dl></>:<p className="rust-muted">Abre un archivo para consultar sus propiedades.</p>}</div>
      </aside>
    </div>
    <SuiteWorkbench embedded open={workbenchOpen} activeView={workbenchView} onViewChange={setWorkbenchView} onBusyChange={reportSuiteBusy} onClose={()=>setWorkbenchOpen(false)} sourcePath={suiteSourcePath} sourceVersion={suiteSourceVersion} documentInfo={info??null} onDocumentChanged={refreshSuiteDocument} onTimeline={inspectTimeline}/>
    {notice?<div className={'notice '+(notice.error?'notice-error':'notice-success')} role={notice.error?'alert':'status'}><span>{notice.error?<AlertTriangle size={14}/>:null}{notice.text}</span><button className="icon-button" aria-label="Cerrar aviso" onClick={()=>setNotice(null)}><X size={14}/></button></div>:null}
    <footer className="studio-status"><span>{busy?<><Loader2 size={12} className="spin"/>{busy} {progress}<button className="text-button" onClick={()=>api.cancel().catch(error=>setNotice({text:errorText(error),error:true}))}>Cancelar</button></>:<><span className="status-dot"/>Listo</>}</span><span className="status-right">RustSWF · PySWF · v0.3.0</span></footer>
    {about?<Dialog title="Flash Universal Studio" onClose={()=>setAbout(false)}><p>Suite de descompilación, edición y compilación SWF con RustSWF, PySWF y herramientas externas.</p><p>Abre un archivo, selecciona un elemento y utiliza las vistas de código, recursos o proyecto. Cada operación de edición conserva el original.</p><p>GPL-3.0-or-later · Tauri · lector de tags Ruffle.</p></Dialog>:null}
    {classesOpen?<Dialog title="Esquema de clases AS3" onClose={()=>setClassesOpen(false)}><pre className="rust-code">{outline?outline.items.map(item=>'class '+item.name+(item.superclass?' extends '+item.superclass:'')+(item.interfaces.length?' implements '+item.interfaces.join(', '):'')+' {\n  '+[...item.fields,...item.methods].join('\n  ')+'\n}\n').join('\n'):'Leyendo clases…'}</pre></Dialog>:null}
  </div>;
}
function TextAsset({asset}:{asset:Asset}){const [text,setText]=useState('');useEffect(()=>{let active=true;fetch(api.assetUrl(asset.path)).then(r=>r.text()).then(t=>{if(active)setText(t);}).catch(e=>{if(active)setText(errorText(e));});return()=>{active=false;};},[asset]);return <pre className="rust-code">{text}</pre>;}


