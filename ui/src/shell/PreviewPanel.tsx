import {Suspense,lazy,useEffect,useMemo,useRef,useState} from 'react';
import {ChevronLeft,ChevronRight,Play,Square} from 'lucide-react';
import * as api from '../api';
import {SuitePreview,SuiteTimeline} from '../types';
import PreviewSurface from '../components/PreviewSurface';
import {Session} from './useSession';
import {PreviewPayload,usePreviewUrl} from './svg';
import {bytesText,problem} from './util';

const RufflePreview=lazy(()=>import('../components/RufflePreview'));

export type PreviewSource=
  |{type:'character';tagIndex:number;name:string;tagType:number}
  |{type:'frame';spriteIndex?:number;frame:number}
  |{type:'movie'};

interface Loaded {payload:PreviewPayload;frameCount?:number;note:string}

/** Static picture of the selection (image, vector, sprite frame, timeline frame) and, on request, the movie in Ruffle. */
export default function PreviewPanel({source,session}:{source:PreviewSource;session:Session}){
  const timeline=source.type==='movie'||(source.type==='frame'&&source.spriteIndex===undefined);
  const animated=source.type==='movie'||source.type==='frame'||(source.type==='character'&&source.tagType===39);
  const morphing=source.type==='character'&&(source.tagType===46||source.tagType===84);
  const sourceKey=JSON.stringify(source);
  const initialFrame=source.type==='frame'?source.frame:1;
  const [frame,setFrame]=useState(initialFrame);
  const [percent,setPercent]=useState(0);
  const [loaded,setLoaded]=useState<Loaded|null>(null);
  const [error,setError]=useState('');
  const [movie,setMovie]=useState<{data:string;filename:string;sha256:string}|null>(null);
  const [playing,setPlaying]=useState(false);
  const epoch=useRef(0);
  const {state,track}=session;
  const revision=state?.revision;

  useEffect(()=>{setFrame(initialFrame);setPercent(0);setPlaying(false);setMovie(null);},[sourceKey,initialFrame]);
  useEffect(()=>{
    if(revision===undefined)return;
    const ticket=++epoch.current;
    const timer=setTimeout(()=>{
      let request:Promise<Loaded>;
      if(timeline){
        request=api.suiteRpc<SuiteTimeline>('timeline',{frame:Math.max(0,frame-1)}).then(result=>{
          if(!result.svg)throw new Error('Este cuadro no tiene una composición estática disponible.');
          return {payload:{mime:'image/svg+xml',encoding:'text' as const,data:result.svg},frameCount:result.frame_count,note:`${result.display_list.length} objetos en la lista de visualización · ActionScript sin ejecutar`};
        });
      }else{
        const tagIndex=source.type==='character'?source.tagIndex:source.type==='frame'?source.spriteIndex!:0;
        request=api.suiteRpc<SuitePreview>('resource_preview',{tag_index:tagIndex,...(animated?{frame:Math.max(0,frame-1)}:{}),...(morphing?{ratio:Math.round(percent/100*65535)}:{})}).then(result=>({
          payload:{mime:result.mime,encoding:result.encoding,data:result.data},frameCount:result.frame_count,
          note:[result.width&&result.height?`${result.width} × ${result.height} px`:'',result.encoding==='base64'?bytesText(Math.floor(result.data.length*3/4)):'',result.glyphs?`${result.glyphs} glifos`:''].filter(Boolean).join(' · '),
        }));
      }
      track(request).then(next=>{if(ticket===epoch.current){setLoaded(next);setError('');}})
        .catch(failure=>{if(ticket===epoch.current){setLoaded(null);setError(problem(failure));}});
    },morphing?150:0);
    return()=>{clearTimeout(timer);epoch.current++;};
  // source is covered by sourceKey
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[sourceKey,frame,percent,revision,track]);

  const url=usePreviewUrl(loaded?.payload);
  const count=loaded?.frameCount??1;
  const label=source.type==='character'?source.name:source.type==='frame'?`Cuadro ${frame}`:'Película';
  const canPlay=animated;

  const play=async()=>{
    const data=await session.run('Preparando la película para Ruffle…',()=>api.suiteRpc<{data:string;filename:string;sha256:string}>('preview_data'));
    if(data){setMovie(data);setPlaying(true);}
  };
  const stop=()=>{setPlaying(false);setMovie(null);};
  const clampFrame=useMemo(()=>(value:number)=>Math.max(1,Math.min(count,Math.floor(value)||1)),[count]);

  return <div className="preview-panel">
    <div className="panel-toolbar">
      {animated?<>
        <button type="button" className="icon-button" aria-label="Cuadro anterior" disabled={frame<=1} onClick={()=>setFrame(clampFrame(frame-1))}><ChevronLeft size={15}/></button>
        <label className="toolbar-field">Cuadro<input type="number" min={1} max={count} value={frame} onChange={event=>setFrame(clampFrame(Number(event.target.value)))} aria-label="Número de cuadro" size={5}/></label>
        <span className="toolbar-text">de {count}</span>
        <button type="button" className="icon-button" aria-label="Cuadro siguiente" disabled={frame>=count} onClick={()=>setFrame(clampFrame(frame+1))}><ChevronRight size={15}/></button>
      </>:null}
      {morphing?<label className="toolbar-field">Interpolación<input type="range" min={0} max={100} value={percent} onChange={event=>setPercent(Number(event.target.value))} aria-label="Posición de la interpolación"/><span className="toolbar-text">{percent} %</span></label>:null}
      <span className="toolbar-text dim" data-testid="preview-note">{playing&&movie?`Ejecutando ActionScript con Ruffle · SHA-256 ${movie.sha256.slice(0,12)}…`:loaded?.note}</span>
      <span className="toolbar-gap"/>
      {canPlay?(playing?<button type="button" className="tool-button" onClick={stop}><Square size={13}/>Detener</button>
        :<button type="button" className="tool-button" disabled={!!session.busy} onClick={()=>void play()}><Play size={13}/>Reproducir película (Ruffle)</button>):null}
    </div>
    <div className="preview-stage">
      {playing&&movie?<Suspense fallback={<p className="panel-muted">Cargando reproductor local…</p>}>
        <RufflePreview data={movie.data} filename={movie.filename}/>
      </Suspense>
      :error?<p className="panel-error" role="alert">{error}</p>
      :url?<PreviewSurface src={url} alt={`Vista previa de ${label}`} pixelated={loaded?.payload.mime!=='image/svg+xml'} onError={()=>setError('El navegador no pudo dibujar esta vista previa.')}/>
      :<p className="panel-muted">Preparando vista previa…</p>}
    </div>
  </div>;
}
