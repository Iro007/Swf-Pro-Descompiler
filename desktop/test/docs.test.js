'use strict';
/**
 * engines/PROTOCOL.md is the contract people read; protocol.json and the manifests are
 * what the code reads. These tests make them say the same thing, so neither can drift.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Registry } = require('../harness/registry');
const { MENU_EVENTS } = require('../ipc/menu');

const ROOT = path.resolve(__dirname, '..', '..');
const doc = fs.readFileSync(path.join(ROOT, 'engines', 'PROTOCOL.md'), 'utf8');
const registry = Registry.load({ roots: [ROOT], env: { PATH: '' }, electron: false });
const { protocol } = registry;

const ticks = (text) => [...text.matchAll(/`([^`]+)`/g)].map((m) => m[1]);

test('the level table lists exactly the levels and operations of protocol.json, in order', () => {
  const rows = [...doc.matchAll(/^\| (L\d) \| ([^|]+) \| ([^|]+) \|$/gm)].map((m) => ({ level: m[1], name: m[2].trim(), ops: ticks(m[3]) }));
  assert.deepEqual(rows.map((r) => r.level), Object.keys(protocol.levels));
  for (const row of rows) {
    assert.equal(row.name, protocol.levels[row.level].name, row.level);
    assert.deepEqual(row.ops, protocol.levels[row.level].ops, row.level);
  }
});

test('every operation of every level is documented under its own level, in order', () => {
  const sections = doc.split(/^### (?=L\d · )/m).slice(1);
  assert.equal(sections.length, Object.keys(protocol.levels).length);
  sections.forEach((section, index) => {
    const [level] = Object.keys(protocol.levels)[index].split(' ');
    assert.ok(section.startsWith(`${level} · ${protocol.levels[level].name}`), `section ${level}`);
    const headings = [...section.matchAll(/^#### `([^`]+)`$/gm)].map((m) => m[1]);
    assert.deepEqual(headings, protocol.levels[level].ops, `operations documented under ${level}`);
  });
  assert.ok(Object.keys(protocol.raw.ops).every((op) => doc.includes(`#### \`${op}\``)));
});

test('the error kinds in the document are the ones protocol.json defines', () => {
  const worker = doc.slice(doc.indexOf('| Los da el motor'), doc.indexOf('| Los añade el arnés'));
  const harness = doc.slice(doc.indexOf('| Los añade el arnés'), doc.indexOf('## 2.'));
  const kinds = (table) => [...table.matchAll(/^\| `([a-z_]+)` \|/gm)].map((m) => m[1]);
  assert.deepEqual(kinds(worker), protocol.raw.error_kinds.worker);
  assert.deepEqual(kinds(harness), protocol.raw.error_kinds.harness);
  for (const kind of protocol.raw.error_kinds.no_fallback) assert.ok(protocol.raw.error_kinds.worker.includes(kind) || protocol.raw.error_kinds.harness.includes(kind));
});

test('the JPEXS folders and their order in the document are the ones the tree builders use', () => {
  const line = doc.split('\n').find((l) => l.includes('`header`, `shapes`, `morphshapes`'));
  assert.ok(line, 'folder list present');
  assert.deepEqual(ticks(line.slice(line.indexOf('en este orden'), line.indexOf('. Qué códigos'))), protocol.tagFolders.order);
});

test('the coverage table shows what the manifests really declare', () => {
  const rows = [...doc.matchAll(/^\| ([^|]+) \| ([^|]+) \| (\d)\/6 \| (\d)\/8 \| (\d)\/3 \| (\d)\/6 \| (\d)\/6 \| ([^|]+) \|$/gm)];
  const byName = (needle) => rows.find((m) => m[1].includes(needle));
  const counts = (id) => {
    const levels = registry.engine(id).levels;
    return [levels.L1.have.length, levels.L2.have.length, levels.L3.have.length, levels.L4.have.length, levels.L5.have.length];
  };
  for (const [needle, ids] of [['RustSWF', ['rust']], ['PySWF', ['native']], ['JsSWF', ['js', 'go', 'asm']], ['JPEXS', ['jpexs']]]) {
    const row = byName(needle);
    assert.ok(row, `${needle} row`);
    for (const id of ids) assert.deepEqual(row.slice(3, 8).map(Number), counts(id), `${needle}/${id}`);
  }
  assert.equal(registry.engine('rust').worker.framing, 'v2');
  assert.equal(registry.engine('native').worker.framing, 'suite');
  assert.equal(registry.engine('js').worker.framing, 'legacy');
});

test('the framing table names the three formats the manifests use', () => {
  const used = new Set(registry.workers().filter((e) => !e.worker.via).map((e) => e.worker.framing));
  assert.deepEqual([...used].sort(), ['legacy', 'suite', 'v2']);
  for (const framing of used) assert.match(doc, new RegExp(`\\| \`${framing}\` \\|`));
});

test('the window.flash table and the menu ids in the document match the code', () => {
  const preload = fs.readFileSync(path.join(ROOT, 'desktop', 'preload.js'), 'utf8');
  const exposed = [...preload.matchAll(/^ {2}(\w+): /gm)].map((m) => m[1]).filter((n) => n !== 'api');
  const rows = [...doc.matchAll(/^\| `(\w+)\(/gm)].map((m) => m[1]);
  assert.deepEqual([...rows].sort(), [...exposed].sort());
  const onMenu = doc.split('\n').find((l) => l.startsWith('| `onMenu(callback)`'));
  const ids = ticks(onMenu).filter((t) => /^[a-z]+:[a-z-]+$/.test(t));
  assert.deepEqual(ids.filter((id) => id !== 'file:open-path').sort(), [...MENU_EVENTS].sort());
});

test('the timeouts, memory and defaults quoted in the document are the ones in protocol.json', () => {
  assert.equal(protocol.raw.timeouts_seconds.default.seconds, 30);
  assert.equal(protocol.raw.timeouts_seconds.compile.seconds, 120);
  assert.equal(protocol.raw.timeouts_seconds.benchmark.seconds, 600);
  assert.equal(protocol.raw.memory_mib.default, 1024);
  assert.equal(protocol.raw.memory_mib.compile, 2048);
  assert.match(doc, /`default` 30 s, `compile` 120 s, `benchmark` 600 s/);
  assert.match(doc, /\(1024 MiB; 2048 PySWF;/);
  assert.match(doc, /plazo de 20 s \(`FLASH_AUTO_TIMEOUT_SECONDS`\)/);
  assert.deepEqual(protocol.fallbackChain, ['rust', 'native', 'jpexs']);
  assert.match(doc, /\*\*RustSWF → PySWF → JPEXS a través de PySWF\*\*/);
});

test('the READMEs point at the protocol and describe the desktop host', () => {
  const engines = fs.readFileSync(path.join(ROOT, 'engines', 'README.md'), 'utf8');
  assert.match(engines, /PROTOCOL\.md/);
  assert.match(engines, /worker/);
  const root = fs.readFileSync(path.join(ROOT, 'README.md'), 'utf8');
  assert.match(root, /desktop\//);
  assert.match(root, /Electron/);
});

test('the operations the session owner answers first are the ones protocol.json lists, and the document says why', () => {
  const paragraph = doc.split('\n').find((line) => line.startsWith('**El propietario de la sesión primero.**'));
  assert.ok(paragraph, 'paragraph present');
  assert.deepEqual([...protocol.preferAuthority].sort(), ['resource_info', 'tag_tree']);
  for (const op of protocol.preferAuthority) assert.ok(ticks(paragraph).includes(op), op);
  for (const op of protocol.preferAuthority) assert.ok(protocol.canonicalOps.includes(op), `${op} is a protocol operation`);
});

test('ui/src/api.ts, the only module of the interface that talks to its host, maps onto window.flash one to one', () => {
  const api = fs.readFileSync(path.join(ROOT, 'ui', 'src', 'api.ts'), 'utf8');
  const exported = [...api.matchAll(/^export const (\w+)/gm)].map((m) => m[1]);
  assert.ok(exported.length >= 7, 'the host module was found');
  const preload = fs.readFileSync(path.join(ROOT, 'desktop', 'preload.js'), 'utf8');
  const exposed = new Set([...preload.matchAll(/^ {2}(\w+): /gm)].map((m) => m[1]));
  const section = doc.slice(doc.indexOf('`ui/src/api.ts` es el único módulo'));
  const rows = [...section.matchAll(/^\| `(\w+)` \| (.+) \|$/gm)].map((m) => ({ name: m[1], target: m[2] }));
  assert.deepEqual(rows.map((r) => r.name).sort(), [...exported].sort(), 'every function of api.ts has a documented counterpart, and nothing else is documented');
  for (const row of rows) {
    if (row.name === 'native') continue;
    const [first] = ticks(row.target);
    assert.ok(first && exposed.has(first.replace(/\(.*$/, '')), `${row.name} maps onto ${row.target}`);
  }
});

test('desktop/ui-host/api.ts exports exactly what ui/src/api.ts exports, so it can take its place', () => {
  const names = (file) => [...fs.readFileSync(file, 'utf8').matchAll(/^export (?:const|interface|function) (\w+)/gm)].map((m) => m[1]).sort();
  const electron = names(path.join(ROOT, 'desktop', 'ui-host', 'api.ts'));
  assert.ok(electron.length >= 7, 'the Electron host module was found');
  assert.deepEqual(electron, names(path.join(ROOT, 'ui', 'src', 'api.ts')));
  const source = fs.readFileSync(path.join(ROOT, 'desktop', 'ui-host', 'api.ts'), 'utf8');
  const preload = fs.readFileSync(path.join(ROOT, 'desktop', 'preload.js'), 'utf8');
  const exposed = new Set([...preload.matchAll(/^ {2}(\w+): /gm)].map((m) => m[1]));
  const used = [...source.matchAll(/host\(\)!\.(\w+)\(/g)].map((m) => m[1]);
  assert.ok(used.length >= 6);
  for (const name of used) assert.ok(exposed.has(name), `window.flash.${name} exists`);
});
