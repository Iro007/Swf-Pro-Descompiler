import {useEffect,useRef,useState} from 'react';
import {Check} from 'lucide-react';
import * as api from '../api';
import {SuiteResourceInfo,SuiteState,SuiteVectorPoint} from '../types';
import VectorPointEditor,{PointDraft} from '../components/VectorPointEditor';
import {Session} from './useSession';
import {problem} from './util';

type TextDraft={text:string;revision:string};
type FontDraft={name:string;bold:boolean;italic:boolean;ascent:string;descent:string;leading:string;revision:string};
const glyphLabel=(code:number|null,index:number)=>code!==null&&Number.isInteger(code)&&code>=0&&code<=0x10ffff?`${index} · U+${code.toString(16).toUpperCase().padStart(4,'0')}`:`${index} · sin código`;
export const editableTypes=new Set([2,22,32,83,48,75,37,14,6,20,21,35,36,90]);

/** Structured editing of one resource (media replacement, text, font properties, vertices) on the working copy. */
export default function ResourceEditor({tagIndex,session}:{tagIndex:number;session:Session}){
  const {state,documentKey,drafts}=session;
  const [info,setInfo]=useState<SuiteResourceInfo|null>(null);
  const [error,setError]=useState('');
  const [mediaPath,setMediaPath]=useState('');
  const [glyphIndex,setGlyphIndex]=useState<number|null>(null);
  const [points,setPoints]=useState<Record<string,PointDraft>>({});
  const [showMeta,setShowMeta]=useState(false);
  const epoch=useRef(0);
  const revision=state?.revision;
  const base=`res|${documentKey}|${tagIndex}`;

  useEffect(()=>{setInfo(null);setMediaPath('');setGlyphIndex(null);setPoints({});},[tagIndex,documentKey]);
  useEffect(()=>{
    if(revision===undefined)return;
    const ticket=++epoch.current;
    session.track(api.suiteRpc<SuiteResourceInfo>('resource_info',{tag_index:tagIndex})).then(next=>{if(ticket===epoch.current){setInfo(next);setError('');}})
      .catch(failure=>{if(ticket===epoch.current){setInfo(null);setError(problem(failure));}});
    return()=>{epoch.current++;};
  // session.track is stable
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[tagIndex,revision]);

  const disabled=!!session.busy||!state;
  const change=(label:string,args:Record<string,unknown>,baseRevision:string|undefined,message:string)=>session.apply(label,()=>api.suiteRpc<SuiteState>('resource_edit',{tag_index:tagIndex,base_revision:baseRevision??state?.revision,...args}),message);
  const choose=async(kind:'image'|'audio')=>{
    try{const path=await api.suitePick(kind);if(path)setMediaPath(path);}catch(failure){session.notify(problem(failure),true);}
  };

  const parsed=info?.parsed??{};
  const textDraft=drafts.get<TextDraft>(`${base}|text`);
  const initialText=String(parsed.initial_text??parsed.text??'');
  const text=textDraft?.text??initialText;
  const layout=parsed.layout&&typeof parsed.layout==='object'?parsed.layout as Record<string,unknown>:{};
  const storedFont=drafts.get<FontDraft>(`${base}|font`);
  const fontDraft:FontDraft=storedFont??{name:String(parsed.name??parsed.font_name??''),bold:!!parsed.bold,italic:!!parsed.italic,
    ascent:layout.ascent===undefined?'':String(layout.ascent),descent:layout.descent===undefined?'':String(layout.descent),leading:layout.leading===undefined?'':String(layout.leading),revision:info?.revision??state?.revision??''};
  const setFont=(field:keyof FontDraft,value:string|boolean)=>drafts.set(`${base}|font`,{...fontDraft,[field]:value});
  const metricsValid=(['ascent','descent','leading'] as const).every(field=>fontDraft[field]===''||Number.isInteger(Number(fontDraft[field])));
  const glyphs=info?.glyphs??[];
  const glyph=glyphIndex===null?glyphs[0]:glyphs.find(value=>value.index===glyphIndex);
  const pointList:SuiteVectorPoint[]=info?.kind==='font'?glyph?.points??[]:info?.points??[];
  const pointPrefix=`${base}|${info?.kind}|${glyph?.index??''}`;

  const replaceMedia=async(kind:'image'|'sound')=>{
    const next=await change(`Reemplazando ${kind==='image'?'imagen':'audio'}…`,{operation:`replace-${kind}`,path:mediaPath.trim()},undefined,'Recurso validado y aplicado a la copia de trabajo.');
    if(next)setMediaPath('');
  };
  const saveText=async()=>{
    if(!textDraft)return;
    const next=await change('Guardando texto…',{operation:'text',text:textDraft.text},textDraft.revision,'Texto actualizado y validado.');
    if(next)drafts.set(`${base}|text`,undefined);
  };
  const saveFont=async()=>{
    const metrics=Object.fromEntries((['ascent','descent','leading'] as const).filter(field=>fontDraft[field]!=='').map(field=>[field,Number(fontDraft[field])]));
    const next=await change('Guardando propiedades de fuente…',{operation:'font-properties',font_name:fontDraft.name,bold:fontDraft.bold,italic:fontDraft.italic,...metrics},fontDraft.revision,'Propiedades de la fuente actualizadas.');
    if(next)drafts.set(`${base}|font`,undefined);
  };
  const applyPoint=async(point:SuiteVectorPoint,draft:PointDraft,key:string)=>{
    const font=info?.kind==='font';
    const next=await change('Aplicando vértice…',{operation:font?'font-glyph':'shape-point',...(font?{glyph_index:glyph?.index}:{}),point_index:point.index,x:Number(draft.x),y:Number(draft.y)},draft.revision,`${font?'Glifo':'Forma'} actualizado.`);
    if(next)setPoints(previous=>{const copy={...previous};delete copy[key];return copy;});
  };

  if(error)return <p className="panel-error" role="alert">{error}</p>;
  if(!info)return <p className="panel-muted">Leyendo estructura del recurso…</p>;
  return <div className="resource-editor">
    {info.kind==='image'||info.kind==='sound'?<section>
      <h4>{info.kind==='image'?'Reemplazar imagen':'Reemplazar audio (MP3 o WAV)'}</h4>
      <div className="path-row">
        <input value={mediaPath} disabled={disabled} onChange={event=>setMediaPath(event.target.value)} aria-label={info.kind==='image'?'Imagen nueva':'Audio nuevo'} placeholder="Ruta del recurso de reemplazo"/>
        <button type="button" className="tool-button" disabled={disabled||!api.native} onClick={()=>void choose(info.kind==='image'?'image':'audio')}>Elegir</button>
      </div>
      <button type="button" className="tool-button primary" disabled={disabled||!mediaPath.trim()} onClick={()=>void replaceMedia(info.kind==='image'?'image':'sound')}><Check size={14}/>Reemplazar y validar</button>
    </section>:null}
    {info.kind==='text'?<section>
      <h4>Texto editable</h4>
      <textarea className="resource-text" aria-label="Texto del recurso" value={text} disabled={disabled} spellCheck={false}
        onChange={event=>drafts.set<TextDraft>(`${base}|text`,{text:event.target.value,revision:textDraft?.revision??info.revision})}/>
      <div className="button-row">
        <button type="button" className="tool-button" disabled={disabled||!textDraft} onClick={()=>drafts.set(`${base}|text`,undefined)}>Descartar texto</button>
        <button type="button" className="tool-button primary" disabled={disabled||!textDraft||text===initialText} onClick={()=>void saveText()}><Check size={14}/>Guardar texto y validar</button>
      </div>
      {textDraft&&textDraft.revision!==info.revision?<p className="panel-error">El borrador usa una revisión anterior y se conserva hasta que lo revises.</p>:null}
    </section>:null}
    {info.kind==='font'?<section>
      <h4>Propiedades de la fuente</h4>
      <label className="field"><span>Nombre de la fuente</span><input aria-label="Nombre de la fuente" value={fontDraft.name} disabled={disabled} onChange={event=>setFont('name',event.target.value)}/></label>
      <div className="check-row">
        <label><input type="checkbox" checked={fontDraft.bold} disabled={disabled} onChange={event=>setFont('bold',event.target.checked)}/>Negrita</label>
        <label><input type="checkbox" checked={fontDraft.italic} disabled={disabled} onChange={event=>setFont('italic',event.target.checked)}/>Cursiva</label>
      </div>
      <div className="coord-fields">{([['ascent','Ascenso'],['descent','Descenso'],['leading','Interlineado']] as const).map(([field,label])=>
        <label key={field}>{label}<input aria-label={label} type="number" step={1} value={fontDraft[field]} disabled={disabled} onChange={event=>setFont(field,event.target.value)}/></label>)}</div>
      {storedFont&&fontDraft.revision!==info.revision?<p className="panel-error">Las propiedades usan una revisión anterior; se conservan hasta que las revises.</p>:null}
      <div className="button-row">
        <button type="button" className="tool-button" disabled={disabled||!storedFont} onClick={()=>drafts.set(`${base}|font`,undefined)}>Descartar propiedades</button>
        <button type="button" className="tool-button primary" disabled={disabled||!storedFont||!metricsValid} onClick={()=>void saveFont()}>Guardar propiedades</button>
      </div>
      <label className="field"><span>Glifo</span><select aria-label="Glifo de la fuente" value={glyph?.index??''} disabled={disabled||!glyphs.length} onChange={event=>setGlyphIndex(Number(event.target.value))}>
        {glyphs.map(value=><option key={value.index} value={value.index}>{glyphLabel(value.code,value.index)}</option>)}</select></label>
      <p className="panel-muted">Escala EM: {String(parsed.em_scale??'según la fuente')} unidades.</p>
    </section>:null}
    {info.kind==='shape'||info.kind==='font'?<VectorPointEditor key={`${base}|${glyph?.index??'shape'}`} points={pointList} units={info.coordinate_units} revision={info.revision} disabled={disabled}
      drafts={points} draftPrefix={pointPrefix} onDraft={(key,value)=>setPoints(previous=>({...previous,[key]:value}))}
      onDiscard={key=>setPoints(previous=>{const copy={...previous};delete copy[key];return copy;})} onApply={applyPoint}/>:null}
    {info.kind==='raw'?<p className="panel-muted">Este tag conserva sus bytes originales; no anuncia una edición estructurada compatible.</p>:null}
    <details className="metadata" onToggle={event=>setShowMeta(event.currentTarget.open)}><summary>Propiedades interpretadas</summary>{showMeta?<pre>{JSON.stringify(info.parsed,null,2)}</pre>:null}</details>
  </div>;
}
