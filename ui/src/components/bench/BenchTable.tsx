import {BenchLine,BenchRow,BenchTable as Table,Outcome} from '../../types';
import {percentText} from '../../shell/util';
import {classify,engineName,metrics,outcomeSpecs,plural,secondsLabel,slotOf,termsOf,indexTerms,valueLabel} from './model';

const dash='—';
const seconds=(line:BenchLine)=>typeof line.median_seconds==='number'?secondsLabel(line.median_seconds):dash;
const ratio=(value:number|null|undefined)=>percentText(value);

/** Name of an engine in a table row, with its colour and why it is not ranked, if it is not. */
function Who({line,ranked}:{line:BenchLine;ranked:boolean}){
  return <>
    <i className={`bench-dot s${slotOf(line.engine)}`} aria-hidden="true"/>{engineName(line)}
    {line.reference?<span className="badge" title="JPEXS es la referencia de la cercanía y la cobertura (100 % por definición); en todo lo demás compite como un motor más">referencia</span>:null}
    {!ranked&&!line.reference&&line.cases>0?<span className="badge" title="Medido sobre otros casos que los demás: no se ordena con el resto">otros casos</span>:null}
  </>;
}

/** The reasons an engine gave for every gap, under the table. */
function Reasons({table}:{table:Table}){
  return <>{table.engines.filter(line=>line.reasons?.length).map(line=><div key={line.engine}><strong>{engineName(line)}</strong>
    <ul className="reasons">{line.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul></div>)}</>;
}

const outcomeShort=Object.fromEntries(outcomeSpecs.map(spec=>[spec.id,spec.short])) as Record<Outcome,string>;

/** Robustness: the outcomes per engine, the score per kind of damage and, for every damaged file, what each engine did. */
function RobustnessTable({table,rows}:{table:Table;rows?:BenchRow[]}){
  const ranked=classify(table).ranked;
  const kinds=Object.keys(table.kinds??{}).filter(kind=>table.engines.some(line=>line.by_kind?.[kind]));
  const engines=table.engines.filter(line=>line.cases>0);
  const files=[...new Map((rows??[]).filter(row=>row.kind).map(row=>[row.case,row])).values()];
  const by=new Map((rows??[]).map(row=>[`${row.engine}|${row.case}`,row]));
  return <>
    <div className="table-scroll">
      <table className="data-table bench-table">
        <caption className="bench-sr">Resultados de {table.unit}</caption>
        <thead><tr><th scope="col">Motor</th><th scope="col">Archivos</th>{outcomeSpecs.map(spec=><th key={spec.id} scope="col" title={spec.label}>{spec.short[0].toUpperCase()+spec.short.slice(1)}</th>)}
          <th scope="col" title="Media de los desenlaces: recuperó 100, parcial 60, error claro 40, caída 0, cuelgue 0">Robustez</th>
          <th scope="col" title="Unidades intactas del original (ningún byte dañado las toca) para las que el motor emitió fuente verificado, media por archivo con algo intacto">Unidades recuperadas</th>
          <th scope="col" title="Mediana de segundos por archivo que dedica el motor a abrirlo y rebuscar su código">s por archivo</th>
          <th scope="col" title="Pico de memoria comprometida de todo el árbol de procesos (incluye el worker de Python)">MiB pico</th></tr></thead>
        <tbody>{table.engines.map(line=>{
          const win=line.engine===table.winner;
          return <tr key={line.engine}>
            <td className={win?'best':''}><Who line={line} ranked={ranked.includes(line)}/></td>
            <td>{line.cases}</td>
            {outcomeSpecs.map(spec=><td key={spec.id} data-testid={`${spec.id}-${line.engine}`}>{line.cases?line.outcomes?.[spec.id]??dash:dash}</td>)}
            <td className={win?'best':''} data-testid={`robustness-${line.engine}`}>{typeof line.robustness==='number'?valueLabel('index',line.robustness):dash}</td>
            <td data-testid={`recovery-${line.engine}`} title={typeof line.intact_units==='number'?`${line.recovered_units??0} de ${line.intact_units} unidades intactas`:undefined}>{ratio(line.unit_recovery)}</td>
            <td data-testid={`seconds-${line.engine}`}>{seconds(line)}</td>
            <td>{typeof line.peak_memory_mib==='number'?Math.round(line.peak_memory_mib):dash}</td></tr>;
        })}</tbody>
      </table>
    </div>
    {kinds.length?<>
      <h5 className="bench-subhead small">Nota por tipo de daño</h5>
      <div className="table-scroll">
        <table className="data-table bench-table">
          <caption className="bench-sr">Nota de robustez por tipo de daño</caption>
          <thead><tr><th scope="col">Motor</th>{kinds.map(kind=><th key={kind} scope="col" title={table.kinds![kind]}>{table.kinds![kind]}</th>)}</tr></thead>
          <tbody>{engines.map(line=><tr key={line.engine}><td><Who line={line} ranked={ranked.includes(line)}/></td>
            {kinds.map(kind=>{const part=line.by_kind?.[kind];return <td key={kind} title={part?.outcomes?outcomeSpecs.map(spec=>`${spec.short} ${part.outcomes![spec.id]}`).join(' · '):undefined}>{typeof part?.score==='number'?valueLabel('index',part.score):dash}</td>;})}</tr>)}</tbody>
        </table>
      </div>
    </>:null}
    {files.length?<details className="bench-details nested">
      <summary>Desenlace de cada archivo dañado · {plural(files.length,'archivo','archivos')}</summary>
      <div className="table-scroll">
        <table className="data-table bench-table files">
          <caption className="bench-sr">Qué hizo cada motor con cada archivo dañado</caption>
          <thead><tr><th scope="col">Archivo dañado</th>{engines.map(line=><th key={line.engine} scope="col"><i className={`bench-dot s${slotOf(line.engine)}`} aria-hidden="true"/>{engineName(line)}</th>)}</tr></thead>
          <tbody>{files.map(file=><tr key={file.case}><td title={`${table.kinds?.[file.kind??'']??file.kind} · ${file.family}`}>{file.case}</td>
            {engines.map(line=>{
              const row=by.get(`${line.engine}|${file.case}`);
              const outcome=row?.outcome;
              return <td key={line.engine} title={row?.reason||undefined}>{outcome?<span className={`out-chip seg-${outcome}`}>{outcomeShort[outcome]}</span>:dash}</td>;
            })}</tr>)}</tbody>
        </table>
      </div>
      <p className="bench-note">Pasa el puntero por un desenlace para ver el motivo (el mensaje del motor o por qué se clasificó así). «—» indica que el motor no lee el lenguaje del archivo o no está instalado.</p>
    </details>:null}
  </>;
}

/** Per-unit fidelity: the outcomes per engine and kind of unit, and the units that did not end with the same trace. */
function UnitsTable({table,rows}:{table:Table;rows?:BenchRow[]}){
  const ranked=classify(table).ranked;
  const kinds=Object.keys(table.kinds??{});
  const notable=(rows??[]).flatMap(row=>(row.details??[]).map(detail=>({...detail,engine:row.engine,case:row.family}))).slice(0,80);
  return <>
    <div className="table-scroll">
      <table className="data-table bench-table">
        <caption className="bench-sr">Resultados de {table.unit}</caption>
        <thead><tr><th scope="col">Motor</th><th scope="col">Casos</th><th scope="col">Evaluadas</th><th scope="col">Misma traza</th><th scope="col">Otra traza</th><th scope="col">No compila</th>
          <th scope="col" title="La traza del caso nunca ejecuta la unidad: no se juzga">No se ejecuta</th>
          <th scope="col" title="Misma traza / (misma + otra traza); no compila y no se ejecuta quedan fuera">Fidelidad</th>
          {kinds.map(kind=><th key={kind} scope="col" title={`Fidelidad de ${table.kinds![kind].toLowerCase()}`}>{table.kinds![kind]}</th>)}</tr></thead>
        <tbody>{table.engines.map(line=>{
          const win=line.engine===table.winner;
          return <tr key={line.engine}>
            <td className={win?'best':''}><Who line={line} ranked={ranked.includes(line)}/></td>
            <td>{line.cases}</td><td>{line.cases?line.evaluated??dash:dash}</td><td>{line.cases?line.same??dash:dash}</td><td>{line.cases?line.different??dash:dash}</td>
            <td>{line.cases?line.no_compile??dash:dash}</td><td>{line.cases?line.not_run??dash:dash}</td>
            <td className={win?'best':''} data-testid={`fidelity-${line.engine}`} title={typeof line.judged==='number'?`${line.same??0} de ${line.judged} unidades juzgadas`:undefined}>{ratio(line.fidelity)}</td>
            {kinds.map(kind=>{const part=line.by_kind?.[kind];return <td key={kind} title={typeof part?.judged==='number'?`${part.same??0} de ${part.judged} unidades juzgadas`:undefined}>{ratio(part?.fidelity)}</td>;})}</tr>;
        })}</tbody>
      </table>
    </div>
    {notable.length?<details className="bench-details nested">
      <summary>Unidades que no terminaron con la misma traza · {notable.length}{notable.length>=80?' o más':''}</summary>
      <div className="table-scroll">
        <table className="data-table bench-table files">
          <caption className="bench-sr">Unidades sin la misma traza</caption>
          <thead><tr><th scope="col">Motor</th><th scope="col">Caso</th><th scope="col">Unidad</th><th scope="col">Resultado</th><th scope="col">Motivo</th></tr></thead>
          <tbody>{notable.map((item,index)=><tr key={index}><td><i className={`bench-dot s${slotOf(item.engine)}`} aria-hidden="true"/>{engineName({engine:item.engine})}</td><td>{item.case}</td><td>{item.unit}</td>
            <td><span className={`out-chip seg-${item.outcome}`}>{{different:'otra traza',no_compile:'no compila',not_run:'no se ejecuta',same:'misma'}[item.outcome]}</span></td><td className="reason">{item.reason}</td></tr>)}</tbody>
        </table>
      </div>
    </details>:null}
  </>;
}

/** The numbers behind every chart, one row per engine, and the reasons for every gap: the text twin of the charts. */
export default function BenchTable({category,table,rows}:{category:string;table:Table;rows?:BenchRow[]}){
  const decompilers=category==='decompilers';
  const ranked=classify(table).ranked;
  const best=table.engines.find(line=>line.engine===table.winner);
  const terms=decompilers?termsOf(table).map(id=>indexTerms.find(term=>term.id===id)?.label??id).join(', '):'';
  return <details className="bench-details">
    <summary>Tabla completa y motivos · {plural(table.engines.length,'motor','motores')}</summary>
    {category==='robustness'?<RobustnessTable table={table} rows={rows}/>
      :category==='unit_fidelity'?<UnitsTable table={table} rows={rows}/>
      :<div className="table-scroll">
        <table className="data-table bench-table">
          <caption className="bench-sr">Resultados de {table.unit}</caption>
          {decompilers
            ?<thead><tr><th scope="col">Motor</th><th scope="col">Casos</th><th scope="col">Genera fuente</th><th scope="col">Recompila</th><th scope="col">Misma traza</th><th scope="col" title="Unidades del SWF (clases, scripts y acciones de su inventario) con fuente verificado, media por caso">Unidades con fuente</th><th scope="col" title="Unidades (clases, métodos y acciones) cuyo fuente, compilado solo y empalmado en el SWF original, imprime la misma traza">Fidelidad por unidad</th><th scope="col" title="Nota de 0 a 100 ante archivos dañados">Robustez</th><th scope="col" title="Similitud media del texto generado con el de JPEXS, unidad por unidad">% cerca de JPEXS</th><th scope="col" title="Fracción de las unidades de JPEXS que el motor también emitió">% de unidades de JPEXS</th><th scope="col" title={`Media de: ${terms||'—'} (0 a 100)`}>Índice Flash</th><th scope="col" title="Mediana de segundos por caso">s por caso</th></tr></thead>
            :<thead><tr><th scope="col">Motor</th><th scope="col">Casos</th><th scope="col">Correctos</th><th scope="col">% correctos</th><th scope="col" title="Mediana de segundos por caso">s por caso</th></tr></thead>}
          <tbody>{table.engines.map(line=>{
            const win=line===best;
            const who=<td className={win?'best':''}><Who line={line} ranked={ranked.includes(line)}/></td>;
            return decompilers
              ?<tr key={line.engine}>{who}
                <td>{line.cases}</td><td>{line.source??dash}</td><td>{line.recompiles??dash}</td><td className={win?'best':''}>{line.same_trace??dash}</td>
                <td data-testid={`units-${line.engine}`} title={typeof line.units_total==='number'?`${line.units_with_source??0} de ${line.units_total} unidades en total`:undefined}>{percentText(line.unit_coverage)}</td>
                <td data-testid={`unit-fidelity-${line.engine}`} title={typeof line.unit_fidelity_judged==='number'?`${line.unit_fidelity_judged} unidades juzgadas`:undefined}>{percentText(line.unit_fidelity)}</td>
                <td data-testid={`robustness-${line.engine}`}>{typeof line.robustness==='number'?valueLabel('index',line.robustness):dash}</td>
                <td data-testid={`closeness-${line.engine}`}>{percentText(line.jpexs_closeness)}</td>
                <td data-testid={`coverage-${line.engine}`}>{percentText(line.jpexs_coverage)}</td>
                <td data-testid={`index-${line.engine}`}>{typeof line.flash_index==='number'?valueLabel('index',line.flash_index):dash}</td>
                <td data-testid={`seconds-${line.engine}`}>{seconds(line)}</td></tr>
              :<tr key={line.engine}>{who}
                <td>{line.cases}</td><td className={win?'best':''}>{line.cases?line.ok:dash}</td>
                <td>{line.cases?valueLabel('percent',metrics.ok.value(line)!):dash}</td>
                <td data-testid={`seconds-${line.engine}`}>{seconds(line)}</td></tr>;
          })}</tbody>
        </table>
      </div>}
    <Reasons table={table}/>
    {decompilers?<p className="bench-note">Las columnas de paridad con JPEXS miden parecido sintáctico con esa herramienta externa, no corrección semántica, y no entran en el Índice Flash, que se calcula con {terms||'los términos que la corrida midió'}. La fidelidad real se juzga por comportamiento ejecutable (recompila y misma traza en el oráculo Ruffle). «—» indica que no hay medida disponible para ese motor o métrica.</p>:null}
    <p className="bench-note">{best?`${category==='robustness'?'Más robusto':category==='unit_fidelity'?'Más fiel por unidad':'Mejor por comportamiento'}: ${engineName(best)}.`:table.note}</p>
  </details>;
}
