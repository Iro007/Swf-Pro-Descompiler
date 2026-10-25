import {useEffect,useId,useMemo,useState} from 'react';
import * as api from '../api';
import {BenchData,BenchResults,BenchmarkEngine} from '../types';
import {problem} from '../shell/util';
import Highlights from './bench/Highlights';
import {CategorySection,DecompilersSection,IndexSection,RobustnessSection,UnitFidelitySection} from './bench/Sections';
import {categoryOrder,dateLabel,decompilerQuestions,highlights,modeLabel,runIdToIso,runLabel} from './bench/model';
import {FocusContext} from './bench/shared';
import './bench/bench.css';

/** What identifies a run: when, how, on what, and the Ruffle build that judged it. */
function RunFacts({results}:{results:BenchResults}){
  const sha=results.oracle?.runtime_sha256;
  const when=dateLabel(results.created_utc??runIdToIso(results.run_id));
  return <ul className="bench-meta" aria-label="Datos de la ejecución">
    <li className="bench-chip"><b>{modeLabel(results.mode)}</b></li>
    {when?<li className="bench-chip">{when}</li>:null}
    <li className="bench-chip">Corpus <b>{results.corpus}</b></li>
    <li className="bench-chip"><b>{results.cases}</b> casos</li>
    <li className="bench-chip"><b>{results.seconds}</b> s en total</li>
    {results.oracle?<li className="bench-chip" title={sha?`SHA-256 ${sha}`:undefined}>Oráculo Ruffle <b>{results.oracle.runtime}</b>{sha?` · ${sha.slice(0,10)}`:''}</li>:null}
    <li className="bench-chip" title={(results.schema_version??1)<2?'Corrida anterior a la medición de tiempos: los gráficos de velocidad quedan sin datos.':(results.schema_version??1)<3?'Corrida anterior a la medición de robustez y de fidelidad por unidad: esas secciones quedan sin datos.':'Esquema con tiempos por caso, robustez ante archivos dañados y fidelidad por unidad'}>Esquema <b>{results.schema_version??1}</b></li>
  </ul>;
}

/**
 * The Benchmarks window: a leaderboard in the manner of an analysis site. Highlights, the Índice Flash, then one section per
 * category with a chart for every metric, the scatter of closeness against speed and, behind a toggle, the full table.
 */
export default function SuiteBenchmarks(){
  const [bench,setBench]=useState<BenchData|null>(null);
  const [engines,setEngines]=useState<BenchmarkEngine[]>([]);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [hover,setHover]=useState<string|null>(null);
  const [pinned,setPinned]=useState<string|null>(null);
  const runField=useId();
  const load=(run?:string)=>{
    setLoading(true);
    return api.suiteRpc<BenchData>('bench_results',run?{run}:{}).then(value=>{setBench(value);setError('');}).catch(failure=>setError(problem(failure))).finally(()=>setLoading(false));
  };
  useEffect(()=>{
    if(!api.native)return;
    void load();
    void api.suiteRpc<{engines:BenchmarkEngine[]}>('benchmark_tools').then(result=>setEngines(result.engines)).catch(()=>undefined);
  },[]);
  const results=bench?.results??null;
  useEffect(()=>setHover(null),[results]);
  const cards=useMemo(()=>results?highlights(results):[],[results]);
  const missing=engines.filter(item=>!item.configured);
  // The two new questions about the decompilers always get a section, with "sin datos en esta corrida" for a run that predates them.
  const categories=results?[...categoryOrder.filter(key=>key in results.tables||(decompilerQuestions.includes(key)&&'decompilers' in results.tables)),...Object.keys(results.tables).filter(key=>!categoryOrder.includes(key))]:[];
  const focus={engine:hover??pinned,set:setHover,pinned,pin:setPinned};
  return <FocusContext.Provider value={focus}>
    <div className="bench" aria-busy={loading||undefined} data-loading={loading?'true':undefined} onPointerLeave={()=>setHover(null)}>
      {error?<p className="panel-error" role="alert">{error}</p>:null}
      {!api.native?<p className="panel-muted">Los resultados requieren la aplicación de escritorio y sus motores locales.</p>:null}
      {!results?<p className="panel-muted">Aún no hay resultados. Ejecuta <code>.\medir-codigo.ps1</code> (modo rápido) o <code>.\medir-codigo.ps1 -Completo</code>.</p>:<>
        <header className="bench-head">
          <div>
            <h3 className="bench-title">Clasificación de motores</h3>
            <p className="bench-lead">¿Lo que genera cada motor se vuelve a compilar y se comporta igual en Ruffle? Mismo corpus y mismo oráculo para todos; los motores que no se midieron sobre los mismos casos no se ordenan entre sí.</p>
          </div>
          <div className="bench-run"><label htmlFor={runField}>Ejecución</label>
            <select id={runField} value={results.run_id} onChange={event=>void load(event.target.value)}>{bench!.runs.map(run=><option key={run.run_id} value={run.run_id}>{runLabel(run)}</option>)}</select></div>
        </header>
        <RunFacts results={results}/>
        <Highlights cards={cards}/>
        <IndexSection results={results}/>
        {categories.map(key=>key==='decompilers'?<DecompilersSection key={key} results={results}/>:key==='robustness'?<RobustnessSection key={key} results={results}/>:key==='unit_fidelity'?<UnitFidelitySection key={key} results={results}/>:<CategorySection key={key} category={key} results={results}/>)}
      </>}
      {missing.length?<section className="bench-section"><header className="bench-section-head"><div><h3>Sin medir en este equipo</h3></div></header>
        <ul className="reasons">{missing.map(item=><li key={item.id}><strong>{item.name}</strong> · {item.setup_diagnostic??'sin configurar en este equipo'}</li>)}</ul></section>:null}
    </div>
  </FocusContext.Provider>;
}
