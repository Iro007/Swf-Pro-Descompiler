import {BenchLine,BenchResults,BenchRun,BenchTable,Outcome,UnitOutcome} from '../../types';
import {displayEngineName} from '../../displayNames';

/**
 * What the Benchmarks page draws, computed from `bench_results` and nothing else. Pure functions: formatting, one
 * metric per chart, who may be ranked against whom, the order of the bars and the highlight cards.
 * docs/BENCHMARKS_CODIGO.md defines the numbers (Índice Flash, medians, "measured on the same cases").
 */

export const categoryOrder=['decompilers','robustness','unit_fidelity','compilers','disassemblers','converters','extractors'];
export const categoryTitles:Record<string,string>={decompilers:'Descompiladores',robustness:'Robustez',unit_fidelity:'Fidelidad por unidad',compilers:'Compiladores',disassemblers:'Desensambladores',converters:'Conversores XML',extractors:'Extractores'};
/** The two questions about the decompilers that join the Índice Flash: a run from before schema 3 has no table for them. */
export const decompilerQuestions=['robustness','unit_fidelity'];
export const categoryQuestions:Record<string,string>={
  decompilers:'¿Lo que emite recompila con un compilador independiente (Flex o Rascal) y Ruffle imprime la misma traza que el original?',
  robustness:'¿Qué hace cada motor con un SWF dañado, truncado o corrupto? Lo mejor es recuperar lo que sigue intacto y avisar del daño; después dar salida sin avisar, fallar con un mensaje claro y, lo peor, caerse o colgarse.',
  unit_fidelity:'¿El fuente de cada clase, método o acción, compilado solo y empalmado en el SWF original, imprime la misma traza que el original?',
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
const slots:Record<string,number>={native:1,rust:2,go:3,js:4,flare:5,pyflashkit:6,asm:7,jpexs:8,as3_sorcerer:1,asv:2,sothink:3,trillix:4,decompiler_com:5,flex:7,rascal:6,haxe:2,rabcdasm:3,flasm:5,swfmill:7,swfextract:6,flex_swfdump:4,mtasc:4};
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
export const runCategoriesLabel=(categories?:string[]):string=>{
  if(!categories||!categories.length)return '';
  if(categories.includes('decompilers')){
    if(categories.length>=6)return 'Completa (todas las cat.)';
    if(categories.length===1)return 'Solo Descompiladores';
    return `Descompiladores + ${categories.length-1} cat.`;
  }
  if(categories.length===1){
    const key=categories[0];
    return `Solo ${categoryTitles[key]??key}`;
  }
  return categories.map(c=>categoryTitles[c]??c).join(', ');
};
export const runLabel=(run:BenchRun)=>{
  const parts=[dateLabel(run.created_utc??runIdToIso(run.run_id))||run.run_id,modeLabel(run.mode),`${run.cases} casos`];
  const cat=runCategoriesLabel(run.categories);
  if(cat)parts.push(cat);
  return parts.join(' · ');
};

/* ---- metrics: one chart each ---- */

/* ---- the terms of the Índice Flash ---- */

export interface Term {id:string;label:string}
/** The six terms the index can have, in order: the first four are in every run that counts the units, the last two when the run measured them for every ranked engine. */
export const indexTerms:Term[]=[{id:'source',label:'fuente'},{id:'recompiles',label:'recompila'},{id:'same_trace',label:'misma traza'},{id:'units',label:'unidades con fuente'},{id:'unit_fidelity',label:'fidelidad por unidad'},{id:'robustness',label:'robustez'}];
const baseTerms=['source','recompiles','same_trace','units'];
/** What the run says its index averages. A run from before schema 3 does not say: it averaged the four base terms if it has an index at all. */
export const termsOf=(table:BenchTable):string[]=>table.index_components??(table.engines.some(line=>typeof line.flash_index==='number')?baseTerms:[]);
const termLabel=(id:string)=>indexTerms.find(term=>term.id===id)?.label??id;

export interface Metric {id:string;title:string;unit:Unit;lowerIsBetter?:boolean;referenceIs100?:boolean;value:(line:BenchLine)=>number|null;note:string;
  /** what one sample of the figure is called in the tooltips (cases, damaged files...) */
  noun?:[string,string];
  /** more lines for the tooltip of a bar, after the sample */
  extra?:(line:BenchLine)=>string[]}
export const metrics:Record<string,Metric>={
  flash:{id:'flash',title:'Índice Flash',unit:'index',value:line=>finite(line.flash_index),
    note:'Media de los porcentajes que se miden igual en todos los motores, JPEXS incluido (de 0 a 100). No usa la cercanía ni la cobertura de JPEXS. Solo se ordenan motores medidos sobre los mismos casos.'},
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
  robustness:{id:'robustness',title:'Robustez',unit:'index',value:line=>finite(line.robustness),noun:['archivo dañado','archivos dañados'],
    note:'Nota de 0 a 100 sobre los archivos dañados (truncados o corruptos): cada archivo vale 100 si el motor recupera lo que sigue intacto y avisa del daño, 60 si da salida sin avisar, 40 si no da salida pero explica el error, y 0 si se cae o se cuelga. La nota es la media.',
    extra:line=>typeof line.recovered_units==='number'&&typeof line.intact_units==='number'&&line.intact_units>0?[`${line.recovered_units} de ${plural(line.intact_units,'unidad intacta','unidades intactas')} con fuente`]:[]},
  recovery:{id:'recovery',title:'Unidades recuperadas',unit:'percent',value:line=>scaled(line.unit_recovery),noun:['archivo dañado','archivos dañados'],
    note:'Media, por archivo dañado, de las clases, scripts y acciones que seguían intactos (ningún byte dañado toca su código ni lo que necesita para leerse) para las que el motor emitió fuente verificado. Solo cuenta los archivos de los que algo se podía recuperar; no entra en la nota de robustez.'},
  fidelity:{id:'fidelity',title:'Fidelidad por unidad',unit:'percent',value:line=>scaled(line.fidelity!==undefined?line.fidelity:line.unit_fidelity),
    note:'Porcentaje de unidades (clases, métodos y acciones) cuyo fuente, compilado solo con el compilador de PySWF y empalmado en el SWF original, hace que Ruffle imprima la misma traza. Las unidades que la traza del caso nunca ejecuta no se juzgan y no cuentan. Es la misma ruta de compilación para todos los motores.',
    extra:line=>{const judged=line.judged??line.unit_fidelity_judged;return typeof judged==='number'?[plural(judged,'unidad juzgada','unidades juzgadas')+(typeof line.not_run==='number'&&line.not_run>0?`, ${plural(line.not_run,'no se ejecuta','no se ejecutan')}`:'')]:[];}},
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

const casesText=(line:BenchLine,total:number,noun:[string,string]=['caso','casos'])=>total>line.cases?`${line.cases} de ${total} ${total===1?noun[0]:noun[1]}`:plural(line.cases,noun[0],noun[1]);

/** Lines of a tooltip, after the value: the sample the number comes from and what else explains it. */
function detail(line:BenchLine,metric:Metric,total:number,muted:boolean,terms:string[]):string[]{
  const lines=[metric.id==='speed'&&typeof line.timed_cases==='number'?`mediana de ${plural(line.timed_cases,'caso','casos')}`:casesText(line,total,metric.noun)];
  if(metric.id==='flash'){
    lines.push(terms.map(term=>{const value=termPercent(line,term);return `${termLabel(term)} ${value===null?'—':valueLabel('percent',value)}`;}).join(' · '));
  }
  lines.push(...(metric.extra?.(line)??[]));
  if(line.reference&&metric.referenceIs100)lines.push('Referencia: vale 100 % por definición');
  else if(muted)lines.push('Medido sobre otros casos: no se ordena con el resto');
  return lines;
}

/** One term of the index for a decompiler line, as a percentage; None where the run did not measure it. An engine measured for
 *  per-unit fidelity that had no unit to judge scores 0 there, as in the index. */
function termPercent(line:BenchLine,term:string):number|null{
  switch(term){
    case 'source':return metrics.source.value(line);
    case 'recompiles':return metrics.recompiles.value(line);
    case 'same_trace':return metrics.trace.value(line);
    case 'units':return metrics.units.value(line);
    case 'unit_fidelity':{const value=scaled(line.unit_fidelity);return value!==null?value:line.unit_fidelity_cases?0:null;}
    case 'robustness':return finite(line.robustness);
    default:return null;
  }
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
  const terms=termsOf(table);
  const item=(line:BenchLine,muted:boolean):BarItem=>{
    const value=metric.value(line);
    const label=engineName(line);
    const lines=detail(line,metric,total,muted,terms);
    const reference=!!line.reference&&!!metric.referenceIs100;
    const shown=value===null?'sin datos en esta corrida':exactLabel(metric.unit,value);
    return {key:line.engine,engine:line.engine,label,sub:muted?casesText(line,total,metric.noun):undefined,value,slot:slotOf(line.engine),reference,muted,rank:null,
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

/* ---- stacked bars: how a set of cases ended, one bar per engine ---- */

export interface StackSpec {id:string;label:string;short:string}
/** The five outcomes of an engine on a damaged file, best first. */
export const outcomeSpecs:(StackSpec&{id:Outcome})[]=[
  {id:'recovered',label:'Recuperó lo intacto y avisó del daño',short:'recuperó'},
  {id:'partial',label:'Dio salida, pero sin avisar o sin lo intacto',short:'parcial'},
  {id:'clean_error',label:'Sin salida, con un error claro',short:'error claro'},
  {id:'crash',label:'Se cayó',short:'caída'},
  {id:'hang',label:'Se colgó',short:'cuelgue'}];
/** The four outcomes of a unit compiled alone and spliced into the original SWF. */
export const unitSpecs:(StackSpec&{id:UnitOutcome})[]=[
  {id:'same',label:'Misma traza que el original',short:'misma traza'},
  {id:'different',label:'Otra traza',short:'otra traza'},
  {id:'no_compile',label:'No compila',short:'no compila'},
  {id:'not_run',label:'La traza no la ejecuta (no se juzga)',short:'no se ejecuta'}];

export interface StackItem {key:string;engine:string;label:string;sub?:string;slot:number;muted:boolean;rank:number|null;total:number;segments:{id:string;value:number}[];aria:string;tip:Tip}
export interface StackGroup {label?:string;items:StackItem[]}
export interface Stack {mode:Classified['mode'];groups:StackGroup[];hasData:boolean}

/** One stacked bar per engine, ordered and ranked by `order` (best first), the engines measured on other cases apart. */
export function stackFor(table:BenchTable,specs:StackSpec[],read:(line:BenchLine)=>Record<string,number>|undefined,order:Metric,noun:[string,string]):Stack{
  const found=classify(table);
  const item=(line:BenchLine,muted:boolean):StackItem=>{
    const counts=read(line)??{};
    const segments=specs.map(spec=>({id:spec.id,value:counts[spec.id]??0}));
    const total=segments.reduce((sum,segment)=>sum+segment.value,0);
    const label=engineName(line);
    const score=order.value(line);
    const lines=[plural(total,noun[0],noun[1]),...specs.map(spec=>`${spec.short}: ${counts[spec.id]??0}${total?` (${valueLabel('percent',100*(counts[spec.id]??0)/total)})`:''}`),
      ...(muted?['Medido sobre otros casos: no se ordena con el resto']:[])];
    const value=score===null?'Sin datos en esta corrida':`${order.title} ${exactLabel(order.unit,score)}`;
    return {key:line.engine,engine:line.engine,label,sub:muted?plural(total,noun[0],noun[1]):undefined,slot:slotOf(line.engine),muted,rank:null,total,segments,
      aria:`${label}: ${total?specs.map(spec=>`${spec.short} ${counts[spec.id]??0}`).join(', '):'sin datos'}. ${lines[0]}`,tip:{title:label,value,lines}};
  };
  const scoreOf=(bar:StackItem)=>order.value(found.measured.find(line=>line.engine===bar.engine)!);
  const sorted=(items:StackItem[])=>[...items].sort((a,b)=>Number(a.total===0)-Number(b.total===0)||((tidy(scoreOf(b))??-1)-(tidy(scoreOf(a))??-1))||a.label.localeCompare(b.label,'es'));
  const groups:StackGroup[]=[];
  if(found.mode==='ranked'||found.mode==='unranked'||found.mode==='single'){
    const first=sorted((found.mode==='ranked'?found.ranked:found.measured).map(line=>item(line,false)));
    if(found.mode==='ranked'){
      let position=0,last:number|null=null,rank=0;
      for(const bar of first){
        const score=tidy(scoreOf(bar));
        if(score===null||bar.total===0)continue;
        position+=1;
        if(score!==last){rank=position;last=score;}
        bar.rank=rank;
      }
    }
    groups.push({items:first});
    if(found.others.length&&found.mode==='ranked')groups.push({label:'Medidos sobre otros casos',items:sorted(found.others.map(line=>item(line,true)))});
  }
  return {mode:found.mode,groups,hasData:groups.some(group=>group.items.some(bar=>bar.total>0))};
}

/* ---- small multiples: one chart per kind of damage, per kind of unit ---- */

/** What each kind of damage does to a file, for the note under its chart. */
export const damageNotes:Record<string,string>={
  truncate_header:'El archivo se corta dentro de la cabecera (firma, longitud, rectángulo o frecuencia): no queda nada que recuperar, así que lo mejor es un error claro.',
  truncate_tag:'El archivo se corta en mitad de un tag que no es código. Si el corte cae después del código, el código sigue entero y se puede recuperar.',
  truncate_code:'El archivo se corta en mitad del cuerpo de un método o de una acción: lo anterior sigue intacto y un motor tolerante debería recuperarlo.',
  bad_length:'La longitud de archivo de la cabecera no coincide con la real, pero el contenido está entero: lo ideal es leerlo todo y avisar.',
  bad_compression:'El flujo zlib de un CWS está roto (bytes cambiados, cabecera o suma de comprobación malas): se pierde lo que viene después del primer byte dañado.',
  bad_tag_length:'Un tag declara una longitud errónea y los siguientes se leen desde un sitio equivocado; el código anterior al tag sigue intacto.',
  flip_code:'Se cambian bytes al azar dentro del tag de código (DoABC o DoAction): las unidades que el daño no toca siguen intactas.',
  flip_noncode:'Se cambian bytes al azar dentro de un tag que no es código: todo el código sigue intacto.'};

/** The robustness score of the files of one kind of damage, as a metric for its own chart. */
export const damageMetric=(kind:string,title:string):Metric=>({id:`damage-${kind}`,title,unit:'index',noun:['archivo dañado','archivos dañados'],note:damageNotes[kind]??'',
  value:line=>finite(line.by_kind?.[kind]?.score)});

/** Per-unit fidelity of one group of unit kinds (classes and actions together, methods apart), as a metric for its own chart. */
export const unitKindMetric=(id:string,title:string,kinds:string[],note:string):Metric=>({id,title,unit:'percent',noun:['caso','casos'],note,
  value:line=>{
    const parts=kinds.map(kind=>line.by_kind?.[kind]).filter(part=>!!part);
    const judged=parts.reduce((sum,part)=>sum+(part!.judged??0),0);
    return judged>0?100*parts.reduce((sum,part)=>sum+(part!.same??0),0)/judged:null;
  },
  extra:line=>{const judged=kinds.reduce((sum,kind)=>sum+(line.by_kind?.[kind]?.judged??0),0);return judged?[plural(judged,'unidad juzgada','unidades juzgadas')]:['ninguna unidad juzgada'];}});

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
 * Six cards from the decompilers table: only engines measured on the same cases compete, JPEXS among them (except for the
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
  const robust=leaders(pool,metrics.robustness.value),faithful=leaders(pool,metrics.fidelity.value);
  const robustTie=robust.length?tiedWith(robust):[],faithfulTie=faithful.length?tiedWith(faithful):[];
  const damaged=results.tables.robustness?.engines.find(line=>line.engine===robust[0]?.line.engine);
  const wasMeasured=(key:string)=>key in results.tables?'Sin datos en esta corrida.':'Sin datos en esta corrida: es anterior a esta medición.';
  return [
    card('flash','Mejor Índice Flash',flash[0],flash[0]?valueLabel('index',flash[0].value):'',
      !flash.length?(pool.length?'Sin datos en esta corrida: no contó las unidades del SWF.':none):flashTie.length?`Empate con ${names(flashTie)}.`:gap===null?'Único motor con índice.':gap<1?`Empate técnico con ${engineName(flash[1].line)}.`:`${valueLabel('index',gap)} puntos sobre ${engineName(flash[1].line)}.`),
    card('closeness','Más cercano a JPEXS',close[0],close[0]?valueLabel('percent',close[0].value):'',
      !close.length?(pool.length?'Sin datos en esta corrida: falta la salida de JPEXS, la referencia.':none):`${closeTie.length?`Empate con ${names(closeTie)}. `:''}Cubre ${valueLabel('percent',metrics.coverage.value(close[0].line)??0)} de sus unidades.`),
    card('speed','Más rápido',fast[0],fast[0]?secondsLabel(fast[0].value):'',
      !fast.length?(pool.length?'Sin datos en esta corrida: la corrida no midió tiempos.':none):`${fastTie.length?`Empate con ${names(fastTie)}. `:''}Por caso (mediana)${flashOf!=null?` · Índice Flash ${valueLabel('index',flashOf)}`:''}.`),
    card('cases','Más casos con la misma traza',trace[0],trace[0]?`${trace[0].value} de ${trace[0].line.cases}`:'',
      !trace.length?none:lead===null?'Único motor comparable.':lead===0?`Empate con ${names(traceTie)}.`:lead<=1?`Ventaja de 1 caso sobre ${engineName(trace[1].line)}: dentro del ruido.`:`Ventaja de ${lead} casos sobre ${engineName(trace[1].line)}.`),
    card('robustness','Más robusto ante archivos dañados',robust[0],robust[0]?valueLabel('index',robust[0].value):'',
      !robust.length?(pool.length?wasMeasured('robustness'):none):`${robustTie.length?`Empate con ${names(robustTie)}. `:''}${damaged?.outcomes?`Recuperó ${damaged.outcomes.recovered} de ${plural(damaged.cases,'archivo','archivos')}.`:'Nota de 0 a 100.'}`),
    card('fidelity','Mayor fidelidad por unidad',faithful[0],faithful[0]?valueLabel('percent',faithful[0].value):'',
      !faithful.length?(pool.length?wasMeasured('unit_fidelity'):none):`${faithfulTie.length?`Empate con ${names(faithfulTie)}. `:''}${typeof faithful[0].line.unit_fidelity_judged==='number'?`Sobre ${plural(faithful[0].line.unit_fidelity_judged,'unidad juzgada','unidades juzgadas')}.`:'Unidades compiladas solas.'}`),
  ];
}

/* ---- scatter: Flash Index / semantic fidelity against speed ---- */

export interface Dot {key:string;engine:string;label:string;x:number;y:number;slot:number;reference:boolean;muted:boolean;aria:string;tip:Tip}
/** One dot per measured engine that has both figures; the ones measured on other cases are marked, never dropped. */
export function scatterDots(table:BenchTable):Dot[]{
  const found=classify(table);
  const total=table.corpus_cases??Math.max(0,...found.measured.map(line=>line.cases));
  return found.measured.flatMap(line=>{
    const x=metrics.speed.value(line);
    const y=metrics.flash.value(line)??(typeof line.same_trace==='number'&&line.cases>0?100*line.same_trace/line.cases:null);
    if(x===null||y===null||!Number.isFinite(y)||x<=0)return [];
    const muted=!found.ranked.includes(line)&&found.mode==='ranked';
    const label=engineName(line);
    const closenessVal=metrics.closeness.value(line);
    const lines=[
      casesText(line,total),
      `Índice Flash: ${exactLabel('index',y)}`,
      ...(closenessVal!==null?[`Paridad textual con JPEXS: ${exactLabel('percent',closenessVal)}`]:[]),
      ...(line.reference?['JPEXS: compite como herramienta externa']:muted?['Medido sobre otros casos: no se ordena con el resto']:[])
    ];
    const value=`Índice ${exactLabel('index',y)} · ${secondsLabel(x)}`;
    return [{key:line.engine,engine:line.engine,label,x,y,slot:slotOf(line.engine),reference:false,muted,
      aria:`${label}: Índice Flash ${exactLabel('index',y)}, ${secondsLabel(x)} por caso. ${lines.join('. ')}`,tip:{title:label,value,lines}}];
  });
}
