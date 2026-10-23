'use strict';
/** Shape fixes shared by the adapters: every engine's answer becomes the protocol's answer. */
const { HarnessError, KINDS } = require('../errors');

const CLASS_UNIT = /\/class:\d+$/;
const isClassUnit = (id) => typeof id === 'string' && CLASS_UNIT.test(id);

function language(raw) {
  switch (raw) {
    case 'avm1':
    case 'as1':
    case 'as2':
      return 'as2';
    case 'avm2':
    case 'as3':
      return 'as3';
    default:
      return raw;
  }
}

/** Diagnostics are `{code, message, offset}`; some engines send strings or null. */
function diagnostics(list, code = 'ENGINE') {
  if (!Array.isArray(list)) return [];
  return list.map((item) => (typeof item === 'string' ? { code, message: item, offset: null } : item));
}

/** Category of a tag code, as RustSWF's `resources` reports it. */
function category(code) {
  if ([2, 22, 32, 83].includes(code)) return 'shapes';
  if ([46, 84].includes(code)) return 'morphs';
  if ([6, 20, 21, 35, 36, 90].includes(code)) return 'images';
  if ([10, 13, 48, 62, 75, 88, 91].includes(code)) return 'fonts';
  if ([11, 33, 37].includes(code)) return 'texts';
  if ([14, 15, 18, 19, 45, 89].includes(code)) return 'sounds';
  if ([7, 34].includes(code)) return 'buttons';
  if (code === 39) return 'sprites';
  if ([12, 59, 72, 82].includes(code)) return 'scripts';
  if ([60, 61].includes(code)) return 'videos';
  if (code === 87) return 'binary';
  if ([0, 1, 4, 5, 26, 28, 43, 70, 94].includes(code)) return 'frames';
  return 'others';
}

/** Codes RustSWF files under a named category; `other: true` asks for the rest. */
const KNOWN_CODES = new Set([
  6, 20, 21, 35, 36, 90, 2, 22, 32, 83, 46, 84, 14, 15, 18, 19, 45, 89, 10, 11, 13, 33, 37, 48, 62, 73, 74, 75, 88,
  91, 39, 7, 17, 23, 34, 1, 4, 5, 26, 28, 43, 70, 86, 94, 12, 59, 72, 82,
]);

function scriptItem(item) {
  const id = item.id ?? item.document_id;
  const own = Array.isArray(item.diagnostics) ? item.diagnostics : item.diagnostic ? [item.diagnostic] : [];
  return {
    ...item,
    id,
    name: item.name ?? item.display_name ?? id,
    language: language(item.language),
    engine_language: item.language,
    kind: item.kind === 'abc_method' ? 'method' : item.kind,
    tag_path: item.tag_path,
    byte_len: item.byte_len ?? item.bytecode_bytes ?? null,
    sha256: item.sha256 ?? item.bytecode_sha256 ?? null,
    diagnostics: diagnostics(own),
  };
}

function decompileResult(raw, unitId, engine) {
  const source = typeof raw.source === 'string' ? raw.source : null;
  const pcode = typeof raw.pcode === 'string' ? raw.pcode : '';
  return {
    ...raw,
    unit_id: raw.unit_id ?? unitId,
    engine,
    state: raw.state ?? (source !== null ? 'structured' : 'pcode_only'),
    source,
    pcode,
    pcode_total: raw.pcode_total ?? pcode.length,
    diagnostics: diagnostics(raw.diagnostics),
  };
}

function disassemblyResult(raw, unitId) {
  const pcode = typeof raw.pcode === 'string' ? raw.pcode : '';
  return {
    ...raw,
    unit_id: raw.unit_id ?? unitId,
    state: raw.state ?? 'decoded',
    pcode,
    pcode_total: raw.pcode_total ?? pcode.length,
    offset: raw.offset ?? 0,
    diagnostics: diagnostics(raw.diagnostics),
  };
}

/** A resource or script page: `{total, offset, items}`. */
function page(raw, items) {
  return { ...raw, total: raw.total ?? items.length, offset: raw.offset ?? 0, items };
}

/** All pages of a paged listing (`scripts`, `resources`). */
async function fetchAll(rpc, op, base = {}, pageSize = 500) {
  const items = [];
  let offset = 0;
  for (;;) {
    const reply = await rpc(op, { ...base, offset, limit: pageSize });
    items.push(...reply.items);
    offset += reply.items.length;
    if (reply.items.length === 0 || offset >= reply.total) return { total: reply.total, items };
  }
}

function pick(source, keys) {
  const out = {};
  for (const key of keys) if (source[key] !== undefined) out[key] = source[key];
  return out;
}

/**
 * Which tag a request names: `tag_path` (a tag inside a sprite), `tag_index` (top level) or
 * `id` (`tag:6`, `tag:2/0`). Returns `{tag_path}` or `{tag_index}`.
 */
function tagReference(args) {
  if (args.tag_path !== undefined) return { tag_path: args.tag_path };
  if (args.tag_index !== undefined) return { tag_index: args.tag_index };
  const named = typeof args.id === 'string' ? /^tag:(\d+(?:\/\d+)*)$/.exec(args.id) : null;
  if (named) {
    const path = named[1].split('/').map(Number);
    return path.length === 1 ? { tag_index: path[0] } : { tag_path: path };
  }
  throw new HarnessError(KINDS.BAD_REQUEST, 'Indique tag_index, tag_path o id');
}

module.exports = {
  isClassUnit, language, diagnostics, category, KNOWN_CODES, scriptItem, decompileResult, disassemblyResult, page, fetchAll, pick, tagReference,
};
