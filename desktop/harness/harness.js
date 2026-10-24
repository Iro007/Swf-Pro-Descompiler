'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');
const { Registry } = require('./registry');
const { Engine } = require('./engine');
const { Session, sha256File } = require('./session');
const { HarnessError, KINDS, allowsFallback, asHarnessError } = require('./errors');
const { score, isClean, pickBest } = require('./scoring');
const { isClassUnit } = require('./adapters/normalize');
const builtin = require('./builtin');
const { boundedEnv } = require('./protocol');

const PROJECT_FILE = /\.(pyswfproj|flashproj)$/i;
/** Arguments that steer the harness and never reach a worker. */
const HARNESS_ARGS = ['engine', 'engines', 'strict', 'session_id', 'include_jpexs'];
/** Canonical edits that PySWF refuses unless they name the revision they were made against. */
const NEEDS_BASE_REVISION = new Set(['assemble', 'compile', 'replace_resource']);

const isPlainObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

/**
 * Where manifests and worker binaries are looked for, in order: the packaged
 * application's resources, the repository, then FLASH_ENGINE_ROOTS.
 */
function defaultRoots({ resourcesPath, repoRoot, env = process.env } = {}) {
  const roots = [];
  if (resourcesPath) roots.push(resourcesPath);
  if (repoRoot) roots.push(repoRoot);
  for (const extra of (env.FLASH_ENGINE_ROOTS || '').split(path.delimiter)) if (extra) roots.push(path.resolve(extra));
  return [...new Set(roots)];
}

/**
 * The editing UI reads a PySWF state after `open` (revision, history, validation). When the
 * engine that opened the document is not the session owner, the harness completes the
 * answer from what it knows; PySWF takes over the document when the first edit asks for it.
 */
function withSuiteState(session, out) {
  const info = out.info || {};
  const notes = Array.isArray(out.diagnostics) ? out.diagnostics : [];
  const defaults = {
    revision: session.sha256,
    dirty: false,
    history: { undo: false, redo: false },
    script_count: null,
    signature: info.signature ?? null,
    frame_count: info.frame_count ?? null,
    validation: {
      valid: true,
      errors: [],
      warnings: notes,
      signature: info.signature ?? null,
      version: info.version ?? null,
      tag_count: info.tag_count ?? null,
      frame_count: info.frame_count ?? null,
    },
  };
  for (const [key, value] of Object.entries(defaults)) if (out[key] === undefined || out[key] === null) out[key] = value;
  return out;
}

/**
 * Engines read the working copy (`working-000.swf`), so their answers name the document
 * after it. The person knows it by the file they opened, and the revision of an answer
 * that describes the document is the hash of the bytes it was read from.
 */
function nameDocument(session, op, result) {
  if (!result || typeof result !== 'object' || Array.isArray(result)) return;
  const describesDocument = op === 'header' || op === 'tag_tree';
  if (describesDocument || typeof result.filename === 'string') result.filename = session.filename;
  if (typeof result.source_path === 'string') result.source_path = session.sourcePath;
  if (describesDocument && result.revision === undefined) result.revision = session.sha256;
}

function attemptOf(info, ms, error) {
  const base = { engine: info.id, name: info.name, ms };
  if (info.worker && info.worker.via) base.via = info.worker.via;
  return error ? { ...base, ok: false, kind: error.kind, error: error.message } : { ...base, ok: true };
}

class Harness extends EventEmitter {
  /**
   * @param {object} options
   * @param {string[]} options.roots see defaultRoots
   * @param {object} [options.env]
   * @param {string} [options.stateRoot] where sessions keep their working files
   * @param {boolean} [options.supervise] wrap workers in `rustswf supervise` (default true)
   * @param {string} [options.nodePath]
   * @param {boolean} [options.electron]
   * @param {Function} [options.spawn] test hook for worker processes
   */
  constructor(options) {
    super();
    this.env = options.env || process.env;
    this.roots = options.roots;
    this.registry =
      options.registry ||
      Registry.load({ roots: this.roots, env: this.env, nodePath: options.nodePath, electron: options.electron, platform: options.platform });
    this.protocol = this.registry.protocol;
    this.stateRoot = options.stateRoot || path.join(os.tmpdir(), 'flash-suite', crypto.randomUUID());
    fs.mkdirSync(this.stateRoot, { recursive: true });
    this.engines = new Map();
    for (const info of this.registry.workers().filter((e) => !e.worker.via)) {
      const engine = new Engine(info, { registry: this.registry, stateRoot: this.stateRoot, env: this.env, supervise: options.supervise, spawn: options.spawn });
      engine.on('exit', (event) => this.emit('engine-exit', event));
      engine.on('start', (event) => this.emit('engine-start', event));
      this.engines.set(info.id, engine);
    }
    this.session = null;
    this.epoch = 0;
    this._hierarchy = undefined;
    this.closed = false;
  }

  static create(options) {
    return new Harness(options);
  }

  get authority() {
    const info = this.registry.authority;
    return info ? this.engines.get(info.id) : null;
  }

  /** The engine process that runs `info` (a proxy such as JPEXS runs inside another). */
  _target(info) {
    return this.engines.get(info.worker.via || info.id);
  }

  /** Engines with their capability levels and, for the ones that have run, their process state. */
  listEngines() {
    return this.registry.summary().map((entry) => {
      const engine = this.engines.get(entry.id);
      return { ...entry, runtime: engine ? engine.status() : null };
    });
  }

  /** The player-SDK class hierarchy that lets RustSWF prove a class's ancestors, if installed. */
  hierarchyContract() {
    if (this._hierarchy !== undefined) return this._hierarchy;
    this._hierarchy = null;
    const contract = this.protocol.raw.contracts && this.protocol.raw.contracts.hierarchy;
    if (contract) {
      for (const root of [this.env.FLASH_SUITE_WORKSPACE, ...this.roots].filter(Boolean)) {
        const file = path.join(root, contract.path);
        try {
          if (fs.existsSync(file) && sha256File(file) === contract.sha256) {
            this._hierarchy = { hierarchy_swc: file, hierarchy_sha256: contract.sha256 };
            break;
          }
        } catch {
          /* unreadable: treated as absent */
        }
      }
    }
    return this._hierarchy;
  }

  _checkEpoch(ctx) {
    if (ctx.epoch !== this.epoch) throw new HarnessError(KINDS.CANCELLED, 'Operación cancelada');
  }

  /**
   * Run one operation and return its envelope; throws a HarnessError (with `attempts`) on failure.
   *
   * @param {string} op a protocol operation or an engine's native one
   * @param {object} [args]
   * @param {{signal?: AbortSignal}} [options]
   */
  async call(op, args = {}, options = {}) {
    const started = Date.now();
    if (this.closed) throw new HarnessError(KINDS.CANCELLED, 'La aplicación se está cerrando');
    if (typeof op !== 'string' || op === '') throw new HarnessError(KINDS.BAD_REQUEST, 'Falta el nombre de la operación');
    if (!isPlainObject(args)) throw new HarnessError(KINDS.BAD_REQUEST, 'Los argumentos deben ser un objeto');
    // `load_project` is how the interface opens a PySWF project: the same as `open` on a project file.
    if (op === 'open' || op === 'load_project') return this._open(args, options, started, op);
    const spec = this.registry.opSpec(op);
    if (!spec) throw new HarnessError(KINDS.BAD_REQUEST, `Operación desconocida: ${op}`);
    const session = spec.document === false ? null : this.session;
    if (spec.document !== false) {
      if (!session) throw new HarnessError(KINDS.NOT_OPEN, 'No hay SWF abierto');
      if (args.session_id !== undefined && args.session_id !== session.id) {
        throw new HarnessError(KINDS.STALE, 'Sesión reemplazada; vuelva a seleccionar el recurso');
      }
    }
    const ctx = { session, signal: options.signal, epoch: this.epoch, changed: false };
    const run = () => (spec.name === 'decompile' && (args.engine === undefined || args.engine === 'auto') ? this._auto(spec, args, ctx) : this._route(spec, args, ctx));
    const outcome = session ? await (spec.mutates ? session.gate.write(run) : session.gate.read(run)) : await run();
    return {
      ok: true,
      op,
      session_id: session ? session.id : null,
      revision: session ? session.revision : null,
      sha256: session ? session.sha256 : null,
      served_by: outcome.served_by,
      ...(outcome.via ? { via: outcome.via } : {}),
      attempts: outcome.attempts,
      changed: ctx.changed,
      elapsed_ms: Date.now() - started,
      result: outcome.result,
    };
  }

  /** Like `call`, but never throws: a failure is `{ok:false, error:{message, kind, attempts}}`. */
  async request(op, args = {}, options = {}) {
    const started = Date.now();
    try {
      return await this.call(op, args, options);
    } catch (raw) {
      const error = asHarnessError(raw);
      return {
        ok: false,
        op,
        session_id: this.session ? this.session.id : null,
        revision: this.session ? this.session.revision : null,
        error: error.toJSON(),
        elapsed_ms: Date.now() - started,
      };
    }
  }

  // ---- open ---------------------------------------------------------------------

  async _open(args, options, started, op = 'open') {
    if (typeof args.path !== 'string' || args.path === '') throw new HarnessError(KINDS.BAD_REQUEST, 'Falta la ruta del SWF');
    const file = path.resolve(args.path);
    let stat;
    try {
      stat = fs.statSync(file);
    } catch {
      throw new HarnessError(KINDS.NOT_FOUND, `No existe el archivo ${file}`);
    }
    if (!stat.isFile()) throw new HarnessError(KINDS.BAD_REQUEST, 'La ruta no es un archivo');
    const project = op === 'load_project' || PROJECT_FILE.test(file);
    const ctx = { session: null, signal: options.signal, epoch: this.epoch, changed: false };
    const session = Session.create(this, file, { project });
    ctx.session = session;
    let outcome;
    try {
      outcome = await session.gate.write(() => this._openInto(session, file, project, ctx));
      this._checkEpoch(ctx);
    } catch (error) {
      await session.dispose();
      throw error;
    }
    const previous = this.session;
    this.session = session;
    if (previous) previous.dispose().catch(() => {});
    return {
      ok: true,
      op,
      session_id: session.id,
      revision: session.revision,
      sha256: session.sha256,
      served_by: outcome.served_by,
      attempts: outcome.attempts,
      changed: true,
      elapsed_ms: Date.now() - started,
      result: outcome.result,
    };
  }

  async _openInto(session, file, project, ctx) {
    const attempts = [];
    const errors = [];
    const providers = this.registry.providers('open');
    const authority = this.registry.authority;
    const order = project ? (authority ? [authority] : []) : [...providers.chain, ...providers.extras];
    if (project && !authority) throw new HarnessError(KINDS.NO_ENGINE, 'Ningún motor abre proyectos (.pyswfproj)');
    const timeoutMs = this.protocol.timeoutMs('default', this.env);
    for (const info of order) {
      const t0 = Date.now();
      try {
        if (info.available === false) throw new HarnessError(KINDS.UNAVAILABLE, `${info.name} no está disponible: ${info.reason}`, { engine: info.id });
        const target = this._target(info);
        const result = await target.call('open', { path: project ? file : session.workingPath }, { document: session.documentInfo(), timeoutMs, signal: ctx.signal });
        this._checkEpoch(ctx);
        if (project) session.adoptFile(result.working_path);
        if (info.worker.authority) session.state = result;
        target.openedKey = session.key();
        session.openedBy = info.id;
        attempts.push(attemptOf(info, Date.now() - t0));
        const out = { ...result, session_id: session.id, source_path: session.sourcePath, working_path: session.workingPath, filename: session.filename };
        if (out.info) out.info = { ...out.info, filename: session.filename };
        return { result: info.worker.authority ? out : withSuiteState(session, out), served_by: info.id, attempts };
      } catch (raw) {
        const error = asHarnessError(raw);
        attempts.push(attemptOf(info, Date.now() - t0, error));
        errors.push(error);
        // An engine that rejects the document would reject it on every later read too.
        if (error.kind === KINDS.FAILED || error.kind === KINDS.LIMIT) session.openFailures.set(info.id, session.revision);
        if (!allowsFallback(error)) break;
      }
    }
    throw this._failure({ name: 'open' }, attempts, errors);
  }

  // ---- routing ------------------------------------------------------------------

  _requested(args) {
    if (args.engine === undefined || args.engine === 'auto') return null;
    if (typeof args.engine !== 'string') throw new HarnessError(KINDS.BAD_REQUEST, 'engine debe ser un texto');
    const info = this.registry.engine(args.engine);
    if (!info) throw new HarnessError(KINDS.BAD_REQUEST, `Motor desconocido: ${args.engine}`);
    return info;
  }

  /**
   * Engines to try, in order. The requested engine goes first when it implements the
   * operation; then the fallback chain (RustSWF -> PySWF -> JPEXS through PySWF), then
   * the remaining engines that implement it.
   */
  _plan(spec, args) {
    if (!spec.canonical) return spec.owners.map((id) => ({ info: this.registry.engine(id) }));
    const requested = this._requested(args);
    const { chain, extras } = this.registry.providers(spec.name);
    const plan = [];
    if (requested) {
      if (requested.implements(spec.name)) plan.push({ info: requested });
      else plan.push({ info: requested, skip: new HarnessError(KINDS.UNSUPPORTED, `${requested.name} no implementa ${spec.name}`, { engine: requested.id }) });
      if (args.strict === true) return plan;
    }
    for (const info of chain) if (!plan.some((p) => p.info === info)) plan.push({ info });
    // The harness reads the header and the raw bytes from the file itself: complete, unlike
    // what the engines that only know `info` can derive, so it goes before them.
    if (builtin.BUILTIN.ops.has(spec.name)) plan.push({ info: builtin.BUILTIN, builtin: true });
    for (const info of extras) if (!plan.some((p) => p.info === info)) plan.push({ info });
    return plan;
  }

  /** Arguments for the worker: harness-only keys removed, proxy and contract arguments added. */
  _prepare(info, spec, args, session) {
    const routed = { ...args };
    for (const key of HARNESS_ARGS) delete routed[key];
    if (info.worker.via) Object.assign(routed, info.worker.viaArgs);
    if (spec.canonical && spec.mutates && NEEDS_BASE_REVISION.has(spec.name) && routed.base_revision === undefined && session) {
      routed.base_revision = session.sha256;
    }
    const wantsContract = (spec.name === 'decompile' && isClassUnit(routed.unit_id)) || spec.name === 'decompile_document' || spec.name === 'source_document';
    if (info.id === 'rust' && wantsContract && routed.hierarchy_swc === undefined) {
      const contract = this.hierarchyContract();
      if (contract) Object.assign(routed, contract);
    }
    return routed;
  }

  async _invoke(info, spec, args, ctx) {
    if (info.available === false) throw new HarnessError(KINDS.UNAVAILABLE, `${info.name} no está disponible: ${info.reason}`, { engine: info.id });
    if (info.available === null) throw new HarnessError(KINDS.UNAVAILABLE, `${info.name} es una herramienta externa`, { engine: info.id });
    const target = this._target(info);
    const session = ctx.session;
    const timeoutMs = ctx.timeoutMs ?? this.protocol.timeoutMs(spec.timeout, this.env);
    if (spec.document !== false && session) await session.ensureOpen(target, { timeoutMs, signal: ctx.signal });
    this._checkEpoch(ctx);
    const routed = this._prepare(info, spec, args, session);
    const result = await target.call(spec.name, routed, { document: session ? session.documentInfo() : undefined, timeoutMs, signal: ctx.signal });
    this._checkEpoch(ctx);
    if (session) nameDocument(session, spec.name, result);
    if (session && spec.mutates && target.info.worker.authority) {
      if (await session.followAuthority(target, result, { timeoutMs, signal: ctx.signal })) ctx.changed = true;
    }
    if (session && spec.name === 'info' && result && result.info) result.info.filename = session.filename;
    return result;
  }

  _builtin(spec, args, ctx) {
    this._checkEpoch(ctx);
    return builtin.run(spec.name, args, ctx.session.documentInfo());
  }

  async _route(spec, args, ctx) {
    const attempts = [];
    const errors = [];
    for (const entry of this._plan(spec, args)) {
      const t0 = Date.now();
      try {
        if (entry.skip) throw entry.skip;
        const result = entry.builtin ? this._builtin(spec, args, ctx) : await this._invoke(entry.info, spec, args, ctx);
        attempts.push(attemptOf(entry.info, Date.now() - t0));
        return { result, served_by: entry.info.id, via: entry.info.worker ? entry.info.worker.via || undefined : undefined, attempts };
      } catch (raw) {
        const error = asHarnessError(raw);
        attempts.push(attemptOf(entry.info, Date.now() - t0, error));
        errors.push(error);
        if (!allowsFallback(error) || args.strict === true) break;
      }
    }
    throw this._failure(spec, attempts, errors);
  }

  /** The error to report when no engine served the operation: the first real failure, with all attempts. */
  _failure(spec, attempts, errors) {
    if (errors.length === 0) {
      return new HarnessError(KINDS.UNSUPPORTED, `Ningún motor implementa ${spec.name}`, { attempts });
    }
    const real = errors.find((e) => e.kind !== KINDS.UNSUPPORTED && e.kind !== KINDS.UNAVAILABLE);
    if (real) return new HarnessError(real.kind, real.message, { attempts, engine: real.engine });
    const detail = attempts.map((a) => `${a.engine}: ${a.error}`).join('; ');
    if (errors.every((e) => e.kind === KINDS.UNSUPPORTED)) {
      return new HarnessError(KINDS.UNSUPPORTED, `${spec.name} no está soportada (${detail})`, { attempts });
    }
    return new HarnessError(KINDS.NO_ENGINE, `Ningún motor pudo atender ${spec.name} (${detail})`, { attempts });
  }

  // ---- auto decompile -----------------------------------------------------------

  /**
   * Ask every source engine at once and keep the best source by the common score. Ties go
   * to an editable result (PySWF), then to the lower manifest order. JPEXS (through PySWF) is asked only when no native engine
   * produced a clean result, unless `include_jpexs` says `always` or `never`.
   */
  async _auto(spec, args, ctx) {
    const unit = args.unit_id;
    const asClass = isClassUnit(unit);
    const wanted = Array.isArray(args.engines) ? args.engines.map((id) => this.registry.normalizeId(id)) : null;
    const pool = this.registry.workers().filter((e) => e.implements('decompile') && !e.worker.via && (wanted ? wanted.includes(e.id) : true));
    const skipped = [];
    const runnable = [];
    for (const info of pool) {
      if (asClass && info.worker.raw.class_units === false) skipped.push(info);
      else runnable.push(info);
    }
    const timeoutMs = boundedEnv(this.env, 'FLASH_AUTO_TIMEOUT_SECONDS', 20, 1, 600) * 1000;
    const forwarded = { ...args };
    delete forwarded.engine;
    const run = async (info) => {
      const t0 = Date.now();
      try {
        const result = await this._invoke(info, spec, forwarded, { ...ctx, timeoutMs });
        return { info, ok: true, result, ms: Date.now() - t0, score: score(result && result.source) };
      } catch (raw) {
        return { info, ok: false, error: asHarnessError(raw), ms: Date.now() - t0, score: null };
      }
    };
    const outcomes = await Promise.all(runnable.map(run));
    this._checkEpoch(ctx);
    const best = () => pickBest(outcomes.filter((o) => o.ok).map((o) => ({ engine: o.info.id, text: o.result.source, order: o.info.order, editable: o.result.editable === true, outcome: o })));
    const jpexs = this.registry.engine('jpexs');
    const mode = args.include_jpexs ?? 'if_needed';
    if (jpexs && jpexs.implements('decompile') && mode !== 'never' && !asClass && (!wanted || wanted.includes('jpexs'))) {
      const current = best();
      if (mode === 'always' || !current || !isClean(current.score)) outcomes.push(await run(jpexs));
    }
    const winner = best();
    const attempts = [
      ...outcomes.map((o) => attemptOf(o.info, o.ms, o.ok ? null : o.error)),
      ...skipped.map((info) => ({ engine: info.id, name: info.name, ms: 0, ok: false, kind: KINDS.UNSUPPORTED, error: `${info.name} trabaja con métodos, no con clases` })),
    ];
    let chosen = winner ? winner.candidate.outcome : null;
    let chosenScore = winner ? winner.score : null;
    if (!chosen) {
      // Nobody produced source. The first engine that answered at least shows its P-code and why.
      chosen = outcomes.filter((o) => o.ok).sort((a, b) => a.info.order - b.info.order)[0] || null;
    }
    if (!chosen) {
      throw this._failure(spec, attempts, outcomes.map((o) => o.error).filter(Boolean));
    }
    const selection = {
      strategy: 'auto',
      engine: chosen.info.id,
      name: chosen.info.name,
      score: chosenScore,
      candidates: outcomes.map((o) => ({
        engine: o.info.id,
        name: o.info.name,
        ok: o.ok,
        score: o.score ? o.score.value : null,
        hard: o.score ? o.score.hard : null,
        soft: o.score ? o.score.soft : null,
        balanced: o.score ? o.score.balanced : null,
        state: o.ok ? o.result.state : null,
        ms: o.ms,
        ...(o.ok ? {} : { kind: o.error.kind, error: o.error.message }),
      })),
    };
    return {
      result: { ...chosen.result, engine: chosen.info.id, selection },
      served_by: chosen.info.id,
      via: chosen.info.worker.via || undefined,
      attempts,
    };
  }

  // ---- lifecycle ----------------------------------------------------------------

  /** Stop what is running and forget what the engines had open. Their next call restarts them. */
  cancel() {
    this.epoch += 1;
    for (const engine of this.engines.values()) engine.cancel('Operación cancelada');
    if (this.session) this.session.forgetEngines();
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    this.epoch += 1;
    await Promise.all([...this.engines.values()].map((engine) => engine.stop().catch(() => {})));
    if (this.session) await this.session.dispose();
    this.session = null;
    try {
      fs.rmSync(this.stateRoot, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch {
      /* temp folder */
    }
  }
}

module.exports = { Harness, defaultRoots };
