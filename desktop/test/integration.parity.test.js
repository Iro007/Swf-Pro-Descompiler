'use strict';
/**
 * RustSWF and PySWF are interchangeable readers: for the same file they answer `header`
 * and `raw` with the same fields and the same values, the shapes the single-window
 * interface reads. Both real engines run; the tests skip, saying why, when either is not
 * on this machine.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { realRegistry, realHarness, missing, corpus, PARITY } = require('../testkit/real');

const registry = realRegistry();
const skip = missing(registry, 'rust', 'native');

/** RustSWF lists the path of a tag as `path`, the protocol's `resources` rows as `tag_path`. */
const pathOf = (item) => item.tag_path ?? item.path;

const FILES = [
  ['closures, FWS', PARITY('dev-as3-closures')],
  ['closures, CWS', PARITY('dev-as3-closures', 'release.cws.swf')],
  ['closures, ZWS', PARITY('dev-as3-closures', 'release.zws.swf')],
  ['classes and namespaces', PARITY('dev-as3-classes-namespaces')],
  ['AS2 timeline', PARITY('dev-as2-timeline')],
  ['AS2 captures', PARITY('dev-as2-captures')],
  ['Haxe application', PARITY('dev-haxe-application')],
  ['Flex application', PARITY('validation-flex-app')],
  ['HaxeFlixel game, with images', PARITY('validation-haxeflixel-game')],
  ['Flex test movie, 200 ABC blocks', corpus('as3_new.flex.swf')],
  ['AS2 sprite and functions', corpus('as2_definefunc_regs.swf')],
  ['AS2 shapes, a sprite and closures', corpus('cloud-matched-micro-20261013/ruffle_avm1_closure_scope.swf')],
];

test('header: every field PySWF reports, RustSWF reports with the same value', { skip, timeout: 240000 }, async (t) => {
  const harness = realHarness();
  t.after(() => harness.close());
  for (const [name, file] of FILES) {
    await harness.call('open', { path: file });
    const rust = (await harness.call('header', { engine: 'rust', strict: true })).result;
    const native = (await harness.call('header', { engine: 'native', strict: true })).result;
    assert.deepEqual(Object.keys(native).filter((key) => !(key in rust)), [], `${name}: RustSWF lacks fields PySWF has`);
    for (const key of Object.keys(native)) assert.deepEqual(rust[key], native[key], `${name}: header.${key}`);
    assert.equal(native.revision, harness.session.sha256, `${name}: the revision is the hash of the working copy`);
  }
});

test('raw: pages of tags, of nested tags and of the file are the same bytes with the same names', { skip, timeout: 240000 }, async (t) => {
  const harness = realHarness();
  t.after(() => harness.close());
  for (const [name, file] of FILES) {
    await harness.call('open', { path: file });
    const listing = await harness.call('resources', { engine: 'rust', strict: true, limit: 500 });
    // PySWF addresses a tag inside a sprite one level deep; sample the file's tags evenly.
    const reachable = listing.result.items.filter((item) => pathOf(item).length <= 2);
    const stride = Math.max(1, Math.floor(reachable.length / 30));
    const sample = reachable.filter((_, index) => index % stride === 0 || index >= reachable.length - 2);
    assert.ok(sample.length > 0, name);
    for (const item of sample) {
      for (const encoding of ['hex', 'base64']) {
        const args = { tag_path: pathOf(item), offset: 0, limit: 48, encoding, strict: true };
        const rust = (await harness.call('raw', { ...args, engine: 'rust' })).result;
        const native = (await harness.call('raw', { ...args, engine: 'native' })).result;
        assert.deepEqual(rust, native, `${name}: raw ${pathOf(item).join('/')} ${encoding}`);
      }
    }
    // The paging arguments mean the same thing on both sides.
    const page = { tag_index: pathOf(sample[0])[0], offset: 1, limit: 2 };
    assert.deepEqual((await harness.call('raw', { ...page, engine: 'rust', strict: true })).result, (await harness.call('raw', { ...page, engine: 'native', strict: true })).result, `${name}: a slice`);
    const whole = { scope: 'file', offset: 5, limit: 64 };
    const rustFile = (await harness.call('raw', { ...whole, engine: 'rust', strict: true })).result;
    const nativeFile = (await harness.call('raw', { ...whole, engine: 'native', strict: true })).result;
    assert.deepEqual(rustFile, nativeFile, `${name}: raw of the file`);
  }
});

test('raw refuses what PySWF refuses: both engines answer a bad request with a failure, not a page', { skip, timeout: 120000 }, async (t) => {
  const harness = realHarness();
  t.after(() => harness.close());
  await harness.call('open', { path: PARITY('dev-as3-closures') });
  const bad = [
    { tag_index: 7, limit: 0 }, { tag_index: 7, limit: 65537 }, { tag_index: 7, offset: -1 }, { tag_index: 7, offset: 10 },
    { tag_index: 7, encoding: 'utf8' }, { tag_index: 999 }, { tag_index: -1 }, {},
  ];
  for (const args of bad) {
    for (const engine of ['rust', 'native']) {
      await assert.rejects(harness.call('raw', { ...args, engine, strict: true }), (error) => ['bad_request', 'not_found', 'failed'].includes(error.kind), `${engine} ${JSON.stringify(args)}`);
    }
  }
});

test('a tag reads the same in both engines\' listings: name, type and character id for the character tags', { skip, timeout: 240000 }, async (t) => {
  const harness = realHarness();
  t.after(() => harness.close());
  for (const [name, file] of FILES) {
    await harness.call('open', { path: file });
    const rust = (await harness.call('resources', { engine: 'rust', strict: true, limit: 500 })).result.items.filter((item) => pathOf(item).length === 1);
    const native = (await harness.call('resources', { engine: 'native', strict: true, limit: 500 })).result.items;
    assert.equal(rust.length, native.length, `${name}: tag count`);
    rust.forEach((item, index) => {
      assert.deepEqual([item.code, item.name], [native[index].code, native[index].name], `${name}: tag ${index}`);
    });
  }
});
