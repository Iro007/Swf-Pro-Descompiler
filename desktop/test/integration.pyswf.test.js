'use strict';
/**
 * The harness with the real PySWF universal worker (and RustSWF beside it): the
 * editing flow end to end, the document following PySWF's commits, class sources
 * from PySWF's native decompiler, and its history surviving a restart. Skips,
 * saying why, when the Python environment or rustswf.exe is not on this machine.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { realRegistry, realHarness, missing, PARITY, corpus } = require('../testkit/real');
const { waitUntil, isAlive } = require('../testkit');

const registry = realRegistry();
const skipBoth = missing(registry, 'rust', 'native');
const skipNative = missing(registry, 'native');

const NAMESPACES = PARITY('dev-as3-classes-namespaces');
const sha = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const EDITED = 'this.total = (this.total + local1) + 1;\nreturn this.total;';

async function opened(t, file = NAMESPACES, options) {
  const harness = realHarness(options);
  t.after(() => harness.close());
  const reply = await harness.call('open', { path: file });
  return { harness, reply };
}

test('an edit made through PySWF becomes the next revision and RustSWF reads the edited bytes', { skip: skipBoth, timeout: 120000 }, async (t) => {
  const { harness, reply } = await opened(t);
  assert.equal(reply.served_by, 'rust');
  const original = reply.sha256;
  assert.equal(original, sha(fs.readFileSync(NAMESPACES)));

  const before = await harness.call('disassemble', { unit_id: 'tag:6/method:5', engine: 'rust' });
  assert.doesNotMatch(before.result.pcode, /pushbyte 1/);

  const edit = await harness.call('compile', { unit_id: 'tag:6/method:5', source: EDITED });
  assert.equal(edit.served_by, 'native');
  assert.equal(edit.changed, true);
  assert.equal(edit.revision, 1);
  assert.notEqual(edit.sha256, original);
  assert.equal(edit.result.revision, edit.sha256, 'PySWF and the harness name the same bytes by the same hash');
  assert.equal(edit.result.history.undo, true);
  assert.equal(sha(fs.readFileSync(harness.session.workingPath)), edit.sha256);

  const after = await harness.call('disassemble', { unit_id: 'tag:6/method:5', engine: 'rust' });
  assert.equal(after.revision, 1);
  assert.match(after.result.pcode, /pushbyte 1/, 'RustSWF was re-opened on the edited movie');

  const undo = await harness.call('undo', {});
  assert.deepEqual([undo.revision, undo.sha256, undo.result.history.redo], [2, original, true]);
  assert.doesNotMatch((await harness.call('disassemble', { unit_id: 'tag:6/method:5', engine: 'rust' })).result.pcode, /pushbyte 1/);
  const redo = await harness.call('redo', {});
  assert.equal(redo.sha256, edit.sha256);
  assert.equal(redo.revision, 3);
});

test('PySWF refuses an edit made against an old revision', { skip: skipNative, timeout: 60000 }, async (t) => {
  const { harness } = await opened(t);
  await assert.rejects(
    harness.call('compile', { unit_id: 'tag:6/method:5', source: EDITED, base_revision: '0'.repeat(64) }),
    (e) => e.kind === 'failed' && /documento cambió/i.test(e.message),
  );
  assert.equal(harness.session.revision, 0);
});

test('PySWF keeps its undo history across a restart and the harness adopts the recovered project', { skip: skipNative, timeout: 120000 }, async (t) => {
  const { harness } = await opened(t);
  const original = harness.session.sha256;
  const edit = await harness.call('compile', { unit_id: 'tag:6/method:5', source: EDITED });
  const pid = harness.engines.get('native').worker.pid;
  harness.cancel();
  assert.ok(await waitUntil(() => !isAlive(pid), { timeoutMs: 10000 }), 'the worker was killed');
  const undo = await harness.call('undo', {});
  assert.equal(undo.sha256, original, 'the history survived the restart: undo returned to the original bytes');
  assert.equal(undo.revision, 2);
  assert.notEqual(harness.engines.get('native').worker.pid, pid);
  const redo = await harness.call('redo', {});
  assert.equal(redo.sha256, edit.sha256);
});

test('a whole class from PySWF\'s native decompiler wins the vote when RustSWF only has P-code', { skip: skipBoth, timeout: 120000 }, async (t) => {
  const { harness } = await opened(t);
  const reply = await harness.call('decompile', { unit_id: 'tag:6/class:1' });
  const { selection } = reply.result;
  assert.equal(reply.served_by, 'native');
  assert.match(reply.result.source, /package app\s*\{[\s\S]*public class Model/);
  assert.equal(reply.result.scope, 'class');
  assert.equal(selection.score.value, 100);
  const byEngine = Object.fromEntries(selection.candidates.map((c) => [c.engine, c]));
  assert.equal(byEngine.rust.score, null, 'RustSWF produced no class source for this sample');
  assert.equal(byEngine.native.score, 100);
  assert.equal(byEngine.js, undefined, 'JsSWF is not asked about classes');
  assert.match(reply.result.pcode, /; ---- tag:6\/method:4/);
});

const flat = (nodes, out = []) => {
  for (const node of nodes) {
    out.push(node);
    if (node.children) flat(node.children, out);
  }
  return out;
};

test('PySWF answers every read by itself: resources, scripts, header, raw, the tag tree and the frame renderer', { skip: skipNative, timeout: 120000 }, async (t) => {
  const { harness } = await opened(t);
  const resources = await harness.call('resources', { engine: 'native', strict: true, limit: 50 });
  assert.equal(resources.served_by, 'native');
  assert.equal(resources.result.total, 10);
  assert.deepEqual([resources.result.items[0].id, resources.result.items[0].name, resources.result.items[0].tag_path], ['tag:0', 'FileAttributes', [0]]);
  const scripts = await harness.call('scripts', { engine: 'native', strict: true });
  assert.equal(scripts.result.total, 11);
  assert.deepEqual(scripts.result.items.slice(0, 2).map((u) => [u.id, u.language]), [['tag:6/method:0', 'as3'], ['tag:6/method:1', 'as3']]);
  const info = await harness.call('resource_info', { engine: 'native', strict: true, tag_index: 0 });
  assert.deepEqual([info.result.kind, info.result.tag_path], ['raw', [0]]);
  const frame = await harness.call('sprite_frames', { frame: 0 });
  assert.equal(frame.served_by, 'native');
  assert.match(frame.result.svg, /^<svg/);

  // The header, in the shape the single-window interface reads.
  const header = await harness.call('header', { engine: 'native', strict: true });
  assert.equal(header.served_by, 'native');
  assert.deepEqual([header.result.filename, header.result.signature, header.result.compression, header.result.version], ['release.fws.swf', 'FWS', 'none', 38]);
  assert.deepEqual(header.result.frame_size, { xmin: 0, xmax: 10000, ymin: 0, ymax: 7500, width: 500, height: 375 });
  assert.deepEqual([header.result.tag_count, header.result.shown_frames, header.result.abc_blocks, header.result.script_count], [10, 1, 1, 11]);
  assert.deepEqual([header.result.has_as3, header.result.has_metadata, header.result.file_attributes.actionscript3], [true, true, true]);
  assert.deepEqual([header.result.sha256, header.result.revision], [harness.session.sha256, harness.session.sha256]);

  // The tree: ids the interface keeps its expansion state by, class members it composes sources from.
  const tree = await harness.call('tag_tree', { engine: 'native', strict: true });
  assert.equal(tree.served_by, 'native');
  assert.deepEqual(tree.result.nodes.map((n) => n.id), ['header', 'folder:frames', 'folder:others', 'folder:scripts']);
  assert.deepEqual([tree.result.filename, tree.result.revision, tree.result.truncated, tree.result.limit], ['release.fws.swf', harness.session.sha256, false, 20000]);
  const everything = flat(tree.result.nodes);
  assert.equal(tree.result.node_count, everything.length);
  assert.equal(new Set(everything.map((n) => n.id)).size, everything.length, 'node ids are unique');
  const model = everything.find((n) => n.kind === 'class' && n.label === 'Model');
  assert.deepEqual([model.document_id, model.detail, model.language, model.tag_index], ['tag:6/class:1', 'app', 'as3', 6]);
  assert.ok(model.members.some((m) => m.name === 'add' && m.role === 'method' && m.unit_id === 'tag:6/method:5'));
  const listed = everything.filter((n) => n.kind === 'class').flatMap((n) => n.members.map((m) => m.unit_id)).concat(everything.filter((n) => n.kind === 'script').map((n) => n.unit_id));
  assert.deepEqual([...listed].sort(), scripts.result.items.map((u) => u.id).sort(), 'every code unit appears exactly once');

  // Bytes: a tag, the file, and the paging arguments.
  const raw = await harness.call('raw', { engine: 'native', strict: true, tag_index: 7, limit: 16 });
  assert.deepEqual([raw.result.scope, raw.result.name, raw.result.type, raw.result.hex], ['tag', 'SymbolClass', 76, '010000004d61696e00']);
  const file = await harness.call('raw', { engine: 'native', strict: true, scope: 'file', offset: 0, limit: 8, encoding: 'base64' });
  assert.deepEqual([file.result.scope, file.result.total, Buffer.from(file.result.base64, 'base64').toString('latin1', 0, 3)], ['file', fs.statSync(NAMESPACES).size, 'FWS']);
  await assert.rejects(harness.call('raw', { engine: 'native', strict: true, tag_index: 7, limit: 0 }), (e) => e.kind === 'failed');

  const caps = await harness.call('capabilities', { engine: 'native' });
  assert.equal(caps.result.adapted, true);
  assert.ok(caps.result.version);
});

test('PySWF previews shapes, sprites and images without running the movie', { skip: skipNative, timeout: 120000 }, async (t) => {
  const { harness } = await opened(t, corpus('cloud-matched-micro-20261013/ruffle_avm1_closure_scope.swf'));
  const tree = await harness.call('tag_tree', {});
  assert.equal(tree.served_by, 'native');
  const tags = flat(tree.result.nodes).filter((n) => n.kind === 'tag' && n.tag_index !== undefined && !n.tag_path);
  const shape = tags.find((n) => [2, 22, 32, 83].includes(n.tag_type));
  const sprite = tags.find((n) => n.tag_type === 39);
  const preview = await harness.call('resource_preview', { tag_index: shape.tag_index });
  assert.equal(preview.served_by, 'native');
  assert.deepEqual([preview.result.kind, preview.result.mime, preview.result.encoding, preview.result.tag_index, preview.result.revision], ['shape', 'image/svg+xml', 'text', shape.tag_index, harness.session.sha256]);
  assert.match(preview.result.data, /^<svg/);
  const inSprite = await harness.call('resource_preview', { id: `tag:${sprite.tag_index}`, frame: 0 });
  assert.deepEqual([inSprite.result.kind, inSprite.result.frame], ['sprite', 0]);
  await assert.rejects(harness.call('resource_preview', { tag_index: 0 }), (e) => e.kind === 'failed');
  await assert.rejects(harness.call('resource_preview', {}), (e) => e.kind === 'bad_request');
  const images = await opened(t, corpus('parity-20261014/optimized/swf/validation-haxeflixel-game/release.fws.swf'));
  const photo = flat((await images.harness.call('tag_tree', {})).result.nodes).find((n) => n.kind === 'tag' && [6, 20, 21, 35, 36, 90].includes(n.tag_type));
  const image = await images.harness.call('resource_preview', { tag_index: photo.tag_index });
  assert.deepEqual([image.result.kind, image.result.encoding], ['image', 'base64']);
  assert.ok(['image/png', 'image/jpeg'].includes(image.result.mime));
  assert.ok(image.result.width > 0 && image.result.height > 0);
});

test('saving writes the current bytes; the project format round-trips through PySWF', { skip: skipNative, timeout: 120000 }, async (t) => {
  const { harness } = await opened(t);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'flash-save-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  await harness.call('compile', { unit_id: 'tag:6/method:5', source: EDITED });
  const swf = path.join(dir, 'edited.swf');
  const saved = await harness.call('save', { path: swf });
  assert.equal(saved.revision, 1);
  assert.equal(sha(fs.readFileSync(swf)), harness.session.sha256);
  const project = path.join(dir, 'game.pyswfproj');
  const stored = await harness.call('save', { path: project, format: 'project' });
  assert.ok(fs.statSync(project).size > 0);
  assert.equal(stored.revision, 1);
  const reopened = await harness.call('open', { path: project });
  assert.equal(reopened.served_by, 'native');
  assert.equal(reopened.sha256, saved.sha256, 'the project carries the edited movie');
  assert.equal(reopened.result.history.undo, true, 'and its undo history');
  assert.equal((await harness.call('disassemble', { unit_id: 'tag:6/method:5', engine: 'rust' })).result.pcode.includes('pushbyte 1'), true);
});

test('PySWF alone is enough to open, read and edit when RustSWF cannot start', { skip: skipNative, timeout: 120000 }, async (t) => {
  const harness = realHarness();
  t.after(() => harness.close());
  // Take RustSWF out of the picture as if it had not been built.
  harness.registry.engine('rust').available = false;
  harness.registry.engine('rust').reason = 'simulado: no está compilado';
  const reply = await harness.call('open', { path: NAMESPACES });
  assert.equal(reply.served_by, 'native');
  assert.deepEqual(reply.attempts.map((a) => [a.engine, a.kind]), [['rust', 'unavailable'], ['native', undefined]]);
  const scripts = await harness.call('scripts', {});
  assert.equal(scripts.served_by, 'native');
  const header = await harness.call('header', {});
  assert.equal(header.served_by, 'native', 'RustSWF is out: PySWF answers the header in the same shape');
  assert.equal(header.result.version, 38);
  const tree = await harness.call('tag_tree', {});
  assert.equal(tree.served_by, 'native');
  const raw = await harness.call('raw', { tag_index: 7, limit: 16 });
  assert.equal(raw.served_by, 'native');
  assert.match(raw.result.hex, /4d61696e/);
  const auto = await harness.call('decompile', { unit_id: 'tag:6/method:5' });
  assert.ok(['native', 'js', 'go', 'asm'].includes(auto.served_by));
  const edit = await harness.call('compile', { unit_id: 'tag:6/method:5', source: EDITED });
  assert.equal(edit.served_by, 'native');
  assert.equal(edit.revision, 1);
});
