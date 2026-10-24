'use strict';
/**
 * Quality score of one decompiled script. A score only detects what the engine
 * itself admits (error markers) and unbalanced brackets: it does not prove the
 * source recompiles or behaves the same. Rules are the recovered
 * shared/scoring-rules.json (base 100, hard -30, soft -5, unbalanced -40,
 * at most 10 hits per marker); the golden cases are test/scoring-vectors.json.
 */
const RULES = require('./scoring-rules.json');

const { base_score: BASE_SCORE, hard_penalty: HARD_PENALTY, soft_penalty: SOFT_PENALTY,
  unbalanced_penalty: UNBALANCED_PENALTY, max_marker_hits: MAX_MARKER_HITS } = RULES;
const HARD_MARKERS = Object.freeze([...RULES.hard_markers]);
const SOFT_MARKERS = Object.freeze([...RULES.soft_markers]);

/** Text without string literals and comments, so brackets inside them do not count. */
function stripStringsAndComments(text) {
  const chars = Array.from(text);
  const out = [];
  const n = chars.length;
  let i = 0;
  while (i < n) {
    const c = chars[i];
    const next = i + 1 < n ? chars[i + 1] : '';
    if (c === '/' && next === '/') {
      while (i < n && chars[i] !== '\n') i += 1;
    } else if (c === '/' && next === '*') {
      i += 2;
      while (i < n && !(chars[i] === '*' && chars[i + 1] === '/')) i += 1;
      i = Math.min(i + 2, n);
    } else if (c === '"' || c === "'") {
      i += 1;
      while (i < n && chars[i] !== c) i += chars[i] === '\\' ? 2 : 1;
      i += 1;
    } else {
      out.push(c);
      i += 1;
    }
  }
  return out;
}

const OPENING = { ')': '(', ']': '[', '}': '{' };

function balanced(text) {
  const stack = [];
  for (const c of stripStringsAndComments(text)) {
    if (c === '(' || c === '[' || c === '{') {
      stack.push(c);
    } else if (OPENING[c] !== undefined) {
      if (stack.pop() !== OPENING[c]) return false;
    }
  }
  return stack.length === 0;
}

/** Non-overlapping occurrences, like Python's str.count and Rust's matches().count(). */
function countOccurrences(text, marker) {
  let count = 0;
  let from = 0;
  for (;;) {
    const at = text.indexOf(marker, from);
    if (at < 0) return count;
    count += 1;
    from = at + marker.length;
  }
}

/**
 * @returns {null | {value:number, hard:number, soft:number, balanced:boolean, reasons:string[]}}
 *   null means the engine produced nothing (missing or blank text).
 */
function score(text) {
  if (typeof text !== 'string' || text.trim() === '') return null;
  const result = { value: BASE_SCORE, hard: 0, soft: 0, balanced: true, reasons: [] };
  for (const marker of HARD_MARKERS) {
    const hits = Math.min(countOccurrences(text, marker), MAX_MARKER_HITS);
    if (hits) {
      result.hard += hits;
      result.reasons.push(`'${marker}' x${hits}`);
    }
  }
  for (const marker of SOFT_MARKERS) {
    const hits = Math.min(countOccurrences(text, marker), MAX_MARKER_HITS);
    if (hits) {
      result.soft += hits;
      result.reasons.push(`'${marker}' x${hits}`);
    }
  }
  result.balanced = balanced(text);
  if (!result.balanced) result.reasons.push('unbalanced brackets');
  result.value -= result.hard * HARD_PENALTY + result.soft * SOFT_PENALTY;
  if (!result.balanced) result.value -= UNBALANCED_PENALTY;
  return result;
}

/** A clean result: full score, nothing the engine flagged. */
function isClean(result) {
  return result !== null && result.value === BASE_SCORE;
}

/**
 * Choose among candidates `{engine, text, order, editable?}`: the highest score wins; on a
 * tie an editable result beats a read-only one (so the person can edit what they see),
 * then the lower manifest `order`. Candidates without text never win.
 * @returns {null | {engine, score, candidate}}
 */
function pickBest(candidates) {
  let best = null;
  for (const candidate of candidates) {
    const result = score(candidate.text);
    if (result === null) continue;
    const editable = candidate.editable === true;
    const bestEditable = best !== null && best.candidate.editable === true;
    const better =
      best === null ||
      result.value > best.score.value ||
      (result.value === best.score.value &&
        (editable !== bestEditable ? editable : (candidate.order ?? 0) < (best.candidate.order ?? 0)));
    if (better) best = { engine: candidate.engine, score: result, candidate };
  }
  return best;
}

module.exports = {
  RULES, BASE_SCORE, HARD_MARKERS, SOFT_MARKERS, score, balanced, isClean, pickBest, stripStringsAndComments,
};
