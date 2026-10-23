'use strict';
/**
 * Adapters turn each worker's own vocabulary into the protocol's. These tests feed
 * them the answers the real GoSWF/AsmSWF/JsSWF/PySWF workers give (recorded from the
 * real executables) and check the protocol shapes come out, without any process.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { legacy, headerFromInfo, documentInfo } = require('../harness/adapters/legacy');
const { suite } = require('../harness/adapters/suite');
const { v2 } = require('../harness/adapters/v2');
const { scriptItem, decompileResult, diagnostics, language, category, fetchAll } = require('../harness/adapters/normalize');
const { Protocol } = require('../harness/protocol');
const path = require('node:path');

const protocol = Protocol.load([path.resolve(__dirname, '..', '..')]);

/** A context whose `rpc` answers from a table and remembers what it was asked. */
function context(answers, extra = {}) {
  const calls = [];
  const rpc = async (op, args = {}) => {
    calls.push([op, args]);
    const answer = answers[op];
    if (answer === undefined) throw Object.assign(new Error(`no answer for ${op}`), { kind: 'unsupported' });
    return typeof answer === 'function' ? answer(args) : answer;
  };
  const info = {
    id: 'go',
    name: 'GoSWF',
    languages: ['as2', 'as3'],
    worker: { ops: new Set(['open', 'header']), nativeOps: new Map([['info', {}]]), derivedOps: new Set(['tag_tree']) },
    ...extra.info,
  };
  return { calls, ctx: { rpc, info, protocol, document: extra.document } };
}

// What `goswf.exe worker` really answers (recorded from dev-as3-classes-namespaces).
const GO_INFO = {
  abc_count: 1, compression: 'FWS', diagnostic_count: 0, file_length: 1242, frame_count: 1, frame_rate: 24,
  normalized_sha256: 'ab86', path: 'C:\\x\\release.fws.swf', script_count: 11, sha256: 'ab86', stage_height_twips: 7500,
  stage_width_twips: 10000, symbol_count: 1, tag_count: 10, version: 38,
};

test('legacy open: GoSWF/AsmSWF info becomes the protocol document info', async () => {
  const { ctx, calls } = context({ open: { info: GO_INFO, diagnostics: null } });
  const result = await legacy.invoke('open', { path: 'D:\\juegos\\Main.swf' }, ctx);
  assert.deepEqual(calls, [['open', { path: 'D:\\juegos\\Main.swf' }]]);
  assert.deepEqual(result.info, {
    filename: 'Main.swf', sha256: 'ab86', signature: 'FWS', version: 38, width: 500, height: 375, frame_rate: 24, frame_count: 1, tag_count: 10,
  });
  assert.deepEqual(result.diagnostics, []);
  assert.equal(result.script_count, 11);
});

test('legacy open: JsSWF already answers in the protocol shape and keeps it', async () => {
  const js = { info: { filename: 'w.swf', sha256: 'x', signature: 'CWS', version: 9, width: 100, height: 50, frame_rate: 12, frame_count: 3, tag_count: 4 }, diagnostics: [], script_count: 2 };
  const { ctx } = context({ open: js });
  const result = await legacy.invoke('open', { path: 'w.swf' }, ctx);
  assert.deepEqual(result.info, js.info);
  assert.equal(result.script_count, 2);
});

test('legacy header is derived from info, says so, and reports what it cannot know', async () => {
  const { ctx } = context({ info: GO_INFO });
  const header = await legacy.invoke('header', {}, ctx);
  assert.equal(header.signature, 'FWS');
  assert.equal(header.compression, 'none');
  assert.deepEqual(header.frame_size, { xmin: 0, xmax: 10000, ymin: 0, ymax: 7500, width: 500, height: 375 });
  assert.equal(header.file_length, 1242);
  assert.equal(header.derived, true);
  assert.equal(header.file_attributes, null);
  assert.deepEqual([header.shown_frames, header.abc_blocks, header.has_as3, header.has_metadata, header.metadata], [null, null, null, null, null]);
  assert.deepEqual([header.tag_count, header.sha256, header.revision], [10, 'ab86', 'ab86']);
  assert.equal(header.diagnostics[0].code, 'header_partial');
  assert.equal(headerFromInfo({ info: { signature: 'ZWS', width: 1, height: 2 } }).compression, 'lzma');
  assert.equal(documentInfo({ compression: 'zlib' }).signature, 'CWS');
  assert.equal(headerFromInfo({}).frame_size, null);
});

test('legacy scripts: Go and Asm items get a name, a language, a kind and diagnostics like everybody else', async () => {
  const items = [{
    id: 'tag:6/method:0', tag_index: 6, tag_path: [6], tag_code: 82, language: 'avm2', kind: 'abc_method',
    display_name: 'frame1 :: Main/class_init', byte_start: 1014, byte_len: 3, sha256: 'a96', parameters: [], diagnostics: null,
  }];
  const { ctx, calls } = context({ scripts: { total: 11, offset: 0, limit: 2, items } });
  const page = await legacy.invoke('scripts', { query: 'm', offset: 0, limit: 2, engine: 'go', unit_id: 'ignored' }, ctx);
  assert.deepEqual(calls[0], ['scripts', { query: 'm', offset: 0, limit: 2 }], 'only the arguments the worker knows');
  assert.equal(page.total, 11);
  const [unit] = page.items;
  assert.deepEqual(
    [unit.id, unit.name, unit.language, unit.engine_language, unit.kind, unit.byte_len, unit.sha256, unit.diagnostics],
    ['tag:6/method:0', 'frame1 :: Main/class_init', 'as3', 'avm2', 'method', 3, 'a96', []],
  );
});

test('legacy disassemble and decompile: unit_id becomes id, results are completed', async () => {
  const { ctx, calls } = context({
    disassemble: { unit_id: 'tag:6/method:5', language: 'avm2', state: 'decoded', offset: 0, limit: 200, pcode_total: 348, pcode: '0000: getlocal_0', diagnostics: null },
    decompile: { unit_id: 'tag:6/method:5', language: 'avm2', state: 'structured', source: 'function () {}', pcode: '0000: x', diagnostics: [] },
  });
  const listing = await legacy.invoke('disassemble', { unit_id: 'tag:6/method:5', offset: 0, limit: 200 }, ctx);
  assert.deepEqual(calls[0], ['disassemble', { id: 'tag:6/method:5', offset: 0, limit: 200 }]);
  assert.deepEqual([listing.state, listing.pcode_total, listing.diagnostics], ['decoded', 348, []]);
  const source = await legacy.invoke('decompile', { unit_id: 'tag:6/method:5' }, ctx);
  assert.deepEqual(calls[1], ['decompile', { id: 'tag:6/method:5' }]);
  assert.deepEqual([source.engine, source.state, source.source], ['go', 'structured', 'function () {}']);
});

test('legacy engines refuse class units without bothering the worker', async () => {
  const { ctx, calls } = context({});
  for (const op of ['decompile', 'disassemble']) {
    await assert.rejects(legacy.invoke(op, { unit_id: 'tag:6/class:0' }, ctx), (e) => e.kind === 'unsupported' && /GoSWF/.test(e.message));
  }
  assert.deepEqual(calls, []);
});

test('legacy capabilities: the engine\'s own when it has them, the manifest\'s when it does not', async () => {
  const own = context({ capabilities: { engine: 'js', version: '0.3.0' } });
  assert.equal((await legacy.invoke('capabilities', {}, own.ctx)).version, '0.3.0');
  const bare = context({});
  const caps = await legacy.invoke('capabilities', {}, bare.ctx);
  assert.deepEqual([caps.version, caps.adapted, caps.protocol.framings, caps.ops], [null, true, ['legacy'], ['open', 'header']]);
  assert.deepEqual(caps.native_ops, ['info']);
});

test('legacy native calls pass through untouched', async () => {
  const { ctx, calls } = context({ info: GO_INFO });
  assert.equal(await legacy.invoke('info', { a: 1 }, ctx), GO_INFO);
  assert.deepEqual(calls, [['info', { a: 1 }]]);
});

test('v2 workers are passed through; scripts are only normalized', async () => {
  const { ctx } = context({
    header: { signature: 'FWS' },
    scripts: { total: 1, offset: 0, items: [{ id: 'tag:1/actions', name: 'DoAction', language: 'avm1', kind: 'frame', tag_path: [1] }] },
  });
  assert.deepEqual(await v2.invoke('header', {}, ctx), { signature: 'FWS' });
  const scripts = await v2.invoke('scripts', {}, ctx);
  assert.deepEqual([scripts.items[0].language, scripts.items[0].engine_language], ['as2', 'avm1']);
});

test('normalizers: language names, diagnostics, categories, paging', async () => {
  assert.deepEqual(['avm1', 'avm2', 'as1', 'as2', 'as3', 'zzz'].map(language), ['as2', 'as3', 'as2', 'as2', 'as3', 'zzz']);
  assert.deepEqual(diagnostics(['uno', { code: 'X', message: 'dos', offset: 3 }, null].filter(Boolean)), [{ code: 'ENGINE', message: 'uno', offset: null }, { code: 'X', message: 'dos', offset: 3 }]);
  assert.deepEqual(diagnostics(undefined), []);
  assert.deepEqual([2, 14, 7, 87, 76, 12, 999].map(category), ['shapes', 'sounds', 'buttons', 'binary', 'others', 'scripts', 'others']);
  const item = scriptItem({ document_id: 'tag:2/actions', tag_path: [2], language: 'as2', kind: 'frame', name: 'DoAction', bytecode_bytes: 9, bytecode_sha256: 'h', diagnostic: 'raro' });
  assert.deepEqual([item.id, item.byte_len, item.sha256, item.diagnostics], ['tag:2/actions', 9, 'h', [{ code: 'ENGINE', message: 'raro', offset: null }]]);
  assert.equal(decompileResult({ source: null, pcode: 'p' }, 'u', 'x').state, 'pcode_only');
  assert.equal(decompileResult({ source: 's' }, 'u', 'x').state, 'structured');
  assert.equal(decompileResult({ source: 's', state: 'partial' }, 'u', 'x').state, 'partial');
  const pages = [{ total: 3, items: [1, 2] }, { total: 3, items: [3] }];
  const fetched = await fetchAll(async (op, args) => pages[args.offset === 0 ? 0 : 1], 'x');
  assert.deepEqual(fetched, { total: 3, items: [1, 2, 3] });
  assert.deepEqual(await fetchAll(async () => ({ total: 0, items: [] }), 'x'), { total: 0, items: [] });
});

// ---- PySWF ----------------------------------------------------------------------------

const PY_STATE = {
  protocol_version: 1, filename: 'working-000.swf', working_path: 'C:\\tmp\\working.swf', source_path: 'C:\\x.swf', revision: 'abc',
  dirty: false, script_count: 11, history: { undo: false, redo: false }, signature: 'FWS', frame_count: 1,
  validation: { valid: true, errors: [], warnings: ['aviso'], signature: 'FWS', version: 38, tag_count: 10, frame_count: 1 },
};
const pyInfo = () => ({
  id: 'native', name: 'PySWF', languages: ['as2', 'as3'],
  worker: { ops: new Set(['open', 'resources']), nativeOps: new Map([['state', {}]]), derivedOps: new Set(['disassemble']) },
});

test('suite open: a SWF opens with open, a project with load_project, and the state travels along', async () => {
  const { ctx, calls } = context({ open: PY_STATE, load_project: PY_STATE }, { info: pyInfo() });
  const swf = await suite.invoke('open', { path: 'C:\\w\\game.swf' }, ctx);
  assert.deepEqual(calls[0], ['open', { path: 'C:\\w\\game.swf' }]);
  await suite.invoke('open', { path: 'C:\\w\\game.PYSWFPROJ' }, ctx);
  assert.equal(calls[1][0], 'load_project');
  assert.deepEqual([swf.info.filename, swf.info.sha256, swf.info.signature, swf.info.version, swf.info.tag_count, swf.info.frame_count], ['working-000.swf', 'abc', 'FWS', 38, 10, 1]);
  assert.equal(swf.info.width, null, 'the working file is not readable here, so no stage size');
  assert.deepEqual(swf.diagnostics, [{ code: 'ENGINE', message: 'aviso', offset: null }]);
  assert.equal(swf.revision, 'abc', 'the PySWF state rides along: the session follows it');
  assert.equal(swf.working_path, 'C:\\tmp\\working.swf');
  assert.equal(swf.engine, 'native');
});

test('suite resources: PySWF rows become the protocol rows, filtered by code the way RustSWF filters', async () => {
  const rows = [
    { id: 'tag:0', index: 0, tag_index: 0, name: 'FileAttributes', type: 69, formats: [], bytes: 4, character_id: null },
    { id: 'tag:1', index: 1, tag_index: 1, name: 'DefineShape', type: 2, formats: ['svg'], bytes: 20, character_id: 1 },
    { id: 'tag:2', index: 2, tag_index: 2, name: 'DefineBitsLossless', type: 20, formats: ['png'], bytes: 90, character_id: 2 },
  ];
  const { ctx, calls } = context({ resources: (a) => ({ total: rows.length, offset: a.offset, items: rows.slice(a.offset, a.offset + a.limit) }) }, { info: pyInfo() });
  const all = await suite.invoke('resources', { query: 'x', offset: 1, limit: 1 }, ctx);
  assert.deepEqual(calls[0], ['resources', { query: 'x', offset: 1, limit: 1 }]);
  assert.deepEqual(all.items[0], {
    id: 'tag:1', tag_index: 1, tag_path: [1], code: 2, name: 'DefineShape', length: 20, character_id: 1, symbol: null,
    category: 'shapes', formats: ['raw', 'svg'], diagnostics: [],
  });
  const images = await suite.invoke('resources', { codes: [20, 21] }, ctx);
  assert.deepEqual(images.items.map((i) => i.id), ['tag:2']);
  const other = await suite.invoke('resources', { other: true }, ctx);
  assert.deepEqual(other.items.map((i) => i.id), ['tag:0']);
  assert.equal(other.total, 1);
});

test('suite resource_info and sprite_frames map to resource_info and timeline', async () => {
  const { ctx, calls } = context({
    resource_info: { kind: 'shape', parsed: { bounds: 1 }, revision: 'r', tag_index: 3, points: [] },
    timeline: { frame: 0, frame_count: 1, display_list: [], svg: '<svg/>', actionscript_executed: false },
  }, { info: pyInfo() });
  const info = await suite.invoke('resource_info', { tag_path: [3] }, ctx);
  assert.deepEqual(calls[0], ['resource_info', { tag_index: 3 }]);
  assert.deepEqual([info.id, info.tag_index, info.tag_path, info.kind], ['tag:3', 3, [3], 'shape']);
  assert.equal((await suite.invoke('resource_info', { id: 'tag:5' }, ctx)).tag_index, 5);
  await assert.rejects(suite.invoke('resource_info', { tag_path: [3, 1] }, ctx), (e) => e.kind === 'unsupported');
  await assert.rejects(suite.invoke('resource_info', {}, ctx), (e) => e.kind === 'bad_request');
  const frame = await suite.invoke('sprite_frames', { frame: 2 }, ctx);
  assert.deepEqual(calls.at(-1), ['timeline', { frame: 2 }]);
  assert.equal(frame.svg, '<svg/>');
  await assert.rejects(suite.invoke('sprite_frames', { id: 'tag:4', frame: 0 }, ctx), (e) => e.kind === 'unsupported');
});

test('suite scripts: document_id items become protocol units', async () => {
  const { ctx } = context({
    scripts: {
      total: 1, offset: 0,
      items: [{ document_id: 'tag:6/method:0', id: 'tag:6/method:0', tag_path: [6], language: 'as3', kind: 'method', name: 'Main.static_init', bytecode_bytes: 3, bytecode_sha256: 'a96', diagnostic: null }],
    },
  }, { info: pyInfo() });
  const page = await suite.invoke('scripts', { query: 'main' }, ctx);
  const [unit] = page.items;
  assert.deepEqual([unit.id, unit.name, unit.language, unit.byte_len, unit.sha256, unit.diagnostics], ['tag:6/method:0', 'Main.static_init', 'as3', 3, 'a96', []]);
});

test('suite decompile: methods go to PySWF\'s python engine (or JPEXS when asked), classes to code_documents', async () => {
  const doc = { document_id: 'tag:6/class:1', completeness: 'complete', source: 'package app {}', unit_ids: ['tag:6/method:4', 'tag:6/method:5'], editable: false, revision: 'r', diagnostics: ['Whole ABC exact reproduction must be verified'] };
  const { ctx, calls } = context({
    decompile: (a) => ({ engine: a.engine, source: a.engine === 'jpexs' ? 'jx' : 'this.x = 1;', pcode: `p ${a.unit_id}`, diagnostics: ['nota'], editable: true, revision: 'r', unit_id: a.unit_id }),
    code_documents: doc,
  }, { info: pyInfo() });
  const python = await suite.invoke('decompile', { unit_id: 'tag:6/method:5' }, ctx);
  assert.deepEqual(calls[0], ['decompile', { unit_id: 'tag:6/method:5', engine: 'python' }]);
  assert.deepEqual([python.engine, python.state, python.source, python.diagnostics], ['native', 'structured', 'this.x = 1;', [{ code: 'ENGINE', message: 'nota', offset: null }]]);
  const jpexs = await suite.invoke('decompile', { unit_id: 'tag:6/method:5', engine: 'jpexs' }, ctx);
  assert.deepEqual([calls[1][1].engine, jpexs.engine, jpexs.source], ['jpexs', 'jpexs', 'jx']);
  const klass = await suite.invoke('decompile', { unit_id: 'tag:6/class:1' }, ctx);
  assert.deepEqual(calls[2], ['code_documents', { document_id: 'tag:6/class:1', engine: 'native' }]);
  assert.deepEqual([klass.scope, klass.state, klass.source, klass.engine], ['class', 'structured', 'package app {}', 'native']);
  assert.match(klass.pcode, /; ---- tag:6\/method:4 \(tag:6\/method:4\) ----\np tag:6\/method:4\n/);
  assert.match(klass.pcode, /tag:6\/method:5/);
  await assert.rejects(suite.invoke('decompile', { unit_id: 'tag:6/class:1', engine: 'jpexs' }, ctx), (e) => e.kind === 'unsupported');
  const partial = context({ code_documents: { ...doc, completeness: 'partial' }, decompile: { pcode: 'p', diagnostics: [] } }, { info: pyInfo() });
  assert.equal((await suite.invoke('decompile', { unit_id: 'tag:6/class:1' }, partial.ctx)).state, 'partial');
  const none = context({ code_documents: { ...doc, source: '' }, decompile: { pcode: 'p' } }, { info: pyInfo() });
  assert.equal((await suite.invoke('decompile', { unit_id: 'tag:6/class:1' }, none.ctx)).state, 'pcode_only');
});

test('suite disassemble is derived from the P-code PySWF returns with its source, paged by offset', async () => {
  const { ctx } = context({
    decompile: { source: 'x', pcode: 'abcdefghij', diagnostics: [] },
    code_documents: { unit_ids: ['u1', 'u2'] },
  }, { info: pyInfo() });
  const method = await suite.invoke('disassemble', { unit_id: 'tag:1/method:0', offset: 3 }, ctx);
  assert.deepEqual([method.pcode, method.pcode_total, method.offset, method.derived, method.state], ['defghij', 10, 3, true, 'decoded']);
  const klass = await suite.invoke('disassemble', { unit_id: 'tag:1/class:0' }, ctx);
  assert.equal(klass.pcode, '; ---- u1 (u1) ----\nabcdefghij\n; ---- u2 (u2) ----\nabcdefghij\n');
});

test('suite editing operations map to PySWF\'s calls with the revision they name', async () => {
  const { ctx, calls } = context({}, { info: pyInfo() });
  const answer = { ok: true };
  const rpcAll = { edit: answer, compile_project: answer, compile_saved: answer, resource_edit: answer, undo: answer, redo: answer, save_project: answer, export_swf: answer, export: answer };
  const bound = context(rpcAll, { info: pyInfo() });
  void ctx; void calls;
  const { ctx: c, calls: k } = bound;
  await suite.invoke('assemble', { unit_id: 'u', pcode: 'p', base_revision: 'R' }, c);
  await suite.invoke('compile', { unit_id: 'u', source: 's', base_revision: 'R', engine: 'jpexs', source_document: 'd' }, c);
  await suite.invoke('compile', { unit_id: 'u', source: 's', base_revision: 'R', engine: 'rust' }, c);
  await suite.invoke('compile', { entry: 'Main.as', profile: 'auto-as3', output: 'o.swf', main_class: 'Main', debug: true, base_revision: 'R', ignored: 1 }, c);
  await suite.invoke('compile', { output: 'o.swf', profile: 'native-as3' }, c);
  await suite.invoke('replace_resource', { tag_index: 4, operation: 'text', text: 'hi', base_revision: 'R' }, c);
  await suite.invoke('undo', { junk: 1 }, c);
  await suite.invoke('redo', {}, c);
  await suite.invoke('save', { path: 'a.swf' }, c);
  await suite.invoke('save', { path: 'a.pyswfproj', format: 'project' }, c);
  for (const op of ['export_fla', 'export_xfl', 'export_xml']) await suite.invoke(op, { path: 'out' }, c);
  assert.deepEqual(k, [
    ['edit', { base_revision: 'R', unit_id: 'u', code: 'p', mode: 'pcode', engine: 'python' }],
    ['edit', { base_revision: 'R', unit_id: 'u', code: 's', mode: 'source', engine: 'jpexs', source_document: 'd' }],
    ['edit', { base_revision: 'R', unit_id: 'u', code: 's', mode: 'source', engine: 'python' }],
    ['compile_project', { profile: 'auto-as3', entry: 'Main.as', output: 'o.swf', main_class: 'Main', debug: true, base_revision: 'R' }],
    ['compile_saved', { output: 'o.swf', profile: 'native-as3' }],
    ['resource_edit', { tag_index: 4, operation: 'text', text: 'hi', base_revision: 'R' }],
    ['undo', {}],
    ['redo', {}],
    ['export_swf', { path: 'a.swf' }],
    ['save_project', { path: 'a.pyswfproj' }],
    ['export', { format: 'fla', path: 'out' }],
    ['export', { format: 'xfl', path: 'out' }],
    ['export', { format: 'xml', path: 'out' }],
  ]);
});

test('suite capabilities come from ping and the manifest; other calls pass through verbatim', async () => {
  const { ctx, calls } = context({ ping: { version: '0.3.0' }, state: PY_STATE, engines: { engines: [] } }, { info: pyInfo() });
  const caps = await suite.invoke('capabilities', {}, ctx);
  assert.deepEqual([caps.version, caps.derived_ops, caps.protocol.framings, caps.native_ops], ['0.3.0', ['disassemble'], ['suite'], ['state']]);
  assert.equal(await suite.invoke('state', {}, ctx), PY_STATE);
  assert.deepEqual((await suite.invoke('engines', { a: 1 }, ctx)).engines, []);
  assert.deepEqual(calls.at(-1), ['engines', { a: 1 }]);
});

test('suite header, tag_tree, raw and resource_preview are PySWF\'s own answers, passed through untouched', async () => {
  const tree = { nodes: [{ id: 'header', label: 'Cabecera', kind: 'header' }], node_count: 1, truncated: false, limit: 20000, revision: 'r', filename: 'x.swf' };
  const header = { filename: 'x.swf', signature: 'FWS', frame_size: { xmin: 0, xmax: 1, ymin: 0, ymax: 1, width: 0.05, height: 0.05 }, revision: 'r' };
  const page = { scope: 'tag', name: 'DoABC', tag_index: 6, type: 82, offset: 0, limit: 16, total: 9, length: 9, encoding: 'hex', hex: '00', revision: 'r' };
  const preview = { kind: 'shape', mime: 'image/svg+xml', encoding: 'text', data: '<svg/>', tag_index: 3, name: 'DefineShape', revision: 'r' };
  const { ctx, calls } = context({ tag_tree: tree, header, raw: page, resource_preview: preview }, { info: pyInfo() });
  assert.equal(await suite.invoke('tag_tree', { limit: 50, junk: 1 }, ctx), tree);
  assert.equal(await suite.invoke('header', { junk: 1 }, ctx), header);
  assert.deepEqual(calls.map(([op, args]) => [op, args]), [['tag_tree', { limit: 50 }], ['header', {}]]);

  // raw: a tag by index, path or id, or the file; the paging arguments travel, nothing else does.
  assert.equal(await suite.invoke('raw', { tag_index: 6, offset: 1, limit: 16, encoding: 'base64', junk: 1 }, ctx), page);
  assert.deepEqual(calls.at(-1), ['raw', { tag_index: 6, offset: 1, limit: 16, encoding: 'base64' }]);
  await suite.invoke('raw', { tag_path: [3, 1] }, ctx);
  assert.deepEqual(calls.at(-1), ['raw', { tag_path: [3, 1] }]);
  await suite.invoke('raw', { id: 'tag:7' }, ctx);
  assert.deepEqual(calls.at(-1), ['raw', { tag_index: 7 }]);
  await suite.invoke('raw', { id: 'tag:2/0', limit: 4 }, ctx);
  assert.deepEqual(calls.at(-1), ['raw', { tag_path: [2, 0], limit: 4 }]);
  await suite.invoke('raw', { scope: 'file', offset: 8, limit: 8, tag_index: 3 }, ctx);
  assert.deepEqual(calls.at(-1), ['raw', { scope: 'file', offset: 8, limit: 8 }], 'the file needs no tag');
  await assert.rejects(suite.invoke('raw', {}, ctx), (e) => e.kind === 'bad_request');

  // resource_preview: PySWF draws top-level characters; id, tag_path and tag_index all name one.
  assert.equal(await suite.invoke('resource_preview', { tag_index: 3, frame: 2, ratio: 100, junk: 1 }, ctx), preview);
  assert.deepEqual(calls.at(-1), ['resource_preview', { tag_index: 3, frame: 2, ratio: 100 }]);
  await suite.invoke('resource_preview', { id: 'tag:5' }, ctx);
  assert.deepEqual(calls.at(-1), ['resource_preview', { tag_index: 5 }]);
  await suite.invoke('resource_preview', { tag_path: [4] }, ctx);
  assert.deepEqual(calls.at(-1), ['resource_preview', { tag_index: 4 }]);
  await assert.rejects(suite.invoke('resource_preview', { tag_path: [3, 1] }, ctx), (e) => e.kind === 'unsupported');
  await assert.rejects(suite.invoke('resource_preview', {}, ctx), (e) => e.kind === 'bad_request');
});
