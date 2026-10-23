import {useEffect,useMemo,useRef,useState} from 'react';
import {ChevronLeft,ChevronRight,ChevronsLeft,ChevronsRight,Copy} from 'lucide-react';
import * as api from '../api';
import {SuiteRaw} from '../types';
import {Session} from './useSession';
import {problem} from './util';

export interface HexTarget {tagIndex?:number;tagPath?:number[];scope?:'file'}
const PAGE_SIZES=[1024,4096,16384,65536];

function bytesOf(hex:string):Uint8Array{
  const bytes=new Uint8Array(hex.length>>1);
  for(let i=0;i<bytes.length;i++)bytes[i]=parseInt(hex.slice(i*2,i*2+2),16);
  return bytes;
}

/** Classic dump: offset, 16 bytes in two groups of 8, printable characters. */
export function hexdump(bytes:Uint8Array,base:number):string{
  const lines:string[]=[];
  for(let row=0;row<bytes.length;row+=16){
    const slice=bytes.subarray(row,row+16);
    let hex='',text='';
    for(let i=0;i<16;i++){
      if(i===8)hex+=' ';
      if(i<slice.length){const value=slice[i];hex+=value.toString(16).padStart(2,'0')+' ';text+=value>=32&&value<127?String.fromCharCode(value):'.';}
      else hex+='   ';
    }
    lines.push(`${(base+row).toString(16).padStart(8,'0')}  ${hex} |${text}|`);
  }
  return lines.join('\n');
}

/** Paged hex view of one tag (or of the whole file) through the `raw` operation. */
export default function HexPanel({target,session}:{target:HexTarget;session:Session}){
  const [offset,setOffset]=useState(0);
  const [pageSize,setPageSize]=useState(4096);
  const [page,setPage]=useState<SuiteRaw|null>(null);
  const [error,setError]=useState('');
  const [jump,setJump]=useState('');
  const epoch=useRef(0);
  const targetKey=JSON.stringify(target);
  const revision=session.state?.revision;
  const {track}=session;

  useEffect(()=>{setOffset(0);setJump('');},[targetKey]);
  useEffect(()=>{
    if(revision===undefined)return;
    const ticket=++epoch.current;
    const request={...(target.scope==='file'?{scope:'file'}:target.tagPath&&target.tagPath.length>1?{tag_path:target.tagPath}:{tag_index:target.tagIndex??target.tagPath?.[0]}),offset,limit:pageSize};
    track(api.suiteRpc<SuiteRaw>('raw',request)).then(next=>{if(ticket===epoch.current){setPage(next);setError('');}})
      .catch(failure=>{if(ticket===epoch.current){setPage(null);setError(problem(failure));}});
    return()=>{epoch.current++;};
  // target is covered by targetKey
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[targetKey,offset,pageSize,revision,track]);

  const bytes=useMemo(()=>page?.hex!==undefined?bytesOf(page.hex):null,[page]);
  const dump=useMemo(()=>bytes&&page?hexdump(bytes,page.offset):'',[bytes,page]);
  const total=page?.total??0;
  const last=Math.max(0,Math.floor((Math.max(total,1)-1)/pageSize)*pageSize);
  const go=(value:number)=>setOffset(Math.max(0,Math.min(last,Math.floor(value/pageSize)*pageSize)));
  const submitJump=()=>{const value=Number(jump.trim());if(Number.isFinite(value)&&value>=0)setOffset(Math.max(0,Math.min(Math.floor(value/16)*16,Math.max(0,total-1))));};
  const copy=()=>{if(page?.hex)void navigator.clipboard?.writeText(page.hex.replace(/(..)/g,'$1 ').trim()).then(()=>session.notify('Bytes de la página copiados.'),()=>session.notify('No se pudo copiar al portapapeles.',true));};

  return <div className="hex-panel">
    <div className="panel-toolbar">
      <button type="button" className="icon-button" aria-label="Primera página" disabled={offset===0} onClick={()=>go(0)}><ChevronsLeft size={15}/></button>
      <button type="button" className="icon-button" aria-label="Página anterior" disabled={offset===0} onClick={()=>go(offset-pageSize)}><ChevronLeft size={15}/></button>
      <span className="toolbar-text" aria-live="polite">{page?`${page.offset.toLocaleString()}–${Math.max(page.offset,page.offset+page.length-1).toLocaleString()} de ${total.toLocaleString()} bytes`:'—'}</span>
      <button type="button" className="icon-button" aria-label="Página siguiente" disabled={offset>=last} onClick={()=>go(offset+pageSize)}><ChevronRight size={15}/></button>
      <button type="button" className="icon-button" aria-label="Última página" disabled={offset>=last} onClick={()=>go(last)}><ChevronsRight size={15}/></button>
      <span className="toolbar-gap"/>
      <label className="toolbar-field">Ir a<input value={jump} onChange={event=>setJump(event.target.value)} onKeyDown={event=>{if(event.key==='Enter')submitJump();}} placeholder="0x0" aria-label="Desplazamiento al que ir" size={8}/></label>
      <label className="toolbar-field">Página<select value={pageSize} onChange={event=>{setPageSize(Number(event.target.value));setOffset(0);}} aria-label="Tamaño de página">{PAGE_SIZES.map(size=><option key={size} value={size}>{size>=1024?`${size/1024} KiB`:size}</option>)}</select></label>
      <button type="button" className="tool-button" disabled={!page?.hex} onClick={copy}><Copy size={14}/>Copiar hex</button>
    </div>
    {error?<p className="panel-error" role="alert">{error}</p>:null}
    <pre className="hex-dump" data-testid="hex-dump" aria-label={page?`Bytes de ${page.name}`:'Bytes'} tabIndex={0}>{dump||(page&&!page.length?'(sin contenido)':'')}</pre>
  </div>;
}
