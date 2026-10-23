'use strict';
/**
 * What the single-window interface (ui/src/shell) asks of its host, asked of the harness with the
 * real engines: the same operations with the same arguments, and the answers shaped like the
 * types in ui/src/types.ts. The interface calls `window.flash.call(op, args)` and reads `result`;
 * this test calls `harness.call` and reads the same. Skips, saying why, when RustSWF or
 * PySWF is not on this machine.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { realRegistry, realHarness, missing, PARITY } = require('../testkit/real');

const registry = realRegistry();
const skip = missing(registry, 'rust', 'native');
const NAMESPACES = PARITY('dev-as3-classes-namespaces');
const EDITED = 'this.total = (this.total + local1) + 1;\nreturn this.total;';

/** The fields of ui/src/types.ts that the interface reads, with their types. */
const SUITE_STATE = { filename: 'string', working_path: 'string', revision: 'string', dirty: 'boolean', script_count: 'number', history: 'object' };
const SUITE_DECOMPILE = { engine: 'string', pcode: 'string', diagnostics: 'object', revision: 'string', unit_id: 'string' };
const SUITE_TAG_TREE = { nodes: 'object', node_count: 'number', truncated: 'boolean', limit: 'number', revision: 'string', filename: 'string' };
const SUITE_HEADER = { filename: 'string', signature: 'string', compression: 'string', version: 'number', file_length: 'number', file_size: 'number', frame_size: 'object', frame_rate: 'number', frame_count: 'number', shown_frames: 'number', tag_count: 'number', abc_blocks: 'number', has_as3: 'boolean', has_metadata: 'boolean', sha256: 'string', revision: 'string' };
const SUITE_RAW = { scope: 'string', name: 'string', offset: 'number', limit: 'number', total: 'number', length: 'number', encoding: 'string', revision: 'string' };
const SUITE_ENGINE = { id: 'string', name: 'string', available: 'boolean', category: 'string', operations: 'object' };

function assertShape(value, spec, label) {
  assert.ok(value && typeof value === 'object', `${label} is an object`);
  for (const [key, type] of Object.entries(spec)) assert.equal(typeof value[key], type, `${label}.${key} is a ${type}`);
}

async function session(t, file = NAMESPACES, options) {
  const harness = realHarness(options);
  t.after(() => harness.close());
  const opened = await harness.call('open', { path: file });
  return { harness, opened };
}

test('opening a file: the answer is a suite state, and the state, tree, header and bytes follow it', { skip, timeout: 120000 }, async (t) => {
  const { harness, opened } = await session(t);
  assertShape(opened.result, SUITE_STATE, 'open');
  assert.equal(opened.result.revision, opened.sha256);
  assert.equal(opened.result.filename, 'release.fws.swf', 'the name of the file the person opened');
  assert.equal(opened.result.validation.tag_count, 10);

  const state = (await harness.call('state')).result;
  assertShape(state, SUITE_STATE, 'state');
  assert.deepEqual([state.revision, state.filename, state.dirty, state.history], [opened.sha256, 'release.fws.swf', false, { undo: false, redo: false }]);

  const tree = (await harness.call('tag_tree')).result;
  assertShape(tree, SUITE_TAG_TREE, 'tag_tree');
  assert.equal(tree.revision, state.revision);
  const header = (await harness.call('header')).result;
  assertShape(header, SUITE_HEADER, 'header');
  assert.equal(header.revision, state.revision);
  const raw = (await harness.call('raw', { scope: 'file', offset: 0, limit: 16, encoding: 'hex' })).result;
  assertShape(raw, SUITE_RAW, 'raw');
  assert.equal(raw.hex.slice(0, 6), '465753', 'FWS');
});

test('the catalogue the engine pickers read lists the engines the harness routes to', { skip, timeout: 120000 }, async (t) => {
  const { harness } = await session(t);
  const catalog = (await harness.call('engines')).result;
  assert.equal(typeof catalog.default_engine, 'string');
  for (const engine of catalog.engines) assertShape(engine, SUITE_ENGINE, `engine ${engine.id}`);
  const decompilers = catalog.engines.filter((engine) => engine.operations.includes('decompile') && engine.available).map((engine) => engine.id);
  for (const id of ['python', 'rust', 'jpexs']) assert.ok(decompilers.includes(id), `${id} is offered for decompile`);
  // Every id the picker can send, the harness understands: the ids of PySWF's catalogue are aliases of its own.
  for (const id of decompilers) {
    const reply = await harness.call('decompile', { unit_id: 'tag:6/method:5', engine: id });
    assert.ok(reply.ok, id);
    assert.equal(reply.result.unit_id, 'tag:6/method:5');
  }
});

test('decompile, edit and the history: the sequence of a person changing one method', { skip, timeout: 180000 }, async (t) => {
  const { harness } = await session(t);
  const first = (await harness.call('decompile', { unit_id: 'tag:6/method:5', engine: 'native' })).result;
  assertShape(first, SUITE_DECOMPILE, 'decompile');
  assert.deepEqual([first.engine, first.editable, typeof first.source], ['native', true, 'string']);
  assert.equal(first.revision, harness.session.sha256);

  // The interface sends back the engine that produced the source and the revision it was read at.
  const before = harness.session.sha256;
  const edited = await harness.call('edit', { unit_id: 'tag:6/method:5', code: EDITED, mode: 'source', base_revision: first.revision, engine: first.engine });
  assertShape(edited.result, SUITE_STATE, 'edit');
  assert.deepEqual([edited.changed, edited.revision, edited.result.dirty, edited.result.history.undo], [true, 1, true, true]);
  assert.notEqual(edited.result.revision, before);
  assert.equal(edited.result.revision, harness.session.sha256, 'the harness follows the revision PySWF names');
  assert.equal(edited.result.filename, 'release.fws.swf');

  // Everything that depends on the document moved with it.
  assert.equal((await harness.call('tag_tree')).result.revision, edited.result.revision);
  assert.equal((await harness.call('header')).result.revision, edited.result.revision);
  assert.match((await harness.call('decompile', { unit_id: 'tag:6/method:5', engine: 'native' })).result.source, /\(this\.total \+ local1\) \+ 1/, 'the edited method decompiles to the edit');
  // A draft made against the old revision is refused with a message, and the document stays put.
  await assert.rejects(
    harness.call('edit', { unit_id: 'tag:6/method:5', code: EDITED, mode: 'source', base_revision: before, engine: 'native' }),
    (e) => e.kind === 'failed' && /documento cambió/i.test(e.message),
  );
  assert.equal(harness.session.sha256, edited.result.revision);

  const validation = (await harness.call('validate')).result;
  assert.deepEqual([validation.valid, validation.errors], [true, []]);
  const undone = (await harness.call('undo')).result;
  assert.deepEqual([undone.revision, undone.dirty, undone.history], [before, false, { undo: false, redo: true }]);
  assert.equal((await harness.call('redo')).result.revision, edited.result.revision);

  // The comparison dialog.
  const compared = (await harness.call('compare', { unit_id: 'tag:6/method:5' })).result;
  assert.deepEqual(compared.results.map((r) => r.engine), ['python', 'rust', 'jpexs']);
});

test('saving: the SWF, then the project, which opens again with its history', { skip, timeout: 180000 }, async (t) => {
  const { harness } = await session(t);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'flash-ui-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  await harness.call('edit', { unit_id: 'tag:6/method:5', code: EDITED, mode: 'source', base_revision: harness.session.sha256, engine: 'native' });
  const swf = (await harness.call('export_swf', { path: path.join(dir, 'out.swf') })).result;
  assertShape(swf, { ...SUITE_STATE, path: 'string' }, 'export_swf');
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(swf.path)).digest('hex'), harness.session.sha256);
  const project = (await harness.call('save_project', { path: path.join(dir, 'game.pyswfproj') })).result;
  assert.equal(path.basename(project.path), 'game.pyswfproj');
  // The interface opens a project with `load_project`: a new session, with the edited movie and its history.
  const reopened = await harness.call('load_project', { path: project.path });
  assert.equal(reopened.op, 'load_project');
  assert.equal(reopened.served_by, 'native');
  assertShape(reopened.result, SUITE_STATE, 'load_project');
  assert.deepEqual([reopened.result.history.undo, reopened.result.dirty], [true, true]);
  assert.equal(reopened.result.revision, reopened.sha256);
  assert.equal(path.extname(harness.session.sourcePath), '.pyswfproj');
});

test('the rest of what the panels ask: resource info, previews, frames, the movie for Ruffle, sources, benchmarks', { skip, timeout: 180000 }, async (t) => {
  const { harness } = await session(t);
  const info = (await harness.call('resource_info', { tag_index: 0 })).result;
  assert.deepEqual([info.kind, typeof info.revision, info.tag_index], ['raw', 'string', 0]);
  const frame = (await harness.call('timeline', { frame: 0 })).result;
  assert.deepEqual([frame.frame, frame.actionscript_executed, typeof frame.svg], [0, false, 'string']);
  const movie = (await harness.call('preview_data')).result;
  assert.deepEqual([typeof movie.data, movie.sha256], ['string', harness.session.sha256]);
  assert.deepEqual((await harness.call('source_files', {})).result, { files: [] });
  const benchmarks = (await harness.call('bench_results', {})).result;
  assert.ok(Array.isArray(benchmarks.runs) && 'results' in benchmarks && typeof benchmarks.folder === 'string');
  const tools = (await harness.call('benchmark_tools', {})).result;
  assert.ok(tools.engines.length > 5 && tools.engines.every((engine) => typeof engine.id === 'string'));
  assert.equal((await harness.call('resource_edit', { operation: 'header', frame_rate: 30, base_revision: harness.session.sha256 })).result.dirty, true);
  assert.equal((await harness.call('header')).result.frame_rate, 30, 'the header edit shows in the header');
});

test('cancelling and asking for the state recovers the session, as the interface does', { skip, timeout: 120000 }, async (t) => {
  const { harness } = await session(t);
  const before = (await harness.call('state')).result;
  harness.cancel();
  const after = (await harness.call('state')).result;
  assert.equal(after.revision, before.revision);
  assert.equal(after.revision, harness.session.sha256);
  assertShape((await harness.call('tag_tree')).result, SUITE_TAG_TREE, 'tag_tree after cancel');
});
