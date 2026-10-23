'use strict';
const fs = require('node:fs');
const path = require('node:path');

/** Integer from the environment, clamped; anything unusable gives the default. */
function boundedEnv(env, name, fallback, min, max) {
  const raw = env && env[name];
  const value = raw === undefined || raw === '' ? NaN : Number.parseInt(raw, 10);
  const chosen = Number.isFinite(value) ? value : fallback;
  return Math.min(max, Math.max(min, chosen));
}

function validate(raw, source) {
  const fail = (why) => {
    throw new Error(`engines/protocol.json inválido (${source}): ${why}`);
  };
  if (!raw || raw.protocol !== 'flash-engine-jsonl') fail('protocol debe ser flash-engine-jsonl');
  if (raw.version !== 2) fail('version debe ser 2');
  if (!raw.levels || typeof raw.levels !== 'object') fail('faltan los niveles');
  if (!raw.ops || typeof raw.ops !== 'object') fail('falta la tabla ops');
  const seen = new Set();
  for (const [name, level] of Object.entries(raw.levels)) {
    if (!Array.isArray(level.ops) || !level.name) fail(`el nivel ${name} necesita name y ops`);
    for (const op of level.ops) {
      if (seen.has(op)) fail(`la operación ${op} está en dos niveles`);
      seen.add(op);
      if (!raw.ops[op]) fail(`la operación ${op} del nivel ${name} no está en ops`);
    }
  }
  for (const op of Object.keys(raw.ops)) {
    if (!seen.has(op)) fail(`la operación ${op} no pertenece a ningún nivel`);
  }
  if (!Array.isArray(raw.fallback_chain) || raw.fallback_chain.length === 0) fail('falta fallback_chain');
  if (typeof raw.authority !== 'string') fail('falta authority');
  for (const op of raw.prefer_authority || []) {
    if (!raw.ops[op]) fail(`prefer_authority nombra ${op}, que no es una operación`);
  }
  return raw;
}

class Protocol {
  constructor(raw, source = '<memory>') {
    this.raw = validate(raw, source);
    this.source = source;
    this.levels = raw.levels;
    this.fallbackChain = [...raw.fallback_chain];
    this.authority = raw.authority;
    /** Operations the session owner answers before the fallback chain does (see PROTOCOL.md, section 7). */
    this.preferAuthority = new Set(raw.prefer_authority || []);
    this.opLevel = new Map();
    for (const [level, { ops }] of Object.entries(raw.levels)) {
      for (const op of ops) this.opLevel.set(op, level);
    }
    this.canonicalOps = [...this.opLevel.keys()];
    this.tagFolders = raw.tag_folders;
    this.folderOfCode = new Map();
    for (const [folder, codes] of Object.entries(raw.tag_folders.by_code)) {
      for (const code of codes) this.folderOfCode.set(code, folder);
    }
    this.frameControl = new Set(raw.tag_folders.frame_control);
    this.noFallbackKinds = new Set(raw.error_kinds.no_fallback);
  }

  static load(roots) {
    const tried = [];
    for (const root of roots) {
      const file = path.join(root, 'engines', 'protocol.json');
      tried.push(file);
      if (fs.existsSync(file)) return new Protocol(JSON.parse(fs.readFileSync(file, 'utf8')), file);
    }
    throw new Error(`No se encontró engines/protocol.json en: ${tried.join(', ')}`);
  }

  isCanonical(op) {
    return this.opLevel.has(op);
  }

  levelOf(op) {
    return this.opLevel.get(op);
  }

  /** `{timeout, mutates, document}` of a canonical operation. */
  canonicalSpec(op) {
    const spec = this.raw.ops[op];
    if (!spec) return undefined;
    return { timeout: spec.timeout || 'default', mutates: Boolean(spec.mutates), document: spec.document !== false };
  }

  /**
   * Which levels an engine completes. A level is complete when every one of its
   * operations is present; `missing` is the roadmap of that engine.
   */
  levelsFor(ops) {
    const have = new Set(ops);
    const result = {};
    for (const [level, { name, ops: list }] of Object.entries(this.levels)) {
      const present = list.filter((op) => have.has(op));
      result[level] = {
        name,
        total: list.length,
        have: present,
        missing: list.filter((op) => !have.has(op)),
        complete: present.length === list.length,
      };
    }
    return result;
  }

  /** One line per engine: `L1 6/6  L2 6/7  L3 3/3  L4 0/6  L5 0/6`. */
  static levelsLine(levels) {
    return Object.entries(levels)
      .map(([level, { have, total }]) => `${level} ${have.length}/${total}`)
      .join('  ');
  }

  /** Per-operation deadline, in milliseconds. Same classes and variables as the Tauri host. */
  timeoutMs(timeoutClass, env = process.env) {
    const entry = this.raw.timeouts_seconds[timeoutClass] || this.raw.timeouts_seconds.default;
    return boundedEnv(env, entry.env, entry.seconds, 1, 600) * 1000;
  }

  /** Memory cap in MiB for a worker; FLASH_SUITE_MEMORY_MIB overrides, as in suite.rs. */
  memoryMiB(workerMemory, env = process.env) {
    const memory = this.raw.memory_mib;
    return boundedEnv(env, memory.env, workerMemory || memory.default, memory.min, memory.max);
  }
}

module.exports = { Protocol, boundedEnv };
