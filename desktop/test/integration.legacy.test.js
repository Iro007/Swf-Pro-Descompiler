'use strict';
/**
 * JsSWF, GoSWF and AsmSWF behind the legacy adapter, run for real. JsSWF is plain
 * Node and always available; GoSWF and AsmSWF need their executables
 * (`engines/goswf/build.ps1`, `engines/asmswf/build.ps1`) and skip without them.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { realRegistry, realHarness, missing, PARITY } = require('../testkit/real');

const registry = realRegistry();
const SWF = PARITY('dev-as3-classes-namespaces');
const AS2 = PARITY('dev-as2-timeline');
const UNIT = 'tag:6/method:5';

for (const id of ['js', 'go', 'asm']) {
  const skip = missing(registry, id);

  test(`${id}: opens, reads the header and lists, disassembles and decompiles units like any engine`, { skip, timeout: 60000 }, async (t) => {
    const harness = realHarness();
    t.after(() => harness.close());
    // The legacy engine is asked explicitly, so the document is opened in it (not in RustSWF).
    await harness.call('open', { path: SWF });
    const header = await harness.call('header', { engine: id, strict: true });
    assert.equal(header.served_by, id);
    assert.deepEqual([header.result.signature, header.result.version, header.result.frame_size.width, header.result.frame_size.height], ['FWS', 38, 500, 375]);
    assert.equal(header.result.derived, true);
    const scripts = await harness.call('scripts', { engine: id, strict: true });
    assert.equal(scripts.result.total, 11);
    const unit = scripts.result.items.find((u) => u.id === UNIT);
    assert.deepEqual([unit.language, unit.engine_language, unit.kind], ['as3', 'avm2', 'method']);
    assert.ok(unit.name.length > 0);
    const listing = await harness.call('disassemble', { unit_id: UNIT, engine: id, strict: true });
    assert.equal(listing.result.state, 'decoded');
    assert.match(listing.result.pcode, /getproperty/);
    const source = await harness.call('decompile', { unit_id: UNIT, engine: id, strict: true });
    assert.equal(source.served_by, id);
    assert.equal(source.result.state, 'structured');
    assert.match(source.result.source, /total/);
    assert.equal(source.result.engine, id);
    await assert.rejects(harness.call('decompile', { unit_id: 'tag:6/class:1', engine: id, strict: true }), (e) => e.kind === 'unsupported');
    const caps = await harness.call('capabilities', { engine: id });
    assert.deepEqual(caps.result.ops, ['capabilities', 'open', 'header', 'scripts', 'disassemble', 'decompile']);
    assert.equal(caps.result.adapted, true);
  });

  test(`${id}: AVM1 units come through with the same ids RustSWF uses`, { skip: skip || missing(registry, 'rust'), timeout: 60000 }, async (t) => {
    const harness = realHarness();
    t.after(() => harness.close());
    await harness.call('open', { path: AS2 });
    const mine = await harness.call('scripts', { engine: id, strict: true });
    const theirs = await harness.call('scripts', { engine: 'rust' });
    assert.deepEqual(mine.result.items.map((u) => u.id), theirs.result.items.map((u) => u.id));
    assert.equal(mine.result.items[0].language, 'as2');
    const source = await harness.call('decompile', { unit_id: mine.result.items[0].id, engine: id, strict: true });
    assert.ok(source.result.pcode.length > 0);
  });
}

test('the vote across every engine that is built: one candidate each, a score each, the winner is the best', { skip: missing(registry, 'js'), timeout: 90000 }, async (t) => {
  const harness = realHarness();
  t.after(() => harness.close());
  await harness.call('open', { path: SWF });
  const reply = await harness.call('decompile', { unit_id: UNIT });
  const { selection } = reply.result;
  const ids = selection.candidates.map((c) => c.engine);
  assert.ok(ids.includes('js'));
  assert.equal(new Set(ids).size, ids.length);
  for (const candidate of selection.candidates.filter((c) => c.ok && c.score !== null)) {
    assert.ok(Number.isInteger(candidate.score));
    assert.ok(candidate.score <= selection.score.value);
  }
  assert.equal(reply.served_by, selection.engine);
  assert.equal(reply.result.engine, selection.engine);
  const best = Math.max(...selection.candidates.filter((c) => c.score !== null).map((c) => c.score));
  assert.equal(selection.score.value, best);
  const tied = selection.candidates.filter((c) => c.score === best);
  assert.equal(selection.engine, tied[0].engine, 'a tie goes to the first in manifest order');
});
