'use strict';
const childProcess = require('node:child_process');
const { EventEmitter } = require('node:events');
const { StringDecoder } = require('node:string_decoder');
const { codecFor } = require('./framing');
const { HarnessError, KINDS } = require('./errors');

// Same limits as the Tauri supervisor (src-tauri/src/suite.rs).
const MAX_LINE_BYTES = 16 * 1024 * 1024;
const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
const MAX_STDERR_BYTES = 8192;
const OP_NAME = /^[A-Za-z0-9_]{1,64}$/;
/** After `exit`, how long to wait for the stdio pipes to drain before failing pending work. */
const DRAIN_MS = 250;

/**
 * Kill a process and everything it started. `taskkill /T` is the Windows way:
 * compilers, JVMs and the like are grandchildren of the worker.
 */
function killTree(child) {
  if (!child || child.pid === undefined || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === 'win32') {
    childProcess.spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
      windowsHide: true,
      timeout: 5000,
      stdio: 'ignore',
    });
  } else {
    try {
      process.kill(-child.pid, 'SIGKILL');
    } catch {
      /* not a group leader; fall through to the direct kill */
    }
  }
  try {
    child.kill('SIGKILL');
  } catch {
    /* already gone */
  }
}

/**
 * One engine process speaking JSONL on stdin/stdout: one request in flight at
 * a time (every worker reads its pipe sequentially), a deadline per request,
 * the whole process tree killed on timeout, cancel or protocol violation, and a
 * bounded copy of stderr for diagnostics.
 */
class WorkerProcess extends EventEmitter {
  /**
   * @param {object} options
   * @param {string} options.name engine name for messages
   * @param {string} options.command executable
   * @param {string[]} [options.args]
   * @param {string} [options.cwd]
   * @param {object} [options.env] full environment of the child
   * @param {'v2'|'legacy'|'suite'} [options.framing]
   * @param {Function} [options.spawn] injectable for tests
   * @param {Function} [options.killTree] injectable for tests
   */
  constructor(options) {
    super();
    this.name = options.name || 'worker';
    this.command = options.command;
    this.args = options.args || [];
    this.cwd = options.cwd;
    this.env = options.env;
    this.codec = codecFor(options.framing || 'v2');
    this.spawnImpl = options.spawn || childProcess.spawn;
    this.killTreeImpl = options.killTree || killTree;
    this.child = null;
    this.exited = false;
    this.exitInfo = null;
    this.nextId = 1;
    this.queue = [];
    this.current = null;
    this.partial = '';
    this.partialBytes = 0;
    this.decoder = new StringDecoder('utf8');
    this.stderrChunks = [];
    this.stderrBytes = 0;
    this.startedAt = 0;
    this.requestsServed = 0;
  }

  get pid() {
    return this.child ? this.child.pid : undefined;
  }

  get alive() {
    return this.child !== null && !this.exited;
  }

  /** Last bytes the worker wrote to stderr (at most 8 KiB). */
  stderrTail() {
    return Buffer.concat(this.stderrChunks).toString('utf8').trim();
  }

  start() {
    if (this.child) return Promise.reject(new Error(`${this.name} ya se inició`));
    return new Promise((resolve, reject) => {
      let spawned = false;
      let child;
      try {
        child = this.spawnImpl(this.command, this.args, {
          cwd: this.cwd,
          env: this.env,
          stdio: ['pipe', 'pipe', 'pipe'],
          windowsHide: true,
          detached: process.platform !== 'win32',
        });
      } catch (error) {
        reject(new HarnessError(KINDS.UNAVAILABLE, `No se pudo iniciar ${this.name}: ${error.message}`, { engine: this.name }));
        return;
      }
      this.child = child;
      this.startedAt = Date.now();
      child.stdin.on('error', () => {
        /* a dead worker is reported through its exit; EPIPE here is the same event */
      });
      child.stdout.on('data', (chunk) => this._onStdout(chunk));
      child.stderr.on('data', (chunk) => this._onStderr(chunk));
      child.on('error', (error) => {
        if (!spawned) {
          this.exited = true;
          reject(new HarnessError(KINDS.UNAVAILABLE, `No se pudo iniciar ${this.name}: ${error.message}`, { engine: this.name }));
        } else {
          this._failEverything(new HarnessError(KINDS.CRASHED, `${this.name} falló: ${error.message}`, { engine: this.name }));
        }
      });
      child.once('spawn', () => {
        spawned = true;
        resolve();
      });
      let drained = false;
      const finish = () => {
        if (drained) return;
        drained = true;
        this._onExit();
      };
      child.on('exit', (code, signal) => {
        this.exitInfo = { code, signal };
        this.exited = true;
        setTimeout(finish, DRAIN_MS).unref();
      });
      child.on('close', finish);
    });
  }

  _onStderr(chunk) {
    this.stderrChunks.push(chunk);
    this.stderrBytes += chunk.length;
    while (this.stderrBytes > MAX_STDERR_BYTES && this.stderrChunks.length > 0) {
      const first = this.stderrChunks[0];
      const excess = this.stderrBytes - MAX_STDERR_BYTES;
      if (first.length <= excess) {
        this.stderrChunks.shift();
        this.stderrBytes -= first.length;
      } else {
        this.stderrChunks[0] = first.subarray(excess);
        this.stderrBytes -= excess;
      }
    }
  }

  _onStdout(chunk) {
    this.partialBytes += chunk.length;
    this.partial += this.decoder.write(chunk);
    for (;;) {
      const newline = this.partial.indexOf('\n');
      if (newline < 0) break;
      const line = this.partial.slice(0, newline).replace(/\r$/, '');
      this.partial = this.partial.slice(newline + 1);
      this.partialBytes = Buffer.byteLength(this.partial);
      if (line.trim() !== '') this._onLine(line);
      if (!this.alive) return;
    }
    if (this.partialBytes > MAX_LINE_BYTES) {
      this._protocolError(`Respuesta JSONL mayor a ${MAX_LINE_BYTES / (1024 * 1024)} MiB`);
    }
  }

  _onLine(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch (error) {
      this._protocolError(`Respuesta JSONL inválida: ${error.message}`);
      return;
    }
    const decoded = this.codec.decode(message);
    if (decoded === null) {
      this._protocolError('Respuesta incompatible con el protocolo JSONL del motor');
      return;
    }
    const entry = this.current;
    if (!entry) {
      this._protocolError('Respuesta sin solicitud pendiente');
      return;
    }
    if (decoded.id === null && !decoded.ok) {
      this._protocolError(decoded.error.message);
      return;
    }
    if (decoded.id !== String(entry.id)) {
      this._protocolError('Respuesta fuera de secuencia');
      return;
    }
    this.current = null;
    clearTimeout(entry.timer);
    entry.cleanup();
    this.requestsServed += 1;
    if (decoded.ok) {
      entry.resolve(decoded.result);
    } else {
      entry.reject(new HarnessError(decoded.error.kind, decoded.error.message, { engine: this.name }));
    }
    this._pump();
  }

  _protocolError(message) {
    const error = new HarnessError(KINDS.PROTOCOL, `${this.name}: ${message}`, { engine: this.name, stderr: this.stderrTail() || undefined });
    this._failEverything(error);
    this._terminate();
  }

  /**
   * Queue a request. Resolves with the engine's result; rejects with a
   * HarnessError (`kind` from the worker, or timeout / crashed / cancelled /
   * protocol / limit / bad_request).
   */
  request(op, args = {}, { timeoutMs = 30000, signal } = {}) {
    if (typeof op !== 'string' || !OP_NAME.test(op)) {
      return Promise.reject(new HarnessError(KINDS.BAD_REQUEST, 'Nombre de operación inválido'));
    }
    if (args === null || typeof args !== 'object' || Array.isArray(args)) {
      return Promise.reject(new HarnessError(KINDS.BAD_REQUEST, 'Los argumentos deben ser un objeto JSON'));
    }
    if (signal && signal.aborted) {
      return Promise.reject(new HarnessError(KINDS.CANCELLED, 'Operación cancelada', { engine: this.name }));
    }
    if (!this.alive) {
      return Promise.reject(new HarnessError(KINDS.CRASHED, `${this.name} no está en ejecución`, { engine: this.name }));
    }
    const id = this.nextId++;
    let payload;
    try {
      payload = `${JSON.stringify(this.codec.encode(id, op, args))}\n`;
    } catch (error) {
      return Promise.reject(new HarnessError(KINDS.BAD_REQUEST, `Argumentos no serializables: ${error.message}`));
    }
    if (Buffer.byteLength(payload) > MAX_REQUEST_BYTES) {
      return Promise.reject(new HarnessError(KINDS.LIMIT, 'Solicitud mayor a 8 MiB', { engine: this.name }));
    }
    return new Promise((resolve, reject) => {
      const entry = { id, op, payload, timeoutMs, resolve, reject, timer: null, cleanup: () => {} };
      if (signal) {
        const onAbort = () => this._abort(entry);
        signal.addEventListener('abort', onAbort, { once: true });
        entry.cleanup = () => signal.removeEventListener('abort', onAbort);
      }
      this.queue.push(entry);
      this._pump();
    });
  }

  _abort(entry) {
    const cancelled = new HarnessError(KINDS.CANCELLED, 'Operación cancelada', { engine: this.name });
    const at = this.queue.indexOf(entry);
    if (at >= 0) {
      this.queue.splice(at, 1);
      entry.cleanup();
      entry.reject(cancelled);
    } else if (this.current === entry) {
      // The worker cannot abandon a request half way: it is retired and restarted on demand.
      this._failEverything(cancelled);
      this._terminate();
    }
  }

  _pump() {
    if (this.current !== null) return;
    if (!this.alive) {
      this._failEverything(new HarnessError(KINDS.CRASHED, `${this.name} no está en ejecución`, { engine: this.name }));
      return;
    }
    const entry = this.queue.shift();
    if (!entry) return;
    this.current = entry;
    entry.timer = setTimeout(() => this._onTimeout(entry), entry.timeoutMs);
    entry.timer.unref?.();
    this.child.stdin.write(entry.payload, (error) => {
      if (error && this.current === entry) {
        this._failEverything(new HarnessError(KINDS.CRASHED, `No se pudo enviar la solicitud a ${this.name}: ${error.message}`, { engine: this.name }));
        this._terminate();
      }
    });
  }

  _onTimeout(entry) {
    if (this.current !== entry) return;
    const seconds = Math.round(entry.timeoutMs / 100) / 10;
    const error = new HarnessError(
      KINDS.TIMEOUT,
      `Límite de ${seconds} segundos agotado en ${this.name}.${entry.op}. El proyecto se recuperará en la próxima operación`,
      { engine: this.name, stderr: this.stderrTail() || undefined },
    );
    this._failEverything(error);
    this._terminate();
  }

  /** Reject the request in flight with `error` and everything queued behind it as crashed. */
  _failEverything(error) {
    const entry = this.current;
    this.current = null;
    if (entry) {
      clearTimeout(entry.timer);
      entry.cleanup();
      entry.reject(error);
    }
    const queued = this.queue.splice(0);
    for (const waiting of queued) {
      waiting.cleanup();
      const detail = error.kind === KINDS.CANCELLED ? 'cancelada' : `descartada (${error.message})`;
      waiting.reject(
        new HarnessError(error.kind === KINDS.CANCELLED ? KINDS.CANCELLED : KINDS.CRASHED, `Solicitud ${detail}`, { engine: this.name }),
      );
    }
  }

  _terminate() {
    this.exited = true;
    this.killTreeImpl(this.child);
  }

  _onExit() {
    this.exited = true;
    const { code, signal } = this.exitInfo || {};
    const stderr = this.stderrTail();
    this._failEverything(
      new HarnessError(
        KINDS.CRASHED,
        `${this.name} terminó inesperadamente (código ${code ?? 'n/d'}${signal ? `, señal ${signal}` : ''})${stderr ? `: ${stderr}` : ''}`,
        { engine: this.name, stderr: stderr || undefined },
      ),
    );
    this.emit('exit', { code, signal });
  }

  /** Reject everything in flight as cancelled and kill the process tree. */
  kill(message = 'Operación cancelada') {
    if (!this.child) return;
    this._failEverything(new HarnessError(KINDS.CANCELLED, message, { engine: this.name }));
    this._terminate();
  }

  /** Polite shutdown: close stdin (EOF ends every worker), then kill the tree if it lingers. */
  async stop({ graceMs = 1500 } = {}) {
    if (!this.child) return;
    const child = this.child;
    if (child.exitCode !== null || child.signalCode !== null) {
      this.exited = true;
      return;
    }
    this._failEverything(new HarnessError(KINDS.CANCELLED, 'Motor detenido', { engine: this.name }));
    await new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(() => {
        this.killTreeImpl(child);
        setTimeout(finish, 100).unref();
      }, graceMs);
      child.once('exit', finish);
      try {
        child.stdin.end();
      } catch {
        this.killTreeImpl(child);
      }
    });
    this.exited = true;
  }
}

module.exports = { WorkerProcess, killTree, MAX_LINE_BYTES, MAX_REQUEST_BYTES, MAX_STDERR_BYTES };
