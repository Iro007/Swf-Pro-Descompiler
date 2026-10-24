import {useId,useState} from 'react';
import {BarItem,Chart,Unit,tickLabel,valueLabel} from './model';
import {TipAt,Tooltip,barPath,moveBetweenMarks,useFocus,useWidth} from './shared';

type Placed={kind:'bar';bar:BarItem;y:number;h:number;at:number}|{kind:'divider';label:string;y:number};

/**
 * Horizontal bars, best first, one engine per row: name on the left, value at the tip of the bar. The colour of an
 * engine is the same in every chart; where JPEXS is the reference (closeness and coverage) its bar is hatched. Hover or focus a row for the exact value and the
 * number of cases; the same figures are in the table under the charts.
 */
export default function BarChart({title,chart,unit,lowerIsBetter,size='sm'}:{title:string;chart:Chart;unit:Unit;lowerIsBetter?:boolean;size?:'lg'|'sm'}){
  const [ref,width]=useWidth();
  const {engine:focus,set}=useFocus();
  const id=useId().replace(/:/g,'');
  const [tip,setTip]=useState<TipAt|null>(null);
  const [stop,setStop]=useState(0);
  const large=size==='lg';
  const pitch=large?40:30,thick=large?22:14,dividerHeight=32,top=6;
  const labelWidth=Math.min(large?176:150,Math.max(84,Math.round(width*0.3)));
  const rankWidth=large?28:0;
  const x0=rankWidth+labelWidth,x1=Math.max(x0+40,width-(unit==='seconds'?64:54));
  const [low,high]=chart.domain;
  const scale=(value:number)=>x0+(x1-x0)*Math.min(1,Math.max(0,(value-low)/(high-low)));

  const placed:Placed[]=[];
  let y=top,at=0;
  for(const group of chart.groups){
    if(group.label){placed.push({kind:'divider',label:group.label,y});y+=dividerHeight;}
    for(const bar of group.items){const h=bar.sub?pitch+8:pitch;placed.push({kind:'bar',bar,y,h,at:at++});y+=h;}
  }
  const bottom=y,height=bottom+26,tabStop=Math.min(stop,Math.max(0,at-1));
  const hatched=[...new Set(chart.groups.flatMap(group=>group.items).filter(bar=>bar.reference).map(bar=>bar.slot))];
  const summary=`${lowerIsBetter?'Menos es mejor':'Más es mejor'}. ${chart.groups.flatMap(group=>group.items).map(bar=>`${bar.label} ${bar.value===null?'sin datos':valueLabel(unit,bar.value)}`).join('; ')}.`;

  return <div className="bench-chart" ref={ref}>
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-labelledby={`${id}t`} aria-describedby={`${id}d`} onKeyDown={moveBetweenMarks}>
      <title id={`${id}t`}>{title}</title>
      <desc id={`${id}d`}>{summary}</desc>
      <defs>
        {hatched.map(slot=><pattern key={slot} id={`${id}h${slot}`} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect className="hatch-bg" width="7" height="7"/><line className={`hatch-line s${slot}`} x1="1.5" y1="0" x2="1.5" y2="7"/>
        </pattern>)}
      </defs>
      {chart.ticks.map(tick=>{
        const x=scale(tick);
        return <g key={tick}><line className={tick===low?'bc-base':'bc-grid'} x1={x} x2={x} y1={top-2} y2={bottom}/><text className="bc-tick" x={x} y={bottom+17} textAnchor="middle">{tickLabel(unit,tick)}</text></g>;
      })}
      {placed.map(item=>{
        if(item.kind==='divider')return <g key={`d${item.y}`}><line className="bc-grid" x1={0} x2={width} y1={item.y+6} y2={item.y+6}/><text className="bc-divider" x={0} y={item.y+23}>{item.label}</text></g>;
        const {bar,y:rowY,h:rowH,at:index}=item;
        const cy=rowY+rowH/2;
        const end=bar.value===null?x0:Math.max(x0+(bar.value>0?2:0),scale(bar.value));
        const show=()=>{set(bar.engine);setTip({...bar.tip,x:Math.max(4,Math.min(end+14,width-250)),y:rowY+rowH-6});};
        const hide=()=>{set(null);setTip(null);};
        return <g key={bar.key} data-mark="" data-engine={bar.engine} role="img" aria-label={bar.aria} tabIndex={index===tabStop?0:-1}
          className={`bc-row${bar.muted?' muted':''}${focus&&focus!==bar.engine?' dim':''}`}
          onPointerEnter={show} onPointerLeave={hide} onFocus={()=>{setStop(index);show();}} onBlur={hide}>
          <rect className="bc-hit" x={0} y={rowY} width={width} height={rowH}/>
          <rect className="bc-ring" x={1} y={rowY+1} width={width-2} height={rowH-2} rx={6}/>
          {bar.rank!==null&&large?<text className="bc-rank" x={rankWidth-8} y={cy} textAnchor="end" dominantBaseline="central">{bar.rank}</text>:null}
          <text className="bc-name" x={x0-10} y={bar.sub?cy-6:cy} textAnchor="end" dominantBaseline="central">{bar.label}{bar.reference?<tspan className="tag"> ref.</tspan>:null}</text>
          {bar.sub?<text className="bc-sub" x={x0-10} y={cy+8} textAnchor="end" dominantBaseline="central">{bar.sub}</text>:null}
          {bar.value===null
            ?<text className="bc-none" x={x0+8} y={cy} dominantBaseline="central">sin datos en esta corrida</text>
            :<>
              <path className={`bc-bar${bar.reference?' ref':` s${bar.slot}`}`} fill={bar.reference?`url(#${id}h${bar.slot})`:undefined} d={barPath(x0,cy-thick/2,end-x0,thick)}/>
              <text className="bc-value" x={end+8} y={cy} dominantBaseline="central">{valueLabel(unit,bar.value)}</text>
            </>}
        </g>;
      })}
    </svg>
    <Tooltip tip={tip}/>
  </div>;
}
