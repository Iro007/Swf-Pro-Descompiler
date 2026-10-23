'use strict';
/**
 * Adapter for the PySWF universal worker (`UniversalSuite.dispatch`). PySWF is
 * the engine that edits: it owns the undo history and the project, so the
 * operations of levels L4 and L5 live here, and it also answers the read levels
 * as a fallback to RustSWF.
 *
 *   protocol op        PySWF dispatch op
 *   -------------      -----------------------------------------------------
 *   open               open | load_project   (a .pyswfproj project)
 *   header, tag_tree   header, tag_tree      (the shapes the single-window UI reads)
 *   raw                raw
 *   resources          resources
 *   resource_info      resource_info
 *   resource_preview   resource_preview
 *   sprite_frames      timeline
 *   scripts            scripts
 *   decompile          decompile (python | jpexs) | code_documents (classes)
 *   assemble           edit  mode=pcode
 *   compile            edit  mode=source | compile_project | compile_saved
 *   replace_resource   resource_edit
 *   undo, redo         undo, redo
 *   save               export_swf | save_project
 *   export_fla/xfl/xml export  format=fla|xfl|xml
 *
 * Derived here, because the worker has no call for it: disassemble (from the P-code
 * decompile returns). Every other PySWF call (the manifest's native_ops) passes through
 * unchanged.
 */
const fs = require('node:fs');
const { HarnessError, KINDS } = require('../errors');
const { parseHeader } = require('../swfheader');
const {
  isClassUnit, scriptItem, decompileResult, page, diagnostics, category, KNOWN_CODES, fetchAll, pick, tagReference,
} = require('./normalize');

const PROJECT_FILE = /\.(pyswfproj|flashproj)$/i;

/** What the protocol's `open` returns, from PySWF's state. */
function openResult(state, ctx) {
  let header = null;
  try {
    header = parseHeader(fs.readFileSync(state.working_path));
  } catch {
    /* the state alone is still a valid answer */
  }
  const validation = state.validation || {};
  return {
    info: {
      filename: state.filename,
      sha256: state.revision,
      signature: state.signature ?? validation.signature ?? null,
      version: validation.version ?? header?.version ?? null,
      width: header?.frame_size ? header.frame_size.width : null,
      height: header?.frame_size ? header.frame_size.height : null,
      frame_rate: header?.frame_rate ?? null,
      frame_count: state.frame_count ?? validation.frame_count ?? null,
      tag_count: validation.tag_count ?? null,
    },
    diagnostics: diagnostics(validation.errors).concat(diagnostics(validation.warnings)),
    script_count: state.script_count ?? null,
    // The state is what undo/redo/edit return too; the session reads it to follow the document.
    ...state,
    engine: ctx.info.id,
  };
}

/** A resource row as RustSWF reports it. */
function resourceItem(item) {
  const code = item.type ?? item.code;
  const index = item.tag_index ?? item.index;
  return {
    id: `tag:${index}`,
    tag_index: index,
    tag_path: [index],
    code,
    name: item.name,
    length: item.bytes ?? item.length ?? 0,
    character_id: item.character_id ?? null,
    symbol: null,
    category: category(code),
    formats: ['raw', ...(item.formats || [])],
    diagnostics: [],
  };
}

async function resources(args, ctx) {
  const { rpc } = ctx;
  const base = pick(args, ['query']);
  const offset = Math.max(0, Number(args.offset) || 0);
  const limit = Math.min(500, Math.max(1, Number(args.limit) || 200));
  if (!Array.isArray(args.codes) && !args.other) {
    const raw = await rpc('resources', { ...base, offset, limit });
    return page(raw, raw.items.map(resourceItem));
  }
  const everything = await fetchAll(rpc, 'resources', base);
  const filtered = everything.items
    .map(resourceItem)
    .filter((item) => (args.other ? !KNOWN_CODES.has(item.code) : args.codes.includes(item.code)));
  return { total: filtered.length, offset, items: filtered.slice(offset, offset + limit) };
}

function pcodeDocument(unitId, name, text) {
  return `; ---- ${name} (${unitId}) ----\n${text}\n`;
}

/** Method units of a class, by the unit ids PySWF lists in its code document. */
async function classUnits(unitId, rpc) {
  const document = await rpc('code_documents', { document_id: unitId, engine: 'native' });
  return { document, units: document.unit_ids || [] };
}

async function decompile(args, ctx) {
  const { rpc, info } = ctx;
  const unitId = args.unit_id;
  const proxied = args.engine === 'jpexs';
  if (isClassUnit(unitId)) {
    if (proxied) throw new HarnessError(KINDS.UNSUPPORTED, 'JPEXS entrega las clases por método; pida un método', { engine: info.id });
    const { document, units } = await classUnits(unitId, rpc);
    const source = typeof document.source === 'string' && document.source !== '' ? document.source : null;
    let pcode = '';
    for (const id of units) {
      const part = await rpc('decompile', { unit_id: id, engine: 'python' });
      pcode += pcodeDocument(id, id, part.pcode);
    }
    const complete = document.completeness === 'complete';
    return {
      ...pick(document, ['kind', 'scope', 'completeness', 'compiler_eligibility', 'editable', 'revision', 'unit_ids', 'symbol_context']),
      unit_id: unitId,
      engine: info.id,
      state: source !== null && complete ? 'structured' : source !== null ? 'partial' : 'pcode_only',
      source,
      pcode,
      pcode_total: pcode.length,
      diagnostics: diagnostics(document.diagnostics),
      scope: 'class',
    };
  }
  const raw = await rpc('decompile', { unit_id: unitId, engine: proxied ? 'jpexs' : 'python' });
  return decompileResult({ ...raw, engine: undefined }, unitId, proxied ? 'jpexs' : info.id);
}

async function disassemble(args, ctx) {
  const { rpc } = ctx;
  const unitId = args.unit_id;
  let text = '';
  let diag = [];
  if (isClassUnit(unitId)) {
    const { units } = await classUnits(unitId, rpc);
    for (const id of units) {
      const part = await rpc('decompile', { unit_id: id, engine: 'python' });
      text += pcodeDocument(id, id, part.pcode);
      diag = diag.concat(diagnostics(part.diagnostics));
    }
  } else {
    const part = await rpc('decompile', { unit_id: unitId, engine: 'python' });
    text = part.pcode;
    diag = diagnostics(part.diagnostics);
  }
  const offset = Math.max(0, Number(args.offset) || 0);
  return { unit_id: unitId, state: 'decoded', pcode: text.slice(offset, offset + 65536), pcode_total: text.length, offset, diagnostics: diag, derived: true };
}

const WITH_BASE = (args, extra) => ({ ...(args.base_revision !== undefined ? { base_revision: args.base_revision } : {}), ...extra });

const suite = {
  name: 'suite',
  async invoke(op, args, ctx) {
    const { rpc, info } = ctx;
    switch (op) {
      case 'capabilities': {
        const ping = await rpc('ping', {});
        return {
          schema_version: 1,
          engine: info.id,
          display_name: info.name,
          version: ping.version ?? null,
          protocol: { name: 'flash-engine-jsonl', version: 2, framings: ['suite'], adapted: true },
          ops: [...info.worker.ops],
          native_ops: [...info.worker.nativeOps.keys()],
          derived_ops: [...info.worker.derivedOps],
          languages: info.languages,
          adapted: true,
        };
      }
      case 'open': {
        const native = PROJECT_FILE.test(String(args.path)) ? 'load_project' : 'open';
        return openResult(await rpc(native, { path: args.path }), ctx);
      }
      case 'header':
        return rpc('header', {});
      case 'tag_tree':
        return rpc('tag_tree', pick(args, ['limit']));
      case 'raw':
        return rpc('raw', args.scope === 'file' ? pick(args, ['scope', 'offset', 'limit', 'encoding']) : { ...tagReference(args), ...pick(args, ['offset', 'limit', 'encoding']) });
      case 'resources':
        return resources(args, ctx);
      case 'resource_info': {
        const named = typeof args.id === 'string' && /^tag:\d+$/.test(args.id) ? Number(args.id.slice(4)) : undefined;
        const index = args.tag_index ?? (Array.isArray(args.tag_path) ? args.tag_path[0] : named);
        if (!Number.isInteger(index)) throw new HarnessError(KINDS.BAD_REQUEST, 'Indique tag_index');
        if (Array.isArray(args.tag_path) && args.tag_path.length > 1) throw new HarnessError(KINDS.UNSUPPORTED, 'PySWF solo describe tags del nivel superior', { engine: info.id });
        const raw = await rpc('resource_info', { tag_index: index });
        return { ...raw, id: `tag:${index}`, tag_index: index, tag_path: [index] };
      }
      case 'resource_preview': {
        const reference = tagReference(args);
        if (reference.tag_path && reference.tag_path.length > 1) throw new HarnessError(KINDS.UNSUPPORTED, 'PySWF solo dibuja los tags del nivel superior', { engine: info.id });
        return rpc('resource_preview', { tag_index: reference.tag_index ?? reference.tag_path[0], ...pick(args, ['frame', 'ratio']) });
      }
      case 'sprite_frames': {
        if (args.id !== undefined || args.tag_path !== undefined) {
          throw new HarnessError(KINDS.UNSUPPORTED, 'PySWF solo dibuja los cuadros de la línea de tiempo principal', { engine: info.id });
        }
        return rpc('timeline', { frame: args.frame ?? 0 });
      }
      case 'scripts': {
        const raw = await rpc('scripts', pick(args, ['query', 'offset', 'limit']));
        return page(raw, raw.items.map(scriptItem));
      }
      case 'disassemble':
        return disassemble(args, ctx);
      case 'decompile':
        return decompile(args, ctx);
      case 'assemble': {
        const code = args.pcode ?? args.code;
        return rpc('edit', WITH_BASE(args, { unit_id: args.unit_id, code, mode: 'pcode', engine: 'python' }));
      }
      case 'compile': {
        if (args.entry !== undefined) return rpc('compile_project', pick(args, ['profile', 'entry', 'output', 'main_class', 'debug', 'base_revision']));
        if (args.unit_id === undefined) return rpc('compile_saved', pick(args, ['output', 'profile', 'main_class']));
        const params = WITH_BASE(args, {
          unit_id: args.unit_id,
          code: args.source ?? args.code,
          mode: args.mode ?? 'source',
          engine: args.engine === 'jpexs' ? 'jpexs' : 'python',
        });
        if (args.source_document !== undefined) params.source_document = args.source_document;
        return rpc('edit', params);
      }
      case 'replace_resource': {
        const { base_revision: baseRevision, tag_index: tagIndex, operation, ...rest } = args;
        return rpc('resource_edit', { ...rest, operation, tag_index: tagIndex, base_revision: baseRevision });
      }
      case 'undo':
      case 'redo':
        return rpc(op, {});
      case 'save':
        return args.format === 'project' ? rpc('save_project', { path: args.path }) : rpc('export_swf', { path: args.path });
      case 'export_fla':
      case 'export_xfl':
      case 'export_xml':
        return rpc('export', { format: op.slice('export_'.length), path: args.path, ...pick(args, ['engine']) });
      default:
        return rpc(op, args);
    }
  },
};

module.exports = { suite, openResult, resourceItem };
