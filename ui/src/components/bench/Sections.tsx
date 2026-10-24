import {ReactNode} from 'react';
import {BenchLine,BenchResults,BenchTable as Table} from '../../types';
import BarChart from './BarChart';
import BenchTable from './BenchTable';
import ScatterChart from './ScatterChart';
import {Metric,categoryQuestions,categoryTitles,chartFor,classify,engineName,metrics,plural,primaryMetric,scatterDots,secondsLabel,slotOf,valueLabel} from './model';
import {Legend} from './shared';

const sampleOf=(results:BenchResults,table:Table)=>{
  const total=table.corpus_cases??results.cases,shared=classify(table).ranked[0]?.cases;
  return shared&&shared<total?`Los motores ordenados comparten ${shared} de los ${total} casos del corpus.`:`Muestra: ${plural(total,'caso','casos')} del corpus.`;
};
const Empty=({children}:{children:ReactNode})=><p className="bench-empty">{children}</p>;

function Panel({title,tag,note,children,wide,span2}:{title?:string;tag?:string;note:string;children:ReactNode;wide?:boolean;span2?:boolean}){
  return <figure className={`bench-panel${wide?' wide':''}${span2?' span2':''}`}>
    {title?<div className="bench-panel-head"><h4>{title}</h4>{tag?<span className="bench-tag">{tag}</span>:null}</div>:null}
    {children}
    <figcaption className="bench-note">{note}</figcaption>
  </figure>;
}

/** One metric as sorted bars, or the reason there are none. A run from before schema 2 has no times: that is said, not drawn. */
function MetricPanel({results,table,metric,size,wide,bare}:{results:BenchResults;table:Table;metric:Metric;size?:'lg'|'sm';wide?:boolean;bare?:boolean}){
  const chart=chartFor(table,metric);
  const why=metric.id==='speed'?' La corrida es anterior a la medición de tiempos.':metric.id==='flash'||metric.id==='units'?' La corrida es anterior al recuento de las unidades del SWF.':metric.id==='closeness'||metric.id==='coverage'?' Hace falta la salida de JPEXS, la referencia.':'';
  const alone=chart.mode==='unranked'?' Cada motor se midió sobre casos distintos: no hay clasificación entre ellos.':'';
  return <Panel title={bare?undefined:metric.title} tag={metric.lowerIsBetter?'menos es mejor':undefined} wide={wide}
    note={`${metric.note}${alone} ${sampleOf(results,table)}`.trim()}>
    {chart.hasData
      ?<BarChart title={`${metric.title} por motor`} chart={chart} unit={metric.unit} lowerIsBetter={metric.lowerIsBetter} size={size}/>
      :<Empty>Sin datos en esta corrida.{why}</Empty>}
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

/** The headline: decompilers by the Índice Flash, with how it is computed. */
export function IndexSection({results}:{results:BenchResults}){
  const table=results.tables.decompilers;
  if(!table)return null;
  return <section className="bench-card" aria-labelledby="bench-index">
    <header className="bench-section-head">
      <div><h3 id="bench-index">Índice Flash</h3><p className="bench-lead">Una nota de 0 a 100 por descompilador: cuánto de lo que emite está completo, recompila, se comporta igual que el original y cubre las unidades del SWF. JPEXS compite como los demás.</p></div>
      <Legend items={legendOf(table)}/>
    </header>
    <MetricPanel results={results} table={table} metric={metrics.flash} size="lg" wide bare/>
    <details className="bench-how">
      <summary>Cómo se calcula</summary>
      <p><strong>Índice Flash = (fuente + recompila + misma traza + unidades con fuente) / 4</strong>, cada término en porcentaje.</p>
      <ul>
        <li><strong>Fuente</strong>: casos en que el motor emite fuente completo de todas las unidades del SWF.</li>
        <li><strong>Recompila</strong>: casos en que ese fuente compila con un compilador independiente (Flex para AS3, Rascal para AS2).</li>
        <li><strong>Misma traza</strong>: casos en que, además, Ruffle imprime la misma traza que el SWF original.</li>
        <li><strong>Unidades con fuente</strong>: media, por caso, de las unidades del propio SWF (clases, scripts y acciones de su inventario ABC y AVM1) para las que el motor emitió fuente verificado.</li>
      </ul>
      <p>Los tres primeros se cuentan sobre los casos medidos del motor. Los cuatro términos se miden igual en todos los motores, JPEXS incluido, y ninguno usa la salida de otro descompilador: la cercanía y la cobertura de JPEXS no entran en el índice y se muestran aparte. Solo se ordenan motores medidos sobre los mismos casos; los demás van aparte. Es una nota compuesta: el ganador por comportamiento (misma traza) puede no ser el primero del índice. Una corrida anterior al recuento de unidades no tiene índice.</p>
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
