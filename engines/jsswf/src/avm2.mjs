/**
 * AVM2 (ActionScript 3 ABC) parser and bytecode disassembler for JsSWF.
 */
import crypto from "node:crypto";

function sha256Hex(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

export class AbcReader {
  constructor(bytes) {
    this.bytes = bytes;
    this.pos = 0;
  }

  take(n) {
    if (n < 0 || this.pos + n > this.bytes.length) {
      throw new Error("Truncated ABC stream");
    }
    const s = this.bytes.subarray(this.pos, this.pos + n);
    this.pos += n;
    return s;
  }

  byte() {
    if (this.pos >= this.bytes.length) {
      throw new Error("Truncated ABC byte");
    }
    return this.bytes[this.pos++];
  }

  u16() {
    const s = this.take(2);
    return s.readUInt16LE(0);
  }

  s24() {
    const s = this.take(3);
    const v = s[0] | (s[1] << 8) | (s[2] << 16);
    return v & 0x800000 ? v - 0x1000000 : v;
  }

  u32var() {
    let val = 0;
    for (let shift = 0; shift < 35; shift += 7) {
      const b = this.byte();
      if (shift === 28 && (b & 0xf0) !== 0) {
        throw new Error("Invalid 32-bit ABC varint");
      }
      val = (val | ((b & 0x7f) << shift)) >>> 0;
      if ((b & 0x80) === 0) {
        return val;
      }
    }
    throw new Error("Unterminated ABC varint");
  }

  s32var() {
    let val = 0;
    let shift = 0;
    for (; shift < 35; shift += 7) {
      const b = this.byte();
      if (shift === 28 && (b & 0xf0) !== 0 && (b & 0xf0) !== 0xf0) {
        // allow sign bits
      }
      val |= (b & 0x7f) << shift;
      if ((b & 0x80) === 0) {
        if (shift < 28 && (b & 0x40) !== 0) {
          val |= ~0 << (shift + 7);
        }
        return val | 0;
      }
    }
    throw new Error("Unterminated ABC signed varint");
  }

  u30() {
    const n = this.u32var();
    if (n > 0x3fffffff) {
      throw new Error("ABC u30 exceeds 30 bits");
    }
    return n;
  }
}

export function parseAbc(bytes) {
  const r = new AbcReader(bytes);
  const minor = r.u16();
  const major = r.u16();
  if (
    !((major === 46 && (minor === 16 || minor === 17)) || (major === 47 && minor === 16))
  ) {
    throw new Error(`Unsupported ABC version ${major}.${minor}`);
  }

  const intCount = Math.max(0, r.u30() - 1);
  const integers = [0];
  for (let i = 0; i < intCount; i++) integers.push(r.s32var());

  const uintCount = Math.max(0, r.u30() - 1);
  const uints = [0];
  for (let i = 0; i < uintCount; i++) uints.push(r.u32var());

  const doubleCount = Math.max(0, r.u30() - 1);
  const doubles = [0.0];
  for (let i = 0; i < doubleCount; i++) {
    doubles.push(r.take(8).readDoubleLE(0));
  }

  if (major === 46 && minor === 17) {
    const decCount = Math.max(0, r.u30() - 1);
    r.take(decCount * 16);
  }
  if (major >= 47) {
    const floatCount = Math.max(0, r.u30() - 1);
    r.take(floatCount * 4);
  }

  const strCount = Math.max(0, r.u30() - 1);
  const strings = [""];
  for (let i = 0; i < strCount; i++) {
    const len = r.u30();
    strings.push(r.take(len).toString("utf8"));
  }

  const nsCount = Math.max(0, r.u30() - 1);
  const namespaces = [{ kind: 0, name: 0 }];
  for (let i = 0; i < nsCount; i++) {
    namespaces.push({ kind: r.byte(), name: r.u30() });
  }

  const nsSetCount = Math.max(0, r.u30() - 1);
  const nsSets = [[]];
  for (let i = 0; i < nsSetCount; i++) {
    const c = r.u30();
    const set = [];
    for (let j = 0; j < c; j++) set.push(r.u30());
    nsSets.push(set);
  }

  const mnCount = Math.max(0, r.u30() - 1);
  const multinames = [{ kind: 0, name: 0, namespace: 0, parameters: [] }];
  for (let i = 0; i < mnCount; i++) {
    const kind = r.byte();
    if (kind === 7 || kind === 13) {
      const namespace = r.u30();
      const name = r.u30();
      multinames.push({ kind, namespace, name, parameters: [] });
    } else if (kind === 15 || kind === 16) {
      multinames.push({ kind, name: r.u30(), namespace: 0, parameters: [] });
    } else if (kind === 17 || kind === 18) {
      multinames.push({ kind, name: 0, namespace: 0, parameters: [] });
    } else if (kind === 9 || kind === 14) {
      const name = r.u30();
      const nsSet = r.u30();
      multinames.push({ kind, name, nsSet, namespace: 0, parameters: [] });
    } else if (kind === 27 || kind === 28) {
      const nsSet = r.u30();
      multinames.push({ kind, name: 0, nsSet, namespace: 0, parameters: [] });
    } else if (kind === 29) {
      const base = r.u30();
      const pc = r.u30();
      const parameters = [];
      for (let j = 0; j < pc; j++) parameters.push(r.u30());
      multinames.push({ kind, name: base, namespace: 0, parameters });
    } else {
      throw new Error(`Unknown multiname kind ${kind}`);
    }
  }

  const methodCount = r.u30();
  const methods = [];
  for (let i = 0; i < methodCount; i++) {
    const paramCount = r.u30();
    const returnType = r.u30();
    const paramTypes = [];
    for (let j = 0; j < paramCount; j++) paramTypes.push(r.u30());
    const name = r.u30();
    const flags = r.byte();
    const options = [];
    if (flags & 0x08) {
      const optCount = r.u30();
      for (let j = 0; j < optCount; j++) {
        options.push({ val: r.u30(), kind: r.byte() });
      }
    }
    const paramNames = [];
    if (flags & 0x80) {
      for (let j = 0; j < paramCount; j++) paramNames.push(r.u30());
    }
    methods.push({
      return_type: returnType,
      param_types: paramTypes,
      name,
      flags,
      options,
      param_names: paramNames,
    });
  }

  const metaCount = r.u30();
  for (let i = 0; i < metaCount; i++) {
    r.u30();
    const kc = r.u30();
    for (let j = 0; j < kc; j++) {
      r.u30();
      r.u30();
    }
  }

  function readTraits() {
    const count = r.u30();
    const list = [];
    for (let i = 0; i < count; i++) {
      const name = r.u30();
      const flags = r.byte();
      const kind = flags & 0x0f;
      let method = null;
      let classIdx = null;
      if (kind === 0 || kind === 6) {
        r.u30();
        r.u30();
        const vindex = r.u30();
        if (vindex !== 0) r.byte();
      } else if (kind === 1 || kind === 2 || kind === 3 || kind === 5) {
        r.u30();
        method = r.u30();
      } else if (kind === 4) {
        r.u30();
        classIdx = r.u30();
      } else {
        throw new Error(`Unknown trait kind ${kind}`);
      }
      if (flags & 0x40) {
        const mc = r.u30();
        for (let j = 0; j < mc; j++) r.u30();
      }
      list.push({ name, kind, method, classIdx });
    }
    return list;
  }

  const classCount = r.u30();
  const instances = [];
  for (let i = 0; i < classCount; i++) {
    const name = r.u30();
    const superName = r.u30();
    const flags = r.byte();
    if (flags & 0x08) r.u30();
    const intfCount = r.u30();
    for (let j = 0; j < intfCount; j++) r.u30();
    const iinit = r.u30();
    const traits = readTraits();
    instances.push({ name, superName, flags, iinit, traits });
  }
  const classes = [];
  for (let i = 0; i < classCount; i++) {
    const cinit = r.u30();
    const traits = readTraits();
    classes.push({ cinit, traits });
  }
  const scriptCount = r.u30();
  const scripts = [];
  for (let i = 0; i < scriptCount; i++) {
    const sinit = r.u30();
    const traits = readTraits();
    scripts.push({ sinit, traits });
  }

  const bodyCount = r.u30();
  const bodies = [];
  for (let i = 0; i < bodyCount; i++) {
    const method = r.u30();
    const maxStack = r.u30();
    const localCount = r.u30();
    const initScopeDepth = r.u30();
    const maxScopeDepth = r.u30();
    const codeLen = r.u30();
    const codeOffset = r.pos;
    const code = r.take(codeLen);
    const exCount = r.u30();
    const exceptions = [];
    for (let j = 0; j < exCount; j++) {
      exceptions.push({
        from: r.u30(),
        to: r.u30(),
        target: r.u30(),
        exc_type: r.u30(),
        var_name: r.u30(),
      });
    }
    const traits = readTraits();
    bodies.push({
      method,
      max_stack: maxStack,
      local_count: localCount,
      init_scope_depth: initScopeDepth,
      max_scope_depth: maxScopeDepth,
      code,
      code_offset: codeOffset,
      exceptions,
      traits,
    });
  }

  function multinameDisplay(idx, depth = 0) {
    if (!idx || idx >= multinames.length || depth > 8) return "*";
    const m = multinames[idx];
    if (m.kind === 29) {
      const base = multinameDisplay(m.name, depth + 1);
      const params = m.parameters.map((p) => multinameDisplay(p, depth + 1)).join(", ");
      return `${base}.<${params}>`;
    }
    const nameStr = strings[m.name] || "*";
    if (m.namespace && m.namespace < namespaces.length) {
      const ns = namespaces[m.namespace];
      const nsStr = strings[ns.name] || "";
      if (nsStr && (ns.kind === 0x16 || ns.kind === 0x08)) {
        return `${nsStr}:${nameStr}`;
      }
    }
    return nameStr;
  }

  function shortMultiname(idx) {
    const full = multinameDisplay(idx);
    const colon = full.lastIndexOf(":");
    return colon >= 0 ? full.slice(colon + 1) : full;
  }

  const methodNames = new Map();
  for (let i = 0; i < instances.length; i++) {
    const inst = instances[i];
    const cls = classes[i];
    const cname = multinameDisplay(inst.name);
    methodNames.set(inst.iinit, `${cname}/instance_init`);
    methodNames.set(cls.cinit, `${cname}/class_init`);
    for (const t of inst.traits) {
      if (t.method !== null) methodNames.set(t.method, `${cname}/${shortMultiname(t.name)}`);
    }
    for (const t of cls.traits) {
      if (t.method !== null) methodNames.set(t.method, `${cname}$/${shortMultiname(t.name)}`);
    }
  }
  for (let i = 0; i < scripts.length; i++) {
    const s = scripts[i];
    if (!methodNames.has(s.sinit)) methodNames.set(s.sinit, `script_${i}/init`);
    for (const t of s.traits) {
      if (t.method !== null && !methodNames.has(t.method)) {
        methodNames.set(t.method, `script_${i}/${shortMultiname(t.name)}`);
      }
    }
  }

  return {
    raw_sha256: sha256Hex(bytes),
    major,
    minor,
    integers,
    uints,
    doubles,
    strings,
    namespaces,
    nsSets,
    multinames,
    methods,
    instances,
    classes,
    scripts,
    bodies,
    multinameDisplay,
    shortMultiname,
    methodName(idx) {
      if (methodNames.has(idx)) return methodNames.get(idx);
      const m = methods[idx];
      if (m && m.name && strings[m.name]) return strings[m.name];
      return `method_${idx}`;
    },
  };
}

// Operand types: 0 = u30, 1 = byte, 2 = s24 branch, 3 = lookupswitch, 4 = s8
const AVM2_TABLE = new Map([
  [0x01, ["bkpt", []]],
  [0x02, ["nop", []]],
  [0x03, ["throw", []]],
  [0x04, ["getsuper", [0]]],
  [0x05, ["setsuper", [0]]],
  [0x06, ["dxns", [0]]],
  [0x07, ["dxnslate", []]],
  [0x08, ["kill", [0]]],
  [0x09, ["label", []]],
  [0x0c, ["ifnlt", [2]]],
  [0x0d, ["ifnle", [2]]],
  [0x0e, ["ifngt", [2]]],
  [0x0f, ["ifnge", [2]]],
  [0x10, ["jump", [2]]],
  [0x11, ["iftrue", [2]]],
  [0x12, ["iffalse", [2]]],
  [0x13, ["ifeq", [2]]],
  [0x14, ["ifne", [2]]],
  [0x15, ["iflt", [2]]],
  [0x16, ["ifle", [2]]],
  [0x17, ["ifgt", [2]]],
  [0x18, ["ifge", [2]]],
  [0x19, ["ifstricteq", [2]]],
  [0x1a, ["ifstrictne", [2]]],
  [0x1b, ["lookupswitch", [3]]],
  [0x1c, ["pushwith", []]],
  [0x1d, ["popscope", []]],
  [0x1e, ["nextname", []]],
  [0x1f, ["hasnext", []]],
  [0x20, ["pushnull", []]],
  [0x21, ["pushundefined", []]],
  [0x22, ["pushfloat", [0]]],
  [0x23, ["nextvalue", []]],
  [0x24, ["pushbyte", [4]]],
  [0x25, ["pushshort", [0]]],
  [0x26, ["pushtrue", []]],
  [0x27, ["pushfalse", []]],
  [0x28, ["pushnan", []]],
  [0x29, ["pop", []]],
  [0x2a, ["dup", []]],
  [0x2b, ["swap", []]],
  [0x2c, ["pushstring", [0]]],
  [0x2d, ["pushint", [0]]],
  [0x2e, ["pushuint", [0]]],
  [0x2f, ["pushdouble", [0]]],
  [0x30, ["pushscope", []]],
  [0x31, ["pushnamespace", [0]]],
  [0x32, ["hasnext2", [0, 0]]],
  [0x35, ["li8", []]],
  [0x36, ["li16", []]],
  [0x37, ["li32", []]],
  [0x38, ["lf32", []]],
  [0x39, ["lf64", []]],
  [0x3a, ["si8", []]],
  [0x3b, ["si16", []]],
  [0x3c, ["si32", []]],
  [0x3d, ["sf32", []]],
  [0x3e, ["sf64", []]],
  [0x40, ["newfunction", [0]]],
  [0x41, ["call", [0]]],
  [0x42, ["construct", [0]]],
  [0x43, ["callmethod", [0, 0]]],
  [0x44, ["callstatic", [0, 0]]],
  [0x45, ["callsuper", [0, 0]]],
  [0x46, ["callproperty", [0, 0]]],
  [0x47, ["returnvoid", []]],
  [0x48, ["returnvalue", []]],
  [0x49, ["constructsuper", [0]]],
  [0x4a, ["constructprop", [0, 0]]],
  [0x4c, ["callproplex", [0, 0]]],
  [0x4e, ["callsupervoid", [0, 0]]],
  [0x4f, ["callpropvoid", [0, 0]]],
  [0x50, ["sxi1", []]],
  [0x51, ["sxi8", []]],
  [0x52, ["sxi16", []]],
  [0x53, ["applytype", [0]]],
  [0x55, ["newobject", [0]]],
  [0x56, ["newarray", [0]]],
  [0x57, ["newactivation", []]],
  [0x58, ["newclass", [0]]],
  [0x59, ["getdescendants", [0]]],
  [0x5a, ["newcatch", [0]]],
  [0x5d, ["findpropstrict", [0]]],
  [0x5e, ["findproperty", [0]]],
  [0x5f, ["finddef", [0]]],
  [0x60, ["getlex", [0]]],
  [0x61, ["setproperty", [0]]],
  [0x62, ["getlocal", [0]]],
  [0x63, ["setlocal", [0]]],
  [0x64, ["getglobalscope", []]],
  [0x65, ["getscopeobject", [1]]],
  [0x66, ["getproperty", [0]]],
  [0x67, ["getouterscope", [0]]],
  [0x68, ["initproperty", [0]]],
  [0x6a, ["deleteproperty", [0]]],
  [0x6c, ["getslot", [0]]],
  [0x6d, ["setslot", [0]]],
  [0x6e, ["getglobalslot", [0]]],
  [0x6f, ["setglobalslot", [0]]],
  [0x70, ["convert_s", []]],
  [0x71, ["esc_xelem", []]],
  [0x72, ["esc_xattr", []]],
  [0x73, ["convert_i", []]],
  [0x74, ["convert_u", []]],
  [0x75, ["convert_d", []]],
  [0x76, ["convert_b", []]],
  [0x77, ["convert_o", []]],
  [0x78, ["checkfilter", []]],
  [0x79, ["convert_f", []]],
  [0x7a, ["unplus", []]],
  [0x80, ["coerce", [0]]],
  [0x81, ["coerce_b", []]],
  [0x82, ["coerce_a", []]],
  [0x83, ["coerce_i", []]],
  [0x84, ["coerce_d", []]],
  [0x85, ["coerce_s", []]],
  [0x86, ["astype", [0]]],
  [0x87, ["astypelate", []]],
  [0x88, ["coerce_u", []]],
  [0x89, ["coerce_o", []]],
  [0x90, ["negate", []]],
  [0x91, ["increment", []]],
  [0x92, ["inclocal", [0]]],
  [0x93, ["decrement", []]],
  [0x94, ["declocal", [0]]],
  [0x95, ["typeof", []]],
  [0x96, ["not", []]],
  [0x97, ["bitnot", []]],
  [0xa0, ["add", []]],
  [0xa1, ["subtract", []]],
  [0xa2, ["multiply", []]],
  [0xa3, ["divide", []]],
  [0xa4, ["modulo", []]],
  [0xa5, ["lshift", []]],
  [0xa6, ["rshift", []]],
  [0xa7, ["urshift", []]],
  [0xa8, ["bitand", []]],
  [0xa9, ["bitor", []]],
  [0xaa, ["bitxor", []]],
  [0xab, ["equals", []]],
  [0xac, ["strictequals", []]],
  [0xad, ["lessthan", []]],
  [0xae, ["lessequals", []]],
  [0xaf, ["greaterthan", []]],
  [0xb0, ["greaterequals", []]],
  [0xb1, ["instanceof", []]],
  [0xb2, ["istype", [0]]],
  [0xb3, ["istypelate", []]],
  [0xb4, ["in", []]],
  [0xc0, ["increment_i", []]],
  [0xc1, ["decrement_i", []]],
  [0xc2, ["inclocal_i", [0]]],
  [0xc3, ["declocal_i", [0]]],
  [0xc4, ["negate_i", []]],
  [0xc5, ["add_i", []]],
  [0xc6, ["subtract_i", []]],
  [0xc7, ["multiply_i", []]],
  [0xd0, ["getlocal_0", []]],
  [0xd1, ["getlocal_1", []]],
  [0xd2, ["getlocal_2", []]],
  [0xd3, ["getlocal_3", []]],
  [0xd4, ["setlocal_0", []]],
  [0xd5, ["setlocal_1", []]],
  [0xd6, ["setlocal_2", []]],
  [0xd7, ["setlocal_3", []]],
  [0xef, ["debug", [1, 0, 1, 0]]],
  [0xf0, ["debugline", [0]]],
  [0xf1, ["debugfile", [0]]],
  [0xf2, ["bkptline", [0]]],
  [0xf3, ["timestamp", []]],
]);

export function disassembleAvm2(unit) {
  const data = unit.code;
  const abc = unit.abc;
  const r = new AbcReader(data);
  const instructions = [];
  const diagnostics = [];

  while (r.pos < data.length) {
    const start = r.pos;
    const op = r.byte();
    const spec = AVM2_TABLE.get(op);
    if (!spec) {
      diagnostics.push({
        code: "avm2_unknown_opcode",
        message: `unknown opcode 0x${op.toString(16).padStart(2, "0")}`,
        offset: start,
      });
      break;
    }
    const [name, argSpecs] = spec;
    const operands = [];
    const rawOperands = [];
    const branchTargets = [];
    try {
      for (let i = 0; i < argSpecs.length; i++) {
        const kind = argSpecs[i];
        if (kind === 0) {
          const val = r.u30();
          if (op === 0x25) {
            const signed = (val << 16) >> 16;
            rawOperands.push(signed);
            operands.push(String(signed));
          } else {
            rawOperands.push(val);
            if (abc && i === 0) {
              if (op === 0x2c || op === 0xf1 || op === 0x06) {
                const s = abc.strings[val];
                operands.push(s !== undefined ? `string[${val}]=${JSON.stringify(s)}` : `string[${val}]`);
              } else if (op === 0x2d) {
                const v = abc.integers[val];
                operands.push(v !== undefined ? `int[${val}]="${v}"` : `int[${val}]`);
              } else if (op === 0x2e) {
                const v = abc.uints[val];
                operands.push(v !== undefined ? `uint[${val}]="${v}"` : `uint[${val}]`);
              } else if (op === 0x2f) {
                const v = abc.doubles[val];
                operands.push(v !== undefined ? `double[${val}]="${v}"` : `double[${val}]`);
              } else if (
                [
                  0x04, 0x05, 0x45, 0x46, 0x4a, 0x4c, 0x4e, 0x4f, 0x59, 0x5d, 0x5e, 0x5f, 0x60,
                  0x61, 0x66, 0x68, 0x6a, 0x80, 0x86, 0xb2,
                ].includes(op)
              ) {
                const mn = abc.multinameDisplay(val);
                operands.push(`multiname[${val}]=${JSON.stringify(mn)}`);
              } else {
                operands.push(String(val));
              }
            } else {
              operands.push(String(val));
            }
          }
        } else if (kind === 1) {
          const val = r.byte();
          rawOperands.push(val);
          operands.push(String(val));
        } else if (kind === 4) {
          const b = r.byte();
          const signed = b >= 128 ? b - 256 : b;
          rawOperands.push(signed);
          operands.push(String(signed));
        } else if (kind === 2) {
          const rel = r.s24();
          const target = r.pos + rel;
          rawOperands.push(rel);
          operands.push(String(rel));
          if (target >= 0 && target <= data.length) {
            branchTargets.push(target);
          }
        } else if (kind === 3) {
          const defRel = r.s24();
          const maxIdx = r.u30();
          const defTarget = start + defRel;
          const cases = [defTarget];
          for (let c = 0; c <= maxIdx; c++) {
            const rel = r.s24();
            cases.push(start + rel);
          }
          rawOperands.push(defRel, maxIdx, ...cases);
          operands.push(String(defRel), String(maxIdx));
          branchTargets.push(...cases.filter((t) => t >= 0 && t <= data.length));
        }
      }
      instructions.push({
        offset: start,
        length: r.pos - start,
        opcode: op,
        name,
        operands,
        rawOperands,
        branchTargets,
      });
    } catch (err) {
      diagnostics.push({ code: "avm2_decode_error", message: err.message, offset: start });
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
  return { instructions, pcode, diagnostics, state };
}
