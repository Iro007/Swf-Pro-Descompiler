import { FileInfo, FontInfo, ScriptListing, SoundInfo, TagInfo } from "./types";

const BASE = "/api/files";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name="ApiError";
    this.status=status;
  }
}

async function jsonOrThrow<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      if (body.detail)
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail,resp.status);
  }
  return resp.json() as Promise<T>;
}

export async function uploadSwf(file: File): Promise<FileInfo> {
  const form = new FormData();
  form.append("file", file, file.name);
  return jsonOrThrow(await fetch(BASE, { method: "POST", body: form }));
}

export async function uploadProject(file: File): Promise<FileInfo> {
  const form = new FormData();
  form.append("file", file, file.name);
  return jsonOrThrow(
    await fetch(`${BASE}/projects`, { method: "POST", body: form }),
  );
}

export async function createSwfFromSource(source: string, filename: string): Promise<FileInfo> {
  return jsonOrThrow(await fetch(`${BASE}/from-source`, {
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({source,filename,compression:"CWS"}),
  }));
}

export async function getTags(sid: string): Promise<TagInfo[]> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/tags`));
}

export async function getRawBytes(
  sid: string,
  index: number,
): Promise<Uint8Array> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/raw`);
  if (!resp.ok) throw new Error(resp.statusText);
  return new Uint8Array(await resp.arrayBuffer());
}

export async function putRawBytes(
  sid: string,
  index: number,
  bytes: Uint8Array,
): Promise<void> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/raw`, {
    method: "PUT",
    headers: { "Content-Type": "application/octet-stream" },
    body: bytes,
  });
  await jsonOrThrow(resp);
}

export function imageUrl(sid: string, index: number): string {
  return `${BASE}/${sid}/tags/${index}/export/image`;
}

export async function getSvg(
  sid: string,
  index: number,
  ratio = 0,
): Promise<string> {
  const resp = await fetch(
    `${BASE}/${sid}/tags/${index}/export/svg?ratio=${ratio}`,
  );
  if (!resp.ok) throw new Error((await resp.json()).detail ?? resp.statusText);
  return resp.text();
}

export async function getTimelineInfo(
  sid: string,
): Promise<{ frame_count: number; frame_rate: number }> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/timeline`));
}

export function frameSvgUrl(sid: string, frame: number): string {
  return `${BASE}/${sid}/frames/${frame}/svg`;
}

export async function getSpriteInfo(
  sid: string,
  index: number,
): Promise<{ char_id: number; declared_frames: number; frame_count: number }> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/tags/${index}/sprite-info`));
}

export function spriteFrameSvgUrl(
  sid: string,
  index: number,
  frame: number,
): string {
  return `${BASE}/${sid}/tags/${index}/sprite-frames/${frame}/svg`;
}

export async function getDisassembly(
  sid: string,
  index: number,
): Promise<ScriptListing> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/tags/${index}/disassemble`));
}

export interface DecompileSection {
  name: string;
  source: string;
  error: string | null;
  body_index?: number;
  editable_source?: boolean;
  replaceable_source?: boolean;
  source_edit_reason?: string | null;
  reconstruction?: "structured" | "pcode";
  source_document?: SourceDocument;
  saved_source?: string;
}

export interface SourceDocument {
  document_id: string;
  kind: "method" | "action" | "outline" | "script" | "class";
  base_swf_sha256: string;
  editable: boolean;
  diagnostics: string[];
  symbol_context: { body_index?: number | null; method_index?: number | null; script_index?: number; abc_major?: number | null; abc_minor?: number | null };
  source_bytecode_map: unknown[];
  map_status: "not_available";
}

export type Engine = "auto" | "native" | "jpexs" | "compare";
export interface EngineOutput {
  sections: DecompileSection[];
  ok?: boolean;
  error?: string | null;
}
export interface Decompilation {
  kind: string;
  engine: string;
  sections?: DecompileSection[];
  native?: EngineOutput;
  jpexs?: EngineOutput;
  error?: string | null;
  metrics?: {
    native_lines: number;
    jpexs_lines: number;
    matching_line_ratio: number;
  };
}

export async function getDecompilation(
  sid: string,
  index: number,
  engine: Engine = "native",
  signal?: AbortSignal,
): Promise<Decompilation> {
  return jsonOrThrow(
    await fetch(`${BASE}/${sid}/tags/${index}/decompile?engine=${engine}`, {
      signal,
    }),
  );
}

export interface EngineStatus {
  name: string;
  available: boolean;
  bridge?: string;
  configured_executable?: string | null;
  configured_jar?: string | null;
}

export async function getEngineStatus(): Promise<{
  default: string;
  backends: EngineStatus[];
}> {
  return jsonOrThrow(await fetch("/api/files/engines/status"));
}

export async function assemble(
  sid: string,
  index: number,
  bodyIndex: number,
  code: string,
): Promise<void> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/assemble`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body_index: bodyIndex, code }),
  });
  await jsonOrThrow(resp);
}

export async function compileSource(
  sid: string,
  index: number,
  bodyIndex: number,
  source: string,
  language = "auto",
): Promise<void> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/compile-source`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body_index: bodyIndex, source, language }),
  });
  await jsonOrThrow(resp);
}

export async function compileDocument(sid: string, index: number, document: SourceDocument, source: string): Promise<void> {
  await jsonOrThrow(await fetch(`${BASE}/${sid}/tags/${index}/compile-document`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_id: document.document_id, source, base_swf_sha256: document.base_swf_sha256 }),
  }));
}

export async function updateTextString(
  sid: string,
  index: number,
  text: string,
): Promise<void> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/text/update-string`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  await jsonOrThrow(resp);
}

export interface SearchResult {
  index: number;
  code: number;
  name: string;
  match_type: "tag" | "symbol" | "string" | "script" | "text";
  snippet: string;
  char_id?: number | null;
}

export async function searchFile(
  sid: string,
  query: string,
  signal?: AbortSignal,
): Promise<SearchResult[]> {
  const resp = await fetch(
    `${BASE}/${sid}/search?q=${encodeURIComponent(query)}`,
    { signal },
  );
  const data = await jsonOrThrow<{ results: SearchResult[] }>(resp);
  return data.results;
}

export async function replaceResource(
  sid: string,
  index: number,
  file: File,
): Promise<void> {
  const form = new FormData();
  form.append("file", file, file.name);
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/replace`, {
    method: "POST",
    body: form,
  });
  await jsonOrThrow(resp);
}

export function downloadUrl(sid: string): string {
  return `${BASE}/${sid}/download`;
}

export function projectUrl(sid: string): string {
  return `${BASE}/${sid}/project`;
}

export interface ProjectHistory {
  undo: number;
  redo: number;
  dirty: boolean;
  snapshots: string[];
  entries: {
    operation: string;
    at: string;
    metadata?: Record<string, unknown>;
  }[];
}

export async function getHistory(sid: string): Promise<ProjectHistory> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/history`));
}

export async function createSnapshot(sid: string, name: string): Promise<void> {
  await jsonOrThrow(
    await fetch(`${BASE}/${sid}/snapshot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export async function restoreSnapshot(
  sid: string,
  name: string,
): Promise<void> {
  await jsonOrThrow(
    await fetch(`${BASE}/${sid}/snapshot/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export interface SnapshotComparison {
  identical: boolean;
  header_changed: boolean;
  changed_tags: {
    index: number;
    name: string;
    change: string;
    before_size: number;
    after_size: number;
  }[];
}

export async function compareSnapshots(
  sid: string,
  left: string,
  right?: string,
): Promise<SnapshotComparison> {
  const params = new URLSearchParams({ left });
  if (right !== undefined) params.set("right", right);
  return jsonOrThrow(await fetch(`${BASE}/${sid}/snapshot/compare?${params}`));
}

export async function downloadFile(
  sid: string,
  kind: "swf" | "project",
): Promise<Blob> {
  const resp = await fetch(kind === "swf" ? downloadUrl(sid) : projectUrl(sid));
  if (!resp.ok) await jsonOrThrow(resp);
  return resp.blob();
}

export async function undo(sid: string): Promise<void> {
  await jsonOrThrow(await fetch(`${BASE}/${sid}/undo`, { method: "POST" }));
}

export async function redo(sid: string): Promise<void> {
  await jsonOrThrow(await fetch(`${BASE}/${sid}/redo`, { method: "POST" }));
}

export function soundUrl(sid: string, index: number): string {
  return `${BASE}/${sid}/tags/${index}/export/sound`;
}

export async function getSoundInfo(
  sid: string,
  index: number,
): Promise<SoundInfo> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/tags/${index}/sound-info`));
}

export function fontUrl(sid: string, index: number): string {
  return `${BASE}/${sid}/tags/${index}/export/font`;
}

export async function getFontInfo(
  sid: string,
  index: number,
): Promise<FontInfo> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/tags/${index}/font-info`));
}

export async function getTextSvg(sid: string, index: number): Promise<string> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/export/text-svg`);
  if (!resp.ok) throw new Error((await resp.json()).detail ?? resp.statusText);
  return resp.text();
}

export async function getEditTextInfo(
  sid: string,
  index: number,
): Promise<Record<string, unknown>> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/tags/${index}/edit-text`));
}

export interface BatchExportResult {
  index: number;
  char_id?: number;
  name?: string;
  data?: string;
  format?: string;
  size?: number;
  error?: string;
}

export interface BatchExportRequest {
  tag_indices: number[];
  format: string;
}

export async function batchExportImages(
  sid: string,
  req: BatchExportRequest,
): Promise<{ results: BatchExportResult[] }> {
  const resp = await fetch(`${BASE}/${sid}/batch-export/images`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return jsonOrThrow(resp);
}

export async function batchExportShapes(
  sid: string,
  req: BatchExportRequest,
): Promise<{ results: BatchExportResult[] }> {
  const resp = await fetch(`${BASE}/${sid}/batch-export/shapes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return jsonOrThrow(resp);
}

export async function batchExportScripts(
  sid: string,
  req: BatchExportRequest,
): Promise<{ results: BatchExportResult[] }> {
  const resp = await fetch(`${BASE}/${sid}/batch-export/scripts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return jsonOrThrow(resp);
}

export async function exportAllResources(sid: string): Promise<Blob> {
  const resp = await fetch(`${BASE}/${sid}/export/all-resources`);
  if (!resp.ok) throw new Error((await resp.json()).detail ?? resp.statusText);
  return resp.blob();
}

export interface ClassInfo {
  name: string;
  super?: string;
  traits: number;
  methods: number;
  fields: number;
}

export async function getClassHierarchy(
  sid: string,
): Promise<{ classes: ClassInfo[] }> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/class-hierarchy`));
}

export async function getShapeData(sid: string, index: number): Promise<any> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/tags/${index}/shape-data`));
}

export async function updateShapeVertex(
  sid: string,
  index: number,
  data: {
    group_idx: number;
    subpath_idx: number;
    vertex_idx: number;
    x: number;
    y: number;
  },
): Promise<any> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/shape/update-vertex`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return jsonOrThrow(resp);
}

export async function addShapeEdge(
  sid: string,
  index: number,
  data: {
    group_idx: number;
    subpath_idx: number;
    after_vertex_idx: number;
    edge_type: string;
    x: number;
    y: number;
    cx?: number;
    cy?: number;
  },
): Promise<any> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/shape/add-edge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return jsonOrThrow(resp);
}

export async function deleteShapeVertex(
  sid: string,
  index: number,
  data: { group_idx: number; subpath_idx: number; vertex_idx: number },
): Promise<any> {
  const resp = await fetch(`${BASE}/${sid}/tags/${index}/shape/delete-vertex`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return jsonOrThrow(resp);
}

export async function getVideoInfo(sid: string, index: number): Promise<any> {
  return jsonOrThrow(await fetch(`${BASE}/${sid}/tags/${index}/video-info`));
}

export function videoExportUrl(sid: string): string {
  return `${BASE}/${sid}/export/video`;
}
