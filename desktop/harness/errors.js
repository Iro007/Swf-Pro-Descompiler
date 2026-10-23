'use strict';

/**
 * Error kinds. `worker` kinds come from the engines (engines/PROTOCOL.md);
 * `harness` kinds are added here. Only `NO_FALLBACK` kinds stop the fallback
 * chain: another engine would reject the same request for the same reason.
 */
const KINDS = Object.freeze({
  BAD_REQUEST: 'bad_request',
  NOT_OPEN: 'not_open',
  NOT_FOUND: 'not_found',
  UNSUPPORTED: 'unsupported',
  FAILED: 'failed',
  LIMIT: 'limit',
  INTERNAL: 'internal',
  TIMEOUT: 'timeout',
  CRASHED: 'crashed',
  UNAVAILABLE: 'unavailable',
  CANCELLED: 'cancelled',
  STALE: 'stale',
  PROTOCOL: 'protocol',
  NO_ENGINE: 'no_engine',
});

const NO_FALLBACK = new Set([KINDS.BAD_REQUEST, KINDS.CANCELLED, KINDS.STALE]);

class HarnessError extends Error {
  /**
   * @param {string} kind one of KINDS
   * @param {string} message human readable (Spanish for end users, as the engines do)
   * @param {object} [extra] `engine`, `attempts`, `stderr`, `cause`
   */
  constructor(kind, message, extra = {}) {
    super(message);
    this.name = 'HarnessError';
    this.kind = kind;
    if (extra.engine) this.engine = extra.engine;
    if (extra.attempts) this.attempts = extra.attempts;
    if (extra.stderr) this.stderr = extra.stderr;
    if (extra.cause) this.cause = extra.cause;
  }

  /** Plain object that survives IPC. */
  toJSON() {
    const out = { message: this.message, kind: this.kind };
    if (this.engine) out.engine = this.engine;
    if (this.attempts) out.attempts = this.attempts;
    if (this.stderr) out.stderr = this.stderr;
    return out;
  }
}

function isHarnessError(error) {
  return error instanceof HarnessError;
}

/** Whether another engine should be tried after this failure. */
function allowsFallback(error) {
  return !(error && NO_FALLBACK.has(error.kind));
}

/** Wrap anything thrown into a HarnessError, keeping a kind if it has one. */
function asHarnessError(error, fallbackKind = KINDS.INTERNAL) {
  if (error instanceof HarnessError) return error;
  const message = error && error.message ? error.message : String(error);
  return new HarnessError(error && error.kind ? error.kind : fallbackKind, message, { cause: error });
}

module.exports = { KINDS, NO_FALLBACK, HarnessError, isHarnessError, allowsFallback, asHarnessError };
