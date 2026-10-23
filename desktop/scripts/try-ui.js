#!/usr/bin/env node
'use strict';
/**
 * Build the React interface (ui/) with Vite and open it in Electron: the window serves ui/dist
 * (main.js looks there when the application is not packaged).
 *
 *   node scripts/try-ui.js [file.swf] [--smoke] [--smoke-report <file.json>] [--build-only] [--no-build]
 *
 *   --smoke          run as a verification: the interface opens the file, shows a script and an
 *                    image and reports what it saw; the report is reports/electron-ui-smoke.json
 *   --smoke-report   the same, with the report written to the given file
 *   --build-only     build ui/dist and stop, do not open the window
 *   --no-build       open the window on the ui/dist that already exists
 *
 * The interface's dependencies come from ui/node_modules (`npm ci --prefix ui`).
 */
const fs = require('node:fs');
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

const vite = path.join(REPO, 'ui', 'node_modules', 'vite', 'bin', 'vite.js');
if (!flag('--no-build')) {
  if (!fs.existsSync(vite)) fail('Faltan las dependencias de la interfaz: ejecute `npm ci --prefix ui`.');
  console.log('Compilando la interfaz (ui/dist)');
  const build = spawnSync(process.execPath, [vite, 'build'], { cwd: path.join(REPO, 'ui'), stdio: 'inherit' });
  if (build.status !== 0) fail('vite build falló');
}
if (!fs.existsSync(path.join(REPO, 'ui', 'dist', 'index.html'))) fail('Falta ui/dist: compile la interfaz sin --no-build.');
if (flag('--build-only')) {
  console.log(`Interfaz compilada en ${path.join(REPO, 'ui', 'dist')}`);
  process.exit(0);
}

const electron = require('electron');
const args = ['.', '--open', path.resolve(sample)];
const report = option('--smoke-report') || (flag('--smoke') ? path.join(REPO, 'reports', 'electron-ui-smoke.json') : null);
if (report) args.push('--smoke-report', path.resolve(report));
const child = spawn(electron, args, { cwd: DESKTOP, stdio: 'inherit' });
child.on('exit', (code) => process.exit(code === null ? 1 : code));
