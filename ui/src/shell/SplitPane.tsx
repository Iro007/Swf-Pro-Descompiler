import {KeyboardEvent,PointerEvent,ReactNode,useCallback,useRef,useState} from 'react';
import {readStored,writeStored} from './util';

interface Props {
  first:ReactNode;second:ReactNode;
  /** localStorage key that remembers the size of the first pane. */
  storageKey:string;
  /** 'px': the first pane keeps a fixed width; 'ratio': it keeps a share of the container. */
  unit:'px'|'ratio';
  initial:number;min:number;max:number;
  label:string;className?:string;
}

const clamp=(value:number,min:number,max:number)=>Math.min(max,Math.max(min,value));

/** Two panes with a draggable, keyboard-operable separator; the size survives restarts. */
export default function SplitPane({first,second,storageKey,unit,initial,min,max,label,className}:Props){
  const container=useRef<HTMLDivElement>(null);
  const [size,setSize]=useState(()=>{const stored=Number(readStored(storageKey));return clamp(Number.isFinite(stored)&&stored>0?stored:initial,min,max);});
  const [dragging,setDragging]=useState(false);
  const remember=useCallback((value:number)=>writeStored(storageKey,String(Math.round(unit==='ratio'?value*1000:value)/(unit==='ratio'?1000:1))),[storageKey,unit]);
  const fromPointer=(event:PointerEvent<HTMLDivElement>)=>{
    const box=container.current?.getBoundingClientRect();
    if(!box||box.width<=0)return size;
    const offset=event.clientX-box.left;
    return clamp(unit==='ratio'?offset/box.width:offset,min,max);
  };
  const onPointerDown=(event:PointerEvent<HTMLDivElement>)=>{event.currentTarget.setPointerCapture(event.pointerId);setDragging(true);};
  const onPointerMove=(event:PointerEvent<HTMLDivElement>)=>{if(dragging)setSize(fromPointer(event));};
  const onPointerUp=(event:PointerEvent<HTMLDivElement>)=>{if(!dragging)return;setDragging(false);const next=fromPointer(event);setSize(next);remember(next);};
  const onKeyDown=(event:KeyboardEvent<HTMLDivElement>)=>{
    const step=unit==='ratio'?0.02:16;
    let next=size;
    if(event.key==='ArrowLeft')next=size-step;else if(event.key==='ArrowRight')next=size+step;
    else if(event.key==='Home')next=min;else if(event.key==='End')next=max;else return;
    event.preventDefault();next=clamp(next,min,max);setSize(next);remember(next);
  };
  const reset=()=>{setSize(initial);remember(initial);};
  const basis=unit==='ratio'?`${size*100}%`:`${size}px`;
  return <div ref={container} className={`split ${dragging?'dragging':''} ${className??''}`}>
    <div className="split-first" style={{flexBasis:basis}}>{first}</div>
    <div className="split-bar" role="separator" aria-orientation="vertical" aria-label={label} aria-valuemin={Math.round(min*(unit==='ratio'?100:1))}
      aria-valuemax={Math.round(max*(unit==='ratio'?100:1))} aria-valuenow={Math.round(size*(unit==='ratio'?100:1))} tabIndex={0}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={()=>setDragging(false)} onKeyDown={onKeyDown} onDoubleClick={reset} title="Arrastra para cambiar el tamaño · doble clic para restablecer"/>
    <div className="split-second">{second}</div>
  </div>;
}
