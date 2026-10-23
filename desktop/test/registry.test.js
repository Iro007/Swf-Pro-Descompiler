'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Registry, candidates, which } = require('../harness/registry');
const { Protocol } = require('../harness/protocol');
const { tempDir, removeDir, REAL_PROTOCOL } = require('../testkit');

const REPO = path.resolve(__dirname, '..', '..');
const QUIET_ENV = { PATH: '' };

function repoRegistry(extra = {}) {
  return Registry.load({ roots: [REPO], env: QUIET_ENV, electron: false, ...extra });
}

/** A throwaway workspace whose manifests are written by the test. */
function workspace(manifests, files = []) {
  const root = tempDir('flash-registry-');
  fs.mkdirSync(path.join(root, 'engines'), { recursive: true });
  fs.copyFileSync(REAL_PROTOCOL, path.join(root, 'engines', 'protocol.json'));
  for (const manifest of manifests) {
    const folder = path.join(root, 'engines', manifest.folder || manifest.id);
    fs.mkdirSync(folder, { recursive: true });
    const { folder: _folder, ...body } = manifest;
    fs.writeFileSync(path.join(folder, 'engine.json'), JSON.stringify({ order: 10, name: body.id, category: 'decompiler', ...body }));
  }
  for (const file of files) {
    fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    fs.writeFileSync(path.join(root, file), 'x');
  }
  return { root, cleanup: () => removeDir(root) };
}

test('the repository manifests load without problems and keep the documented order', () => {
  const registry = repoRegistry();
  assert.deepEqual(registry.problems, []);
  assert.equal(registry.engines.size, 19);
  assert.deepEqual([...registry.engines.keys()].slice(0, 6), ['rust', 'native', 'js', 'go', 'asm', 'jpexs']);
  for (const info of registry.workers()) assert.deepEqual(info.problems, [], info.id);
});

test('levels: RustSWF and PySWF are the parity targets, JsSWF/GoSWF/AsmSWF cover L3 and part of L1', () => {
  const registry = repoRegistry();
  const levels = (id) => {
    const out = {};
    for (const [level, v] of Object.entries(registry.engine(id).levels)) out[level] = [v.have.length, v.total, v.complete];
    return out;
  };
  assert.deepEqual(levels('rust'), { L1: [6, 6, true], L2: [6, 8, false], L3: [3, 3, true], L4: [0, 6, false], L5: [0, 6, false] });
  assert.deepEqual(registry.engine('rust').levels.L2.missing, ['resource_preview', 'sprite_frames']);
  assert.deepEqual(levels('native'), { L1: [6, 6, true], L2: [3, 8, false], L3: [3, 3, true], L4: [6, 6, true], L5: [3, 6, false] });
  assert.deepEqual(registry.engine('native').levels.L2.missing, ['extract_image', 'extract_sound', 'extract_font', 'extract_text', 'shape_svg']);
  for (const id of ['js', 'go', 'asm']) {
    assert.equal(registry.engine(id).levels.L3.complete, true, `${id} L3`);
    assert.deepEqual(registry.engine(id).levels.L1.missing, ['tag_tree', 'raw', 'resources'], `${id} L1`);
    assert.equal(registry.engine(id).levels.L4.have.length, 0);
    assert.equal(registry.engine(id).worker.framing, 'legacy');
  }
  assert.deepEqual([...registry.engine('jpexs').worker.ops], ['decompile', 'compile']);
  // The summary shows the same thing as one line per engine.
  const line = registry.summary().find((e) => e.id === 'rust').levels_line;
  assert.equal(line, 'L1 6/6  L2 6/8  L3 3/3  L4 0/6  L5 0/6');
});

test('a level is complete only when every one of its operations is present', () => {
  const protocol = Protocol.load([REPO]);
  const all = protocol.levels.L3.ops;
  assert.equal(protocol.levelsFor(all).L3.complete, true);
  assert.equal(protocol.levelsFor(all.slice(1)).L3.complete, false);
  assert.deepEqual(protocol.levelsFor(all.slice(1)).L3.missing, [all[0]]);
  assert.equal(protocol.levelsFor([]).L1.complete, false);
  assert.equal(Object.keys(protocol.levelsFor([])).join(), 'L1,L2,L3,L4,L5');
});

test('every manifest operation belongs to the protocol and native operations never shadow a canonical one', () => {
  const registry = repoRegistry();
  const canonical = new Set(registry.protocol.canonicalOps);
  for (const info of registry.workers()) {
    for (const op of info.worker.ops) assert.ok(canonical.has(op), `${info.id}.${op}`);
    for (const op of info.worker.nativeOps.keys()) assert.ok(!canonical.has(op), `${info.id} native ${op}`);
  }
  assert.ok(registry.allowedOps().has('header'));
  assert.ok(registry.allowedOps().has('compile_project'), 'PySWF-only operations stay reachable');
  assert.ok(registry.allowedOps().has('info'), 'RustSWF-only operations stay reachable');
  assert.ok(!registry.allowedOps().has('frobnicate'));
});

test('operation specs carry the timeout class and whether the operation changes the document', () => {
  const registry = repoRegistry();
  assert.deepEqual(
    (({ canonical, level, timeout, mutates, document }) => ({ canonical, level, timeout, mutates, document }))(registry.opSpec('compile')),
    { canonical: true, level: 'L4', timeout: 'compile', mutates: true, document: true },
  );
  assert.equal(registry.opSpec('capabilities').document, false);
  assert.equal(registry.opSpec('header').mutates, false);
  const project = registry.opSpec('compile_project');
  assert.deepEqual([project.canonical, project.timeout, project.mutates, project.owners], [false, 'compile', true, ['native']]);
  assert.equal(registry.opSpec('state').mutates, false);
  assert.equal(registry.opSpec('ping').document, false, 'tool catalogue operations need no open document');
  assert.equal(registry.opSpec('edit').document, true);
  assert.equal(registry.opSpec('frobnicate'), undefined);
  assert.deepEqual(registry.opSpec('info').owners, ['rust', 'js', 'go', 'asm'], 'every engine with an info call, in manifest order');
});

test('providers: the fallback chain first, in chain order, then the other engines by manifest order', () => {
  const registry = repoRegistry();
  const decompile = registry.providers('decompile');
  assert.deepEqual(decompile.chain.map((e) => e.id), ['rust', 'native', 'jpexs']);
  assert.deepEqual(decompile.extras.map((e) => e.id), ['js', 'go', 'asm']);
  assert.deepEqual(registry.providers('undo').chain.map((e) => e.id), ['native']);
  assert.deepEqual(registry.providers('raw').chain.map((e) => e.id), ['rust', 'native']);
  assert.deepEqual(registry.providers('export_svg'), { chain: [], extras: [] });
  assert.equal(registry.normalizeId('python'), 'native');
  assert.equal(registry.normalizeId('RustSWF'), 'rust');
  assert.equal(registry.engine('pyswf').id, 'native');
});

test('providers: the session owner goes first for the operations protocol.json says the editing session depends on', () => {
  const registry = repoRegistry();
  assert.deepEqual([...registry.protocol.preferAuthority], ['tag_tree', 'resource_info']);
  assert.deepEqual(registry.providers('tag_tree').chain.map((e) => e.id), ['native', 'rust']);
  assert.deepEqual(registry.providers('resource_info').chain.map((e) => e.id), ['native', 'rust']);
  // Everything else keeps the chain order, RustSWF first.
  assert.deepEqual(registry.providers('header').chain.map((e) => e.id), ['rust', 'native']);
  assert.deepEqual(registry.providers('open').chain.map((e) => e.id), ['rust', 'native']);
  assert.deepEqual(registry.opSpec('tag_tree').owners, ['native', 'rust']);
});

test('third-party tools are listed as tools; proxies follow the engine that serves them', () => {
  const registry = repoRegistry();
  const byId = Object.fromEntries(registry.summary().map((e) => [e.id, e]));
  assert.equal(byId.sothink.kind, 'tool');
  assert.equal(byId.sothink.available, null);
  assert.equal(byId.jpexs.kind, 'proxy');
  assert.equal(byId.jpexs.via, 'native');
  assert.equal(byId.jpexs.available, byId.native.available);
  assert.equal(byId.native.authority, true);
  assert.deepEqual(byId.native.derived_ops, ['disassemble']);
  assert.equal(byId.sothink.levels.L1.have, 0);
});

test('command resolution: the packaged layout wins, then the development build, release before debug', () => {
  const manifest = {
    id: 'rust',
    worker: {
      framing: 'v2',
      command: ['bin/rustswf.exe', 'worker'],
      dev_command: [['target/x86_64-pc-windows-gnu/release/rustswf.exe', 'worker'], ['target/x86_64-pc-windows-gnu/debug/rustswf.exe', 'worker']],
      supervisor: true,
      ops: ['capabilities', 'open'],
    },
  };
  const packaged = workspace([manifest], ['bin/rustswf.exe', 'target/x86_64-pc-windows-gnu/release/rustswf.exe']);
  const dev = workspace([manifest], ['target/x86_64-pc-windows-gnu/release/rustswf.exe', 'target/x86_64-pc-windows-gnu/debug/rustswf.exe']);
  const debugOnly = workspace([manifest], ['target/x86_64-pc-windows-gnu/debug/rustswf.exe']);
  const none = workspace([manifest], []);
  try {
    const a = Registry.load({ roots: [packaged.root], env: QUIET_ENV });
    assert.equal(a.engine('rust').launch.source, 'command');
    assert.equal(a.engine('rust').launch.command, path.join(packaged.root, 'bin', 'rustswf.exe'));
    assert.deepEqual(a.engine('rust').launch.args, ['worker']);
    assert.equal(a.supervisorPath, a.engine('rust').launch.command);
    const b = Registry.load({ roots: [dev.root], env: QUIET_ENV });
    assert.equal(b.engine('rust').launch.source, 'dev_command');
    assert.match(b.engine('rust').launch.command, /release[\\/]rustswf\.exe$/);
    const c = Registry.load({ roots: [debugOnly.root], env: QUIET_ENV });
    assert.match(c.engine('rust').launch.command, /debug[\\/]rustswf\.exe$/);
    const d = Registry.load({ roots: [none.root], env: QUIET_ENV });
    assert.equal(d.engine('rust').available, false);
    assert.match(d.engine('rust').reason, /bin[\\/]rustswf\.exe/);
    assert.match(d.engine('rust').reason, /release[\\/]rustswf\.exe/);
    assert.equal(d.supervisorPath, null);
    // Several roots: the application's own files come first.
    const both = Registry.load({ roots: [packaged.root, dev.root], env: QUIET_ENV });
    assert.equal(both.engine('rust').launch.root, packaged.root);
    const fallback = Registry.load({ roots: [none.root, dev.root], env: QUIET_ENV });
    assert.equal(fallback.engine('rust').launch.root, dev.root, 'an extra root (FLASH_ENGINE_ROOTS) can supply the binary');
  } finally {
    for (const w of [packaged, dev, debugOnly, none]) w.cleanup();
  }
});

test('{node} is the running Node, with ELECTRON_RUN_AS_NODE only inside Electron; scripts resolve under the root that has them', () => {
  const manifest = { id: 'js', worker: { framing: 'legacy', command: ['{node}', 'engines/jsswf/src/cli.mjs', 'worker'], ops: ['open'] } };
  const withScript = workspace([manifest], ['engines/jsswf/src/cli.mjs']);
  const without = workspace([manifest], []);
  try {
    const plain = Registry.load({ roots: [without.root, withScript.root], env: QUIET_ENV, nodePath: 'C:\\node\\node.exe', electron: false });
    const launch = plain.engine('js').launch;
    assert.equal(launch.command, 'C:\\node\\node.exe');
    assert.deepEqual(launch.args, [path.join(withScript.root, 'engines', 'jsswf', 'src', 'cli.mjs'), 'worker']);
    assert.equal(launch.root, withScript.root);
    assert.equal(launch.env.ELECTRON_RUN_AS_NODE, undefined);
    const electron = Registry.load({ roots: [withScript.root], env: QUIET_ENV, nodePath: 'C:\\app\\Flash.exe', electron: true });
    assert.equal(electron.engine('js').launch.env.ELECTRON_RUN_AS_NODE, '1');
    const missing = Registry.load({ roots: [without.root], env: QUIET_ENV });
    assert.equal(missing.engine('js').available, false);
    assert.match(missing.engine('js').reason, /cli\.mjs/);
  } finally {
    withScript.cleanup();
    without.cleanup();
  }
});

test('environment placeholders and the working directory follow the root that supplied the command', () => {
  const manifest = {
    id: 'native',
    worker: {
      framing: 'suite',
      command: ['bin/FlashSuiteWorker.exe', 'worker'],
      dev_command: ['engines/pyswf/.venv/Scripts/python.exe', '-m', 'py_swf.universal_worker', 'worker'],
      cwd: 'engines/pyswf',
      env: { FLASH_SUITE_WORKSPACE: '{workspace}', FLASH_SUITE_STATE_DIR: '{state_dir}', LOOKED_IN: '{root}' },
      ops: ['open'],
    },
  };
  const dev = workspace([manifest], ['engines/pyswf/.venv/Scripts/python.exe', 'engines/pyswf/py_swf/universal_worker.py']);
  const packaged = workspace([manifest], ['bin/FlashSuiteWorker.exe']);
  try {
    const a = Registry.load({ roots: [dev.root], env: QUIET_ENV });
    const launch = a.engine('native').launch;
    assert.equal(launch.source, 'dev_command');
    assert.deepEqual(launch.args, ['-m', 'py_swf.universal_worker', 'worker']);
    assert.equal(launch.cwd, path.join(dev.root, 'engines', 'pyswf'));
    assert.equal(launch.env.FLASH_SUITE_WORKSPACE, dev.root);
    assert.equal(launch.env.LOOKED_IN, dev.root);
    assert.equal(launch.env.FLASH_SUITE_STATE_DIR, '{state_dir}', 'the session fills this one in');
    const b = Registry.load({ roots: [packaged.root], env: QUIET_ENV });
    assert.equal(b.engine('native').launch.cwd, path.join(packaged.root, 'bin'), 'no source tree: run beside the executable');
    const forced = Registry.load({ roots: [packaged.root], env: { ...QUIET_ENV, FLASH_SUITE_WORKSPACE: 'D:\\toolbox' } });
    assert.equal(forced.engine('native').launch.env.FLASH_SUITE_WORKSPACE, 'D:\\toolbox');
  } finally {
    dev.cleanup();
    packaged.cleanup();
  }
});

test('a bare command name is looked up on PATH', () => {
  const tools = tempDir('flash-path-');
  try {
    fs.writeFileSync(path.join(tools, 'mytool.exe'), 'x');
    const env = { PATH: tools, PATHEXT: '.EXE;.CMD' };
    assert.equal(which('mytool', env, 'win32').toLowerCase(), path.join(tools, 'mytool.exe').toLowerCase());
    assert.equal(which('nothing', env, 'win32'), null);
    const w = workspace([{ id: 'tool', worker: { command: ['mytool', 'worker'], ops: ['open'] } }]);
    try {
      const registry = Registry.load({ roots: [w.root], env });
      assert.equal(registry.engine('tool').launch.command.toLowerCase(), path.join(tools, 'mytool.exe').toLowerCase());
      assert.equal(Registry.load({ roots: [w.root], env: { PATH: '' } }).engine('tool').available, false);
    } finally {
      w.cleanup();
    }
  } finally {
    removeDir(tools);
  }
});

test('broken manifests are reported, not fatal: bad JSON, duplicate ids, unknown operations or framing', () => {
  const w = workspace([
    { id: 'a', folder: 'a1', order: 1, worker: { command: ['x.exe'], ops: ['open', 'teleport'], native_ops: { header: {} }, framing: 'carrier-pigeon' } },
    { id: 'a', folder: 'a2', order: 2 },
    { id: 'fine', worker: { command: ['x.exe'], ops: ['open'] } },
  ]);
  fs.mkdirSync(path.join(w.root, 'engines', 'broken'));
  fs.writeFileSync(path.join(w.root, 'engines', 'broken', 'engine.json'), '{ nope');
  fs.mkdirSync(path.join(w.root, 'engines', 'noid'));
  fs.writeFileSync(path.join(w.root, 'engines', 'noid', 'engine.json'), '{}');
  try {
    const registry = Registry.load({ roots: [w.root], env: QUIET_ENV });
    const text = registry.problems.join('\n');
    assert.match(text, /teleport/);
    assert.match(text, /carrier-pigeon/);
    assert.match(text, /native_ops repite la operación canónica header/);
    assert.match(text, /ya lo declara/);
    assert.match(text, /JSON inválido/);
    assert.match(text, /falta id/);
    assert.deepEqual([...registry.engines.keys()].sort(), ['a', 'fine']);
    assert.deepEqual([...registry.engine('a').worker.ops], ['open']);
    assert.equal(registry.engine('a').worker.framing, 'v2');
  } finally {
    w.cleanup();
  }
});

test('a proxy whose target is missing is unavailable and says why', () => {
  const w = workspace([
    { id: 'ghost', worker: { via: 'nothing', ops: ['decompile'] } },
    { id: 'real', worker: { command: [path.join(tempDir(), 'missing.exe')], ops: ['decompile'] } },
    { id: 'proxy', worker: { via: 'real', ops: ['decompile'] } },
  ]);
  try {
    const registry = Registry.load({ roots: [w.root], env: QUIET_ENV });
    assert.equal(registry.engine('ghost').available, false);
    assert.match(registry.engine('ghost').reason, /nothing/);
    assert.equal(registry.engine('proxy').available, false);
    assert.match(registry.engine('proxy').reason, /real/);
  } finally {
    w.cleanup();
  }
});

test('candidate lists accept one command or several', () => {
  assert.deepEqual(candidates(['a', 'b']), [['a', 'b']]);
  assert.deepEqual(candidates([['a'], ['b']]), [['a'], ['b']]);
  assert.deepEqual(candidates(undefined), []);
  assert.deepEqual(candidates([]), []);
});

test('missing protocol.json is a clear error', () => {
  const empty = tempDir();
  try {
    assert.throws(() => Registry.load({ roots: [empty], env: QUIET_ENV }), /engines[\\/]protocol\.json/);
    assert.throws(() => new Protocol({ protocol: 'x' }), /protocol debe ser/);
    const broken = JSON.parse(fs.readFileSync(REAL_PROTOCOL, 'utf8'));
    delete broken.ops.header;
    assert.throws(() => new Protocol(broken), /header/);
    const stray = JSON.parse(fs.readFileSync(REAL_PROTOCOL, 'utf8'));
    stray.prefer_authority = ['teleport'];
    assert.throws(() => new Protocol(stray), /prefer_authority nombra teleport/);
  } finally {
    removeDir(empty);
  }
});

test('timeouts and memory keep the limits of the previous host: 30 s, 120 s for compilers, 600 s for benchmarks; 1 GiB / 2 GiB', () => {
  const protocol = Protocol.load([REPO]);
  assert.equal(protocol.timeoutMs('default', {}), 30000);
  assert.equal(protocol.timeoutMs('compile', {}), 120000);
  assert.equal(protocol.timeoutMs('benchmark', {}), 600000);
  assert.equal(protocol.timeoutMs('compile', { FLASH_SUITE_COMPILE_TIMEOUT_SECONDS: '45' }), 45000);
  assert.equal(protocol.timeoutMs('default', { FLASH_SUITE_TIMEOUT_SECONDS: '9999' }), 600000);
  assert.equal(protocol.timeoutMs('default', { FLASH_SUITE_TIMEOUT_SECONDS: '0' }), 1000);
  assert.equal(protocol.timeoutMs('default', { FLASH_SUITE_TIMEOUT_SECONDS: 'abc' }), 30000);
  assert.equal(protocol.timeoutMs('unknown-class', {}), 30000);
  assert.equal(protocol.memoryMiB(null, {}), 1024);
  assert.equal(protocol.memoryMiB(2048, {}), 2048);
  assert.equal(protocol.memoryMiB(2048, { FLASH_SUITE_MEMORY_MIB: '100' }), 256);
  assert.equal(protocol.memoryMiB(1024, { FLASH_SUITE_MEMORY_MIB: '99999' }), 4096);
});
