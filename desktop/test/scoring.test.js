'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { score, balanced, isClean, pickBest, RULES, BASE_SCORE } = require('../harness/scoring');
const vectors = require('./scoring-vectors.json');

test('the golden vectors of the recovered scoring rules all pass', () => {
  assert.ok(vectors.cases.length >= 10);
  for (const item of vectors.cases) {
    const result = score(item.text);
    assert.equal(result === null ? null : result.value, item.score, `score for ${JSON.stringify(item.text)}`);
    if (typeof item.text === 'string' && 'balanced' in item) {
      assert.equal(balanced(item.text), item.balanced, `balanced for ${JSON.stringify(item.text)}`);
    }
  }
});

test('rules: base 100, hard markers cost 30, soft 5, unbalanced brackets 40, ten hits at most per marker', () => {
  assert.equal(BASE_SCORE, 100);
  assert.equal(RULES.hard_penalty, 30);
  assert.equal(RULES.soft_penalty, 5);
  assert.equal(RULES.unbalanced_penalty, 40);
  assert.equal(RULES.max_marker_hits, 10);
  assert.equal(score('x = 1; // Unsupported thing').value, 95);
  assert.equal(score('pcode_only').value, 70);
  assert.equal(score('pcode_only pcode_only').value, 40);
  // Eleven markers count as ten.
  assert.equal(score('§§'.repeat(11)).value, 100 - 10 * 30);
  // Hard + soft + unbalanced stack up, and the result may go negative.
  const result = score('{ // Unsupported\n§§');
  assert.equal(result.value, 100 - 30 - 5 - 40);
  assert.equal(result.hard, 1);
  assert.equal(result.soft, 1);
  assert.equal(result.balanced, false);
  assert.deepEqual(result.reasons, ["'§§' x1", "'// Unsupported' x1", 'unbalanced brackets']);
});

test('no output (missing, blank or non-string) is null, never zero', () => {
  for (const empty of [null, undefined, '', '   \n\t', 42, {}]) assert.equal(score(empty), null);
});

test('brackets inside strings and comments do not count; escapes and unterminated literals are handled', () => {
  assert.equal(balanced('trace("}"); // {\n/* ( */ var x = [1];'), true);
  assert.equal(balanced("var s = 'it\\'s ]';"), true);
  assert.equal(balanced('var s = "never closed ('), true);
  assert.equal(balanced('/* unterminated ( comment'), true);
  assert.equal(balanced('if (a) { b(;'), false);
  assert.equal(balanced('a)'), false);
  assert.equal(balanced('(]'), false);
  assert.equal(balanced(''), true);
});

test('isClean is a full score and nothing else', () => {
  assert.equal(isClean(score('function f() { return 1; }')), true);
  assert.equal(isClean(score('function f() { // Unsupported\n}')), false);
  assert.equal(isClean(null), false);
});

test('pickBest takes the highest score and breaks ties by manifest order', () => {
  const clean = 'class A { function f() { return 1; } }';
  const broken = 'class A { §§push(1); }';
  const winner = pickBest([
    { engine: 'js', text: clean, order: 2 },
    { engine: 'rust', text: broken, order: 0 },
    { engine: 'native', text: clean, order: 1 },
    { engine: 'go', text: null, order: 3 },
  ]);
  assert.equal(winner.engine, 'native');
  assert.equal(winner.score.value, 100);
  assert.equal(pickBest([{ engine: 'a', text: '' }, { engine: 'b', text: null }]), null);
  assert.equal(pickBest([]), null);
  // A broken-but-present source still beats nothing.
  assert.equal(pickBest([{ engine: 'a', text: broken, order: 0 }, { engine: 'b', text: null, order: 1 }]).engine, 'a');
  // Candidates without an order sort as 0 and keep their position on a tie.
  assert.equal(pickBest([{ engine: 'x', text: clean }, { engine: 'y', text: clean }]).engine, 'x');
});

test('pickBest prefers an editable result on a tie, but never over a higher score', () => {
  const clean = 'class A { function f() { return 1; } }';
  const broken = 'class A { §§push(1); }';
  const tie = [
    { engine: 'rust', text: clean, order: 0 },
    { engine: 'native', text: clean, order: 1, editable: true },
    { engine: 'js', text: clean, order: 2 },
  ];
  assert.equal(pickBest(tie).engine, 'native');
  assert.equal(pickBest([...tie].reverse()).engine, 'native');
  assert.equal(pickBest([{ engine: 'rust', text: clean, order: 0 }, { engine: 'native', text: broken, order: 1, editable: true }]).engine, 'rust');
});
