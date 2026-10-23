'use strict';
/**
 * Adapter for the workers that predate protocol v2 (JsSWF, GoSWF, AsmSWF): they
 * answer open, info, scripts, disassemble and decompile with `id` for the unit.
 * The adapter gives them the protocol's names and shapes, and the operation
 * they lack (`header`, `capabilities`) is derived from `info` and the manifest.
 */
const path = require('node:path');
const { HarnessError, KINDS } = require('../errors');
const { isClassUnit, scriptItem, decompileResult, disassemblyResult, page, diagnostics, pick } = require('./normalize');

const SIGNATURES = { none: 'FWS', FWS: 'FWS', zlib: 'CWS', CWS: 'CWS', lzma: 'ZWS', ZWS: 'ZWS' };
const COMPRESSION = { FWS: 'none', CWS: 'zlib', ZWS: 'lzma' };

/** Engines disagree on the names of the header fields; this reads any of them. */
function documentInfo(raw, sourcePath) {
  const info = raw.info ?? raw;
  const signature = SIGNATURES[info.signature] ?? SIGNATURES[info.compression] ?? null;
  const width = info.width ?? (info.stage_width_twips !== undefined ? info.stage_width_twips / 20 : null);
  const height = info.height ?? (info.stage_height_twips !== undefined ? info.stage_height_twips / 20 : null);
  return {
    filename: info.filename ?? (sourcePath ? path.basename(sourcePath) : null),
    sha256: info.sha256 ?? null,
    signature,
    version: info.version ?? null,
    width,
    height,
    frame_rate: info.frame_rate ?? null,
    frame_count: info.frame_count ?? null,
    tag_count: info.tag_count ?? null,
  };
}

/** The header these engines can derive from `info`: the shape every engine uses, with null for what `info` lacks. */
function headerFromInfo(raw) {
  const info = documentInfo(raw);
  const file = raw.info ?? raw;
  const twipsW = file.stage_width_twips ?? (info.width === null ? null : Math.round(info.width * 20));
  const twipsH = file.stage_height_twips ?? (info.height === null ? null : Math.round(info.height * 20));
  return {
    filename: info.filename,
    signature: info.signature,
    compression: info.signature ? COMPRESSION[info.signature] : null,
    version: info.version,
    file_length: file.file_length ?? null,
    file_size: null,
    frame_size:
      twipsW === null || twipsH === null
        ? null
        : { xmin: 0, xmax: twipsW, ymin: 0, ymax: twipsH, width: twipsW / 20, height: twipsH / 20 },
    frame_rate: info.frame_rate,
    frame_count: info.frame_count,
    shown_frames: null,
    tag_count: info.tag_count,
    abc_blocks: null,
    has_as3: null,
    has_metadata: null,
    file_attributes: null,
    metadata: null,
    background_color: null,
    script_limits: null,
    sha256: info.sha256,
    revision: info.sha256,
    derived: true,
    diagnostics: [{ code: 'header_partial', message: 'Cabecera derivada de info: faltan los atributos de archivo, color de fondo y límites de script', offset: null }],
  };
}

function refuseClasses(unitId, engine) {
  if (isClassUnit(unitId)) {
    throw new HarnessError(KINDS.UNSUPPORTED, `${engine.name} trabaja con métodos, no con clases completas`, { engine: engine.id });
  }
}

const legacy = {
  name: 'legacy',
  async invoke(op, args, ctx) {
    const { rpc, info } = ctx;
    switch (op) {
      case 'capabilities': {
        let own = {};
        try {
          own = await rpc('capabilities', {});
        } catch {
          /* GoSWF and AsmSWF have no capabilities call; the manifest says it all */
        }
        return {
          schema_version: 1,
          engine: info.id,
          display_name: info.name,
          version: own.version ?? null,
          protocol: { name: 'flash-engine-jsonl', version: 2, framings: ['legacy'], adapted: true },
          ops: [...info.worker.ops],
          native_ops: [...info.worker.nativeOps.keys()],
          languages: info.languages,
          adapted: true,
        };
      }
      case 'open': {
        const raw = await rpc('open', { path: args.path });
        return { info: documentInfo(raw, args.path), diagnostics: diagnostics(raw.diagnostics), script_count: raw.script_count ?? raw.info?.script_count ?? null };
      }
      case 'header':
        return headerFromInfo(await rpc('info', {}));
      case 'scripts': {
        const raw = await rpc('scripts', pick(args, ['query', 'offset', 'limit']));
        return page(raw, raw.items.map(scriptItem));
      }
      case 'disassemble': {
        refuseClasses(args.unit_id, info);
        const raw = await rpc('disassemble', { id: args.unit_id, ...pick(args, ['offset', 'limit']) });
        return disassemblyResult(raw, args.unit_id);
      }
      case 'decompile': {
        refuseClasses(args.unit_id, info);
        return decompileResult(await rpc('decompile', { id: args.unit_id }), args.unit_id, info.id);
      }
      default:
        return rpc(op, args);
    }
  },
};

module.exports = { legacy, documentInfo, headerFromInfo };
