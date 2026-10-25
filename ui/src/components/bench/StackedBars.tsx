import {useId,useState} from 'react';
import {Stack,StackSpec} from './model';
import {TipAt,Tooltip,moveBetweenMarks,useFocus,useWidth} from './shared';

type Placed={kind:'bar';bar:Stack['groups'][number]['items'][number];y:number;h:number;at:number}|{kind:'divider';label:string;y:number};

/** The colour key of a stacked chart: one swatch per way a case can end, in the order of the segments. */
export function StackLegend({specs}:{specs:StackSpec[]}){
  return <ul className="stack-legend" aria-label="Cómo terminó cada caso">
    {specs.map(spec=><li key={spec.id}><i className={`stack-swatch seg-${spec.id}`} aria-hidden="true"/>{spec.label}</li>)}
  </ul>;
}

/**
 * One horizontal bar per engine, as long as the whole sample (100 %), split by how each case ended: the segments are the
 * outcomes, in the order of `specs`, with their counts inside when they fit. The engine is named (and coloured) on the left, as
 * in the other charts; hover or focus a row for every count and its share. The same figures are in the table under the chart.
 */
export default function StackedBars({title,stack,specs}:{title:string;stack:Stack;specs:StackSpec[]}){
  const [ref,width]=useWidth();
  const {engine:focus,set}=useFocus();
  const id=useId().replace(/:/g,'');
  const [tip,setTip]=useState<TipAt|null>(null);
  const [stop,setStop]=useState(0);
  const pitch=38,thick=22,dividerHeight=32,top=6;
  const labelWidth=Math.min(150,Math.max(84,Math.round(width*0.3)));
  const x0=labelWidth,x1=Math.max(x0+60,width-64);
  const placed:Placed[]=[];
  let y=top,at=0;
  for(const group of stack.groups){
    if(group.label){placed.push({kind:'divider',label:group.label,y});y+=dividerHeight;}
    for(const bar of group.items){const h=bar.sub?pitch+8:pitch;placed.push({kind:'bar',bar,y,h,at:at++});y+=h;}
  }
  const bottom=y,height=bottom+4,tabStop=Math.min(stop,Math.max(0,at-1));
  const summary=stack.groups.flatMap(group=>group.items).map(bar=>`${bar.label}: ${bar.total?bar.segments.map(segment=>`${specs.find(spec=>spec.id===segment.id)?.short} ${segment.value}`).join(', '):'sin datos'}`).join('; ');

  return <div className="bench-chart" ref={ref}>
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-labelledby={`${id}t`} aria-describedby={`${id}d`} onKeyDown={moveBetweenMarks}>
      <title id={`${id}t`}>{title}</title>
      <desc id={`${id}d`}>{summary}.</desc>
      {placed.map(item=>{
        if(item.kind==='divider')return <g key={`d${item.y}`}><line className="bc-grid" x1={0} x2={width} y1={item.y+6} y2={item.y+6}/><text className="bc-divider" x={0} y={item.y+23}>{item.label}</text></g>;
        const {bar,y:rowY,h:rowH,at:index}=item;
        const cy=rowY+rowH/2;
        const show=()=>{set(bar.engine);setTip({...bar.tip,x:Math.max(4,Math.min(x1-120,width-250)),y:rowY+rowH-6});};
        const hide=()=>{set(null);setTip(null);};
        let cursor=x0;
        return <g key={bar.key} data-mark="" data-engine={bar.engine} role="img" aria-label={bar.aria} tabIndex={index===tabStop?0:-1}
          className={`bc-row${bar.muted?' muted':''}${focus&&focus!==bar.engine?' dim':''}`}
          onPointerEnter={show} onPointerLeave={hide} onFocus={()=>{setStop(index);show();}} onBlur={hide}>
          <rect className="bc-hit" x={0} y={rowY} width={width} height={rowH}/>
          <rect className="bc-ring" x={1} y={rowY+1} width={width-2} height={rowH-2} rx={6}/>
          <circle className={`sb-dot s${bar.slot}`} cx={7} cy={bar.sub?cy-6:cy} r={5}/>
          <text className="bc-name" x={19} y={bar.sub?cy-6:cy} dominantBaseline="central">{bar.label}</text>
          {bar.sub?<text className="bc-sub" x={19} y={cy+8} dominantBaseline="central">{bar.sub}</text>:null}
          {bar.total===0
            ?<text className="bc-none" x={x0+8} y={cy} dominantBaseline="central">sin datos en esta corrida</text>
            :<>
              {bar.segments.map((segment,index)=>{
                if(!segment.value)return null;
                const w=(x1-x0)*segment.value/bar.total;
                const x=cursor;
                cursor+=w;
                return <g key={segment.id}>
                  <rect className={`sb-seg seg-${segment.id}`} x={x} y={cy-thick/2} width={Math.max(0,w-(index<bar.segments.length-1?1:0))} height={thick} rx={index===0?3:0}/>
                  {w>=22?<text className={`sb-count${segment.id==='partial'||segment.id==='no_compile'?' dark':''}`} x={x+w/2} y={cy} textAnchor="middle" dominantBaseline="central">{segment.value}</text>:null}
                </g>;
              })}
              <text className="bc-value" x={x1+8} y={cy} dominantBaseline="central">{bar.total}</text>
            </>}
        </g>;
      })}
    </svg>
    <Tooltip tip={tip}/>
  </div>;
}
