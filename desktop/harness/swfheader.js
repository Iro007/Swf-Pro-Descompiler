'use strict';
/**
 * A small SWF reader for what the harness derives itself when no engine can
 * answer: the header, the tag list and raw tag bytes. FWS and CWS only; ZWS needs
 * an LZMA decoder (RustSWF has one), so those files report a partial header.
 * Tag headers are scanned, never parsed: this is not a decompiler.
 */
const zlib = require('node:zlib');
const { HarnessError, KINDS } = require('./errors');

const MAX_TAGS = 100000;
const MAX_DEPTH = 64;
const SPRITE = 39;
/** Tags whose first two bytes are the id of the character they define (as RustSWF reads it). */
const CHARACTER_TAGS = new Set([2, 6, 7, 10, 11, 14, 20, 21, 22, 32, 33, 34, 35, 36, 37, 39, 46, 48, 60, 75, 83, 84, 87, 90, 91]);
const FRAME_LABEL = 43;
const SYMBOL_TABLES = new Set([56, 76]);

class BitReader {
  constructor(bytes) {
    this.bytes = bytes;
    this.pos = 0;
  }

  unsigned(count) {
    let value = 0;
    for (let i = 0; i < count; i += 1) {
      const byte = this.bytes[this.pos >> 3];
      if (byte === undefined) throw new HarnessError(KINDS.FAILED, 'Cabecera SWF truncada');
      value = value * 2 + ((byte >> (7 - (this.pos & 7))) & 1);
      this.pos += 1;
    }
    return value;
  }

  signed(count) {
    if (count === 0) return 0;
    const value = this.unsigned(count);
    return value >= 2 ** (count - 1) ? value - 2 ** count : value;
  }

  get byteLength() {
    return Math.ceil(this.pos / 8);
  }
}

/** Uncompressed image of the file, or null for ZWS. `limit` bounds how much CWS to inflate. */
function body(buffer, signature, limit) {
  if (signature === 'FWS') return buffer.subarray(8);
  if (signature === 'CWS') {
    const input = limit ? buffer.subarray(8, 8 + limit) : buffer.subarray(8);
    return zlib.inflateSync(input, { finishFlush: zlib.constants.Z_SYNC_FLUSH });
  }
  return null;
}

function readSignature(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 8) throw new HarnessError(KINDS.FAILED, 'SWF truncado');
  const signature = buffer.toString('latin1', 0, 3);
  if (!['FWS', 'CWS', 'ZWS'].includes(signature)) throw new HarnessError(KINDS.FAILED, 'Firma SWF desconocida');
  return signature;
}

const COMPRESSION = { FWS: 'none', CWS: 'zlib', ZWS: 'lzma' };

/** Header fields only (cheap: a CWS file inflates just its first block). */
function parseHeader(buffer) {
  const signature = readSignature(buffer);
  const header = {
    signature,
    compression: COMPRESSION[signature],
    version: buffer[3],
    file_length: buffer.readUInt32LE(4),
    file_size: buffer.length,
    frame_size: null,
    frame_rate: null,
    frame_count: null,
  };
  const data = body(buffer, signature, 4096);
  if (data === null) return { ...header, partial: 'ZWS usa LZMA: el tamaño de escena y la velocidad no se leen sin RustSWF' };
  const bits = new BitReader(data);
  const width = bits.unsigned(5);
  const [xMin, xMax, yMin, yMax] = [bits.signed(width), bits.signed(width), bits.signed(width), bits.signed(width)];
  const at = bits.byteLength;
  if (data.length < at + 4) throw new HarnessError(KINDS.FAILED, 'Cabecera SWF truncada');
  header.frame_size = { xmin: xMin, xmax: xMax, ymin: yMin, ymax: yMax, width: (xMax - xMin) / 20, height: (yMax - yMin) / 20 };
  header.frame_rate = data.readUInt16LE(at) / 256;
  header.frame_count = data.readUInt16LE(at + 2);
  return header;
}

function tagName(code) {
  return TAG_NAMES[code] || `Unknown_${code}`;
}

/**
 * Every tag, nested sprite content included, in document order, like RustSWF's
 * inventory: `{path, code, name, start, dataStart, length}` with offsets into
 * the uncompressed image.
 */
function scanTags(buffer) {
  const signature = readSignature(buffer);
  const data = body(buffer, signature);
  if (data === null) throw new HarnessError(KINDS.UNSUPPORTED, 'ZWS usa LZMA: la lectura derivada no está disponible; use RustSWF');
  const bits = new BitReader(data);
  bits.unsigned(5);
  const width = (data[0] >> 3) & 31;
  for (let i = 0; i < 4; i += 1) bits.signed(width);
  const first = bits.byteLength + 4;
  const tags = [];
  const scan = (from, end, parent, depth) => {
    if (depth >= MAX_DEPTH) throw new HarnessError(KINDS.LIMIT, 'Sprites anidados más de 64 niveles');
    let pos = from;
    let index = 0;
    while (pos + 2 <= end) {
      if (tags.length >= MAX_TAGS) throw new HarnessError(KINDS.LIMIT, 'El SWF supera 100000 tags');
      const start = pos;
      const header = data.readUInt16LE(pos);
      pos += 2;
      const code = header >> 6;
      let length = header & 63;
      if (length === 63) {
        if (pos + 4 > end) throw new HarnessError(KINDS.FAILED, `Cabecera de tag truncada en ${start}`);
        length = data.readUInt32LE(pos);
        pos += 4;
      }
      if (pos + length > end) throw new HarnessError(KINDS.FAILED, `El tag ${code} excede su contenedor en ${start}`);
      const path = [...parent, index];
      const tag = { path, code, name: tagName(code), start, dataStart: pos, length };
      tags.push(tag);
      if (code === SPRITE && length >= 4) scan(pos + 4, pos + length, path, depth + 1);
      pos += length;
      index += 1;
      if (code === 0) break;
    }
  };
  scan(first, data.length, [], 0);
  annotate(data, tags);
  return { signature, data, tags };
}

/** Character ids, exported symbol names (ExportAssets / SymbolClass) and frame label text. */
function annotate(data, tags) {
  const symbols = new Map();
  for (const tag of tags) {
    if (!SYMBOL_TABLES.has(tag.code) || tag.length < 2) continue;
    let pos = tag.dataStart + 2;
    const end = tag.dataStart + tag.length;
    for (let n = data.readUInt16LE(tag.dataStart); n > 0 && pos + 2 <= end; n -= 1) {
      const id = data.readUInt16LE(pos);
      pos += 2;
      const stop = data.indexOf(0, pos);
      if (stop < 0 || stop >= end) break;
      if (!symbols.has(id)) symbols.set(id, data.toString('utf8', pos, stop));
      pos = stop + 1;
    }
  }
  for (const tag of tags) {
    tag.character_id = CHARACTER_TAGS.has(tag.code) && tag.length >= 2 ? data.readUInt16LE(tag.dataStart) : null;
    tag.symbol = tag.character_id !== null ? symbols.get(tag.character_id) ?? null : null;
    if (tag.code === FRAME_LABEL) {
      const stop = data.indexOf(0, tag.dataStart);
      tag.label_text = data.toString('utf8', tag.dataStart, stop < 0 || stop > tag.dataStart + tag.length ? tag.dataStart + tag.length : stop);
    }
  }
}

/**
 * What the header node shows, from the top-level tags of a scan: FileAttributes,
 * SetBackgroundColor, ScriptLimits and Metadata, plus the counts (`tag_count` and
 * `shown_frames` count top-level tags only, as PySWF does).
 */
function headerTags({ data, tags }) {
  const top = tags.filter((t) => t.path.length === 1);
  const first = (code) => top.find((t) => t.code === code);
  const bytes = (tag) => data.subarray(tag.dataStart, tag.dataStart + tag.length);
  const out = {
    tag_count: top.length,
    shown_frames: top.filter((t) => t.code === 1).length,
    abc_blocks: top.filter((t) => t.code === 72 || t.code === 82).length,
    background_color: null,
    file_attributes: null,
    script_limits: null,
    metadata: null,
  };
  const color = first(9);
  if (color && color.length >= 3) {
    const [r, g, b] = bytes(color);
    out.background_color = `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
  }
  let flags = null;
  const attributes = first(69);
  if (attributes && attributes.length >= 1) {
    flags = bytes(attributes)[0];
    out.file_attributes = {
      use_network: Boolean(flags & 0x01),
      actionscript3: Boolean(flags & 0x08),
      has_metadata: Boolean(flags & 0x10),
      use_gpu: Boolean(flags & 0x20),
      use_direct_blit: Boolean(flags & 0x40),
    };
  }
  out.has_as3 = Boolean(flags !== null && flags & 0x08) || out.abc_blocks > 0;
  const limits = first(65);
  if (limits && limits.length >= 4) {
    const view = bytes(limits);
    out.script_limits = { max_recursion_depth: view.readUInt16LE(0), script_timeout_seconds: view.readUInt16LE(2) };
  }
  const metadata = first(77);
  if (metadata) {
    const view = bytes(metadata);
    const end = view.indexOf(0);
    out.metadata = view.subarray(0, end < 0 ? view.length : end).toString('utf8').slice(0, 65536);
  }
  out.has_metadata = metadata !== undefined || Boolean(flags !== null && flags & 0x10);
  return out;
}

// Tag names as PySWF and RustSWF both report them (a test keeps this table equal to PySWF's).
const TAG_NAMES = {
  0: 'End', 1: 'ShowFrame', 2: 'DefineShape', 3: 'FreeCharacter', 4: 'PlaceObject', 5: 'RemoveObject', 6: 'DefineBits',
  7: 'DefineButton', 8: 'JPEGTables', 9: 'SetBackgroundColor', 10: 'DefineFont', 11: 'DefineText', 12: 'DoAction',
  13: 'DefineFontInfo', 14: 'DefineSound', 15: 'StartSound', 17: 'DefineButtonSound', 18: 'SoundStreamHead',
  19: 'SoundStreamBlock', 20: 'DefineBitsLossless', 21: 'DefineBitsJPEG2', 22: 'DefineShape2', 23: 'DefineButtonCxform',
  24: 'Protect', 26: 'PlaceObject2', 28: 'RemoveObject2', 32: 'DefineShape3', 33: 'DefineText2', 34: 'DefineButton2',
  35: 'DefineBitsJPEG3', 36: 'DefineBitsLossless2', 37: 'DefineEditText', 38: 'DefineVideo', 39: 'DefineSprite',
  40: 'NameCharacter', 41: 'ProductInfo', 42: 'DefineTextFormat', 43: 'FrameLabel', 45: 'SoundStreamHead2',
  46: 'DefineMorphShape', 48: 'DefineFont2', 49: 'GenCommand', 51: 'CharacterSet', 52: 'FontRef', 56: 'ExportAssets',
  57: 'ImportAssets', 58: 'EnableDebugger', 59: 'DoInitAction', 60: 'DefineVideoStream', 61: 'VideoFrame',
  62: 'DefineFontInfo2', 63: 'DebugID', 64: 'EnableDebugger2', 65: 'ScriptLimits', 66: 'SetTabIndex',
  69: 'FileAttributes', 70: 'PlaceObject3', 71: 'ImportAssets2', 72: 'DoABC1', 73: 'DefineFontAlignZones',
  74: 'CSMTextSettings', 75: 'DefineFont3', 76: 'SymbolClass', 77: 'Metadata', 78: 'DefineScalingGrid',
  82: 'DoABC', 83: 'DefineShape4', 84: 'DefineMorphShape2', 86: 'DefineSceneAndFrameLabelData',
  87: 'DefineBinaryData', 88: 'DefineFontName', 89: 'StartSound2', 90: 'DefineBitsJPEG4', 91: 'DefineFont4',
  93: 'EnableTelemetry', 94: 'PlaceObject4',
};

module.exports = { parseHeader, scanTags, headerTags, tagName, TAG_NAMES, CHARACTER_TAGS };
