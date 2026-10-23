#!/usr/bin/env node
'use strict';
/**
 * Try the React interface inside Electron without touching ui/: copy the interface to a temporary
 * folder, put desktop/ui-host/api.ts (the Electron version of its host module) in place of
 * src/api.ts, build it with Vite and open the window on that build (FLASH_UI_ROOT).
 *
 *   node scripts/try-ui.js [file.swf] [--smoke] [--build-only] [--node-modules <folder>]
 *
 *   --smoke         run as a verification: the interface opens the file, shows a script and an
 *                   image and reports what it saw; the report is reports/electron-ui-smoke.json
 *   --build-only    build and print where, do not open the window
 *   --node-modules  the interface's dependencies (default ui/node_modules, then FLASH_UI_NODE_MODULES;
 *                   `npm ci --prefix ui` installs them)
 */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const REPO = path.resolve(__dirname, '..', '..');
const DESKTOP = path.resolve(__dirname, '..');
const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const option = (name) => {
  const at = argv.indexOf(name);
  return at < 0 ? null : argv[at + 1];
};
const sample = argv.find((a) => /\.(swf|pyswfproj)$/i.test(a)) || path.join(REPO, 'corpus', 'parity-20261014', 'optimized', 'swf', 'dev-as3-classes-namespaces', 'release.fws.swf');

function fail(message) {
  console.error(message);
  process.exit(2);
}

const modules = [option('--node-modules'), process.env.FLASH_UI_NODE_MODULES, path.join(REPO, 'ui', 'node_modules')]
  .filter(Boolean)
  .map((p) => path.resolve(p))
  .find((p) => fs.existsSync(path.join(p, 'vite', 'bin', 'vite.js')));
if (!modules) fail('Faltan las dependencias de la interfaz: ejecute `npm ci --prefix ui` (o indique --node-modules <carpeta>).');

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'flash-ui-electron-'));
const copy = path.join(work, 'ui');
const cleanup = () => {
  try {
    // The dependencies are linked, not copied: remove the link itself before the folder.
    fs.rmdirSync(path.join(copy, 'node_modules'));
  } catch {
    /* no link yet */
  }
  fs.rmSync(work, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
};
process.on('exit', cleanup);
process.on('SIGINT', () => process.exit(130));

fs.cpSync(path.join(REPO, 'ui'), copy, {
  recursive: true,
  filter: (source) => !/(^|[\\/])(node_modules|dist|tests)([\\/]|$)/.test(path.relative(path.join(REPO, 'ui'), source)),
});
fs.symlinkSync(modules, path.join(copy, 'node_modules'), 'junction');
fs.copyFileSync(path.join(DESKTOP, 'ui-host', 'api.ts'), path.join(copy, 'src', 'api.ts'));

console.log(`Compilando la interfaz con ui-host/api.ts (dependencias: ${modules})`);
const build = spawnSync(process.execPath, [path.join(modules, 'vite', 'bin', 'vite.js'), 'build'], { cwd: copy, stdio: 'inherit' });
if (build.status !== 0) fail('vite build falló');
const dist = path.join(copy, 'dist');
if (flag('--build-only')) {
  // Keep the build: copy it out of the temporary folder.
  const kept = path.join(REPO, 'reports', 'ui-electron-dist');
  fs.rmSync(kept, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(kept), { recursive: true });
  fs.cpSync(dist, kept, { recursive: true });
  console.log(`Interfaz compilada en ${kept} (FLASH_UI_ROOT=${kept})`);
  process.exit(0);
}

const electron = require('electron');
const args = ['.', '--open', path.resolve(sample)];
if (flag('--smoke')) args.push('--smoke-report', path.join(REPO, 'reports', 'electron-ui-smoke.json'));
const child = spawn(electron, args, { cwd: DESKTOP, stdio: 'inherit', env: { ...process.env, FLASH_UI_ROOT: dist } });
child.on('exit', (code) => process.exit(code === null ? 1 : code));
