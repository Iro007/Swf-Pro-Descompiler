'use strict';
/**
 * A small but complete cast of scripted engines for the routing tests: RustSWF
 * (v2), PySWF (suite framing, a miniature editor), JsSWF (legacy framing) and JPEXS
 * (served through PySWF). Each test overrides only what it is about.
 */
const OPEN_INFO = { filename: 'working-000.swf', sha256: 'x', signature: 'FWS', version: 9, width: 100, height: 50, frame_rate: 24, frame_count: 1, tag_count: 2 };
const UNIT = 'tag:1/method:0';
const CLEAN = 'function m0() {\n    return 1;\n}\n';

const RUST_OPS = ['capabilities', 'open', 'header', 'tag_tree', 'raw', 'resources', 'resource_info', 'extract_image', 'scripts', 'disassemble', 'decompile'];
const NATIVE_OPS = ['capabilities', 'open', 'header', 'tag_tree', 'raw', 'resources', 'resource_info', 'resource_preview', 'scripts', 'disassemble', 'decompile', 'assemble', 'compile', 'replace_resource', 'undo', 'redo', 'save'];
const JS_OPS = ['capabilities', 'open', 'header', 'scripts', 'disassemble', 'decompile'];

const unitsReply = (name = 'm0') => ({
  total: 1,
  offset: 0,
  items: [{ id: UNIT, name, language: 'avm2', kind: 'method', tag_path: [1] }],
});

/** A decompile spec: source text (null for P-code only) for the known unit, not_found for others. */
function decompileSpec(source, extra = {}) {
  return {
    byUnit: {
      [UNIT]: { result: { unit_id: UNIT, state: source === null ? 'pcode_only' : 'structured', source, pcode: 'getlocal_0', diagnostics: [], ...extra } },
      default: { error: { message: 'Unidad de script desconocida', kind: 'not_found' } },
    },
  };
}

function rust(config = {}, over = {}) {
  return {
    id: 'rust',
    order: 0,
    name: 'RustSWF',
    framing: 'v2',
    ops: RUST_OPS,
    native_ops: { info: {}, decompile_document: {} },
    config: {
      requireOpen: true,
      ops: {
        capabilities: { result: { protocol: { name: 'flash-engine-jsonl', version: 2 }, ops: RUST_OPS } },
        open: { result: { info: OPEN_INFO, diagnostics: [], script_count: 1 } },
        header: { result: { signature: 'FWS', frame_rate: 24, from: 'rust' } },
        scripts: { result: unitsReply('Rust m0') },
        decompile: decompileSpec(CLEAN),
        disassemble: { result: { unit_id: UNIT, state: 'decoded', pcode: '000000: getlocal_0', pcode_total: 18, offset: 0, diagnostics: [] } },
        info: { result: { info: { ...OPEN_INFO } } },
        decompile_document: { echo: true },
        ...config,
      },
    },
    ...over,
  };
}

function native(config = {}, over = {}) {
  return {
    id: 'native',
    order: 1,
    name: 'PySWF',
    framing: 'suite',
    ops: NATIVE_OPS,
    native_ops: {
      state: {},
      edit: { mutates: true },
      resource_edit: { mutates: true },
      compile_project: { timeout: 'compile', mutates: true },
      export_swf: {},
      ping: { document: false },
      engines: { document: false },
    },
    worker: { authority: true, env: { FLASH_SUITE_STATE_DIR: '{state_dir}' } },
    config: {
      pyswf: true,
      ops: {
        scripts: { result: { total: 1, offset: 0, items: [{ id: UNIT, document_id: UNIT, name: 'Py m0', language: 'as3', kind: 'method', tag_path: [1], bytecode_bytes: 3, bytecode_sha256: 'a' }] } },
        decompile: decompileSpec(CLEAN, { engine: 'python', editable: true }),
        resources: { result: { total: 1, offset: 0, items: [{ id: 'tag:0', index: 0, tag_index: 0, name: 'FileAttributes', type: 69, formats: [], bytes: 4, character_id: null }] } },
        resource_info: { result: { kind: 'raw', parsed: {}, revision: 'r', tag_index: 0 } },
        header: { result: { filename: 'sample.swf', signature: 'FWS', frame_rate: 24, revision: 'r', from: 'native' } },
        tag_tree: { result: { nodes: [{ id: 'header', label: 'Cabecera', kind: 'header' }], node_count: 1, truncated: false, limit: 20000, revision: 'r', filename: 'sample.swf', from: 'native' } },
        raw: { result: { scope: 'tag', name: 'FileAttributes', tag_index: 0, type: 69, offset: 0, limit: 4096, total: 4, length: 4, encoding: 'hex', hex: '08000000', revision: 'r', from: 'native' } },
        resource_preview: { result: { kind: 'shape', mime: 'image/svg+xml', encoding: 'text', data: '<svg/>', tag_index: 1, name: 'DefineShape', revision: 'r' } },
        engines: { result: { engines: [], default_engine: 'auto' } },
        ...config,
      },
    },
    ...over,
  };
}

function js(config = {}, over = {}) {
  return {
    id: 'js',
    order: 2,
    name: 'JsSWF',
    framing: 'legacy',
    ops: JS_OPS,
    native_ops: { info: {} },
    worker: { class_units: false },
    config: {
      requireOpen: true,
      ops: {
        open: { result: { info: OPEN_INFO, diagnostics: [], script_count: 1 } },
        info: { result: { info: OPEN_INFO, diagnostics: [], script_count: 1 } },
        scripts: { result: unitsReply('Js m0') },
        decompile: decompileSpec(CLEAN),
        disassemble: { result: { unit_id: UNIT, state: 'decoded', pcode: 'js pcode', pcode_total: 8, offset: 0, diagnostics: [] } },
        ...config,
      },
    },
    ...over,
  };
}

function jpexs(over = {}) {
  return { id: 'jpexs', order: 5, name: 'JPEXS', via: 'native', args: { engine: 'jpexs' }, ops: ['decompile', 'compile'], ...over };
}

/** Rust + PySWF + JsSWF + JPEXS with default behavior; `over` replaces an engine by id. */
function standardWorld(over = {}) {
  const engines = [rust(), native(), js(), jpexs()];
  return engines.map((engine) => over[engine.id] || engine);
}

module.exports = { UNIT, CLEAN, OPEN_INFO, RUST_OPS, NATIVE_OPS, JS_OPS, unitsReply, decompileSpec, rust, native, js, jpexs, standardWorld };
