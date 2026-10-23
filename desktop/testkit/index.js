'use strict';
/** Helpers shared by the harness tests: scripted engines and throwaway workspaces. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const FAKE_ENGINE = path.join(__dirname, 'fake-engine.js');
const REAL_PROTOCOL = path.join(__dirname, '..', '..', 'engines', 'protocol.json');

function tempDir(prefix = 'flash-harness-') {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function removeDir(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  } catch {
    /* Windows may still hold a handle for a moment; the OS temp dir is cleaned anyway */
  }
}

/** Spawn parameters for one scripted engine. */
function fakeWorker(dir, config, name = 'fake') {
  const file = path.join(dir, `${name}.config.json`);
  fs.writeFileSync(file, JSON.stringify({ name, ...config }));
  return { command: process.execPath, args: [FAKE_ENGINE, '--config', file], config: file };
}

/** Whether a process id is alive. */
function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

async function waitUntil(predicate, { timeoutMs = 5000, stepMs = 25 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await predicate()) return true;
    if (Date.now() > deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, stepMs));
  }
}

/**
 * A workspace with scripted engines, laid out like the repository:
 *   <root>/engines/protocol.json
 *   <root>/engines/<id>/engine.json   (worker.command runs fake-engine.js)
 *
 * @param {Array<object>} engines each `{id, order, name, category, framing, ops,
 *   native_ops, via, args, available:false, config:{...fake config}}`
 * @param {object} [options] `protocol` overrides the copied engines/protocol.json
 */
function makeWorkspace(engines, options = {}) {
  const root = tempDir();
  fs.mkdirSync(path.join(root, 'engines'), { recursive: true });
  const protocol = options.protocol || JSON.parse(fs.readFileSync(REAL_PROTOCOL, 'utf8'));
  fs.writeFileSync(path.join(root, 'engines', 'protocol.json'), JSON.stringify(protocol));
  const logs = {};
  const configs = {};
  for (const engine of engines) {
    const folder = path.join(root, 'engines', engine.folder || engine.id);
    fs.mkdirSync(folder, { recursive: true });
    const manifest = {
      id: engine.id,
      order: engine.order ?? 50,
      name: engine.name || engine.id,
      category: engine.category || 'decompiler',
      operations: engine.operations || ['source', 'disassemble'],
      languages: engine.languages || ['as2', 'as3'],
      website: '',
      color: engine.color || '#336699',
      license: 'test',
      delivery: engine.delivery || 'repo',
      root: '.',
    };
    if (engine.via) {
      manifest.worker = { via: engine.via, args: engine.args || {}, ops: engine.ops || [] };
    } else if (engine.ops) {
      const log = path.join(root, `${engine.id}.requests.jsonl`);
      logs[engine.id] = log;
      const config = { name: engine.id, framing: engine.framing || 'v2', log, ...(engine.config || {}) };
      const configFile = path.join(folder, 'fake.config.json');
      fs.writeFileSync(configFile, JSON.stringify(config));
      configs[engine.id] = configFile;
      manifest.worker = {
        protocol: 'jsonl',
        framing: engine.framing || 'v2',
        command: engine.available === false ? [path.join(root, 'no-such-binary.exe'), 'worker'] : ['{node}', FAKE_ENGINE, '--config', configFile],
        ops: engine.ops,
        native_ops: engine.native_ops || {},
        ...(engine.worker || {}),
      };
    }
    fs.writeFileSync(path.join(folder, 'engine.json'), JSON.stringify(manifest, null, 2));
  }
  /** Parsed request log of an engine: `[{op, args, opened, pid}]`. */
  const requests = (id) => {
    if (!logs[id] || !fs.existsSync(logs[id])) return [];
    return fs
      .readFileSync(logs[id], 'utf8')
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  };
  return { root, logs, configs, requests, cleanup: () => removeDir(root) };
}

const zlib = require('node:zlib');

function bitFields(fields) {
  const bytes = [];
  let position = 0;
  for (const [value, width] of fields) {
    for (let bit = width - 1; bit >= 0; bit -= 1) {
      if (position % 8 === 0) bytes.push(0);
      bytes[bytes.length - 1] |= ((value >> bit) & 1) << (7 - (position % 8));
      position += 1;
    }
  }
  return Buffer.from(bytes);
}

/** One SWF tag record: short header when the payload is under 63 bytes, long otherwise. */
function tagBytes(code, payload = []) {
  const body = Buffer.from(payload);
  if (body.length < 63) return Buffer.concat([Buffer.from([((code << 6) | body.length) & 255, (code << 6 | body.length) >> 8]), body]);
  const head = Buffer.alloc(6);
  head.writeUInt16LE((code << 6) | 63, 0);
  head.writeUInt32LE(body.length, 2);
  return Buffer.concat([head, body]);
}

/**
 * A valid SWF (stage 100x50 px, 24 fps) holding `tags` and a closing End. `signature`
 * FWS or CWS; ZWS needs LZMA, which the harness never writes.
 */
function swfBytes(tags = [], { version = 9, signature = 'FWS', frameCount = 1 } = {}) {
  const rate = Buffer.alloc(4);
  rate.writeUInt16LE(24 * 256, 0);
  rate.writeUInt16LE(frameCount, 2);
  const body = Buffer.concat([bitFields([[12, 5], [0, 12], [2000, 12], [0, 12], [1000, 12]]), rate, ...tags, tagBytes(0)]);
  const head = Buffer.concat([Buffer.from(signature, 'latin1'), Buffer.alloc(5)]);
  head[3] = version;
  head.writeUInt32LE(8 + body.length, 4);
  return Buffer.concat([head, signature === 'CWS' ? zlib.deflateSync(body) : body]);
}

/** A small SWF with a few definitions, enough for the harness's derived reads. */
function defaultSwf() {
  return swfBytes([tagBytes(69, [8, 0, 0, 0]), tagBytes(2, [1, 0, 8, 0, 0, 0, 0, 0]), tagBytes(1)]);
}

/** A throwaway document the scripted engines can "open" (they never parse it). */
function sampleFile(dir, name = 'sample.swf', content = defaultSwf()) {
  const file = path.join(dir, name);
  fs.writeFileSync(file, content);
  return file;
}

/**
 * Scripted engines behind a real Harness. Returns `{harness, ws, sample, requests(id), cleanup()}`.
 * @param {Array<object>} engines see makeWorkspace
 * @param {{env?: object, protocol?: object, supervise?: boolean}} [options]
 */
function makeHarness(engines, options = {}) {
  // Loaded here so the testkit stays usable by tests of the lower layers alone.
  const { Harness } = require('../harness/harness');
  const ws = makeWorkspace(engines, options);
  const harness = new Harness({
    roots: [ws.root],
    env: { ...process.env, ...(options.env || {}) },
    stateRoot: path.join(ws.root, 'state'),
    supervise: options.supervise === true,
    electron: false,
    nodePath: process.execPath,
  });
  const sample = sampleFile(ws.root, 'sample.swf', options.sampleBytes);
  return {
    harness,
    ws,
    sample,
    requests: ws.requests,
    cleanup: async () => {
      await harness.close();
      ws.cleanup();
    },
  };
}

module.exports = {
  FAKE_ENGINE, REAL_PROTOCOL, tempDir, removeDir, fakeWorker, isAlive, waitUntil, makeWorkspace, sampleFile, makeHarness,
  bitFields, tagBytes, swfBytes, defaultSwf,
};
