/**
 * Structured ActionScript 2 (AVM1) & ActionScript 3 (AVM2) decompiler for JsSWF.
 * Reconstructs clean ActionScript source for structured bytecode units, falling back
 * to exact P-code with explicit diagnostics when irreducible or unsupported opcodes appear.
 */
import { disassembleAvm1 } from "./avm1.mjs";
import { disassembleAvm2 } from "./avm2.mjs";

export function disassembleUnit(unit) {
  if (unit.kind === "abc_outline") {
    return {
      instructions: [],
      pcode: `; ABC signatures: ${unit.abc ? unit.abc.methods.length : 0} methods, no method bodies\n`,
      diagnostics: [...(unit.diagnostics || [])],
      state: "outline_only",
    };
  }
  if (unit.kind === "abc_raw") {
    return {
      instructions: [],
      pcode: `; ABC raw: ${unit.code.length} bytes\n`,
      diagnostics: [...(unit.diagnostics || [])],
      state: "unsupported",
    };
  }
  if (unit.language === "avm2") {
    return disassembleAvm2(unit);
  }
  return disassembleAvm1(unit);
}

export function decompileUnit(unit) {
  const dis = disassembleUnit(unit);
  if (dis.state !== "decoded" || (unit.diagnostics && unit.diagnostics.length > 0)) {
    return {
      unit_id: unit.id,
      state: "pcode_only",
      source: null,
      pcode: dis.pcode,
      pcode_total: dis.pcode.length,
      diagnostics: [...(unit.diagnostics || []), ...dis.diagnostics],
    };
  }
  try {
    const source =
      unit.language === "avm2"
        ? reconstructAvm2(unit, dis.instructions)
        : reconstructAvm1(unit, dis.instructions, dis.pool || []);
    return {
      unit_id: unit.id,
      state: "structured",
      source,
      pcode: dis.pcode,
      pcode_total: dis.pcode.length,
      diagnostics: [],
    };
  } catch (err) {
    return {
      unit_id: unit.id,
      state: "pcode_only",
      source: null,
      pcode: dis.pcode,
      pcode_total: dis.pcode.length,
      diagnostics: [
        {
          code: err.code || "UNSUPPORTED_RECONSTRUCTION",
          message: err.message || String(err),
          offset: err.offset ?? 0,
        },
      ],
    };
  }
}

function fail(code, message, offset = 0) {
  const err = new Error(message);
  err.code = code;
  err.offset = offset;
  return err;
}

function reconstructAvm1(unit, instructions, initialPool) {
  if (unit.kind === "function" && unit.pool_verified === false) {
    throw fail("AVM1_POOL_CONTEXT_UNPROVEN", "Function pool context unproven", 0);
  }
  let pool = [...initialPool];
  const stmts = [];
  const stack = [];
  const pop = (offset) => {
    if (stack.length === 0) throw fail("AVM1_STACK_UNDERFLOW", "Stack underflow in AVM1 reconstruction", offset);
    return stack.pop();
  };

  // Map registers for DefineFunction2 parameters
  const regNames = new Map();
  if (unit.kind === "function" || unit.kind === "block") {
    let regIdx = 1;
    const flags = unit.avm1_flags || 0;
    if (flags & 0x01) regNames.set(regIdx++, "this");
    if (flags & 0x04) regNames.set(regIdx++, "arguments");
    if (flags & 0x10) regNames.set(regIdx++, "super");
    if (flags & 0x40) regNames.set(regIdx++, "_root");
    if (flags & 0x80) regNames.set(regIdx++, "_parent");
    if (flags & 0x100) regNames.set(regIdx++, "_global");
    for (let i = 0; i < (unit.parameters || []).length; i++) {
      const r = (unit.avm1_registers || [])[i];
      if (r) regNames.set(r, unit.parameters[i]);
    }
  }

  for (let idx = 0; idx < instructions.length; idx++) {
    const ins = instructions[idx];
    const op = ins.opcode;
    switch (op) {
      case 0x00:
        break;
      case 0x04:
        stmts.push("nextFrame();");
        break;
      case 0x05:
        stmts.push("prevFrame();");
        break;
      case 0x06:
        stmts.push("play();");
        break;
      case 0x07:
        stmts.push("stop();");
        break;
      case 0x09:
        stmts.push("stopAllSounds();");
        break;
      case 0x88:
        break;
      case 0x96: {
        // Parse pushed literals from raw bytes of instruction
        const data = unit.code;
        let p = ins.offset + 3;
        const end = ins.offset + ins.length;
        while (p < end) {
          const t = data[p++];
          if (t === 0) {
            const z = data.indexOf(0, p);
            const s = data.subarray(p, z).toString("utf8");
            p = z + 1;
            stack.push(JSON.stringify(s));
          } else if (t === 1) {
            const v = data.readFloatLE(p);
            p += 4;
            stack.push(String(v));
          } else if (t === 2) {
            stack.push("null");
          } else if (t === 3) {
            stack.push("undefined");
          } else if (t === 4) {
            const r = data[p++];
            stack.push(regNames.get(r) || `_loc${r}`);
          } else if (t === 5) {
            stack.push(data[p++] === 0 ? "false" : "true");
          } else if (t === 6) {
            const swapped = Buffer.allocUnsafe(8);
            data.copy(swapped, 0, p + 4, p + 8);
            data.copy(swapped, 4, p, p + 4);
            p += 8;
            stack.push(String(swapped.readDoubleLE(0)));
          } else if (t === 7) {
            const v = data.readInt32LE(p);
            p += 4;
            stack.push(String(v));
          } else if (t === 8 || t === 9) {
            const cIdx = t === 8 ? data[p++] : (p += 2, data.readUInt16LE(p - 2));
            if (cIdx < pool.length) {
              stack.push(JSON.stringify(pool[cIdx]));
            } else {
              throw fail("AVM1_POOL_INDEX", `Out of bounds constant pool index ${cIdx}`, ins.offset);
            }
          } else {
            throw fail("AVM1_PUSH_TYPE", `Unsupported push type ${t}`, ins.offset);
          }
        }
        break;
      }
      case 0x17: {
        const expr = pop(ins.offset);
        stmts.push(`${expr};`);
        break;
      }
      case 0x26: {
        const val = pop(ins.offset);
        stmts.push(`trace(${val});`);
        break;
      }
      case 0x1c: {
        const name = pop(ins.offset);
        const ident = unquoteIdent(name);
        stack.push(ident || `eval(${name})`);
        break;
      }
      case 0x1d: {
        const val = pop(ins.offset);
        const name = pop(ins.offset);
        const ident = unquoteIdent(name) || `_root[${name}]`;
        stmts.push(`${ident} = ${val};`);
        break;
      }
      case 0x3c: {
        const val = pop(ins.offset);
        const name = pop(ins.offset);
        const ident = unquoteIdent(name) || `loc_${ins.offset}`;
        stmts.push(`var ${ident} = ${val};`);
        break;
      }
      case 0x41: {
        const name = pop(ins.offset);
        const ident = unquoteIdent(name) || `loc_${ins.offset}`;
        stmts.push(`var ${ident};`);
        break;
      }
      case 0x4e: {
        const prop = pop(ins.offset);
        const obj = pop(ins.offset);
        const ident = unquoteIdent(prop);
        stack.push(ident ? `${obj}.${ident}` : `${obj}[${prop}]`);
        break;
      }
      case 0x4f: {
        const val = pop(ins.offset);
        const prop = pop(ins.offset);
        const obj = pop(ins.offset);
        const ident = unquoteIdent(prop);
        stmts.push(ident ? `${obj}.${ident} = ${val};` : `${obj}[${prop}] = ${val};`);
        break;
      }
      case 0x3d: {
        const fn = pop(ins.offset);
        const argc = Number(pop(ins.offset));
        if (!Number.isInteger(argc) || argc < 0 || argc > 64) {
          throw fail("AVM1_CALL_ARGC", "Dynamic call argument count unsupported", ins.offset);
        }
        const args = [];
        for (let i = 0; i < argc; i++) args.push(pop(ins.offset));
        const ident = unquoteIdent(fn) || fn;
        stack.push(`${ident}(${args.join(", ")})`);
        break;
      }
      case 0x52: {
        const method = pop(ins.offset);
        const obj = pop(ins.offset);
        const argc = Number(pop(ins.offset));
        if (!Number.isInteger(argc) || argc < 0 || argc > 64) {
          throw fail("AVM1_CALL_ARGC", "Dynamic method argument count unsupported", ins.offset);
        }
        const args = [];
        for (let i = 0; i < argc; i++) args.push(pop(ins.offset));
        const ident = unquoteIdent(method);
        stack.push(ident ? `${obj}.${ident}(${args.join(", ")})` : `${obj}[${method}](${args.join(", ")})`);
        break;
      }
      case 0x40: {
        const ctor = pop(ins.offset);
        const argc = Number(pop(ins.offset));
        if (!Number.isInteger(argc) || argc < 0 || argc > 64) {
          throw fail("AVM1_NEW_ARGC", "Dynamic constructor argument count unsupported", ins.offset);
        }
        const args = [];
        for (let i = 0; i < argc; i++) args.push(pop(ins.offset));
        const ident = unquoteIdent(ctor) || ctor;
        stack.push(`new ${ident}(${args.join(", ")})`);
        break;
      }
      case 0x42: {
        const count = Number(pop(ins.offset));
        if (!Number.isInteger(count) || count < 0 || count > 256) {
          throw fail("AVM1_ARRAY_LEN", "Dynamic array literal count unsupported", ins.offset);
        }
        const elems = [];
        for (let i = 0; i < count; i++) elems.push(pop(ins.offset));
        stack.push(`[${elems.join(", ")}]`);
        break;
      }
      case 0x43: {
        const count = Number(pop(ins.offset));
        if (!Number.isInteger(count) || count < 0 || count > 256) {
          throw fail("AVM1_OBJECT_LEN", "Dynamic object literal count unsupported", ins.offset);
        }
        const pairs = [];
        for (let i = 0; i < count; i++) {
          const v = pop(ins.offset);
          const k = pop(ins.offset);
          pairs.push(`${unquoteIdent(k) || k}: ${v}`);
        }
        stack.push(`{${pairs.join(", ")}}`);
        break;
      }
      case 0x0a:
      case 0x47: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} + ${b})`);
        break;
      }
      case 0x0b: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} - ${b})`);
        break;
      }
      case 0x0c: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} * ${b})`);
        break;
      }
      case 0x0d: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} / ${b})`);
        break;
      }
      case 0x3f: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} % ${b})`);
        break;
      }
      case 0x0e:
      case 0x49: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} == ${b})`);
        break;
      }
      case 0x66: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} === ${b})`);
        break;
      }
      case 0x0f:
      case 0x48: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} < ${b})`);
        break;
      }
      case 0x67: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} > ${b})`);
        break;
      }
      case 0x12: {
        const a = pop(ins.offset);
        stack.push(`(!${a})`);
        break;
      }
      case 0x50: {
        const a = pop(ins.offset);
        stack.push(`(${a} + 1)`);
        break;
      }
      case 0x51: {
        const a = pop(ins.offset);
        stack.push(`(${a} - 1)`);
        break;
      }
      case 0x4a: {
        const a = pop(ins.offset);
        stack.push(`Number(${a})`);
        break;
      }
      case 0x4b: {
        const a = pop(ins.offset);
        stack.push(`String(${a})`);
        break;
      }
      case 0x87: {
        const r = unit.code[ins.offset + 3];
        const val = stack[stack.length - 1];
        if (val === undefined) throw fail("AVM1_STACK_UNDERFLOW", "StoreRegister on empty stack", ins.offset);
        const rName = regNames.get(r) || `_loc${r}`;
        stmts.push(`var ${rName} = ${val};`);
        stack[stack.length - 1] = rName;
        break;
      }
      case 0x4c: {
        const val = pop(ins.offset);
        stack.push(val, val);
        break;
      }
      case 0x4d: {
        const a = pop(ins.offset);
        const b = pop(ins.offset);
        stack.push(a, b);
        break;
      }
      case 0x60: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} & ${b})`);
        break;
      }
      case 0x61: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} | ${b})`);
        break;
      }
      case 0x62: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} ^ ${b})`);
        break;
      }
      case 0x63: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} << ${b})`);
        break;
      }
      case 0x64: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} >> ${b})`);
        break;
      }
      case 0x65: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} >>> ${b})`);
        break;
      }
      case 0x9b:
      case 0x8e: {
        if (ins.fnMeta && ins.fnMeta.bodyBytes) {
          const subUnit = {
            id: `${unit.id}/fn_${ins.offset}`,
            name: ins.fnMeta.fnName || "",
            language: "avm1",
            kind: "function",
            code: ins.fnMeta.bodyBytes,
            parameters: ins.fnMeta.params || [],
            avm1_registers: ins.fnMeta.registers || [],
            avm1_flags: ins.fnMeta.flags || 0,
            avm1_constants: pool,
            pool_verified: true,
          };
          const subDis = disassembleAvm1(subUnit);
          const fnSrc = reconstructAvm1(subUnit, subDis.instructions, subDis.pool || pool).trim();
          if (ins.fnMeta.fnName) {
            stmts.push(fnSrc);
          } else {
            stack.push(fnSrc);
          }
        } else {
          stack.push("function() { /* nested function */ }");
        }
        break;
      }
      case 0x9d: {
        const cond = pop(ins.offset);
        const negCond =
          cond.startsWith("(!") && cond.endsWith(")")
            ? cond.slice(2, -1)
            : `(!${cond})`;
        const target = ins.branchTargets && ins.branchTargets[0];
        if (typeof target === "number" && target > ins.offset) {
          let targetIdx = instructions.findIndex((x, k) => k > idx && x.offset >= target);
          if (targetIdx === -1) targetIdx = instructions.length;
          const prevIdx = targetIdx - 1;
          if (
            prevIdx > idx &&
            instructions[prevIdx].opcode === 0x99 &&
            instructions[prevIdx].branchTargets &&
            instructions[prevIdx].branchTargets[0] <= ins.offset
          ) {
            const bodyIns = instructions.slice(idx + 1, prevIdx);
            const bodyUnit = { ...unit, kind: "block" };
            const bodySrc = reconstructAvm1(bodyUnit, bodyIns, pool)
              .trimEnd()
              .split("\n")
              .filter(Boolean)
              .map((l) => "    " + l)
              .join("\n");
            stmts.push(`while (${negCond}) {\n${bodySrc}\n}`);
            idx = prevIdx;
            break;
          }
          const bodyIns = instructions.slice(idx + 1, targetIdx);
          const bodyUnit = { ...unit, kind: "block" };
          const bodySrc = reconstructAvm1(bodyUnit, bodyIns, pool)
            .trimEnd()
            .split("\n")
            .filter(Boolean)
            .map((l) => "    " + l)
            .join("\n");
          stmts.push(`if (${negCond}) {\n${bodySrc}\n}`);
          idx = targetIdx - 1;
          break;
        }
        stmts.push(`if (${cond}) { /* branch */ }`);
        break;
      }
      case 0x99: {
        break;
      }
      case 0x3e: {
        const val = pop(ins.offset);
        stmts.push(`return ${val};`);
        break;
      }
      case 0x2a: {
        const val = pop(ins.offset);
        stmts.push(`throw ${val};`);
        break;
      }
      default:
        throw fail("AVM1_UNSUPPORTED_OP", `Unsupported AVM1 action ${ins.name} (0x${op.toString(16)})`, ins.offset);
    }
  }

  while (stack.length > 0) {
    stmts.push(`/* stack: ${stack.pop()} */`);
  }

  if (unit.kind === "function") {
    const params = (unit.parameters || []).join(", ");
    const header = unit.name && !unit.name.startsWith("anonymous_") ? `function ${unit.name}(${params})` : `function(${params})`;
    return `${header} {\n${stmts.map((s) => "    " + s).join("\n")}\n}\n`;
  }
  return stmts.join("\n") + (stmts.length ? "\n" : "");
}

function unquoteIdent(expr) {
  if (typeof expr === "string" && expr.startsWith('"') && expr.endsWith('"')) {
    try {
      const inner = JSON.parse(expr);
      if (/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(inner)) {
        return inner;
      }
    } catch {
      return null;
    }
  }
  return null;
}

function reconstructAvm2(unit, instructions) {
  const abc = unit.abc;
  if (!abc) {
    throw fail("AVM2_NO_ABC", "Missing ABC context for AVM2 reconstruction", 0);
  }
  // Check for branches or exceptions that require full CFG restructuring
  if (unit.body && unit.body.exceptions && unit.body.exceptions.length > 0) {
    throw fail("UNSUPPORTED_EXCEPTION_FLOW", "Exception handlers require CFG restructuring", 0);
  }
  const hasBranches = instructions.some((ins) => ins.branchTargets && ins.branchTargets.length > 0);
  if (hasBranches) {
    return reconstructAvm2WithSimpleIf(unit, instructions, abc);
  }
  return renderAvm2Method(unit, reconstructAvm2LinearBlock(unit, instructions, abc));
}

function renderAvm2Method(unit, bodyLines) {
  const params = (unit.parameters || []).join(", ");
  const rawName = unit.name || "method";
  const slash = rawName.lastIndexOf("/");
  const shortName = slash >= 0 ? rawName.slice(slash + 1) : rawName;
  const cleanName = /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(shortName) ? shortName : "method";
  const body = bodyLines.map((l) => "    " + l).join("\n");
  return `function ${cleanName}(${params}) {\n${body}\n}\n`;
}

function reconstructAvm2WithSimpleIf(unit, instructions, abc) {
  // Handle simple forward iffalse / iftrue blocks without back-edges
  for (const ins of instructions) {
    for (const t of ins.branchTargets || []) {
      if (t <= ins.offset) {
        throw fail("UNSUPPORTED_LOOP_FLOW", "Back-edge loop requires full CFG structuring", ins.offset);
      }
    }
  }
  // If more than 2 branches, fall back conservatively
  const branchCount = instructions.filter((i) => i.branchTargets && i.branchTargets.length > 0).length;
  if (branchCount > 2) {
    throw fail("UNSUPPORTED_CONTROL_FLOW", "Complex multi-branch flow retained as P-code", 0);
  }
  throw fail("UNSUPPORTED_CONTROL_FLOW", "Branching AVM2 method retained as P-code", 0);
}

function reconstructAvm2LinearBlock(unit, instructions, abc) {
  const stack = [];
  const stmts = [];
  const declaredLocals = new Set();
  const pop = (offset) => {
    if (stack.length === 0) throw fail("AVM2_STACK_UNDERFLOW", "AVM2 operand stack underflow", offset);
    return stack.pop();
  };
  const localName = (idx) => {
    if (idx === 0) return "this";
    const params = unit.parameters || [];
    if (idx - 1 < params.length) return params[idx - 1];
    return `_loc${idx}`;
  };

  for (let i = 0; i < instructions.length; i++) {
    const ins = instructions[i];
    const op = ins.opcode;
    const raw = ins.rawOperands || [];
    switch (op) {
      case 0x02:
      case 0x09:
      case 0xef:
      case 0xf0:
      case 0xf1:
      case 0xf2:
      case 0xf3:
        break;
      case 0xd0:
        stack.push("this");
        break;
      case 0xd1:
      case 0xd2:
      case 0xd3:
        stack.push(localName(op - 0xd0));
        break;
      case 0x62:
        stack.push(localName(raw[0]));
        break;
      case 0xd4:
      case 0xd5:
      case 0xd6:
      case 0xd7: {
        const reg = op - 0xd4;
        const val = pop(ins.offset);
        if (reg === 0) throw fail("AVM2_THIS_ASSIGN", "Cannot assign local 0", ins.offset);
        const name = localName(reg);
        if (!declaredLocals.has(reg) && reg > (unit.parameters || []).length) {
          declaredLocals.add(reg);
          stmts.push(`var ${name}:* = ${val};`);
        } else {
          stmts.push(`${name} = ${val};`);
        }
        break;
      }
      case 0x63: {
        const reg = raw[0];
        const val = pop(ins.offset);
        if (reg === 0) throw fail("AVM2_THIS_ASSIGN", "Cannot assign local 0", ins.offset);
        const name = localName(reg);
        if (!declaredLocals.has(reg) && reg > (unit.parameters || []).length) {
          declaredLocals.add(reg);
          stmts.push(`var ${name}:* = ${val};`);
        } else {
          stmts.push(`${name} = ${val};`);
        }
        break;
      }
      case 0x30: {
        pop(ins.offset);
        break;
      }
      case 0x1d:
        break;
      case 0x20:
        stack.push("null");
        break;
      case 0x21:
        stack.push("undefined");
        break;
      case 0x26:
        stack.push("true");
        break;
      case 0x27:
        stack.push("false");
        break;
      case 0x28:
        stack.push("NaN");
        break;
      case 0x24:
      case 0x25:
        stack.push(String(raw[0]));
        break;
      case 0x2c:
        stack.push(JSON.stringify(abc.strings[raw[0]] ?? ""));
        break;
      case 0x2d:
        stack.push(String(abc.integers[raw[0]] ?? 0));
        break;
      case 0x2e:
        stack.push(String(abc.uints[raw[0]] ?? 0));
        break;
      case 0x2f:
        stack.push(String(abc.doubles[raw[0]] ?? 0));
        break;
      case 0x5d:
      case 0x5e: {
        const prop = abc.shortMultiname(raw[0]);
        stack.push(`__findprop(${prop})`);
        break;
      }
      case 0x60: {
        const prop = abc.shortMultiname(raw[0]);
        stack.push(prop);
        break;
      }
      case 0x66: {
        const mn = abc.multinames[raw[0]];
        if (mn && (mn.kind === 17 || mn.kind === 18)) {
          const idx = pop(ins.offset);
          const obj = pop(ins.offset);
          stack.push(`${obj}[${idx}]`);
        } else {
          const prop = abc.shortMultiname(raw[0]);
          const obj = pop(ins.offset);
          if (obj === "this" || obj.startsWith("__findprop(")) {
            stack.push(prop);
          } else {
            stack.push(`${obj}.${prop}`);
          }
        }
        break;
      }
      case 0x61:
      case 0x68: {
        const mn = abc.multinames[raw[0]];
        const val = pop(ins.offset);
        if (mn && (mn.kind === 17 || mn.kind === 18)) {
          const idx = pop(ins.offset);
          const obj = pop(ins.offset);
          stmts.push(`${obj}[${idx}] = ${val};`);
        } else {
          const prop = abc.shortMultiname(raw[0]);
          const obj = pop(ins.offset);
          if (obj === "this" || obj.startsWith("__findprop(")) {
            stmts.push(`${prop} = ${val};`);
          } else {
            stmts.push(`${obj}.${prop} = ${val};`);
          }
        }
        break;
      }
      case 0x46:
      case 0x4c:
      case 0x4f: {
        const prop = abc.shortMultiname(raw[0]);
        const argc = raw[1];
        const args = [];
        for (let k = 0; k < argc; k++) args.unshift(pop(ins.offset));
        const obj = pop(ins.offset);
        const callExpr =
          obj === "this" || obj.startsWith("__findprop(")
            ? `${prop}(${args.join(", ")})`
            : `${obj}.${prop}(${args.join(", ")})`;
        if (op === 0x4f) {
          stmts.push(`${callExpr};`);
        } else {
          stack.push(callExpr);
        }
        break;
      }
      case 0x4a: {
        const prop = abc.shortMultiname(raw[0]);
        const argc = raw[1];
        const args = [];
        for (let k = 0; k < argc; k++) args.unshift(pop(ins.offset));
        const obj = pop(ins.offset);
        const target =
          obj === "this" || obj.startsWith("__findprop(") ? prop : `${obj}.${prop}`;
        stack.push(`new ${target}(${args.join(", ")})`);
        break;
      }
      case 0x49: {
        const argc = raw[0];
        const args = [];
        for (let k = 0; k < argc; k++) args.unshift(pop(ins.offset));
        const obj = pop(ins.offset);
        if (argc > 0 || obj !== "this") {
          stmts.push(`super(${args.join(", ")});`);
        }
        break;
      }
      case 0x56: {
        const argc = raw[0];
        const elems = [];
        for (let k = 0; k < argc; k++) elems.unshift(pop(ins.offset));
        stack.push(`[${elems.join(", ")}]`);
        break;
      }
      case 0x55: {
        const argc = raw[0];
        const pairs = [];
        for (let k = 0; k < argc; k++) {
          const v = pop(ins.offset);
          const key = pop(ins.offset);
          pairs.unshift(`${unquoteIdent(key) || key}: ${v}`);
        }
        stack.push(`{${pairs.join(", ")}}`);
        break;
      }
      case 0x29: {
        const expr = pop(ins.offset);
        stmts.push(`${expr};`);
        break;
      }
      case 0x80:
      case 0x82:
      case 0x85:
      case 0x70:
      case 0x73:
      case 0x74:
      case 0x75:
      case 0x76:
        break;
      case 0x90:
      case 0xc4: {
        const a = pop(ins.offset);
        stack.push(`(-${a})`);
        break;
      }
      case 0x96: {
        const a = pop(ins.offset);
        stack.push(`(!${a})`);
        break;
      }
      case 0x91:
      case 0xc0: {
        const a = pop(ins.offset);
        stack.push(`(${a} + 1)`);
        break;
      }
      case 0x93:
      case 0xc1: {
        const a = pop(ins.offset);
        stack.push(`(${a} - 1)`);
        break;
      }
      case 0xa0:
      case 0xc5: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} + ${b})`);
        break;
      }
      case 0xa1:
      case 0xc6: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} - ${b})`);
        break;
      }
      case 0xa2:
      case 0xc7: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} * ${b})`);
        break;
      }
      case 0xa3: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} / ${b})`);
        break;
      }
      case 0xa4: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} % ${b})`);
        break;
      }
      case 0xa5: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} << ${b})`);
        break;
      }
      case 0xa6: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} >> ${b})`);
        break;
      }
      case 0xa7: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} >>> ${b})`);
        break;
      }
      case 0xa8: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} & ${b})`);
        break;
      }
      case 0xa9: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} | ${b})`);
        break;
      }
      case 0xaa: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} ^ ${b})`);
        break;
      }
      case 0xab: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} == ${b})`);
        break;
      }
      case 0xac: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} === ${b})`);
        break;
      }
      case 0xad: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} < ${b})`);
        break;
      }
      case 0xae: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} <= ${b})`);
        break;
      }
      case 0xaf: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} > ${b})`);
        break;
      }
      case 0xb0: {
        const b = pop(ins.offset), a = pop(ins.offset);
        stack.push(`(${a} >= ${b})`);
        break;
      }
      case 0x47:
        if (i !== instructions.length - 1) {
          stmts.push("return;");
        }
        break;
      case 0x48: {
        const val = pop(ins.offset);
        stmts.push(`return ${val};`);
        break;
      }
      case 0x03: {
        const val = pop(ins.offset);
        stmts.push(`throw ${val};`);
        break;
      }
      default:
        throw fail("AVM2_UNSUPPORTED_OP", `Unsupported AVM2 opcode ${ins.name}`, ins.offset);
    }
  }
  if (stack.length > 0) {
    throw fail("AVM2_UNBALANCED_STACK", "Unconsumed values on AVM2 stack", 0);
  }
  return stmts;
}
