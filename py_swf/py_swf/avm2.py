import struct
import math
from .avm2_opcodes import OPCODES, MNEMONICS

class ByteReader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def read_byte(self):
        if self.offset >= len(self.data):
            raise EOFError("ByteReader EOF")
        val = self.data[self.offset]
        self.offset += 1
        return val

    def read_bytes(self, n):
        if self.offset + n > len(self.data):
            raise EOFError("ByteReader EOF")
        val = self.data[self.offset : self.offset + n]
        self.offset += n
        return val

    def read_u16(self):
        return int.from_bytes(self.read_bytes(2), "little")

    def read_u32(self):
        return int.from_bytes(self.read_bytes(4), "little")

    def read_double(self):
        return struct.unpack("<d", self.read_bytes(8))[0]

    def read_u30(self):
        result = 0
        shift = 0
        while True:
            b = self.read_byte()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
            if shift >= 35:
                raise ValueError("Invalid variable-length integer")
        return result

    def read_s32(self):
        result = 0
        shift = 0
        while True:
            b = self.read_byte()
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
            if shift >= 35:
                raise ValueError("Invalid signed variable-length integer")
        result &= 0xffffffff
        if result & 0x80000000:
            return result - 0x100000000
        return result

    def read_s24(self):
        b = self.read_bytes(3)
        val = b[0] | (b[1] << 8) | (b[2] << 16)
        if val & 0x800000:
            val -= 0x1000000
        return val

class ByteWriter:
    def __init__(self):
        self.data = bytearray()

    def write_byte(self, val):
        self.data.append(val & 0xFF)

    def write_bytes(self, b):
        self.data.extend(b)

    def write_u16(self, val):
        self.data.extend(val.to_bytes(2, "little"))

    def write_u32(self, val):
        self.data.extend(val.to_bytes(4, "little"))

    def write_double(self, val):
        self.data.extend(struct.pack("<d", val))

    def write_u30(self, value):
        if not isinstance(value, int) or not 0 <= value <= 0xffffffff:
            raise ValueError("Invalid unsigned variable-length integer")
        while True:
            b = value & 0x7F
            value >>= 7
            if value > 0:
                self.write_byte(b | 0x80)
            else:
                self.write_byte(b)
                break

    def write_s32(self, value):
        if not -(2 ** 31) <= value < 2 ** 31:
            raise ValueError("Signed constant is outside 32-bit range")
        # ABC s32 uses a variable-length two's-complement word, not signed
        # LEB128. Short values such as 0x7f are positive; negatives take 5 bytes.
        self.write_u30(value & 0xffffffff)

    def write_s24(self, val):
        if not -(2 ** 23) <= val < 2 ** 23:
            raise ValueError("Branch displacement is outside signed 24-bit range")
        val = val & 0xFFFFFF
        self.write_bytes(val.to_bytes(3, "little"))

class ConstantPool:
    def __init__(self):
        self.integers = [0]
        self.uintegers = [0]
        self.doubles = [0.0]
        self.floats = [0.0]
        self.float_raw = [bytes(4)]
        self.decimal_raw = [bytes(16)]
        self.float_count_encoding = 0
        self.decimal_count_encoding = 0
        self.strings = [""]
        self.namespaces = [{"kind": 0, "name": 0}]
        self.ns_sets = [[]]
        self.multinames = [None]

class MethodInfo:
    def __init__(self):
        self.param_types = []
        self.return_type = 0
        self.name = 0
        self.flags = 0
        self.options = []
        self.param_names = []

class MetadataInfo:
    def __init__(self):
        self.name = 0
        self.items = []

class TraitsInfo:
    def __init__(self):
        self.name = 0
        self.kind_flags = 0
        self.slot_id = 0
        self.type_name = 0
        self.vindex = 0
        self.vkind = 0
        self.disp_id = 0
        self.method = 0
        self.class_idx = 0
        self.function = 0
        self.metadata = []

class InstanceInfo:
    def __init__(self):
        self.name = 0
        self.super_name = 0
        self.flags = 0
        self.protectedNs = 0
        self.interfaces = []
        self.iinit = 0
        self.traits = []

class ClassInfo:
    def __init__(self):
        self.cinit = 0
        self.traits = []

class ScriptInfo:
    def __init__(self):
        self.init = 0
        self.traits = []

class ExceptionInfo:
    def __init__(self):
        self.from_offset = 0
        self.to_offset = 0
        self.target_offset = 0
        self.exc_type = 0
        self.var_name = 0

class MethodBodyInfo:
    def __init__(self):
        self.method = 0
        self.max_stack = 0
        self.local_count = 0
        self.init_scope_depth = 0
        self.max_scope_depth = 0
        self.code = b""
        self.exceptions = []
        self.traits = []

def parse_constant_pool(reader, major=46, minor=16):
    pool = ConstantPool()
    
    int_count = reader.read_u30()
    if int_count > 0:
        for _ in range(int_count - 1):
            pool.integers.append(reader.read_s32())
            
    uint_count = reader.read_u30()
    if uint_count > 0:
        for _ in range(uint_count - 1):
            pool.uintegers.append(reader.read_u30())
            
    double_count = reader.read_u30()
    if double_count > 0:
        for _ in range(double_count - 1):
            pool.doubles.append(reader.read_double())
            
    # Extended pool layout gates verified against pinned FFDec ABC 26.3.0.
    # Numeric payloads remain raw so signaling NaN/decimal bits survive edits.
    if minor >= 17:
        count = reader.read_u30()
        pool.decimal_count_encoding = count
        pool.decimal_raw.extend(reader.read_bytes(16) for _ in range(max(0,count-1)))
    if (major,minor) >= (47,16):
        count = reader.read_u30()
        pool.float_count_encoding = count
        for _ in range(max(0,count-1)):
            raw=reader.read_bytes(4)
            pool.float_raw.append(raw)
            pool.floats.append(struct.unpack("<f",raw)[0])
    string_count = reader.read_u30()
    if string_count > 0:
        for _ in range(string_count - 1):
            utf_len = reader.read_u30()
            s_bytes = reader.read_bytes(utf_len)
            pool.strings.append(s_bytes.decode("utf-8", errors="ignore"))
            
    ns_count = reader.read_u30()
    if ns_count > 0:
        for _ in range(ns_count - 1):
            kind = reader.read_byte()
            name_idx = reader.read_u30()
            pool.namespaces.append({"kind": kind, "name": name_idx})
            
    ns_set_count = reader.read_u30()
    if ns_set_count > 0:
        for _ in range(ns_set_count - 1):
            count = reader.read_u30()
            ns_set = []
            for _ in range(count):
                ns_set.append(reader.read_u30())
            pool.ns_sets.append(ns_set)
            
    multi_count = reader.read_u30()
    if multi_count > 0:
        for _ in range(multi_count - 1):
            kind = reader.read_byte()
            multiname = {"kind": kind}
            if kind in (0x07, 0x0D):
                multiname["ns"] = reader.read_u30()
                multiname["name"] = reader.read_u30()
            elif kind in (0x0F, 0x10):
                multiname["name"] = reader.read_u30()
            elif kind in (0x11, 0x12):
                pass
            elif kind in (0x09, 0x0E):
                multiname["name"] = reader.read_u30()
                multiname["ns_set"] = reader.read_u30()
            elif kind in (0x1B, 0x1C):
                multiname["ns_set"] = reader.read_u30()
            elif kind == 0x1D:
                multiname["name"] = reader.read_u30()
                param_count = reader.read_u30()
                multiname["params"] = [reader.read_u30() for _ in range(param_count)]
            pool.multinames.append(multiname)
            
    return pool

def serialize_constant_pool(writer, pool, major=46, minor=16):
    if len(pool.integers) <= 1:
        writer.write_u30(0)
    else:
        writer.write_u30(len(pool.integers))
        for val in pool.integers[1:]:
            writer.write_s32(val)
            
    if len(pool.uintegers) <= 1:
        writer.write_u30(0)
    else:
        writer.write_u30(len(pool.uintegers))
        for val in pool.uintegers[1:]:
            writer.write_u30(val)
            
    if len(pool.doubles) <= 1:
        writer.write_u30(0)
    else:
        writer.write_u30(len(pool.doubles))
        for val in pool.doubles[1:]:
            writer.write_double(val)
            
    if minor >= 17:
        writer.write_u30(pool.decimal_count_encoding if len(pool.decimal_raw)<=1 else len(pool.decimal_raw))
        for raw in pool.decimal_raw[1:]:
            if len(raw)!=16:
                raise ValueError("Invalid raw decimal constant")
            writer.write_bytes(raw)
    if (major,minor) >= (47,16):
        if len(pool.floats)!=len(pool.float_raw):
            raise ValueError("Float-pool mutation is not supported; preserve raw constants")
        writer.write_u30(pool.float_count_encoding if len(pool.float_raw)<=1 else len(pool.float_raw))
        for value,raw in zip(pool.floats[1:],pool.float_raw[1:]):
            if len(raw)!=4:
                raise ValueError("Invalid raw float constant")
            original=struct.unpack("<f",raw)[0]
            if value!=original and not (math.isnan(value) and math.isnan(original)):
                raise ValueError("Float-pool value edits require an exact raw encoding")
            writer.write_bytes(raw)
    if len(pool.strings) <= 1:
        writer.write_u30(0)
    else:
        writer.write_u30(len(pool.strings))
        for s in pool.strings[1:]:
            s_bytes = s.encode("utf-8")
            writer.write_u30(len(s_bytes))
            writer.write_bytes(s_bytes)
            
    if len(pool.namespaces) <= 1:
        writer.write_u30(0)
    else:
        writer.write_u30(len(pool.namespaces))
        for ns in pool.namespaces[1:]:
            writer.write_byte(ns["kind"])
            writer.write_u30(ns["name"])
            
    if len(pool.ns_sets) <= 1:
        writer.write_u30(0)
    else:
        writer.write_u30(len(pool.ns_sets))
        for ns_set in pool.ns_sets[1:]:
            writer.write_u30(len(ns_set))
            for ns_idx in ns_set:
                writer.write_u30(ns_idx)
                
    if len(pool.multinames) <= 1:
        writer.write_u30(0)
    else:
        writer.write_u30(len(pool.multinames))
        for multiname in pool.multinames[1:]:
            kind = multiname["kind"]
            writer.write_byte(kind)
            if kind in (0x07, 0x0D):
                writer.write_u30(multiname["ns"])
                writer.write_u30(multiname["name"])
            elif kind in (0x0F, 0x10):
                writer.write_u30(multiname["name"])
            elif kind in (0x11, 0x12):
                pass
            elif kind in (0x09, 0x0E):
                writer.write_u30(multiname["name"])
                writer.write_u30(multiname["ns_set"])
            elif kind in (0x1B, 0x1C):
                writer.write_u30(multiname["ns_set"])
            elif kind == 0x1D:
                writer.write_u30(multiname["name"])
                writer.write_u30(len(multiname["params"]))
                for param_idx in multiname["params"]:
                    writer.write_u30(param_idx)

def parse_method_info(reader):
    m = MethodInfo()
    param_count = reader.read_u30()
    m.return_type = reader.read_u30()
    m.param_types = [reader.read_u30() for _ in range(param_count)]
    m.name = reader.read_u30()
    m.flags = reader.read_byte()
    
    if m.flags & 0x08:
        opt_count = reader.read_u30()
        for _ in range(opt_count):
            val = reader.read_u30()
            kind = reader.read_byte()
            m.options.append({"val": val, "kind": kind})
            
    if m.flags & 0x80:
        m.param_names = [reader.read_u30() for _ in range(param_count)]
        
    return m

def serialize_method_info(writer, m):
    writer.write_u30(len(m.param_types))
    writer.write_u30(m.return_type)
    for p_type in m.param_types:
        writer.write_u30(p_type)
    writer.write_u30(m.name)
    writer.write_byte(m.flags)
    
    if m.flags & 0x08:
        writer.write_u30(len(m.options))
        for opt in m.options:
            writer.write_u30(opt["val"])
            writer.write_byte(opt["kind"])
            
    if m.flags & 0x80:
        for p_name in m.param_names:
            writer.write_u30(p_name)

def parse_metadata_info(reader):
    meta = MetadataInfo()
    meta.name = reader.read_u30()
    item_count = reader.read_u30()
    for _ in range(item_count):
        k = reader.read_u30()
        v = reader.read_u30()
        meta.items.append((k, v))
    return meta

def serialize_metadata_info(writer, meta):
    writer.write_u30(meta.name)
    writer.write_u30(len(meta.items))
    for k, v in meta.items:
        writer.write_u30(k)
        writer.write_u30(v)

def parse_traits_info(reader):
    t = TraitsInfo()
    t.name = reader.read_u30()
    t.kind_flags = reader.read_byte()
    kind = t.kind_flags & 0x0F
    
    if kind in (0, 6):
        t.slot_id = reader.read_u30()
        t.type_name = reader.read_u30()
        t.vindex = reader.read_u30()
        if t.vindex != 0:
            t.vkind = reader.read_byte()
    elif kind in (1, 2, 3):
        t.disp_id = reader.read_u30()
        t.method = reader.read_u30()
    elif kind == 4:
        t.slot_id = reader.read_u30()
        t.class_idx = reader.read_u30()
    elif kind == 5:
        t.slot_id = reader.read_u30()
        t.function = reader.read_u30()
        
    if t.kind_flags & 0x40:
        meta_count = reader.read_u30()
        t.metadata = [reader.read_u30() for _ in range(meta_count)]
        
    return t

def serialize_traits_info(writer, t):
    writer.write_u30(t.name)
    writer.write_byte(t.kind_flags)
    kind = t.kind_flags & 0x0F
    
    if kind in (0, 6):
        writer.write_u30(t.slot_id)
        writer.write_u30(t.type_name)
        writer.write_u30(t.vindex)
        if t.vindex != 0:
            writer.write_byte(t.vkind)
    elif kind in (1, 2, 3):
        writer.write_u30(t.disp_id)
        writer.write_u30(t.method)
    elif kind == 4:
        writer.write_u30(t.slot_id)
        writer.write_u30(t.class_idx)
    elif kind == 5:
        writer.write_u30(t.slot_id)
        writer.write_u30(t.function)
        
    if t.kind_flags & 0x40:
        writer.write_u30(len(t.metadata))
        for m_idx in t.metadata:
            writer.write_u30(m_idx)

def parse_instance_info(reader):
    inst = InstanceInfo()
    inst.name = reader.read_u30()
    inst.super_name = reader.read_u30()
    inst.flags = reader.read_byte()
    
    if inst.flags & 0x08:
        inst.protectedNs = reader.read_u30()
        
    interface_count = reader.read_u30()
    inst.interfaces = [reader.read_u30() for _ in range(interface_count)]
    
    inst.iinit = reader.read_u30()
    
    trait_count = reader.read_u30()
    inst.traits = [parse_traits_info(reader) for _ in range(trait_count)]
    
    return inst

def serialize_instance_info(writer, inst):
    writer.write_u30(inst.name)
    writer.write_u30(inst.super_name)
    writer.write_byte(inst.flags)
    
    if inst.flags & 0x08:
        writer.write_u30(inst.protectedNs)
        
    writer.write_u30(len(inst.interfaces))
    for iface in inst.interfaces:
        writer.write_u30(iface)
        
    writer.write_u30(inst.iinit)
    
    writer.write_u30(len(inst.traits))
    for t in inst.traits:
        serialize_traits_info(writer, t)

def parse_class_info(reader):
    c = ClassInfo()
    c.cinit = reader.read_u30()
    trait_count = reader.read_u30()
    c.traits = [parse_traits_info(reader) for _ in range(trait_count)]
    return c

def serialize_class_info(writer, c):
    writer.write_u30(c.cinit)
    writer.write_u30(len(c.traits))
    for t in c.traits:
        serialize_traits_info(writer, t)

def parse_script_info(reader):
    s = ScriptInfo()
    s.init = reader.read_u30()
    trait_count = reader.read_u30()
    s.traits = [parse_traits_info(reader) for _ in range(trait_count)]
    return s

def serialize_script_info(writer, s):
    writer.write_u30(s.init)
    writer.write_u30(len(s.traits))
    for t in s.traits:
        serialize_traits_info(writer, t)

def parse_exception_info(reader):
    e = ExceptionInfo()
    e.from_offset = reader.read_u30()
    e.to_offset = reader.read_u30()
    e.target_offset = reader.read_u30()
    e.exc_type = reader.read_u30()
    e.var_name = reader.read_u30()
    return e

def serialize_exception_info(writer, e):
    writer.write_u30(e.from_offset)
    writer.write_u30(e.to_offset)
    writer.write_u30(e.target_offset)
    writer.write_u30(e.exc_type)
    writer.write_u30(e.var_name)

def parse_method_body_info(reader):
    mb = MethodBodyInfo()
    mb.method = reader.read_u30()
    mb.max_stack = reader.read_u30()
    mb.local_count = reader.read_u30()
    mb.init_scope_depth = reader.read_u30()
    mb.max_scope_depth = reader.read_u30()
    
    code_length = reader.read_u30()
    mb.code = reader.read_bytes(code_length)
    
    exc_count = reader.read_u30()
    mb.exceptions = [parse_exception_info(reader) for _ in range(exc_count)]
    
    trait_count = reader.read_u30()
    mb.traits = [parse_traits_info(reader) for _ in range(trait_count)]
    
    return mb

def serialize_method_body_info(writer, mb):
    writer.write_u30(mb.method)
    writer.write_u30(mb.max_stack)
    writer.write_u30(mb.local_count)
    writer.write_u30(mb.init_scope_depth)
    writer.write_u30(mb.max_scope_depth)
    
    writer.write_u30(len(mb.code))
    writer.write_bytes(mb.code)
    
    writer.write_u30(len(mb.exceptions))
    for e in mb.exceptions:
        serialize_exception_info(writer, e)
        
    writer.write_u30(len(mb.traits))
    for t in mb.traits:
        serialize_traits_info(writer, t)

class ABCFile:
    def __init__(self):
        self.minor_version = 16
        self.major_version = 46
        self.constant_pool = ConstantPool()
        self.methods = []
        self.metadata = []
        self.instances = []
        self.classes = []
        self.scripts = []
        self.method_bodies = []

    def parse(self, data, *, strict=False):
        reader = ByteReader(data)
        self.minor_version = reader.read_u16()
        self.major_version = reader.read_u16()
        self.constant_pool = parse_constant_pool(reader, self.major_version, self.minor_version)
        
        method_count = reader.read_u30()
        self.methods = [parse_method_info(reader) for _ in range(method_count)]
        
        metadata_count = reader.read_u30()
        self.metadata = [parse_metadata_info(reader) for _ in range(metadata_count)]
        
        instance_count = reader.read_u30()
        self.instances = [parse_instance_info(reader) for _ in range(instance_count)]
        
        self.classes = [parse_class_info(reader) for _ in range(instance_count)]
        
        script_count = reader.read_u30()
        self.scripts = [parse_script_info(reader) for _ in range(script_count)]
        
        method_body_count = reader.read_u30()
        self.method_bodies = [parse_method_body_info(reader) for _ in range(method_body_count)]
        if strict and reader.offset != len(data):
            raise ValueError("Trailing data after ABC structure")

    def serialize(self):
        writer = ByteWriter()
        writer.write_u16(self.minor_version)
        writer.write_u16(self.major_version)
        serialize_constant_pool(writer, self.constant_pool, self.major_version, self.minor_version)
        
        writer.write_u30(len(self.methods))
        for m in self.methods:
            serialize_method_info(writer, m)
            
        writer.write_u30(len(self.metadata))
        for meta in self.metadata:
            serialize_metadata_info(writer, meta)
            
        writer.write_u30(len(self.instances))
        for inst in self.instances:
            serialize_instance_info(writer, inst)
            
        for c in self.classes:
            serialize_class_info(writer, c)
            
        writer.write_u30(len(self.scripts))
        for s in self.scripts:
            serialize_script_info(writer, s)
            
        writer.write_u30(len(self.method_bodies))
        for mb in self.method_bodies:
            serialize_method_body_info(writer, mb)
            
        return bytes(writer.data)

def resolve_namespace(pool, idx):
    if idx == 0:
        return ""
    if idx >= len(pool.namespaces):
        return f"ns_{idx}"
    ns = pool.namespaces[idx]
    name_idx = ns["name"]
    return pool.strings[name_idx] if name_idx < len(pool.strings) else f"str_{name_idx}"

def resolve_multiname(pool, idx):
    if idx == 0:
        return "*"
    if idx >= len(pool.multinames):
        return f"multiname_{idx}"
    multiname = pool.multinames[idx]
    if not multiname:
        return "*"
    kind = multiname["kind"]
    if kind in (0x07, 0x0D): # QName, QNameA
        ns_idx = multiname["ns"]
        name_idx = multiname["name"]
        ns_str = resolve_namespace(pool, ns_idx)
        name_str = pool.strings[name_idx] if name_idx < len(pool.strings) else f"str_{name_idx}"
        if ns_str == "":
            return name_str
        return f"{ns_str}::{name_str}"
    elif kind in (0x09, 0x0E): # Multiname, MultinameA
        name_idx = multiname["name"]
        name_str = pool.strings[name_idx] if name_idx < len(pool.strings) else f"str_{name_idx}"
        return name_str
    if "name" in multiname:
        name_idx = multiname["name"]
        return pool.strings[name_idx] if name_idx < len(pool.strings) else f"name_{name_idx}"
    return f"multiname_{idx}"

def disassemble_instructions(pool, code, exact=False, *, exceptions=None):
    from .script_strings import quote_string
    reader = ByteReader(code)
    instructions = []
    target_pcs = set()
    for entry in exceptions or ():
        target_pcs.update((entry.from_offset, entry.to_offset, entry.target_offset))
    
    # Pass 1: decode instructions to discover target offsets for branch labels
    while reader.offset < len(code):
        pc = reader.offset
        try:
            opcode = reader.read_byte()
        except EOFError:
            break
            
        if opcode not in OPCODES:
            instructions.append((pc, opcode, b""))
            continue
            
        mnemonic, arg_types = OPCODES[opcode]
        args = []
        try:
            if mnemonic == "lookupswitch":
                default_offset = reader.read_s24()
                case_limit = reader.read_u30()
                case_offsets = [reader.read_s24() for _ in range(case_limit + 1)]
                args = [default_offset, case_limit, case_offsets]
                target_pcs.add(pc + default_offset)
                for coff in case_offsets:
                    target_pcs.add(pc + coff)
            else:
                for arg_type in arg_types:
                    if arg_type == "u30":
                        val = reader.read_u30()
                        args.append(val)
                    elif arg_type == "s24":
                        val = reader.read_s24()
                        args.append(val)
                        target_pcs.add(pc + 4 + val)
                    elif arg_type == "byte":
                        val = reader.read_byte()
                        if mnemonic == "pushbyte" and val >= 128:
                            val -= 256
                        args.append(val)
        except Exception:
            # parsing exception, record partial instruction
            pass
        instructions.append((pc, opcode, args))
        
    # Pass 2: format instructions as human-readable assembly lines
    lines = []
    for pc, opcode, args in instructions:
        if pc in target_pcs:
            lines.append(f"L_{pc}:")
            
        if opcode not in OPCODES:
            # Output unknown opcode as raw hex byte
            lines.append(f"    raw_0x{opcode:02X}")
            continue
            
        mnemonic, arg_types = OPCODES[opcode]
        formatted_args = []
        
        if mnemonic == "lookupswitch":
            default_off, case_limit, case_offs = args
            default_lbl = f"L_{pc + default_off}"
            case_lbls = [f"L_{pc + coff}" for coff in case_offs]
            formatted_args.append(default_lbl)
            formatted_args.append(str(case_limit))
            formatted_args.append("[" + ", ".join(case_lbls) + "]")
        else:
            for i, arg in enumerate(args):
                arg_type = arg_types[i]
                if arg_type == "s24":
                    target_pc = pc + 4 + arg
                    formatted_args.append(f"L_{target_pc}")
                elif arg_type == "u30":
                    # Check if opcode maps to constant pools
                    if mnemonic == "pushstring":
                        s = pool.strings[arg] if arg < len(pool.strings) else ""
                        # Escape quotes
                        formatted_args.append(quote_string(s))
                    elif mnemonic == "pushint":
                        val = pool.integers[arg] if arg < len(pool.integers) else 0
                        formatted_args.append(str(val))
                    elif mnemonic == "pushuint":
                        val = pool.uintegers[arg] if arg < len(pool.uintegers) else 0
                        formatted_args.append(str(val))
                    elif mnemonic == "pushdouble":
                        val = pool.doubles[arg] if arg < len(pool.doubles) else 0.0
                        formatted_args.append(str(val))
                    elif i == 0 and mnemonic in _MULTINAME_OPERANDS:
                        # Multinames
                        multiname_str = resolve_multiname(pool, arg)
                        formatted_args.append(multiname_str)
                    else:
                        formatted_args.append(str(arg))
                else:
                    formatted_args.append(str(arg))
                    
        comment = ""
        if exact:
            for index, kind in enumerate(arg_types):
                if kind == "u30" and index == 0 and mnemonic in _POOL_OPERANDS:
                    comment = f" // {formatted_args[index]}"
                    formatted_args[index] = f"#{args[index]}"
        args_str = " " + " ".join(formatted_args) if formatted_args else ""
        lines.append(f"    {mnemonic}{args_str}{comment}")
        
    if len(code) in target_pcs:
        lines.append(f"L_{len(code)}:")
    for entry in exceptions or ():
        lines.append(f".exception L_{entry.from_offset} L_{entry.to_offset} L_{entry.target_offset} #{entry.exc_type} #{entry.var_name}")
    return "\n".join(lines)

def search_or_add_string(pool, s):
    try:
        return pool.strings.index(s)
    except ValueError:
        pool.strings.append(s)
        return len(pool.strings) - 1

def search_or_add_multiname(pool, name_str):
    if name_str == "*":
        return 0
        
    # Split by namespace separator if present
    if "::" in name_str:
        ns_str, local_name = name_str.split("::", 1)
    else:
        ns_str, local_name = "", name_str
        
    # 1. Search or add names in string pool
    name_idx = search_or_add_string(pool, local_name)
    
    # 2. Namespace index
    ns_idx = 0
    if ns_str:
        ns_name_idx = search_or_add_string(pool, ns_str)
        # Search namespace in pool
        for idx, ns in enumerate(pool.namespaces):
            if ns and ns["kind"] == 0x16 and ns["name"] == ns_name_idx: # Internal/Public kind
                ns_idx = idx
                break
        else:
            pool.namespaces.append({"kind": 0x16, "name": ns_name_idx})
            ns_idx = len(pool.namespaces) - 1
    else:
        # Check if default public namespace exists
        for idx, ns in enumerate(pool.namespaces):
            if ns and ns["kind"] == 0x16 and ns["name"] == 0:
                ns_idx = idx
                break
        else:
            pool.namespaces.append({"kind": 0x16, "name": 0})
            ns_idx = len(pool.namespaces) - 1
            
    # 3. Search multiname in pool
    for idx, mn in enumerate(pool.multinames):
        if mn and mn["kind"] == 0x07 and mn["ns"] == ns_idx and mn["name"] == name_idx: # QName
            return idx
            
    # Add new QName multiname
    pool.multinames.append({"kind": 0x07, "ns": ns_idx, "name": name_idx})
    return len(pool.multinames) - 1

_MULTINAME_OPERANDS = {
    "getsuper", "setsuper", "getlex", "finddef", "findpropstrict", "findproperty", "getproperty",
    "setproperty", "initproperty", "deleteproperty", "coerce", "astype", "istype",
    "callproperty", "callpropvoid", "callproplex", "callsuper", "callsupervoid", "constructprop", "getdescendants",
}
_POOL_OPERANDS = {name: "multinames" for name in _MULTINAME_OPERANDS} | {
    "pushstring": "strings", "pushint": "integers", "pushuint": "uintegers",
    "pushdouble": "doubles", "pushnamespace": "namespaces", "dxns": "strings", "debugfile": "strings",
}


def assemble_instructions(pool, text, *, exception_table=None, activation_traits=None):
    regions = []
    activations = []
    from .script_strings import parse_string, strip_asm_comment
    def len_u30(val):
        if not 0 <= val <= 0xffffffff:
            raise ValueError("Invalid unsigned operand")
        c = 0
        while True:
            c += 1
            val >>= 7
            if val == 0:
                break
        return c

    lines = text.strip().split("\n")
    instructions = [] # elements will be: (label, mnemonic, args)
    
    label_defs = []
    for line in lines:
        line = strip_asm_comment(line).strip()
        # Skip empty lines or comments
        if not line or line.startswith("#") or line.startswith("//"):
            continue
            
        # Check for label
        if line.endswith(":"):
            label_defs.append(line[:-1].strip())
            continue
        if line.startswith(".exception "):
            fields = line.split()[1:]
            if len(fields) != 5:
                raise ValueError(".exception requires from, to, target, type and variable")
            regions.append(fields)
            continue
        if line.startswith(".activation "):
            fields = line.split()[1:]
            if len(fields) != 3:
                raise ValueError(".activation requires name, slot and type")
            activations.append(fields)
            continue
            
        # Parse mnemonic and args
        parts = []
        in_quotes = False
        escaped = False
        current = []
        for char in line:
            if escaped:
                current.append(char)
                escaped = False
            elif char == '\\':
                escaped = True
                current.append(char)
            elif char == '"':
                in_quotes = not in_quotes
                current.append(char)
            elif char.isspace() and not in_quotes:
                if current:
                    parts.append("".join(current))
                    current = []
            else:
                current.append(char)
        if current:
            parts.append("".join(current))
            
        if not parts:
            continue
            
        mnemonic = parts[0]
        args = parts[1:]
        
        instructions.append({
            "labels": label_defs,
            "mnemonic": mnemonic,
            "args": args
        })
        label_defs = []

    if regions and exception_table is None:
        raise ValueError("Compiled AS3 exceptions require an exception_table output; handlers cannot be discarded")
    if activations and activation_traits is None:
        raise ValueError("Compiled AS3 captures require an activation_traits output; cells cannot be discarded")

    # Pass 1: compute size of each instruction and assign exact PC values
    pc = 0
    inst_info_list = []
    label_pcs = {}
    
    for inst in instructions:
        for lbl in inst["labels"]:
            if not lbl or lbl in label_pcs:
                raise ValueError(f"Duplicate or empty label: {lbl}")
            label_pcs[lbl] = pc
            
        mnemonic = inst["mnemonic"]
        raw_args = inst["args"]
        
        if mnemonic.startswith("raw_0x"):
            # Raw byte
            opcode = int(mnemonic[6:], 16)
            size = 1
            inst_info_list.append({"pc": pc, "opcode": opcode, "args": [], "size": size, "type": "raw"})
            pc += size
            continue
            
        if mnemonic not in MNEMONICS:
            raise ValueError(f"Unknown AVM2 instruction: '{mnemonic}'")
            
        opcode, arg_types = MNEMONICS[mnemonic]
        
        # Calculate size of instruction
        inst_size = 1 # opcode byte
        parsed_args = []
        
        if mnemonic == "lookupswitch":
            # Args: default_label, case_limit, [L_1, L_2...]
            default_lbl = raw_args[0]
            case_limit = int(raw_args[1])
            # Parse list of labels
            case_lbl_str = "".join(raw_args[2:])
            case_lbls = case_lbl_str.strip("[]").split(",")
            case_lbls = [c.strip() for c in case_lbls]
            if case_limit < 0 or len(case_lbls) != case_limit + 1:
                raise ValueError("lookupswitch requires case_limit + 1 destinations")
            
            parsed_args = [default_lbl, case_limit, case_lbls]
            # default offset (s24) is 3 bytes
            inst_size += 3
            # case_limit (u30) variable length size
            inst_size += len_u30(case_limit)
            # case offsets (each s24 is 3 bytes)
            inst_size += 3 * (case_limit + 1)
            
            inst_info_list.append({"pc": pc, "opcode": opcode, "args": parsed_args, "size": inst_size, "type": "lookupswitch"})
        else:
            for i, arg_type in enumerate(arg_types):
                arg_val = raw_args[i]
                if arg_type == "s24":
                    parsed_args.append(arg_val) # stores target label string
                    inst_size += 3
                elif arg_type == "byte":
                    value = int(arg_val)
                    if not (-128 if mnemonic == "pushbyte" else 0) <= value <= 255:
                        raise ValueError("Byte operand is out of range")
                    parsed_args.append(value)
                    inst_size += 1
                elif arg_type == "u30":
                    # Constant pool resolution
                    if i == 0 and mnemonic in _POOL_OPERANDS and arg_val.startswith("#"):
                        val = int(arg_val[1:])
                        values = getattr(pool, _POOL_OPERANDS[mnemonic])
                        if not 0 <= val < len(values):
                            raise ValueError(f"Invalid {mnemonic} pool reference: {arg_val}")
                        if mnemonic in ("pushdouble", "pushstring") and val == 0:
                            raise ValueError(f"{mnemonic} cannot reference reserved pool index #0")
                    elif mnemonic == "pushstring":
                        s_val = parse_string(arg_val)
                        # Literal operands require a real string entry even
                        # for ""; QName/namespace empty-string identities keep
                        # their separate, existing index-zero semantics.
                        val = next((index for index, current in enumerate(pool.strings[1:], 1)
                                    if current == s_val), None)
                        if val is None:
                            pool.strings.append(s_val)
                            val = len(pool.strings) - 1
                    elif mnemonic == "pushint":
                        val_int = int(arg_val)
                        if not -(2 ** 31) <= val_int < 2 ** 31:
                            raise ValueError("pushint requires a signed 32-bit value")
                        try:
                            val = pool.integers.index(val_int)
                        except ValueError:
                            pool.integers.append(val_int)
                            val = len(pool.integers) - 1
                    elif mnemonic == "pushuint":
                        val_uint = int(arg_val)
                        if not 0 <= val_uint < 2 ** 32:
                            raise ValueError("pushuint requires an unsigned 32-bit value")
                        try:
                            val = pool.uintegers.index(val_uint)
                        except ValueError:
                            pool.uintegers.append(val_uint)
                            val = len(pool.uintegers) - 1
                    elif mnemonic == "pushdouble":
                        val_double = float(arg_val)
                        # Entry zero is a sentinel, not a pushdouble operand.
                        # Float equality also merges signed zeros and cannot
                        # reuse NaNs; constant identity is its IEEE-754 bits.
                        encoded_double = struct.pack("<d", val_double)
                        val = next((index for index, current in enumerate(pool.doubles[1:], 1)
                                    if struct.pack("<d", current) == encoded_double), None)
                        if val is None:
                            pool.doubles.append(val_double)
                            val = len(pool.doubles) - 1
                    elif i == 0 and mnemonic in _MULTINAME_OPERANDS:
                        val = search_or_add_multiname(pool, arg_val)
                    else:
                        val = int(arg_val)
                    parsed_args.append(val)
                    inst_size += len_u30(val)
                    
            inst_info_list.append({"pc": pc, "opcode": opcode, "args": parsed_args, "size": inst_size, "type": "standard"})
            
        pc += inst_size

    for label in label_defs:
        if not label or label in label_pcs:
            raise ValueError(f"Duplicate or empty label: {label}")
        label_pcs[label] = pc

    # Pass 2: write instructions to bytecode array, resolving label references to s24 offsets
    writer = ByteWriter()
    for inst in inst_info_list:
        pc = inst["pc"]
        opcode = inst["opcode"]
        args = inst["args"]
        inst_type = inst["type"]
        
        writer.write_byte(opcode)
        
        if inst_type == "raw":
            continue
            
        mnemonic, arg_types = OPCODES[opcode]
        
        if inst_type == "lookupswitch":
            default_lbl, case_limit, case_lbls = args
            
            # Resolve default label
            if default_lbl not in label_pcs:
                raise ValueError(f"Label '{default_lbl}' not defined")
            default_offset = label_pcs[default_lbl] - pc
            writer.write_s24(default_offset)
            
            writer.write_u30(case_limit)
            for case_lbl in case_lbls:
                if case_lbl not in label_pcs:
                    raise ValueError(f"Label '{case_lbl}' not defined")
                case_offset = label_pcs[case_lbl] - pc
                writer.write_s24(case_offset)
        else:
            for i, arg_type in enumerate(arg_types):
                arg_val = args[i]
                if arg_type == "s24":
                    # Resolve relative label jump offset
                    if arg_val not in label_pcs:
                        raise ValueError(f"Label '{arg_val}' not defined")
                    target_pc = label_pcs[arg_val]
                    # jump offset is relative to the END of jump instruction (pc + 4)
                    relative_offset = target_pc - (pc + 4)
                    writer.write_s24(relative_offset)
                elif arg_type == "byte":
                    writer.write_byte(arg_val)
                elif arg_type == "u30":
                    writer.write_u30(arg_val)
                    
    if exception_table is not None:
        exceptions = []
        for start, stop, target, type_name, var_name in regions:
            entry = ExceptionInfo()
            if any(label not in label_pcs for label in (start, stop, target)):
                raise ValueError("Undefined exception boundary/handler label")
            entry.from_offset, entry.to_offset, entry.target_offset = (label_pcs[label] for label in (start, stop, target))
            def reference(name):
                if name.startswith("#"):
                    index = int(name[1:])
                    if not 0 <= index < len(pool.multinames):
                        raise ValueError("Invalid exception multiname reference")
                    return index
                return search_or_add_multiname(pool, name)
            entry.exc_type, entry.var_name = reference(type_name), reference(var_name)
            exceptions.append(entry)
        exception_table[:] = exceptions
    if activation_traits is not None:
        traits = []
        slots = set()
        for name, slot, type_name in activations:
            if not name.startswith("#") or not type_name.startswith("#"):
                raise ValueError("Activation traits require exact multiname references")
            trait = TraitsInfo();trait.name=int(name[1:]);trait.type_name=int(type_name[1:]);trait.slot_id=int(slot);trait.kind_flags=0
            if not 0 < trait.name < len(pool.multinames) or not 0 <= trait.type_name < len(pool.multinames) or not 0 < trait.slot_id < 2**30 or trait.slot_id in slots:
                raise ValueError("Invalid or duplicate activation trait")
            slots.add(trait.slot_id);traits.append(trait)
        activation_traits[:] = traits
    return bytes(writer.data)

def build_method_mapping(abc):
    mapping = {}
    
    # 1. Script initializers
    for i, script in enumerate(abc.scripts):
        mapping[script.init] = f"Script_{i}.init"
        for trait in script.traits:
            kind = trait.kind_flags & 0x0F
            if kind in (1, 2, 3):
                name_str = resolve_multiname(abc.constant_pool, trait.name)
                mapping[trait.method] = f"Script_{i}.{name_str}"
            elif kind == 5:
                name_str = resolve_multiname(abc.constant_pool, trait.name)
                mapping[trait.function] = f"Script_{i}.{name_str}"
                
    # 2. Class/Instance initializers and traits
    for i, inst in enumerate(abc.instances):
        class_name = resolve_multiname(abc.constant_pool, inst.name)
        mapping[inst.iinit] = f"{class_name}.constructor"
        
        for trait in inst.traits:
            kind = trait.kind_flags & 0x0F
            if kind in (1, 2, 3):
                name_str = resolve_multiname(abc.constant_pool, trait.name)
                mapping[trait.method] = f"{class_name}.{name_str}"
            elif kind == 5:
                name_str = resolve_multiname(abc.constant_pool, trait.name)
                mapping[trait.function] = f"{class_name}.{name_str}"
                
        if i < len(abc.classes):
            cls = abc.classes[i]
            mapping[cls.cinit] = f"{class_name}.static_init"
            
            for trait in cls.traits:
                kind = trait.kind_flags & 0x0F
                if kind in (1, 2, 3):
                    name_str = resolve_multiname(abc.constant_pool, trait.name)
                    mapping[trait.method] = f"{class_name}.static.{name_str}"
                elif kind == 5:
                    name_str = resolve_multiname(abc.constant_pool, trait.name)
                    mapping[trait.function] = f"{class_name}.static.{name_str}"
                    
    # Fill in any missing methods
    for idx in range(len(abc.methods)):
        if idx not in mapping:
            method_info = abc.methods[idx]
            name_str = abc.constant_pool.strings[method_info.name] if method_info.name < len(abc.constant_pool.strings) else ""
            if name_str:
                mapping[idx] = f"method_{idx}_{name_str}"
            else:
                mapping[idx] = f"method_{idx}"
                
    return mapping
