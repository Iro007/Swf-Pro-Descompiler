'use strict';
/**
 * Everything the renderer sends over IPC is checked here, in the main process,
 * before it reaches the harness: the renderer is the least trusted part of the
 * application (it renders documents the user opened). Pure functions, no Electron.
 */
const { HarnessError, KINDS } = require('../harness/errors');

const OP_NAME = /^[A-Za-z0-9_]{1,64}$/;
const MAX_ARGS_BYTES = 8 * 1024 * 1024;
const MAX_DEPTH = 32;

const isPlainObject = (value) => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
};

/** JSON-only data: no functions, symbols, class instances, cycles or absurd nesting. */
function assertPlainData(value, depth = 0, seen = new Set()) {
  if (depth > MAX_DEPTH) throw new HarnessError(KINDS.BAD_REQUEST, 'Argumentos demasiado anidados');
  if (value === null) return;
  switch (typeof value) {
    case 'string':
    case 'boolean':
      return;
    case 'number':
      if (!Number.isFinite(value)) throw new HarnessError(KINDS.BAD_REQUEST, 'Los números deben ser finitos');
      return;
    case 'object':
      break;
    default:
      throw new HarnessError(KINDS.BAD_REQUEST, `Tipo no permitido en los argumentos: ${typeof value}`);
  }
  if (seen.has(value)) throw new HarnessError(KINDS.BAD_REQUEST, 'Argumentos circulares');
  seen.add(value);
  if (Array.isArray(value)) {
    for (const item of value) assertPlainData(item, depth + 1, seen);
  } else if (isPlainObject(value)) {
    for (const key of Object.keys(value)) {
      if (key === '__proto__') throw new HarnessError(KINDS.BAD_REQUEST, 'Clave no permitida');
      assertPlainData(value[key], depth + 1, seen);
    }
  } else {
    throw new HarnessError(KINDS.BAD_REQUEST, 'Solo se admiten datos JSON simples');
  }
  seen.delete(value);
}

/**
 * Validate `{op, args}` against the operations the registry knows.
 * @param {unknown} payload what `ipcRenderer.invoke('flash:call', payload)` carried
 * @param {Set<string>} allowedOps registry.allowedOps()
 * @returns {{op: string, args: object}}
 */
function validateCall(payload, allowedOps) {
  if (!isPlainObject(payload)) throw new HarnessError(KINDS.BAD_REQUEST, 'Solicitud inválida');
  const { op } = payload;
  if (typeof op !== 'string' || !OP_NAME.test(op)) throw new HarnessError(KINDS.BAD_REQUEST, 'Nombre de operación inválido');
  if (!allowedOps.has(op)) throw new HarnessError(KINDS.BAD_REQUEST, 'Operación no autorizada en la interfaz');
  const args = payload.args === undefined ? {} : payload.args;
  if (!isPlainObject(args)) throw new HarnessError(KINDS.BAD_REQUEST, 'Los argumentos deben ser un objeto JSON');
  assertPlainData(args);
  if (Buffer.byteLength(JSON.stringify(args)) > MAX_ARGS_BYTES) throw new HarnessError(KINDS.LIMIT, 'Argumentos mayores a 8 MiB');
  return { op, args };
}

/** A dialog kind must be one the application defines. */
function validateKind(kind, allowed) {
  if (typeof kind !== 'string' || !allowed.includes(kind)) {
    throw new HarnessError(KINDS.BAD_REQUEST, 'Tipo de selector desconocido');
  }
  return kind;
}

/** Options of the save dialog: only a file name and an extension list are taken from the renderer. */
function validateSaveOptions(options) {
  if (options === undefined || options === null) return { name: 'archivo', extensions: [] };
  if (!isPlainObject(options)) throw new HarnessError(KINDS.BAD_REQUEST, 'Opciones inválidas');
  const name = options.name === undefined ? 'archivo' : options.name;
  if (typeof name !== 'string' || name === '' || name.length > 200 || /[\\/:*?"<>|\0]/.test(name)) {
    throw new HarnessError(KINDS.BAD_REQUEST, 'Nombre de archivo inválido');
  }
  const extensions = options.extensions === undefined ? [] : options.extensions;
  if (!Array.isArray(extensions) || extensions.length > 8 || !extensions.every((e) => typeof e === 'string' && /^[A-Za-z0-9]{1,8}$/.test(e))) {
    throw new HarnessError(KINDS.BAD_REQUEST, 'Extensiones inválidas');
  }
  const kind = options.kind === undefined ? undefined : options.kind;
  if (kind !== undefined && typeof kind !== 'string') throw new HarnessError(KINDS.BAD_REQUEST, 'Tipo inválido');
  return { name, extensions, kind };
}

const MAX_EVIDENCE_BYTES = 256 * 1024;

/** What a verification run reports about the interface: a plain JSON object, and a small one. */
function validateEvidence(payload) {
  if (!isPlainObject(payload) || !isPlainObject(payload.evidence)) throw new HarnessError(KINDS.BAD_REQUEST, 'Evidencia inválida');
  assertPlainData(payload.evidence);
  if (Buffer.byteLength(JSON.stringify(payload.evidence)) > MAX_EVIDENCE_BYTES) throw new HarnessError(KINDS.LIMIT, 'Evidencia mayor a 256 KiB');
  return payload.evidence;
}

module.exports = { validateCall, validateKind, validateSaveOptions, validateEvidence, assertPlainData, isPlainObject, OP_NAME, MAX_ARGS_BYTES, MAX_EVIDENCE_BYTES };
