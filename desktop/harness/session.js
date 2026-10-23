'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { HarnessError, KINDS } = require('./errors');

const MAX_INPUT_BYTES = 100 * 1024 * 1024;
/** How many working copies a session keeps: the current revision and the ones just before it. */
const KEEP_REVISIONS = 4;

/**
 * Many readers or one writer, first come first served so a mutation is never
 * starved by a stream of reads. Reads of different engines overlap; an edit
 * waits for them and blocks new ones until the document has moved to its new revision.
 */
class RwGate {
  constructor() {
    this.readers = 0;
    this.writing = false;
    this.queue = [];
  }

  read(fn) {
    return this._run('r', fn);
  }

  write(fn) {
    return this._run('w', fn);
  }

  async _run(kind, fn) {
    await new Promise((resolve) => {
      this.queue.push({ kind, resolve });
      this._drain();
    });
    try {
      return await fn();
    } finally {
      if (kind === 'r') this.readers -= 1;
      else this.writing = false;
      this._drain();
    }
  }

  _drain() {
    while (this.queue.length > 0) {
      const head = this.queue[0];
      if (head.kind === 'w') {
        if (this.readers > 0 || this.writing) return;
        this.queue.shift();
        this.writing = true;
        head.resolve();
        return;
      }
      if (this.writing) return;
      this.queue.shift();
      this.readers += 1;
      head.resolve();
    }
  }
}

function sha256File(file) {
  const hash = crypto.createHash('sha256');
  const fd = fs.openSync(file, 'r');
  try {
    const block = Buffer.alloc(1 << 20);
    for (;;) {
      const read = fs.readSync(fd, block, 0, block.length, null);
      if (read === 0) break;
      hash.update(block.subarray(0, read));
    }
  } finally {
    fs.closeSync(fd);
  }
  return hash.digest('hex');
}

/**
 * The open document. The user's file is copied once to a working file inside the
 * session folder and never touched again: engines read `working-NNN.swf`, and
 * every change PySWF commits becomes the next numbered copy. `revision` counts
 * those changes; `sha256` is the hash of the current bytes (what PySWF calls
 * `revision` and expects back as `base_revision`).
 */
class Session {
  constructor(harness, { id, dir, sourcePath }) {
    this.harness = harness;
    this.id = id;
    this.dir = dir;
    this.sourcePath = sourcePath;
    this.filename = path.basename(sourcePath);
    this.revision = 0;
    this.sha256 = null;
    this.workingPath = null;
    this.state = null;
    this.gate = new RwGate();
    this.openFailures = new Map();
    this.opening = new Map();
    this.openedBy = null;
    this.disposed = false;
  }

  /**
   * Copy `sourcePath` into a new session folder as revision 0. A PySWF project is not
   * a SWF: its working movie arrives from PySWF and becomes revision 0 then.
   */
  static create(harness, sourcePath, { project = false } = {}) {
    const stat = fs.statSync(sourcePath);
    if (!stat.isFile()) throw new HarnessError(KINDS.BAD_REQUEST, 'La ruta no es un archivo');
    if (!project && stat.size > MAX_INPUT_BYTES) throw new HarnessError(KINDS.LIMIT, 'El SWF supera 100 MiB');
    const id = crypto.randomUUID();
    const dir = path.join(harness.stateRoot, id);
    fs.mkdirSync(dir, { recursive: true });
    const session = new Session(harness, { id, dir, sourcePath });
    if (!project) session.adoptFile(sourcePath);
    return session;
  }

  _revisionFile(revision) {
    return path.join(this.dir, `working-${String(revision).padStart(3, '0')}.swf`);
  }

  /** Make the bytes of `file` the current revision (revision 0 the first time). */
  adoptFile(file) {
    const first = this.workingPath === null;
    if (!first) this.revision += 1;
    const target = this._revisionFile(this.revision);
    fs.copyFileSync(file, target);
    this.workingPath = target;
    this.sha256 = sha256File(target);
    if (!first) this._prune();
    return this.revision;
  }

  _prune() {
    for (let old = this.revision - KEEP_REVISIONS; old >= 0; old -= 1) { // keeps revision-KEEP+1 .. revision
      try {
        fs.unlinkSync(this._revisionFile(old));
      } catch {
        break;
      }
    }
  }

  key() {
    return `${this.id}:${this.revision}`;
  }

  /** What adapters get to read (the derived header and raw bytes). */
  documentInfo() {
    return {
      sessionId: this.id,
      workingPath: this.workingPath,
      revision: this.revision,
      sha256: this.sha256,
      sourcePath: this.sourcePath,
      dir: this.dir,
    };
  }

  /**
   * Make sure `target` has this revision of the document open. Concurrent calls share
   * one open; an engine that rejected this revision is not asked again for it.
   */
  ensureOpen(target, options = {}) {
    const key = this.key();
    if (target.running && target.openedKey === key) return Promise.resolve();
    if (this.openFailures.get(target.id) === this.revision) {
      return Promise.reject(new HarnessError(KINDS.FAILED, `${target.info.name} no pudo abrir esta revisión del documento`, { engine: target.id }));
    }
    const pending = this.opening.get(target.id);
    if (pending && pending.key === key) return pending.promise;
    const promise = (target.info.worker.authority ? this._openAuthority(target, options) : this._openReader(target, options)).finally(() => {
      if (this.opening.get(target.id)?.promise === promise) this.opening.delete(target.id);
    });
    this.opening.set(target.id, { key, promise });
    return promise;
  }

  async _openReader(target, { timeoutMs, signal } = {}) {
    const key = this.key();
    const revision = this.revision;
    try {
      await target.call('open', { path: this.workingPath }, { document: this.documentInfo(), timeoutMs, signal });
    } catch (error) {
      if (error.kind === KINDS.FAILED || error.kind === KINDS.LIMIT) this.openFailures.set(target.id, revision);
      throw error;
    }
    target.openedKey = key;
  }

  /**
   * PySWF keeps the editable project. After a crash its recovery project is still in
   * its state folder: if that holds the current bytes it is adopted as it is (history
   * included), otherwise the working file is opened afresh.
   */
  async _openAuthority(target, { timeoutMs, signal } = {}) {
    const key = this.key();
    if (this.state !== null) {
      try {
        const state = await target.call('state', {}, { document: this.documentInfo(), timeoutMs, signal });
        if (state && state.revision === this.sha256) {
          this.state = state;
          target.openedKey = key;
          return;
        }
      } catch (error) {
        if (error.kind === KINDS.CANCELLED) throw error;
        /* no project in the worker: open the working file below */
      }
    }
    const opened = await target.call('open', { path: this.workingPath }, { document: this.documentInfo(), timeoutMs, signal });
    this.state = opened;
    target.openedKey = key;
  }

  /**
   * After PySWF changed the document, follow it: read its state, and when the bytes
   * differ from the current revision copy them to the next numbered file. Other
   * engines notice through their `openedKey` and re-open lazily.
   * @returns {Promise<boolean>} whether the document changed
   */
  async followAuthority(target, result, options = {}) {
    const usable = result && typeof result.revision === 'string' && typeof result.working_path === 'string';
    const state = usable ? result : await target.call('state', {}, { document: this.documentInfo(), ...options });
    this.state = state;
    const changed = state.revision !== this.sha256;
    if (changed) this.adoptFile(state.working_path);
    target.openedKey = this.key();
    return changed;
  }

  /** Forget what the engines have open (cancel, engine restart). */
  forgetEngines() {
    this.opening.clear();
  }

  async dispose() {
    if (this.disposed) return;
    this.disposed = true;
    try {
      fs.rmSync(this.dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch {
      /* the temp folder is cleaned by the system eventually */
    }
  }
}

module.exports = { Session, RwGate, sha256File, MAX_INPUT_BYTES, KEEP_REVISIONS };
