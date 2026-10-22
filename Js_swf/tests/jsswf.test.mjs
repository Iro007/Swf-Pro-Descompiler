import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { parseSwfFile, buildInventory } from "../src/swf.mjs";
import { disassembleUnit, decompileUnit } from "../src/decompile.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");

test("JsSWF parses AVM1 DefineFunction2 registers and constant pool hash", () => {
  const swfPath = path.join(repoRoot, "benchmark_inputs", "as2_definefunc_regs.swf");
  const movie = parseSwfFile(swfPath);
  const units = buildInventory(movie);
  assert.equal(movie.info.signature, "CWS");
  assert.ok(units.length >= 2);
  const fnUnits = units.filter((u) => u.kind === "function");
  assert.ok(fnUnits.length >= 1);
  assert.ok(typeof fnUnits[0].avm1_constants_sha256 === "string");
  assert.equal(fnUnits[0].avm1_constants_sha256.length, 64);
  const dec = decompileUnit(fnUnits[0]);
  assert.equal(dec.state, "structured");
  assert.ok(dec.source.includes("function"));
});

test("JsSWF parses AVM2 DoABC methods and reconstructs structured source", () => {
  const swfPath = path.join(repoRoot, "benchmark_inputs", "as3_new.flex.swf");
  const movie = parseSwfFile(swfPath);
  const units = buildInventory(movie);
  assert.ok(units.length > 0);
  const m0 = units[0];
  assert.equal(m0.language, "avm2");
  const dis = disassembleUnit(m0);
  assert.equal(dis.state, "decoded");
  assert.ok(dis.pcode.length > 0);
  const dec = decompileUnit(m0);
  assert.equal(dec.state, "structured");
  assert.ok(dec.source.includes("function"));
});

test("JSONL worker responds to open, scripts, disassemble, and decompile", async () => {
  const cliPath = path.join(repoRoot, "Js_swf", "src", "cli.mjs");
  const swfPath = path.join(repoRoot, "benchmark_inputs", "as2_definefunc_regs.swf");
  const child = spawn(process.execPath, [cliPath, "worker"], {
    stdio: ["pipe", "pipe", "pipe"],
  });

  const sendRequest = (req) =>
    new Promise((resolve, reject) => {
      const onData = (chunk) => {
        const lines = chunk.toString("utf8").split("\n").filter(Boolean);
        if (lines.length > 0) {
          child.stdout.off("data", onData);
          resolve(JSON.parse(lines[0]));
        }
      };
      child.stdout.on("data", onData);
      child.once("error", reject);
      child.stdin.write(JSON.stringify(req) + "\n");
    });

  try {
    const openRes = await sendRequest({
      request_id: "r1",
      operation: "open",
      args: { path: swfPath },
    });
    assert.equal(openRes.request_id, "r1");
    assert.ok(openRes.result.script_count >= 1);

    const scriptsRes = await sendRequest({
      request_id: "r2",
      operation: "scripts",
      args: { offset: 0, limit: 50 },
    });
    assert.equal(scriptsRes.request_id, "r2");
    assert.ok(scriptsRes.result.items.length >= 1);
    const firstId = scriptsRes.result.items[0].id;

    const decRes = await sendRequest({
      request_id: "r3",
      operation: "decompile",
      args: { id: firstId },
    });
    assert.equal(decRes.request_id, "r3");
    assert.equal(decRes.result.unit_id, firstId);
    assert.ok(decRes.result.pcode.length > 0);
  } finally {
    child.kill();
  }
});
