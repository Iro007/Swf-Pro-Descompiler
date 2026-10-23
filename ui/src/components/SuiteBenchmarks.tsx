import {useEffect,useState} from 'react';
import * as api from '../api';
import {BenchData,BenchLine,BenchmarkEngine} from '../types';
import {displayEngineName} from '../displayNames';
import {percentText,problem} from '../shell/util';

const categories:Record<string,string>={decompilers:'Descompiladores',compilers:'Compiladores',disassemblers:'Desensambladores',converters:'Conversores XML',extractors:'Extractores'};
const engineName=(engine:string)=>displayEngineName(engine==='native'?'python':engine);

/** Behaviour counts per engine plus, for decompilers, how close its text is to JPEXS and how many JPEXS units it also emits. */
export function DecompilerRow({line,best}:{line:BenchLine;best:boolean}){
  return <tr>
    <td className={best?'best':''}>{engineName(line.engine)}{line.reference?<span className="badge" title="JPEXS actúa de referencia: vale 100 % por definición">referencia</span>:null}</td>
    <td>{line.cases}</td><td>{line.source}</td><td>{line.recompiles}</td>
    <td className={best?'best':''}>{line.same_trace}</td>
    <td data-testid={`closeness-${line.engine}`}>{percentText(line.jpexs_closeness)}</td>
    <td data-testid={`coverage-${line.engine}`}>{percentText(line.jpexs_coverage)}</td>
  </tr>;
}

export default function SuiteBenchmarks(){
  const [bench,setBench]=useState<BenchData|null>(null);
  const [engines,setEngines]=useState<BenchmarkEngine[]>([]);
  const [error,setError]=useState('');
  const load=(run?:string)=>api.suiteRpc<BenchData>('bench_results',run?{run}:{}).then(value=>{setBench(value);setError('');}).catch(failure=>setError(problem(failure)));
  useEffect(()=>{
    if(!api.native)return;
    void load();
    void api.suiteRpc<{engines:BenchmarkEngine[]}>('benchmark_tools').then(result=>setEngines(result.engines)).catch(()=>undefined);
  },[]);
  const results=bench?.results??null;
  const missing=engines.filter(item=>!item.configured);
  return <div className="bench">
    <p className="panel-muted">¿Lo que genera cada descompilador se vuelve a compilar y se comporta igual en Ruffle? Si la diferencia es menor que el ruido no se declara ganador.</p>
    {error?<p className="panel-error" role="alert">{error}</p>:null}
    {!api.native?<p className="panel-muted">Los resultados requieren la aplicación de escritorio y sus motores locales.</p>:null}
    {!results?<p className="panel-muted">Aún no hay resultados. Ejecuta <code>.\medir-codigo.ps1</code> (modo rápido) o <code>.\medir-codigo.ps1 -Completo</code>.</p>:<>
      {bench!.runs.length>1?<label className="field"><span>Ejecución</span>
        <select value={results.run_id} onChange={event=>void load(event.target.value)}>{bench!.runs.map(item=><option key={item.run_id} value={item.run_id}>{item.run_id} · {item.mode} · {item.cases} casos</option>)}</select></label>:null}
      <p className="panel-muted">{results.mode==='full'?'Completa':'Rápida'} · {results.cases} casos de {results.corpus} · {results.seconds} s</p>
      {Object.entries(results.tables).map(([key,table])=><section key={key} className="card">
        <h3>{categories[key]??key}</h3>
        <p className="panel-muted small">{table.unit}</p>
        <div className="table-scroll"><table className="data-table bench-table">
          {key==='decompilers'
            ?<thead><tr><th scope="col">Motor</th><th scope="col">Casos</th><th scope="col">Genera fuente</th><th scope="col">Recompila</th><th scope="col">Misma traza</th><th scope="col" title="Similitud media del texto generado con el de JPEXS, unidad por unidad">% cerca de JPEXS</th><th scope="col" title="Fracción de las unidades de JPEXS que el motor también emitió">% de unidades de JPEXS</th></tr></thead>
            :<thead><tr><th scope="col">Motor</th><th scope="col">Casos</th><th scope="col">Correctos</th></tr></thead>}
          <tbody>{table.engines.map(line=>{
            const best=line.engine===table.winner;
            return key==='decompilers'?<DecompilerRow key={line.engine} line={line} best={best}/>
              :<tr key={line.engine}><td className={best?'best':''}>{engineName(line.engine)}</td><td>{line.cases}</td><td className={best?'best':''}>{line.cases?line.ok:'—'}</td></tr>;
          })}</tbody>
        </table></div>
        {table.engines.filter(line=>line.reasons?.length).map(line=><div key={line.engine}><strong>{engineName(line.engine)}</strong><ul className="reasons">{line.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul></div>)}
        {key==='decompilers'?<p className="panel-muted small">Las dos últimas columnas miden parecido con JPEXS, que actúa de referencia; no miden corrección. El ganador se decide solo por comportamiento (recompila y misma traza en Ruffle). «—» indica que no hay medida: JPEXS no estaba disponible o ninguna unidad verificada coincide.</p>:null}
        <p className="panel-muted small">{table.winner?`Mejor: ${engineName(table.winner)}`:table.note}</p>
      </section>)}
    </>}
    {missing.length?<section className="card"><h3>Sin medir</h3><ul className="reasons">{missing.map(item=><li key={item.id}><strong>{item.name}</strong> · {item.setup_diagnostic??'sin configurar en este equipo'}</li>)}</ul></section>:null}
  </div>;
}
