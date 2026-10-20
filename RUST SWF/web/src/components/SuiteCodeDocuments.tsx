import {useEffect,useRef,useState} from 'react';
import {ChevronLeft,ChevronRight,FileCode2,Loader2} from 'lucide-react';
import * as api from '../api';
import {displayEngineName} from '../displayNames';
import {SuiteRun,SuiteState,SuiteUnit} from '../types';

type CodeDocument={
  document_id:string;scope:string;source?:string|null;completeness?:string;editable?:boolean;
  unit_ids:string[];revision:string;source_engine?:string;compiler_engine?:string;compiler_delegated?:boolean;
  compiler_eligibility?:{eligible:boolean;reason?:string|null};diagnostics?:unknown[];
  compilation?:string;behavior?:string;tag_sha256?:string;source_sha256?:string;
};
type DocumentPage={items:CodeDocument[];total:number;offset:number;limit:number;revision:string;source_engine:string;compiler_engine?:string};
type Pcode={unit_id:string;pcode:string;revision:string;engine:string;diagnostics?:unknown[]};
type Viewer={key:string;page:DocumentPage|null;document:CodeDocument|null;selectedId:string;unitId:string;
  mode:'source'|'pcode';pcode:Pcode|null;pending:string;error:string};
const empty=(key:string):Viewer=>({key,page:null,document:null,selectedId:'',unitId:'',mode:'source',pcode:null,pending:'',error:''});
const problem=(error:unknown)=>error instanceof Error?error.message:String(error);
const message=(value:unknown)=>typeof value==='string'?value:JSON.stringify(value,null,2);
const status=(value:string|undefined)=>value==='not_run'?'Sin ejecutar':value??'No registrado';

export default function SuiteCodeDocuments({unit,state,busy,run}:{unit:SuiteUnit|null;state:SuiteState|null;busy:boolean;run:SuiteRun}){
  const [engine,setEngine]=useState<'native'|'rust'>('native');
  const path=unit?.tag_path??[];
  const prefix='tag:'+path.join('/');
  const context=JSON.stringify([state?.working_path,state?.filename,state?.revision,path,engine]);
  const [snapshot,setSnapshot]=useState<Viewer>(()=>empty(context));
  const epoch=useRef(0),mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;epoch.current++;};},[]);
  useEffect(()=>{epoch.current++;setSnapshot(empty(context));},[context]);
  const view=snapshot.key===context?snapshot:empty(context);
  const available=!!state&&!!unit&&['as3','avm2'].includes(unit.language)&&path.length>0&&path.every(n=>Number.isInteger(n)&&n>=0);
  const disabled=busy||!!view.pending||!api.native||!available;
  const update=(change:Partial<Viewer>)=>setSnapshot(previous=>previous.key===context?{...previous,...change}:previous);

  async function request<T>(label:string,operation:()=>Promise<T>,accept:(previous:Viewer,value:T)=>Viewer){
    const key=context,ticket=++epoch.current;
    update({pending:label,error:''});
    let failure='';
    try{
      const value=await run(label,async()=>{try{return await operation();}catch(error){failure=problem(error);throw error;}});
      if(!mounted.current||ticket!==epoch.current)return;
      setSnapshot(previous=>previous.key!==key?previous:value===undefined?
        {...previous,error:failure||'Espera a que termine la operación actual.'}:accept(previous,value));
    }finally{
      if(mounted.current&&ticket===epoch.current)setSnapshot(previous=>previous.key===key?{...previous,pending:''}:previous);
    }
  }

  function checkRevision(revision:string){
    if(revision!==state?.revision)throw new Error('La revisión cambió durante la lectura. Reconstruye los documentos del tag actual.');
  }
  const ownedDocument=(identity:string)=>identity.startsWith(prefix+'/')&&/^(class|script):\d+$/.test(identity.slice(prefix.length+1));
  function checkDocument(document:CodeDocument,identity:string){
    checkRevision(document.revision);
    if(document.document_id!==identity||!ownedDocument(identity)||
      (document.source_engine&&document.source_engine!==engine))throw new Error('El documento recibido no corresponde al tag o motor seleccionado.');
    if(!Array.isArray(document.unit_ids)||document.unit_ids.some(id=>!id.startsWith(prefix+'/method:')||!/^\d+$/.test(id.slice((prefix+'/method:').length))))
      throw new Error('La API no devolvió identidades de método válidas para este documento.');
    return document;
  }
  const firstUnit=(document:CodeDocument)=>document.unit_ids.includes(unit?.id??'')?unit!.id:document.unit_ids[0]??'';
  const adoptDocument=(previous:Viewer,document:CodeDocument):Viewer=>({...previous,document:{...document,compiler_engine:document.compiler_engine??previous.page?.compiler_engine},selectedId:document.document_id,
    unitId:firstUnit(document),pcode:null,mode:typeof document.source==='string'?'source':'pcode'});

  const loadPage=async(offset:number)=>{
    if(!available||disabled)return;
    await request('Reconstruyendo clases y scripts…',async()=>{
      const page=await api.suiteRpc<DocumentPage>('code_documents',{tag_path:[...path],engine,offset,limit:200});
      checkRevision(page.revision);
      if(!Array.isArray(page.items)||page.offset!==offset||page.limit!==200||page.source_engine!==engine||
        !Number.isSafeInteger(page.total)||page.total<0||page.items.some(item=>!ownedDocument(item.document_id)))
        throw new Error('La página recibida no corresponde al tag o a la paginación solicitada.');
      const document=page.items.length?checkDocument(await api.suiteRpc<CodeDocument>('code_documents',
        {document_id:page.items[0].document_id,engine}),page.items[0].document_id):null;
      return {page,document};
    },(previous,next)=>next.document?adoptDocument({...previous,page:next.page},next.document):
      {...previous,page:next.page,document:null,selectedId:'',unitId:'',pcode:null});
  };
  const selectDocument=async(identity:string)=>{
    if(disabled||!view.page?.items.some(item=>item.document_id===identity))return;
    update({selectedId:identity,document:null,pcode:null,unitId:'',error:''});
    await request('Leyendo documento reconstruido…',async()=>checkDocument(await api.suiteRpc<CodeDocument>(
      'code_documents',{document_id:identity,engine}),identity),adoptDocument);
  };
  const readPcode=async()=>{
    const identity=view.unitId;
    if(disabled||!view.document?.unit_ids.includes(identity))return;
    await request('Leyendo P-code completo de la unidad…',async()=>{
      const result=await api.suiteRpc<Pcode>('decompile',{unit_id:identity,engine:'native'});
      checkRevision(result.revision);
      if(result.unit_id!==identity||typeof result.pcode!=='string'||!['python','native'].includes(result.engine))throw new Error('El P-code no corresponde a la unidad solicitada o al proveedor PySWF.');
      return result;
    },(previous,pcode)=>({...previous,pcode}));
  };
  const document=view.document,eligible=document?.compiler_eligibility?.eligible;
  return <details className="suite-advanced">
    <summary>Clases y scripts reconstruidos · solo lectura</summary>
    <p className="suite-muted">Reconstruye las declaraciones del ABC seleccionado. Cada documento conserva su estado y sus diagnósticos. La compilación y el comportamiento se verifican por separado.</p>
    <div className="suite-two-columns">
      <label className="suite-field"><span>Motor de reconstrucción</span><select aria-label="Motor de clases y scripts" value={engine} disabled={!api.native||!available}
        onChange={event=>{epoch.current++;setEngine(event.target.value as 'native'|'rust');}}>
        <option value="native">PySWF</option><option value="rust">RustSWF</option>
      </select></label>
      <div><p className="suite-muted">ABC {prefix} · revisión {state?.revision.slice(0,12)??'Sin documento'}</p>
        <button type="button" className="studio-button secondary" disabled={disabled} onClick={()=>void loadPage(0)}><FileCode2 size={14}/>Reconstruir clases/scripts</button>
      </div>
    </div>
    {!available?<p className="suite-muted">Selecciona una unidad AS3 para consultar su bloque ABC.</p>:null}
    {view.pending?<p className="suite-muted" role="status"><Loader2 size={14} className="spin"/>{view.pending}</p>:null}
    {view.error?<p className="suite-field-error" role="alert">{view.error}</p>:null}
    {view.page?<>
      <div className="suite-section-title"><span>{view.page.total?view.page.offset+1:0}–{view.page.offset+view.page.items.length} de {view.page.total} documentos</span>
        <div className="suite-actions"><button type="button" className="studio-button secondary" aria-label="Página anterior de documentos" disabled={disabled||view.page.offset===0} onClick={()=>void loadPage(Math.max(0,view.page!.offset-200))}><ChevronLeft size={14}/>Anterior</button>
          <button type="button" className="studio-button secondary" aria-label="Página siguiente de documentos" disabled={disabled||view.page.offset+view.page.items.length>=view.page.total} onClick={()=>void loadPage(view.page!.offset+200)}>Siguiente<ChevronRight size={14}/></button></div>
      </div>
      {view.page.items.length?<label className="suite-field"><span>Clase o script</span><select aria-label="Documento de clase o script" value={view.selectedId} disabled={disabled} onChange={event=>void selectDocument(event.target.value)}>
        {view.page.items.map(item=><option key={item.document_id} value={item.document_id}>{item.document_id} · {item.completeness??'Sin estado'}</option>)}
      </select></label>:<p className="suite-muted">La API no devolvió documentos de clase o script para este ABC.</p>}
    </>:null}
    {document?<section aria-label="Documento reconstruido de solo lectura">
      <div className="suite-code-heading"><div><h4>{document.document_id}</h4><span>{displayEngineName(document.source_engine??engine)} · {document.completeness??'Completitud no registrada'} · Visor de solo lectura</span></div>
        <div className="suite-segments"><button type="button" aria-pressed={view.mode==='source'} disabled={typeof document.source!=='string'} onClick={()=>update({mode:'source'})}>Fuente</button><button type="button" aria-pressed={view.mode==='pcode'} onClick={()=>update({mode:'pcode'})}>P-code</button></div>
      </div>
      <p className="suite-muted">Elegibilidad de recompilación: {eligible===true?'Elegible según la API':eligible===false?'No elegible':'No registrada'}. Estado de edición en la API: {document.editable===true?'permitida':document.editable===false?'solo lectura':'no registrado'}.</p>
      {document.compiler_eligibility?.reason?<p className="suite-muted">Motivo: {document.compiler_eligibility.reason}</p>:null}
      <p className="suite-muted">Compilador asociado: {document.compiler_engine?displayEngineName(document.compiler_engine):'No registrado'}{document.compiler_delegated===true?' · compilación delegada':document.compiler_delegated===false?' · sin delegación':''}. Compilación: {status(document.compilation)}. Comportamiento: {status(document.behavior)}.</p>
      {document.diagnostics?.length?<pre className="suite-diagnostics" aria-label="Diagnósticos del documento">{document.diagnostics.map(message).join('\n')}</pre>:null}
      {view.mode==='source'?<textarea className="suite-code-editor" rows={18} aria-label={`Fuente del documento ${document.document_id}`} spellCheck={false} readOnly value={document.source??''}/>:<>
        <p className="suite-muted">P-code íntegro de una unidad por lectura, desensamblado por PySWF. Las identidades provienen de este documento; no se atribuye cobertura adicional al ABC.</p>
        {document.unit_ids.length?<div className="suite-path"><select aria-label="Unidad para ver P-code" value={view.unitId} disabled={disabled} onChange={event=>update({unitId:event.target.value,pcode:null})}>{document.unit_ids.map(id=><option key={id} value={id}>{id}</option>)}</select>
          <button type="button" className="studio-button secondary" disabled={disabled||!view.unitId} onClick={()=>void readPcode()}>Ver P-code</button></div>:<p className="suite-muted">La API no proporcionó unidades para este documento.</p>}
        {view.pcode?<><p className="suite-muted">{view.pcode.unit_id} · {displayEngineName(view.pcode.engine)} · revisión {view.pcode.revision.slice(0,12)}</p><textarea className="suite-code-editor" rows={18} aria-label={`P-code completo de ${view.pcode.unit_id}`} spellCheck={false} readOnly value={view.pcode.pcode}/>{view.pcode.diagnostics?.length?<pre className="suite-diagnostics">{view.pcode.diagnostics.map(message).join('\n')}</pre>:null}</>:<p className="suite-muted">Selecciona una unidad y pulsa Ver P-code para consultar el respaldo exacto.</p>}
      </>}
    </section>:null}
  </details>;
}
