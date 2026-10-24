import {KeyboardEvent,RefObject,createContext,useContext,useLayoutEffect,useRef,useState} from 'react';
import {Tip} from './model';

/**
 * The engine under the pointer (or pinned in a legend) in any chart: every chart dims the other engines, so one engine
 * can be followed through the whole page. Marks report it through `set`; legends also pin it.
 */
export interface FocusState {engine:string|null;set:(engine:string|null)=>void;pinned:string|null;pin:(engine:string|null)=>void}
export const FocusContext=createContext<FocusState>({engine:null,set:()=>undefined,pinned:null,pin:()=>undefined});
export const useFocus=()=>useContext(FocusContext);

/** Width of an element in CSS pixels, kept current: charts are drawn at their real size, so their text never scales. */
export function useWidth(initial=640):[RefObject<HTMLDivElement|null>,number]{
  const ref=useRef<HTMLDivElement>(null);
  const [width,setWidth]=useState(initial);
  useLayoutEffect(()=>{
    const element=ref.current;
    if(!element)return;
    const read=()=>setWidth(Math.max(240,Math.round(element.clientWidth)));
    read();
    if(typeof ResizeObserver==='undefined')return;
    const observer=new ResizeObserver(read);
    observer.observe(element);
    return()=>observer.disconnect();
  },[]);
  return [ref,width];
}

export interface TipAt extends Tip {x:number;y:number}
/** The value of a mark, in words, next to it. It repeats what the mark's own label says, so it is hidden from screen readers. */
export function Tooltip({tip}:{tip:TipAt|null}){
  if(!tip)return null;
  return <div className="bench-tip" aria-hidden="true" style={{left:tip.x,top:tip.y}}>
    <span className="v">{tip.value}</span>
    <span className="t">{tip.title}</span>
    {tip.lines.map(line=><span key={line} className="l">{line}</span>)}
  </div>;
}

/** Arrow keys, Home and End move between the marks of a chart (the marks carry data-mark); the chart is one tab stop. */
export function moveBetweenMarks(event:KeyboardEvent<Element>){
  const forward=event.key==='ArrowDown'||event.key==='ArrowRight';
  const back=event.key==='ArrowUp'||event.key==='ArrowLeft';
  if(!forward&&!back&&event.key!=='Home'&&event.key!=='End')return;
  const marks=Array.from(event.currentTarget.querySelectorAll<SVGElement>('[data-mark]'));
  const at=marks.indexOf(document.activeElement as SVGElement);
  if(at<0)return;
  event.preventDefault();
  const next=event.key==='Home'?0:event.key==='End'?marks.length-1:at+(forward?1:-1);
  marks[Math.max(0,Math.min(marks.length-1,next))]?.focus();
}

/** A bar that grows from the baseline: square where it starts, rounded at its end. */
export function barPath(x:number,y:number,width:number,height:number,radius=4){
  if(!(width>0))return '';
  const r=Math.min(radius,width,height/2);
  return `M${x},${y}h${width-r}a${r},${r} 0 0 1 ${r},${r}v${height-2*r}a${r},${r} 0 0 1 ${-r},${r}h${-(width-r)}z`;
}

/** The colour key of a chart, doubling as the control that pins an engine across every chart. */
export function Legend({items}:{items:{engine:string;label:string;slot:number;reference?:boolean}[]}){
  const {pinned,pin,set}=useFocus();
  return <ul className="bench-legend" aria-label="Motores de esta sección">
    {items.map(item=><li key={item.engine}>
      <button type="button" aria-pressed={pinned===item.engine} title="Resaltar este motor en todos los gráficos"
        onClick={()=>pin(pinned===item.engine?null:item.engine)} onPointerEnter={()=>set(item.engine)} onPointerLeave={()=>set(null)}
        onFocus={()=>set(item.engine)} onBlur={()=>set(null)}>
        <i className={`bench-dot s${item.slot}`} aria-hidden="true"/>{item.label}{item.reference?<small title="JPEXS es la referencia de la cercanía y la cobertura; en lo demás compite como un motor más">ref. de cercanía</small>:null}
      </button>
    </li>)}
  </ul>;
}
