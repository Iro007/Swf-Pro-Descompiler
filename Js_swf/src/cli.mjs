#!/usr/bin/env node
/**
 * JsSWF CLI & Persistent JSONL Benchmark Worker.
 */
import readline from "node:readline";
import { performance } from "node:perf_hooks";
import { parseSwfFile, buildInventory } from "./swf.mjs";
import { disassembleUnit, decompileUnit } from "./decompile.mjs";

export const VERSION = "0.3.0";

export function capabilities() {
  return {
    engine: "js",
    display_name: "JsSWF",
    version: VERSION,
    protocol: "rust-swf-jsonl-v1",
    operations: ["capabilities", "open", "info", "scripts", "disassemble", "decompile"],
    languages: ["as2", "as3"],
  };
}

class Session {
  constructor(filePath) {
    this.movie = parseSwfFile(filePath);
    this.units = buildInventory(this.movie);
    this.byId = new Map(this.units.map((u) => [u.id, u]));
  }

  unitMetadata(u) {
    const meta = {
      id: u.id,
      name: u.name,
      language: u.language,
      kind: u.kind,
      tag_path: u.tag_path,
      method_index: u.method_index ?? null,
      body_index: u.body_index ?? null,
      abc_version: u.abc_version ?? null,
      ...(u.abc_sha256 ? { abc_sha256: u.abc_sha256 } : {}),
      byte_start: u.byte_start,
      byte_len: u.byte_len,
      sha256: u.sha256,
      diagnostics: u.diagnostics || [],
      parameters: u.parameters || [],
      avm1_flags: u.avm1_flags ?? null,
      event_flags: u.event_flags ?? null,
      avm1_registers: u.avm1_registers || [],
    };
    if (u.avm1_register_count !== undefined && u.avm1_register_count !== null) {
      meta.avm1_register_count = u.avm1_register_count;
    }
    if (u.avm1_constants_sha256) {
      meta.avm1_constants_sha256 = u.avm1_constants_sha256;
    }
    return meta;
  }

  dispatch(op, args = {}) {
    const id = String(args.id || "");
    const offset = Math.max(0, Number(args.offset || 0));
    const limit = Math.min(500, Math.max(1, Number(args.limit || 200)));
    const query = String(args.query || "").toLowerCase();

    switch (op) {
      case "capabilities":
        return capabilities();
      case "info":
        return {
          info: this.movie.info,
          diagnostics: this.movie.diagnostics,
          script_count: this.units.length,
        };
      case "scripts": {
        const filtered = query
          ? this.units.filter((u) => `${u.id} ${u.name} ${u.language}`.toLowerCase().includes(query))
          : this.units;
        const page = filtered.slice(offset, offset + limit).map((u) => this.unitMetadata(u));
        return {
          total: filtered.length,
          offset,
          items: page,
        };
      }
      case "disassemble": {
        const u = this.byId.get(id);
        if (!u) throw new Error(`Unknown script unit: ${id}`);
        const listing = disassembleUnit(u);
        const pageText = listing.pcode.slice(offset, offset + 65536);
        return {
          unit_id: id,
          state: listing.state,
          pcode: pageText,
          pcode_total: listing.pcode.length,
          offset,
          instruction_total: listing.instructions.length,
          diagnostics: listing.diagnostics,
        };
      }
      case "decompile": {
        const u = this.byId.get(id);
        if (!u) throw new Error(`Unknown script unit: ${id}`);
        return decompileUnit(u);
      }
      default:
        throw new Error(`Unsupported operation: ${op}`);
    }
  }
}

async function runWorker() {
  let session = null;
  const rl = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });

  for await (const line of rl) {
    if (!line.trim()) continue;
    const t0 = performance.now();
    let reqId = null;
    try {
      const req = JSON.parse(line);
      reqId = req.request_id ?? req.id ?? null;
      const op = req.operation || req.op || "";
      const args = req.args || {};
      let result;
      if (op === "open") {
        if (!args.path) throw new Error("Missing path argument");
        session = new Session(args.path);
        result = session.dispatch("info", {});
      } else if (op === "capabilities") {
        result = capabilities();
      } else {
        if (!session) throw new Error("No active SWF session");
        result = session.dispatch(op, args);
      }
      const elapsed = performance.now() - t0;
      process.stdout.write(
        JSON.stringify({
          request_id: reqId,
          elapsed_ms: elapsed,
          result,
        }) + "\n"
      );
    } catch (err) {
      const elapsed = performance.now() - t0;
      process.stdout.write(
        JSON.stringify({
          request_id: reqId,
          elapsed_ms: elapsed,
          error: err.message || String(err),
        }) + "\n"
      );
    }
  }
}

function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0] || "capabilities";
  if (cmd === "worker") {
    runWorker();
    return;
  }
  if (cmd === "capabilities") {
    process.stdout.write(JSON.stringify(capabilities(), null, 2) + "\n");
    return;
  }
  const filePath = argv[1];
  if (!filePath) {
    console.error("Usage: jsswf <worker|capabilities|info|scripts|disassemble|decompile> [swf_path] [unit_id]");
    process.exit(1);
  }
  const session = new Session(filePath);
  if (cmd === "info") {
    process.stdout.write(JSON.stringify(session.dispatch("info", {}), null, 2) + "\n");
  } else if (cmd === "scripts") {
    process.stdout.write(JSON.stringify(session.dispatch("scripts", { offset: 0, limit: 500 }), null, 2) + "\n");
  } else if (cmd === "disassemble") {
    process.stdout.write(JSON.stringify(session.dispatch("disassemble", { id: argv[2] || "" }), null, 2) + "\n");
  } else if (cmd === "decompile") {
    process.stdout.write(JSON.stringify(session.dispatch("decompile", { id: argv[2] || "" }), null, 2) + "\n");
  } else {
    console.error(`Unknown command: ${cmd}`);
    process.exit(1);
  }
}

main();
