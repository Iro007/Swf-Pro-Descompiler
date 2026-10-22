/**
 * AVM1 (AS1/AS2) action decoder, embedded action scanner, nested function discovery, and P-code formatter.
 */
import crypto from "node:crypto";
import { BitReader } from "./swf.mjs";

const AVM1_NAMES = {
  0x00: "end",
  0x04: "next_frame",
  0x05: "prev_frame",
  0x06: "play",
  0x07: "stop",
  0x08: "toggle_quality",
  0x09: "stop_sounds",
  0x0a: "add",
  0x0b: "subtract",
  0x0c: "multiply",
  0x0d: "divide",
  0x0e: "equals",
  0x0f: "less_than",
  0x10: "and",
  0x11: "or",
  0x12: "not",
  0x13: "string_equals",
  0x14: "string_length",
  0x15: "string_extract",
  0x17: "pop",
  0x18: "to_integer",
  0x1c: "get_variable",
  0x1d: "set_variable",
  0x20: "set_target2",
  0x21: "string_add",
  0x22: "get_property",
  0x23: "set_property",
  0x24: "clone_sprite",
  0x25: "remove_sprite",
  0x26: "trace",
  0x27: "start_drag",
  0x28: "end_drag",
  0x29: "string_less_than",
  0x2a: "throw",
  0x2b: "cast_op",
  0x2c: "implements_op",
  0x2d: "fs_command2",
  0x30: "random_number",
  0x31: "mb_string_length",
  0x32: "char_to_ascii",
  0x33: "ascii_to_char",
  0x34: "get_time",
  0x35: "mb_string_extract",
  0x36: "mb_char_to_ascii",
  0x37: "mb_ascii_to_char",
  0x3a: "delete",
  0x3b: "delete2",
  0x3c: "define_local",
  0x3d: "call_function",
  0x3e: "return",
  0x3f: "modulo",
  0x40: "new_object",
  0x41: "define_local2",
  0x42: "init_array",
  0x43: "init_object",
  0x44: "type_of",
  0x45: "target_path",
  0x46: "enumerate",
  0x47: "add2",
  0x48: "less2",
  0x49: "equals2",
  0x4a: "to_number",
  0x4b: "to_string",
  0x4c: "push_duplicate",
  0x4d: "stack_swap",
  0x4e: "get_member",
  0x4f: "set_member",
  0x50: "increment",
  0x51: "decrement",
  0x52: "call_method",
  0x53: "new_method",
  0x54: "instance_of",
  0x55: "enumerate2",
  0x60: "bit_and",
  0x61: "bit_or",
  0x62: "bit_xor",
  0x63: "bit_lshift",
  0x64: "bit_rshift",
  0x65: "bit_urshift",
  0x66: "strict_equals",
  0x67: "greater",
  0x68: "string_greater",
  0x69: "extends",
  0x81: "goto_frame",
  0x83: "get_url",
  0x87: "store_register",
  0x88: "constant_pool",
  0x89: "strict_mode",
  0x8a: "wait_for_frame",
  0x8b: "set_target",
  0x8c: "goto_label",
  0x8d: "wait_for_frame2",
  0x8e: "define_function2",
  0x8f: "try",
  0x94: "with",
  0x96: "push",
  0x99: "jump",
  0x9a: "get_url2",
  0x9b: "define_function",
  0x9d: "if",
  0x9e: "call",
  0x9f: "goto_frame2",
};

function sha256Hex(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function readCString(buf, posObj) {
  const zero = buf.indexOf(0, posObj.pos);
  if (zero < 0) {
    throw new Error("Unterminated AVM1 string");
  }
  const slice = buf.subarray(posObj.pos, zero);
  posObj.pos = zero + 1;
  return slice.toString("utf8");
}

function skipMatrix(bits) {
  if (bits.unsigned(1)) {
    const n = bits.unsigned(5);
    bits.unsigned(n);
    bits.unsigned(n);
  }
  if (bits.unsigned(1)) {
    const n = bits.unsigned(5);
    bits.unsigned(n);
    bits.unsigned(n);
  }
  const n = bits.unsigned(5);
  bits.unsigned(n);
  bits.unsigned(n);
}

function skipCxform(bits) {
  const add = bits.unsigned(1);
  const mult = bits.unsigned(1);
  const n = bits.unsigned(4);
  for (let i = 0; i < 4 * (add + mult); i++) {
    bits.unsigned(n);
  }
}

export function embeddedAvm1Actions(data, kind, version) {
  const out = [];
  let pos = 0;
  const take = (n) => {
    if (n < 0 || pos + n > data.length) throw new Error("Truncated embedded action container");
    const s = data.subarray(pos, pos + n);
    pos += n;
    return s;
  };
  const uint = (w) => {
    const b = take(w);
    if (w === 1) return b[0];
    if (w === 2) return b.readUInt16LE(0);
    if (w === 4) return b.readUInt32LE(0);
    throw new Error("Unsupported uint width");
  };
  const skipString = () => {
    const zero = data.indexOf(0, pos);
    if (zero < 0) throw new Error("Unterminated container string");
    pos = zero + 1;
  };
  const readBits = (fn) => {
    const br = new BitReader(data, pos);
    fn(br);
    br.align();
    take(br.byteLen());
  };

  if (kind === 7) {
    take(2);
    while (uint(1)) {
      take(4);
      readBits(skipMatrix);
    }
    if (pos < data.length) {
      out.push({ kind: "button", start: pos, end: data.length, event: null });
    }
  } else if (kind === 34) {
    take(3);
    const actionOffset = uint(2);
    if (!actionOffset) return out;
    pos = 3 + actionOffset;
    if (pos < 5 || pos >= data.length) throw new Error("Invalid Button2 action offset");
    while (pos < data.length) {
      const start = pos;
      const size = uint(2);
      const flags = uint(2);
      const end = size ? start + size : data.length;
      if (end < pos || end > data.length) throw new Error("Invalid ButtonCondAction size");
      out.push({ kind: "button2", start: pos, end, event: flags });
      pos = end;
      if (!size) break;
    }
  } else if (kind === 26 || kind === 70 || kind === 94) {
    const flags = uint(1);
    const extra = kind === 70 || kind === 94 ? uint(1) : 0;
    take(2);
    if ((extra & 8) || ((extra & 16) && (flags & 2))) skipString();
    if (flags & 2) take(2);
    if (flags & 4) readBits(skipMatrix);
    if (flags & 8) readBits(skipCxform);
    if (flags & 16) take(2);
    if (flags & 32) skipString();
    if (flags & 64) take(2);
    if (extra & 1) {
      const count = uint(1);
      for (let i = 0; i < count; i++) {
        const fkind = uint(1);
        const fixedSizes = { 0: 23, 1: 9, 2: 15, 3: 27, 6: 80 };
        if (fkind in fixedSizes) take(fixedSizes[fkind]);
        else if (fkind === 4 || fkind === 7) take(uint(1) * 5 + 19);
        else if (fkind === 5) {
          const w = uint(1), h = uint(1);
          take(13 + 4 * w * h);
        } else throw new Error(`Unknown filter ${fkind}`);
      }
    }
    if (extra & 2) take(1);
    if (extra & 4) take(1);
    if (extra & 32) take(1);
    if (extra & 64) take(4);
    if (!(flags & 128)) return out;
    const width = version >= 6 ? 4 : 2;
    take(2 + width);
    while (true) {
      const event = uint(width);
      if (!event) break;
      const size = uint(4);
      const end = pos + size;
      if (event & 0x20000) take(1);
      if (end < pos || end > data.length) throw new Error("Invalid ClipActionRecord size");
      out.push({ kind: "clip", start: pos, end, event });
      pos = end;
    }
  }
  return out;
}

export function discoverNestedAvm1Functions(parentUnit, code, relativeBase, initialPool, inheritedVerified = true, depth = 0) {
  if (depth >= 64) {
    throw new Error("Nested AVM1 function depth limit exceeded");
  }
  // Check pool lineage
  let firstBranch = null;
  const poolOffsets = [];
  {
    let pos = 0;
    while (pos < code.length) {
      const offset = pos;
      const op = code[pos++];
      if (op === 0) break;
      if (op < 0x80) continue;
      if (pos + 2 > code.length) break;
      const len = code.readUInt16LE(pos);
      pos += 2;
      if (pos + len > code.length) break;
      const payload = code.subarray(pos, pos + len);
      pos += len;
      if ((op === 0x99 || op === 0x9d || op === 0x8a || op === 0x8d) && firstBranch === null) {
        firstBranch = offset;
      }
      if (op === 0x88) {
        poolOffsets.push(offset);
      }
      if (op === 0x9b || op === 0x8e || op === 0x94) {
        if (payload.length < 2) break;
        const bodyLen = payload.readUInt16LE(payload.length - 2);
        pos += bodyLen;
      } else if (op === 0x8f) {
        if (payload.length < 7) break;
        const sum = payload.readUInt16LE(1) + payload.readUInt16LE(3) + payload.readUInt16LE(5);
        pos += sum;
      }
    }
  }
  const poolVerified =
    inheritedVerified &&
    !(firstBranch !== null && (poolOffsets.length > 1 || poolOffsets.some((p) => p > firstBranch)));

  const output = [];
  let pool = [...initialPool];
  let pos = 0;
  while (pos < code.length) {
    const op = code[pos++];
    if (op === 0) break;
    if (op < 0x80) continue;
    if (pos + 2 > code.length) throw new Error("Truncated AVM1 action header");
    const len = code.readUInt16LE(pos);
    pos += 2;
    if (pos + len > code.length) throw new Error("Truncated AVM1 action payload");
    const payload = code.subarray(pos, pos + len);
    pos += len;
    const hPos = { pos: 0 };
    if (op === 0x88) {
      if (payload.length < 2) throw new Error("Truncated ConstantPool");
      const count = payload.readUInt16LE(0);
      hPos.pos = 2;
      const nextPool = [];
      for (let i = 0; i < count; i++) {
        nextPool.push(readCString(payload, hPos));
      }
      pool = nextPool;
    } else if (op === 0x9b || op === 0x8e) {
      const fnName = readCString(payload, hPos);
      if (hPos.pos + 2 > payload.length) throw new Error("Truncated function param count");
      const paramCount = payload.readUInt16LE(hPos.pos);
      hPos.pos += 2;
      let registerCount = null;
      let flags = 0;
      const registers = [];
      if (op === 0x8e) {
        if (hPos.pos + 3 > payload.length) throw new Error("Truncated DefineFunction2 header");
        registerCount = payload[hPos.pos++];
        flags = payload.readUInt16LE(hPos.pos);
        hPos.pos += 2;
      }
      const parameters = [];
      for (let i = 0; i < paramCount; i++) {
        if (op === 0x8e) {
          if (hPos.pos >= payload.length) throw new Error("Truncated DefineFunction2 param");
          registers.push(payload[hPos.pos++]);
        }
        parameters.push(readCString(payload, hPos));
      }
      if (hPos.pos + 2 !== payload.length) {
        throw new Error("Unexpected AVM1 function header bytes");
      }
      const codeSize = payload.readUInt16LE(hPos.pos);
      if (pos + codeSize > code.length) {
        throw new Error("Truncated AVM1 function body");
      }
      const bodyStart = relativeBase + pos;
      const body = code.subarray(pos, pos + codeSize);
      pos += codeSize;

      const poolJson = JSON.stringify(pool);
      const constantsSha256 = sha256Hex(Buffer.from(poolJson, "utf8"));
      const diagnostics = [];
      if (!poolVerified) {
        diagnostics.push({
          code: "AVM1_POOL_CONTEXT_UNPROVEN",
          message: "Captured constant pool follows ambiguous control flow; linear lineage is not a runtime proof",
          offset: bodyStart,
        });
      }
      const child = {
        id: `${parentUnit.id}/function:${bodyStart.toString(16)}`,
        name: fnName || `anonymous_${bodyStart.toString(16)}`,
        language: "avm1",
        kind: "function",
        tag_path: parentUnit.tag_path,
        byte_start: bodyStart,
        byte_len: body.length,
        sha256: sha256Hex(body),
        diagnostics,
        parameters,
        avm1_flags: flags,
        event_flags: parentUnit.event_flags,
        avm1_registers: registers,
        ...(registerCount !== null ? { avm1_register_count: registerCount } : {}),
        avm1_constants_sha256: constantsSha256,
        avm1_constants: [...pool],
        pool_verified: poolVerified,
        code: body,
      };
      const grandChildren = discoverNestedAvm1Functions(child, body, bodyStart, pool, poolVerified, depth + 1);
      output.push(child);
      output.push(...grandChildren);
    } else if (op === 0x94) {
      if (payload.length !== 2) throw new Error("Unexpected AVM1 With header bytes");
      const size = payload.readUInt16LE(0);
      const start = relativeBase + pos;
      const body = code.subarray(pos, pos + size);
      pos += size;
      output.push(...discoverNestedAvm1Functions(parentUnit, body, start, pool, poolVerified, depth + 1));
    } else if (op === 0x8f) {
      if (payload.length < 7) throw new Error("Truncated AVM1 Try header");
      const flags = payload[0];
      const sizes = [payload.readUInt16LE(1), payload.readUInt16LE(3), payload.readUInt16LE(5)];
      for (const size of sizes) {
        const start = relativeBase + pos;
        const body = code.subarray(pos, pos + size);
        pos += size;
        output.push(...discoverNestedAvm1Functions(parentUnit, body, start, pool, poolVerified, depth + 1));
      }
    }
  }
  return output;
}

export function disassembleAvm1(unit) {
  const data = unit.code;
  let pool = [...(unit.avm1_constants || [])];
  const instructions = [];
  const diagnostics = [];
  let pos = 0;

  while (pos < data.length) {
    const start = pos;
    const op = data[pos++];
    const name = AVM1_NAMES[op];
    if (!name) {
      diagnostics.push({
        code: "avm1_unknown_opcode",
        message: `unknown action 0x${op.toString(16).padStart(2, "0")}; raw tail preserved`,
        offset: start,
      });
      break;
    }
    const operands = [];
    const branchTargets = [];
    try {
      if (op >= 0x80) {
        if (pos + 2 > data.length) throw new Error("truncated action length");
        const len = data.readUInt16LE(pos);
        pos += 2;
        if (pos + len > data.length) throw new Error("truncated action payload");
        const payload = data.subarray(pos, pos + len);
        pos += len;
        const pObj = { pos: 0 };
        if (op === 0x81) {
          operands.push(String(payload.readUInt16LE(0)));
        } else if (op === 0x83) {
          operands.push(JSON.stringify(readCString(payload, pObj)));
          operands.push(JSON.stringify(readCString(payload, pObj)));
        } else if (op === 0x87 || op === 0x89 || op === 0x8d || op === 0x9a) {
          operands.push(String(payload[0]));
        } else if (op === 0x88) {
          const count = payload.readUInt16LE(0);
          pObj.pos = 2;
          const nextPool = [];
          for (let i = 0; i < count; i++) {
            nextPool.push(readCString(payload, pObj));
          }
          pool = nextPool;
          operands.push(pool.map((s) => JSON.stringify(s)).join(", "));
        } else if (op === 0x8a) {
          operands.push(String(payload.readUInt16LE(0)));
          operands.push(String(payload[2]));
        } else if (op === 0x8b || op === 0x8c) {
          operands.push(JSON.stringify(readCString(payload, pObj)));
        } else if (op === 0x96) {
          const vals = [];
          while (pObj.pos < payload.length) {
            const type = payload[pObj.pos++];
            if (type === 0) {
              vals.push(JSON.stringify(readCString(payload, pObj)));
            } else if (type === 1) {
              const v = payload.readFloatLE(pObj.pos);
              pObj.pos += 4;
              vals.push(Number.isFinite(v) ? String(v) : "NaN");
            } else if (type === 2) {
              vals.push("null");
            } else if (type === 3) {
              vals.push("undefined");
            } else if (type === 4) {
              vals.push(`r:${payload[pObj.pos++]}`);
            } else if (type === 5) {
              vals.push(payload[pObj.pos++] === 0 ? "false" : "true");
            } else if (type === 6) {
              const swapped = Buffer.allocUnsafe(8);
              payload.copy(swapped, 0, pObj.pos + 4, pObj.pos + 8);
              payload.copy(swapped, 4, pObj.pos, pObj.pos + 4);
              pObj.pos += 8;
              const v = swapped.readDoubleLE(0);
              vals.push(Number.isFinite(v) ? String(v) : "NaN");
            } else if (type === 7) {
              const v = payload.readInt32LE(pObj.pos);
              pObj.pos += 4;
              vals.push(String(v));
            } else if (type === 8 || type === 9) {
              const idx = type === 8 ? payload[pObj.pos++] : (pObj.pos += 2, payload.readUInt16LE(pObj.pos - 2));
              if (idx < pool.length) {
                vals.push(`c:${idx}=${JSON.stringify(pool[idx])}`);
              } else {
                vals.push(`c:${idx}`);
              }
            } else {
              throw new Error(`unknown push type ${type}`);
            }
          }
          operands.push(vals.join(", "));
        } else if (op === 0x99 || op === 0x9d) {
          const rel = payload.readInt16LE(0);
          operands.push(String(rel));
          const target = pos + rel;
          if (target >= 0 && target <= data.length) {
            branchTargets.push(target);
          }
        } else if (op === 0x9b || op === 0x8e) {
          const fnName = readCString(payload, pObj);
          const paramCount = payload.readUInt16LE(pObj.pos);
          pObj.pos += 2;
          let regCount = 0, flags = 0;
          if (op === 0x8e) {
            regCount = payload[pObj.pos++];
            flags = payload.readUInt16LE(pObj.pos);
            pObj.pos += 2;
          }
          const params = [];
          const registers = [];
          for (let i = 0; i < paramCount; i++) {
            if (op === 0x8e) registers.push(payload[pObj.pos++]);
            params.push(readCString(payload, pObj));
          }
          const codeSize = payload.readUInt16LE(pObj.pos);
          const bodyBytes = data.subarray(pos, pos + codeSize);
          pos += codeSize;
          operands.push(JSON.stringify(fnName));
          if (op === 0x8e) {
            operands.push(String(regCount), String(flags));
          }
          operands.push(String(codeSize));
          instructions.push({
            offset: start,
            length: pos - start,
            opcode: op,
            name,
            operands,
            branchTargets,
            fnMeta: { fnName, params, registers, regCount, flags, bodyBytes },
          });
          continue;
        } else if (op === 0x94) {
          const size = payload.readUInt16LE(0);
          pos += size;
          operands.push(String(size));
        } else if (op === 0x8f) {
          const s0 = payload.readUInt16LE(1);
          const s1 = payload.readUInt16LE(3);
          const s2 = payload.readUInt16LE(5);
          pos += s0 + s1 + s2;
          operands.push(String(s0), String(s1), String(s2));
        } else if (op === 0x9f) {
          const flags = payload[0];
          operands.push(String(flags));
          if (flags & 2) operands.push(String(payload.readUInt16LE(1)));
        }
      }
      instructions.push({
        offset: start,
        length: pos - start,
        opcode: op,
        name,
        operands,
        branchTargets,
      });
    } catch (err) {
      diagnostics.push({ code: "avm1_decode_error", message: err.message, offset: start });
      break;
    }
  }

  let pcode = "";
  for (const ins of instructions) {
    const args = ins.operands.join(" ");
    const targets = ins.branchTargets.length ? ` ; targets=[${ins.branchTargets.join(", ")}]` : "";
    pcode += `${ins.offset.toString(16).padStart(6, "0")}: ${ins.name} ${args}${targets}\n`;
  }
  const state = diagnostics.length === 0 ? "decoded" : instructions.length === 0 ? "invalid" : "partial";
  return { instructions, pcode, diagnostics, state, pool };
}
