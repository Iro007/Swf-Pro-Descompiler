import {useEffect,useState} from 'react';
import * as api from '../api';
import {displayEngineName} from '../displayNames';
import './bench.css';

type Line={engine:string;name:string;cases:number;source:number;recompiles:number;same_trace:number;reasons:string[]};
type Table={unit:string;winner:string|null;note:string|null;engines:Line[]};
type Results={run_id:string;mode:string;created_utc:string;seconds:number;corpus:string;cases:number;tables:Record<string,Table>};
type Bench={runs:{run_id:string;mode:string;cases:number}[];results:Results|null;folder:string};
type Engine={id:string;name:string;category:string;configured:boolean;operations:string[];setup_diagnostic?:string};
const categories:Record<string,string>={decompilers:'Descompiladores'};
export default function SuiteBenchmarks({available}:{catalog?:unknown;busy?:boolean;run?:unknown;available:boolean}) {
  const [bench,setBench]=useState<Bench|null>(null),[engines,setEngines]=useState<Engine[]>([]),[error,setError]=useState('');
  const load=(run?:string)=>api.suiteRpc<Bench>('bench_results',run?{run}:{}).then(value=>{setBench(value);setError('');}).catch(e=>setError(String(e)));
  useEffect(()=>{if(!api.native||!available)return;void load();void api.suiteRpc<{engines:Engine[]}>('benchmark_tools').then(r=>setEngines(r.engines)).catch(()=>undefined);},[available]);
  const results=bench?.results??null;
  const missing=engines.filter(item=>!item.configured);
  return <div className="suite-pane bench-workspace">
    <h3>Benchmarks</h3>
    <p className="suite-muted">¿Lo que genera cada descompilador se vuelve a compilar, y se comporta igual en Ruffle? Si hay una diferencia menor que el ruido no se declara ganador.</p>
    {error?<p className="suite-field-error">{error}</p>:null}
    {!results?<p className="suite-muted">Aún no hay resultados. Ejecuta <code>.\medir-codigo.ps1</code> (modo rápido) o <code>.\medir-codigo.ps1 -Completo</code>.</p>:<>
      {bench!.runs.length>1?<label className="suite-field"><span>Ejecución</span><select value={results.run_id} onChange={event=>void load(event.target.value)}>{bench!.runs.map(item=><option key={item.run_id} value={item.run_id}>{item.run_id} · {item.mode} · {item.cases} casos</option>)}</select></label>:null}
      <p className="suite-muted">{results.mode==='full'?'Completa':'Rápida'} · {results.cases} casos de {results.corpus} · {results.seconds} s</p>
      {Object.entries(results.tables).map(([key,table])=><section key={key} className="suite-card"><h4>{categories[key]??key}</h4><p className="suite-muted">{table.unit}</p>
        <table className="bench-table"><thead><tr><th>Motor</th><th>Casos</th><th>Genera fuente</th><th>Recompila</th><th>Misma traza</th></tr></thead><tbody>
          {table.engines.map(line=><tr key={line.engine}><td className={line.engine===table.winner?'bench-best':''}>{displayEngineName(line.engine==='native'?'python':line.engine)}</td><td>{line.cases}</td><td>{line.source}</td><td>{line.recompiles}</td><td className={line.engine===table.winner?'bench-best':''}>{line.same_trace}</td></tr>)}
        </tbody></table>
        {table.engines.filter(line=>line.reasons.length).map(line=><div key={line.engine}><strong>{displayEngineName(line.engine==='native'?'python':line.engine)}</strong><ul className="bench-reasons">{line.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul></div>)}
        <p className="suite-muted">{table.winner?`Mejor: ${displayEngineName(table.winner==='native'?'python':table.winner)}`:table.note}</p></section>)}
    </>}
    {missing.length?<section className="suite-card"><h4>Sin medir</h4><ul className="bench-unavailable">{missing.map(item=><li key={item.id}><strong>{item.name}</strong> · {item.setup_diagnostic??'sin configurar en este equipo'}</li>)}</ul></section>:null}
  </div>;
}
