/** Pure benchmark selectors. Identity, source, compilation and timing are separate. */
export type ScopeMetric={canonical_denominator:number;identity_verified:number;source_available:number|null;disassembly_available?:number|null};
export type Campaign={id:string;label:string;environment:string;platform:string;operation:string;created_at:string|null;run_count:number;artifact_count:number;source_kind:string};
export type CatalogRow={campaign_id:string;file:string;artifact_sha256:string|null;engine:string;engine_name:string;version:string|null;build_id?:string;operation:string;environment:string;environment_id?:string;phase?:string;status:string;observation_status?:string;run_count:number;success_count:number;error_count:number;timeout_count:number;median_ms:number|null;peak_tree_rss_bytes:number|null;coverage_percent:number|null;source_available:number|null;expected_units:number|null;disassembly_available?:number|null;compilation:unknown;behavior:unknown;quality_evaluation?:unknown;family_id?:string|null;split?:string|null;language?:string|null;adapter_mode?:string|null;inventory_status?:string;canonical_inventory_sha256?:string;scope_metrics?:Record<string,ScopeMetric>;origin_scope_metrics?:Record<string,Record<string,ScopeMetric>>;valid_performance_summary?:boolean|null;timing_scope?:string;memory_scope?:string;warnings?:unknown[]};
export type Catalog={schema_version:1;database:string;available?:boolean;truncated?:boolean;totals:{campaigns:number;artifacts:number;runs:number;tools:number};unit_totals?:{canonical_units:number;unit_observations:number};campaigns:Campaign[];rows:CatalogRow[];tools:Record<string,unknown>[]};
export type Filters={dataset:string;operation:string;scope:string;origin:string;family:string;split:string;language:string;environment:string;phase:string;mode:string;compiler:string;query:string;common:boolean};
export type QualityCount={total:number|null;passed:number;evaluated:number;failed:number;unknown:number;unsupported:number;ineligible:number;unknownProjects:number;percent:number|null;complete:boolean;contextKey:string|null;reason:string};
export type ObservedRow=CatalogRow&{value:number|null;denominator:number|null;verified:boolean;qualityIdentity:boolean;sourceCount:number|null;fileKey:string;timestamp:number;selected_scope:string;selected_compiler:string;code_origin:string;compilationQuality:QualityCount;behaviorQuality:QualityCount};
export type ToolScore={id:string;name:string;color:string;coverage:number|null;compilation:number|null;behavior:number|null;emitted:number|null;time:number|null;memory:number|null;observedTime:number|null;observedMemory:number|null;files:number;verifiedFiles:number;source:number;units:number;failures:number;rows:ObservedRow[];chartRows:ObservedRow[];qualityRows:ObservedRow[];fullEligible:boolean;compilationCount:QualityCount;behaviorCount:QualityCount;performanceReason:string};
export const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
export const scopeNames:Record<string,string>={method:'Métodos AS3',action:'Acciones AS2',function:'Funciones anidadas',class:'Clases',script:'Scripts',legacy:'Recuento histórico'};
export const engineNames:Record<string,string>={native:'PySWF',python:'PySWF',rust:'RustSWF',js:'JsSWF',go:'GoSWF',asm:'AsmSWF (x86_64)',jpexs:'JPEXS',pyflashkit:'pyflashkit',rabcdasm:'RABCDAsm',flasm:'Flasm',flex_swfdump:'Flex SWFDump',swfdump:'SWFTools',flare:'Flare',flashdec_py:'flashdec-py',flashdec_rs:'flashdec-rs'};
export const colors:Record<string,string>={native:'#c98263',python:'#c98263',rust:'#f57918',js:'#e5a00d',go:'#00add8',asm:'#8c42d6',jpexs:'#2da653',pyflashkit:'#7867cf',rabcdasm:'#2546cf',flasm:'#008ded',flex_swfdump:'#20aaa8',flare:'#b2609a',flashdec_py:'#2f8f6f',flashdec_rs:'#d8622a'};
export const toolName=(id:string,fallback?:string)=>engineNames[id]??fallback??id;
export const toolColor=(id:string)=>colors[id]??'#53616c';
export const languageOf=(value:string|null|undefined)=>/^(avm1|as1|as2)$/i.test(value??'')?'as2':/^(avm2|as3)$/i.test(value??'')?'as3':value;
export const format=(value:unknown,digits=1)=>finite(value)?value.toLocaleString('es-ES',{maximumFractionDigits:digits}):'Sin verificar';
const blocked=new Set(['failed','error','timeout','output_unmapped','comparison_unavailable','not_configured','not_applicable','bytecode_mismatch','identity_unverified','partial_identity_mapping','execution_only']);
export const badMapping=(row:CatalogRow)=>blocked.has(row.status)||blocked.has(row.observation_status??'')||row.inventory_status==='disagreement';
const median=(numbers:number[])=>{const sorted=[...numbers].sort((a,b)=>a-b);const n=sorted.length;return n?(n%2?sorted[(n-1)/2]:(sorted[n/2-1]+sorted[n/2])/2):null;};
export const phaseName=(s:string)=>({hot:'Operación caliente',cold:'Proceso frío',warmup:'Calentamiento',observation:'Observación',summary:'Resumen',heap_probe:'Memoria'}[s]??s);
export const defaults:Filters={dataset:'canonical',operation:'source',scope:'method',origin:'all',family:'all',split:'all',language:'all',environment:'auto',phase:'hot',mode:'auto',compiler:'auto',query:'',common:false};
export const environmentKey=(row:CatalogRow)=>row.environment_id??row.environment;
const record=(value:unknown):Record<string,unknown>=>value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
const array=(value:unknown):Record<string,unknown>[]=>Array.isArray(value)?value.map(record):[];
const text=(value:unknown)=>typeof value==='string'?value:'';
const count=(value:unknown):number|null=>finite(value)&&Number.isSafeInteger(value)&&value>=0?value:null;
const sha=(value:unknown)=>/^[a-f0-9]{64}$/i.test(text(value));
const completeScopes=new Set(['complete_project','complete_document','complete_actions','complete_generated_project','complete_generated_actions']);
const successStates=new Set(['passed','compiled','passed_bounded_trace','bounded_trace_passed']);
const failureStates=new Set(['failed','error','compilation_failed','behavior_mismatch','timeout']);
const unsupportedStates=new Set(['not_applicable','not_supported','unsupported','runtime_reference_unsupported','original_runtime_unsupported','reference_unsupported']);
const emptyQuality=(reason:string,total:number|null=1):QualityCount=>({total,passed:0,evaluated:0,failed:0,unknown:total??0,unsupported:0,ineligible:0,unknownProjects:total===null?1:0,percent:total===null?null:0,complete:false,contextKey:null,reason});
/** A scheduled case can retain its denominator even when its execution evidence
 * was invalidated. Passing a check additionally requires verified context. */
export const hasQualityIdentity=(row:CatalogRow)=>{const q=record(row.quality_evaluation);return q.schema_version===1&&sha(row.artifact_sha256)&&q.input_sha256===row.artifact_sha256&&text(q.scope_id).startsWith(text(row.artifact_sha256)+':');};
function qualityCount(total:number,passed:number,evaluated:number,ineligible=0,contextKey:string|null=null,reason=''):QualityCount{
  const eligible=total-ineligible,unknown=eligible-evaluated;
  return {total,passed,evaluated,failed:evaluated-passed,unknown,unsupported:0,ineligible,unknownProjects:0,percent:total>0?100*passed/total:null,complete:total>0&&passed===total,contextKey,reason};
}
/** Only an explicit complete project/document build can validate emitted text.
 * Historical sample compilers, unit checks and status strings are evidence only. */
export function qualityOf(row:CatalogRow,compiler='auto'):{compilation:QualityCount;behavior:QualityCount}{
  const quality=record(row.quality_evaluation);
  if(!Object.keys(quality).length)return {compilation:emptyQuality('Compilación completa sin evaluar'),behavior:emptyQuality('No hay un plan de escenarios registrado',null)};
  const scheduled=count(quality.scenarios_scheduled);
  const noRuntime=(reason:string)=>emptyQuality(reason,scheduled);
  if(quality.role==='disassembly_reference'){
    const compilation=emptyQuality('Desensamblador; no produce fuente');compilation.unsupported=1;
    const behavior=emptyQuality('Desensamblador; no produce fuente',1);behavior.unsupported=1;
    return {compilation,behavior};
  }
  const context=record(quality.context),cache=record(quality.cache);
  const identity=text(quality.input_sha256)||text(context.artifact_sha256)||text(context.input_sha256);
  const stale=quality.evidence_status!=='verified'||quality.context_valid!==true||cache.valid===false||cache.context_valid===false||quality.stale===true
    ||Boolean(identity&&identity!==row.artifact_sha256)
    ||Boolean(context.build_id&&context.build_id!==row.build_id)
    ||Boolean(context.environment_id&&context.environment_id!==environmentKey(row));
  if(stale)return {compilation:emptyQuality('La evidencia pertenece a otro contexto o caché'),behavior:noRuntime('La evidencia pertenece a otro contexto o caché')};
  const scope=record(quality.scope),source=record(quality.source),compilation=record(quality.compilation),behavior=record(quality.behavior);
  const scopeName=text(scope.kind)||text(compilation.scope)||text(source.project_scope)||text(quality.scope);
  const allRoutes=array(quality.compilations);
  const compilerNames=[...new Set(allRoutes.map(route=>text(route.compiler)).filter(Boolean))].sort();
  if(compiler==='all'&&compilerNames.length>1)return {compilation:emptyQuality('Selecciona un compilador; los intentos son independientes'),behavior:noRuntime('Selecciona un compilador; los intentos son independientes')};
  const selectedCompiler=compiler==='auto'?compilerNames[0]:compiler;
  const routes=allRoutes.filter(route=>!selectedCompiler||route.compiler===selectedCompiler);
  const completeRoutes=routes.filter(route=>completeScopes.has(text(route.scope)));
  const complete=completeScopes.has(scopeName)||completeRoutes.length>0;
  // A generation succeeded flag cannot stand in for identity or completeness.
  if(!hasQualityIdentity(row)||!complete||scope.complete===false){
    const compilation=emptyQuality('Alcance completo o correspondencia sin verificar'),behavior=noRuntime('Alcance completo o correspondencia sin verificar');
    if(unsupportedStates.has(text(quality.status))||unsupportedStates.has(text(source.status))){compilation.unsupported=1;behavior.unsupported=scheduled??0;}
    return {compilation,behavior};
  }
  const contextKey=text(quality.comparison_key)||text(context.comparison_key)||text(context.scenario_suite_id)||null;
  let compile=emptyQuality('Compilación completa sin evaluar');
  if(completeRoutes.length){
    const passed=completeRoutes.some(route=>successStates.has(text(route.status)));
    const failed=completeRoutes.some(route=>failureStates.has(text(route.status)));
    compile=qualityCount(1,passed?1:0,passed||failed?1:0,0,contextKey,passed?'Proyecto completo recompilado':failed?'Falló la compilación completa':'Compilador sin evaluar o no disponible');
    if(!passed&&!failed&&completeRoutes.some(route=>unsupportedStates.has(text(route.status))))compile.unsupported=1;
  }else{
    const status=text(compilation.status),total=count(compilation.total_projects)??count(compilation.total)??1;
    const passed=count(compilation.passed_projects)??count(compilation.passed),evaluated=count(compilation.evaluated_projects)??count(compilation.evaluated);
    if(passed!==null&&evaluated!==null&&passed<=evaluated&&evaluated<=total)compile=qualityCount(total,passed,evaluated,0,contextKey,'Proyectos completos');
    else if(successStates.has(status)||failureStates.has(status))compile=qualityCount(1,successStates.has(status)?1:0,1,0,contextKey,'Proyecto completo');
  }
  let runtime=noRuntime('Escenario programado sin evaluar');
  const scenarios=array(behavior.scenarios);
  if(scenarios.length){
    const ids=scenarios.map(s=>text(s.scenario_id)||text(s.id));
    if(ids.every(Boolean)&&new Set(ids).size===ids.length){
      let passed=0,evaluated=0,ineligible=0;
      for(const scenario of scenarios){
        const reference=text(scenario.reference_status)||text(record(scenario.reference).status)||text(behavior.reference_status);
        const status=text(scenario.status);
        if(unsupportedStates.has(reference)||unsupportedStates.has(status)){ineligible++;continue;}
        if(!successStates.has(reference))continue;
        if(failureStates.has(status)){evaluated++;continue;}
        if(successStates.has(status)&&compile.complete){passed++;evaluated++;}
      }
      runtime=qualityCount(scenarios.length,passed,evaluated,ineligible,contextKey?contextKey+'|'+[...ids].sort().join('|'):null,'Escenarios acotados frente al original');
    }
  }else if(completeRoutes.length){
    // A standalone semantic job executes one explicitly bounded trace scenario.
    // Both original and reconstructed records must independently pass it.
    const runs=completeRoutes.map(route=>({route,runtime:record(route.runtime)}));
    const proved=runs.find(({route,runtime:r})=>successStates.has(text(route.status))&&successStates.has(text(r.status))&&['original','reconstructed'].every(label=>{const records=array(r.records).filter(item=>item.label===label);return records.length===1&&records[0].exact_expected_trace_passed===true;}));
    const failed=runs.some(({route,runtime:r})=>successStates.has(text(route.status))&&text(r.status)==='behavior_mismatch'&&array(r.records).some(item=>item.label==='original'&&item.exact_expected_trace_passed===true));
    const unsupported=runs.length>0&&runs.every(({runtime:r})=>unsupportedStates.has(text(r.status)));
    const hasPlan=runs.some(({runtime:r})=>Object.keys(r).length>0);
    if(hasPlan)runtime=qualityCount(1,proved?1:0,proved||failed?1:0,unsupported?1:0,contextKey,unsupported?'Original no ejecutable en la referencia':'Una traza acotada; no prueba equivalencia general');
  }
  return {compilation:compile,behavior:runtime};
}
function sumQuality(rows:ObservedRow[],kind:'compilationQuality'|'behaviorQuality'):QualityCount{
  const values=rows.map(row=>row[kind]);
  const total=values.reduce((sum,q)=>sum+(q.total??0),0),passed=values.reduce((sum,q)=>sum+q.passed,0),evaluated=values.reduce((sum,q)=>sum+q.evaluated,0),ineligible=values.reduce((sum,q)=>sum+q.ineligible,0);
  const result=qualityCount(total,passed,evaluated,ineligible);
  result.unsupported=values.reduce((sum,q)=>sum+q.unsupported,0);
  result.unknownProjects=values.reduce((sum,q)=>sum+q.unknownProjects,0);
  if(!total)result.total=null;
  return result;
}
export const qualityLabel=(quality:QualityCount,unit='proyectos')=>`${quality.passed}/${quality.total??'?'} ${unit} programados · ${quality.evaluated} evaluados · ${quality.unknown} sin evaluar${quality.unsupported?` · ${quality.unsupported} sin soporte`:''}${quality.ineligible?` · ${quality.ineligible} originales no elegibles`:''}${quality.unknownProjects?` · ${quality.unknownProjects} proyectos sin plan`:''}`;
export function observe(row:CatalogRow,filters:Filters,timestamp=0):ObservedRow{
  const metric=filters.origin==='all'?row.scope_metrics?.[filters.scope]:row.origin_scope_metrics?.[filters.origin]?.[filters.scope];
  const denominator=metric?.canonical_denominator??(filters.scope==='legacy'?row.expected_units:null);
  const count=filters.operation==='disassemble'?metric?.disassembly_available??(filters.scope==='legacy'?row.disassembly_available:null):metric?.source_available??(filters.scope==='legacy'?row.source_available:null);
  const validCount=finite(count)&&Number.isSafeInteger(count)&&count>=0&&finite(denominator)&&Number.isSafeInteger(denominator)&&denominator>0&&count<=denominator;
  const verified=Boolean(metric&&Number.isSafeInteger(denominator)&&denominator!>0&&metric.identity_verified===denominator&&/^[a-f0-9]{64}$/i.test(row.artifact_sha256??'')&&/^[a-f0-9]{64}$/i.test(row.canonical_inventory_sha256??'')&&!badMapping(row));
  const value=validCount&&(verified||filters.scope==='legacy'&&!badMapping(row))?100*count!/denominator!:null;
  const quality=qualityOf(row,filters.compiler);
  const qualityIdentity=hasQualityIdentity(row);
  return {...row,value,denominator:denominator??null,sourceCount:finite(count)?count:null,verified,qualityIdentity,selected_scope:filters.scope,selected_compiler:filters.compiler,code_origin:filters.origin,compilationQuality:quality.compilation,behaviorQuality:quality.behavior,
    fileKey:sha(row.artifact_sha256)?[row.artifact_sha256,filters.origin].join('|'):qualityIdentity?[row.artifact_sha256,'complete_project',filters.origin].join('|'):[row.artifact_sha256??row.file,row.canonical_inventory_sha256??'historical',filters.scope,filters.origin].join('|'),timestamp};
}
export function selectRows(catalog:Catalog,filters:Filters):ObservedRow[]{
  const dates=new Map(catalog.campaigns.map(c=>[c.id,Date.parse(c.created_at??'')||0]));
  const qualityCampaigns=catalog.rows.filter(row=>hasQualityIdentity(row));
  const newestQuality=filters.dataset==='canonical'?qualityCampaigns.map(row=>row.campaign_id).sort((a,b)=>(dates.get(b)??0)-(dates.get(a)??0))[0]:undefined;
  const qualityByKey=new Map<string,CatalogRow>();
  const unitByKey=new Map<string,CatalogRow>();
  if(filters.dataset==='canonical'){
    for(const row of catalog.rows){
      if(!sha(row.artifact_sha256))continue;
      const key=row.engine+'|'+row.artifact_sha256;
      if(newestQuality&&row.campaign_id===newestQuality)qualityByKey.set(key,row);
      if(row.canonical_inventory_sha256&&row.scope_metrics&&row.operation===filters.operation&&(row.phase??'observation')===filters.phase){
        const prior=unitByKey.get(key);
        if(!prior||(dates.get(row.campaign_id)??0)>=(dates.get(prior.campaign_id)??0))unitByKey.set(key,row);
      }
    }
  }
  const sourceRows=filters.dataset==='canonical'?catalog.rows.map(row=>{
    if(!sha(row.artifact_sha256))return row;
    const key=row.engine+'|'+row.artifact_sha256;
    const unitRow=unitByKey.get(key),qRow=qualityByKey.get(key);
    if(newestQuality&&row.campaign_id===newestQuality&&unitRow&&!row.scope_metrics){
      return {...row,scope_metrics:unitRow.scope_metrics,origin_scope_metrics:unitRow.origin_scope_metrics,canonical_inventory_sha256:unitRow.canonical_inventory_sha256,inventory_status:unitRow.inventory_status,disassembly_available:unitRow.disassembly_available,median_ms:row.median_ms??unitRow.median_ms,peak_tree_rss_bytes:row.peak_tree_rss_bytes??unitRow.peak_tree_rss_bytes};
    }
    if(row.canonical_inventory_sha256&&qRow&&!hasQualityIdentity(row)){
      const qe=record(qRow.quality_evaluation),ctx=record(qe.context);
      return {...row,family_id:row.family_id??qRow.family_id,split:row.split??qRow.split,quality_evaluation:{...qe,context:{...ctx,build_id:row.build_id,environment_id:environmentKey(row)}}};
    }
    return row;
  }):catalog.rows;
  let rows=sourceRows.filter(row=>(filters.dataset==='canonical'?Boolean(newestQuality&&row.campaign_id===newestQuality||row.canonical_inventory_sha256):row.campaign_id===filters.dataset)
    &&(row.operation===filters.operation||filters.operation==='source'&&record(row.quality_evaluation).role==='disassembly_reference')&&((row.phase??'observation')===filters.phase||hasQualityIdentity(row)&&filters.phase===defaults.phase)
    &&(filters.family==='all'||row.family_id===filters.family)&&(filters.split==='all'||row.split===filters.split)
    &&(filters.language==='all'||languageOf(row.language)===filters.language)&&(filters.environment==='all'||filters.environment==='auto'||environmentKey(row)===filters.environment)
    &&(hasQualityIdentity(row)||filters.scope==='legacy'||!row.scope_metrics||Boolean(row.scope_metrics[filters.scope]?.canonical_denominator))
    &&(filters.mode==='all'||filters.mode==='auto'||row.adapter_mode===filters.mode)
    &&(!filters.query||[row.file,row.family_id,toolName(row.engine,row.engine_name)].join(' ').toLowerCase().includes(filters.query.toLowerCase())));
  if(filters.environment==='auto'){
    const groups=new Map<string,number>();for(const row of rows){const key=environmentKey(row);groups.set(key,(groups.get(key)??0)+1);}
    const selected=[...groups].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0]?.[0];rows=rows.filter(row=>environmentKey(row)===selected);
  }
  if(filters.mode==='auto'){
    // Select the latest recorded mode independently for each tool. A tie uses
    // the explicit all-ABC adapter, then a stable name; never a coverage score.
    const modes=new Map<string,{name:string;timestamp:number}>();
    const rank=(name:string)=>name==='api_all_abc'?'0'+name:'1'+name;
    for(const row of rows){const key=row.engine+'|'+environmentKey(row),name=row.adapter_mode??'unknown',timestamp=dates.get(row.campaign_id)??0,old=modes.get(key);if(!old||timestamp>old.timestamp||timestamp===old.timestamp&&rank(name)<rank(old.name))modes.set(key,{name,timestamp});}
    rows=rows.filter(row=>(row.adapter_mode??'unknown')===modes.get(row.engine+'|'+environmentKey(row))?.name||filters.dataset==='canonical'&&hasQualityIdentity(row));
  }
  // Choose a build by recorded time, never by score. Keep all projects of that
  // build and one observation per tool/input/operation/phase.
  const context=(row:CatalogRow)=>filters.dataset==='canonical'?[row.engine,environmentKey(row)].join('|'):[row.engine,environmentKey(row),row.adapter_mode??'unknown'].join('|');
  const builds=new Map<string,{id:string;timestamp:number}>();
  for(const row of rows){const timestamp=dates.get(row.campaign_id)??0;const old=builds.get(context(row));if(!old||timestamp>old.timestamp)builds.set(context(row),{id:row.build_id??'',timestamp});}
  // Pick a recorded compiler by route count, never by successful outcomes.
  const compilerCounts=new Map<string,number>();
  for(const row of rows)for(const route of array(record(row.quality_evaluation).compilations)){const name=text(route.compiler);if(name)compilerCounts.set(name,(compilerCounts.get(name)??0)+1);}
  const compiler=filters.compiler==='auto'?[...compilerCounts].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]))[0]?.[0]??'auto':filters.compiler;
  const unique=new Map<string,ObservedRow>();
  for(const row of rows){if(filters.dataset!=='canonical'&&(row.build_id??'')!==builds.get(context(row))?.id)continue;const current=observe(row,{...filters,compiler},dates.get(row.campaign_id)??0);const key=context(row)+'|'+current.fileKey;const prior=unique.get(key);if(!prior||current.timestamp>prior.timestamp||current.verified&&!prior.verified)unique.set(key,current);}
  return [...unique.values()];
}
export function scoreTools(rows:ObservedRow[],engines:string[],common:boolean):{tools:ToolScore[];sharedFiles:number;allFiles:number;contextIssues:string[]}{
  const selected=rows.filter(r=>engines.includes(r.engine));
  const contextIssues:string[]=[];
  if(new Set(selected.map(environmentKey)).size>1)contextIssues.push('La selección mezcla entornos. Selecciona uno para agregar resultados.');
  if(new Set(selected.map(row=>row.selected_compiler).filter(c=>c&&c!=='auto')).size>1)contextIssues.push('La selección mezcla compiladores. Compara un compilador de validación a la vez.');
  for(const id of engines){const own=selected.filter(r=>r.engine===id);if(new Set(own.map(row=>row.adapter_mode??'unknown')).size>1)contextIssues.push(toolName(id)+': hay varios modos de adaptador; selecciona uno.');if(new Set(own.map(row=>row.build_id??'unknown')).size>1)contextIssues.push(toolName(id)+': hay varios builds; selecciona una campaña.');}
  const homogeneous=contextIssues.length===0;
  const sets=engines.map(id=>new Set(selected.filter(r=>r.engine===id&&(r.verified||r.qualityIdentity)).map(r=>r.fileKey))).filter(s=>s.size>0);
  const shared=new Set(sets[0]??[]);for(const set of sets.slice(1))for(const key of shared)if(!set.has(key))shared.delete(key);
  // Speed bars describe equivalent validated work on a shared corpus. Raw
  // historical timings remain visible even when this comparative set is empty.
  const performanceKeys=new Set<string>();
  if(homogeneous&&common)for(const key of shared){
    const group=selected.filter(row=>row.fileKey===key);
    if(group.length!==engines.length||group.some(row=>row.operation!=='source'||!row.compilationQuality.complete||!row.behaviorQuality.complete||!row.behaviorQuality.contextKey||row.valid_performance_summary!==true||badMapping(row)||row.success_count<1||!finite(row.median_ms)||row.median_ms<0))continue;
    if(new Set(group.map(row=>row.behaviorQuality.contextKey)).size===1)performanceKeys.add(key);
  }
  const tools=engines.map(id=>{
    const own=selected.filter(r=>r.engine===id);const qualityRows=own.filter(r=>!common||!shared.size||shared.has(r.fileKey));
    const usable=qualityRows.filter(r=>finite(r.value));
    const times=qualityRows.filter(r=>performanceKeys.has(r.fileKey));
    const observedRows=own.filter(r=>r.success_count>0&&!badMapping(r)&&finite(r.median_ms)&&r.median_ms>=0);
    const observedTime=median(observedRows.map(r=>r.median_ms!));
    const observedMemory=median(observedRows.map(r=>r.peak_tree_rss_bytes).filter(finite).map(v=>v/1048576));
    const projects=new Map<string,number[]>();for(const row of usable){const key=row.family_id??row.fileKey;projects.set(key,[...(projects.get(key)??[]),row.value!]);}
    const averages=[...projects.values()].map(values=>values.reduce((sum,value)=>sum+value,0)/values.length);
    const compilationCount=sumQuality(qualityRows,'compilationQuality'),behaviorCount=sumQuality(qualityRows,'behaviorQuality');
    return {id,name:toolName(id,own[0]?.engine_name),color:toolColor(id),coverage:compilationCount.percent,compilation:compilationCount.percent,behavior:behaviorCount.percent,emitted:averages.length?averages.reduce((a,v)=>a+v,0)/averages.length:null,
      time:median(times.map(r=>r.median_ms!)),memory:median(times.map(r=>r.peak_tree_rss_bytes).filter(finite).map(v=>v/1048576)),
      observedTime,observedMemory,
      files:own.length,verifiedFiles:usable.length,source:usable.reduce((a,r)=>a+(r.sourceCount??0),0),units:usable.reduce((a,r)=>a+(r.denominator??0),0),
      failures:own.reduce((a,r)=>a+(r.error_count??0)+(r.timeout_count??0),0),rows:own,chartRows:times.length?times:observedRows,qualityRows,compilationCount,behaviorCount,
      fullEligible:times.length>0,performanceReason:times.length?'Mismos proyectos, compilación completa y escenarios acotados coincidentes':observedRows.length?'Mediana observada sobre ejecuciones exitosas verificadas (sin equivalencia semántica completa común)':!common?'Selecciona la misma base de archivos para comparar rendimiento':'Sin proyectos comunes con calidad, contexto y perfil full equivalentes'} satisfies ToolScore;
  });
  return {tools,sharedFiles:shared.size,allFiles:new Set(selected.map(r=>r.fileKey)).size,contextIssues};
}
export function csv(rows:ObservedRow[]):string{
  const columns=['engine','family_id','file','artifact_sha256','canonical_inventory_sha256','campaign_id','environment','environment_id','build_id','operation','phase','adapter_mode','selected_scope','code_origin','value','sourceCount','denominator','verified','status','median_ms','peak_tree_rss_bytes','compilation','behavior','compilationQuality','behaviorQuality','quality_evaluation'];
  const cell=(value:unknown)=>{let text=typeof value==='object'?JSON.stringify(value):String(value??'');if(typeof value!=='number'&&/^[\s]*[=+@\-\t\r]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};
  return '\uFEFF'+columns.map(cell).join(',')+'\r\n'+rows.map(row=>columns.map(key=>cell(row[key as keyof ObservedRow])).join(',')).join('\r\n');
}
