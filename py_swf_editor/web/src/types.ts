// Types mirroring the FastAPI backend contract (server/routers)

export interface FileInfo {
  session_id: string;
  filename: string;
  signature: "FWS" | "CWS" | "ZWS";
  version: number;
  frame_rate: number;
  frame_count: number;
  width: number;
  height: number;
  tag_count: number;
  dirty?: boolean;
}

export interface TagInfo {
  index: number;
  code: number;
  name: string;
  size: number;
  char_id: number | null;
  symbol_name: string | null;
  parse_error: string | null;
}

export interface SoundInfo {
  sound_id: number;
  format: number;
  format_name: string;
  rate: number;
  bits: number;
  channels: number;
  sample_count: number;
}

export interface FontInfo {
  font_id: number;
  name: string;
  italic: boolean;
  bold: boolean;
  num_glyphs: number;
  codes: number[];
  layout: { ascent: number; descent: number; leading: number } | null;
}

export interface ScriptBody {
  body_index: number;
  name: string;
  code: string;
}

export interface ScriptListing {
  kind: "avm1" | "avm2";
  scripts: ScriptBody[];
}

// Tree items in the JPEXS-like directory view
export type SWFCategory =
  | "header"
  | "shapes"
  | "images"
  | "sounds"
  | "texts"
  | "buttons"
  | "sprites"
  | "scripts"
  | "frames"
  | "others";

export interface TreeItem {
  id: string;
  label: string;
  type: SWFCategory | "tag-node";
  tagIndex?: number;
  children?: TreeItem[];
  tagCode?: number;
}
