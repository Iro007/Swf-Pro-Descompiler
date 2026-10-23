'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { Protocol } = require('./protocol');
const { CODECS } = require('./framing');

const ENGINE_ALIASES = Object.freeze({
  python: 'native',
  pyswf: 'native',
  rustswf: 'rust',
  jsswf: 'js',
  goswf: 'go',
  asmswf: 'asm',
});

const hasSeparator = (text) => /[\\/]/.test(text);
const asList = (value) => (value === undefined || value === null ? [] : value);
/** `command` and `dev_command` hold one command or a list of candidate commands. */
const candidates = (value) => {
  const list = asList(value);
  return Array.isArray(list[0]) ? list : list.length > 0 ? [list] : [];
};

/** PATH lookup for a bare command name (python, node, ...). */
function which(name, env, platform) {
  const extensions = platform === 'win32' ? (env.PATHEXT || '.EXE;.CMD;.BAT').split(';') : [''];
  for (const folder of (env.PATH || env.Path || '').split(path.delimiter)) {
    if (!folder) continue;
    for (const extension of extensions) {
      const file = path.join(folder, name.toLowerCase().endsWith(extension.toLowerCase()) ? name : name + extension);
      if (fs.existsSync(file)) return file;
    }
  }
  return null;
}

function normalizeOpSpec(raw) {
  const spec = raw || {};
  return { timeout: spec.timeout || 'default', mutates: Boolean(spec.mutates), document: spec.document !== false };
}

class EngineInfo {
  constructor(manifest, manifestPath) {
    this.manifest = manifest;
    this.manifestPath = manifestPath;
    this.id = manifest.id;
    this.name = manifest.name || manifest.id;
    this.order = Number.isFinite(manifest.order) ? manifest.order : 1000;
    this.category = manifest.category || '';
    this.color = manifest.color || '';
    this.delivery = manifest.delivery || '';
    this.languages = manifest.languages || [];
    this.license = manifest.license || '';
    this.website = manifest.website || '';
    this.worker = null;
    this.launch = null;
    this.available = null;
    this.reason = null;
    this.levels = null;
    this.problems = [];
  }

  implements(op) {
    return Boolean(this.worker && this.worker.ops.has(op));
  }

  ownsNative(op) {
    return Boolean(this.worker && this.worker.nativeOps.has(op));
  }
}

class Registry {
  /**
   * @param {object} options
   * @param {string[]} options.roots directories searched for `engines/*\/engine.json` and for
   *   the relative paths of worker commands, in order (repository root in development,
   *   `resources` in the packaged app, then FLASH_ENGINE_ROOTS)
   * @param {object} [options.env]
   * @param {string} [options.platform]
   * @param {string} [options.nodePath] what `{node}` expands to
   * @param {boolean} [options.electron] run `{node}` with ELECTRON_RUN_AS_NODE=1
   * @param {Protocol} [options.protocol]
   */
  constructor(options) {
    this.roots = options.roots;
    this.env = options.env || process.env;
    this.platform = options.platform || process.platform;
    this.nodePath = options.nodePath || process.execPath;
    this.electron = options.electron ?? Boolean(process.versions.electron);
    this.protocol = options.protocol || Protocol.load(this.roots);
    this.engines = new Map();
    this.problems = [];
    this.workspace = this.env.FLASH_SUITE_WORKSPACE || this.roots[0];
    this._loadManifests();
    this._link();
  }

  static load(options) {
    return new Registry(options);
  }

  _loadManifests() {
    const seen = new Set();
    for (const root of this.roots) {
      const folder = path.join(root, 'engines');
      let entries = [];
      try {
        entries = fs.readdirSync(folder, { withFileTypes: true });
      } catch {
        continue;
      }
      for (const entry of entries.filter((e) => e.isDirectory()).sort((a, b) => a.name.localeCompare(b.name))) {
        const file = path.join(folder, entry.name, 'engine.json');
        if (!fs.existsSync(file) || seen.has(file)) continue;
        seen.add(file);
        let manifest;
        try {
          manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
        } catch (error) {
          this.problems.push(`${file}: JSON inválido (${error.message})`);
          continue;
        }
        if (typeof manifest.id !== 'string' || !manifest.id) {
          this.problems.push(`${file}: falta id`);
          continue;
        }
        if (this.engines.has(manifest.id)) {
          this.problems.push(`${file}: el id ${manifest.id} ya lo declara ${this.engines.get(manifest.id).manifestPath}`);
          continue;
        }
        const info = new EngineInfo(manifest, file);
        this._readWorker(info, root);
        this.engines.set(info.id, info);
      }
    }
    const ordered = [...this.engines.values()].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
    this.engines = new Map(ordered.map((e) => [e.id, e]));
  }

  _readWorker(info, root) {
    const raw = info.manifest.worker;
    if (!raw) return;
    const problem = (text) => {
      info.problems.push(text);
      this.problems.push(`${info.id}: ${text}`);
    };
    const ops = new Set();
    for (const op of asList(raw.ops)) {
      if (!this.protocol.isCanonical(op)) problem(`worker.ops lista ${op}, que no es una operación de engines/protocol.json`);
      else ops.add(op);
    }
    const nativeOps = new Map();
    for (const [name, spec] of Object.entries(raw.native_ops || {})) {
      if (this.protocol.isCanonical(name)) problem(`native_ops repite la operación canónica ${name}`);
      else nativeOps.set(name, normalizeOpSpec(spec));
    }
    const framing = raw.framing || 'v2';
    if (!CODECS[framing]) problem(`framing desconocido: ${framing}`);
    info.worker = {
      framing: CODECS[framing] ? framing : 'v2',
      ops,
      nativeOps,
      derivedOps: new Set(asList(raw.derived_ops)),
      via: raw.via || null,
      viaArgs: raw.args || {},
      memoryMiB: raw.memory_mib || null,
      authority: Boolean(raw.authority),
      supervisor: Boolean(raw.supervisor),
      raw,
      root,
    };
    info.levels = this.protocol.levelsFor(ops);
  }

  _link() {
    for (const info of this.engines.values()) {
      if (!info.worker) {
        info.available = null;
        info.reason = 'Herramienta externa: la localiza PySWF (operación engines)';
        info.levels = this.protocol.levelsFor([]);
        continue;
      }
      if (info.worker.via) {
        const via = this.engines.get(info.worker.via);
        if (!via || !via.worker) {
          info.available = false;
          info.reason = `Depende de ${info.worker.via}, que no tiene worker`;
          this.problems.push(`${info.id}: via ${info.worker.via} no existe`);
        }
        continue;
      }
      const resolved = this._resolveLaunch(info);
      if (resolved.ok) {
        info.launch = resolved.launch;
        info.available = true;
      } else {
        info.available = false;
        info.reason = resolved.reason;
      }
    }
    for (const info of this.engines.values()) {
      if (info.worker && info.worker.via) {
        const via = this.engines.get(info.worker.via);
        if (via) {
          info.available = via.available;
          info.reason = via.available ? null : `Necesita ${via.name}: ${via.reason}`;
        }
      }
    }
  }

  /** First command candidate whose files exist under one root. */
  _resolveLaunch(info) {
    const worker = info.worker;
    const tried = [];
    const groups = [
      ['command', candidates(worker.raw.command)],
      ['dev_command', candidates(worker.raw.dev_command)],
    ];
    for (const [source, list] of groups) {
      for (const command of list) {
        const attempt = this._tryCommand(command, worker);
        if (attempt.ok) return { ok: true, launch: { ...attempt.launch, source } };
        tried.push(attempt.reason);
      }
    }
    return { ok: false, reason: tried.length ? `No se encontró ${tried.join(' ni ')}` : 'El worker no declara command' };
  }

  _tryCommand(command, worker) {
    const relative = command.map((part, index) => ({ part, index })).filter(({ part }) => hasSeparator(part) && !path.isAbsolute(part) && !part.startsWith('-') && part !== '{node}');
    const absolute = command.filter((part) => path.isAbsolute(part));
    for (const file of absolute) {
      if (!fs.existsSync(file)) return { ok: false, reason: file };
    }
    const roots = relative.length === 0 ? [this.roots[0]] : this.roots;
    for (const root of roots) {
      if (!relative.every(({ part }) => fs.existsSync(path.join(root, part)))) continue;
      const resolved = command.map((part) => {
        if (part === '{node}') return this.nodePath;
        if (hasSeparator(part) && !path.isAbsolute(part) && !part.startsWith('-')) return path.join(root, part);
        return part;
      });
      if (relative.length === 0 && command[0] !== '{node}' && !path.isAbsolute(command[0])) {
        const found = which(command[0], this.env, this.platform);
        if (!found) return { ok: false, reason: command[0] };
        resolved[0] = found;
      }
      const env = {};
      if (command[0] === '{node}' && this.electron) env.ELECTRON_RUN_AS_NODE = '1';
      for (const [key, value] of Object.entries(worker.raw.env || {})) {
        env[key] = String(value).replaceAll('{workspace}', this.workspace).replaceAll('{root}', root);
      }
      const home = worker.raw.cwd ? path.join(root, worker.raw.cwd) : null;
      const cwd = home && fs.existsSync(home) ? home : path.isAbsolute(resolved[0]) && command[0] !== '{node}' ? path.dirname(resolved[0]) : root;
      return { ok: true, launch: { command: resolved[0], args: resolved.slice(1), cwd, env, root } };
    }
    const first = relative.length ? relative[0].part : command[0];
    return { ok: false, reason: first };
  }

  engine(id) {
    return this.engines.get(this.normalizeId(id));
  }

  normalizeId(id) {
    return typeof id === 'string' ? ENGINE_ALIASES[id.toLowerCase()] || id : id;
  }

  /** Engine that keeps the editable document (PySWF). */
  get authority() {
    for (const info of this.engines.values()) if (info.worker && info.worker.authority) return info;
    return this.engines.get(this.protocol.authority) || null;
  }

  /** The Rust executable that can wrap other workers in a Job Object. */
  get supervisorPath() {
    for (const info of this.engines.values()) {
      if (info.worker && info.worker.supervisor && info.launch) return info.launch.command;
    }
    return null;
  }

  /** Engines with a worker (their own or a proxy), in manifest order. */
  workers() {
    return [...this.engines.values()].filter((e) => e.worker);
  }

  /**
   * Engines that implement a canonical operation: the fallback chain first (in chain
   * order, except that the session owner goes first for the operations protocol.json lists
   * in `prefer_authority`), then the others by manifest order. `extras` are only used
   * when asked for.
   */
  providers(op) {
    const chain = [];
    let order = this.protocol.fallbackChain;
    if (this.protocol.preferAuthority.has(op)) {
      const owner = this.authority ? this.authority.id : this.protocol.authority;
      order = [owner, ...order.filter((id) => id !== owner)];
    }
    for (const id of order) {
      const info = this.engines.get(id);
      if (info && info.implements(op)) chain.push(info);
    }
    const extras = this.workers().filter((e) => e.implements(op) && !chain.includes(e));
    return { chain, extras };
  }

  /**
   * Everything the harness knows about an operation name, or undefined:
   * `{name, canonical, level, timeout, mutates, document, owners}`.
   */
  opSpec(name) {
    if (this.protocol.isCanonical(name)) {
      return {
        name,
        canonical: true,
        level: this.protocol.levelOf(name),
        ...this.protocol.canonicalSpec(name),
        owners: [...this.providers(name).chain, ...this.providers(name).extras].map((e) => e.id),
      };
    }
    const owners = this.workers().filter((e) => e.ownsNative(name));
    if (owners.length === 0) return undefined;
    return { name, canonical: false, level: null, ...owners[0].worker.nativeOps.get(name), owners: owners.map((e) => e.id) };
  }

  /** Every operation name the renderer may ask for. */
  allowedOps() {
    const names = new Set(this.protocol.canonicalOps);
    for (const info of this.workers()) for (const name of info.worker.nativeOps.keys()) names.add(name);
    return names;
  }

  /** What `window.flash.engines()` returns. */
  summary() {
    return [...this.engines.values()].map((info) => {
      const worker = info.worker;
      const levels = info.levels
        ? Object.fromEntries(
            Object.entries(info.levels).map(([level, v]) => [level, { name: v.name, have: v.have.length, total: v.total, missing: v.missing, complete: v.complete }]),
          )
        : null;
      return {
        id: info.id,
        name: info.name,
        order: info.order,
        category: info.category,
        color: info.color,
        delivery: info.delivery,
        languages: info.languages,
        kind: !worker ? 'tool' : worker.via ? 'proxy' : 'worker',
        available: info.available,
        reason: info.reason,
        framing: worker ? worker.framing : null,
        via: worker ? worker.via : null,
        authority: worker ? worker.authority : false,
        ops: worker ? [...worker.ops] : [],
        derived_ops: worker ? [...worker.derivedOps] : [],
        native_ops: worker ? [...worker.nativeOps.keys()] : [],
        levels,
        levels_line: info.levels ? Protocol.levelsLine(info.levels) : '',
        command: info.launch ? info.launch.command : null,
        launch_source: info.launch ? info.launch.source : null,
        problems: info.problems,
      };
    });
  }
}

module.exports = { Registry, EngineInfo, ENGINE_ALIASES, candidates, which };
