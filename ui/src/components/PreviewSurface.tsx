import {useEffect,useRef,useState} from 'react';
import {Contrast,Maximize2,Scan,ZoomIn,ZoomOut} from 'lucide-react';

interface Props {
  src:string;
  alt:string;
  onError?:()=>void;
  /** Bitmaps are shown with hard pixels when enlarged; vectors stay smooth. */
  pixelated?:boolean;
}

const STEPS=[0.1,0.125,0.25,0.5,0.75,1,1.5,2,3,4,6,8,12,16];
const BACKGROUNDS=['checker','light','dark'] as const;

/** Image viewer: real size, fit-to-window, zoom steps and a background that shows transparency. */
export default function PreviewSurface({src,alt,onError,pixelated=false}:Props) {
  const [zoom,setZoom]=useState<number|'fit'>('fit');
  const [natural,setNatural]=useState<{width:number;height:number}|null>(null);
  const [room,setRoom]=useState({width:0,height:0});
  const [background,setBackground]=useState<(typeof BACKGROUNDS)[number]>('checker');
  const viewport=useRef<HTMLDivElement>(null);

  useEffect(()=>{setNatural(null);},[src]);
  useEffect(()=>{
    const element=viewport.current;if(!element)return;
    const measure=()=>setRoom({width:element.clientWidth,height:element.clientHeight});
    measure();
    if(typeof ResizeObserver==='undefined')return;
    const observer=new ResizeObserver(measure);observer.observe(element);return()=>observer.disconnect();
  },[]);

  // "Fit" shrinks large images into the window but never enlarges small ones beyond their real size.
  const fit=natural&&room.width>0&&room.height>0?Math.min(1,(room.width-40)/natural.width,(room.height-40)/natural.height):1;
  const scale=zoom==='fit'?Math.max(0.02,fit):zoom;
  const step=(direction:1|-1)=>{
    const next=direction>0?STEPS.find(value=>value>scale+1e-6):[...STEPS].reverse().find(value=>value<scale-1e-6);
    setZoom(next??scale);
  };
  const percent=Math.round(scale*100);

  return (
    <div className="preview-surface">
      <div className="preview-tools">
        <span>VISTA PREVIA{natural?` · ${natural.width} × ${natural.height}`:''}</span>
        <div>
          <button type="button" className="icon-button" aria-label="Reducir zoom" disabled={scale<=STEPS[0]} onClick={()=>step(-1)}><ZoomOut size={14}/></button>
          <output aria-label="Nivel de zoom">{percent}%</output>
          <button type="button" className="icon-button" aria-label="Ampliar zoom" disabled={scale>=STEPS[STEPS.length-1]} onClick={()=>step(1)}><ZoomIn size={14}/></button>
          <button type="button" className="icon-button" aria-label="Tamaño real" title="Tamaño real (100 %)" onClick={()=>setZoom(1)}><Maximize2 size={14}/></button>
          <button type="button" className="icon-button" aria-label="Ajustar a la ventana" title="Ajustar a la ventana" aria-pressed={zoom==='fit'} onClick={()=>setZoom('fit')}><Scan size={14}/></button>
          <button type="button" className="icon-button" aria-label="Cambiar fondo de vista previa" title="Transparencia / claro / oscuro" onClick={()=>setBackground(current=>BACKGROUNDS[(BACKGROUNDS.indexOf(current)+1)%BACKGROUNDS.length])}><Contrast size={14}/></button>
        </div>
      </div>
      <div ref={viewport} className={`preview-viewport background-${background}`}>
        <div className="preview-image-wrap">
          <img src={src} alt={alt} onError={onError}
            onLoad={event=>setNatural({width:event.currentTarget.naturalWidth||1,height:event.currentTarget.naturalHeight||1})}
            style={natural?{width:natural.width*scale,height:natural.height*scale,maxWidth:'none',imageRendering:pixelated&&scale>=2?'pixelated':'auto'}:{maxWidth:'100%'}}/>
        </div>
      </div>
    </div>
  );
}
