'use strict';
/**
 * Last-resort readers built into the harness. When no engine can answer `header`
 * or `raw` (RustSWF cannot read the file and PySWF is not running), the harness reads the
 * working copy itself and says so: `served_by: "harness"`. They need no process, so they
 * also work when every engine is missing, and they answer in the shapes every engine
 * uses (see PROTOCOL.md, `header` and `raw`). FWS and CWS only; ZWS needs an LZMA decoder.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { HarnessError, KINDS } = require('./errors');
const { parseHeader, scanTags, headerTags } = require('./swfheader');

const BUILTIN = Object.freeze({ id: 'harness', name: 'Arnés (lectura directa del archivo)', ops: new Set(['header', 'raw']) });

const DEFAULT_RAW_PAGE = 4096;
const MAX_RAW_PAGE = 64 * 1024;

function workingBytes(document) {
  if (!document || !document.workingPath) throw new HarnessError(KINDS.NOT_OPEN, 'No hay SWF abierto');
  return fs.readFileSync(document.workingPath);
}

function tagOf(tags, args) {
  let wanted = null;
  if (Array.isArray(args.tag_path)) wanted = args.tag_path;
  else if (Number.isInteger(args.tag_index)) wanted = [args.tag_index];
  else if (typeof args.id === 'string' && /^tag:\d+(\/\d+)*$/.test(args.id)) wanted = args.id.slice(4).split('/').map(Number);
  if (wanted === null) throw new HarnessError(KINDS.BAD_REQUEST, 'Indique tag_index, tag_path o id');
  const tag = tags.find((t) => t.path.length === wanted.length && t.path.every((v, i) => v === wanted[i]));
  if (!tag) throw new HarnessError(KINDS.NOT_FOUND, 'Recurso desconocido');
  return tag;
}

function header(document) {
  const bytes = workingBytes(document);
  const sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
  const base = parseHeader(bytes);
  const result = {
    filename: document.sourcePath ? path.basename(document.sourcePath) : null,
    ...base,
    shown_frames: null,
    tag_count: null,
    abc_blocks: null,
    has_as3: null,
    has_metadata: null,
    file_attributes: null,
    metadata: null,
    background_color: null,
    script_limits: null,
    sha256,
    revision: sha256,
    derived: true,
    diagnostics: [],
  };
  if (base.partial) {
    result.diagnostics.push({ code: 'header_partial', message: base.partial, offset: null });
    delete result.partial;
    return result;
  }
  return { ...result, ...headerTags(scanTags(bytes)) };
}

/** An integer argument within bounds, or its default. */
function intArgument(args, name, fallback, minimum, maximum) {
  const value = args[name] === undefined ? fallback : args[name];
  if (!Number.isInteger(value) || value < minimum || value > maximum) throw new HarnessError(KINDS.BAD_REQUEST, `Argumento inválido: ${name}`);
  return value;
}

/** One page of a tag's payload, or of the whole working file with `scope: "file"`. */
function raw(document, args) {
  const offset = intArgument(args, 'offset', 0, 0, 2 ** 40);
  const limit = intArgument(args, 'limit', DEFAULT_RAW_PAGE, 1, MAX_RAW_PAGE);
  const encoding = args.encoding === undefined ? 'hex' : args.encoding;
  if (encoding !== 'hex' && encoding !== 'base64') throw new HarnessError(KINDS.BAD_REQUEST, 'Codificación desconocida: usa hex o base64');
  const bytes = workingBytes(document);
  let payload;
  let meta;
  if (args.scope === 'file') {
    payload = bytes;
    meta = { scope: 'file', name: 'Archivo SWF' };
  } else {
    const scan = scanTags(bytes);
    const tag = tagOf(scan.tags, args);
    payload = scan.data.subarray(tag.dataStart, tag.dataStart + tag.length);
    meta = { scope: 'tag', tag_index: tag.path[0], name: tag.name, type: tag.code, ...(tag.path.length > 1 ? { tag_path: tag.path } : {}) };
  }
  if (offset > payload.length) throw new HarnessError(KINDS.BAD_REQUEST, 'Desplazamiento fuera del contenido');
  const page = payload.subarray(offset, offset + limit);
  return {
    ...meta,
    offset,
    limit,
    encoding,
    total: payload.length,
    length: page.length,
    [encoding]: Buffer.from(page).toString(encoding),
    revision: document.sha256,
    derived: true,
  };
}

/** Run a built-in operation on the session's working copy. */
function run(op, args, document) {
  if (op === 'header') return header(document);
  if (op === 'raw') return raw(document, args);
  throw new HarnessError(KINDS.UNSUPPORTED, `El arnés no implementa ${op}`);
}

module.exports = { BUILTIN, run, header, raw, tagOf };
