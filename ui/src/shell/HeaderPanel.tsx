import {ReactNode,useEffect,useRef,useState} from 'react';
import {Check} from 'lucide-react';
import * as api from '../api';
import {SuiteHeader,SuiteState} from '../types';
import {Session} from './useSession';
import {bytesText,problem} from './util';

const compressionText:Record<string,string>={none:'sin compresión',zlib:'zlib (CWS)',lzma:'LZMA (ZWS)',unknown:'desconocida'};
const attributeText:Record<string,string>={use_network:'Acceso a red',actionscript3:'ActionScript 3',has_metadata:'Metadatos',use_gpu:'Aceleración GPU',use_direct_blit:'Direct blit'};

function Field({name,children}:{name:string;children:ReactNode}){return <><dt>{name}</dt><dd>{children}</dd></>;}

/** The header node: container, stage, timing and flags of the movie, with the frame rate editable. */
export default function HeaderPanel({session}:{session:Session}){
  const [header,setHeader]=useState<SuiteHeader|null>(null);
  const [error,setError]=useState('');
  const [rate,setRate]=useState('');
  const epoch=useRef(0);
  const {state,track}=session;
  const revision=state?.revision;

  useEffect(()=>{
    if(revision===undefined)return;
    const ticket=++epoch.current;
    track(api.suiteRpc<SuiteHeader>('header')).then(next=>{if(ticket===epoch.current){setHeader(next);setRate(String(next.frame_rate));setError('');}})
      .catch(failure=>{if(ticket===epoch.current)setError(problem(failure));});
    return()=>{epoch.current++;};
  },[revision,track]);

  const value=Number(rate);
  const valid=rate.trim()!==''&&Number.isFinite(value)&&value>0&&value<=120;
  const changed=!!header&&valid&&value!==header.frame_rate;
  const applyRate=()=>{if(!state||!changed)return;void session.apply('Actualizando cabecera SWF…',()=>api.suiteRpc<SuiteState>('resource_edit',{operation:'header',frame_rate:value,base_revision:state.revision}),`Velocidad actualizada a ${value} fps.`);};

  if(error)return <p className="panel-error" role="alert">{error}</p>;
  if(!header)return <p className="panel-muted">Leyendo cabecera…</p>;
  const size=header.frame_size;
  return <div className="header-panel" data-testid="header-info">
    <dl className="facts">
      <Field name="Archivo">{header.filename}</Field>
      <Field name="Formato">{header.signature} · {compressionText[header.compression]??header.compression}</Field>
      <Field name="Versión del SWF">{header.version}</Field>
      <Field name="Longitud declarada">{header.file_length.toLocaleString()} bytes <span className="dim">({bytesText(header.file_length)}, sin comprimir)</span></Field>
      <Field name="Tamaño del archivo">{header.file_size.toLocaleString()} bytes <span className="dim">({bytesText(header.file_size)})</span></Field>
      <Field name="Escenario">{size.width.toLocaleString()} × {size.height.toLocaleString()} px <span className="dim">({(size.xmax-size.xmin).toLocaleString()} × {(size.ymax-size.ymin).toLocaleString()} twips)</span></Field>
      <Field name="Velocidad">
        <span className="inline-edit">
          <input type="number" min={0.01} max={120} step="0.01" value={rate} onChange={event=>setRate(event.target.value)} aria-label="Cuadros por segundo" disabled={!!session.busy}/>
          <span className="dim">fps</span>
          <button type="button" className="tool-button" disabled={!changed||!!session.busy} onClick={applyRate}><Check size={14}/>Aplicar</button>
        </span>
      </Field>
      <Field name="Cuadros">{header.frame_count.toLocaleString()} declarados · {header.shown_frames.toLocaleString()} con ShowFrame</Field>
      <Field name="Etiquetas (tags)">{header.tag_count.toLocaleString()}</Field>
      <Field name="ActionScript 3">{header.has_as3?`Sí · ${header.abc_blocks} bloque${header.abc_blocks===1?'':'s'} ABC`:'No'}</Field>
      <Field name="Unidades de código">{header.script_count?.toLocaleString()??'—'}</Field>
      <Field name="Metadatos">{header.has_metadata?'Sí':'No'}</Field>
      {header.file_attributes?<Field name="Atributos">
        <span className="chips">{Object.entries(header.file_attributes).map(([name,on])=><span key={name} className={`chip ${on?'on':''}`}>{attributeText[name]??name}</span>)}</span>
      </Field>:null}
      <Field name="SHA-256"><code className="hash">{header.sha256}</code></Field>
    </dl>
    {header.metadata?<details className="metadata"><summary>Metadatos del documento</summary><pre>{header.metadata}</pre></details>:null}
  </div>;
}
