import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {ArrowDownToLine,BarChart3,RefreshCw} from 'lucide-react';
import * as api from '../api';
import {displayEngineName} from '../displayNames';
import BenchmarkCatalog from './BenchmarkAnalysis';
import './benchmarks.css';

type Stats={median_ms:number|null;p95_ms?:number|null;peak_tree_rss_bytes?:number|null};
type Quality={source_available:number|null;disassembly_available?:number|null;expected_units_reported:number|null;pcode?:number|null;failed?:number|null;behavior_verified?:number};
type EngineResult={engine:string;status?:string;hot:Stats;cold:Stats;quality:Quality;failures?:unknown[]};
type Report={schema_version:number;created_at?:string;profile?:string;operation?:string;completed_files?:number;scheduled_files?:number;configuration?:{hot_runs?:number;cold_runs?:number;warmups?:number};environment?:{background_cpu_percent?:number;available_ram_bytes?:number};claims?:{parity?:boolean;superiority?:boolean};cases:{relative:string;sha256?:string;engines:EngineResult[]}[]};
type RecentReport={path:string;label:string;created_at?:string|null;profile?:string|null;operation?:string|null;completed_files?:number|null};
type Row={file:string;engine:string;status:string;source:number|null;units:number|null;coverage:number|null;hot:number|null;cold:number|null;memory:number|null;failures:number;pcode:number|null;sourceFailed:number|null};
const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
const number=(value:number|null|undefined,decimals=1)=>finite(value)?value.toLocaleString('es-ES',{maximumFractionDigits:decimals}):'No medido';
const engineClass=(id:string)=>id==='native'||id==='python'?'python':id==='rust'?'rust':id==='jpexs'?'jpexs':'other';
const comparable=(status:string)=>!['output_unmapped','failed','not_configured','not_applicable'].includes(status);
const errorText=(value:unknown)=>value instanceof Error?value.message:String(value);
function BarChart({title,unit,rows,value,note}:{title:string;unit:string;rows:Row[];value:(row:Row)=>number|null;note:string}){
  const maximum=Math.max(unit==='%'?100:1,...rows.map(row=>value(row)).filter(finite));
  return <section className="bench-chart" aria-label={title}>
    <h4>{title}</h4><p className="bench-axis">Archivos y motores · eje X: {unit}</p>
    <div className="bench-bars">{rows.map(row=>{
      const measured=value(row);
      return <div className="bench-bar-row" key={row.file+'|'+row.engine}>
        <div className="bench-bar-label"><span title={row.file}>{row.file}</span><strong>{displayEngineName(row.engine)}</strong><span>{number(measured)}{finite(measured)?' '+unit:''}</span></div>
        {finite(measured)?<div className="bench-track"><div className={'bench-fill '+engineClass(row.engine)+(comparable(row.status)?'':' uncertain')} style={{width:Math.min(100,Math.max(0,measured/maximum*100))+'%'}}/></div>:<div className="bench-missing">{row.status==='output_unmapped'?'Unidades sin correspondencia confirmada':'Medición no disponible'}</div>}
      </div>;
    })}</div><p className="bench-chart-note">{note}</p>
  </section>;
}
function Scatter({rows}:{rows:Row[]}){
  const points=rows.filter(row=>finite(row.hot)&&finite(row.coverage)&&comparable(row.status));
  const width=600,height=280,left=64,right=25,top=25,bottom=51;
  const xMax=Math.max(1,...points.map(row=>row.hot!))*1.12;
  return <section className="bench-chart"><h4>Cobertura frente a tiempo</h4><p className="bench-axis">Cada punto es un archivo y motor con correspondencia confirmada.</p>
    <svg viewBox={'0 0 '+width+' '+height} className="bench-scatter" role="img" aria-label="Tiempo caliente mediano en milisegundos en el eje X y porcentaje de unidades con fuente en el eje Y">
      {[0,25,50,75,100].map(tick=>{const y=top+(height-top-bottom)*(1-tick/100);return <g key={tick}><line className="bench-gridline" x1={left} x2={width-right} y1={y} y2={y}/><text x={left-10} y={y+4} textAnchor="end">{tick}%</text></g>;})}
      {[0,1,2,3,4].map(index=>{const x=left+(width-left-right)*index/4;return <g key={index}><line className="bench-gridline" x1={x} x2={x} y1={top} y2={height-bottom}/><text x={x} y={height-bottom+20} textAnchor="middle">{number(xMax*index/4,0)}</text></g>;})}
      <text x={(left+width-right)/2} y={height-6} textAnchor="middle">Tiempo caliente · mediana (ms)</text>
      <text transform={'translate(16 '+height/2+') rotate(-90)'} textAnchor="middle">Unidades con salida (%)</text>
      {points.map(row=><circle key={row.file+'|'+row.engine} className={'bench-point '+engineClass(row.engine)} cx={left+(width-left-right)*row.hot!/xMax} cy={top+(height-top-bottom)*(1-Math.min(100,Math.max(0,row.coverage!))/100)} r={5}><title>{row.file+' · '+displayEngineName(row.engine)+' · '+number(row.hot)+' ms · '+number(row.coverage)+'%'}</title></circle>)}
    </svg><p className="bench-chart-note">Los puntos muestran cobertura y coste de ejecución. La presencia de fuente no prueba equivalencia de comportamiento.</p>
  </section>;
}

export default function BenchmarkResults({reportPath,active=true}:{reportPath:string|null;active?:boolean}){
  const [recent,setRecent]=useState<RecentReport[]>([]);const [path,setPath]=useState('');const [report,setReport]=useState<Report|null>(null);
  const [loading,setLoading]=useState(false);const [error,setError]=useState('');const [file,setFile]=useState('all');const [engine,setEngine]=useState('all');const [timeMode,setTimeMode]=useState<'hot'|'cold'>('hot');
  const epoch=useRef(0);const mounted=useRef(true);const discovered=useRef(false);const loadedPath=useRef<string|null>(null);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;epoch.current++;};},[]);
  const load=useCallback(async(nextPath:string)=>{
    if(!nextPath.trim())return;const request=++epoch.current;setLoading(true);setError('');
    try{const response=await api.suiteRpc<{path:string;report:Report}>('benchmark_report',{path:nextPath.trim()});if(mounted.current&&request===epoch.current){loadedPath.current=response.path;setPath(response.path);setReport(response.report);setFile('all');setEngine('all');}}
    catch(value){if(mounted.current&&request===epoch.current)setError(errorText(value));}
    finally{if(mounted.current&&request===epoch.current)setLoading(false);}
  },[]);
  const discover=useCallback(async()=>{
    if(!api.native)return;try{const response=await api.suiteRpc<{reports:RecentReport[]}>('benchmark_reports');if(!mounted.current)return;setRecent(response.reports);if(!loadedPath.current&&response.reports.length)await load(response.reports[0].path);}catch(value){if(mounted.current)setError(errorText(value));}
  },[load]);
  useEffect(()=>{if(active&&!discovered.current){discovered.current=true;void discover();}},[active,discover]);
  useEffect(()=>{if(active&&reportPath&&reportPath!==loadedPath.current)void load(reportPath);},[active,reportPath,load]);
  const rows=useMemo<Row[]>(()=>report?.cases.flatMap(item=>item.engines.map(result=>{
    const quality=result.quality;const output=report.operation==='disassemble'?quality.disassembly_available:quality.source_available;
    const units=finite(quality.expected_units_reported)?quality.expected_units_reported:null;
    const source=finite(output)?output:null;
    return {file:item.relative,engine:result.engine,status:result.status??'measured',source,units,coverage:source!==null&&units!==null&&units>0?source/units*100:null,hot:finite(result.hot?.median_ms)?result.hot.median_ms:null,cold:finite(result.cold?.median_ms)?result.cold.median_ms:null,memory:finite(result.hot?.peak_tree_rss_bytes)?result.hot.peak_tree_rss_bytes/1048576:null,failures:result.failures?.length??0,pcode:quality.pcode??null,sourceFailed:quality.failed??null};
  }))??[],[report]);
  const filtered=useMemo(()=>rows.filter(row=>(file==='all'||row.file===file)&&(engine==='all'||row.engine===engine)),[rows,file,engine]);
  const engines=Array.from(new Set(rows.map(row=>row.engine)));const files=Array.from(new Set(rows.map(row=>row.file)));
  const totalFailures=rows.reduce((sum,row)=>sum+row.failures,0);const unmapped=rows.filter(row=>!comparable(row.status)).length;
  return <section className="bench-results" aria-label="Gráficos de benchmarks">
    <BenchmarkCatalog active={active}/>
    <details className="bench-legacy-report"><summary>Inspeccionar un informe individual</summary>
    <div className="bench-results-heading"><div><h3>Resultados y gráficos</h3><p>Fuente, tiempo y memoria del mismo corpus.</p></div><button className="studio-button secondary" disabled={loading||!api.native} onClick={()=>void discover()}><RefreshCw size={14}/>Actualizar informes</button></div>
    <div className="bench-report-loader">
      {recent.length?<label>Informe guardado<select aria-label="Informe de benchmark" value={recent.some(item=>item.path===path)?path:''} disabled={loading} onChange={event=>void load(event.target.value)}><option value="">Seleccionar informe…</option>{recent.map(item=><option key={item.path} value={item.path}>{item.label}{item.profile?' · '+item.profile:''}</option>)}</select></label>:null}
      <label>Archivo benchmark.json<div className="suite-path"><input aria-label="Ruta del informe benchmark JSON" value={path} disabled={loading} onChange={event=>setPath(event.target.value)} placeholder="Ruta de un informe existente"/><button className="studio-button secondary" disabled={loading||!path.trim()||!api.native} onClick={()=>void load(path)}><BarChart3 size={14}/>Ver</button></div></label>
    </div>
    {loading?<p className="suite-muted" role="status">Cargando mediciones…</p>:null}{error?<p className="suite-field-error" role="alert">{error}</p>:null}
    {report?<>
      <div className="bench-stat-strip"><div><strong>{report.cases.length}</strong><span>Archivos del informe</span></div><div><strong>{rows.length}</strong><span>Observaciones de motor</span></div><div><strong>{totalFailures}</strong><span>Fallos de operación</span></div><div><strong>{unmapped}</strong><span>Casos sin comparación válida</span></div></div>
      <div className="bench-context"><strong>{report.profile??'Campaña'}</strong><span>{report.created_at?new Date(report.created_at).toLocaleString('es-VE'):''}</span><span>{report.configuration?.hot_runs??'—'} ejecuciones calientes · {report.configuration?.cold_runs??'—'} frías</span></div>
      {finite(report.environment?.background_cpu_percent)&&report.environment.background_cpu_percent>25?<p className="bench-warning">CPU de fondo al inicio: {number(report.environment.background_cpu_percent)}%. Interpreta los tiempos con esta carga registrada.</p>:null}
      <div className="bench-filters"><label>Archivo<select aria-label="Filtrar gráfico por archivo" value={file} onChange={event=>setFile(event.target.value)}><option value="all">Todos los archivos</option>{files.map(value=><option key={value} value={value}>{value}</option>)}</select></label><label>Motor<select aria-label="Filtrar gráfico por motor" value={engine} onChange={event=>setEngine(event.target.value)}><option value="all">Todos los motores</option>{engines.map(value=><option key={value} value={value}>{displayEngineName(value)}</option>)}</select></label><label>Tiempo<select aria-label="Modalidad del tiempo medido" value={timeMode} onChange={event=>setTimeMode(event.target.value as 'hot'|'cold')}><option value="hot">Caliente · mediana</option><option value="cold">Frío · mediana</option></select></label></div>
      <div className="bench-legend">{engines.map(value=><span key={value}><i className={engineClass(value)}/>{displayEngineName(value)}</span>)}</div>
      <div className="bench-chart-grid">
        <BarChart title={report.operation==='disassemble'?'Cobertura de desensamblado':'Cobertura de fuente reconstruida'} unit="%" rows={filtered} value={row=>row.coverage} note="Porcentaje sobre las unidades enumeradas de cada archivo. Una salida sin correspondencia no se dibuja como cero."/>
        <BarChart title={timeMode==='hot'?'Tiempo de operación caliente':'Tiempo de operación en frío'} unit="ms" rows={filtered} value={row=>row[timeMode]} note={timeMode==='hot'?'Mediana de operaciones exitosas, con parseo nuevo y sin incluir el arranque.':'Mediana con arranque del worker. Diferentes coberturas requieren cautela al comparar.'}/>
        <BarChart title="Memoria del árbol de procesos" unit="MiB" rows={filtered} value={row=>row.memory} note="RSS agregado máximo observado. El muestreo puede omitir picos breves; los valores ausentes se mantienen sin medir."/>
        <Scatter rows={filtered}/>
      </div>
      <div className="bench-data-table"><table><caption>Mediciones por archivo y motor</caption><thead><tr><th>Archivo</th><th>Motor</th><th>Fuente / unidades</th><th>Caliente (ms)</th><th>Frío (ms)</th><th>RSS (MiB)</th><th>Estado</th></tr></thead><tbody>{filtered.map(row=><tr key={row.file+'|'+row.engine}><td>{row.file}</td><td>{displayEngineName(row.engine)}</td><td>{row.source===null?'Sin correspondencia':number(row.source,0)+' / '+number(row.units,0)}</td><td>{number(row.hot)}</td><td>{number(row.cold)}</td><td>{number(row.memory)}</td><td>{row.status==='output_unmapped'?'Sin correspondencia':row.status}</td></tr>)}</tbody></table></div>
      <p className="bench-chart-note">Fuente reconstruida, compilación válida y comportamiento equivalente son mediciones diferentes. Este informe {report.claims?.parity?'declara paridad dentro de su alcance':'no demuestra paridad semántica'}.</p>
      <details><summary>Ubicación del informe y datos</summary><p className="suite-result-path"><ArrowDownToLine size={13}/>{loadedPath.current}</p><pre className="suite-log">{JSON.stringify({profile:report.profile,operation:report.operation,configuration:report.configuration,environment:report.environment,claims:report.claims},null,2)}</pre></details>
    </>:!loading?<p className="suite-muted">Selecciona un informe existente o ejecuta una campaña para ver sus mediciones.</p>:null}
    </details>
  </section>;
}
