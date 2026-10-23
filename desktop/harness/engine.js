'use strict';
const path = require('node:path');
const fs = require('node:fs');
const { EventEmitter } = require('node:events');
const { WorkerProcess } = require('./worker');
const { adapterFor } = require('./adapters');
const { HarnessError, KINDS } = require('./errors');

/** A worker that fails to start this many times in a row is left alone for a while. */
const MAX_START_FAILURES = 3;
const COOLDOWN_MS = 30000;
const HANDSHAKE_TIMEOUT_MS = 15000;

/**
 * One engine at run time: its worker process (started on demand, restarted
 * after a crash), the adapter that translates the protocol to what the worker
 * speaks, and which document the worker currently has open.
 */
class Engine extends EventEmitter {
  /**
   * @param {import('./registry').EngineInfo} info
   * @param {object} options
   * @param {import('./registry').Registry} options.registry
   * @param {string} options.stateRoot folder for per-engine state (PySWF's recovery project)
   * @param {object} [options.env]
   * @param {boolean} [options.supervise] wrap workers in `rustswf supervise` when it is available
   * @param {Function} [options.spawn] test hook
   */
  constructor(info, options) {
    super();
    this.info = info;
    this.id = info.id;
    this.registry = options.registry;
    this.stateRoot = options.stateRoot;
    this.env = options.env || process.env;
    this.supervise = options.supervise !== false;
    this.spawnImpl = options.spawn;
    this.adapter = info.worker ? adapterFor(info.worker.framing) : null;
    this.worker = null;
    this.starting = null;
    this.openedKey = null;
    this.startFailures = 0;
    this.lastFailureAt = 0;
    this.spawns = 0;
    this.restarts = 0;
    this.capabilities = null;
    this.mismatch = null;
    this.memoryMiB = info.worker ? this.registry.protocol.memoryMiB(info.worker.memoryMiB, this.env) : null;
    this.supervised = false;
  }

  get running() {
    return Boolean(this.worker && this.worker.alive);
  }

  get stateDir() {
    return path.join(this.stateRoot, this.id);
  }

  /** The command line really executed: the worker, or the supervisor around it. */
  _commandLine() {
    const launch = this.info.launch;
    const supervisor = this.supervise ? this.registry.supervisorPath : null;
    if (supervisor && this.memoryMiB) {
      this.supervised = true;
      return {
        command: supervisor,
        args: ['supervise', '--memory-mib', String(this.memoryMiB), '--', launch.command, ...launch.args],
      };
    }
    this.supervised = false;
    return { command: launch.command, args: launch.args };
  }

  _environment() {
    const launch = this.info.launch;
    const env = { ...this.env, ...launch.env };
    for (const [key, value] of Object.entries(env)) {
      if (typeof value === 'string' && value.includes('{state_dir}')) env[key] = value.replaceAll('{state_dir}', this.stateDir);
    }
    return env;
  }

  /** Start the worker if it is not running. Concurrent callers share one start. */
  async ensureStarted() {
    if (this.running) return this.worker;
    if (this.starting) return this.starting;
    if (!this.info.available || !this.info.launch) {
      throw new HarnessError(KINDS.UNAVAILABLE, `${this.info.name} no está disponible: ${this.info.reason}`, { engine: this.id });
    }
    if (this.startFailures >= MAX_START_FAILURES && Date.now() - this.lastFailureAt < COOLDOWN_MS) {
      throw new HarnessError(KINDS.UNAVAILABLE, `${this.info.name} falló ${this.startFailures} veces al iniciar; se reintentará en unos segundos`, { engine: this.id });
    }
    this.starting = this._start().finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  async _start() {
    fs.mkdirSync(this.stateDir, { recursive: true });
    const { command, args } = this._commandLine();
    const worker = new WorkerProcess({
      name: this.id,
      command,
      args,
      cwd: this.info.launch.cwd,
      env: this._environment(),
      framing: this.info.worker.framing,
      spawn: this.spawnImpl,
    });
    try {
      await worker.start();
      if (this.info.worker.framing === 'v2') await this._handshake(worker);
    } catch (error) {
      this.startFailures += 1;
      this.lastFailureAt = Date.now();
      worker.kill();
      throw error instanceof HarnessError ? error : new HarnessError(KINDS.UNAVAILABLE, `No se pudo iniciar ${this.info.name}: ${error.message}`, { engine: this.id });
    }
    if (this.spawns > 0) this.restarts += 1;
    this.spawns += 1;
    this.startFailures = 0;
    this.worker = worker;
    this.openedKey = null;
    worker.once('exit', (event) => {
      if (this.worker === worker) {
        this.worker = null;
        this.openedKey = null;
      }
      this.emit('exit', { engine: this.id, ...event });
    });
    this.emit('start', { engine: this.id, pid: worker.pid, supervised: this.supervised, memory_mib: this.memoryMiB });
    return worker;
  }

  /** A v2 worker must answer `capabilities` with its protocol version and operations. */
  async _handshake(worker) {
    const caps = await worker.request('capabilities', {}, { timeoutMs: HANDSHAKE_TIMEOUT_MS });
    if (!caps || !caps.protocol || caps.protocol.version !== 2) {
      throw new HarnessError(KINDS.PROTOCOL, `${this.info.name} no habla el protocolo v2`, { engine: this.id });
    }
    const declared = new Set(this.info.worker.ops);
    const offered = new Set(caps.ops || []);
    this.capabilities = caps;
    this.mismatch = {
      missing: [...declared].filter((op) => !offered.has(op)),
      extra: [...offered].filter((op) => !declared.has(op)),
    };
  }

  /**
   * Run a protocol operation through this engine's adapter.
   * @param {string} op
   * @param {object} args
   * @param {{document?: object, timeoutMs?: number, signal?: AbortSignal}} [options]
   */
  async call(op, args = {}, options = {}) {
    if (!this.adapter) throw new HarnessError(KINDS.UNSUPPORTED, `${this.info.name} no tiene worker`, { engine: this.id });
    const timeoutMs = options.timeoutMs ?? this.registry.protocol.timeoutMs('default', this.env);
    const rpc = async (nativeOp, nativeArgs = {}, rpcOptions = {}) => {
      const worker = await this.ensureStarted();
      return worker.request(nativeOp, nativeArgs, { timeoutMs: rpcOptions.timeoutMs ?? timeoutMs, signal: options.signal });
    };
    return this.adapter.invoke(op, args, {
      rpc,
      info: this.info,
      engine: this,
      document: options.document,
      protocol: this.registry.protocol,
      timeoutMs,
      signal: options.signal,
    });
  }

  /** Kill the worker now (cancel). Whatever was open is forgotten; the next call restarts it. */
  cancel(message) {
    this.openedKey = null;
    if (this.worker) this.worker.kill(message);
  }

  async stop() {
    this.openedKey = null;
    const worker = this.worker;
    this.worker = null;
    if (worker) await worker.stop();
  }

  status() {
    return {
      id: this.id,
      running: this.running,
      pid: this.worker ? this.worker.pid : null,
      supervised: this.supervised,
      memory_mib: this.memoryMiB,
      spawns: this.spawns,
      restarts: this.restarts,
      start_failures: this.startFailures,
      requests: this.worker ? this.worker.requestsServed : 0,
      opened: this.openedKey,
      mismatch: this.mismatch,
    };
  }
}

module.exports = { Engine, MAX_START_FAILURES, COOLDOWN_MS };
