'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { WorkerProcess, MAX_STDERR_BYTES } = require('../harness/worker');
const { HarnessError } = require('../harness/errors');
const { tempDir, removeDir, fakeWorker, isAlive, waitUntil } = require('../testkit');

async function withWorker(config, framing, body, extra = {}) {
  const dir = tempDir();
  const spec = fakeWorker(dir, { framing, ...config });
  const worker = new WorkerProcess({ name: 'fake', command: spec.command, args: spec.args, framing, ...extra });
  await worker.start();
  try {
    await body(worker, dir);
  } finally {
    worker.kill();
    await new Promise((resolve) => setTimeout(resolve, 50));
    removeDir(dir);
  }
}

for (const framing of ['v2', 'legacy', 'suite']) {
  test(`${framing} framing: a request gets its own result, ids never mix`, async () => {
    await withWorker({ ops: { echo: { echo: true } } }, framing, async (worker) => {
      const [a, b, c] = await Promise.all([
        worker.request('echo', { n: 1 }),
        worker.request('echo', { n: 2 }),
        worker.request('echo', { n: 3 }),
      ]);
      assert.deepEqual([a.args.n, b.args.n, c.args.n], [1, 2, 3]);
      assert.deepEqual([a.served, b.served, c.served], [1, 2, 3], 'one request in flight at a time, in order');
      assert.equal(a.pid, worker.pid);
      assert.equal(worker.requestsServed, 3);
    });
  });
}

test('worker errors keep their kind in v2 and default to failed in legacy and suite', async () => {
  const ops = {
    boom: { error: { message: 'kaboom', kind: 'not_found' } },
    plain: { error: 'texto plano' },
  };
  await withWorker({ ops }, 'v2', async (worker) => {
    await assert.rejects(worker.request('boom'), (e) => e instanceof HarnessError && e.kind === 'not_found' && e.message === 'kaboom' && e.engine === 'fake');
    await assert.rejects(worker.request('plain'), (e) => e.kind === 'failed' && e.message === 'texto plano');
    await assert.rejects(worker.request('missing'), (e) => e.kind === 'unsupported');
    assert.ok(worker.alive, 'an operation error never retires the worker');
  });
  for (const framing of ['legacy', 'suite']) {
    await withWorker({ ops }, framing, async (worker) => {
      await assert.rejects(worker.request('boom'), (e) => e.kind === 'failed' && e.message === 'kaboom');
      assert.equal((await worker.request('plain').catch((e) => e)).message, 'texto plano');
    });
  }
});

test('a request that exceeds its deadline kills the worker, fails the queue and leaves it retired', async () => {
  await withWorker({ ops: { slow: { hang: true }, echo: { echo: true } } }, 'v2', async (worker) => {
    const pid = worker.pid;
    const slow = worker.request('slow', {}, { timeoutMs: 200 });
    const queued = worker.request('echo');
    await assert.rejects(slow, (e) => e.kind === 'timeout' && /Límite de 0\.2 segundos/.test(e.message));
    await assert.rejects(queued, (e) => e.kind === 'crashed');
    assert.equal(worker.alive, false);
    await assert.rejects(worker.request('echo'), (e) => e.kind === 'crashed');
    assert.ok(await waitUntil(() => !isAlive(pid)), 'the process is gone');
  });
});

test('killing the worker kills the whole process tree', async () => {
  await withWorker({ ops: { spawn: { spawnChild: true } } }, 'v2', async (worker) => {
    const { child } = await worker.request('spawn');
    assert.ok(isAlive(child), 'the grandchild is running');
    worker.kill();
    assert.ok(await waitUntil(() => !isAlive(child), { timeoutMs: 8000 }), 'the grandchild died with its parent');
  });
});

test('a crash fails the request in flight with the exit code and the tail of stderr', async () => {
  await withWorker({ ops: { die: { crash: 3, stderr: 'fatal: out of memory' } } }, 'v2', async (worker) => {
    const failed = await worker.request('die').catch((e) => e);
    assert.equal(failed.kind, 'crashed');
    assert.match(failed.message, /código 3/);
    assert.match(failed.message, /fatal: out of memory/);
    assert.match(failed.stderr, /out of memory/);
    assert.equal(worker.alive, false);
  });
});

test('stderr is bounded to the last 8 KiB', async () => {
  const noise = `${'x'.repeat(20000)}END`;
  await withWorker({ ops: { chatty: { stderr: noise, result: { ok: 1 } } } }, 'v2', async (worker) => {
    await worker.request('chatty');
    await waitUntil(() => worker.stderrTail().endsWith('END'));
    const tail = worker.stderrTail();
    assert.ok(tail.length <= MAX_STDERR_BYTES, `kept ${tail.length} bytes`);
    assert.ok(tail.endsWith('END'));
  });
});

test('protocol violations retire the worker: garbage, a wrong id, or an answer nobody asked for', async () => {
  for (const [op, spec] of [['garbage', { garbage: true }], ['wrongid', { wrongId: true }]]) {
    await withWorker({ ops: { [op]: spec } }, 'v2', async (worker) => {
      await assert.rejects(worker.request(op), (e) => e.kind === 'protocol');
      assert.equal(worker.alive, false);
    });
  }
  await withWorker({ ops: { echo: { echo: true } } }, 'v2', async (worker) => {
    worker.child.stdout.emit('data', Buffer.from('{"id":1,"ok":true,"result":{}}\n'));
    await assert.rejects(worker.request('echo'), (e) => e.kind === 'crashed', 'the stray answer retired the worker');
  });
});

test('incompatible replies are protocol errors: wrong protocol version or a missing result', async () => {
  await withWorker({ ops: { echo: { echo: true } } }, 'v2', async (worker) => {
    const pending = worker.request('echo');
    worker.child.stdout.emit('data', Buffer.from(`${JSON.stringify({ id: 1, ok: true, protocol_version: 9, result: {} })}\n`));
    await assert.rejects(pending, (e) => e.kind === 'protocol');
  });
});

test('size limits: 8 MiB requests are refused before sending, 16 MiB answers retire the worker', async () => {
  await withWorker({ ops: { echo: { echo: true }, big: { big: 17 * 1024 * 1024 } } }, 'v2', async (worker) => {
    await assert.rejects(worker.request('echo', { blob: 'x'.repeat(9 * 1024 * 1024) }), (e) => e.kind === 'limit');
    assert.ok(worker.alive, 'a refused request never reached the worker');
    assert.equal((await worker.request('echo', { n: 1 })).args.n, 1);
    await assert.rejects(worker.request('big', {}, { timeoutMs: 20000 }), (e) => e.kind === 'protocol' && /16 MiB/.test(e.message));
    assert.equal(worker.alive, false);
  });
});

test('bad requests never reach the process', async () => {
  await withWorker({ ops: { echo: { echo: true } } }, 'v2', async (worker) => {
    for (const op of ['', 'has space', 'x'.repeat(65), 'a-b', undefined, 3]) {
      await assert.rejects(worker.request(op), (e) => e.kind === 'bad_request', `op ${String(op)}`);
    }
    for (const args of [null, [], 'text', 7]) {
      await assert.rejects(worker.request('echo', args), (e) => e.kind === 'bad_request');
    }
    const circular = {};
    circular.self = circular;
    await assert.rejects(worker.request('echo', circular), (e) => e.kind === 'bad_request');
    assert.equal((await worker.request('echo', { fine: true })).args.fine, true);
  });
});

test('an aborted request: queued ones are dropped, the one in flight retires the worker', async () => {
  await withWorker({ ops: { slow: { sleep: 400, result: { done: 1 } }, echo: { echo: true } } }, 'v2', async (worker) => {
    const queuedAbort = new AbortController();
    const first = worker.request('slow');
    const queued = worker.request('echo', {}, { signal: queuedAbort.signal });
    queuedAbort.abort();
    await assert.rejects(queued, (e) => e.kind === 'cancelled');
    assert.deepEqual(await first, { done: 1 });
    assert.ok(worker.alive);
    const inFlight = new AbortController();
    const second = worker.request('slow', {}, { signal: inFlight.signal });
    setTimeout(() => inFlight.abort(), 80);
    await assert.rejects(second, (e) => e.kind === 'cancelled');
    assert.equal(worker.alive, false);
    await assert.rejects(worker.request('echo', {}, { signal: AbortSignal.abort() }), (e) => e.kind === 'cancelled');
  });
});

test('multi-byte text split across pipe chunks arrives intact', async () => {
  const text = 'ñandú 漢字 😀 §§ '.repeat(20000);
  await withWorker({ ops: { text: { result: { text } } } }, 'v2', async (worker) => {
    assert.equal((await worker.request('text')).text, text);
  });
});

test('a command that does not exist is unavailable, not a crash', async () => {
  const worker = new WorkerProcess({ name: 'ghost', command: 'definitely-not-an-engine.exe', args: ['worker'] });
  await assert.rejects(worker.start(), (e) => e.kind === 'unavailable' && /ghost/.test(e.message));
  assert.equal(worker.alive, false);
});

test('stop() ends a worker by closing its stdin: exit code 0, no kill needed', async () => {
  const dir = tempDir();
  const spec = fakeWorker(dir, { ops: { echo: { echo: true } } });
  let killed = false;
  const worker = new WorkerProcess({ name: 'fake', command: spec.command, args: spec.args, killTree: () => { killed = true; } });
  await worker.start();
  await worker.request('echo');
  const exit = new Promise((resolve) => worker.once('exit', resolve));
  await worker.stop();
  assert.equal((await exit).code, 0);
  assert.equal(killed, false);
  await worker.stop();
  removeDir(dir);
});

test('a worker that ignores EOF is killed after the grace period', async () => {
  const dir = tempDir();
  const spec = fakeWorker(dir, { ops: { echo: { echo: true } } });
  const worker = new WorkerProcess({ name: 'stubborn', command: process.execPath, args: ['-e', 'process.stdin.resume();setInterval(()=>{},1000)'] });
  await worker.start();
  const pid = worker.pid;
  const started = Date.now();
  await worker.stop({ graceMs: 300 });
  assert.ok(Date.now() - started < 4000);
  assert.ok(await waitUntil(() => !isAlive(pid)));
  removeDir(dir);
  void spec;
});
