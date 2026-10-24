import {BenchLine,BenchTable as Table} from '../../types';
import {percentText} from '../../shell/util';
import {classify,engineName,metrics,plural,secondsLabel,slotOf,valueLabel} from './model';

const dash='—';
const seconds=(line:BenchLine)=>typeof line.median_seconds==='number'?secondsLabel(line.median_seconds):dash;

/** Name of an engine in a table row, with its colour and why it is not ranked, if it is not. */
function Who({line,ranked}:{line:BenchLine;ranked:boolean}){
  return <>
    <i className={`bench-dot s${slotOf(line.engine)}`} aria-hidden="true"/>{engineName(line)}
    {line.reference?<span className="badge" title="JPEXS es la referencia de la cercanía y la cobertura (100 % por definición); en todo lo demás compite como un motor más">referencia</span>:null}
    {!ranked&&!line.reference&&line.cases>0?<span className="badge" title="Medido sobre otros casos que los demás: no se ordena con el resto">otros casos</span>:null}
  </>;
}

/** The numbers behind every chart, one row per engine, and the reasons for every gap: the text twin of the charts. */
export default function BenchTable({category,table}:{category:string;table:Table}){
  const decompilers=category==='decompilers';
  const ranked=classify(table).ranked;
  const best=table.engines.find(line=>line.engine===table.winner);
  return <details className="bench-details">
    <summary>Tabla completa y motivos · {plural(table.engines.length,'motor','motores')}</summary>
    <div className="table-scroll">
      <table className="data-table bench-table">
        <caption className="bench-sr">Resultados de {table.unit}</caption>
        {decompilers
          ?<thead><tr><th scope="col">Motor</th><th scope="col">Casos</th><th scope="col">Genera fuente</th><th scope="col">Recompila</th><th scope="col">Misma traza</th><th scope="col" title="Unidades del SWF (clases, scripts y acciones de su inventario) con fuente verificado, media por caso">Unidades con fuente</th><th scope="col" title="Similitud media del texto generado con el de JPEXS, unidad por unidad">% cerca de JPEXS</th><th scope="col" title="Fracción de las unidades de JPEXS que el motor también emitió">% de unidades de JPEXS</th><th scope="col" title="Media de fuente, recompila, misma traza y unidades con fuente (0 a 100)">Índice Flash</th><th scope="col" title="Mediana de segundos por caso">s por caso</th></tr></thead>
          :<thead><tr><th scope="col">Motor</th><th scope="col">Casos</th><th scope="col">Correctos</th><th scope="col">% correctos</th><th scope="col" title="Mediana de segundos por caso">s por caso</th></tr></thead>}
        <tbody>{table.engines.map(line=>{
          const win=line===best;
          const who=<td className={win?'best':''}><Who line={line} ranked={ranked.includes(line)}/></td>;
          return decompilers
            ?<tr key={line.engine}>{who}
              <td>{line.cases}</td><td>{line.source??dash}</td><td>{line.recompiles??dash}</td><td className={win?'best':''}>{line.same_trace??dash}</td>
              <td data-testid={`units-${line.engine}`} title={typeof line.units_total==='number'?`${line.units_with_source??0} de ${line.units_total} unidades en total`:undefined}>{percentText(line.unit_coverage)}</td>
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
    </div>
    {table.engines.filter(line=>line.reasons?.length).map(line=><div key={line.engine}><strong>{engineName(line)}</strong>
      <ul className="reasons">{line.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul></div>)}
    {decompilers?<p className="bench-note">Las columnas de JPEXS miden parecido con la referencia, no corrección, y no entran en el Índice Flash, que se calcula con fuente, recompila, misma traza y unidades con fuente. El ganador de la tabla se decide solo por comportamiento (recompila y misma traza en Ruffle); el índice ordena la clasificación pero no lo reemplaza. «—» indica que no hay medida: JPEXS no estaba disponible, ninguna unidad verificada coincide o la corrida no midió tiempos ni unidades.</p>:null}
    <p className="bench-note">{best?`Mejor por comportamiento: ${engineName(best)}.`:table.note}</p>
  </details>;
}
