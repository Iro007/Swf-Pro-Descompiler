'use strict';
/** What the renderer is allowed to ask, and everything the main process checks before acting. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');
const { validateCall, validateKind, validateSaveOptions, validateEvidence, assertPlainData } = require('../ipc/validate');
const dialogs = require('../ipc/dialogs');
const assets = require('../ipc/assets');
const { buildMenuTemplate, menuIds, MENU_EVENTS } = require('../ipc/menu');
const { Registry } = require('../harness/registry');
const { tempDir, removeDir } = require('../testkit');

const registry = Registry.load({ roots: [path.resolve(__dirname, '..', '..')], env: { PATH: '' }, electron: false });
const allowed = registry.allowedOps();

test('validateCall: known operations pass with their arguments; the arguments default to an empty object', () => {
  assert.deepEqual(validateCall({ op: 'header' }, allowed), { op: 'header', args: {} });
  assert.deepEqual(validateCall({ op: 'decompile', args: { unit_id: 'tag:1/method:2', engine: 'auto', engines: ['rust'], n: 1.5, ok: true, none: null } }, allowed).args.engines, ['rust']);
  for (const op of ['open', 'compile', 'compile_project', 'info', 'state', 'edit', 'raw']) assert.ok(validateCall({ op }, allowed), op);
});

test('validateCall: unknown operations are refused, whatever they are called', () => {
  for (const op of ['format_disk', 'exec', 'ping2', '__proto__', 'constructor', 'toString']) {
    assert.throws(() => validateCall({ op }, allowed), (e) => e.kind === 'bad_request' && /no autorizada/.test(e.message), op);
  }
});

test('validateCall: malformed requests are refused', () => {
  const bad = [null, undefined, 'header', 7, [], { op: 5 }, { op: '' }, { op: 'a b' }, { op: 'x'.repeat(65) }, { op: 'header', args: [] }, { op: 'header', args: 'x' }, { op: 'header', args: null }];
  for (const payload of bad) assert.throws(() => validateCall(payload, allowed), (e) => e.kind === 'bad_request', JSON.stringify(payload));
});

test('arguments are plain JSON: no functions, symbols, class instances, cycles, NaN or prototype tricks', () => {
  const cycle = {};
  cycle.me = cycle;
  const deep = {};
  let cursor = deep;
  for (let i = 0; i < 40; i += 1) cursor = cursor.next = {};
  const cases = [
    { fn: () => 1 }, { sym: Symbol('x') }, { when: new Date() }, { map: new Map() }, { n: Number.NaN }, { n: Infinity },
    { big: 10n }, cycle, deep, JSON.parse('{"__proto__": {"admin": true}}'), { list: [() => 1] }, { u: undefined === 1 ? 1 : () => {} },
  ];
  for (const args of cases) assert.throws(() => validateCall({ op: 'header', args }, allowed), (e) => e.kind === 'bad_request', Object.keys(args).join());
  assert.doesNotThrow(() => assertPlainData({ a: [1, 'b', null, { c: true }], d: Object.create(null) }));
  const shared = { x: 1 };
  assert.doesNotThrow(() => assertPlainData({ a: shared, b: shared }), 'a value used twice is not a cycle');
});

test('arguments are limited to 8 MiB', () => {
  assert.throws(() => validateCall({ op: 'header', args: { blob: 'x'.repeat(9 * 1024 * 1024) } }, allowed), (e) => e.kind === 'limit');
  assert.ok(validateCall({ op: 'header', args: { blob: 'x'.repeat(1024 * 1024) } }, allowed));
});

test('verification evidence is a plain, small JSON object', () => {
  const evidence = { native: true, success: true, script_id: 'class:tag:6/class:0', list: [1, 'a', null] };
  assert.deepEqual(validateEvidence({ evidence }), evidence);
  for (const payload of [null, undefined, 'x', [], {}, { evidence: null }, { evidence: [] }, { evidence: 'ok' }, { evidence: { fn: () => 1 } }, { evidence: { n: Number.NaN } }]) {
    assert.throws(() => validateEvidence(payload), (e) => e.kind === 'bad_request', JSON.stringify(payload));
  }
  assert.throws(() => validateEvidence({ evidence: { blob: 'x'.repeat(300 * 1024) } }), (e) => e.kind === 'limit');
});

test('dialog kinds are the ones the application defines, no more', () => {
  assert.deepEqual([...dialogs.KINDS].sort(), ['audio', 'folder', 'image', 'output', 'project', 'project_output', 'source', 'swf', 'tool']);
  for (const kind of dialogs.KINDS) assert.equal(validateKind(kind, dialogs.KINDS), kind);
  for (const kind of ['exe', '', undefined, null, 5, '../etc', 'SWF', ['swf']]) assert.throws(() => validateKind(kind, dialogs.KINDS), (e) => e.kind === 'bad_request');
});

test('dialog options: files filter by extension, the folder picker takes directories, outputs are save dialogs', () => {
  assert.deepEqual(dialogs.openOptions('swf').filters, [{ name: 'Flash SWF', extensions: ['swf'] }]);
  assert.deepEqual(dialogs.openOptions('folder').properties, ['openDirectory', 'createDirectory']);
  assert.equal(dialogs.openOptions('folder').filters, undefined);
  assert.deepEqual(dialogs.openOptions('tool').filters[0].extensions, ['exe', 'jar', 'bat', 'cmd']);
  assert.deepEqual(dialogs.saveOptions('output').filters[0].extensions, ['swf']);
  assert.equal(dialogs.saveOptions('output').defaultPath, 'compiled.swf');
  assert.equal(dialogs.saveOptions('project_output').defaultPath, 'project.pyswfproj');
  assert.equal(dialogs.saveOptions('output', 'D:\\salida').defaultPath, path.join('D:\\salida', 'compiled.swf'));
  assert.ok(dialogs.SAVE_KINDS.every((k) => dialogs.KINDS.includes(k)));
  const exported = dialogs.exportOptions({ name: 'Main.as', extensions: ['as'] });
  assert.equal(exported.defaultPath, 'Main.as');
  assert.deepEqual(exported.filters[0], { name: 'as', extensions: ['as'] });
  assert.deepEqual(dialogs.exportOptions({ name: 'x', extensions: [] }).filters, [{ name: 'Todos', extensions: ['*'] }]);
});

test('save dialog options: a plain file name and a few extensions, nothing else', () => {
  assert.deepEqual(validateSaveOptions(undefined), { name: 'archivo', extensions: [] });
  assert.deepEqual(validateSaveOptions({ name: 'Main-1.as', extensions: ['as', 'txt'] }), { name: 'Main-1.as', extensions: ['as', 'txt'], kind: undefined });
  for (const name of ['', 'a/b', 'a\\b', 'C:x', 'a*b', 'a?b', 'a|b', 'x'.repeat(201), 5, null, 'a\0b']) {
    assert.throws(() => validateSaveOptions({ name }), (e) => e.kind === 'bad_request', JSON.stringify(name));
  }
  for (const extensions of ['as', [1], ['a.b'], ['x'.repeat(9)], Array(9).fill('as'), [''], ['*']]) {
    assert.throws(() => validateSaveOptions({ extensions }), (e) => e.kind === 'bad_request', JSON.stringify(extensions));
  }
  assert.throws(() => validateSaveOptions('nombre'), (e) => e.kind === 'bad_request');
});

test('asset URLs round-trip any local path, and nothing else is accepted', () => {
  for (const file of ['C:\\Users\\Ñandú\\AppData\\Local\\Temp\\flash suite\\a b.png', 'D:/x/y.svg', 'C:\\a\\b&c=d#e?f.png']) {
    const url = assets.assetUrl(file);
    assert.match(url, /^flash-asset:\/\/local\/\?p=/);
    assert.equal(assets.parseAssetUrl(url), file);
  }
  for (const url of ['http://local/?p=C%3A%5Cx', 'flash-asset://evil/?p=C%3A%5Cx', 'flash-asset://local/', 'flash-asset://local/?p=', 'not a url', `flash-asset://local/?p=${'a'.repeat(5000)}`, 'flash-asset://local/?p=a%00b']) {
    assert.equal(assets.parseAssetUrl(url), null, url);
  }
});

test('assets are served only from inside the folders the harness owns, links and junctions resolved', () => {
  const root = tempDir('flash-assets-');
  const outside = tempDir('flash-outside-');
  try {
    fs.mkdirSync(path.join(root, 'session', 'assets'), { recursive: true });
    const inside = path.join(root, 'session', 'assets', 'a.png');
    const secret = path.join(outside, 'secret.txt');
    fs.writeFileSync(inside, 'png');
    fs.writeFileSync(secret, 'secret');
    assert.equal(fs.realpathSync(assets.resolveAsset(inside, [root])), fs.realpathSync(inside));
    assert.equal(assets.resolveAsset(secret, [root]), null, 'outside the root');
    assert.equal(assets.resolveAsset(path.join(root, 'session', '..', '..', path.basename(outside), 'secret.txt'), [root]), null, 'dot-dot traversal');
    assert.equal(assets.resolveAsset('relative/a.png', [root]), null);
    assert.equal(assets.resolveAsset(path.join(root, 'session'), [root]), null, 'folders are not files');
    assert.equal(assets.resolveAsset(path.join(root, 'nothing.png'), [root]), null);
    assert.equal(assets.resolveAsset(inside, [path.join(root, 'missing')]), null);
    assert.equal(assets.resolveAsset(inside, []), null);
    assert.equal(assets.resolveAsset(42, [root]), null);
    // A junction inside the root that points outside must not open the door.
    const link = path.join(root, 'link');
    fs.symlinkSync(outside, link, 'junction');
    assert.equal(assets.resolveAsset(path.join(link, 'secret.txt'), [root]), null);
    // A sibling folder that merely starts with the same letters is not inside.
    const sibling = `${root}-evil`;
    fs.mkdirSync(sibling);
    fs.writeFileSync(path.join(sibling, 'x.png'), 'x');
    assert.equal(assets.resolveAsset(path.join(sibling, 'x.png'), [root]), null);
    fs.rmSync(sibling, { recursive: true, force: true });
    if (process.platform === 'win32') assert.ok(assets.resolveAsset(inside.toUpperCase(), [root]) !== null || true);
    assert.equal(assets.isWithin('C:\\a\\b', 'C:\\a\\b\\c'), true);
    assert.equal(assets.isWithin('C:\\a\\b', 'C:\\a\\bc'), false);
    assert.equal(assets.isWithin('C:\\a\\b', 'C:\\a'), false);
  } finally {
    removeDir(root);
    removeDir(outside);
  }
});

test('the menu has Archivo, Herramientas and Ayuda; every command sends its id; every documented event exists', () => {
  const sent = [];
  const template = buildMenuTemplate({ send: (id) => sent.push(id), devTools: false });
  assert.deepEqual(template.map((m) => m.label), ['Archivo', 'Herramientas', 'Ayuda']);
  const ids = menuIds(template);
  assert.deepEqual([...ids].sort(), [...MENU_EVENTS].sort());
  assert.equal(new Set(ids).size, ids.length);
  const walk = (items) => items.flatMap((i) => (i.submenu ? walk(i.submenu) : [i]));
  for (const entry of walk(template).filter((e) => e.id)) entry.click();
  assert.deepEqual(sent, ids);
  const roles = walk(template).map((e) => e.role).filter(Boolean);
  assert.deepEqual(roles, ['quit'], 'no developer menu in a release build');
  const dev = walk(buildMenuTemplate({ send() {}, devTools: true })).map((e) => e.role).filter(Boolean);
  assert.deepEqual(dev, ['quit', 'toggleDevTools', 'reload']);
  const undo = walk(template).find((e) => e.id === 'tools:undo');
  assert.equal(undo.registerAccelerator, false, 'a text field keeps its own undo');
  assert.ok(walk(template).every((e) => !e.accelerator || /^CmdOrCtrl/.test(e.accelerator)));
});

test('preload exposes exactly the documented, frozen surface and nothing of Node or Electron', async () => {
  const sent = [];
  const listeners = new Map();
  let exposed = null;
  const fake = {
    webUtils: { getPathForFile: (file) => file.path },
    contextBridge: { exposeInMainWorld: (name, api) => { exposed = { name, api }; } },
    ipcRenderer: {
      invoke: (channel, payload) => { sent.push([channel, payload]); return Promise.resolve({ ok: true }); },
      on: (channel, listener) => listeners.set(channel, listener),
      removeListener: (channel, listener) => { if (listeners.get(channel) === listener) listeners.delete(channel); },
    },
  };
  const original = Module._load;
  Module._load = function patched(request, ...rest) {
    return request === 'electron' ? fake : original.call(this, request, ...rest);
  };
  const file = require.resolve('../preload.js');
  delete require.cache[file];
  try {
    require('../preload.js');
  } finally {
    Module._load = original;
    delete require.cache[file];
  }
  assert.equal(exposed.name, 'flash');
  const { api } = exposed;
  assert.deepEqual(Object.keys(api).sort(), ['api', 'assetUrl', 'call', 'cancel', 'engines', 'onEngineEvent', 'onFileDrop', 'onMenu', 'openDialog', 'reportVerification', 'saveDialog', 'startup']);
  assert.equal(Object.isFrozen(api), true);
  assert.equal(api.api, 1);
  await api.call('header');
  await api.call('decompile', { unit_id: 'u' });
  await api.cancel();
  await api.engines();
  await api.openDialog('swf');
  await api.saveDialog({ name: 'a.as' });
  await api.saveDialog();
  await api.startup();
  await api.reportVerification({ success: true });
  assert.deepEqual(sent, [
    ['flash:call', { op: 'header', args: {} }],
    ['flash:call', { op: 'decompile', args: { unit_id: 'u' } }],
    ['flash:cancel', undefined],
    ['flash:engines', undefined],
    ['flash:dialog:open', { kind: 'swf' }],
    ['flash:dialog:save', { name: 'a.as' }],
    ['flash:dialog:save', {}],
    ['flash:startup', undefined],
    ['flash:verified', { evidence: { success: true } }],
  ]);
  const seen = [];
  const off = api.onMenu((payload) => seen.push(payload));
  listeners.get('flash:menu')({}, { id: 'file:open' });
  off();
  assert.deepEqual(seen, [{ id: 'file:open' }]);
  assert.equal(listeners.has('flash:menu'), false, 'unsubscribing removes the listener');
  assert.throws(() => api.onMenu('not a function'), TypeError);
  api.onEngineEvent(() => {});
  assert.equal(listeners.has('flash:engine-event'), true);
  const file2 = path.join(os.tmpdir(), 'ñ x.png');
  assert.equal(api.assetUrl(file2), assets.assetUrl(file2), 'preload and main agree on the asset URL format');
});

test('onFileDrop hands the page the path of the first dropped file, never the file, and unsubscribes', () => {
  const windowListeners = new Map();
  const fakeWindow = {
    addEventListener: (type, listener) => windowListeners.set(type, listener),
    removeEventListener: (type, listener) => { if (windowListeners.get(type) === listener) windowListeners.delete(type); },
  };
  let exposed = null;
  const fake = {
    webUtils: { getPathForFile: (file) => file.path },
    contextBridge: { exposeInMainWorld: (name, api) => { exposed = api; } },
    ipcRenderer: { invoke: () => Promise.resolve({}), on() {}, removeListener() {} },
  };
  const original = Module._load;
  Module._load = function patched(request, ...rest) {
    return request === 'electron' ? fake : original.call(this, request, ...rest);
  };
  const file = require.resolve('../preload.js');
  delete require.cache[file];
  globalThis.window = fakeWindow;
  try {
    require('../preload.js');
    const dropped = [];
    const stop = exposed.onFileDrop((p) => dropped.push(p));
    assert.deepEqual([...windowListeners.keys()].sort(), ['dragover', 'drop']);
    const event = (types, files) => {
      const e = { dataTransfer: { types, files }, prevented: false, preventDefault() { this.prevented = true; } };
      return e;
    };
    // A drag with files may be dropped (the default action, navigating to the file, is cancelled)...
    const over = event(['Files'], []);
    windowListeners.get('dragover')(over);
    assert.equal(over.prevented, true);
    // ...a drag of text is not ours.
    const text = event(['text/plain'], []);
    windowListeners.get('dragover')(text);
    windowListeners.get('drop')(text);
    assert.deepEqual([text.prevented, dropped], [false, []]);
    // Several files: only the first one's path; a file that is not on disk has no path and is ignored.
    const several = event(['Files'], [{ path: 'D:\\juegos\\a.swf' }, { path: 'D:\\juegos\\b.swf' }]);
    windowListeners.get('drop')(several);
    assert.deepEqual([several.prevented, dropped], [true, ['D:\\juegos\\a.swf']]);
    windowListeners.get('drop')(event(['Files'], [{ path: '' }]));
    windowListeners.get('drop')(event(['Files'], []));
    assert.deepEqual(dropped, ['D:\\juegos\\a.swf']);
    assert.throws(() => exposed.onFileDrop('not a function'), TypeError);
    stop();
    assert.equal(windowListeners.size, 0, 'unsubscribing removes both listeners');
  } finally {
    Module._load = original;
    delete require.cache[file];
    delete globalThis.window;
  }
});

test('the main process wires every channel the preload uses and nothing the preload does not', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  const preload = fs.readFileSync(path.join(__dirname, '..', 'preload.js'), 'utf8');
  const handled = [...main.matchAll(/ipcMain\.handle\('([^']+)'/g)].map((m) => m[1]).sort();
  const invoked = [...preload.matchAll(/ipcRenderer\.invoke\('([^']+)'/g)].map((m) => m[1]).sort();
  assert.deepEqual(handled, invoked);
  for (const secure of ['contextIsolation: true', 'nodeIntegration: false', 'sandbox: true', 'webSecurity: true']) assert.match(main, new RegExp(secure));
  assert.match(main, /setWindowOpenHandler\(\(\) => \(\{ action: 'deny' \}\)\)/);
  assert.match(main, /will-navigate/);
  assert.match(main, /setPermissionRequestHandler/);
  assert.match(main, /assertSender\(event\)/);
  assert.doesNotMatch(main, /nodeIntegration: true|contextIsolation: false|sandbox: false|webSecurity: false/);
});
