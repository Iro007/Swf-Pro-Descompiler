/**
 * SWF Container (FWS, CWS, ZWS) parser and canonical script inventory for JsSWF.
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { decompressLzma } from "./lzma.mjs";
import { parseAbc } from "./avm2.mjs";
import { discoverNestedAvm1Functions, embeddedAvm1Actions } from "./avm1.mjs";

export const MAX_INPUT_BYTES = 100 * 1024 * 1024;
export const MAX_NORMALIZED_BYTES = 256 * 1024 * 1024;

export function sha256Hex(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

export class BitReader {
  constructor(buffer, startOffset = 0) {
    this.buffer = buffer;
    this.startOffset = startOffset;
    this.bitPos = 0;
  }

  unsigned(n) {
    if (n > 32 || this.startOffset * 8 + this.bitPos + n > this.buffer.length * 8) {
      throw new Error("Truncated SWF bit field");
    }
    let val = 0;
    for (let i = 0; i < n; i++) {
      const totalBit = this.startOffset * 8 + this.bitPos;
      const byteIdx = totalBit >> 3;
      const bitShift = 7 - (totalBit & 7);
      val = ((val << 1) | ((this.buffer[byteIdx] >> bitShift) & 1)) >>> 0;
      this.bitPos++;
    }
    return val;
  }

  signed(n) {
    if (n === 0) return 0;
    const val = this.unsigned(n);
    return (val << (32 - n)) >> (32 - n);
  }

  align() {
    if ((this.bitPos & 7) !== 0) {
      this.bitPos = (this.bitPos + 7) & ~7;
    }
  }

  byteLen() {
    return (this.bitPos + 7) >> 3;
  }
}

export const TAG_NAMES = {
  0: "End",
  1: "ShowFrame",
  2: "DefineShape",
  4: "PlaceObject",
  5: "RemoveObject",
  6: "DefineBits",
  7: "DefineButton",
  8: "JPEGTables",
  9: "SetBackgroundColor",
  10: "DefineFont",
  11: "DefineText",
  12: "DoAction",
  13: "DefineFontInfo",
  14: "DefineSound",
  15: "StartSound",
  17: "DefineButtonSound",
  18: "SoundStreamHead",
  19: "SoundStreamBlock",
  20: "DefineBitsLossless",
  21: "DefineBitsJPEG2",
  22: "DefineShape2",
  23: "DefineButtonCxform",
  24: "Protect",
  26: "PlaceObject2",
  28: "RemoveObject2",
  32: "DefineShape3",
  33: "DefineText2",
  34: "DefineButton2",
  35: "DefineBitsJPEG3",
  36: "DefineBitsLossless2",
  37: "DefineEditText",
  39: "DefineSprite",
  43: "FrameLabel",
  45: "SoundStreamHead2",
  46: "DefineMorphShape",
  48: "DefineFont2",
  56: "ExportAssets",
  59: "DoInitAction",
  60: "DefineVideoStream",
  61: "VideoFrame",
  62: "DefineFontInfo2",
  64: "EnableDebugger2",
  65: "ScriptLimits",
  66: "SetTabIndex",
  69: "FileAttributes",
  70: "PlaceObject3",
  71: "ImportAssets2",
  72: "DoABC1",
  73: "DefineFontAlignZones",
  74: "CSMTextSettings",
  75: "DefineFont3",
  76: "SymbolClass",
  77: "Metadata",
  78: "DefineScalingGrid",
  82: "DoABC",
  83: "DefineShape4",
  84: "DefineMorphShape2",
  86: "DefineSceneAndFrameLabelData",
  87: "DefineBinaryData",
  88: "DefineFontName",
  89: "StartSound2",
  90: "DefineBitsJPEG4",
  91: "DefineFont4",
  94: "PlaceObject4",
};

const DEFINES_CHARACTER = new Set([
  2, 6, 7, 10, 11, 14, 20, 21, 22, 32, 33, 34, 35, 36, 37, 39, 46, 48, 60, 75, 83, 84, 87, 90, 91,
]);

export function parseSwfFile(filePath) {
  const raw = fs.readFileSync(filePath);
  return parseSwfBytes(raw, path.basename(filePath));
}

export function parseSwfBytes(input, filename = "input.swf") {
  if (input.length > MAX_INPUT_BYTES) {
    throw new Error("SWF exceeds 100 MiB input limit");
  }
  if (input.length < 8) {
    throw new Error("Truncated SWF header");
  }
  const signature = input.subarray(0, 3).toString("ascii");
  const version = input[3];
  if (version === 0) {
    throw new Error("SWF version 0 is invalid");
  }
  const declared = input.readUInt32LE(4);
  if (declared < 12 || declared > MAX_NORMALIZED_BYTES) {
    throw new Error("Invalid or oversized declared SWF length");
  }
  const expected = declared - 8;
  let body;
  if (signature === "FWS") {
    if (input.length !== declared) {
      throw new Error("FWS length differs from declared length");
    }
    body = input.subarray(8);
  } else if (signature === "CWS") {
    if (version < 6) {
      throw new Error("CWS requires SWF version 6 or newer");
    }
    body = zlib.inflateSync(input.subarray(8), { maxOutputLength: expected + 1 });
    if (body.length !== expected) {
      throw new Error("CWS expanded length differs from declared length");
    }
  } else if (signature === "ZWS") {
    if (version < 13) {
      throw new Error("ZWS requires SWF version 13 or newer");
    }
    if (input.length < 17) {
      throw new Error("Truncated ZWS properties");
    }
    const compressedLen = input.readUInt32LE(8);
    if (compressedLen !== input.length - 17) {
      throw new Error("ZWS compressed length differs from payload length");
    }
    body = decompressLzma(input.subarray(12, 17), input.subarray(17), expected);
    if (body.length !== expected) {
      throw new Error("ZWS expanded length differs from declared length");
    }
  } else {
    throw new Error("Expected FWS, CWS or ZWS signature");
  }

  const normalized = Buffer.allocUnsafe(8 + body.length);
  normalized.write("FWS", 0, 3, "ascii");
  input.copy(normalized, 3, 3, 8);
  body.copy(normalized, 8);

  const bits = new BitReader(normalized, 8);
  const nbits = bits.unsigned(5);
  const x0 = bits.signed(nbits);
  const x1 = bits.signed(nbits);
  const y0 = bits.signed(nbits);
  const y1 = bits.signed(nbits);
  if (x1 < x0 || y1 < y0) {
    throw new Error("Invalid SWF stage rectangle");
  }
  const headerEnd = 8 + bits.byteLen();
  if (headerEnd + 4 > normalized.length) {
    throw new Error("Truncated frame metadata");
  }
  const frameRate = normalized.readUInt16LE(headerEnd) / 256.0;
  const frameCount = normalized.readUInt16LE(headerEnd + 2);

  const tags = [];
  const diagnostics = [];

  function scan(pos, end, parentPath) {
    if (parentPath.length >= 64) {
      throw new Error("Nested sprite depth exceeds 64");
    }
    let index = 0;
    let ended = false;
    while (pos < end) {
      if (tags.length >= 100000) {
        throw new Error("SWF exceeds 100000 tags");
      }
      if (pos + 2 > end) {
        throw new Error(`Truncated tag header at offset ${pos}`);
      }
      const start = pos;
      const header = normalized.readUInt16LE(pos);
      pos += 2;
      const code = header >>> 6;
      let length = header & 63;
      if (length === 63) {
        if (pos + 4 > end) {
          throw new Error(`Truncated long tag header at offset ${start}`);
        }
        length = normalized.readUInt32LE(pos);
        pos += 4;
      }
      const tagEnd = pos + length;
      if (tagEnd > end) {
        throw new Error(`Tag ${code} exceeds its container at offset ${start}`);
      }
      const tagPath = [...parentPath, index];
      const characterId = DEFINES_CHARACTER.has(code) && length >= 2 ? normalized.readUInt16LE(pos) : null;
      const record = {
        id: `tag:${tagPath.join("/")}`,
        path: tagPath,
        code,
        name: TAG_NAMES[code] || `Unknown_${code}`,
        start,
        data_start: pos,
        length,
        character_id: characterId,
        symbol: null,
        diagnostics: [],
      };
      tags.push(record);
      if (code === 39) {
        if (length < 4) {
          throw new Error(`Truncated sprite at offset ${pos}`);
        }
        scan(pos + 4, tagEnd, tagPath);
      }
      if (code === 0) {
        if (length !== 0) {
          throw new Error("End tag cannot have a payload");
        }
        ended = true;
        if (tagEnd < end) {
          diagnostics.push({
            code: "trailing_after_end",
            message: `${end - tagEnd} bytes after End are retained`,
            offset: tagEnd,
          });
        }
        break;
      }
      pos = tagEnd;
      index++;
    }
    if (!ended) {
      diagnostics.push({
        code: "missing_end",
        message: "Timeline has no End tag",
        offset: end,
      });
    }
  }

  scan(headerEnd + 4, normalized.length, []);

  // Read symbols from ExportAssets (56) and SymbolClass (76)
  const symbols = new Map();
  for (const tag of tags) {
    if (tag.code === 56 || tag.code === 76) {
      const data = normalized.subarray(tag.data_start, tag.data_start + tag.length);
      try {
        if (data.length < 2) throw new Error("Truncated symbol table");
        const count = data.readUInt16LE(0);
        let p = 2;
        for (let i = 0; i < count; i++) {
          if (p + 2 > data.length) throw new Error("Truncated symbol ID");
          const id = data.readUInt16LE(p);
          p += 2;
          const zero = data.indexOf(0, p);
          if (zero < 0) throw new Error("Unterminated symbol name");
          if (!symbols.has(id)) {
            symbols.set(id, data.subarray(p, zero).toString("utf8"));
          }
          p = zero + 1;
        }
      } catch (err) {
        diagnostics.push({ code: "symbol_table", message: err.message, offset: tag.data_start });
      }
    }
  }
  for (const tag of tags) {
    if (tag.character_id !== null && symbols.has(tag.character_id)) {
      tag.symbol = symbols.get(tag.character_id);
    }
  }

  const info = {
    filename,
    sha256: sha256Hex(input),
    signature,
    version,
    width: (x1 - x0) / 20.0,
    height: (y1 - y0) / 20.0,
    frame_rate: frameRate,
    frame_count: frameCount,
    tag_count: tags.length,
  };

  return { info, normalized, original: input, tags, diagnostics };
}

export function buildInventory(movie) {
  const units = [];
  for (const tag of movie.tags) {
    const data = movie.normalized.subarray(tag.data_start, tag.data_start + tag.length);
    try {
      if (tag.code === 72 || tag.code === 82) {
        let start = 0;
        if (tag.code === 82) {
          if (data.length < 5) throw new Error("Truncated DoABC header");
          const zero = data.indexOf(0, 4);
          if (zero < 0) throw new Error("Unterminated DoABC name");
          start = zero + 1;
        }
        const abcBytes = data.subarray(start);
        let abc;
        try {
          abc = parseAbc(abcBytes);
        } catch (err) {
          units.push({
            id: `${tag.id}/diagnostic`,
            name: tag.name,
            language: "avm2",
            kind: "abc_raw",
            tag_path: tag.path,
            byte_start: tag.data_start + start,
            byte_len: abcBytes.length,
            sha256: sha256Hex(abcBytes),
            diagnostics: [{ code: "abc_invalid", message: err.message, offset: tag.data_start + start }],
            parameters: [],
            avm1_registers: [],
            code: abcBytes,
          });
          continue;
        }
        if (abc.bodies.length === 0) {
          units.push({
            id: `${tag.id}/abc`,
            name: tag.symbol || "ABC signatures",
            language: "avm2",
            kind: "abc_outline",
            tag_path: tag.path,
            abc_version: `${abc.major}.${abc.minor}`,
            abc_sha256: abc.raw_sha256,
            byte_start: tag.data_start + start,
            byte_len: abcBytes.length,
            sha256: sha256Hex(abcBytes),
            diagnostics: [...tag.diagnostics],
            parameters: [],
            avm1_registers: [],
            code: abcBytes,
            abc,
          });
        } else {
          for (let bodyIndex = 0; bodyIndex < abc.bodies.length; bodyIndex++) {
            const body = abc.bodies[bodyIndex];
            const method = body.method;
            const mInfo = abc.methods[method] || { param_types: [], param_names: [] };
            const parameters = mInfo.param_types.map((_, i) => {
              const nameIdx = mInfo.param_names[i];
              const str = nameIdx ? abc.strings[nameIdx] : "";
              return str || `arg${i + 1}`;
            });
            units.push({
              id: `${tag.id}/method:${method}`,
              name: abc.methodName(method),
              language: "avm2",
              kind: "method",
              tag_path: tag.path,
              method_index: method,
              body_index: bodyIndex,
              abc_version: `${abc.major}.${abc.minor}`,
              abc_sha256: abc.raw_sha256,
              byte_start: tag.data_start + start + body.code_offset,
              byte_len: body.code.length,
              sha256: sha256Hex(body.code),
              diagnostics: [...tag.diagnostics],
              parameters,
              avm1_registers: [],
              code: body.code,
              abc,
              body,
            });
          }
        }
      } else if (tag.code === 12 || tag.code === 59) {
        const start = tag.code === 59 ? 2 : 0;
        if (data.length < start) {
          throw new Error("DoInitAction lacks character id");
        }
        const code = data.subarray(start);
        const parentUnit = {
          id: `${tag.id}/actions`,
          name: tag.name,
          language: "avm1",
          kind: start === 0 ? "frame" : "init",
          tag_path: tag.path,
          byte_start: tag.data_start + start,
          byte_len: code.length,
          sha256: sha256Hex(code),
          diagnostics: [...tag.diagnostics],
          parameters: [],
          avm1_registers: [],
          avm1_flags: null,
          event_flags: null,
          avm1_constants: [],
          code,
        };
        const nested = discoverNestedAvm1Functions(parentUnit, code, parentUnit.byte_start, [], true, 0);
        units.push(parentUnit);
        units.push(...nested);
      } else if (tag.code === 7 || tag.code === 34 || tag.code === 26 || tag.code === 70 || tag.code === 94) {
        const containers = embeddedAvm1Actions(data, tag.code, movie.info.version);
        for (let ordinal = 0; ordinal < containers.length; ordinal++) {
          const { kind, start, end, event } = containers[ordinal];
          const code = data.subarray(start, end);
          const hexEvent = event !== null ? `0x${(event >>> 0).toString(16)}` : "0x0";
          const parentUnit = {
            id: `${tag.id}/${kind}:${ordinal}`,
            name: `${tag.name} ${kind} ${ordinal} flags=${hexEvent}`,
            language: "avm1",
            kind,
            tag_path: tag.path,
            byte_start: tag.data_start + start,
            byte_len: code.length,
            sha256: sha256Hex(code),
            diagnostics: [...tag.diagnostics],
            parameters: [],
            avm1_registers: [],
            avm1_flags: null,
            event_flags: kind === "button" ? null : event,
            avm1_constants: [],
            code,
          };
          const nested = discoverNestedAvm1Functions(parentUnit, code, parentUnit.byte_start, [], true, 0);
          units.push(parentUnit);
          units.push(...nested);
        }
      }
    } catch (err) {
      units.push({
        id: `${tag.id}/diagnostic`,
        name: tag.name,
        language: "unknown",
        kind: "diagnostic",
        tag_path: tag.path,
        byte_start: tag.data_start,
        byte_len: data.length,
        sha256: sha256Hex(data),
        diagnostics: [...tag.diagnostics, { code: "script_container_error", message: err.message, offset: tag.data_start }],
        parameters: [],
        avm1_registers: [],
        code: data,
      });
    }
  }
  return units;
}
