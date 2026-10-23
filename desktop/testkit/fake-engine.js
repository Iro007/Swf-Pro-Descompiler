#!/usr/bin/env node
'use strict';
/**
 * A scripted engine worker for the harness tests. It speaks JSONL in any of the
 * three framings and answers each operation from `--config file.json`:
 *
 *   {
 *     "name": "rust", "framing": "v2", "log": "requests.jsonl",
 *     "requireOpen": true,                 // reads fail with not_open before `open`
 *     "startDelayMs": 0, "exitAfterRequests": 0,
 *     "ops": { "<op>": <spec> }
 *   }
 *
 * spec = { result } | { error: {message, kind} | "text" } | { echo: true }
 *      | { crash: exitCode } | { garbage: true } | { wrongId: true } | { hang: true }
 *      | { big: bytes } | { spawnChild: true } | { stderr: "text" }
 *      | { byUnit: { "<unit id>": spec, "default": spec } } | { byEngine: { "<engine arg>": spec, "default": spec } }
 *      | { sequence: [spec, ...] } | { crashOnce: "<marker file>" } | { hangOnce: "<marker file>" }
 *   plus an optional { sleep: ms } before answering.
 * With `"pyswf": true` the worker also behaves like the PySWF editor: `open`, `state`,
 * `edit`, `resource_edit`, `compile_project`, `undo`, `redo` and `export_swf` act on a
 * document kept (with its undo history) in FLASH_SUITE_STATE_DIR, so a restarted worker
 * recovers it exactly as the real one does.
 * An operation with no spec is answered `unsupported`, as the real workers do.
 * Every request is appended to `log` as a JSON line, so tests can see which
 * engine was asked what, in which order, with which arguments.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const readline = require('node:readline');
const { spawn } = require('node:child_process');

const at = process.argv.indexOf('--config');
const config = at >= 0 ? JSON.parse(fs.readFileSync(process.argv[at + 1], 'utf8')) : {};
const framing = config.framing || 'v2';
const counts = new Map();
let opened = null;
let served = 0;

function log(entry) {
  if (config.log) {
    fs.appendFileSync(config.log, `${JSON.stringify({ engine: config.name, pid: process.pid, t: Date.now(), opened, ...entry })}\n`);
  }
}
function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}
function request(message) {
  return framing === 'legacy'
    ? { id: message.request_id, op: message.operation, args: message.args || {} }
    : { id: message.id, op: message.op, args: message.args || {} };
}
function ok(id, result) {
  if (framing === 'legacy') return { request_id: id, result, elapsed_ms: 0 };
  if (framing === 'suite') return { id, ok: true, protocol_version: 1, result };
  return { id, ok: true, protocol_version: 2, result };
}
function failure(id, error) {
  const e = typeof error === 'string' ? { message: error, kind: 'failed' } : error;
  if (framing === 'legacy') return { request_id: id, error: e.message, elapsed_ms: 0 };
  if (framing === 'suite') return { id, ok: false, protocol_version: 1, error: e.message };
  return { id, ok: false, protocol_version: 2, error: e };
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---- a miniature PySWF editor -----------------------------------------------------
const stateDir = process.env.FLASH_SUITE_STATE_DIR || null;
const store = { working: null, origin: null, undo: [], redo: [] };
const sha = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const unb64 = (text) => Buffer.from(text, 'base64');
function loadStore() {
  const file = stateDir && path.join(stateDir, 'session.json');
  if (!file || !fs.existsSync(file)) return;
  const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
  store.working = unb64(saved.working);
  store.origin = saved.origin;
  store.undo = saved.undo.map(unb64);
  store.redo = saved.redo.map(unb64);
}
function saveStore() {
  if (!stateDir) return;
  fs.mkdirSync(stateDir, { recursive: true });
  fs.writeFileSync(path.join(stateDir, 'working.swf'), store.working);
  fs.writeFileSync(
    path.join(stateDir, 'session.json'),
    JSON.stringify({
      working: store.working.toString('base64'),
      origin: store.origin,
      undo: store.undo.map((b) => b.toString('base64')),
      redo: store.redo.map((b) => b.toString('base64')),
    }),
  );
}
function editorState() {
  return {
    protocol_version: 1,
    filename: path.basename(store.origin),
    working_path: path.join(stateDir, 'working.swf'),
    source_path: store.origin,
    revision: sha(store.working),
    dirty: store.undo.length > 0,
    script_count: 3,
    history: { undo: store.undo.length > 0, redo: store.redo.length > 0 },
    signature: 'FWS',
    frame_count: 1,
    validation: { valid: true, errors: [], warnings: [], signature: 'FWS', version: 9, tag_count: 2, frame_count: 1 },
    state_dir: stateDir,
  };
}
const refuse = (message) => ({ error: { message, kind: 'failed' } });
function editorOp(op, args) {
  const needsProject = () => (store.working ? null : refuse('Abre un SWF o un proyecto primero'));
  switch (op) {
    case 'open':
    case 'load_project':
      store.working = fs.readFileSync(args.path);
      store.origin = args.path;
      store.undo = [];
      store.redo = [];
      saveStore();
      return { result: editorState() };
    case 'state':
      return needsProject() || { result: editorState() };
    case 'edit':
    case 'resource_edit':
    case 'compile_project': {
      const missing = needsProject();
      if (missing) return missing;
      if (op !== 'compile_project' && args.base_revision !== sha(store.working)) return refuse('El documento cambió; conserva tu borrador y actualiza la revisión');
      store.undo.push(store.working);
      store.redo = [];
      store.working = Buffer.concat([store.working, Buffer.from(`|${op}:${args.unit_id ?? args.tag_index ?? args.operation ?? ''}:${args.mode ?? ''}:${args.engine ?? ''}`)]);
      saveStore();
      return { result: op === 'compile_project' ? { ...editorState(), path: args.output, engine: 'native', log: 'ok' } : editorState() };
    }
    case 'undo':
    case 'redo': {
      const missing = needsProject();
      if (missing) return missing;
      const [from, to] = op === 'undo' ? [store.undo, store.redo] : [store.redo, store.undo];
      if (from.length === 0) return refuse(`No hay cambios para ${op === 'undo' ? 'deshacer' : 'rehacer'}`);
      to.push(store.working);
      store.working = from.pop();
      saveStore();
      return { result: editorState() };
    }
    case 'export_swf':
      fs.writeFileSync(args.path, store.working);
      return { result: { ...editorState(), path: args.path } };
    default:
      return null;
  }
}

function resolveSpec(spec, op, args) {
  if (spec && spec.byEngine) {
    return resolveSpec(spec.byEngine[args.engine] ?? spec.byEngine.default, op, args);
  }
  if (spec && spec.byUnit) {
    const key = args.unit_id ?? args.id ?? args.tag_index;
    return resolveSpec(spec.byUnit[key] ?? spec.byUnit.default, op, args);
  }
  if (spec && spec.sequence) {
    const n = counts.get(op) ?? 0;
    counts.set(op, n + 1);
    return resolveSpec(spec.sequence[Math.min(n, spec.sequence.length - 1)], op, args);
  }
  return spec;
}

async function handle(line) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    send(failure(null, 'JSON inválido'));
    return;
  }
  const { id, op, args } = request(message);
  log({ op, args, framing, request: message });
  served += 1;
  if (config.exitAfterRequests && served > config.exitAfterRequests) process.exit(9);
  const raw = (config.ops || {})[op];
  if (config.pyswf && (raw === undefined || raw.editor)) {
    const outcome = editorOp(op, args);
    if (outcome) {
      send(outcome.error ? failure(id, outcome.error) : ok(id, outcome.result));
      return;
    }
    if (op === 'ping') {
      send(ok(id, { protocol_version: 1, application: 'Fake', version: '0.0.0', engines: {} }));
      return;
    }
  }
  if (raw === undefined) {
    send(failure(id, { message: `Operación no soportada: ${op}`, kind: 'unsupported' }));
    return;
  }
  const spec = resolveSpec(raw, op, args) || {};
  if (config.pyswf) {
    /* the editor answers open/state itself; reads need no check */
  } else if (op === 'open' && typeof args.path === 'string') {
    if (spec.error === undefined) opened = args.path;
  } else if (config.requireOpen && op !== 'capabilities' && !opened && !(config.openless || []).includes(op)) {
    send(failure(id, { message: 'No hay SWF abierto', kind: 'not_open' }));
    return;
  }
  if (spec.crashOnce && !fs.existsSync(spec.crashOnce)) {
    fs.writeFileSync(spec.crashOnce, 'crashed');
    process.exit(3);
  }
  if (spec.hangOnce && !fs.existsSync(spec.hangOnce)) {
    fs.writeFileSync(spec.hangOnce, 'hung');
    return;
  }
  if (spec.stderr) process.stderr.write(`${spec.stderr}\n`);
  if (spec.sleep) await sleep(spec.sleep);
  if (spec.hang) return;
  if (spec.crash !== undefined) process.exit(spec.crash);
  if (spec.garbage) {
    process.stdout.write('this is not json\n');
    return;
  }
  if (spec.wrongId) {
    send(ok(typeof id === 'number' ? id + 1000 : `${id}-wrong`, {}));
    return;
  }
  if (spec.error !== undefined) {
    send(failure(id, spec.error));
    return;
  }
  if (spec.spawnChild) {
    const child = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore', windowsHide: true });
    child.unref();
    send(ok(id, { pid: process.pid, child: child.pid }));
    return;
  }
  if (spec.big) {
    send(ok(id, { data: 'x'.repeat(spec.big) }));
    return;
  }
  if (spec.echo) {
    send(ok(id, { op, args, opened, pid: process.pid, served }));
    return;
  }
  send(ok(id, spec.result === undefined ? {} : spec.result));
}

async function main() {
  if (config.pyswf) loadStore();
  if (config.startDelayMs) await sleep(config.startDelayMs);
  if (config.crashOnStart) process.exit(config.crashOnStart);
  // Requests are handled one at a time, like every real worker.
  let chain = Promise.resolve();
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on('line', (line) => {
    if (line.trim() === '') return;
    chain = chain.then(() => handle(line));
  });
  rl.on('close', () => {
    chain.then(() => process.exit(0));
  });
}
main();
