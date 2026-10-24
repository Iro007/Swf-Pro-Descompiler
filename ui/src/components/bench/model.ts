import {BenchLine,BenchResults,BenchRun,BenchTable} from '../../types';
import {displayEngineName} from '../../displayNames';

/**
 * What the Benchmarks page draws, computed from `bench_results` and nothing else. Pure functions: formatting, one
 * metric per chart, who may be ranked against whom, the order of the bars and the highlight cards.
 * docs/BENCHMARKS_CODIGO.md defines the numbers (Índice Flash, medians, "measured on the same cases").
 */

export const categoryOrder=['decompilers','compilers','disassemblers','converters','extractors'];
export const categoryTitles:Record<string,string>={decompilers:'Descompiladores',compilers:'Compiladores',disassemblers:'Desensambladores',converters:'Conversores XML',extractors:'Extractores'};
export const categoryQuestions:Record<string,string>={
  decompilers:'¿Lo que emite recompila con un compilador independiente (Flex o Rascal) y Ruffle imprime la misma traza que el original?',
  compilers:'¿Compilan el proyecto del corpus y el SWF se comporta como se espera?',
  disassemblers:'¿Desensamblar y reensamblar conserva el comportamiento?',
  converters:'¿Pasar de SWF a XML y de vuelta a SWF conserva el comportamiento?',
  extractors:'¿Todos los recursos se extraen y se decodifican?'};

/* ---- names and colours ---- */

/** Short name for a chart label: the display name without its parenthesis ("AsmSWF (x86_64)" is "AsmSWF"). */
export const engineName=(line:{engine:string;name?:string})=>displayEngineName(line.engine,line.name).replace(/\s*\(.*\)\s*$/,'');

/**
 * Categorical slot (1 to 8, the --bench-N colours) of each engine: the same engine wears the same colour in every chart. The
 * eight decompilers take one each (JPEXS included: it competes like the others); the engines of other categories never share
 * a chart with a decompiler except PySWF, so the tools that only appear in one category reuse slots.
 */
const slots:Record<string,number>={native:1,rust:2,go:3,js:4,flare:5,pyflashkit:6,asm:7,jpexs:8,flex:7,rascal:6,haxe:2,rabcdasm:3,flasm:5,swfmill:7,swfextract:6,flex_swfdump:4,mtasc:4};
export const slotOf=(engine:string)=>slots[engine]??1+[...engine].reduce((sum,char)=>sum+char.charCodeAt(0),0)%8;

/* ---- numbers in Spanish ---- */

const NBSP='\u00a0';
const fixed=(value:number,digits:number)=>value.toLocaleString('es',{minimumFractionDigits:digits,maximumFractionDigits:digits});
export type Unit='percent'|'index'|'seconds';
export const plural=(count:number,one:string,many:string)=>`${count} ${count===1?one:many}`;
export const secondsLabel=(value:number)=>`${fixed(value,value<0.1?3:value<1?2:value<10?1:0)}${NBSP}s`;
/** Value on a bar: whole percentages, one decimal for the index. */
export const valueLabel=(unit:Unit,value:number)=>unit==='seconds'?secondsLabel(value):unit==='index'?fixed(value,1):`${fixed(value,0)}${NBSP}%`;
/** Axis tick: no trailing zeros (0 s, 0,5 s, 2 s). */
export const tickLabel=(unit:Unit,tick:number)=>unit==='seconds'?`${tick.toLocaleString('es',{maximumFractionDigits:3})}${NBSP}s`:unit==='percent'?`${tick}%`:`${tick}`;
/** The same value with the precision of the data, for tooltips and tables. */
export const exactLabel=(unit:Unit,value:number)=>unit==='percent'?`${fixed(value,1)}${NBSP}%`:valueLabel(unit,value);
const finite=(value:number|null|undefined)=>typeof value==='number'&&Number.isFinite(value)?value:null;
const percentOf=(count:number|undefined,cases:number)=>typeof count==='number'&&cases>0?100*count/cases:null;
const scaled=(ratio:number|null|undefined)=>{const value=finite(ratio);return value===null?null:100*value;};

/** Run ids are UTC time stamps (YYYYMMDD-HHMMSS); index entries of older runs carry no other date. */
export const runIdToIso=(id:string)=>{const m=/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/.exec(id);return m?`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`:null;};
export const dateLabel=(iso:string|null|undefined)=>{const date=iso?new Date(iso):null;return date&&!Number.isNaN(date.getTime())?date.toLocaleString('es',{dateStyle:'medium',timeStyle:'short'}):'';};
export const modeLabel=(mode:string)=>mode==='full'?'Completa':'Rápida';
export const runLabel=(run:BenchRun)=>[dateLabel(run.created_utc??runIdToIso(run.run_id))||run.run_id,modeLabel(run.mode),`${run.cases} casos`].join(' · ');

/* ---- metrics: one chart each ---- */

export interface Metric {id:string;title:string;unit:Unit;lowerIsBetter?:boolean;referenceIs100?:boolean;value:(line:BenchLine)=>number|null;note:string}
export const metrics:Record<string,Metric>={
  flash:{id:'flash',title:'Índice Flash',unit:'index',value:line=>finite(line.flash_index),
    note:'Media de cuatro porcentajes que se miden igual en todos los motores, JPEXS incluido: casos con fuente completo, casos que recompilan, casos con la misma traza y unidades del SWF con fuente (de 0 a 100). No usa la cercanía ni la cobertura de JPEXS. Solo se ordenan motores medidos sobre los mismos casos.'},
  trace:{id:'trace',title:'Misma traza',unit:'percent',value:line=>percentOf(line.same_trace,line.cases),
    note:'Porcentaje de casos en que el fuente emitido recompila y Ruffle imprime la misma traza que el SWF original. Es el criterio de comportamiento.'},
  recompiles:{id:'recompiles',title:'Recompila',unit:'percent',value:line=>percentOf(line.recompiles,line.cases),
    note:'Porcentaje de casos en que el fuente emitido compila con un compilador independiente (Flex para AS3, Rascal para AS2), aunque luego se comporte distinto.'},
  source:{id:'source',title:'Fuente completo',unit:'percent',value:line=>percentOf(line.source,line.cases),
    note:'Porcentaje de casos en que el motor emite fuente completo para todas las unidades del SWF, antes de recompilar.'},
  units:{id:'units',title:'Unidades con fuente',unit:'percent',value:line=>scaled(line.unit_coverage),
    note:'Media, por caso, de las unidades del propio SWF (clases, scripts y acciones de su inventario ABC y AVM1, no de otro descompilador) para las que el motor emitió fuente verificado, aunque el proyecto no llegara a recompilar.'},
  closeness:{id:'closeness',title:'Cercanía a JPEXS',unit:'percent',referenceIs100:true,value:line=>scaled(line.jpexs_closeness),
    note:'Similitud media del texto emitido con el de JPEXS, unidad por unidad (se ignoran comentarios, espacios y comillas). JPEXS es aquí la referencia: vale 100 % por definición y no puntúa. Mide parecido, no corrección: JPEXS también se equivoca.'},
  coverage:{id:'coverage',title:'Cobertura de JPEXS',unit:'percent',referenceIs100:true,value:line=>scaled(line.jpexs_coverage),
    note:'Fracción de las unidades de JPEXS (clases, scripts, acciones) que el motor también emitió con la misma identidad y el mismo bytecode. JPEXS es aquí la referencia: vale 100 % por definición.'},
  speed:{id:'speed',title:'Velocidad',unit:'seconds',lowerIsBetter:true,value:line=>finite(line.median_seconds),
    note:'Mediana de segundos por caso que tarda el motor en emitir el fuente, con su arranque incluido; no cuenta recompilar ni Ruffle. Menos es mejor. Un motor que falla pronto también sale rápido: léase junto al Índice Flash.'},
  ok:{id:'ok',title:'Casos correctos',unit:'percent',value:line=>percentOf(line.ok,line.cases),note:''},
};
/** The primary metric of a category: the Índice Flash for decompilers, the share of correct cases for the rest. */
export const primaryMetric=(category:string,table:BenchTable):Metric=>category==='decompilers'?metrics.flash:{...metrics.ok,note:`Porcentaje de ${table.unit}.`};

/* ---- who may be ranked against whom ---- */

export interface Classified {mode:'none'|'single'|'ranked'|'unranked';measured:BenchLine[];unmeasured:BenchLine[];ranked:BenchLine[];others:BenchLine[]}
/**
 * `ranked` are the engines measured on the same cases (two or more; the reference may be one of them); `others` were
 * measured on other cases and are shown apart. A run from before schema 2 says nothing about it, so there the engines
 * with the most common number of cases (they all saw the same ones) are the ranked group.
 */
export function classify(table:BenchTable):Classified{
  const measured=table.engines.filter(line=>line.cases>0);
  let ranked:BenchLine[];
  if(measured.some(line=>line.comparable!==undefined))ranked=measured.filter(line=>line.comparable===true);
  else{
    const groups=new Map<number,BenchLine[]>();
    for(const line of measured)groups.set(line.cases,[...(groups.get(line.cases)??[]),line]);
    ranked=[...groups.values()].sort((a,b)=>b.length-a.length||b[0].cases-a[0].cases)[0]??[];
  }
  if(ranked.length<2)ranked=[];
  const mode=!measured.length?'none':measured.length===1?'single':ranked.length?'ranked':'unranked';
  return {mode,measured,unmeasured:table.engines.filter(line=>!(line.cases>0)),ranked,others:measured.filter(line=>!ranked.includes(line))};
}

/* ---- bar charts ---- */

export interface Tip {title:string;value:string;lines:string[]}
export interface BarItem {key:string;engine:string;label:string;sub?:string;value:number|null;slot:number;reference:boolean;muted:boolean;rank:number|null;aria:string;tip:Tip}
export interface BarGroup {label?:string;items:BarItem[]}
export interface Chart {mode:Classified['mode'];groups:BarGroup[];hasData:boolean;domain:[number,number];ticks:number[]}

const casesText=(line:BenchLine,total:number)=>total>line.cases?`${line.cases} de ${total} ${total===1?'caso':'casos'}`:plural(line.cases,'caso','casos');

/** Lines of a tooltip, after the value: the sample the number comes from and what else explains it. */
function detail(line:BenchLine,metric:Metric,total:number,muted:boolean):string[]{
  const lines=[metric.id==='speed'&&typeof line.timed_cases==='number'?`mediana de ${plural(line.timed_cases,'caso','casos')}`:casesText(line,total)];
  if(metric.id==='flash'){
    const parts:[string,Metric][]=[['fuente',metrics.source],['recompila',metrics.recompiles],['misma traza',metrics.trace],['unidades',metrics.units]];
    lines.push(parts.map(([name,part])=>{const value=part.value(line);return `${name} ${value===null?'—':valueLabel('percent',value)}`;}).join(' · '));
  }
  if(line.reference&&metric.referenceIs100)lines.push('Referencia: vale 100 % por definición');
  else if(muted)lines.push('Medido sobre otros casos: no se ordena con el resto');
  return lines;
}

/** Ticks and domain: 0 to 100 for percentages and the index, a round maximum for seconds. */
export function scaleFor(unit:Unit,values:number[]):{domain:[number,number];ticks:number[]}{
  if(unit!=='seconds')return {domain:[0,100],ticks:[0,25,50,75,100]};
  const max=Math.max(0,...values);
  if(!(max>0))return {domain:[0,1],ticks:[0,0.5,1]};
  const decade=10**Math.floor(Math.log10(max/4));
  const step=[1,2,5,10].map(factor=>factor*decade).find(candidate=>max/candidate<=5)!;
  const top=Math.ceil(max/step-1e-9)*step;
  return {domain:[0,top],ticks:Array.from({length:Math.round(top/step)+1},(_,index)=>Math.round(index*step*1e6)/1e6)};
}

/** Values that differ only by float noise are equal (a tie shares its rank). */
const tidy=(value:number|null)=>value===null?null:Math.round(value*1e6)/1e6;

/** Bars for one metric, best first. Engines measured on other cases go in a group of their own, never numbered. */
export function chartFor(table:BenchTable,metric:Metric):Chart{
  const found=classify(table);
  const total=table.corpus_cases??Math.max(0,...found.measured.map(line=>line.cases));
  const item=(line:BenchLine,muted:boolean):BarItem=>{
    const value=metric.value(line);
    const label=engineName(line);
    const lines=detail(line,metric,total,muted);
    const reference=!!line.reference&&!!metric.referenceIs100;
    const shown=value===null?'sin datos en esta corrida':exactLabel(metric.unit,value);
    return {key:line.engine,engine:line.engine,label,sub:muted?casesText(line,total):undefined,value,slot:slotOf(line.engine),reference,muted,rank:null,
      aria:`${label}: ${shown}. ${lines.join('. ')}`,tip:{title:label,value:value===null?'Sin datos en esta corrida':exactLabel(metric.unit,value),lines}};
  };
  const order=(items:BarItem[])=>[...items].sort((a,b)=>Number(a.value===null)-Number(b.value===null)||(metric.lowerIsBetter?1:-1)*((tidy(a.value)??0)-(tidy(b.value)??0))||a.label.localeCompare(b.label,'es'));
  const groups:BarGroup[]=[];
  if(found.mode==='ranked'||found.mode==='unranked'||found.mode==='single'){
    const first=order((found.mode==='ranked'?found.ranked:found.measured).map(line=>item(line,false)));
    if(found.mode==='ranked'){
      let position=0,last:number|null=null,rank=0;
      for(const bar of first){
        if(bar.reference||bar.value===null)continue;
        position+=1;
        if(tidy(bar.value)!==last){rank=position;last=tidy(bar.value);}
        bar.rank=rank;
      }
    }
    groups.push({items:first});
    if(found.others.length&&found.mode==='ranked')groups.push({label:'Medidos sobre otros casos',items:order(found.others.map(line=>item(line,true)))});
  }
  const shown=groups.flatMap(group=>group.items).map(bar=>bar.value).filter((value):value is number=>value!==null);
  return {mode:found.mode,groups,hasData:shown.length>0,...scaleFor(metric.unit,shown)};
}

/* ---- highlight cards ---- */

export interface Highlight {id:string;title:string;value:string;engine?:string;name?:string;slot?:number;sub:string}
type Scored={line:BenchLine;value:number};
const leaders=(pool:BenchLine[],read:(line:BenchLine)=>number|null,lowerIsBetter=false):Scored[]=>
  pool.flatMap(line=>{const value=read(line);return value===null?[]:[{line,value}];}).sort((a,b)=>(lowerIsBetter?1:-1)*(a.value-b.value)||a.line.engine.localeCompare(b.line.engine));
const card=(id:string,title:string,top:Scored|undefined,value:string,sub:string):Highlight=>
  top?{id,title,value,engine:top.line.engine,name:engineName(top.line),slot:slotOf(top.line.engine),sub}:{id,title,value:'—',sub};
const names=(list:string[])=>list.length>1?`${list.slice(0,-1).join(', ')} y ${list[list.length-1]}`:list[0];
/** The engines that share first place with the leader. */
const tiedWith=(scored:Scored[])=>scored.slice(1).filter(other=>tidy(other.value)===tidy(scored[0].value)).map(other=>engineName(other.line));

/**
 * Four cards from the decompilers table: only engines measured on the same cases compete, JPEXS among them (except for the
 * closeness to JPEXS, where it is the reference). A lead of one case or less is noise (as in the table), a lead of under one
 * point of the index is a technical tie.
 */
export function highlights(results:BenchResults):Highlight[]{
  const table=results.tables.decompilers;
  const pool=table?classify(table).ranked:[];
  const none=pool.length?'Sin datos en esta corrida.':'Sin motores comparables en esta corrida.';
  const flash=leaders(pool,metrics.flash.value);
  const close=leaders(pool.filter(line=>!line.reference),metrics.closeness.value);
  const fast=leaders(pool,metrics.speed.value,true);
  const trace=leaders(pool,line=>line.same_trace??null);
  const gap=flash.length>1?flash[0].value-flash[1].value:null;
  const lead=trace.length>1?trace[0].value-trace[1].value:null;
  const flashTie=flash.length?tiedWith(flash):[],closeTie=close.length?tiedWith(close):[],fastTie=fast.length?tiedWith(fast):[],traceTie=trace.length?tiedWith(trace):[];
  const flashOf=fast.length?fast[0].line.flash_index:null;
  return [
    card('flash','Mejor Índice Flash',flash[0],flash[0]?valueLabel('index',flash[0].value):'',
      !flash.length?(pool.length?'Sin datos en esta corrida: no contó las unidades del SWF.':none):flashTie.length?`Empate con ${names(flashTie)}.`:gap===null?'Único motor con índice.':gap<1?`Empate técnico con ${engineName(flash[1].line)}.`:`${valueLabel('index',gap)} puntos sobre ${engineName(flash[1].line)}.`),
    card('closeness','Más cercano a JPEXS',close[0],close[0]?valueLabel('percent',close[0].value):'',
      !close.length?(pool.length?'Sin datos en esta corrida: falta la salida de JPEXS, la referencia.':none):`${closeTie.length?`Empate con ${names(closeTie)}. `:''}Cubre ${valueLabel('percent',metrics.coverage.value(close[0].line)??0)} de sus unidades.`),
    card('speed','Más rápido',fast[0],fast[0]?secondsLabel(fast[0].value):'',
      !fast.length?(pool.length?'Sin datos en esta corrida: la corrida no midió tiempos.':none):`${fastTie.length?`Empate con ${names(fastTie)}. `:''}Por caso (mediana)${flashOf!=null?` · Índice Flash ${valueLabel('index',flashOf)}`:''}.`),
    card('cases','Más casos con la misma traza',trace[0],trace[0]?`${trace[0].value} de ${trace[0].line.cases}`:'',
      !trace.length?none:lead===null?'Único motor comparable.':lead===0?`Empate con ${names(traceTie)}.`:lead<=1?`Ventaja de 1 caso sobre ${engineName(trace[1].line)}: dentro del ruido.`:`Ventaja de ${lead} casos sobre ${engineName(trace[1].line)}.`),
  ];
}

/* ---- scatter: closeness to JPEXS against speed ---- */

export interface Dot {key:string;engine:string;label:string;x:number;y:number;slot:number;reference:boolean;muted:boolean;aria:string;tip:Tip}
/** One dot per measured engine that has both figures; the ones measured on other cases are marked, never dropped. */
export function scatterDots(table:BenchTable):Dot[]{
  const found=classify(table);
  const total=table.corpus_cases??Math.max(0,...found.measured.map(line=>line.cases));
  return found.measured.flatMap(line=>{
    const x=metrics.speed.value(line),y=metrics.closeness.value(line);
    if(x===null||y===null||x<=0)return [];
    const muted=!found.ranked.includes(line)&&!line.reference&&found.mode==='ranked';
    const label=engineName(line);
    const lines=[casesText(line,total),...(line.reference?['Referencia: vale 100 % por definición']:muted?['Medido sobre otros casos: no se ordena con el resto']:[])];
    const value=`${exactLabel('percent',y)} · ${secondsLabel(x)}`;
    return [{key:line.engine,engine:line.engine,label,x,y,slot:slotOf(line.engine),reference:!!line.reference,muted,
      aria:`${label}: cercanía a JPEXS ${exactLabel('percent',y)}, ${secondsLabel(x)} por caso. ${lines.join('. ')}`,tip:{title:label,value,lines}}];
  });
}
