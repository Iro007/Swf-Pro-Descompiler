'use strict';
/**
 * Helpers for the tests that run the real engines. Those tests skip, with the
 * reason, when an engine is not built on this machine: CI has no Rust build, a
 * fresh checkout has no Python environment, and so on.
 */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Registry } = require('../harness/registry');
const { Harness } = require('../harness/harness');

const REPO = path.resolve(__dirname, '..', '..');

/**
 * Where built engines may live: this checkout, FLASH_ENGINE_ROOTS, and the main checkout
 * when this is a git worktree (`<main>/.claude/worktrees/<name>`), whose ignored build
 * outputs (target/, engines/pyswf/.venv, engines/*\/dist) are not in the worktree.
 */
function realRoots(env = process.env) {
  const roots = [REPO];
  for (const extra of (env.FLASH_ENGINE_ROOTS || '').split(path.delimiter)) if (extra) roots.push(path.resolve(extra));
  const parts = REPO.split(path.sep);
  const at = parts.lastIndexOf('worktrees');
  if (at > 1 && parts[at - 1] === '.claude') roots.push(parts.slice(0, at - 1).join(path.sep));
  return [...new Set(roots)];
}

const corpus = (relative) => path.join(REPO, 'corpus', relative);
const PARITY = (family, file = 'release.fws.swf') => corpus(`parity-20261014/optimized/swf/${family}/${file}`);

function realRegistry(env = process.env) {
  return Registry.load({ roots: realRoots(env), env, electron: false });
}

/** `false` when the engine is runnable, otherwise the reason to put in `{skip}`. */
function missing(registry, ...ids) {
  for (const id of ids) {
    const info = registry.engine(id);
    if (!info || !info.available) return `${id} no está disponible en esta máquina: ${info ? info.reason : 'sin manifiesto'}`;
  }
  return false;
}

/** The root that holds `vendor/` (downloaded tools): PySWF looks there for the player SDK and JPEXS. */
function toolsRoot(roots) {
  return roots.find((root) => fs.existsSync(path.join(root, 'vendor'))) || roots[0];
}

function realHarness(options = {}) {
  const env = { ...process.env, ...(options.env || {}) };
  if (!env.FLASH_SUITE_WORKSPACE) env.FLASH_SUITE_WORKSPACE = toolsRoot(realRoots(env));
  return new Harness({
    roots: realRoots(env),
    env,
    stateRoot: fs.mkdtempSync(path.join(os.tmpdir(), 'flash-real-')),
    supervise: options.supervise === true,
    electron: false,
    nodePath: process.execPath,
  });
}

module.exports = { REPO, realRoots, corpus, PARITY, realRegistry, missing, realHarness, toolsRoot };
