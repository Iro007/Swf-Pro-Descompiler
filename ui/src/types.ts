/** Wire types of the Python suite (JSONL protocol v1). The desktop shell talks to nothing else. */
export interface SuiteEngine {id:string;name:string;available:boolean;version:string|null;category:string;operations:string[];path:string|null;detail:string}
export interface SuiteCatalog {engines:SuiteEngine[];default_engine:string}
export interface SuiteValidation {valid:boolean;errors:unknown[];warnings:unknown[];signature?:string;version?:number;tag_count?:number;frame_count?:number}
export interface SuiteState {
  filename:string;working_path:string;source_path?:string|null;revision:string;source_revision?:string;source_pending?:boolean;
  dirty:boolean;script_count:number;history:{undo:boolean;redo:boolean};signature?:string;frame_count?:number;validation?:SuiteValidation;
}
export interface SuiteDecompile {
  engine:string;source:string|null;pcode:string;diagnostics:unknown[];editable:boolean;revision:string;unit_id?:string;
  source_document?:string|null;document_source?:string|null;saved_source?:string|null;fallback_diagnostics?:unknown[];
}
export type SuiteProfile='auto-as3'|'native-as3'|'as3-flex'|'auto-as2'|'native-as2'|'as2-jpexs'|'as2-mtasc'|'haxe'|'haxeflixel';
export interface SuiteCompileResult extends SuiteState {path:string;engine:string;validation?:SuiteValidation;log:string|string[];diagnostics?:unknown[]}
export interface SuiteExportResult {path:string;engine:string;diagnostics?:unknown[]}
export type SuitePickKind='swf'|'project'|'project_output'|'source'|'output'|'folder'|'tool'|'image'|'audio';
export interface SuiteTimeline {frame:number;frame_count:number;display_list:unknown[];svg:string|null;actionscript_executed:false}
export interface SuiteSourceDocument {id:string;filename?:string;language?:string;source:string;source_sha256:string;editable:boolean;engine?:string;working_swf_sha256?:string}
export interface SuiteVectorPoint {index:number;group?:number;subpath:number;vertex:number;x:number;y:number;cmd:string;cx?:number;cy?:number}
export interface SuiteGlyph {index:number;code:number|null;points:SuiteVectorPoint[]}
export interface SuiteResourceInfo {kind:'image'|'sound'|'text'|'shape'|'font'|'raw';parsed:Record<string,unknown>;points?:SuiteVectorPoint[];glyphs?:SuiteGlyph[];coordinate_units:'pixels'|'twips'|'font units';revision:string}
export type SuiteRun=<T>(label:string,operation:()=>Promise<T>)=>Promise<T|undefined>;

/** Tag tree (operation `tag_tree`). */
export type NodeKind='folder'|'header'|'tag'|'script'|'package'|'class'|'frame'|'source';
export interface ClassMember {unit_id:string;name:string;role:string}
export interface TreeNode {
  id:string;label:string;kind:NodeKind;detail?:string;folder?:string;
  tag_index?:number;tag_path?:number[];tag_type?:number;tag_name?:string;character_id?:number;bytes?:number;error?:string;
  unit_id?:string;language?:string;document_id?:string;members?:ClassMember[];frame?:number;
  source_id?:string;editable?:boolean;children?:TreeNode[];child_count?:number;
}
export interface SuiteTagTree {nodes:TreeNode[];node_count:number;truncated:boolean;limit:number;revision:string;filename:string}

/** Container summary (operation `header`). */
export interface SuiteHeader {
  filename:string;signature:string;compression:'none'|'zlib'|'lzma'|'unknown';version:number;file_length:number;file_size:number;
  frame_size:{xmin:number;xmax:number;ymin:number;ymax:number;width:number;height:number};
  frame_rate:number;frame_count:number;shown_frames:number;tag_count:number;abc_blocks:number;
  has_as3:boolean;has_metadata:boolean;file_attributes:Record<string,boolean>|null;metadata:string|null;
  sha256:string;revision:string;script_count?:number;
}

/** One page of bytes (operation `raw`). */
export interface SuiteRaw {
  scope:'tag'|'file';name:string;tag_index?:number;tag_path?:number[];type?:number;offset:number;limit:number;
  total:number;length:number;encoding:'hex'|'base64';hex?:string;base64?:string;revision:string;
}

/** Static preview of one character (operation `resource_preview`). */
export interface SuitePreview {
  kind:'image'|'shape'|'morph'|'sprite'|'text'|'font';mime:string;encoding:'base64'|'text';data:string;
  width?:number;height?:number;frame?:number;frame_count?:number;ratio?:number;glyphs?:number;tag_index:number;name:string;revision:string;
}

/**
 * Benchmarks (operations `bench_results` and `benchmark_tools`). Runs written before schema 2 lack the optional fields
 * (times, `comparable`, `flash_index`, rows) and those before schema 3 lack the robustness and per-unit fidelity tables: the
 * interface says "sin datos en esta corrida" instead of inventing them.
 */
export type Outcome='recovered'|'partial'|'clean_error'|'crash'|'hang';
export type UnitOutcome='same'|'different'|'no_compile'|'not_run';
/** Outcomes of an engine on the damaged files of one kind, or on all of them (the robustness table). */
export interface OutcomeKind {cases:number;outcomes:Record<Outcome,number>;score:number|null}
/** The units of one kind (class, method, action) an engine was judged on, or all of them (the per-unit fidelity table). */
export interface UnitCounts {same:number;different:number;no_compile:number;not_run:number;evaluated:number;judged:number;fidelity:number|null}
export interface BenchLine {
  engine:string;name:string;cases:number;source?:number;recompiles?:number;same_trace?:number;ok?:number;
  jpexs_closeness?:number|null;jpexs_coverage?:number|null;reference?:boolean;reasons:string[];details?:string[];
  median_seconds?:number|null;timed_cases?:number;comparable?:boolean;flash_index?:number|null;
  units_total?:number|null;units_with_source?:number|null;unit_coverage?:number|null;
  /* schema 3, decompilers: the two terms that join the index when the run measured them for every ranked engine */
  unit_fidelity?:number|null;unit_fidelity_judged?:number|null;unit_fidelity_cases?:number;robustness?:number|null;robustness_cases?:number;
  /* schema 3, robustness table */
  outcomes?:Record<Outcome,number>;by_kind?:Record<string,Partial<OutcomeKind>&Partial<UnitCounts>>;recoverable_cases?:number;
  intact_units?:number;recovered_units?:number;unit_recovery?:number|null;peak_memory_mib?:number|null;
  /* schema 3, per-unit fidelity table */
  same?:number;different?:number;no_compile?:number;not_run?:number;evaluated?:number;judged?:number;fidelity?:number|null;
}
export interface BenchTable {
  unit:string;winner:string|null;note:string|null;corpus_cases?:number;engines:BenchLine[];
  /** the terms of the Índice Flash this run includes (decompilers table) */
  index_components?:string[];
  /** labels of the damage kinds (robustness) or of the unit kinds (per-unit fidelity) */
  kinds?:Record<string,string>;
  /** units the single-unit path cannot take for any engine, with the reasons (per-unit fidelity) */
  out_of_scope?:{units:number;reasons:Record<string,number>};unit_cap?:number;
}
export interface BenchRow {
  engine:string;family:string;case:string;reason:string;measured?:boolean;ok?:boolean|null;source?:boolean;
  recompiles?:boolean|null;same_trace?:boolean|null;seconds?:number|null;language?:string;units_total?:number|null;units_with_source?:number|null;
  /* robustness rows */
  kind?:string;outcome?:Outcome|null;peak_memory_mib?:number|null;intact_units?:number|null;recovered_units?:number|null;reported?:boolean|null;
  /* per-unit fidelity rows */
  units?:Record<UnitOutcome,number>;details?:{unit:string;kind:string;outcome:UnitOutcome;reason:string}[];
}
export interface BenchResults {
  schema_version?:number;run_id:string;mode:string;created_utc:string;seconds:number;corpus:string;cases:number;
  options?:{unit_cap?:number;robustness_timeout?:number;workers?:number};
  oracle?:{runtime:string;runtime_sha256:string|null};tables:Record<string,BenchTable>;rows?:Record<string,BenchRow[]>;
}
export interface BenchRun {run_id:string;mode:string;cases:number;seconds?:number;created_utc?:string;categories?:string[];winner?:string|null}
export interface BenchData {runs:BenchRun[];results:BenchResults|null;folder:string}
export interface BenchmarkEngine {id:string;name:string;category:string;configured:boolean;operations:string[];setup_diagnostic?:string}
