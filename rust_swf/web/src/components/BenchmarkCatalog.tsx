import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {ChevronLeft,ChevronRight,Database,FileArchive,RefreshCw} from 'lucide-react';
import * as api from '../api';
import {displayEngineName} from '../displayNames';

type Campaign={id:string;label:string;environment:string;platform:string;operation:string;created_at:string|null;run_count:number;artifact_count:number;source_kind:string};
type CatalogRow={campaign_id:string;file:string;artifact_sha256:string|null;engine:string;engine_name:string;version:string|null;build_id?:string;operation:string;environment:string;phase?:string;status:string;observation_status?:string;run_count:number;success_count:number;error_count:number;timeout_count:number;median_ms:number|null;median_error_ms?:number|null;median_timeout_ms?:number|null;peak_tree_rss_bytes:number|null;coverage_percent:number|null;source_available:number|null;expected_units:number|null;compilation:unknown;behavior:unknown;timing_scope?:string;memory_scope?:string;language?:string|null;warnings?:unknown[]};
type Catalog={schema_version:1;database:string;available?:boolean;truncated?:boolean;totals:{campaigns:number;artifacts:number;runs:number;tools:number};campaigns:Campaign[];rows:CatalogRow[];tools:Record<string,unknown>[];claims:{parity:boolean;superiority:boolean;performance_winner:string|null}};
type Metric='median_ms'|'peak_tree_rss_bytes'|'coverage_percent';
type ScopeMetric={canonical_denominator:number;identity_verified:number;source_available:number|null};
type ScopedRow=CatalogRow&{scope_metrics?:Record<string,ScopeMetric>;adapter_mode?:string;family_id?:string;split?:string};
const scopeLabels:Record<string,string>={method:'Métodos',action:'Acciones AS2',function:'Funciones anidadas',class:'Clases completas',script:'Scripts completos',diagnostic:'Diagnósticos'};
const PAGE_SIZE=50;
const CHART_LIMIT=80;
const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
const formatNumber=(value:unknown,decimals=1)=>finite(value)?value.toLocaleString('es-ES',{maximumFractionDigits:decimals}):'No verificado';
const errorText=(value:unknown)=>value instanceof Error?value.message:String(value);
const environmentName=(value:string)=>/windows.*local|local.*windows/i.test(value)?'Windows local':/linux.*cloud|cloud.*linux/i.test(value)?'Linux cloud':value||'Entorno no indicado';
const isLocal=(campaign:Campaign)=>campaign.source_kind==='local_report'||/local/i.test(campaign.environment)||(/windows/i.test(campaign.platform)&&!/cloud/i.test(campaign.source_kind));
const originName=(campaign:Campaign)=>{
  if(/cloud/i.test(campaign.environment+' '+campaign.source_kind))return 'Linux cloud · archivo importado';
  if(isLocal(campaign))return 'Windows local · informe guardado';
  return [environmentName(campaign.environment),campaign.platform,campaign.source_kind].filter(Boolean).join(' · ');
};
const campaignTime=(campaign:Campaign)=>{const value=Date.parse(campaign.created_at??'');return Number.isFinite(value)?value:0;};
const preferredCampaign=(campaigns:Campaign[])=>[...campaigns].sort((left,right)=>Number(isLocal(right))-Number(isLocal(left))||campaignTime(right)-campaignTime(left)||left.id.localeCompare(right.id))[0];
const engineName=(row:Pick<CatalogRow,'engine'|'engine_name'>)=>/^flashdec_/.test(row.engine)?displayEngineName(row.engine):/pyflashkit/i.test(row.engine+' '+row.engine_name)?'pyflashkit':row.engine_name||displayEngineName(row.engine);
const engineColor=(engine:string)=>/flashdec_py/i.test(engine)?'flashdec-py':/flashdec_rs/i.test(engine)?'flashdec-rs':/pyflashkit/i.test(engine)?'pyflashkit':/rust/i.test(engine)?'rust':/jpexs/i.test(engine)?'jpexs':/python|pyswf|native/i.test(engine)?'python':'other';
const unavailableStatuses=new Set(['comparison_unavailable','output_unmapped','not_configured','not_applicable','partial_identity_mapping','bytecode_mismatch','execution_only','indeterminate','unknown']);
const statusNames:Record<string,string>={comparison_unavailable:'Comparación no disponible',output_unmapped:'Salida sin correspondencia',not_configured:'Motor no configurado',not_applicable:'No aplicable',partial_identity_mapping:'Correspondencia parcial',bytecode_mismatch:'Bytecode diferente',failed:'Falló',error:'Error',timeout:'Tiempo agotado',success:'Ejecución exitosa',ok:'Ejecución exitosa',measured:'Medido',completed:'Completado',execution_only:'Sólo ejecución registrada',mixed:'Estados mixtos',indeterminate:'Indeterminado',sample_passed:'Muestra compiló',sample_failed:'Muestra falló',reported_passed_unverified:'Reportado · sin verificación'};
const statusName=(status:string)=>statusNames[status]??status;
const hasFailure=(row:CatalogRow)=>row.error_count>0||row.timeout_count>0||['failed','error','timeout'].includes(row.status);
const withoutComparison=(row:CatalogRow)=>unavailableStatuses.has(row.status)||unavailableStatuses.has(row.observation_status??'');
const phaseName=(value:string)=>({hot:'Caliente (hot)',cold:'Fría (cold)',warmup:'Calentamiento',heap_probe:'Sondeo de memoria',summary:'Resumen',observation:'Observación'}[value]??value);
const rowPhase=(row:CatalogRow)=>row.phase??'observation';
const preferredOperation=(values:string[],campaignOperation?:string)=>campaignOperation&&values.includes(campaignOperation)?campaignOperation:values.includes('source')?'source':values[0]??'';
const preferredPhase=(values:string[])=>values.includes('hot')?'hot':values.includes('summary')?'summary':values[0]??'';
const verification=(value:unknown):string=>{
  if(value===null||value===undefined||value==='')return 'No verificado';
  if(typeof value==='boolean')return value?'Verificado':'No verificado';
  if(finite(value))return formatNumber(value,0);
  if(typeof value==='string')return ['unknown','not_verified','unverified','not_measured','not_run'].includes(value)?'No verificado':statusName(value);
  if(typeof value==='object'){
    const record=value as Record<string,unknown>;
    return verification(record.status??record.verified??record.available);
  }
  return 'No verificado';
};
const dateLabel=(value:string|null)=>{const date=new Date(value??'');return Number.isFinite(date.getTime())?date.toLocaleString('es-VE'):'Fecha no indicada';};

function CatalogChart({title,metric,unit,rows}:{title:string;metric:Metric;unit:string;rows:CatalogRow[]}){
  const displayed=rows.slice(0,CHART_LIMIT);
  const value=(row:CatalogRow)=>{
    const measured=row[metric];
    return finite(measured)?metric==='peak_tree_rss_bytes'?measured/1048576:measured:null;
  };
  const maximum=Math.max(metric==='coverage_percent'?100:1,...displayed.map(value).filter(finite));
  return <section className="bench-chart bench-catalog-chart" aria-label={title}>
    <h4>{title}</h4><p className="bench-axis">Campaña activa · archivo / motor · {unit}</p>
    <div className="bench-bars">{displayed.map((row,index)=>{
      const measured=value(row);
      const uncertain=withoutComparison(row)||hasFailure(row);
      return <div className="bench-bar-row" key={row.campaign_id+'|'+row.artifact_sha256+'|'+row.file+'|'+row.engine+'|'+row.build_id+'|'+rowPhase(row)+'|'+index}>
        <div className="bench-bar-label"><span title={row.file+' · '+row.operation+' · '+phaseName(rowPhase(row))}>{row.file} · {row.operation} · {phaseName(rowPhase(row))}</span><strong title={row.version??undefined}>{engineName(row)}{row.version?' '+row.version:''}</strong><span>{formatNumber(measured)}{measured!==null?' '+unit:''}</span></div>
        {measured!==null?<div className="bench-track" aria-label={row.file+' · '+engineName(row)+' · '+formatNumber(measured)+' '+unit}><div className={'bench-fill '+engineColor(row.engine)+(uncertain?' uncertain':'')} style={{width:Math.min(100,Math.max(0,measured/maximum*100))+'%'}}/></div>:<div className="bench-missing">No verificado · {statusName(row.status)}</div>}
      </div>;
    })}</div>
    {rows.length>CHART_LIMIT?<p className="bench-chart-note">Se dibujan las primeras {CHART_LIMIT} filas por archivo y motor. La tabla permite recorrer las {formatNumber(rows.length,0)} filas filtradas.</p>:null}
    <p className="bench-chart-note">{metric==='coverage_percent'?'Cobertura de salida registrada; fuente, compilación y comportamiento se verifican por separado.':metric==='median_ms'?'Mediana de ejecuciones exitosas del origen; el éxito de ejecución no prueba equivalencia. El gráfico no establece un ganador.':'RSS máximo del árbol de procesos, según el origen. Un valor ausente permanece sin verificar.'}</p>
  </section>;
}

function ToolInventory({tools,rows}:{tools:Record<string,unknown>[];rows:CatalogRow[]}){
  return <details><summary>Herramientas y versiones registradas</summary><div className="bench-data-table"><table><caption>Identidad de las herramientas del catálogo</caption><thead><tr><th>Herramienta</th><th>ID</th><th>Versiones registradas</th><th>Builds observados</th></tr></thead><tbody>{tools.map((tool,index)=>{
    const id=String(tool.id??tool.engine??'');
    const name=String(tool.name??tool.engine_name??id);
    const metadata=typeof tool.metadata==='object'&&tool.metadata!==null?tool.metadata as Record<string,unknown>:{};
    const observed=rows.filter(row=>row.engine===id);
    const versions=new Set(observed.map(row=>row.version).filter((value):value is string=>Boolean(value)));
    const reported=tool.version??metadata.version??metadata.tool_version;
    if(typeof reported==='string'||finite(reported))versions.add(String(reported));
    const buildCount=new Set(observed.map(row=>row.build_id).filter(Boolean)).size;
    return <tr key={id+'|'+index}><td>{engineName({engine:id,engine_name:name})}</td><td>{id||'No verificado'}</td><td>{versions.size?Array.from(versions).join(' · '):'No verificado'}</td><td>{buildCount||'No verificado'}</td></tr>;
  })}</tbody></table></div><p className="bench-chart-note">Las versiones y builds mostrados conservan sus filas individuales. pyflashkit y PySWF tienen identidades separadas.</p></details>;
}

export default function BenchmarkCatalog({active=true}:{active?:boolean}){
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [campaignId,setCampaignId]=useState('');
  const [environment,setEnvironment]=useState('all');
  const [operation,setOperation]=useState('all');
  const [phase,setPhase]=useState('default');
  const [engine,setEngine]=useState('all');
  const [scope,setScope]=useState('legacy');
  const [adapterMode,setAdapterMode]=useState('all');
  const [page,setPage]=useState(0);
  const mounted=useRef(true);
  const discovered=useRef(false);
  const epoch=useRef(0);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;discovered.current=false;epoch.current++;};},[]);
  const load=useCallback(async()=>{
    if(!api.native)return;
    const request=++epoch.current;
    setLoading(true);setError('');
    try{
      const response=await api.suiteRpc<Catalog>('benchmark_catalog');
      if(response.schema_version!==1||!Array.isArray(response.campaigns)||!Array.isArray(response.rows)||!response.totals)throw new Error('El catálogo recibido no tiene el formato esperado.');
      if(mounted.current&&request===epoch.current){
        setCatalog(response);
        setCampaignId(current=>response.campaigns.some(campaign=>campaign.id===current)?current:preferredCampaign(response.campaigns)?.id??'');
        setPage(0);
      }
    }catch(value){if(mounted.current&&request===epoch.current)setError(errorText(value));}
    finally{if(mounted.current&&request===epoch.current)setLoading(false);}
  },[]);
  useEffect(()=>{if(active&&!discovered.current){discovered.current=true;void load();}},[active,load]);
  const campaigns=useMemo(()=>catalog?.campaigns??[],[catalog]);
  const campaignOperations=useMemo(()=>{
    const index=new Map<string,Set<string>>();
    for(const row of catalog?.rows??[]){const values=index.get(row.campaign_id)??new Set<string>();values.add(row.operation);index.set(row.campaign_id,values);}
    return index;
  },[catalog]);
  const campaignOptions=useMemo(()=>campaigns.filter(campaign=>(environment==='all'||campaign.environment===environment)&&(operation==='all'||campaign.operation===operation||campaignOperations.get(campaign.id)?.has(operation))).sort((left,right)=>campaignTime(right)-campaignTime(left)),[campaigns,campaignOperations,environment,operation]);
  const selected=campaignOptions.find(campaign=>campaign.id===campaignId)??preferredCampaign(campaignOptions);
  const campaignRows=useMemo(()=>catalog?.rows.filter(row=>row.campaign_id===selected?.id)??[],[catalog,selected?.id]);
  const selectedOperations=useMemo(()=>Array.from(new Set(campaignRows.map(row=>row.operation))).sort(),[campaignRows]);
  const activeOperation=operation==='all'?preferredOperation(selectedOperations,selected?.operation):operation;
  const phases=useMemo(()=>Array.from(new Set(campaignRows.filter(row=>row.operation===activeOperation).map(rowPhase))).sort(),[campaignRows,activeOperation]);
  const activePhase=phases.includes(phase)?phase:preferredPhase(phases);
  const engines=useMemo(()=>Array.from(new Map(campaignRows.map(row=>[row.engine,engineName(row)])).entries()).sort((left,right)=>left[1].localeCompare(right[1])),[campaignRows]);
  const scopes=useMemo(()=>Array.from(new Set(campaignRows.flatMap(row=>Object.keys((row as ScopedRow).scope_metrics??{})))).sort(),[campaignRows]);
  const activeScope=scopes.includes(scope)?scope:'legacy';
  const adapterModes=useMemo(()=>Array.from(new Set(campaignRows.map(row=>(row as ScopedRow).adapter_mode).filter((value):value is string=>Boolean(value)))).sort(),[campaignRows]);
  const activeAdapterMode=adapterModes.includes(adapterMode)?adapterMode:'all';
  const filtered=useMemo(()=>campaignRows.filter(row=>(environment==='all'||row.environment===environment)&&row.operation===activeOperation&&rowPhase(row)===activePhase&&(engine==='all'||row.engine===engine)&&(activeAdapterMode==='all'||(row as ScopedRow).adapter_mode===activeAdapterMode)).map(row=>{
    if(activeScope==='legacy')return row;
    const metric=(row as ScopedRow).scope_metrics?.[activeScope];
    const verified=metric&&metric.identity_verified===metric.canonical_denominator&&metric.canonical_denominator>0;
    return {...row,source_available:verified?metric.source_available:null,expected_units:metric?.canonical_denominator??null,
      coverage_percent:verified&&finite(metric.source_available)?100*metric.source_available/metric.canonical_denominator:null};
  }).sort((left,right)=>left.file.localeCompare(right.file)||engineName(left).localeCompare(engineName(right))||(left.version??'').localeCompare(right.version??'')),[campaignRows,environment,activeOperation,activePhase,engine,activeAdapterMode,activeScope]);
  const environments=useMemo(()=>Array.from(new Set(campaigns.map(campaign=>campaign.environment))).sort(),[campaigns]);
  const operations=useMemo(()=>Array.from(new Set([...(catalog?.rows.map(row=>row.operation)??[]),...campaigns.map(campaign=>campaign.operation).filter(value=>value!=='mixed')])).sort(),[catalog,campaigns]);
  const pageCount=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE));
  const currentPage=Math.min(page,pageCount-1);
  const pageRows=filtered.slice(currentPage*PAGE_SIZE,(currentPage+1)*PAGE_SIZE);
  const failures=filtered.reduce((sum,row)=>sum+(row.error_count??0)+(row.timeout_count??0),0);
  const unverified=filtered.filter(withoutComparison).length;
  const cloudCampaigns=campaigns.filter(campaign=>/cloud/i.test(campaign.environment+' '+campaign.source_kind));
  const localCampaigns=campaigns.filter(isLocal);
  const clearDependentFilters=()=>{setCampaignId('');setPhase('default');setEngine('all');setPage(0);};
  return <section className="bench-catalog" aria-label="Catálogo unificado de benchmarks">
    <div className="bench-results-heading"><div><h3><Database size={17}/>Catálogo unificado</h3><p>Campañas guardadas e importadas, con su entorno y evidencia.</p></div><button type="button" className="studio-button secondary" disabled={loading||!api.native} onClick={()=>void load()}><RefreshCw size={14}/>Actualizar catálogo</button></div>
    {loading?<p className="suite-muted" role="status">Leyendo el catálogo de mediciones…</p>:null}
    {error?<p className="suite-field-error" role="alert">{error}</p>:null}
    {!api.native?<p className="suite-muted">El catálogo se consulta desde RustSWF Studio de escritorio.</p>:null}
    {catalog?<>
      <div className="bench-stat-strip bench-catalog-totals"><div><strong>{formatNumber(catalog.totals.campaigns,0)}</strong><span>Campañas registradas</span></div><div><strong>{formatNumber(catalog.totals.artifacts,0)}</strong><span>Artefactos registrados</span></div><div><strong>{formatNumber(catalog.totals.runs,0)}</strong><span>Ejecuciones registradas</span></div><div><strong>{formatNumber(catalog.totals.tools,0)}</strong><span>Herramientas registradas</span></div></div>
      {catalog.truncated?<p className="bench-warning">La API devolvió una parte del catálogo ({formatNumber(catalog.rows.length,0)} filas). Los gráficos, versiones y tablas sólo muestran esa parte; los totales corresponden a la base completa.</p>:null}
      {catalog.available===false?<p className="suite-muted">Todavía no hay una base de benchmarks disponible. Importa una campaña mediante la CLI para registrarla aquí.</p>:null}
      <div className="bench-catalog-sources" aria-label="Fuentes de las campañas"><span className="bench-source local">Windows local <strong>{localCampaigns.length}</strong></span><span className="bench-source cloud">Linux cloud <strong>{cloudCampaigns.length}</strong></span><span>Los gráficos usan una sola campaña. Los tiempos entre entornos no se combinan.</span></div>
      <div className="bench-filters"><label>Alcance de la fuente<select aria-label="Alcance de la fuente" value={activeScope} onChange={event=>{setScope(event.target.value);setPage(0);}}><option value="legacy">Recuento histórico del informe</option>{scopes.map(value=><option key={value} value={value}>{scopeLabels[value]??value}</option>)}</select></label><label>Modo de la herramienta<select aria-label="Modo de la herramienta" value={activeAdapterMode} onChange={event=>{setAdapterMode(event.target.value);setPage(0);}}><option value="all">Todos los modos registrados</option>{adapterModes.map(value=><option key={value} value={value}>{value}</option>)}</select></label></div>
      {activeScope!=='legacy'?<p className="bench-chart-note">Cobertura de {scopeLabels[activeScope]?.toLowerCase()??activeScope}. La fuente parcial y los inicializadores representados por declaraciones se contabilizan aparte. La compilación y el comportamiento requieren sus propias pruebas. Los tiempos corresponden a la operación completa registrada.</p>:null}
      <div className="bench-filters bench-catalog-filters"><label>Entorno<select aria-label="Filtrar catálogo por entorno" value={environment} onChange={event=>{setEnvironment(event.target.value);clearDependentFilters();}}><option value="all">Todos los entornos</option>{environments.map(value=><option key={value} value={value}>{environmentName(value)}</option>)}</select></label><label>Operación<select aria-label="Filtrar catálogo por operación" value={operation} onChange={event=>{setOperation(event.target.value);clearDependentFilters();}}><option value="all">Predeterminada por campaña</option>{operations.map(value=><option key={value} value={value}>{value}</option>)}</select></label><label className="bench-catalog-campaign">Campaña<select aria-label="Campaña activa del catálogo" value={selected?.id??''} onChange={event=>{setCampaignId(event.target.value);setPhase('default');setEngine('all');setPage(0);}}>{!selected?<option value="">Sin campañas en este filtro</option>:null}{campaignOptions.map(campaign=><option key={campaign.id} value={campaign.id}>{campaign.label} · {environmentName(campaign.environment)} · {campaign.operation}</option>)}</select></label><label>Fase<select aria-label="Fase activa de las mediciones" value={activePhase} onChange={event=>{setPhase(event.target.value);setPage(0);}}>{!activePhase?<option value="">Sin fases registradas</option>:null}{phases.map(value=><option key={value} value={value}>{phaseName(value)}</option>)}</select></label><label>Motor<select aria-label="Filtrar catálogo por motor" value={engine} onChange={event=>{setEngine(event.target.value);setPage(0);}}><option value="all">Todos los motores</option>{engines.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label></div>
      {selected?<>
        <div className="bench-context bench-catalog-context"><strong>{selected.label}</strong><span>{originName(selected)}</span><span>{activeOperation||'Sin operación'} · {phaseName(activePhase)||'Sin fase'}</span><span>{dateLabel(selected.created_at)}</span><span>{formatNumber(selected.run_count,0)} ejecuciones · {formatNumber(selected.artifact_count,0)} artefactos</span></div>
        <div className="bench-catalog-state"><span>{formatNumber(filtered.length,0)} filas filtradas</span><span className={failures?'has-failures':''}>{formatNumber(failures,0)} errores / timeouts</span><span>{formatNumber(unverified,0)} filas sin comparación confirmada</span></div>
        {filtered.length?<>
          <div className="bench-legend bench-catalog-legend">{engines.map(([id,name])=><span key={id}><i className={engineColor(id)}/>{name}</span>)}</div>
          <div className="bench-chart-grid bench-catalog-charts"><CatalogChart title="Tiempo de operación" metric="median_ms" unit="ms" rows={filtered}/><CatalogChart title="Memoria del árbol de procesos" metric="peak_tree_rss_bytes" unit="MiB" rows={filtered}/><CatalogChart title="Cobertura registrada" metric="coverage_percent" unit="%" rows={filtered}/></div>
          <div className="bench-data-table bench-catalog-table"><table><caption>Campaña: {selected.label} · {environmentName(selected.environment)} · {activeOperation} · {phaseName(activePhase)}</caption><thead><tr><th>Archivo / SHA-256</th><th>Motor / versión / build</th><th>Operación / fase</th><th>Estado de ejecución / salida</th><th>Ejecuciones</th><th>Éxitos</th><th>Errores</th><th>Timeouts</th><th>Mediana exitosa (ms)</th><th>Hasta error (ms)</th><th>Hasta timeout (ms)</th><th>RSS (MiB)</th><th>Cobertura (%)</th><th>Fuente / unidades</th><th>Compilación</th><th>Comportamiento</th></tr></thead><tbody>{pageRows.map((row,index)=><tr key={row.campaign_id+'|'+row.artifact_sha256+'|'+row.engine+'|'+row.build_id+'|'+rowPhase(row)+'|'+index}><td><span title={row.file}>{row.file}</span><small title={row.artifact_sha256??undefined}>{row.artifact_sha256?row.artifact_sha256.slice(0,12)+'…':'SHA-256 no verificado'}{row.language?' · '+row.language:''}</small></td><td>{engineName(row)}<small>{row.version||'Versión no verificada'}</small><small title={row.build_id}>{row.build_id?row.build_id.slice(0,10)+'…':'Build no verificado'}</small></td><td>{row.operation}<small>{phaseName(rowPhase(row))}</small></td><td><span className={'bench-catalog-status '+(hasFailure(row)?'failed':withoutComparison(row)?'unverified':'recorded')} title={row.status}>{statusName(row.status)}</span>{row.observation_status&&row.observation_status!==row.status?<small>{statusName(row.observation_status)}</small>:null}</td><td>{formatNumber(row.run_count,0)}</td><td>{formatNumber(row.success_count,0)}</td><td>{formatNumber(row.error_count,0)}</td><td>{formatNumber(row.timeout_count,0)}</td><td title={row.timing_scope}>{formatNumber(row.median_ms)}</td><td>{formatNumber(row.median_error_ms)}</td><td>{formatNumber(row.median_timeout_ms)}</td><td title={row.memory_scope}>{formatNumber(finite(row.peak_tree_rss_bytes)?row.peak_tree_rss_bytes/1048576:null)}</td><td>{formatNumber(row.coverage_percent)}</td><td>{formatNumber(row.source_available,0)} / {formatNumber(row.expected_units,0)}</td><td>{verification(row.compilation)}</td><td>{verification(row.behavior)}</td></tr>)}</tbody></table></div>
          <div className="bench-catalog-pagination"><span>Filas {currentPage*PAGE_SIZE+1}–{Math.min(filtered.length,(currentPage+1)*PAGE_SIZE)} de {formatNumber(filtered.length,0)} · página {currentPage+1} de {pageCount}</span><div><button type="button" className="studio-button secondary" aria-label="Página anterior del catálogo" disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}><ChevronLeft size={13}/>Anterior</button><button type="button" className="studio-button secondary" aria-label="Página siguiente del catálogo" disabled={currentPage>=pageCount-1} onClick={()=>setPage(currentPage+1)}>Siguiente<ChevronRight size={13}/></button></div></div>
          <details><summary>Alcance temporal y de memoria de la selección</summary><p className="bench-chart-note">Cada fila conserva la fase y el alcance registrados por el origen. El nombre de una fase no garantiza que el proceso externo siga abierto.</p><ul className="bench-catalog-scopes">{Array.from(new Set(filtered.map(row=>row.timing_scope).filter(Boolean))).map(value=><li key={'time|'+value}>Tiempo: {value}</li>)}{Array.from(new Set(filtered.map(row=>row.memory_scope).filter(Boolean))).map(value=><li key={'memory|'+value}>Memoria: {value}</li>)}</ul></details>
        </>:<p className="suite-muted">{catalog.truncated?'No hay filas de esta selección en la parte del catálogo recibida.':'No hay mediciones para esta selección.'} Los valores ausentes no se sustituyen por cero.</p>}
      </>:<p className="suite-muted">No hay campañas registradas para estos filtros.</p>}
      <p className="bench-chart-note">El catálogo no demuestra paridad, superioridad ni un ganador de rendimiento. pyflashkit y PySWF son herramientas distintas. Consulta los informes detallados debajo para revisar las condiciones de cada medición.</p>
      <ToolInventory tools={catalog.tools??[]} rows={catalog.rows}/>
      <details><summary>Procedencia e importación</summary><p className="bench-catalog-import"><FileArchive size={14}/>Importa el ZIP de una campaña mediante la CLI del workspace y actualiza el catálogo. Esta vista sólo lee mediciones guardadas.</p><p className="suite-result-path">Base de datos: {catalog.database}</p><div className="bench-data-table"><table><caption>Fuentes registradas</caption><thead><tr><th>Campaña</th><th>Entorno</th><th>Plataforma</th><th>Origen</th><th>Operación</th></tr></thead><tbody>{campaigns.map(campaign=><tr key={campaign.id}><td>{campaign.label}</td><td>{environmentName(campaign.environment)}</td><td>{campaign.platform||'No verificado'}</td><td>{campaign.source_kind||'No verificado'}</td><td>{campaign.operation}</td></tr>)}</tbody></table></div><p className="bench-chart-note">Orden alfabético de archivo y motor; ninguna fila se ordena por tiempo, memoria o cobertura.</p></details>
    </>:!loading&&api.native?<p className="suite-muted">Actualiza el catálogo para consultar las campañas guardadas.</p>:null}
  </section>;
}
