'use strict';
/**
 * Routing and fallback: every operation goes to the requested engine when it
 * implements it, otherwise along the chain RustSWF -> PySWF -> JPEXS (through
 * PySWF), and the answer says which engine served it (`served_by`).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { makeHarness, REAL_PROTOCOL, swfBytes, tagBytes } = require('../testkit');
const { rust, native, js, jpexs, standardWorld, UNIT, CLEAN, decompileSpec, RUST_OPS, NATIVE_OPS } = require('../testkit/world');

const sha = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');

/** A harness with the document already open; cleanup is registered before anything can fail. */
async function opened(t, engines, options) {
  const w = makeHarness(engines, options);
  t.after(w.cleanup);
  const reply = await w.harness.call('open', { path: w.sample });
  return { ...w, opened: reply };
}

test('open copies the document, leaves the original alone and says which engine served it', async (t) => {
  const w = makeHarness(standardWorld());
  t.after(w.cleanup);
  const original = fs.readFileSync(w.sample);
  const reply = await w.harness.call('open', { path: w.sample });
  assert.equal(reply.ok, true);
  assert.equal(reply.op, 'open');
  assert.equal(reply.served_by, 'rust');
  assert.equal(reply.revision, 0);
  assert.equal(reply.sha256, sha(original));
  assert.equal(reply.changed, true);
  assert.deepEqual(reply.attempts.map((a) => [a.engine, a.ok]), [['rust', true]]);
  assert.equal(reply.result.info.filename, 'sample.swf', 'the user sees the original name, not working-000.swf');
  assert.equal(reply.result.filename, 'sample.swf');
  // RustSWF is not the session owner: the harness completes the state the editing interface reads.
  assert.deepEqual(
    [reply.result.revision, reply.result.dirty, reply.result.history, reply.result.script_count, reply.result.signature, reply.result.frame_count],
    [sha(original), false, { undo: false, redo: false }, 1, 'FWS', 1],
  );
  assert.deepEqual(reply.result.validation, { valid: true, errors: [], warnings: [], signature: 'FWS', version: 9, tag_count: 2, frame_count: 1 });
  const { session } = w.harness;
  assert.equal(reply.session_id, session.id);
  assert.notEqual(session.workingPath, w.sample);
  assert.equal(path.basename(session.workingPath), 'working-000.swf');
  assert.deepEqual(fs.readFileSync(session.workingPath), original);
  assert.equal(reply.result.working_path, session.workingPath);
  const open = w.requests('rust').find((r) => r.op === 'open');
  assert.equal(open.args.path, session.workingPath, 'engines read the working copy, never the user file');
  assert.deepEqual(fs.readFileSync(w.sample), original);
});

test('open reports a missing file, a directory, a bad path and an empty request', async (t) => {
  const w = makeHarness(standardWorld());
  t.after(w.cleanup);
  await assert.rejects(w.harness.call('open', { path: path.join(w.ws.root, 'missing.swf') }), (e) => e.kind === 'not_found');
  await assert.rejects(w.harness.call('open', { path: w.ws.root }), (e) => e.kind === 'bad_request');
  await assert.rejects(w.harness.call('open', {}), (e) => e.kind === 'bad_request');
  await assert.rejects(w.harness.call('open', { path: 5 }), (e) => e.kind === 'bad_request');
  assert.equal(w.harness.session, null);
});

test('when RustSWF cannot read the file, PySWF opens it and later reads skip RustSWF without asking again', async (t) => {
  const failing = rust({ open: { error: { message: 'Truncated SWF header', kind: 'failed' } } });
  const w = makeHarness(standardWorld({ rust: failing }));
  t.after(w.cleanup);
  const reply = await w.harness.call('open', { path: w.sample });
  assert.equal(reply.served_by, 'native');
  assert.deepEqual(reply.attempts.map((a) => [a.engine, a.ok, a.kind]), [['rust', false, 'failed'], ['native', true, undefined]]);
  assert.equal(reply.result.info.filename, 'sample.swf');
  assert.deepEqual([reply.result.history, reply.result.dirty, reply.result.revision], [{ undo: false, redo: false }, false, reply.sha256], 'PySWF\'s own state, untouched');
  const scripts = await w.harness.call('scripts', {});
  assert.equal(scripts.served_by, 'native');
  assert.equal(scripts.attempts[0].engine, 'rust');
  assert.match(scripts.attempts[0].error, /no pudo abrir esta revisión/);
  assert.equal(w.requests('rust').filter((r) => r.op === 'open').length, 1, 'RustSWF is not asked to open the same revision twice');
});

test('nothing works before a document is open, except operations that need none', async (t) => {
  const w = makeHarness(standardWorld());
  t.after(w.cleanup);
  for (const op of ['scripts', 'header', 'tag_tree', 'decompile', 'undo', 'state']) {
    await assert.rejects(w.harness.call(op, { unit_id: UNIT }), (e) => e.kind === 'not_open', op);
  }
  const caps = await w.harness.call('capabilities', {});
  assert.equal(caps.served_by, 'rust');
  assert.equal(caps.session_id, null);
  assert.equal(caps.result.protocol.version, 2);
  const engines = await w.harness.call('engines', {});
  assert.equal(engines.served_by, 'native', 'PySWF native operations that need no document run with none open');
});

test('invalid requests are refused before any engine sees them', async (t) => {
  const w = await opened(t, standardWorld());
  const before = w.requests('rust').length;
  await assert.rejects(w.harness.call('frobnicate', {}), (e) => e.kind === 'bad_request' && /frobnicate/.test(e.message));
  await assert.rejects(w.harness.call('', {}), (e) => e.kind === 'bad_request');
  await assert.rejects(w.harness.call(7, {}), (e) => e.kind === 'bad_request');
  await assert.rejects(w.harness.call('header', []), (e) => e.kind === 'bad_request');
  await assert.rejects(w.harness.call('header', 'x'), (e) => e.kind === 'bad_request');
  await assert.rejects(w.harness.call('header', { engine: 'nope' }), (e) => e.kind === 'bad_request' && /nope/.test(e.message));
  await assert.rejects(w.harness.call('header', { engine: 5 }), (e) => e.kind === 'bad_request');
  assert.equal(w.requests('rust').length, before);
});

test('the requested engine serves first; aliases work; others follow the chain', async (t) => {
  const w = await opened(t, standardWorld());
  const byDefault = await w.harness.call('header', {});
  assert.equal(byDefault.served_by, 'rust');
  assert.equal(byDefault.result.from, 'rust');
  const requested = await w.harness.call('scripts', { engine: 'native' });
  assert.equal(requested.served_by, 'native');
  assert.equal(requested.result.items[0].name, 'Py m0');
  assert.equal((await w.harness.call('scripts', { engine: 'pyswf' })).served_by, 'native');
  assert.equal((await w.harness.call('scripts', { engine: 'python' })).served_by, 'native');
  assert.equal((await w.harness.call('scripts', { engine: 'RustSWF' })).served_by, 'rust');
  // PySWF answers header itself, in the shape the single-window interface reads.
  const viaNative = await w.harness.call('header', { engine: 'native' });
  assert.equal(viaNative.served_by, 'native');
  assert.deepEqual([viaNative.result.from, viaNative.result.filename, viaNative.result.revision], ['native', 'sample.swf', 'r']);
  const legacy = await w.harness.call('header', { engine: 'js' });
  assert.equal(legacy.served_by, 'js');
  assert.equal(legacy.result.derived, true, 'the legacy adapter derives it from info');
  assert.equal(legacy.result.frame_size.width, 100);
});

test('tag_tree and resource_info are answered by the session owner first, and by RustSWF when it cannot', async (t) => {
  const rustAnswers = { tag_tree: { result: { nodes: [], node_count: 0, truncated: false, limit: 20000, from: 'rust' } }, resource_info: { result: { kind: 'raw', parsed: {}, from: 'rust' } } };
  const w = await opened(t, standardWorld({ rust: rust(rustAnswers) }));
  const tree = await w.harness.call('tag_tree', {});
  assert.equal(tree.served_by, 'native');
  assert.deepEqual(tree.attempts.map((a) => [a.engine, a.ok]), [['native', true]]);
  assert.deepEqual([tree.result.from, tree.result.filename, tree.result.revision], ['native', 'sample.swf', 'r']);
  const info = await w.harness.call('resource_info', { tag_index: 0 });
  assert.equal(info.served_by, 'native');
  assert.equal(w.requests('rust').filter((r) => r.op === 'tag_tree' || r.op === 'resource_info').length, 0, 'RustSWF was not even asked');
  // An explicit request still goes where it says.
  const explicit = await w.harness.call('tag_tree', { engine: 'rust' });
  assert.equal(explicit.served_by, 'rust');
  assert.equal(explicit.result.from, 'rust');
  assert.equal(explicit.result.filename, 'sample.swf', 'named after the file the person opened');
  assert.equal(explicit.result.revision, w.harness.session.sha256, 'and stamped with the revision it describes');
  // When the session owner cannot answer, RustSWF does.
  const failing = await opened(t, standardWorld({ rust: rust(rustAnswers), native: native({ tag_tree: { error: { message: 'boom', kind: 'failed' } } }) }));
  const fallback = await failing.harness.call('tag_tree', {});
  assert.equal(fallback.served_by, 'rust');
  assert.deepEqual(fallback.attempts.map((a) => [a.engine, a.ok, a.kind]), [['native', false, 'failed'], ['rust', true, undefined]]);
  assert.equal(fallback.result.from, 'rust');
  // Every other read keeps the chain order.
  assert.equal((await w.harness.call('header', {})).served_by, 'rust');
});

test('PySWF answers call the document by the name of the file that was opened', async (t) => {
  const w = await opened(t, standardWorld());
  const state = await w.harness.call('state', {});
  assert.deepEqual([state.result.filename, state.result.source_path], ['sample.swf', w.sample]);
  const edit = await w.harness.call('edit', { unit_id: UNIT, code: 'x', mode: 'source', base_revision: w.harness.session.sha256 });
  assert.equal(edit.result.filename, 'sample.swf');
  assert.equal(edit.changed, true);
  assert.equal(edit.result.revision, w.harness.session.sha256, 'the revision of the answer is the session\'s');
});

test('a requested engine that lacks the operation is recorded and the chain takes over; strict stops there', async (t) => {
  const w = await opened(t, standardWorld({ rust: rust({ extract_image: { result: { format: 'png' } } }) }));
  const lenient = await w.harness.call('extract_image', { engine: 'js', tag_index: 0 });
  assert.equal(lenient.served_by, 'rust');
  assert.deepEqual(lenient.attempts.map((a) => [a.engine, a.ok, a.kind]), [['js', false, 'unsupported'], ['rust', true, undefined]]);
  assert.match(lenient.attempts[0].error, /JsSWF no implementa extract_image/);
  const before = w.requests('js').length;
  await assert.rejects(
    w.harness.call('extract_image', { engine: 'js', strict: true, tag_index: 0 }),
    (e) => e.kind === 'unsupported' && e.attempts.length === 1 && e.attempts[0].engine === 'js',
  );
  assert.equal(w.requests('js').length, before, 'a refused engine is never contacted');
});

test('the harness reads the header and raw bytes itself when no engine can', async (t) => {
  const refusing = { error: { message: 'RustSWF no pudo', kind: 'failed' } };
  const noReads = { ops: NATIVE_OPS.filter((op) => op !== 'header' && op !== 'raw') };
  const w = await opened(t, [rust({ header: refusing, raw: refusing }), native({}, noReads), jpexs()]);
  const header = await w.harness.call('header', {});
  assert.equal(header.served_by, 'harness');
  assert.deepEqual(header.attempts.map((a) => [a.engine, a.ok]), [['rust', false], ['harness', true]]);
  assert.equal(header.result.derived, true);
  assert.equal(header.result.signature, 'FWS');
  assert.equal(header.result.filename, 'sample.swf');
  assert.deepEqual([header.result.frame_size.width, header.result.frame_size.height, header.result.frame_rate], [100, 50, 24]);
  assert.deepEqual(header.result.frame_size, { xmin: 0, xmax: 2000, ymin: 0, ymax: 1000, width: 100, height: 50 });
  assert.deepEqual([header.result.tag_count, header.result.shown_frames, header.result.abc_blocks], [4, 1, 0]);
  assert.deepEqual([header.result.has_as3, header.result.has_metadata, header.result.metadata], [true, false, null]);
  assert.equal(header.result.file_attributes.actionscript3, true);
  assert.equal(header.result.sha256, w.harness.session.sha256);
  assert.equal(header.result.revision, w.harness.session.sha256);
  const raw = await w.harness.call('raw', { tag_index: 0, limit: 16 });
  assert.equal(raw.served_by, 'harness');
  assert.deepEqual([raw.result.scope, raw.result.name, raw.result.tag_index, raw.result.type], ['tag', 'FileAttributes', 0, 69]);
  assert.deepEqual([raw.result.total, raw.result.length, raw.result.offset, raw.result.limit, raw.result.encoding], [4, 4, 0, 16, 'hex']);
  assert.equal(raw.result.hex, '08000000');
  assert.equal(raw.result.revision, w.harness.session.sha256);
  const page = (await w.harness.call('raw', { tag_path: [1], offset: 2, limit: 2, encoding: 'base64' })).result;
  assert.deepEqual([page.base64, page.hex], ['CAA=', undefined]);
  const whole = (await w.harness.call('raw', { scope: 'file', limit: 8 })).result;
  assert.deepEqual([whole.scope, whole.name, whole.total, whole.length], ['file', 'Archivo SWF', fs.statSync(w.sample).size, 8]);
  assert.equal(whole.hex, fs.readFileSync(w.sample).subarray(0, 8).toString('hex'));
  // The error reported is the first engine's; the built-in reader's verdict is in the attempts.
  await assert.rejects(w.harness.call('raw', { tag_index: 99 }), (e) => {
    assert.equal(e.kind, 'failed');
    assert.deepEqual(e.attempts.map((a) => [a.engine, a.kind]), [['rust', 'failed'], ['harness', 'not_found']]);
    return true;
  });
  await assert.rejects(w.harness.call('raw', {}), (e) => e.attempts.at(-1).engine === 'harness' && e.attempts.at(-1).kind === 'bad_request');
  // Bad paging is refused with a reason, not clamped.
  for (const args of [{ tag_index: 0, limit: 0 }, { tag_index: 0, limit: 65537 }, { tag_index: 0, offset: -1 }, { tag_index: 0, offset: 5 }, { tag_index: 0, encoding: 'utf8' }]) {
    await assert.rejects(w.harness.call('raw', args), (e) => e.attempts.at(-1).engine === 'harness' && e.attempts.at(-1).kind === 'bad_request', JSON.stringify(args));
  }
  // The reads also work for files that ship compressed.
  const compressed = await opened(t, [rust({ header: refusing }), native({}, noReads)], { sampleBytes: swfBytes([tagBytes(69, [8, 0, 0, 0]), tagBytes(1)], { signature: 'CWS' }) });
  const cws = await compressed.harness.call('header', {});
  assert.equal(cws.served_by, 'harness');
  assert.equal(cws.result.signature, 'CWS');
  assert.equal(cws.result.compression, 'zlib');
  assert.equal(cws.result.tag_count, 3);
});

test('a ZWS file gets a partial header from the harness and no raw bytes, with the reason', async (t) => {
  const lzma = Buffer.concat([Buffer.from('ZWS', 'latin1'), Buffer.from([13, 100, 0, 0, 0]), Buffer.alloc(32)]);
  const refusing = { error: { message: 'no', kind: 'failed' } };
  const noReads = { ops: NATIVE_OPS.filter((op) => op !== 'header' && op !== 'raw') };
  const w = await opened(t, [rust({ header: refusing, raw: refusing }), native({}, noReads)], { sampleBytes: lzma });
  const header = await w.harness.call('header', {});
  assert.equal(header.served_by, 'harness');
  assert.equal(header.result.signature, 'ZWS');
  assert.equal(header.result.frame_size, null);
  assert.equal(header.result.diagnostics[0].code, 'header_partial');
  await assert.rejects(w.harness.call('raw', { tag_index: 0 }), (e) => e.kind === 'failed' && e.attempts.at(-1).engine === 'harness' && e.attempts.at(-1).kind === 'unsupported');
});

test('an operation only PySWF implements goes straight to it', async (t) => {
  const w = await opened(t, standardWorld());
  const rustBefore = w.requests('rust').length;
  const edit = await w.harness.call('compile_project', { entry: 'x', output: 'y' });
  assert.equal(edit.served_by, 'native');
  assert.equal(edit.attempts.length, 1);
  assert.equal(w.requests('rust').length, rustBefore);
  const undo = await w.harness.call('undo', {});
  assert.equal(undo.served_by, 'native');
});

test('when an engine fails the next in the chain serves, and the attempts say what happened', async (t) => {
  const broken = rust({ decompile: { error: { message: 'Unidad de script desconocida', kind: 'not_found' } } });
  const w = await opened(t, standardWorld({ rust: broken }));
  const reply = await w.harness.call('decompile', { unit_id: UNIT, engine: 'rust' });
  assert.equal(reply.served_by, 'native');
  assert.deepEqual(reply.attempts.map((a) => [a.engine, a.ok, a.kind]), [['rust', false, 'not_found'], ['native', true, undefined]]);
  assert.equal(reply.result.source, CLEAN);
  assert.equal(reply.result.engine, 'native');
  assert.equal(reply.result.state, 'structured');
  assert.ok(reply.attempts.every((a) => typeof a.ms === 'number'));
});

test('the chain ends at JPEXS through PySWF, and the answer says so', async (t) => {
  const nativeFails = native({
    decompile: { byEngine: { jpexs: { result: { engine: 'jpexs', source: CLEAN, pcode: 'p', diagnostics: [], unit_id: UNIT } }, default: { error: { message: 'python no pudo', kind: 'failed' } } } },
  });
  const rustFails = rust({ decompile: { error: { message: 'pcode_only', kind: 'failed' } } });
  const w = await opened(t, standardWorld({ rust: rustFails, native: nativeFails }));
  const reply = await w.harness.call('decompile', { unit_id: UNIT, engine: 'rust' });
  assert.equal(reply.served_by, 'jpexs');
  assert.equal(reply.via, 'native');
  assert.deepEqual(reply.attempts.map((a) => [a.engine, a.ok]), [['rust', false], ['native', false], ['jpexs', true]]);
  assert.equal(reply.attempts[2].via, 'native');
  assert.equal(reply.result.engine, 'jpexs');
  const asked = w.requests('native').filter((r) => r.op === 'decompile').map((r) => r.args.engine);
  assert.deepEqual(asked, ['python', 'jpexs'], 'PySWF is told which of its engines to use');
});

test('the chain comes before the other engines, and those still answer when the chain cannot', async (t) => {
  const down = [rust({}, { available: false }), native({}, { available: false }), js(), jpexs()];
  const w = makeHarness(down);
  t.after(w.cleanup);
  const reply = await w.harness.call('open', { path: w.sample });
  assert.equal(reply.served_by, 'js');
  assert.deepEqual(reply.attempts.map((a) => [a.engine, a.ok, a.kind]), [['rust', false, 'unavailable'], ['native', false, 'unavailable'], ['js', true, undefined]]);
  const scripts = await w.harness.call('scripts', {});
  assert.equal(scripts.served_by, 'js');
  assert.equal(scripts.result.items[0].language, 'as3', 'avm2 is reported as as3 whichever engine answers');
});

test('a bad request is not retried elsewhere: another engine would refuse it too', async (t) => {
  const picky = rust({ decompile: { error: { message: 'unit_id inválido', kind: 'bad_request' } } });
  const w = await opened(t, standardWorld({ rust: picky }));
  await assert.rejects(w.harness.call('decompile', { unit_id: UNIT, engine: 'rust' }), (e) => {
    assert.equal(e.kind, 'bad_request');
    assert.equal(e.attempts.length, 1);
    return true;
  });
  assert.equal(w.requests('native').filter((r) => r.op === 'decompile').length, 0);
});

test('when every engine fails the error is the first real one and carries all attempts', async (t) => {
  const nope = { error: { message: 'no sé', kind: 'failed' } };
  const w = await opened(t, standardWorld({ rust: rust({ scripts: nope }), native: native({ scripts: { error: { message: 'tampoco', kind: 'failed' } } }), js: js({ scripts: nope }) }));
  await assert.rejects(w.harness.call('scripts', {}), (e) => {
    assert.equal(e.kind, 'failed');
    assert.equal(e.message, 'no sé');
    assert.deepEqual(e.attempts.map((a) => a.engine), ['rust', 'native', 'js']);
    assert.ok(e.attempts.every((a) => a.ok === false));
    return true;
  });
  const envelope = await w.harness.request('scripts', {});
  assert.equal(envelope.ok, false);
  assert.equal(envelope.error.kind, 'failed');
  assert.equal(envelope.error.attempts.length, 3, 'request() carries the attempts across IPC');
});

test('no engine at all is reported as such, an operation nobody implements as unsupported', async (t) => {
  const w = makeHarness([rust({}, { available: false }), native({}, { available: false })]);
  t.after(w.cleanup);
  await assert.rejects(w.harness.call('open', { path: w.sample }), (e) => e.kind === 'no_engine' && /rust/.test(e.message) && /native/.test(e.message));
  const live = await opened(t, standardWorld());
  await assert.rejects(live.harness.call('export_svg', {}), (e) => e.kind === 'unsupported' && /export_svg/.test(e.message));
});

test('native operations are forwarded verbatim to their owner and several owners fall back in manifest order', async (t) => {
  const w = await opened(t, standardWorld({ rust: rust({ info: { error: { message: 'sin info', kind: 'failed' } } }) }));
  const state = await w.harness.call('state', {});
  assert.equal(state.served_by, 'native');
  assert.equal(state.result.revision, w.harness.session.sha256);
  const info = await w.harness.call('info', {});
  assert.equal(info.served_by, 'js', 'RustSWF failed, the next owner of info answers');
  assert.equal(info.result.info.filename, 'sample.swf', 'info shows the original file name');
  assert.deepEqual(info.attempts.map((a) => a.engine), ['rust', 'js']);
});

test('capabilities answer for the engine asked: real, adapted from the manifest, or from PySWF ping', async (t) => {
  const w = makeHarness(standardWorld());
  t.after(w.cleanup);
  assert.equal((await w.harness.call('capabilities', { engine: 'rust' })).result.protocol.version, 2);
  const adapted = (await w.harness.call('capabilities', { engine: 'js' })).result;
  assert.equal(adapted.adapted, true);
  assert.deepEqual(adapted.ops, ['capabilities', 'open', 'header', 'scripts', 'disassemble', 'decompile']);
  const py = (await w.harness.call('capabilities', { engine: 'native' })).result;
  assert.equal(py.version, '0.0.0');
  assert.deepEqual(py.derived_ops, [], 'only what the manifest of this scripted world declares');
});

test('class units reach RustSWF with the verified hierarchy contract, method units do not', async (t) => {
  const protocol = JSON.parse(fs.readFileSync(REAL_PROTOCOL, 'utf8'));
  const contractFile = Buffer.from('not really a swc');
  protocol.contracts.hierarchy = { path: 'vendor/hierarchy.swc', sha256: sha(contractFile), description: 'test' };
  const echo = rust({ decompile: { echo: true } });
  const w = makeHarness(standardWorld({ rust: echo }), { protocol });
  t.after(w.cleanup);
  fs.mkdirSync(path.join(w.ws.root, 'vendor'));
  fs.writeFileSync(path.join(w.ws.root, 'vendor', 'hierarchy.swc'), contractFile);
  await w.harness.call('open', { path: w.sample });
  const asClass = await w.harness.call('decompile', { unit_id: 'tag:1/class:0', engine: 'rust' });
  assert.equal(asClass.result.args.hierarchy_sha256, sha(contractFile));
  assert.equal(path.normalize(asClass.result.args.hierarchy_swc), path.join(w.ws.root, 'vendor', 'hierarchy.swc'));
  const asMethod = await w.harness.call('decompile', { unit_id: UNIT, engine: 'rust' });
  assert.equal(asMethod.result.args.hierarchy_swc, undefined);
  const explicit = await w.harness.call('decompile', { unit_id: 'tag:1/class:0', engine: 'rust', hierarchy_swc: 'custom.swc', hierarchy_sha256: 'abc' });
  assert.equal(explicit.result.args.hierarchy_swc, 'custom.swc', 'a caller-supplied contract wins');
  // A file with the wrong content is not a contract.
  fs.writeFileSync(path.join(w.ws.root, 'vendor', 'hierarchy.swc'), 'tampered');
  const other = makeHarness(standardWorld({ rust: echo }), { protocol });
  t.after(other.cleanup);
  fs.mkdirSync(path.join(other.ws.root, 'vendor'));
  fs.writeFileSync(path.join(other.ws.root, 'vendor', 'hierarchy.swc'), 'tampered');
  await other.harness.call('open', { path: other.sample });
  const refused = await other.harness.call('decompile', { unit_id: 'tag:1/class:0', engine: 'rust' });
  assert.equal(refused.result.args.hierarchy_swc, undefined);
});

test('arguments meant for the harness never reach a worker', async (t) => {
  const echo = rust({ scripts: { echo: true } });
  const w = await opened(t, standardWorld({ rust: echo }));
  const reply = await w.harness.call('scripts', { query: 'm', engine: 'rust', strict: true, session_id: w.harness.session.id, engines: ['rust'], include_jpexs: 'never', limit: 5 });
  assert.deepEqual(reply.result.args, { query: 'm', limit: 5 });
});

test('the harness-level info about an engine lists its levels and how it runs', async (t) => {
  const w = await opened(t, standardWorld({ rust: rust({ capabilities: { result: { protocol: { name: 'flash-engine-jsonl', version: 2 }, ops: RUST_OPS.filter((op) => op !== 'tag_tree') } } }) }));
  const list = w.harness.listEngines();
  const byId = Object.fromEntries(list.map((e) => [e.id, e]));
  assert.equal(byId.rust.runtime.running, true);
  assert.equal(byId.rust.runtime.spawns, 1);
  assert.deepEqual(byId.rust.runtime.mismatch, { missing: ['tag_tree'], extra: [] }, 'manifest and worker disagree: it is visible');
  assert.equal(byId.native.runtime.running, false, 'PySWF is started on demand, not at open');
  assert.equal(byId.jpexs.runtime, null);
  assert.equal(byId.rust.levels.L3.complete, true);
  assert.match(byId.rust.levels_line, /^L1 \d\/6/);
});

test('a v2 worker that does not speak v2 is refused at start, and the chain moves on', async (t) => {
  const liar = rust({ capabilities: { result: { engine: 'rust', ops: RUST_OPS } } });
  const w = makeHarness(standardWorld({ rust: liar }));
  t.after(w.cleanup);
  const reply = await w.harness.call('open', { path: w.sample });
  assert.equal(reply.served_by, 'native');
  assert.equal(reply.attempts[0].kind, 'protocol');
  assert.match(reply.attempts[0].error, /protocolo v2/);
});

test('close stops every worker and removes the session folders', async (t) => {
  const w = await opened(t, standardWorld());
  await w.harness.call('scripts', {});
  await w.harness.call('undo', {}).catch(() => {});
  const pids = w.harness.listEngines().map((e) => e.runtime && e.runtime.pid).filter(Boolean);
  assert.equal(pids.length, 2);
  const dir = w.harness.session.dir;
  await w.harness.close();
  const { isAlive, waitUntil } = require('../testkit');
  for (const pid of pids) assert.ok(await waitUntil(() => !isAlive(pid)), `pid ${pid} still running`);
  assert.equal(fs.existsSync(dir), false);
  assert.equal(fs.existsSync(w.harness.stateRoot), false);
  await assert.rejects(w.harness.call('scripts', {}), (e) => e.kind === 'cancelled');
  w.ws.cleanup();
  void swfBytes;
  void tagBytes;
  void decompileSpec;
});
