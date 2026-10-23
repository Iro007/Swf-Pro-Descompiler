'use strict';
/**
 * The harness against the real RustSWF worker on the repository corpus. These tests
 * skip, saying why, when rustswf.exe has not been built (`.\build.ps1`, or
 * `cargo build -p swf-cli`): CI runs the rest of the suite without it.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { realRegistry, realHarness, missing, corpus, PARITY, REPO } = require('../testkit/real');
const { scanTags, TAG_NAMES } = require('../harness/swfheader');
const builtin = require('../harness/builtin');
const { swfBytes, tagBytes, isAlive, waitUntil } = require('../testkit');

const registry = realRegistry();
const skip = missing(registry, 'rust');

const CLOSURES = PARITY('dev-as3-closures');
const NAMESPACES = PARITY('dev-as3-classes-namespaces');
const AS2_TIMELINE = PARITY('dev-as2-timeline');

async function session(t, file, options) {
  const harness = realHarness(options);
  t.after(() => harness.close());
  const opened = await harness.call('open', { path: file });
  return { harness, opened };
}

test('RustSWF opens a document through the harness and describes it: header, tree, units', { skip }, async (t) => {
  const { harness, opened } = await session(t, CLOSURES);
  assert.equal(opened.served_by, 'rust');
  assert.equal(opened.result.info.filename, 'release.fws.swf', 'the original name, not the working copy\'s');
  assert.equal(opened.result.info.tag_count, 10);
  assert.equal(opened.revision, 0);
  assert.equal(opened.result.revision, opened.sha256, 'the editing interface reads the revision of the open document here');
  assert.deepEqual([opened.result.dirty, opened.result.history, opened.result.validation.tag_count], [false, { undo: false, redo: false }, 10]);

  const header = await harness.call('header', {});
  assert.equal(header.served_by, 'rust');
  assert.deepEqual(
    [header.result.signature, header.result.version, header.result.frame_size.width, header.result.frame_size.height, header.result.frame_rate, header.result.frame_count],
    ['FWS', 38, 500, 375, 24, 1],
  );
  assert.equal(header.result.filename, 'release.fws.swf');
  assert.deepEqual([header.result.tag_count, header.result.shown_frames, header.result.abc_blocks, header.result.script_count], [10, 1, 1, 5]);
  assert.equal(header.result.file_attributes.actionscript3, true);
  assert.equal(header.result.background_color, '#ffffff');
  assert.deepEqual(header.result.script_limits, { max_recursion_depth: 1000, script_timeout_seconds: 60 });
  assert.deepEqual([header.result.sha256, header.result.revision], [opened.sha256, opened.sha256]);

  const tree = await harness.call('tag_tree', { engine: 'rust', strict: true });
  assert.equal(tree.served_by, 'rust');
  assert.deepEqual(tree.result.nodes.map((n) => n.folder), ['header', 'frames', 'others', 'scripts']);
  const main = tree.result.nodes.find((n) => n.folder === 'scripts').children[0];
  assert.deepEqual([main.kind, main.label, main.unit_id, main.superclass], ['class', 'Main', 'tag:6/class:0', 'flash.display::Sprite']);

  const scripts = await harness.call('scripts', {});
  assert.equal(scripts.result.total, 5);
  assert.deepEqual(scripts.result.items.map((u) => u.language), Array(5).fill('as3'));
  assert.equal(scripts.result.items[0].engine_language, 'avm2');

  const raw = await harness.call('raw', { tag_index: 7, limit: 16 });
  assert.equal(raw.served_by, 'rust');
  assert.deepEqual([raw.result.scope, raw.result.name, raw.result.type, raw.result.tag_index, raw.result.length], ['tag', 'SymbolClass', 76, 7, 9]);
  assert.equal(raw.result.hex, '010000004d61696e00', 'one symbol, id 0, named Main');
  assert.equal(raw.result.revision, opened.sha256);
  const info = await harness.call('resource_info', { engine: 'rust', strict: true, tag_index: 7 });
  assert.deepEqual([info.result.kind, info.result.name], ['raw', 'SymbolClass']);
  const status = harness.listEngines().find((e) => e.id === 'rust').runtime;
  assert.equal(status.supervised, false);
  assert.equal(status.mismatch.missing.length + status.mismatch.extra.length, 0, 'the manifest and the worker declare the same operations');
});

test('classes decompile and disassemble as classes; methods keep working; the hierarchy contract is used when installed', { skip }, async (t) => {
  const { harness } = await session(t, NAMESPACES);
  const listing = await harness.call('disassemble', { unit_id: 'tag:6/class:1', engine: 'rust' });
  assert.equal(listing.served_by, 'rust');
  assert.equal(listing.result.scope, 'class');
  assert.match(listing.result.pcode, /; ---- app::Model constructor \(tag:6\/method:4\) ----/);
  const klass = await harness.call('decompile', { unit_id: 'tag:6/class:1', engine: 'rust' });
  assert.equal(klass.result.scope, 'class');
  assert.equal(klass.result.engine, 'rust');
  assert.match(klass.result.pcode, /app::Model/);
  const method = await harness.call('decompile', { unit_id: 'tag:6/method:5', engine: 'rust' });
  assert.equal(method.result.engine, 'rust');
  assert.equal(method.result.unit_id, 'tag:6/method:5');
  assert.match(method.result.pcode, /getproperty/);
  const contract = harness.hierarchyContract();
  if (contract) assert.equal(contract.hierarchy_sha256.length, 64);
});

test('an AS2 timeline lists its frame script and decompiles it', { skip }, async (t) => {
  const { harness } = await session(t, AS2_TIMELINE);
  const tree = await harness.call('tag_tree', { engine: 'rust', strict: true });
  const scripts = tree.result.nodes.find((n) => n.folder === 'scripts');
  assert.deepEqual([scripts.children[0].kind, scripts.children[0].frame, scripts.children[0].children[0].unit_id], ['frame', 1, 'tag:2/actions']);
  const auto = await harness.call('decompile', { unit_id: 'tag:2/actions' });
  assert.ok(['rust', 'native', 'js', 'go', 'asm'].includes(auto.served_by));
  assert.equal(auto.result.selection.strategy, 'auto');
  assert.ok(auto.result.pcode.length > 0);
});

test('the harness reads files like RustSWF does: tag lists, names, ids, symbols, header and raw bytes agree', { skip }, async (t) => {
  const files = [
    CLOSURES, NAMESPACES, AS2_TIMELINE, PARITY('dev-as2-captures'), PARITY('dev-haxe-application'), PARITY('validation-flex-app'),
    PARITY('dev-as3-classes-namespaces', 'release.cws.swf'), corpus('as2_definefunc_regs.swf'), corpus('as3_new.flex.swf'), corpus('as3_cross_compile.flex.swf'),
    corpus('cloud-matched-micro-20261013/ruffle_avm1_closure_scope.swf'),
  ];
  const harness = realHarness();
  t.after(() => harness.close());
  for (const file of files) {
    await harness.call('open', { path: file });
    const name = path.basename(path.dirname(file)) + '/' + path.basename(file);
    // Tags: Rust's resource listing against the scanner.
    const rustTags = [];
    for (let offset = 0; ; ) {
      const page = await harness.call('resources', { engine: 'rust', strict: true, offset, limit: 500 });
      rustTags.push(...page.result.items);
      offset += page.result.items.length;
      if (offset >= page.result.total || page.result.items.length === 0) break;
    }
    const mine = scanTags(fs.readFileSync(file));
    assert.equal(mine.tags.length, rustTags.length, `${name}: tag count`);
    mine.tags.forEach((tag, i) => {
      const theirs = rustTags[i];
      assert.deepEqual(
        [tag.path, tag.code, tag.name, tag.length, tag.character_id, tag.symbol],
        [theirs.tag_path ?? theirs.path, theirs.code, theirs.name, theirs.length, theirs.character_id, theirs.symbol],
        `${name}: tag ${i}`,
      );
    });
    // Header: everything the harness can read from the file, the way RustSWF reports it.
    const rustHeader = (await harness.call('header', { engine: 'rust', strict: true })).result;
    const header = builtin.header({ workingPath: harness.session.workingPath });
    for (const key of ['signature', 'compression', 'version', 'file_length', 'file_size', 'frame_size', 'frame_rate', 'frame_count', 'shown_frames', 'tag_count', 'abc_blocks', 'has_as3', 'has_metadata', 'background_color', 'file_attributes', 'script_limits', 'metadata', 'sha256', 'revision']) {
      assert.deepEqual(header[key], rustHeader[key], `${name}: header.${key}`);
    }
    // Raw bytes: first, last and a nested tag, as hex and as base64.
    for (const tag of [mine.tags[0], mine.tags.at(-2), mine.tags.find((x) => x.path.length > 1)].filter(Boolean)) {
      for (const encoding of ['hex', 'base64']) {
        const args = { tag_path: tag.path, offset: 0, limit: 64, encoding };
        const theirs = (await harness.call('raw', { ...args, engine: 'rust', strict: true })).result;
        const own = builtin.raw({ workingPath: harness.session.workingPath, sha256: harness.session.sha256 }, args);
        assert.deepEqual(own, { ...theirs, derived: true }, `${name}: raw ${tag.path} ${encoding}`);
      }
    }
    const whole = { scope: 'file', offset: 3, limit: 40 };
    const theirs = (await harness.call('raw', { ...whole, engine: 'rust', strict: true })).result;
    assert.deepEqual(builtin.raw({ workingPath: harness.session.workingPath, sha256: harness.session.sha256 }, whole), { ...theirs, derived: true }, `${name}: raw of the file`);
  }
});

test('ZWS files: RustSWF reads them, the harness reports the partial header it can', { skip }, async (t) => {
  const file = PARITY('dev-as3-closures', 'release.zws.swf');
  const { harness } = await session(t, file);
  const rust = (await harness.call('header', { engine: 'rust', strict: true })).result;
  assert.equal(rust.signature, 'ZWS');
  const own = builtin.header({ workingPath: harness.session.workingPath });
  assert.deepEqual([own.signature, own.version, own.file_length, own.frame_size], ['ZWS', rust.version, rust.file_length, null]);
  assert.equal(own.diagnostics[0].code, 'header_partial');
  assert.throws(() => scanTags(fs.readFileSync(file)), (e) => e.kind === 'unsupported');
});

test('every tag name the harness knows is the name RustSWF reports', { skip }, async (t) => {
  const codes = Object.keys(TAG_NAMES).map(Number).filter((code) => code !== 0 && code !== 39);
  const tags = codes.map((code) => tagBytes(code, []));
  const dir = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'flash-names-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'names.swf');
  fs.writeFileSync(file, swfBytes([...tags, tagBytes(404, [])]));
  const { harness } = await session(t, file);
  const reply = await harness.call('resources', { engine: 'rust', strict: true, limit: 500 });
  const names = new Map(reply.result.items.map((item) => [item.code, item.name]));
  for (const code of codes) assert.equal(names.get(code), TAG_NAMES[code], `tag ${code}`);
  assert.equal(names.get(404), 'Unknown_404');
});

test('workers run under the supervisor: memory cap and kill-on-close, and nothing outlives the harness', { skip }, async (t) => {
  const { harness } = await session(t, CLOSURES, { supervise: true });
  const status = harness.listEngines().find((e) => e.id === 'rust').runtime;
  assert.equal(status.supervised, true);
  assert.equal(status.memory_mib, 1024);
  assert.equal((await harness.call('header', {})).result.version, 38);
  const supervisor = harness.engines.get('rust').worker.pid;
  const children = spawnSync('powershell.exe', ['-NoProfile', '-Command', `(Get-CimInstance Win32_Process -Filter "ParentProcessId=${supervisor}").ProcessId`], { encoding: 'utf8' });
  const worker = Number(children.stdout.trim().split(/\s+/)[0]);
  assert.ok(worker > 0, 'the supervisor started the real worker');
  await harness.close();
  assert.ok(await waitUntil(() => !isAlive(supervisor) && !isAlive(worker), { timeoutMs: 8000 }), 'supervisor and worker are gone');
});

test('the supervisor\'s Job Object memory cap stops a worker that grows without bound', { skip }, () => {
  const supervisor = registry.supervisorPath;
  const hog = 'const hold=[];for(let i=0;i<64;i++){hold.push(Buffer.alloc(16*1024*1024,1));}console.log("survived")';
  const run = spawnSync(supervisor, ['supervise', '--memory-mib', '128', '--', process.execPath, '-e', hog], { encoding: 'utf8', timeout: 60000 });
  assert.notEqual(run.status, 0, 'the process was stopped');
  assert.doesNotMatch(run.stdout, /survived/);
  const polite = spawnSync(supervisor, ['supervise', '--memory-mib', '512', '--', process.execPath, '-e', 'console.log("fine")'], { encoding: 'utf8', timeout: 60000 });
  assert.equal(polite.status, 0);
  assert.match(polite.stdout, /fine/);
  void REPO;
});
