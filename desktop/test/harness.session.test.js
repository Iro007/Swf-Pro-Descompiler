'use strict';
/**
 * The document as the harness keeps it: a working copy per revision that follows
 * what PySWF commits, engines that re-open on the new bytes, edits that wait for
 * reads, and workers that crash, hang or are cancelled without losing the document.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { RwGate, KEEP_REVISIONS } = require('../harness/session');
const { makeHarness, tempDir, removeDir, isAlive, waitUntil, defaultSwf, swfBytes, tagBytes } = require('../testkit');
const { rust, native, jpexs, standardWorld, unitsReply, UNIT, CLEAN } = require('../testkit/world');

const sha = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function opened(t, engines, options) {
  const w = makeHarness(engines, options);
  t.after(w.cleanup);
  await w.harness.call('open', { path: w.sample });
  return w;
}

test('RwGate: readers overlap, a writer is alone, and nobody jumps the queue', async () => {
  const gate = new RwGate();
  const log = [];
  const task = (name, ms) => async () => {
    log.push(`start ${name}`);
    await sleep(ms);
    log.push(`end ${name}`);
    return name;
  };
  const first = gate.read(task('r1', 60));
  const second = gate.read(task('r2', 60));
  await sleep(10);
  const writer = gate.write(task('w', 30));
  const late = gate.read(task('r3', 10));
  assert.deepEqual(await Promise.all([first, second, writer, late]), ['r1', 'r2', 'w', 'r3']);
  assert.deepEqual(log.slice(0, 2).sort(), ['start r1', 'start r2'], 'readers started together');
  const order = log.filter((entry) => /w$|r3$/.test(entry));
  assert.deepEqual(order, ['start w', 'end w', 'start r3', 'end r3'], 'the late reader waited behind the writer');
  assert.ok(log.indexOf('start w') > log.indexOf('end r1') && log.indexOf('start w') > log.indexOf('end r2'));
});

test('RwGate: a failing task releases the gate', async () => {
  const gate = new RwGate();
  await assert.rejects(gate.write(async () => { throw new Error('boom'); }), /boom/);
  await assert.rejects(gate.read(async () => { throw new Error('bam'); }), /bam/);
  assert.equal(await gate.write(async () => 'fine'), 'fine');
  assert.equal(gate.readers, 0);
  assert.equal(gate.writing, false);
});

test('an edit becomes the next revision: PySWF commits, the harness follows, readers re-open on the new bytes', async (t) => {
  const w = await opened(t, standardWorld());
  const { session } = w.harness;
  const original = fs.readFileSync(session.workingPath);
  assert.equal(session.revision, 0);
  const reply = await w.harness.call('assemble', { unit_id: UNIT, pcode: 'getlocal_0\nreturnvoid' });
  assert.equal(reply.ok, true);
  assert.equal(reply.served_by, 'native');
  assert.equal(reply.changed, true);
  assert.equal(reply.revision, 1);
  assert.notEqual(reply.sha256, sha(original));
  const edited = fs.readFileSync(path.join(session.dir, 'working-001.swf'));
  assert.equal(sha(edited), reply.sha256);
  assert.equal(edited.subarray(0, original.length).compare(original), 0);
  assert.equal(edited.subarray(original.length).toString(), '|edit:tag:1/method:0:pcode:python');
  assert.equal(reply.result.history.undo, true);
  assert.equal(reply.result.revision, reply.sha256, 'PySWF names the same bytes by their hash');
  const edit = w.requests('native').find((r) => r.op === 'edit');
  assert.equal(edit.args.base_revision, sha(original), 'the edit names the revision it was made against');
  assert.deepEqual([edit.args.mode, edit.args.engine, edit.args.code], ['pcode', 'python', 'getlocal_0\nreturnvoid']);
  // RustSWF still holds revision 0; its next read re-opens on the new working copy first.
  const scripts = await w.harness.call('scripts', {});
  assert.equal(scripts.revision, 1);
  const rustOps = w.requests('rust').filter((r) => ['open', 'scripts'].includes(r.op));
  assert.deepEqual(rustOps.map((r) => r.op), ['open', 'open', 'scripts']);
  assert.equal(path.basename(rustOps[1].args.path), 'working-001.swf');
});

test('undo and redo walk the history and each step is a revision; with nothing to undo nothing changes', async (t) => {
  const w = await opened(t, standardWorld());
  const { session } = w.harness;
  const initial = session.sha256;
  await assert.rejects(w.harness.call('undo', {}), (e) => e.kind === 'failed' && /deshacer/.test(e.message));
  assert.equal(session.revision, 0);
  const edit = await w.harness.call('compile', { unit_id: UNIT, source: 'function m0(){}' });
  assert.equal(edit.revision, 1);
  const edited = edit.sha256;
  const undo = await w.harness.call('undo', {});
  assert.equal([undo.revision, undo.sha256, undo.changed].join(), [2, initial, true].join());
  assert.equal(undo.result.history.redo, true);
  const redo = await w.harness.call('redo', {});
  assert.equal([redo.revision, redo.sha256].join(), [3, edited].join());
  const compile = w.requests('native').find((r) => r.op === 'edit');
  assert.deepEqual([compile.args.mode, compile.args.code], ['source', 'function m0(){}']);
  assert.equal((await w.harness.call('scripts', {})).revision, 3);
});

test('an edit made against a stale revision is refused by PySWF and leaves the document alone', async (t) => {
  const w = await opened(t, standardWorld());
  await assert.rejects(
    w.harness.call('assemble', { unit_id: UNIT, pcode: 'x', base_revision: 'not-the-current-hash' }),
    (e) => e.kind === 'failed' && /El documento cambió/.test(e.message),
  );
  assert.equal(w.harness.session.revision, 0);
  const fine = await w.harness.call('assemble', { unit_id: UNIT, pcode: 'x', base_revision: w.harness.session.sha256 });
  assert.equal(fine.revision, 1);
});

test('native editing operations are followed too: compile_project and resource edits', async (t) => {
  const w = await opened(t, standardWorld());
  const project = await w.harness.call('compile_project', { profile: 'auto-as3', entry: 'Main.as', output: 'out.swf' });
  assert.equal(project.served_by, 'native');
  assert.equal(project.changed, true);
  assert.equal(project.revision, 1);
  assert.equal(project.result.path, 'out.swf');
  const resource = await w.harness.call('replace_resource', { tag_index: 3, operation: 'text', text: 'hola' });
  assert.equal(resource.revision, 2);
  const request = w.requests('native').filter((r) => r.op === 'resource_edit').at(-1);
  assert.equal(request.args.base_revision, project.sha256, 'replace_resource also names the revision it started from');
  assert.equal(request.args.operation, 'text');
});

test('saving does not create a revision', async (t) => {
  const w = await opened(t, standardWorld());
  const target = path.join(w.ws.root, 'saved.swf');
  const saved = await w.harness.call('save', { path: target });
  assert.equal(saved.served_by, 'native');
  assert.equal(saved.revision, 0);
  assert.equal(saved.changed, false);
  assert.deepEqual(fs.readFileSync(target), fs.readFileSync(w.sample));
});

test('only the last few working copies are kept', async (t) => {
  const w = await opened(t, standardWorld());
  const { session } = w.harness;
  for (let i = 0; i < KEEP_REVISIONS + 3; i += 1) await w.harness.call('assemble', { unit_id: UNIT, pcode: `step ${i}` });
  const files = fs.readdirSync(session.dir).filter((name) => /^working-\d+\.swf$/.test(name)).sort();
  assert.equal(session.revision, KEEP_REVISIONS + 3);
  assert.equal(files.length, KEEP_REVISIONS);
  assert.equal(files.at(-1), `working-${String(session.revision).padStart(3, '0')}.swf`);
  assert.equal(fs.existsSync(path.join(session.dir, 'working-000.swf')), false);
});

test('an edit waits for the reads in progress, and reads queued behind it see the new revision', async (t) => {
  const slow = rust({ decompile: { byUnit: { [UNIT]: { sleep: 500, result: { unit_id: UNIT, state: 'structured', source: CLEAN, pcode: 'p', diagnostics: [] } } } } });
  const w = await opened(t, standardWorld({ rust: slow }));
  const read = w.harness.call('decompile', { unit_id: UNIT, engine: 'rust' });
  await sleep(120);
  const write = w.harness.call('assemble', { unit_id: UNIT, pcode: 'x' });
  await sleep(60);
  const later = w.harness.call('scripts', {});
  const [readReply, writeReply, laterReply] = await Promise.all([read, write, later]);
  const decompileAt = w.requests('rust').find((r) => r.op === 'decompile').t;
  const editAt = w.requests('native').find((r) => r.op === 'edit').t;
  assert.ok(editAt >= decompileAt + 450, `the edit started ${editAt - decompileAt} ms after the read began`);
  assert.equal(readReply.revision, 0);
  assert.equal(writeReply.revision, 1);
  assert.equal(laterReply.revision, 1, 'a read queued behind the edit sees its result');
  const lastOpen = w.requests('rust').filter((r) => r.op === 'open').at(-1);
  assert.equal(path.basename(lastOpen.args.path), 'working-001.swf');
});

test('reads of one revision share a single open', async (t) => {
  const w = makeHarness(standardWorld());
  t.after(w.cleanup);
  const opening = await w.harness.call('open', { path: w.sample });
  assert.equal(opening.ok, true);
  // Drop what RustSWF has open, as a restart would, then ask five things at once.
  w.harness.engines.get('rust').openedKey = null;
  const replies = await Promise.all([1, 2, 3, 4, 5].map(() => w.harness.call('scripts', { engine: 'rust' })));
  assert.ok(replies.every((r) => r.served_by === 'rust'));
  assert.equal(w.requests('rust').filter((r) => r.op === 'open').length, 2, 'one for the first open, one shared by the five reads');
});

test('a crashed engine is replaced by the next one for that call, and restarted and re-opened for the next', async (t) => {
  const scratch = tempDir('marker-');
  t.after(() => removeDir(scratch));
  const crashing = rust({ scripts: { crashOnce: path.join(scratch, 'crashed.marker'), result: unitsReply('Rust m0') } });
  const w = makeHarness(standardWorld({ rust: crashing }));
  t.after(w.cleanup);
  const exits = [];
  w.harness.on('engine-exit', (event) => exits.push(event));
  await w.harness.call('open', { path: w.sample });
  const firstPid = w.harness.engines.get('rust').worker.pid;
  const first = await w.harness.call('scripts', {});
  assert.equal(first.served_by, 'native');
  assert.deepEqual(first.attempts.map((a) => [a.engine, a.kind]), [['rust', 'crashed'], ['native', undefined]]);
  assert.match(first.attempts[0].error, /código 3/);
  assert.ok(await waitUntil(() => !isAlive(firstPid)));
  assert.ok(await waitUntil(() => exits.length > 0));
  assert.equal(exits[0].engine, 'rust');
  const second = await w.harness.call('scripts', {});
  assert.equal(second.served_by, 'rust');
  const status = w.harness.listEngines().find((e) => e.id === 'rust').runtime;
  assert.deepEqual([status.spawns, status.restarts], [2, 1]);
  const opens = w.requests('rust').filter((r) => r.op === 'open');
  assert.equal(opens.length, 2, 'the restarted worker was given the document again');
  assert.notEqual(opens[0].pid, opens[1].pid);
  assert.equal(opens[0].args.path, opens[1].args.path);
});

test('an engine that hangs hits its deadline, is replaced for that call, and works again afterwards', async (t) => {
  const scratch = tempDir('marker-');
  t.after(() => removeDir(scratch));
  const hanging = rust({ scripts: { hangOnce: path.join(scratch, 'hung.marker'), result: unitsReply('Rust m0') } });
  const w = makeHarness(standardWorld({ rust: hanging }), { env: { FLASH_SUITE_TIMEOUT_SECONDS: '1' } });
  t.after(w.cleanup);
  await w.harness.call('open', { path: w.sample });
  const pid = w.harness.engines.get('rust').worker.pid;
  const started = Date.now();
  const first = await w.harness.call('scripts', {});
  const took = Date.now() - started;
  assert.ok(took >= 900 && took < 6000, `took ${took} ms`);
  assert.equal(first.served_by, 'native');
  assert.equal(first.attempts[0].kind, 'timeout');
  assert.match(first.attempts[0].error, /Límite de 1 segundos/);
  assert.ok(await waitUntil(() => !isAlive(pid)), 'the hung worker was killed');
  assert.equal((await w.harness.call('scripts', {})).served_by, 'rust');
});

test('cancel stops what is running, discards its result, and the document survives', async (t) => {
  const slow = rust({ decompile: { byUnit: { [UNIT]: { sleep: 8000, result: { unit_id: UNIT, source: CLEAN, state: 'structured', pcode: '', diagnostics: [] } } } } });
  const w = await opened(t, standardWorld({ rust: slow }));
  const pid = w.harness.engines.get('rust').worker.pid;
  const pending = w.harness.call('decompile', { unit_id: UNIT, engine: 'rust' });
  await sleep(300);
  const started = Date.now();
  w.harness.cancel();
  await assert.rejects(pending, (e) => e.kind === 'cancelled');
  assert.ok(Date.now() - started < 4000, 'cancel does not wait for the engine');
  assert.ok(await waitUntil(() => !isAlive(pid)));
  const after = await w.harness.call('scripts', {});
  assert.equal(after.served_by, 'rust', 'the engine restarts and re-opens the same revision');
  assert.equal(after.revision, 0);
  const envelope = await w.harness.request('scripts', { session_id: 'someone-else' });
  assert.equal(envelope.ok, false);
  assert.equal(envelope.error.kind, 'stale');
});

test('PySWF keeps its undo history across a restart: its recovery project is adopted, not re-opened', async (t) => {
  const w = await opened(t, standardWorld());
  const edit = await w.harness.call('assemble', { unit_id: UNIT, pcode: 'x' });
  assert.equal(edit.revision, 1);
  const pid = w.harness.engines.get('native').worker.pid;
  w.harness.cancel();
  assert.ok(await waitUntil(() => !isAlive(pid)));
  const undo = await w.harness.call('undo', {});
  assert.equal(undo.revision, 2);
  assert.equal(undo.sha256, sha(fs.readFileSync(w.sample)), 'the history survived: undo went back to the original bytes');
  const native = w.requests('native');
  assert.equal(native.filter((r) => r.op === 'open').length, 1, 'the project was never reopened');
  assert.ok(native.some((r) => r.op === 'state'), 'the restarted worker was asked what it recovered');
  assert.notEqual(native[0].pid, native.at(-1).pid);
});

test('opening another document replaces the session: the old folder goes and its ids go stale', async (t) => {
  const w = await opened(t, standardWorld());
  const first = w.harness.session;
  const other = path.join(w.ws.root, 'other.swf');
  fs.writeFileSync(other, defaultSwf());
  const reply = await w.harness.call('open', { path: other });
  assert.notEqual(reply.session_id, first.id);
  assert.equal(reply.result.info.filename, 'other.swf');
  assert.ok(await waitUntil(() => !fs.existsSync(first.dir)));
  await assert.rejects(w.harness.call('scripts', { session_id: first.id }), (e) => e.kind === 'stale');
  const fresh = await w.harness.call('scripts', { session_id: reply.session_id });
  assert.equal(fresh.session_id, reply.session_id);
  assert.equal(fresh.revision, 0);
});

test('a failed open leaves the open document as it was and no half-made session folder behind', async (t) => {
  const w = await opened(t, standardWorld());
  const before = w.harness.session.id;
  await assert.rejects(w.harness.call('open', { path: path.join(w.ws.root, 'gone.swf') }), (e) => e.kind === 'not_found');
  assert.equal(w.harness.session.id, before);
  const broken = makeHarness([rust({ open: { error: { message: 'roto', kind: 'failed' } } }), native({}, { available: false }), jpexs()]);
  t.after(broken.cleanup);
  await assert.rejects(broken.harness.call('open', { path: broken.sample }), (e) => e.kind === 'failed' && e.message === 'roto');
  assert.equal(broken.harness.session, null);
  const leftovers = fs.readdirSync(broken.harness.stateRoot).filter((name) => /^[0-9a-f]{8}-[0-9a-f]{4}-/.test(name));
  assert.deepEqual(leftovers, []);
});

test('a project file is opened by PySWF, whose working movie becomes revision 0', async (t) => {
  const w = makeHarness(standardWorld());
  t.after(w.cleanup);
  const project = path.join(w.ws.root, 'game.pyswfproj');
  const movie = swfBytes([tagBytes(69, [8, 0, 0, 0]), tagBytes(1)]);
  fs.writeFileSync(project, movie);
  const reply = await w.harness.call('open', { path: project });
  assert.equal(reply.served_by, 'native');
  assert.equal(reply.revision, 0);
  assert.equal(reply.result.filename, 'game.pyswfproj');
  const { session } = w.harness;
  assert.deepEqual(fs.readFileSync(session.workingPath), movie);
  assert.equal(path.basename(session.workingPath), 'working-000.swf');
  assert.ok(w.requests('native').some((r) => r.op === 'load_project'));
  const scripts = await w.harness.call('scripts', {});
  assert.equal(scripts.served_by, 'rust');
  assert.equal(path.basename(w.requests('rust').find((r) => r.op === 'open').args.path), 'working-000.swf');
});

test('load_project, the way the interface opens a project, is an open that only PySWF can serve', async (t) => {
  const w = makeHarness(standardWorld());
  t.after(w.cleanup);
  const project = path.join(w.ws.root, 'game.pyswfproj');
  fs.writeFileSync(project, swfBytes([tagBytes(69, [8, 0, 0, 0]), tagBytes(1)]));
  const reply = await w.harness.call('load_project', { path: project });
  assert.equal(reply.op, 'load_project');
  assert.equal(reply.served_by, 'native');
  assert.equal(reply.revision, 0);
  assert.equal(reply.result.filename, 'game.pyswfproj');
  assert.equal(w.requests('rust').length, 0, 'RustSWF is not asked about a project');
  assert.ok(w.requests('native').some((r) => r.op === 'load_project'));
  // With no PySWF there is nobody to open a project.
  const alone = makeHarness([rust(), native({}, { available: false })]);
  t.after(alone.cleanup);
  const lonely = path.join(alone.ws.root, 'game.pyswfproj');
  fs.writeFileSync(lonely, 'x');
  await assert.rejects(alone.harness.call('load_project', { path: lonely }), (e) => e.kind === 'no_engine' || e.kind === 'unavailable');
  assert.equal(alone.harness.session, null);
});
