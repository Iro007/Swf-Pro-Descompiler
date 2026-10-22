export interface Diagnostic { code:string; message:string; offset:number|null }
export interface DocumentInfo { filename:string; sha256:string; signature:string; version:number; width:number; height:number; frame_rate:number; frame_count:number; tag_count:number }
export interface OpenResult {session_id:string;source_path?:string;info:DocumentInfo;diagnostics:Diagnostic[];script_count:number}
export interface Resource {id:string;path:number[];code:number;name:string;length:number;start:number;data_start:number;character_id:number|null;symbol:string|null;category:string;diagnostics:Diagnostic[];formats:string[]}
export interface Script {id:string;name:string;language:string;kind:string;tag_path:number[];abc_version:string|null;byte_len:number;sha256:string;diagnostics:Diagnostic[]}
export interface Page<T>{items:T[];total:number;offset:number}
export interface Reconstruction{unit_id:string;state:string;source:string|null;pcode:string;pcode_total?:number;diagnostics:Diagnostic[]}
export interface ClassOutline{name:string;superclass:string;interfaces:string[];fields:string[];methods:string[];source_tag:number[]}
export interface Asset {path:string;mime:string;bytes:number;extension:string;diagnostics:Diagnostic[]}
export interface SuiteEngine {id:string;name:string;available:boolean;version:string|null;category:string;operations:string[];path:string|null;detail:string}
export interface SuiteCatalog {engines:SuiteEngine[];default_engine:string}
export interface SuiteState {filename:string;working_path:string;revision:string;source_revision?:string;source_pending?:boolean;dirty:boolean;script_count:number;history:{undo:boolean;redo:boolean}}
export interface SuiteUnit {id:string;name:string;language:string;tag_path:number[];body_index?:number|null}
export interface SuiteDecompile {engine:string;source:string|null;pcode:string;diagnostics:unknown[];editable:boolean;revision:string;source_document?:string;document_source?:string|null}
export type SuiteProfile='auto-as3'|'native-as3'|'as3-flex'|'auto-as2'|'native-as2'|'as2-jpexs'|'as2-mtasc'|'haxe'|'haxeflixel';
export interface SuiteCompileResult extends SuiteState {path:string;engine:string;validation?:{valid:boolean;errors?:unknown[];warnings?:unknown[]};log:string|string[];diagnostics?:unknown[]}
export interface SuiteExportResult {path:string;engine:string;diagnostics?:unknown[]}
export type SuitePickKind='swf'|'project'|'project_output'|'source'|'output'|'folder'|'tool'|'image'|'audio';
export interface SuiteTimeline {frame:number;frame_count:number;display_list:unknown[];svg:string|null;actionscript_executed:false}
export interface SuiteResource {id:string;tag_index:number;index:number;name:string;type:string|number;formats:string[];code?:number}
export type SuiteRun=<T>(label:string,operation:()=>Promise<T>)=>Promise<T|undefined>;
export interface SuiteBenchmarkResult {path:string;html_path:string;completed_files:number;scheduled_files:number;claims:{parity:boolean;superiority:boolean;performance_winner:string|null}}
export interface SuiteSourceFile {id:string;filename:string;language:string;bytes:number;editable?:boolean}
export interface SuiteSourceDocument {id:string;filename?:string;language?:string;source:string;source_sha256:string;editable:boolean;engine?:string;working_swf_sha256?:string}
export interface SuiteVectorPoint {index:number;group?:number;subpath:number;vertex:number;x:number;y:number;cmd:string;cx?:number;cy?:number}
export interface SuiteGlyph {index:number;code:number|null;points:SuiteVectorPoint[]}
export interface SuiteResourceInfo {kind:'image'|'sound'|'text'|'shape'|'font'|'raw';parsed:Record<string,unknown>;points?:SuiteVectorPoint[];glyphs?:SuiteGlyph[];coordinate_units:'pixels'|'twips'|'font units';revision:string}
