import {ReactNode} from 'react';
import {BenchLine,BenchResults,BenchTable as Table} from '../../types';
import BarChart from './BarChart';
import BenchTable from './BenchTable';
import ScatterChart from './ScatterChart';
import StackedBars,{StackLegend} from './StackedBars';
import {Metric,categoryQuestions,categoryTitles,chartFor,classify,damageMetric,engineName,indexTerms,metrics,outcomeSpecs,plural,primaryMetric,scatterDots,secondsLabel,slotOf,stackFor,termsOf,unitKindMetric,unitSpecs,valueLabel} from './model';
import {Legend} from './shared';

const sampleOf=(results:BenchResults,table:Table,noun:[string,string]=['caso','casos'],place='del corpus')=>{
  const total=table.corpus_cases??results.cases,shared=classify(table).ranked[0]?.cases;
  const where=place?` ${place}`:'';
  return shared&&shared<total?`Los motores ordenados comparten ${shared} de los ${total} ${noun[1]}${where}.`:`Muestra: ${plural(total,noun[0],noun[1])}${where}.`;
};
const Empty=({children}:{children:ReactNode})=><p className="bench-empty">{children}</p>;

function Panel({title,tag,note,children,wide,span2}:{title?:string;tag?:string;note:string;children:ReactNode;wide?:boolean;span2?:boolean}){
  return <figure className={`bench-panel${wide?' wide':''}${span2?' span2':''}`}>
    {title?<div className="bench-panel-head"><h4>{title}</h4>{tag?<span className="bench-tag">{tag}</span>:null}</div>:null}
    {children}
    <figcaption className="bench-note">{note}</figcaption>
  </figure>;
}

/** What to say where a chart has no figures because the run predates the measurement. */
const whyEmpty=(metric:Metric)=>metric.id==='speed'?' La corrida es anterior a la medición de tiempos.'
  :metric.id==='flash'||metric.id==='units'?' La corrida es anterior al recuento de las unidades del SWF.'
  :metric.id==='closeness'||metric.id==='coverage'?' Hace falta la salida de JPEXS, la referencia.'
  :metric.id==='robustness'||metric.id==='recovery'||metric.id.startsWith('damage-')?' La corrida es anterior a la medición de robustez ante archivos dañados.'
  :metric.id==='fidelity'||metric.id.startsWith('unit-')?' La corrida es anterior a la medición de fidelidad por unidad, o ningún motor emitió fuente que se pudiera compilar sola.'
  :'';

/** One metric as sorted bars, or the reason there are none. A run from before schema 2 has no times: that is said, not drawn. */
function MetricPanel({results,table,metric,size,wide,bare,note,sample,title}:{results:BenchResults;table:Table;metric:Metric;size?:'lg'|'sm';wide?:boolean;bare?:boolean;note?:string;sample?:string;title?:string}){
  const chart=chartFor(table,metric);
  const alone=chart.mode==='unranked'?' Cada motor se midió sobre casos distintos: no hay clasificación entre ellos.':'';
  return <Panel title={bare?undefined:title??metric.title} tag={metric.lowerIsBetter?'menos es mejor':undefined} wide={wide}
    note={`${note??metric.note}${alone} ${sample??sampleOf(results,table)}`.trim()}>
    {chart.hasData
      ?<BarChart title={`${title??metric.title} por motor`} chart={chart} unit={metric.unit} lowerIsBetter={metric.lowerIsBetter} size={size}/>
      :<Empty>Sin datos en esta corrida.{whyEmpty(metric)}</Empty>}
  </Panel>;
}

/** The figure of a category with a single engine: nothing to sort, so a tile instead of a one-bar chart. */
function Tile({results,table,line}:{results:BenchResults;table:Table;line:BenchLine}){
  const ok=metrics.ok.value(line);
  return <figure className="bench-panel tile-panel">
    <div className="bench-panel-head"><h4>Casos correctos</h4></div>
    <div className="bench-stat tile">
      <strong className="bench-stat-value">{ok===null?'—':valueLabel('percent',ok)}</strong>
      <span className="bench-stat-engine"><i className={`bench-dot s${slotOf(line.engine)}`} aria-hidden="true"/>{engineName(line)}</span>
      <span className="bench-stat-sub">{line.ok} de {plural(line.cases,'caso','casos')}{typeof line.median_seconds==='number'?` · ${secondsLabel(line.median_seconds)} por caso (mediana)`:' · sin tiempos en esta corrida'}.</span>
    </div>
    <figcaption className="bench-note">Porcentaje de {table.unit}. Un solo motor medido: no hay con qué compararlo. {sampleOf(results,table)}</figcaption>
  </figure>;
}

/** Engines the run could not measure, with the reason they gave (not installed, does not read the language, only lists). */
function Unmeasured({lines}:{lines:BenchLine[]}){
  if(!lines.length)return null;
  const groups=new Map<string,string[]>();
  for(const line of lines){const why=line.reasons[0]??'sin medir';groups.set(why,[...(groups.get(why)??[]),engineName(line)]);}
  return <p className="bench-unmeasured"><strong>Sin medir:</strong> {[...groups].map(([why,names])=>`${names.join(', ')} (${why})`).join(' · ')}</p>;
}

const legendOf=(table:Table)=>table.engines.filter(line=>line.cases>0).map(line=>({engine:line.engine,label:engineName(line),slot:slotOf(line.engine),reference:line.reference}));

/** What each term of the Índice Flash measures, for the "how it is computed" note (only the terms the run includes are listed). */
const termNotes:Record<string,string>={
  source:'casos en que el motor emite fuente completo de todas las unidades del SWF.',
  recompiles:'casos en que ese fuente compila con un compilador independiente (Flex para AS3, Rascal para AS2).',
  same_trace:'casos en que, además, Ruffle imprime la misma traza que el SWF original.',
  units:'media, por caso, de las unidades del propio SWF (clases, scripts y acciones de su inventario ABC y AVM1) para las que el motor emitió fuente verificado.',
  unit_fidelity:'unidades (clases, métodos y acciones) cuyo fuente, compilado solo con el compilador de PySWF y empalmado en el SWF original, imprime la misma traza: misma / (evaluadas − no ejecutadas). Un motor sin ninguna unidad juzgada puntúa 0.',
  robustness:'nota de 0 a 100 sobre los archivos dañados (truncados o corruptos): recuperó y avisó 100, dio salida sin avisar 60, error claro 40, caída o cuelgue 0; la media de todos los archivos.'};

/** The headline: decompilers by the Índice Flash, with how it is computed and which terms this run includes. */
export function IndexSection({results}:{results:BenchResults}){
  const table=results.tables.decompilers;
  if(!table)return null;
  const terms=termsOf(table);
  const left=indexTerms.filter(term=>!terms.includes(term.id));
  const formula=terms.map(id=>indexTerms.find(term=>term.id===id)?.label??id).join(' + ');
  const note=terms.length
    ?`Media de ${terms.length} porcentajes que se miden igual en todos los motores, JPEXS incluido (de 0 a 100): ${indexTerms.filter(term=>terms.includes(term.id)).map(term=>term.label).join(', ')}. No usa la cercanía ni la cobertura de JPEXS. Solo se ordenan motores medidos sobre los mismos casos.`
    :'Esta corrida no contó las unidades del SWF, así que no tiene Índice Flash.';
  return <section className="bench-card" aria-labelledby="bench-index">
    <header className="bench-section-head">
      <div><h3 id="bench-index">Índice Flash</h3><p className="bench-lead">Una nota de 0 a 100 por descompilador: cuánto de lo que emite está completo, recompila, se comporta igual que el original, cubre las unidades del SWF, sigue siendo fiel unidad a unidad y aguanta archivos dañados. JPEXS compite como los demás.</p></div>
      <Legend items={legendOf(table)}/>
    </header>
    <p className="bench-terms" data-testid="index-terms"><strong>Esta corrida incluye:</strong> {terms.length?terms.map(id=>indexTerms.find(term=>term.id===id)?.label??id).join(', '):'ninguno'}.
      {left.length&&terms.length?<> <strong>No incluye:</strong> {left.map(term=>term.label).join(', ')} (sin datos en esta corrida, o no medidos para todos los motores que se ordenan).</>:null}</p>
    <MetricPanel results={results} table={table} metric={metrics.flash} size="lg" wide bare note={note}/>
    <details className="bench-how">
      <summary>Cómo se calcula</summary>
      <p><strong>Índice Flash = ({formula||'—'}) / {terms.length||'—'}</strong>, cada término en porcentaje.</p>
      <ul>{terms.map(id=><li key={id}><strong>{indexTerms.find(term=>term.id===id)?.label}</strong>: {termNotes[id]}</li>)}</ul>
      <p>Los tres primeros se cuentan sobre los casos medidos del motor. Todos los términos se miden igual en todos los motores, JPEXS incluido, y ninguno usa la salida de otro descompilador: la cercanía y la cobertura de JPEXS no entran en el índice y se muestran aparte. La fidelidad por unidad y la robustez solo entran si la corrida las midió para todos los motores que se ordenan; si no, el índice usa los otros términos y esta página lo dice. Solo se ordenan motores medidos sobre los mismos casos; los demás van aparte. Es una nota compuesta: el ganador por comportamiento (misma traza) puede no ser el primero del índice. Una corrida anterior al recuento de unidades no tiene índice.</p>
    </details>
  </section>;
}

/** Decompilers: every sub-metric as small multiples, the scatter of closeness against speed, and the table. */
export function DecompilersSection({results}:{results:BenchResults}){
  const table=results.tables.decompilers;
  if(!table)return null;
  const dots=scatterDots(table);
  const unmeasured=classify(table).unmeasured;
  return <section className="bench-section" aria-labelledby="bench-decompilers">
    <header className="bench-section-head">
      <div><h3 id="bench-decompilers">{categoryTitles.decompilers}</h3><p className="bench-lead">{categoryQuestions.decompilers}</p></div>
      <Legend items={legendOf(table)}/>
    </header>
    <div className="bench-grid">
      {[metrics.source,metrics.recompiles,metrics.trace,metrics.units,metrics.closeness,metrics.coverage,metrics.speed].map(metric=><MetricPanel key={metric.id} results={results} table={table} metric={metric}/>)}
      <Panel title="Cercanía a JPEXS frente a velocidad" span2
        note={`Cada punto es un motor: arriba, más parecido a JPEXS; a la izquierda, más rápido (mediana de segundos por caso, con el arranque del motor, en escala logarítmica). El rombo es JPEXS, la referencia de cercanía; los puntos huecos se midieron sobre otros casos. Mide parecido y velocidad, no corrección: léase junto al Índice Flash. ${sampleOf(results,table)}`}>
        {dots.length>1
          ?<ScatterChart title="Cercanía a JPEXS frente a segundos por caso, por motor" dots={dots}/>
          :<Empty>Sin datos en esta corrida: hacen falta al menos dos motores con tiempos y con cercanía a JPEXS.</Empty>}
      </Panel>
    </div>
    <Unmeasured lines={unmeasured}/>
    <BenchTable category="decompilers" table={table}/>
  </section>;
}

const casesNoun:[string,string]=['archivo dañado','archivos dañados'];

/** Robustness: how each decompiler copes with truncated and corrupted SWF files. */
export function RobustnessSection({results}:{results:BenchResults}){
  const table=results.tables.robustness;
  const head=<header className="bench-section-head">
    <div><h3 id="bench-robustness">{categoryTitles.robustness}</h3><p className="bench-lead">{categoryQuestions.robustness}</p></div>
    {table&&classify(table).mode!=='none'?<Legend items={legendOf(table)}/>:null}
  </header>;
  if(!table)return <section className="bench-section" aria-labelledby="bench-robustness">{head}
    <Empty>Sin datos en esta corrida. La corrida es anterior a la medición de robustez ante archivos dañados (esquema 3).</Empty></section>;
  const found=classify(table);
  const stack=stackFor(table,outcomeSpecs,line=>line.outcomes,metrics.robustness,casesNoun);
  const kinds=Object.keys(table.kinds??{}).filter(kind=>table.engines.some(line=>line.by_kind?.[kind]));
  const sample=sampleOf(results,table,casesNoun,'');
  const filesOf=(kind:string)=>new Set((results.rows?.robustness??[]).filter(row=>row.kind===kind).map(row=>row.case)).size;
  return <section className="bench-section" aria-labelledby="bench-robustness">
    {head}
    {found.mode==='none'?<Empty>Ningún motor se pudo medir en esta corrida.</Empty>:<>
      <div className="bench-grid">
        <Panel title="Cómo terminó cada archivo dañado" wide
          note={`Cada barra reparte los archivos dañados de un motor entre los cinco desenlaces. Recuperó: dio fuente de lo que seguía intacto y avisó del daño con un mensaje que no daba con el archivo sano. Parcial: dio salida, pero sin avisar o sin lo intacto. Error claro: no dio salida y explicó por qué. Caída: el proceso murió, dio una traza de pila o una excepción sin controlar. Cuelgue: superó el plazo. ${sample}`}>
          {stack.hasData?<><StackedBars title="Desenlace de cada archivo dañado, por motor" stack={stack} specs={outcomeSpecs}/><StackLegend specs={outcomeSpecs}/></>
            :<Empty>Sin datos en esta corrida.</Empty>}
        </Panel>
        <MetricPanel results={results} table={table} metric={metrics.robustness} sample={sample}/>
        <MetricPanel results={results} table={table} metric={metrics.recovery} sample={sample}/>
      </div>
      {kinds.length?<>
        <h4 className="bench-subhead">Por tipo de daño</h4>
        <div className="bench-grid bench-multiples">
          {kinds.map(kind=><MetricPanel key={kind} results={results} table={table} metric={damageMetric(kind,table.kinds![kind])} size="sm"
            sample={filesOf(kind)?`${plural(filesOf(kind),'archivo','archivos')} de este tipo.`:''}/>)}
        </div>
      </>:null}
    </>}
    <Unmeasured lines={found.unmeasured}/>
    <BenchTable category="robustness" table={table} rows={results.rows?.robustness}/>
  </section>;
}

/** Per-unit fidelity: the source of each class, method or action compiled alone and spliced into the original SWF. */
export function UnitFidelitySection({results}:{results:BenchResults}){
  const table=results.tables.unit_fidelity;
  const head=<header className="bench-section-head">
    <div><h3 id="bench-unit_fidelity">{categoryTitles.unit_fidelity}</h3><p className="bench-lead">{categoryQuestions.unit_fidelity}</p></div>
    {table&&classify(table).mode!=='none'?<Legend items={legendOf(table)}/>:null}
  </header>;
  if(!table)return <section className="bench-section" aria-labelledby="bench-unit_fidelity">{head}
    <Empty>Sin datos en esta corrida. La corrida es anterior a la medición de fidelidad por unidad (esquema 3).</Empty></section>;
  const found=classify(table);
  const stack=stackFor(table,unitSpecs,line=>line.same===undefined?undefined:{same:line.same,different:line.different??0,no_compile:line.no_compile??0,not_run:line.not_run??0},metrics.fidelity,['unidad','unidades']);
  const scope=table.out_of_scope;
  const sample=`${sampleOf(results,table)}${table.unit_cap?` Como mucho ${plural(table.unit_cap,'unidad','unidades')} por caso, repartidas por el inventario del SWF.`:''}`;
  return <section className="bench-section" aria-labelledby="bench-unit_fidelity">
    {head}
    {found.mode==='none'?<Empty>Ningún motor se pudo medir en esta corrida.</Empty>:<>
      <div className="bench-grid">
        <MetricPanel results={results} table={table} metric={metrics.fidelity} sample={sample}/>
        <Panel title="Cómo terminó cada unidad"
          note={`Misma traza: Ruffle imprime la del original. Otra traza: difiere. No compila: la ruta de edición de una sola unidad de PySWF no tomó el fuente (es la misma para todos los motores). No se ejecuta: la traza del caso nunca pasa por la unidad, así que no se puede juzgar y queda fuera de la fidelidad. ${sample}`}>
          {stack.hasData?<><StackedBars title="Desenlace de cada unidad, por motor" stack={stack} specs={unitSpecs}/><StackLegend specs={unitSpecs}/></>
            :<Empty>Sin datos en esta corrida: ningún motor emitió fuente de una unidad que se pudiera compilar sola.</Empty>}
        </Panel>
      </div>
      <h4 className="bench-subhead">Clases frente a métodos</h4>
      <div className="bench-grid bench-multiples">
        <MetricPanel results={results} table={table} size="sm" sample={sample} title="Clases y acciones"
          metric={unitKindMetric('unit-documents','Clases y acciones',['class','action'],'Una clase de AS3 se juzga empalmando a la vez todos sus métodos editables con el fuente del motor; una acción de AS2 es el código entero de un fotograma, botón o clip (sus funciones viajan dentro).')}/>
        <MetricPanel results={results} table={table} size="sm" sample={sample} title="Métodos"
          metric={unitKindMetric('unit-methods','Métodos',['method'],'Un método de AS3 se juzga solo: su fuente, sin el resto de la clase, sustituye al cuerpo original. Los constructores deben conservar su super(...).')}/>
      </div>
      {scope&&scope.units?<p className="bench-unmeasured"><strong>Fuera de alcance:</strong> {plural(scope.units,'método queda','métodos quedan')} sin juzgar porque la ruta de edición no puede tomarlo para ningún motor ({Object.entries(scope.reasons).map(([why,count])=>`${count} ${why}`).join('; ')}). No cuentan contra ningún motor.</p>:null}
    </>}
    <Unmeasured lines={found.unmeasured}/>
    <BenchTable category="unit_fidelity" table={table} rows={results.rows?.unit_fidelity}/>
  </section>;
}

/** Compilers, disassemblers, converters and extractors: the share of correct cases, the speed and the table. */
export function CategorySection({category,results}:{category:string;results:BenchResults}){
  const table=results.tables[category];
  if(!table)return null;
  const found=classify(table);
  return <section className="bench-section" aria-labelledby={`bench-${category}`}>
    <header className="bench-section-head">
      <div><h3 id={`bench-${category}`}>{categoryTitles[category]??category}</h3><p className="bench-lead">{categoryQuestions[category]??table.unit}</p></div>
      {found.mode==='none'||found.mode==='single'?null:<Legend items={legendOf(table)}/>}
    </header>
    {found.mode==='none'?<Empty>Ningún motor se pudo medir en esta corrida.</Empty>
      :found.mode==='single'?<div className="bench-grid"><Tile results={results} table={table} line={found.measured[0]}/></div>
      :<div className="bench-grid">
        <MetricPanel results={results} table={table} metric={primaryMetric(category,table)}/>
        <MetricPanel results={results} table={table} metric={metrics.speed}/>
      </div>}
    <Unmeasured lines={found.unmeasured}/>
    <BenchTable category={category} table={table}/>
  </section>;
}
