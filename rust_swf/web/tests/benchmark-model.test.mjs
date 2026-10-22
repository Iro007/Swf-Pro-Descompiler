import {test} from 'node:test';
import assert from 'node:assert/strict';
import {defaults,observe,scoreTools,selectRows,csv,qualityOf} from '../src/components/benchmarkModel.ts';
const A='a'.repeat(64),B='b'.repeat(64),C='c'.repeat(64);
const hash=id=>id==='x'?A:id==='y'?B:C;
function input(engine,id='x',source=5,total=10,more={}){
  return {engine,engine_name:engine,file:id,family_id:id,campaign_id:'c',artifact_sha256:hash(id),canonical_inventory_sha256:B,build_id:'build',environment:'Windows',operation:'source',phase:'hot',status:'measured',run_count:1,success_count:1,error_count:0,timeout_count:0,median_ms:10,peak_tree_rss_bytes:1048576,compilation:'not_run',behavior:'not_run',version:'test',scope_metrics:{method:{canonical_denominator:total,identity_verified:total,source_available:source}},...more};
}
const row=(engine,id='x',source=5,total=10,more={},filters=defaults)=>observe(input(engine,id,source,total,more),filters);
function quality(id='x',more={}){
  return {schema_version:1,evidence_status:'verified',context_valid:true,input_sha256:hash(id),scope_id:hash(id)+':generated-project',scope:{kind:'complete_project',complete:true},role:'source_decompiler',projects_scheduled:1,scenarios_scheduled:1,source:{status:'complete',project_scope:'complete_generated_project'},comparison_key:'scenario-context-'+id,compilations:[{compiler:'mxmlc',status:'compiled',scope:'complete_generated_project',complete_project:true,runtime:{status:'passed_bounded_trace',records:[{label:'original',exact_expected_trace_passed:true},{label:'reconstructed',exact_expected_trace_passed:true}]}}],...more};
}
const validated=(engine,id='x',more={},filters=defaults)=>row(engine,id,5,10,{quality_evaluation:quality(id),valid_performance_summary:true,...more},filters);
const catalog=(rows,campaigns=[{id:'c',created_at:'2026-10-14'}])=>({rows,campaigns});
test('historical source emission never proves compilation, behavior or comparable speed',()=>{
  const historical=row('pyflashkit','x',8),score=scoreTools([historical],['pyflashkit'],true).tools[0];
  assert.equal(historical.value,80);assert.equal(score.emitted,80);assert.equal(score.compilationCount.passed,0);assert.equal(score.compilationCount.evaluated,0);assert.equal(score.compilationCount.unknown,1);assert.equal(score.behaviorCount.total,null);assert.equal(score.time,null);
});
test('emission is a macro average per project, independent of compilation quality',()=>{
  const score=scoreTools([row('native','x',1000,1000,{family_id:'same'}),row('native','y',1,1,{family_id:'same'}),row('native','z',0,1,{family_id:'other'})],['native'],false).tools[0];
  assert.equal(score.emitted,50);assert.equal(score.compilation,0);
});
test('complete compilation and bounded behavior remain independent',()=>{
  const q=quality();q.compilations[0].runtime={status:'not_configured'};
  const result=qualityOf(input('native','x',5,10,{quality_evaluation:q}));
  assert.equal(result.compilation.passed,1);assert.equal(result.behavior.passed,0);assert.equal(result.behavior.total,1);assert.equal(result.behavior.evaluated,0);assert.equal(result.behavior.unknown,1);
});
test('one passing project among six scheduled keeps all five unknown projects and scenarios',()=>{
  const rows=['x','y','z','a','b','c'].map((id,index)=>validated('native',id,{file:id,quality_evaluation:index===0?quality(id):quality(id,{scope:{kind:'complete_project',complete:false},source:{status:'partial'},compilations:[]})}));
  const score=scoreTools(rows,['native'],false).tools[0];
  for(const q of [score.compilationCount,score.behaviorCount]){assert.equal(q.total,6);assert.equal(q.passed,1);assert.equal(q.evaluated,1);assert.equal(q.unknown,5);assert.equal(q.percent,100/6);}
});
test('partial project and isolated method compilers cannot prove full-project compilation',()=>{
  for(const q of [quality('x',{scope:{kind:'complete_project',complete:false}}),quality('x',{scope:{kind:'method',complete:true},source:{project_scope:'method'},compilations:[{compiler:'mxmlc',status:'compiled',scope:'method'}]})]){
    const result=qualityOf(input('native','x',5,10,{quality_evaluation:q}));assert.equal(result.compilation.passed,0);assert.equal(result.compilation.evaluated,0);assert.equal(result.behavior.passed,0);
  }
});
test('observed failures are evaluated; missing compilers remain unknown',()=>{
  const q=quality();q.compilations[0].status='compilation_failed';
  const failed=qualityOf(input('native','x',5,10,{quality_evaluation:q})).compilation;
  assert.equal(failed.evaluated,1);assert.equal(failed.failed,1);assert.equal(failed.unknown,0);
  q.compilations[0].status='not_configured';
  const missing=qualityOf(input('native','x',5,10,{quality_evaluation:q})).compilation;
  assert.equal(missing.evaluated,0);assert.equal(missing.failed,0);assert.equal(missing.unknown,1);
});
test('unsupported source tools show 0/n and unsupported counts without observed failures',()=>{
  const q=quality('x',{status:'not_applicable',scope:{kind:'complete_project',complete:false},compilations:[]});
  const result=qualityOf(input('flare','x',null,10,{quality_evaluation:q}));
  assert.equal(result.compilation.total,1);assert.equal(result.compilation.passed,0);assert.equal(result.compilation.unsupported,1);assert.equal(result.compilation.failed,0);
});
test('unsupported original runtime stays scheduled but noneligible and never passes',()=>{
  const q=quality();q.compilations[0].runtime={status:'runtime_reference_unsupported',records:[]};
  const result=qualityOf(input('native','x',5,10,{quality_evaluation:q}));
  assert.equal(result.compilation.passed,1);assert.equal(result.behavior.total,1);assert.equal(result.behavior.passed,0);assert.equal(result.behavior.evaluated,0);assert.equal(result.behavior.ineligible,1);assert.equal(result.behavior.complete,false);
});
test('missing, duplicate or failing original and candidate trace records cannot pass',()=>{
  for(const records of [[{label:'reconstructed',exact_expected_trace_passed:true}],[{label:'original',exact_expected_trace_passed:false},{label:'reconstructed',exact_expected_trace_passed:true}],[{label:'original',exact_expected_trace_passed:true},{label:'original',exact_expected_trace_passed:true},{label:'reconstructed',exact_expected_trace_passed:true}]]){
    const q=quality();q.compilations[0].runtime.records=records;
    assert.equal(qualityOf(input('native','x',5,10,{quality_evaluation:q})).behavior.passed,0);
  }
});
test('distinct compilers cannot combine separate compilation and runtime successes',()=>{
  const q=quality();q.compilations[0].runtime.status='behavior_mismatch';q.compilations.push({compiler:'flex',status:'compilation_failed',scope:'complete_generated_project',runtime:quality().compilations[0].runtime});
  const native=qualityOf(input('native','x',5,10,{quality_evaluation:q}),'mxmlc'),flex=qualityOf(input('native','x',5,10,{quality_evaluation:q}),'flex');
  assert.equal(native.compilation.passed,1);assert.equal(native.behavior.passed,0);assert.equal(flex.compilation.passed,0);assert.equal(flex.behavior.passed,0);assert.equal(qualityOf(input('native','x',5,10,{quality_evaluation:q}),'all').compilation.passed,0);
});
test('stale cache and changed input, environment or build context invalidate quality',()=>{
  for(const extra of [{context_valid:false},{cache:{valid:false}},{input_sha256:B},{context:{build_id:'other'}},{context:{environment_id:'Linux'}},{evidence_status:'reported'}]){
    const observed=row('native','x',7,10,{quality_evaluation:quality('x',extra)});assert.equal(observed.value,70);assert.equal(observed.compilationQuality.passed,0);assert.equal(observed.behaviorQuality.passed,0);
  }
});
test('whole-project quality uses exact input SHA without inventing canonical method counts',()=>{
  const first=validated('native','x',{scope_metrics:undefined,canonical_inventory_sha256:undefined}),second=validated('jpexs','x',{scope_metrics:undefined,canonical_inventory_sha256:undefined});
  assert.equal(first.value,null);assert.equal(first.verified,false);assert.equal(first.qualityIdentity,true);
  const scores=scoreTools([first,second],['native','jpexs'],true);assert.equal(scores.sharedFiles,1);assert.equal(scores.tools[0].compilationCount.passed,1);
});
test('performance uses common projects with full quality, retaining exclusive raw evidence',()=>{
  const scores=scoreTools([validated('native'),validated('native','y',{median_ms:1000,peak_tree_rss_bytes:500*1048576}),validated('rust','x',{median_ms:20})],['native','rust'],true);
  assert.equal(scores.sharedFiles,1);assert.equal(scores.tools[0].time,10);assert.equal(scores.tools[0].memory,1);assert.equal(scores.tools[0].rows.length,2);assert.equal(scores.tools[0].chartRows.length,1);assert.equal(scores.tools[1].time,20);
});
test('speed comparison is withheld without matching scenarios, full profile and complete quality',()=>{
  for(const more of [{valid_performance_summary:false},{quality_evaluation:quality('x',{comparison_key:'other'})},{quality_evaluation:quality('x',{compilations:[]})}]){
    const scores=scoreTools([validated('native'),validated('rust','x',more)],['native','rust'],true);assert.equal(scores.tools[0].time,null);assert.equal(scores.tools[0].chartRows.length,0);assert.equal(scores.tools[0].rows[0].median_ms,10);
  }
  assert.equal(scoreTools([validated('native')],['native'],false).tools[0].time,null);
});
test('different SWF hashes with the same name cannot join a common quality set',()=>{
  const scores=scoreTools([validated('native'),validated('rust','x',{artifact_sha256:C})],['native','rust'],true);assert.equal(scores.sharedFiles,0);assert.equal(scores.tools[0].time,null);
});
test('malformed source counts stay unknown without altering actual compiler evidence',()=>{
  for(const value of [null,-1,11,1.5,Infinity])assert.equal(row('native','x',value).value,null);
  assert.equal(row('native','x',0).value,0);assert.equal(validated('native','x',{scope_metrics:{method:{canonical_denominator:10,identity_verified:10,source_available:99}}}).compilationQuality.passed,1);
});
test('mixed environments, builds and adapter modes retain raw rows and withhold aggregates',()=>{
  for(const rows of [[validated('native'),validated('rust','x',{environment:'Linux'})],[validated('native','x',{adapter_mode:'api'}),validated('native','y',{adapter_mode:'cli'})],[validated('native','x',{environment_id:A}),validated('rust','x',{environment_id:B})]]){
    const scores=scoreTools(rows,[...new Set(rows.map(row=>row.engine))],true);assert.ok(scores.contextIssues.length);assert.equal(scores.tools[0].compilation,null);assert.equal(scores.tools[0].time,null);assert.ok(scores.tools[0].rows.length);
  }
});
test('canonical default prefers newest quality campaign and retains disassembly roles as 0/n',()=>{
  const source=validated('native','x',{campaign_id:'new',canonical_inventory_sha256:undefined,scope_metrics:{}}),reference=validated('rabcdasm','x',{campaign_id:'new',operation:'disassemble',canonical_inventory_sha256:undefined,quality_evaluation:quality('x',{role:'disassembly_reference',compilations:[]})});
  const selected=selectRows(catalog([row('pyflashkit','x',10,10,{campaign_id:'old'}),source,reference],[{id:'old',created_at:'2026-10-13'},{id:'new',created_at:'2026-10-14'}]),defaults);
  assert.equal(selected.length,2);assert.ok(selected.every(row=>row.campaign_id==='new'));assert.equal(selected.find(row=>row.engine==='rabcdasm').compilationQuality.unsupported,1);
});
test('latest invalidated campaign retains scheduled denominators instead of reverting to old passes',()=>{
  const old=validated('native','x',{campaign_id:'old'}),latest=validated('native','x',{campaign_id:'new',scope_metrics:undefined,canonical_inventory_sha256:undefined,quality_evaluation:quality('x',{context_valid:false,evidence_status:'invalidated_context_changed'})});
  const selected=selectRows(catalog([old,latest],[{id:'old',created_at:'2026-10-13'},{id:'new',created_at:'2026-10-14'}]),defaults),score=scoreTools(selected,['native'],true).tools[0];
  assert.equal(selected[0].campaign_id,'new');assert.equal(score.compilationCount.total,1);assert.equal(score.compilationCount.passed,0);assert.equal(score.compilationCount.unknown,1);
});
test('automatic adapter chooses the latest date, never the best emitted score',()=>{
  const old=row('pyflashkit','x',10,10,{campaign_id:'old',adapter_mode:'cli_first_abc'}),now=row('pyflashkit','x',2,10,{campaign_id:'now',adapter_mode:'api_all_abc'});
  const selected=selectRows(catalog([old,now],[{id:'old',created_at:'2026-10-13'},{id:'now',created_at:'2026-10-14'}]),defaults);assert.equal(selected.length,1);assert.equal(selected[0].value,20);
});
test('CSV preserves quality context and neutralizes untrusted spreadsheet formulas',()=>{
  const output=csv([validated('native','x',{file:'=HYPERLINK("https://invalid")'})]);
  for(const field of ['selected_scope','environment','campaign_id','quality_evaluation','compilationQuality','behaviorQuality'])assert.ok(output.includes('"'+field+'"'));assert.ok(output.includes("\"'=HYPERLINK"));
});
