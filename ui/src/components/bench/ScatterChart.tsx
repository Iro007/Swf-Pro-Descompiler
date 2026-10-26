import {PointerEvent,useId,useState} from 'react';
import {Dot,secondsLabel,tickLabel} from './model';
import {TipAt,Tooltip,moveBetweenMarks,useFocus,useWidth} from './shared';

/** The range of the data with a margin, and the ticks inside it at 1, 2 and 5 of every power of ten (only the powers of ten when narrow). */
function logScale(min:number,max:number,coarse:boolean){
  const low=min/1.3,high=max*1.3;
  const ticks:number[]=[];
  for(let power=Math.floor(Math.log10(low));power<=Math.ceil(Math.log10(high));power+=1)
    for(const factor of coarse?[1]:[1,2,5]){const tick=Number((factor*10**power).toPrecision(6));if(tick>=low&&tick<=high)ticks.push(tick);}
  return {low,high,ticks};
}

interface Spot {dot:Dot;px:number;py:number}
interface Label {x:number;y:number;anchor:'start'|'middle'|'end'}

/** A label beside its dot where it overlaps neither another label, nor another dot, nor the edge of the plot. */
function placeLabels(spots:Spot[],left:number,right:number,top:number,bottom:number){
  const boxes:{x0:number;x1:number;y0:number;y1:number}[]=[];
  const hits=(box:{x0:number;x1:number;y0:number;y1:number})=>box.x0<left||box.x1>right||box.y0<top||box.y1>bottom
    ||boxes.some(other=>box.x0<other.x1&&box.x1>other.x0&&box.y0<other.y1&&box.y1>other.y0)
    ||spots.some(spot=>box.x0<spot.px+9&&box.x1>spot.px-9&&box.y0<spot.py+9&&box.y1>spot.py-9);
  return spots.map(({dot,px,py}):Label=>{
    const w=dot.label.length*6.6+4;
    const options:(Label&{x0:number;y0:number})[]=[
      {x:px+11,y:py,anchor:'start',x0:px+11,y0:py-8},{x:px-11,y:py,anchor:'end',x0:px-11-w,y0:py-8},
      {x:px,y:py-18,anchor:'middle',x0:px-w/2,y0:py-26},{x:px,y:py+18,anchor:'middle',x0:px-w/2,y0:py+10}];
    const box=(option:{x0:number;y0:number})=>({x0:option.x0,x1:option.x0+w,y0:option.y0,y1:option.y0+16});
    const chosen=options.find(option=>!hits(box(option)))??options[0];
    boxes.push(box(chosen));
    return {x:chosen.x,y:chosen.y,anchor:chosen.anchor};
  });
}

/**
 * Closeness to JPEXS (up) against seconds per case (right, logarithmic): the engines at the top left are the most
 * similar to the reference and the fastest. Every dot carries the name of its engine; the dot nearest the pointer answers.
 */
export default function ScatterChart({title,yAxisLabel='Fidelidad (Índice Flash / Oráculo Ruffle)',dots}:{title:string;yAxisLabel?:string;dots:Dot[]}){
  const [ref,width]=useWidth();
  const {engine:focus,set}=useFocus();
  const id=useId().replace(/:/g,'');
  const [tip,setTip]=useState<TipAt|null>(null);
  const [stop,setStop]=useState(0);
  const left=52,right=width-22,top=14,height=Math.min(380,Math.max(270,Math.round(width*0.5))),bottom=height-52;
  const scale=logScale(Math.min(...dots.map(dot=>dot.x)),Math.max(...dots.map(dot=>dot.x)),width<460);
  const sx=(value:number)=>left+(right-left)*(Math.log10(value)-Math.log10(scale.low))/(Math.log10(scale.high)-Math.log10(scale.low));
  const sy=(value:number)=>bottom-(bottom-top)*value/100;
  const spots:Spot[]=dots.map(dot=>({dot,px:sx(dot.x),py:sy(dot.y)}));
  const labels=placeLabels(spots,left-4,width-2,top-4,bottom);

  const show=(spot:Spot)=>{set(spot.dot.engine);setTip({...spot.dot.tip,x:Math.max(4,Math.min(spot.px+14,width-250)),y:Math.min(spot.py+14,height-96)});};
  const hide=()=>{set(null);setTip(null);};
  const nearest=(event:PointerEvent<SVGRectElement>)=>{
    const box=event.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const x=event.clientX-box.left,y=event.clientY-box.top;
    const best=spots.map(spot=>({spot,distance:Math.hypot(spot.px-x,spot.py-y)})).sort((a,b)=>a.distance-b.distance)[0];
    if(best&&best.distance<=48)show(best.spot);else hide();
  };
  const summary=`Cada punto es un motor. ${dots.map(dot=>`${dot.label}: fidelidad ${Math.round(dot.y)}/100, ${secondsLabel(dot.x)} por caso`).join('; ')}.`;

  return <div className="bench-chart" ref={ref}>
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-labelledby={`${id}t`} aria-describedby={`${id}d`} onKeyDown={moveBetweenMarks}>
      <title id={`${id}t`}>{title}</title>
      <desc id={`${id}d`}>{summary}</desc>
      {[0,25,50,75,100].map(tick=><g key={tick}>
        <line className={tick===0?'bc-base':'bc-grid'} x1={left} x2={right} y1={sy(tick)} y2={sy(tick)}/>
        <text className="bc-tick" x={left-8} y={sy(tick)} textAnchor="end" dominantBaseline="central">{tick}%</text>
      </g>)}
      {scale.ticks.map(tick=><g key={tick}>
        <line className="bc-grid" x1={sx(tick)} x2={sx(tick)} y1={top} y2={bottom}/>
        <text className="bc-tick" x={sx(tick)} y={bottom+16} textAnchor="middle">{tickLabel('seconds',tick)}</text>
      </g>)}
      <text className="bc-axis" x={(left+right)/2} y={height-8} textAnchor="middle">Segundos por caso (mediana, escala logarítmica): más a la izquierda, más rápido</text>
      <text className="bc-axis" transform={`rotate(-90 12 ${(top+bottom)/2})`} x={12} y={(top+bottom)/2} textAnchor="middle">{yAxisLabel}</text>
      <rect className="bc-hit" x={left} y={top} width={right-left} height={bottom-top} onPointerMove={nearest} onPointerLeave={hide}/>
      {spots.map((spot,index)=>{
        const {dot}=spot,label=labels[index];
        return <g key={dot.key} data-mark="" data-engine={dot.engine} role="img" aria-label={dot.aria} tabIndex={index===Math.min(stop,spots.length-1)?0:-1}
          className={`sc-dot${dot.muted?' muted':''}${focus&&focus!==dot.engine?' dim':''}`}
          onFocus={()=>{setStop(index);show(spot);}} onBlur={hide}>
          <circle className="sc-halo" cx={spot.px} cy={spot.py} r={14}/>
          {dot.reference
            ?<path className={`sc-ref s${dot.slot}`} d={`M${spot.px},${spot.py-8}L${spot.px+8},${spot.py}L${spot.px},${spot.py+8}L${spot.px-8},${spot.py}Z`}/>
            :<circle className={`sc-mark${dot.muted?' hollow':''} s${dot.slot}`} cx={spot.px} cy={spot.py} r={6.5}/>}
          <text className="sc-label" x={label.x} y={label.y} textAnchor={label.anchor} dominantBaseline="central">{dot.label}{dot.reference?' (ref.)':''}</text>
        </g>;
      })}
    </svg>
    <Tooltip tip={tip}/>
  </div>;
}
