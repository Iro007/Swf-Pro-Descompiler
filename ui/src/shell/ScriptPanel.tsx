import {useEffect,useMemo,useRef,useState} from 'react';
import {AlertTriangle,Check,GitCompareArrows,Loader2} from 'lucide-react';
import * as api from '../api';
import {ClassMember,SuiteDecompile,SuiteEngine,SuiteState,TreeNode} from '../types';
import {displayEngineName} from '../displayNames';
import CodeEditor from './CodeEditor';
import CompareDialog from './CompareDialog';
import SplitPane from './SplitPane';
import {Session} from './useSession';
import {problem,shortRevision} from './util';

interface Part {unitId:string;name:string;result?:SuiteDecompile;error?:string}
interface Draft {text:string;revision:string}
const COMPOSITE_STEP=40;
const roleText:Record<string,string>={constructor:'constructor',static_init:'inicializador estático',script_init:'inicializador del script',method:'método',getter:'get',setter:'set',function:'función',
  static_method:'estático',static_getter:'get estático',static_setter:'set estático',static_function:'función estática',other:'anónimo'};

/** The source JPEXS returns is the whole class document; every other engine returns the method body. */
const sourceOf=(result:SuiteDecompile)=>result.engine==='jpexs'&&result.document_source!=null?result.document_source:result.source;
const diagnosticsOf=(result?:SuiteDecompile)=>[...(result?.diagnostics??[]),...(result?.fallback_diagnostics??[])].map(problem).filter(Boolean);

function composeSource(parts:Part[]):string{
  // JPEXS answers with the whole class document: show it once instead of repeating it per member.
  const documents=new Map<string,string>();
  for(const part of parts){const {result}=part;if(result?.engine==='jpexs'&&result.document_source!=null)documents.set(result.source_document??part.unitId,result.document_source);}
  if(documents.size)return [...documents.values()].join('\n\n');
  return parts.map(part=>{
    const {result}=part;
    if(!result)return `// ── ${part.name} ──\n${part.error?`// Error: ${part.error}`:'// Leyendo…'}`;
    const note=diagnosticsOf(result)[0];
    const body=result.source??`// Fuente no disponible con ${displayEngineName(result.engine)}${note?`: ${note.split('\n')[0]}`:''}. Consulta el P-code.`;
    return `// ── ${part.name} ──\n${body}`;
  }).join('\n\n');
}
const composePcode=(parts:Part[])=>parts.map(part=>`// ── ${part.name} ──\n${part.result?part.result.pcode||'// (sin P-code)':part.error?`// Error: ${part.error}`:'// Leyendo…'}`).join('\n\n');

/** Decompiled source next to P-code for one method, or for a whole class (members listed like JPEXS traits). */
export default function ScriptPanel({node,session,engine,onEngineChange}:{node:TreeNode;session:Session;engine:string;onEngineChange:(engine:string)=>void}){
  const isClass=node.kind==='class';
  const members:ClassMember[]=useMemo(()=>isClass?node.members??[]:[],[isClass,node.members]);
  const [focus,setFocus]=useState<string|null>(null);
  const [limit,setLimit]=useState(COMPOSITE_STEP);
  const [parts,setParts]=useState<Part[]>([]);
  const [loading,setLoading]=useState(false);
  const [comparing,setComparing]=useState(false);
  const [generation,setGeneration]=useState(0);
  const epoch=useRef(0);
  const {state,catalog,documentKey,drafts,track}=session;
  const revision=state?.revision;

  useEffect(()=>{setFocus(null);setLimit(COMPOSITE_STEP);},[node.id]);
  const names=useMemo(()=>new Map(members.map(member=>[member.unit_id,member.name])),[members]);
  const unitIds=useMemo(()=>focus?[focus]:isClass?members.slice(0,limit).map(member=>member.unit_id):node.unit_id?[node.unit_id]:[],[focus,isClass,members,limit,node.unit_id]);
  const loadKey=`${unitIds.join(',')}|${engine}|${revision}`;

  useEffect(()=>{
    if(revision===undefined||!unitIds.length){setParts([]);setLoading(false);return;}
    const ticket=++epoch.current;
    setLoading(true);
    void(async()=>{
      await new Promise(resolve=>setTimeout(resolve,100));
      if(ticket!==epoch.current)return;
      const next:Part[]=unitIds.map(unitId=>({unitId,name:names.get(unitId)??node.label}));
      setParts(next.map(part=>({...part})));
      for(let index=0;index<unitIds.length;index++){
        try{
          const result=await track(api.suiteRpc<SuiteDecompile>('decompile',{unit_id:unitIds[index],engine}));
          if(ticket!==epoch.current)return;
          next[index]={...next[index],result};
        }catch(failure){
          if(ticket!==epoch.current)return;
          next[index]={...next[index],error:problem(failure)};
        }
        setParts([...next]);
      }
      if(ticket===epoch.current)setLoading(false);
    })();
    return()=>{epoch.current++;};
  // unitIds/names/engine/revision are all part of loadKey
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[loadKey,track]);

  const single=parts.length===1&&(focus!==null||!isClass);
  const part=single?parts[0]:undefined;
  const result=part?.result;
  const draftKey=`script|${documentKey}|${part?.unitId}|${engine}`;
  const draft=single?drafts.get<Draft>(draftKey):undefined;
  const original=result?sourceOf(result):null;
  const canEdit=single&&!!result&&(result.editable||(result.engine==='jpexs'&&!!result.source_document&&result.document_source!=null));
  const placeholderSource=result&&original===null?`// Fuente no disponible con ${displayEngineName(result.engine)}${diagnosticsOf(result)[0]?`: ${diagnosticsOf(result)[0].split('\n')[0]}`:''}.\n// Consulta el P-code o prueba otro motor.`:'';
  const sourceText=single?(draft?.text??original??(result?placeholderSource:part?.error?`// Error: ${part.error}`:'')):composeSource(parts);
  const pcodeText=single?(result?result.pcode:part?.error?`// Error: ${part.error}`:''):composePcode(parts);
  const usedEngine=result?.engine??parts.find(item=>item.result)?.result?.engine;
  const diagnostics=single?diagnosticsOf(result):[];
  const engines:SuiteEngine[]=(catalog?.engines??[]).filter(item=>item.operations.includes('decompile'));
  const status=!single?`Vista de solo lectura de ${parts.length} miembro${parts.length===1?'':'s'}`:draft?`Borrador · revisión base ${shortRevision(draft.revision)}`:canEdit?'Listo para editar':result?'Solo lectura con este motor':'';
  const stale=!!draft&&draft.revision!==revision;
  const busy=!!session.busy;
  const loadedMask=parts.map(item=>item.result||item.error?'1':'0').join('');
  const syncKey=`${loadKey}|${loadedMask}|${generation}`;
  const discard=()=>{drafts.set(draftKey,undefined);setGeneration(value=>value+1);};

  const type=(value:string)=>{
    if(!single||!result||!canEdit)return;
    if(value===(original??''))drafts.set(draftKey,undefined);
    else drafts.set<Draft>(draftKey,{text:value,revision:draft?.revision??result.revision});
  };
  const apply=async()=>{
    if(!part||!result||!draft||!canEdit)return;
    const next=await session.apply('Aplicando y validando…',()=>api.suiteRpc<SuiteState>('edit',{unit_id:part.unitId,code:draft.text,mode:'source',base_revision:draft.revision,engine:result.engine,
      ...(result.source_document?{source_document:result.source_document}:{})}),'Cambio validado y aplicado a la copia de trabajo.');
    if(next)drafts.set(draftKey,undefined);
  };

  if(!unitIds.length)return <div className="script-panel"><p className="panel-muted">{isClass?'Esta clase no tiene métodos con código.':'La unidad de código no está disponible.'}</p></div>;
  return <div className="script-panel" data-testid="script-panel">
    <div className="panel-toolbar">
      <label className="toolbar-field">Motor
        <select value={engine} onChange={event=>onEngineChange(event.target.value)} aria-label="Motor de descompilación">
          <option value="auto">Automático</option>
          {engines.map(item=><option key={item.id} value={item.id} disabled={!item.available}>{displayEngineName(item.id,item.name)}{item.available?'':' · no disponible'}</option>)}
        </select>
      </label>
      {usedEngine?<span className="badge" title="Motor que produjo este resultado">{displayEngineName(usedEngine)}</span>:null}
      {loading?<span className="toolbar-text dim"><Loader2 size={13} className="spin"/>Leyendo…</span>:null}
      <button type="button" className="tool-button" disabled={!single||busy} onClick={()=>setComparing(true)} title="Compara la salida de cada motor para esta unidad"><GitCompareArrows size={14}/>Comparar motores</button>
      <span className="toolbar-gap"/>
      <span className="toolbar-text dim" data-testid="script-status">{status}</span>
      <button type="button" className="tool-button" disabled={!draft||busy} onClick={discard}>Descartar</button>
      <button type="button" className="tool-button primary" disabled={!draft||!canEdit||busy} onClick={()=>void apply()}><Check size={14}/>Aplicar y validar</button>
    </div>
    {diagnostics.length?<details className="diagnostics"><summary><AlertTriangle size={13}/>{diagnostics[0].split('\n')[0]}{diagnostics.length>1?` (+${diagnostics.length-1})`:''}</summary>{diagnostics.map((text,index)=><p key={index}>{text}</p>)}</details>:null}
    {stale?<p className="panel-error">El documento cambió desde que empezaste este borrador. Se conserva tu texto; el motor comprobará la revisión antes de aplicar.</p>:null}
    {result?.engine==='jpexs'&&result.document_source!=null&&single?<p className="panel-note">Documento ActionScript completo de JPEXS: el cambio se aplica a su clase, con validación y reapertura.</p>:null}
    <SplitPane unit="ratio" storageKey="flash-studio.code-split" initial={0.5} min={0.2} max={0.8} label="Reparto entre fuente y P-code" className="code-split"
      first={<section className="code-pane" aria-label="Código fuente descompilado">
        <header className="pane-title"><span>ActionScript</span><span className="dim">{single?(canEdit?'editable':'lectura'):'lectura'}</span></header>
        <CodeEditor value={sourceText} docKey={`src|${loadKey}|${documentKey}`} syncKey={syncKey} language="actionscript" readOnly={!canEdit} label={`Fuente de ${part?.name??node.label}`} testId="script-source" onChange={type}/>
      </section>}
      second={<section className="code-pane" aria-label="P-code">
        <header className="pane-title"><span>P-code</span><span className="dim">solo lectura</span></header>
        <CodeEditor value={pcodeText} docKey={`pcode|${loadKey}|${documentKey}`} syncKey={syncKey} language="pcode" readOnly label={`P-code de ${part?.name??node.label}`} testId="script-pcode"/>
      </section>}/>
    {isClass?<nav className="members" aria-label="Miembros de la clase">
      <button type="button" className={`member ${focus===null?'active':''}`} aria-pressed={focus===null} onClick={()=>setFocus(null)}>Clase completa</button>
      {members.map(member=><button type="button" key={member.unit_id} className={`member ${focus===member.unit_id?'active':''}`} aria-pressed={focus===member.unit_id} title={`${roleText[member.role]??member.role} · ${member.unit_id}`} onClick={()=>setFocus(member.unit_id)}>
        {member.name}<small>{roleText[member.role]??member.role}</small></button>)}
      {focus===null&&members.length>limit?<button type="button" className="member more" onClick={()=>setLimit(value=>value+COMPOSITE_STEP)}>Cargar {Math.min(COMPOSITE_STEP,members.length-limit)} más ({limit} de {members.length})</button>:null}
    </nav>:null}
    {comparing&&part?<CompareDialog unitId={part.unitId} name={part.name} onClose={()=>setComparing(false)}/>:null}
  </div>;
}
