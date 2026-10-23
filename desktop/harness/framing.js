'use strict';
/**
 * Wire formats of the engine workers. The harness speaks one shape internally
 * (`{id, op, args}` in, `{id, ok, result | error:{message,kind}}` out); a codec
 * translates it to what a given worker really reads and writes:
 *
 *   v2      {id, op, args}                       {id, ok, result | error:{message,kind}}
 *   legacy  {request_id, operation, args}        {request_id, result | error:"text"}
 *           (rustswf before v2, JsSWF, GoSWF, AsmSWF; ids are strings because GoSWF
 *            declares request_id as a string)
 *   suite   {protocol_version:1, id, op, args}   {id, ok, protocol_version:1, result | error:"text"}
 *           (the PySWF universal worker)
 */
const { KINDS } = require('./errors');

function normalizeError(value, fallbackKind = KINDS.FAILED) {
  if (value && typeof value === 'object') {
    return {
      message: String(value.message ?? JSON.stringify(value)),
      kind: typeof value.kind === 'string' ? value.kind : fallbackKind,
    };
  }
  return { message: String(value), kind: fallbackKind };
}

const v2 = {
  name: 'v2',
  encode(id, op, args) {
    return { protocol_version: 2, id, op, args };
  },
  decode(message) {
    if (!message || typeof message !== 'object' || !('id' in message) || typeof message.ok !== 'boolean') return null;
    if (message.protocol_version !== undefined && message.protocol_version !== 2) return null;
    if (message.ok) return { id: String(message.id), ok: true, result: message.result };
    return { id: String(message.id), ok: false, error: normalizeError(message.error) };
  },
};

const legacy = {
  name: 'legacy',
  encode(id, op, args) {
    return { request_id: String(id), operation: op, args };
  },
  decode(message) {
    if (!message || typeof message !== 'object' || !('request_id' in message)) return null;
    if (message.request_id === null) {
      // Workers answer an unreadable request without its id; surface it as a protocol failure.
      return { id: null, ok: false, error: normalizeError(message.error ?? 'Respuesta sin identificador', KINDS.PROTOCOL) };
    }
    if ('error' in message && message.error !== null && message.error !== undefined) {
      return { id: String(message.request_id), ok: false, error: normalizeError(message.error) };
    }
    return { id: String(message.request_id), ok: true, result: message.result };
  },
};

const suite = {
  name: 'suite',
  encode(id, op, args) {
    return { protocol_version: 1, id, op, args };
  },
  decode(message) {
    if (!message || typeof message !== 'object' || !('id' in message) || typeof message.ok !== 'boolean') return null;
    if (message.protocol_version !== undefined && message.protocol_version !== 1) return null;
    if (message.ok) {
      return 'result' in message
        ? { id: String(message.id), ok: true, result: message.result }
        : { id: String(message.id), ok: false, error: normalizeError('Respuesta exitosa sin resultado', KINDS.PROTOCOL) };
    }
    return { id: message.id === null ? null : String(message.id), ok: false, error: normalizeError(message.error) };
  },
};

const CODECS = Object.freeze({ v2, legacy, suite });

function codecFor(name) {
  const codec = CODECS[name];
  if (!codec) throw new Error(`Framing desconocido: ${name}`);
  return codec;
}

module.exports = { CODECS, codecFor, normalizeError };
