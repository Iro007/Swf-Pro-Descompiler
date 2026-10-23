'use strict';
/**
 * `decompile` with engine `auto`: every source engine is asked at once and the best
 * source by the common score wins; ties go to the lower manifest order; JPEXS (through
 * PySWF) is asked only when nobody produced a clean result.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { makeHarness, tempDir, removeDir } = require('../testkit');
const { rust, native, js, standardWorld, decompileSpec, UNIT, CLEAN } = require('../testkit/world');

const HARD = 'function m0() {\n    /* retained as P-code */\n}\n'; // 70
const SOFT = 'function m0() {\n    // Unsupported construct\n}\n'; // 95
const BROKEN = 'function m0() {\n'; // 60

const FAIL = { error: { message: 'el motor falló', kind: 'failed' } };
const spec = (source) => (source === 'FAIL' ? FAIL : decompileSpec(source));

/** The standard cast with a chosen decompile result per engine ('FAIL', null for P-code only, or text). */
function cast({ rust: r, native: n, js: j, jpexs: x }, extra = {}) {
  return standardWorld({
    rust: rust({ decompile: spec(r), ...(extra.rust || {}) }),
    native: native({ decompile: { byEngine: { jpexs: spec(x), default: spec(n) } }, ...(extra.native || {}) }),
    js: js({ decompile: spec(j), ...(extra.js || {}) }),
  });
}

async function opened(t, engines, options) {
  const w = makeHarness(engines, options);
  t.after(w.cleanup);
  await w.harness.call('open', { path: w.sample });
  return w;
}

const asked = (w, id) => w.requests(id).filter((r) => r.op === 'decompile');

test('the best score wins and a tie goes to the lower manifest order', async (t) => {
  const w = await opened(t, cast({ rust: HARD, native: CLEAN, js: CLEAN, jpexs: CLEAN }));
  const reply = await w.harness.call('decompile', { unit_id: UNIT });
  assert.equal(reply.served_by, 'native', 'PySWF (order 1) beats JsSWF (order 2) on a tie at 100');
  assert.equal(reply.result.source, CLEAN);
  assert.equal(reply.result.engine, 'native');
  const { selection } = reply.result;
  assert.equal(selection.strategy, 'auto');
  assert.equal(selection.engine, 'native');
  assert.equal(selection.name, 'PySWF');
  assert.equal(selection.score.value, 100);
  assert.deepEqual(selection.candidates.map((c) => [c.engine, c.score, c.ok]), [['rust', 70, true], ['native', 100, true], ['js', 100, true]]);
  assert.deepEqual(
    Object.keys(selection.candidates[0]).sort(),
    ['balanced', 'engine', 'hard', 'name', 'ms', 'ok', 'score', 'soft', 'state'].sort(),
  );
  assert.deepEqual([selection.candidates[0].hard, selection.candidates[0].soft, selection.candidates[0].balanced], [1, 0, true]);
  assert.equal(asked(w, 'native').filter((r) => r.args.engine === 'jpexs').length, 0, 'JPEXS is not asked when a clean source exists');
  assert.equal(reply.attempts.length, 3);
});

test('with equal scores everywhere the first engine in manifest order is kept', async (t) => {
  const w = await opened(t, cast({ rust: CLEAN, native: CLEAN, js: CLEAN, jpexs: CLEAN }));
  const reply = await w.harness.call('decompile', { unit_id: UNIT, engine: 'auto' });
  assert.equal(reply.served_by, 'rust');
  assert.equal(reply.result.selection.engine, 'rust');
});

test('a worse source from an engine that is usually better does not win', async (t) => {
  const w = await opened(t, cast({ rust: BROKEN, native: SOFT, js: HARD, jpexs: 'FAIL' }));
  const reply = await w.harness.call('decompile', { unit_id: UNIT });
  assert.equal(reply.served_by, 'native');
  assert.deepEqual(reply.result.selection.candidates.map((c) => [c.engine, c.score]), [['rust', 60], ['native', 95], ['js', 70], ['jpexs', null]]);
});

test('JPEXS through PySWF is asked when nobody is clean, and wins only if it is better', async (t) => {
  const w = await opened(t, cast({ rust: HARD, native: SOFT, js: BROKEN, jpexs: CLEAN }));
  const reply = await w.harness.call('decompile', { unit_id: UNIT });
  assert.equal(reply.served_by, 'jpexs');
  assert.equal(reply.via, 'native');
  assert.equal(reply.result.engine, 'jpexs');
  assert.deepEqual(reply.result.selection.candidates.map((c) => [c.engine, c.score]), [['rust', 70], ['native', 95], ['js', 60], ['jpexs', 100]]);
  assert.deepEqual(asked(w, 'native').map((r) => r.args.engine), ['python', 'jpexs']);
  // When JPEXS is no better, the native result stays.
  const worse = await opened(t, cast({ rust: HARD, native: SOFT, js: BROKEN, jpexs: HARD }));
  const kept = await worse.harness.call('decompile', { unit_id: UNIT });
  assert.equal(kept.served_by, 'native');
  assert.equal(kept.result.selection.candidates.at(-1).engine, 'jpexs');
});

test('include_jpexs never leaves JPEXS out, always asks it even when something is clean', async (t) => {
  const w = await opened(t, cast({ rust: HARD, native: SOFT, js: BROKEN, jpexs: CLEAN }));
  const never = await w.harness.call('decompile', { unit_id: UNIT, include_jpexs: 'never' });
  assert.equal(never.served_by, 'native');
  assert.equal(never.result.selection.candidates.some((c) => c.engine === 'jpexs'), false);
  const clean = await opened(t, cast({ rust: CLEAN, native: CLEAN, js: CLEAN, jpexs: CLEAN }));
  const always = await clean.harness.call('decompile', { unit_id: UNIT, include_jpexs: 'always' });
  assert.equal(always.result.selection.candidates.at(-1).engine, 'jpexs');
  assert.equal(always.served_by, 'rust', 'a tie at 100 still goes to the lowest order');
});

test('when nobody produces source, the first engine that answered shows its P-code and the reason', async (t) => {
  const w = await opened(t, cast({ rust: null, native: null, js: null, jpexs: null }));
  const reply = await w.harness.call('decompile', { unit_id: UNIT });
  assert.equal(reply.served_by, 'rust');
  assert.equal(reply.result.source, null);
  assert.equal(reply.result.state, 'pcode_only');
  assert.equal(reply.result.selection.score, null);
  assert.ok(reply.result.selection.candidates.every((c) => c.score === null && c.ok));
  assert.equal(asked(w, 'native').at(-1).args.engine, 'jpexs', 'JPEXS was tried before giving up');
});

test('engines that fail are listed with their reason and do not stop the others', async (t) => {
  const w = await opened(t, cast({ rust: 'FAIL', native: CLEAN, js: 'FAIL', jpexs: CLEAN }));
  const reply = await w.harness.call('decompile', { unit_id: UNIT });
  assert.equal(reply.served_by, 'native');
  const byEngine = Object.fromEntries(reply.result.selection.candidates.map((c) => [c.engine, c]));
  assert.deepEqual([byEngine.rust.ok, byEngine.rust.kind, byEngine.rust.error], [false, 'failed', 'el motor falló']);
  assert.equal(byEngine.js.ok, false);
  assert.equal(byEngine.native.score, 100);
  assert.deepEqual(reply.attempts.map((a) => [a.engine, a.ok]), [['rust', false], ['native', true], ['js', false]]);
});

test('when every engine fails the error carries every attempt', async (t) => {
  const w = await opened(t, cast({ rust: 'FAIL', native: 'FAIL', js: 'FAIL', jpexs: 'FAIL' }));
  await assert.rejects(w.harness.call('decompile', { unit_id: UNIT }), (e) => {
    assert.equal(e.kind, 'failed');
    assert.equal(e.message, 'el motor falló');
    assert.deepEqual(e.attempts.map((a) => a.engine), ['rust', 'native', 'js', 'jpexs'], 'JPEXS is the last resort and is tried too');
    return true;
  });
});

test('the engines are asked at the same time, not one after another', async (t) => {
  const slow = (source) => ({ byUnit: { [UNIT]: { sleep: 500, result: { unit_id: UNIT, state: 'structured', source, pcode: '', diagnostics: [] } } } });
  const w = await opened(t, standardWorld({ rust: rust({ decompile: slow(CLEAN) }), native: native({ decompile: slow(CLEAN) }), js: js({ decompile: slow(CLEAN) }) }));
  const started = Date.now();
  const reply = await w.harness.call('decompile', { unit_id: UNIT });
  const took = Date.now() - started;
  assert.ok(took < 1200, `three 500 ms engines took ${took} ms`);
  assert.equal(reply.served_by, 'rust');
  const starts = ['rust', 'native', 'js'].map((id) => asked(w, id)[0].t);
  assert.ok(Math.max(...starts) - Math.min(...starts) < 400, 'all three requests began together');
});

test('an engine that hangs is dropped from the vote at its deadline and the rest decide', async (t) => {
  const scratch = tempDir('marker-');
  t.after(() => removeDir(scratch));
  const hang = { byUnit: { [UNIT]: { hangOnce: path.join(scratch, 'hung'), result: { unit_id: UNIT, state: 'structured', source: CLEAN, pcode: '', diagnostics: [] } } } };
  const w = await opened(t, standardWorld({ rust: rust({ decompile: hang }) }), { env: { FLASH_AUTO_TIMEOUT_SECONDS: '1' } });
  const started = Date.now();
  const reply = await w.harness.call('decompile', { unit_id: UNIT });
  assert.ok(Date.now() - started < 5000);
  assert.equal(reply.served_by, 'native');
  const rustCandidate = reply.result.selection.candidates.find((c) => c.engine === 'rust');
  assert.deepEqual([rustCandidate.ok, rustCandidate.kind], [false, 'timeout']);
  const again = await w.harness.call('decompile', { unit_id: UNIT });
  assert.equal(again.served_by, 'rust', 'RustSWF restarted and is back in the vote');
});

test('the engines argument limits the vote; an explicit engine skips it', async (t) => {
  const w = await opened(t, cast({ rust: HARD, native: CLEAN, js: CLEAN, jpexs: CLEAN }));
  const limited = await w.harness.call('decompile', { unit_id: UNIT, engines: ['rust', 'js', 'pyswf-no'] });
  assert.deepEqual(limited.result.selection.candidates.map((c) => c.engine), ['rust', 'js']);
  assert.equal(limited.served_by, 'js');
  const alias = await w.harness.call('decompile', { unit_id: UNIT, engines: ['python', 'rustswf'], include_jpexs: 'never' });
  assert.deepEqual(alias.result.selection.candidates.map((c) => c.engine), ['rust', 'native']);
  const explicit = await w.harness.call('decompile', { unit_id: UNIT, engine: 'rust' });
  assert.equal(explicit.served_by, 'rust');
  assert.equal(explicit.result.selection, undefined);
  assert.equal(explicit.result.source, HARD);
});

test('classes go only to the engines that can decompile a whole class', async (t) => {
  const classDoc = (source) => ({
    result: { document_id: 'tag:1/class:0', kind: 'class', scope: 'class', completeness: 'complete', unit_ids: [UNIT], source, editable: false, revision: 'r', diagnostics: [] },
  });
  const classSpec = (source) => ({ byUnit: { 'tag:1/class:0': { result: { unit_id: 'tag:1/class:0', state: source === null ? 'pcode_only' : 'structured', source, pcode: '', diagnostics: [], scope: 'class' } } } });
  const world = standardWorld({
    rust: rust({ decompile: classSpec(null) }),
    native: native({ code_documents: classDoc('package app { public class Model {} }'), decompile: decompileSpec(CLEAN) }),
  });
  const w = await opened(t, world);
  const reply = await w.harness.call('decompile', { unit_id: 'tag:1/class:0' });
  assert.equal(reply.served_by, 'native');
  assert.equal(reply.result.source, 'package app { public class Model {} }');
  assert.equal(reply.result.scope, 'class');
  assert.equal(reply.result.state, 'structured');
  assert.ok(reply.result.pcode.includes('; ---- tag:1/method:0'), 'the class P-code lists its methods');
  const skipped = reply.attempts.find((a) => a.engine === 'js');
  assert.deepEqual([skipped.ok, skipped.kind], [false, 'unsupported']);
  assert.equal(asked(w, 'js').length, 0, 'JsSWF was never asked about a class');
  assert.equal(reply.result.selection.candidates.some((c) => c.engine === 'jpexs'), false, 'JPEXS delivers methods, not classes');
});

test('disassemble has no vote: the chain answers, an explicit engine is honored', async (t) => {
  const w = await opened(t, cast({ rust: CLEAN, native: CLEAN, js: CLEAN, jpexs: CLEAN }));
  const chain = await w.harness.call('disassemble', { unit_id: UNIT });
  assert.equal(chain.served_by, 'rust');
  assert.equal(chain.result.selection, undefined);
  const viaNative = await w.harness.call('disassemble', { unit_id: UNIT, engine: 'native' });
  assert.equal(viaNative.served_by, 'native');
  assert.equal(viaNative.result.derived, true, 'PySWF has no disassemble call: its P-code comes with decompile');
  assert.equal(viaNative.result.pcode, 'getlocal_0');
  const viaJs = await w.harness.call('disassemble', { unit_id: UNIT, engine: 'js' });
  assert.equal(viaJs.result.pcode, 'js pcode');
});
