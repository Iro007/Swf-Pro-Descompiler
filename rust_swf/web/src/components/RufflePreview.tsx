import {useEffect,useRef,useState} from 'react';

type PlayerAPI={load:(options:Record<string,unknown>)=>Promise<void>;suspend:()=>void;resume:()=>void;traceObserver:((message:string)=>void)|null};
type RuffleElement=HTMLElement&{ruffle:()=>PlayerAPI};
type RuffleGlobal={newest:()=>{createPlayer:()=>RuffleElement};config?:Record<string,unknown>};
let runtimePromise:Promise<RuffleGlobal>|null=null;
const runtimeWindow=()=>window as typeof window&{RufflePlayer?:RuffleGlobal};
const assetRoot=()=>new URL('./ruffle/',document.baseURI).href;

async function runtime():Promise<RuffleGlobal>{
  const installed=runtimeWindow().RufflePlayer;
  if(installed?.newest)return installed;
  if(!runtimePromise){
    runtimePromise=new Promise((resolve,reject)=>{
      const script=document.createElement('script');
      script.src=new URL('ruffle.js',assetRoot()).href;script.async=true;
      script.onload=()=>{const api=runtimeWindow().RufflePlayer;if(api?.newest)resolve(api);else reject(new Error('El runtime Ruffle no expuso su API.'));};
      script.onerror=()=>{script.remove();runtimePromise=null;reject(new Error('No se pudo cargar el runtime Ruffle local.'));};
      document.head.appendChild(script);
    });
  }
  return runtimePromise;
}

export default function RufflePreview({data,filename,onTrace}:{data:string;filename:string;onTrace?:(message:string)=>void}){
  const container=useRef<HTMLDivElement>(null);const player=useRef<RuffleElement|null>(null);const observer=useRef(onTrace);
  const [status,setStatus]=useState('Cargando Ruffle…');const [error,setError]=useState('');const [traces,setTraces]=useState<string[]>([]);const [paused,setPaused]=useState(false);
  useEffect(()=>{observer.current=onTrace;},[onTrace]);
  useEffect(()=>{
    let cancelled=false;setError('');setTraces([]);setPaused(false);setStatus('Cargando Ruffle…');
    void(async()=>{
      try{
        if(!data||data.length>140_000_000)throw new Error('La vista previa necesita un SWF dentro del límite de 100 MB.');
        const binary=atob(data);const bytes=Uint8Array.from(binary,char=>char.charCodeAt(0));
        const api=await runtime();if(cancelled||!container.current)return;
        const element=api.newest().createPlayer();element.style.width='100%';element.style.height='100%';element.setAttribute('aria-label',`Película Flash ${filename}`);
        player.current=element;container.current.replaceChildren(element);
        const trace=(message:string)=>{if(cancelled)return;setTraces(previous=>[...previous.slice(-199),message]);observer.current?.(message);};
        element.addEventListener('loadedmetadata',()=>{if(!cancelled)element.ruffle().traceObserver=trace;},{once:true});
        await element.ruffle().load({data:bytes,swfFileName:filename,publicPath:assetRoot(),polyfills:false,autoplay:'off',allowNetworking:'none',allowScriptAccess:false,openUrlMode:'deny',allowFullscreen:false,splashScreen:false,unmuteOverlay:'hidden',maxExecutionDuration:10});
        if(!cancelled){element.ruffle().traceObserver=trace;element.ruffle().resume();setStatus('Reproduciendo con Ruffle');}
      }catch(cause){if(!cancelled){setError(String(cause));setStatus('Ruffle no pudo iniciar esta película');}}
    })();
    return()=>{cancelled=true;try{player.current?.ruffle().suspend();}catch{}player.current?.remove();player.current=null;};
  },[data,filename]);
  const toggle=()=>{const api=player.current?.ruffle();if(!api)return;paused?api.resume():api.suspend();setPaused(!paused);};
  return <section aria-label="Vista previa Flash" style={{display:'grid',gap:12}}><div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}><strong>Ruffle · {filename}</strong><button type="button" className="studio-button secondary" onClick={toggle} disabled={Boolean(error)||!player.current}>{paused?'Continuar':'Pausar'}</button></div><div role="status">{status}</div>{error?<p role="alert">{error}</p>:null}<div ref={container} style={{width:'100%',height:480,minHeight:240,background:'#111827',borderRadius:12,overflow:'hidden'}}/>{traces.length?<details><summary>Salida ActionScript ({traces.length})</summary><pre style={{maxHeight:200,overflow:'auto',whiteSpace:'pre-wrap'}}>{traces.join('\n')}</pre></details>:null}<small>La compatibilidad depende del runtime Ruffle. Las solicitudes de red y el acceso JavaScript de la película están desactivados.</small></section>;
}
