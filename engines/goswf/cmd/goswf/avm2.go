package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"strings"
)

type Avm2Instruction struct {
	Offset   int
	Code     byte
	Name     string
	Operands string
}

type Avm2Disassembly struct {
	State            string
	Pcode            string
	Instructions     []Avm2Instruction
	InstructionTotal int
	Diagnostics      []string
}

type AbcMethod struct {
	Index      int
	Name       string
	ParamCount int
	ReturnType string
	ParamTypes []string
	ParamNames []string
	Flags      byte
}

type AbcMethodBody struct {
	MethodIndex        int
	MaxStack           uint32
	LocalCount         uint32
	InitScopeDepth     uint32
	MaxScopeDepth      uint32
	CodeOffset         int
	Code               []byte
	ExceptionCount     uint32
	TraitCount         uint32
	DecoderDiagnostics []string
}

type AbcFile struct {
	MinorVersion       uint16
	MajorVersion       uint16
	AbcName            string
	AbcHeaderOffset    int
	Integers           []int32
	UIntegers          []uint32
	Doubles            []float64
	Strings            []string
	Namespaces         []string
	Multinames         []string
	Methods            []AbcMethod
	MethodNames        map[int]string
	Bodies             []AbcMethodBody
	Diagnostics        []string
}

type abcReader struct {
	buf []byte
	pos int
}

func (r *abcReader) readU8() (byte, error) {
	if r.pos >= len(r.buf) {
		return 0, fmt.Errorf("Unexpected end of ABC buffer")
	}
	v := r.buf[r.pos]
	r.pos++
	return v, nil
}

func (r *abcReader) readU16() (uint16, error) {
	if r.pos+2 > len(r.buf) {
		return 0, fmt.Errorf("Unexpected end of ABC U16")
	}
	v := binary.LittleEndian.Uint16(r.buf[r.pos : r.pos+2])
	r.pos += 2
	return v, nil
}

func (r *abcReader) readS24() (int32, error) {
	if r.pos+3 > len(r.buf) {
		return 0, fmt.Errorf("Unexpected end of ABC S24")
	}
	b0 := int32(r.buf[r.pos])
	b1 := int32(r.buf[r.pos+1])
	b2 := int32(int8(r.buf[r.pos+2]))
	r.pos += 3
	return b0 | (b1 << 8) | (b2 << 16), nil
}

func (r *abcReader) readU30() (uint32, error) {
	var result uint32
	var shift uint
	for i := 0; i < 5; i++ {
		b, err := r.readU8()
		if err != nil {
			return 0, err
		}
		result |= uint32(b&0x7F) << shift
		if (b & 0x80) == 0 {
			return result, nil
		}
		shift += 7
	}
	return result, nil
}

func (r *abcReader) readS32() (int32, error) {
	v, err := r.readU30()
	return int32(v), err
}

func (r *abcReader) readF64() (float64, error) {
	if r.pos+8 > len(r.buf) {
		return 0, fmt.Errorf("Unexpected end of ABC F64")
	}
	bits := binary.LittleEndian.Uint64(r.buf[r.pos : r.pos+8])
	r.pos += 8
	return math.Float64frombits(bits), nil
}

func (r *abcReader) readBytes(length int) ([]byte, error) {
	if r.pos+length > len(r.buf) {
		return nil, fmt.Errorf("Unexpected end of ABC slice")
	}
	out := r.buf[r.pos : r.pos+length]
	r.pos += length
	return out, nil
}

func skipTraits(r *abcReader) (uint32, error) {
	count, err := r.readU30()
	if err != nil {
		return 0, err
	}
	for i := uint32(0); i < count; i++ {
		if _, err := r.readU30(); err != nil {
			return 0, err
		}
		kindAttr, err := r.readU8()
		if err != nil {
			return 0, err
		}
		kind := kindAttr & 0x0F
		attr := kindAttr >> 4
		switch kind {
		case 0, 6:
			if _, err := r.readU30(); err != nil {
				return 0, err
			}
			if _, err := r.readU30(); err != nil {
				return 0, err
			}
			vindex, err := r.readU30()
			if err != nil {
				return 0, err
			}
			if vindex != 0 {
				if _, err := r.readU8(); err != nil {
					return 0, err
				}
			}
		case 1, 2, 3, 4, 5:
			if _, err := r.readU30(); err != nil {
				return 0, err
			}
			if _, err := r.readU30(); err != nil {
				return 0, err
			}
		default:
			return 0, fmt.Errorf("Unknown ABC trait kind %d", kind)
		}
		if (attr & 0x04) != 0 {
			mcount, err := r.readU30()
			if err != nil {
				return 0, err
			}
			for m := uint32(0); m < mcount; m++ {
				if _, err := r.readU30(); err != nil {
					return 0, err
				}
			}
		}
	}
	return count, nil
}

func readTraits(r *abcReader, multinames []string, owner string, out map[int]string) error {
	count, err := r.readU30()
	if err != nil {
		return err
	}
	for i := uint32(0); i < count; i++ {
		nameIdx, err := r.readU30()
		if err != nil {
			return err
		}
		traitName := fmt.Sprintf("trait_%d", i)
		if int(nameIdx) < len(multinames) {
			traitName = multinames[nameIdx]
		}
		kindAttr, err := r.readU8()
		if err != nil {
			return err
		}
		kind := kindAttr & 0x0F
		attr := kindAttr >> 4
		switch kind {
		case 0, 6:
			if _, err := r.readU30(); err != nil {
				return err
			}
			if _, err := r.readU30(); err != nil {
				return err
			}
			vindex, err := r.readU30()
			if err != nil {
				return err
			}
			if vindex != 0 {
				if _, err := r.readU8(); err != nil {
					return err
				}
			}
		case 1, 2, 3:
			if _, err := r.readU30(); err != nil {
				return err
			}
			methodIdx, err := r.readU30()
			if err != nil {
				return err
			}
			suffix := ""
			if kind == 2 {
				suffix = "/get"
			} else if kind == 3 {
				suffix = "/set"
			}
			out[int(methodIdx)] = fmt.Sprintf("%s.%s%s", owner, traitName, suffix)
		case 4, 5:
			if _, err := r.readU30(); err != nil {
				return err
			}
			if _, err := r.readU30(); err != nil {
				return err
			}
		default:
			return fmt.Errorf("Unknown trait kind %d", kind)
		}
		if (attr & 0x04) != 0 {
			mcount, err := r.readU30()
			if err != nil {
				return err
			}
			for m := uint32(0); m < mcount; m++ {
				if _, err := r.readU30(); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func parseDoAbc(tagData []byte, isDefinedAbc bool) (*AbcFile, error) {
	abcOffset := 0
	abcName := ""
	if isDefinedAbc {
		if len(tagData) < 5 {
			return nil, fmt.Errorf("DoABC2 tag shorter than header")
		}
		p := 4
		for p < len(tagData) && tagData[p] != 0 {
			p++
		}
		if p >= len(tagData) {
			return nil, fmt.Errorf("Unterminated DoABC2 name")
		}
		abcName = string(bytes.ToValidUTF8(tagData[4:p], []byte("\uFFFD")))
		abcOffset = p + 1
	}

	abcBytes := tagData[abcOffset:]
	r := &abcReader{buf: abcBytes}
	minorVersion, err := r.readU16()
	if err != nil {
		return nil, err
	}
	majorVersion, err := r.readU16()
	if err != nil {
		return nil, err
	}

	intCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	integers := []int32{0}
	for i := uint32(1); i < intCount; i++ {
		v, err := r.readS32()
		if err != nil {
			return nil, err
		}
		integers = append(integers, v)
	}

	uintCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	uintegers := []uint32{0}
	for i := uint32(1); i < uintCount; i++ {
		v, err := r.readU30()
		if err != nil {
			return nil, err
		}
		uintegers = append(uintegers, v)
	}

	doubleCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	doubles := []float64{0.0}
	for i := uint32(1); i < doubleCount; i++ {
		v, err := r.readF64()
		if err != nil {
			return nil, err
		}
		doubles = append(doubles, v)
	}

	stringCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	stringsPool := []string{""}
	for i := uint32(1); i < stringCount; i++ {
		length, err := r.readU30()
		if err != nil {
			return nil, err
		}
		raw, err := r.readBytes(int(length))
		if err != nil {
			return nil, err
		}
		stringsPool = append(stringsPool, string(bytes.ToValidUTF8(raw, []byte("\uFFFD"))))
	}

	nsCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	namespaces := []string{"*"}
	for i := uint32(1); i < nsCount; i++ {
		if _, err := r.readU8(); err != nil {
			return nil, err
		}
		nameIdx, err := r.readU30()
		if err != nil {
			return nil, err
		}
		name := ""
		if int(nameIdx) < len(stringsPool) {
			name = stringsPool[nameIdx]
		}
		namespaces = append(namespaces, name)
	}

	nsSetCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	for i := uint32(1); i < nsSetCount; i++ {
		count, err := r.readU30()
		if err != nil {
			return nil, err
		}
		for j := uint32(0); j < count; j++ {
			if _, err := r.readU30(); err != nil {
				return nil, err
			}
		}
	}

	multinameCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	multinames := []string{"*"}
	for i := uint32(1); i < multinameCount; i++ {
		kind, err := r.readU8()
		if err != nil {
			return nil, err
		}
		switch kind {
		case 0x07, 0x0D:
			nsIdx, err := r.readU30()
			if err != nil {
				return nil, err
			}
			nameIdx, err := r.readU30()
			if err != nil {
				return nil, err
			}
			ns := ""
			if int(nsIdx) < len(namespaces) {
				ns = namespaces[nsIdx]
			}
			name := "*"
			if int(nameIdx) < len(stringsPool) {
				name = stringsPool[nameIdx]
			}
			if ns == "" || ns == "*" {
				multinames = append(multinames, name)
			} else {
				multinames = append(multinames, ns+"::"+name)
			}
		case 0x0F, 0x10:
			nameIdx, err := r.readU30()
			if err != nil {
				return nil, err
			}
			name := "*"
			if int(nameIdx) < len(stringsPool) {
				name = stringsPool[nameIdx]
			}
			multinames = append(multinames, name)
		case 0x11, 0x12:
			multinames = append(multinames, "<rtqname>")
		case 0x09, 0x0E:
			nameIdx, err := r.readU30()
			if err != nil {
				return nil, err
			}
			if _, err := r.readU30(); err != nil {
				return nil, err
			}
			name := "*"
			if int(nameIdx) < len(stringsPool) {
				name = stringsPool[nameIdx]
			}
			multinames = append(multinames, name)
		case 0x1B, 0x1C:
			if _, err := r.readU30(); err != nil {
				return nil, err
			}
			multinames = append(multinames, "<multinameL>")
		case 0x1D:
			baseIdx, err := r.readU30()
			if err != nil {
				return nil, err
			}
			paramCount, err := r.readU30()
			if err != nil {
				return nil, err
			}
			var params []string
			for p := uint32(0); p < paramCount; p++ {
				pIdx, err := r.readU30()
				if err != nil {
					return nil, err
				}
				pname := "*"
				if int(pIdx) < len(multinames) {
					pname = multinames[pIdx]
				}
				params = append(params, pname)
			}
			base := "Vector"
			if int(baseIdx) < len(multinames) {
				base = multinames[baseIdx]
			}
			multinames = append(multinames, fmt.Sprintf("%s.<%s>", base, strings.Join(params, ", ")))
		default:
			return nil, fmt.Errorf("Unknown ABC multiname kind 0x%02x", kind)
		}
	}

	methodCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	var methods []AbcMethod
	for i := uint32(0); i < methodCount; i++ {
		paramCount, err := r.readU30()
		if err != nil {
			return nil, err
		}
		retIdx, err := r.readU30()
		if err != nil {
			return nil, err
		}
		returnType := "*"
		if int(retIdx) < len(multinames) {
			returnType = multinames[retIdx]
		}
		var paramTypes []string
		for p := uint32(0); p < paramCount; p++ {
			tIdx, err := r.readU30()
			if err != nil {
				return nil, err
			}
			pt := "*"
			if int(tIdx) < len(multinames) {
				pt = multinames[tIdx]
			}
			paramTypes = append(paramTypes, pt)
		}
		nameIdx, err := r.readU30()
		if err != nil {
			return nil, err
		}
		name := ""
		if int(nameIdx) < len(stringsPool) {
			name = stringsPool[nameIdx]
		}
		flags, err := r.readU8()
		if err != nil {
			return nil, err
		}
		if (flags & 0x08) != 0 {
			optCount, err := r.readU30()
			if err != nil {
				return nil, err
			}
			for o := uint32(0); o < optCount; o++ {
				if _, err := r.readU30(); err != nil {
					return nil, err
				}
				if _, err := r.readU8(); err != nil {
					return nil, err
				}
			}
		}
		var paramNames []string
		if (flags & 0x80) != 0 {
			for p := uint32(0); p < paramCount; p++ {
				pnIdx, err := r.readU30()
				if err != nil {
					return nil, err
				}
				pn := ""
				if int(pnIdx) < len(stringsPool) {
					pn = stringsPool[pnIdx]
				}
				paramNames = append(paramNames, pn)
			}
		}
		methods = append(methods, AbcMethod{
			Index:      int(i),
			Name:       name,
			ParamCount: int(paramCount),
			ReturnType: returnType,
			ParamTypes: paramTypes,
			ParamNames: paramNames,
			Flags:      flags,
		})
	}

	metadataCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	for i := uint32(0); i < metadataCount; i++ {
		if _, err := r.readU30(); err != nil {
			return nil, err
		}
		itemCount, err := r.readU30()
		if err != nil {
			return nil, err
		}
		for j := uint32(0); j < itemCount; j++ {
			if _, err := r.readU30(); err != nil {
				return nil, err
			}
			if _, err := r.readU30(); err != nil {
				return nil, err
			}
		}
	}

	classCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	methodNames := make(map[int]string)
	var classNames []string
	for i := uint32(0); i < classCount; i++ {
		nameIdx, err := r.readU30()
		if err != nil {
			return nil, err
		}
		className := fmt.Sprintf("Class_%d", i)
		if int(nameIdx) < len(multinames) {
			className = multinames[nameIdx]
		}
		classNames = append(classNames, className)
		if _, err := r.readU30(); err != nil {
			return nil, err
		}
		flags, err := r.readU8()
		if err != nil {
			return nil, err
		}
		if (flags & 0x08) != 0 {
			if _, err := r.readU30(); err != nil {
				return nil, err
			}
		}
		intrfCount, err := r.readU30()
		if err != nil {
			return nil, err
		}
		for j := uint32(0); j < intrfCount; j++ {
			if _, err := r.readU30(); err != nil {
				return nil, err
			}
		}
		iinit, err := r.readU30()
		if err != nil {
			return nil, err
		}
		methodNames[int(iinit)] = className + "/instance_init"
		if err := readTraits(r, multinames, className, methodNames); err != nil {
			return nil, err
		}
	}

	for i := uint32(0); i < classCount; i++ {
		cinit, err := r.readU30()
		if err != nil {
			return nil, err
		}
		className := classNames[i]
		methodNames[int(cinit)] = className + "/class_init"
		if err := readTraits(r, multinames, className+"$", methodNames); err != nil {
			return nil, err
		}
	}

	scriptCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	for i := uint32(0); i < scriptCount; i++ {
		init, err := r.readU30()
		if err != nil {
			return nil, err
		}
		scriptName := fmt.Sprintf("script_%d", i)
		methodNames[int(init)] = scriptName + "/init"
		if err := readTraits(r, multinames, scriptName, methodNames); err != nil {
			return nil, err
		}
	}

	bodyCount, err := r.readU30()
	if err != nil {
		return nil, err
	}
	var bodies []AbcMethodBody
	var diags []string
	for i := uint32(0); i < bodyCount; i++ {
		methodIdx, err := r.readU30()
		if err != nil {
			return nil, err
		}
		maxStack, err := r.readU30()
		if err != nil {
			return nil, err
		}
		localCount, err := r.readU30()
		if err != nil {
			return nil, err
		}
		initScopeDepth, err := r.readU30()
		if err != nil {
			return nil, err
		}
		maxScopeDepth, err := r.readU30()
		if err != nil {
			return nil, err
		}
		codeLen, err := r.readU30()
		if err != nil {
			return nil, err
		}
		codeOffset := r.pos
		code, err := r.readBytes(int(codeLen))
		if err != nil {
			return nil, err
		}
		exceptionCount, err := r.readU30()
		if err != nil {
			return nil, err
		}
		for e := uint32(0); e < exceptionCount; e++ {
			for k := 0; k < 3; k++ {
				if _, err := r.readU30(); err != nil {
					return nil, err
				}
			}
			if !(majorVersion == 46 && minorVersion == 15) {
				if _, err := r.readU30(); err != nil {
					return nil, err
				}
				if _, err := r.readU30(); err != nil {
					return nil, err
				}
			}
		}
		traitCount, err := skipTraits(r)
		if err != nil {
			return nil, err
		}
		if int(methodIdx) >= len(methods) {
			diags = append(diags, fmt.Sprintf("Method body references out-of-range method %d", methodIdx))
		}
		bodies = append(bodies, AbcMethodBody{
			MethodIndex:        int(methodIdx),
			MaxStack:           maxStack,
			LocalCount:         localCount,
			InitScopeDepth:     initScopeDepth,
			MaxScopeDepth:      maxScopeDepth,
			CodeOffset:         codeOffset,
			Code:               code,
			ExceptionCount:     exceptionCount,
			TraitCount:         traitCount,
			DecoderDiagnostics: nil,
		})
	}

	return &AbcFile{
		MinorVersion:    minorVersion,
		MajorVersion:    majorVersion,
		AbcName:         abcName,
		AbcHeaderOffset: abcOffset,
		Integers:        integers,
		UIntegers:       uintegers,
		Doubles:         doubles,
		Strings:         stringsPool,
		Namespaces:      namespaces,
		Multinames:      multinames,
		Methods:         methods,
		MethodNames:     methodNames,
		Bodies:          bodies,
		Diagnostics:     diags,
	}, nil
}

func avm2OpName(op byte) string {
	switch op {
	case 0x01:
		return "bkpt"
	case 0x02:
		return "nop"
	case 0x03:
		return "throw"
	case 0x04:
		return "getsuper"
	case 0x05:
		return "setsuper"
	case 0x06:
		return "dxns"
	case 0x07:
		return "dxnslate"
	case 0x08:
		return "kill"
	case 0x09:
		return "label"
	case 0x0C:
		return "ifnlt"
	case 0x0D:
		return "ifnle"
	case 0x0E:
		return "ifngt"
	case 0x0F:
		return "ifnge"
	case 0x10:
		return "jump"
	case 0x11:
		return "iftrue"
	case 0x12:
		return "iffalse"
	case 0x13:
		return "ifeq"
	case 0x14:
		return "ifne"
	case 0x15:
		return "iflt"
	case 0x16:
		return "ifle"
	case 0x17:
		return "ifgt"
	case 0x18:
		return "ifge"
	case 0x19:
		return "ifstricteq"
	case 0x1A:
		return "ifstrictne"
	case 0x1B:
		return "lookupswitch"
	case 0x1C:
		return "pushwith"
	case 0x1D:
		return "popscope"
	case 0x1E:
		return "nextname"
	case 0x1F:
		return "hasnext"
	case 0x20:
		return "pushnull"
	case 0x21:
		return "pushundefined"
	case 0x23:
		return "nextvalue"
	case 0x24:
		return "pushbyte"
	case 0x25:
		return "pushshort"
	case 0x26:
		return "pushtrue"
	case 0x27:
		return "pushfalse"
	case 0x28:
		return "pushnan"
	case 0x29:
		return "pop"
	case 0x2A:
		return "dup"
	case 0x2B:
		return "swap"
	case 0x2C:
		return "pushstring"
	case 0x2D:
		return "pushint"
	case 0x2E:
		return "pushuint"
	case 0x2F:
		return "pushdouble"
	case 0x30:
		return "pushscope"
	case 0x31:
		return "pushnamespace"
	case 0x32:
		return "hasnext2"
	case 0x40:
		return "newfunction"
	case 0x41:
		return "call"
	case 0x42:
		return "construct"
	case 0x43:
		return "callmethod"
	case 0x44:
		return "callstatic"
	case 0x45:
		return "callsuper"
	case 0x46:
		return "callproperty"
	case 0x47:
		return "returnvoid"
	case 0x48:
		return "returnvalue"
	case 0x49:
		return "constructsuper"
	case 0x4A:
		return "constructprop"
	case 0x4C:
		return "callproplex"
	case 0x4E:
		return "callsupervoid"
	case 0x4F:
		return "callpropvoid"
	case 0x53:
		return "applytype"
	case 0x55:
		return "newobject"
	case 0x56:
		return "newarray"
	case 0x57:
		return "newactivation"
	case 0x58:
		return "newclass"
	case 0x59:
		return "getdescendants"
	case 0x5A:
		return "newcatch"
	case 0x5D:
		return "findpropstrict"
	case 0x5E:
		return "findproperty"
	case 0x5F:
		return "finddef"
	case 0x60:
		return "getlex"
	case 0x61:
		return "setproperty"
	case 0x62:
		return "getlocal"
	case 0x63:
		return "setlocal"
	case 0x64:
		return "getglobalscope"
	case 0x65:
		return "getscopeobject"
	case 0x66:
		return "getproperty"
	case 0x68:
		return "initproperty"
	case 0x6A:
		return "deleteproperty"
	case 0x6C:
		return "getslot"
	case 0x6D:
		return "setslot"
	case 0x6E:
		return "getglobalslot"
	case 0x6F:
		return "setglobalslot"
	case 0x70:
		return "convert_s"
	case 0x71:
		return "esc_xelem"
	case 0x72:
		return "esc_xattr"
	case 0x73:
		return "convert_i"
	case 0x74:
		return "convert_u"
	case 0x75:
		return "convert_d"
	case 0x76:
		return "convert_b"
	case 0x77:
		return "convert_o"
	case 0x78:
		return "checkfilter"
	case 0x80:
		return "coerce"
	case 0x82:
		return "coerce_a"
	case 0x85:
		return "coerce_s"
	case 0x86:
		return "astype"
	case 0x87:
		return "astypelate"
	case 0x89:
		return "coerce_o"
	case 0x90:
		return "negate"
	case 0x91:
		return "increment"
	case 0x92:
		return "inclocal"
	case 0x93:
		return "decrement"
	case 0x94:
		return "declocal"
	case 0x95:
		return "typeof"
	case 0x96:
		return "not"
	case 0x97:
		return "bitnot"
	case 0xA0:
		return "add"
	case 0xA1:
		return "subtract"
	case 0xA2:
		return "multiply"
	case 0xA3:
		return "divide"
	case 0xA4:
		return "modulo"
	case 0xA5:
		return "lshift"
	case 0xA6:
		return "rshift"
	case 0xA7:
		return "urshift"
	case 0xA8:
		return "bitand"
	case 0xA9:
		return "bitor"
	case 0xAA:
		return "bitxor"
	case 0xAB:
		return "equals"
	case 0xAC:
		return "strictequals"
	case 0xAD:
		return "lessthan"
	case 0xAE:
		return "lessequals"
	case 0xAF:
		return "greaterthan"
	case 0xB0:
		return "greaterequals"
	case 0xB1:
		return "instanceof"
	case 0xB2:
		return "istype"
	case 0xB3:
		return "istypelate"
	case 0xB4:
		return "in"
	case 0xC0:
		return "increment_i"
	case 0xC1:
		return "decrement_i"
	case 0xC2:
		return "inclocal_i"
	case 0xC3:
		return "declocal_i"
	case 0xC4:
		return "negate_i"
	case 0xC5:
		return "add_i"
	case 0xC6:
		return "subtract_i"
	case 0xC7:
		return "multiply_i"
	case 0xD0:
		return "getlocal_0"
	case 0xD1:
		return "getlocal_1"
	case 0xD2:
		return "getlocal_2"
	case 0xD3:
		return "getlocal_3"
	case 0xD4:
		return "setlocal_0"
	case 0xD5:
		return "setlocal_1"
	case 0xD6:
		return "setlocal_2"
	case 0xD7:
		return "setlocal_3"
	case 0xEF:
		return "debug"
	case 0xF0:
		return "debugline"
	case 0xF1:
		return "debugfile"
	default:
		return "unknown_op"
	}
}

func disassembleAvm2(abc *AbcFile, body *AbcMethodBody) Avm2Disassembly {
	r := &abcReader{buf: body.Code}
	var instructions []Avm2Instruction
	var diags []string
	diags = append(diags, body.DecoderDiagnostics...)
	state := "decoded"

	mn := func(idx uint32) string {
		if int(idx) < len(abc.Multinames) {
			return abc.Multinames[idx]
		}
		return fmt.Sprintf("mn#%d", idx)
	}
	str := func(idx uint32) string {
		s := fmt.Sprintf("str#%d", idx)
		if int(idx) < len(abc.Strings) {
			s = abc.Strings[idx]
		}
		b, _ := json.Marshal(s)
		return string(b)
	}

	for r.pos < len(body.Code) {
		offset := r.pos
		op, _ := r.readU8()
		name := avm2OpName(op)
		var operands string
		err := func() error {
			switch op {
			case 0x24:
				b, err := r.readU8()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("%d", int8(b))
			case 0x25:
				v, err := r.readS32()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("%d", v)
			case 0x2C, 0x06, 0xF1:
				idx, err := r.readU30()
				if err != nil {
					return err
				}
				operands = str(idx)
			case 0x2D:
				idx, err := r.readU30()
				if err != nil {
					return err
				}
				if int(idx) < len(abc.Integers) {
					operands = fmt.Sprintf("%d", abc.Integers[idx])
				} else {
					operands = fmt.Sprintf("int#%d", idx)
				}
			case 0x2E:
				idx, err := r.readU30()
				if err != nil {
					return err
				}
				if int(idx) < len(abc.UIntegers) {
					operands = fmt.Sprintf("%d", abc.UIntegers[idx])
				} else {
					operands = fmt.Sprintf("uint#%d", idx)
				}
			case 0x2F:
				idx, err := r.readU30()
				if err != nil {
					return err
				}
				if int(idx) < len(abc.Doubles) {
					operands = fmt.Sprintf("%g", abc.Doubles[idx])
				} else {
					operands = fmt.Sprintf("double#%d", idx)
				}
			case 0x31:
				idx, err := r.readU30()
				if err != nil {
					return err
				}
				if int(idx) < len(abc.Namespaces) {
					operands = abc.Namespaces[idx]
				} else {
					operands = fmt.Sprintf("ns#%d", idx)
				}
			case 0x04, 0x05, 0x59, 0x5D, 0x5E, 0x5F, 0x60, 0x61, 0x66, 0x68, 0x6A, 0x80, 0x86, 0xB2:
				idx, err := r.readU30()
				if err != nil {
					return err
				}
				operands = mn(idx)
			case 0x08, 0x62, 0x63, 0x92, 0x94, 0xC2, 0xC3:
				reg, err := r.readU30()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("r%d", reg)
			case 0x41, 0x42, 0x49, 0x53, 0x55, 0x56:
				argc, err := r.readU30()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("argc=%d", argc)
			case 0x40, 0x58, 0x5A, 0x65, 0x6C, 0x6D, 0x6E, 0x6F, 0xF0:
				idx, err := r.readU30()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("%d", idx)
			case 0x43, 0x44:
				id, err := r.readU30()
				if err != nil {
					return err
				}
				argc, err := r.readU30()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("method=%d argc=%d", id, argc)
			case 0x45, 0x46, 0x4A, 0x4C, 0x4E, 0x4F:
				idx, err := r.readU30()
				if err != nil {
					return err
				}
				argc, err := r.readU30()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("%s argc=%d", mn(idx), argc)
			case 0x32:
				a, err := r.readU30()
				if err != nil {
					return err
				}
				b, err := r.readU30()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("r%d r%d", a, b)
			case 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A:
				rel, err := r.readS24()
				if err != nil {
					return err
				}
				target := r.pos + int(rel)
				operands = fmt.Sprintf("%+d -> 0x%04x", rel, target)
			case 0x1B:
				defRel, err := r.readS24()
				if err != nil {
					return err
				}
				maxIndex, err := r.readU30()
				if err != nil {
					return err
				}
				var targets []string
				for i := uint32(0); i <= maxIndex; i++ {
					rel, err := r.readS24()
					if err != nil {
						return err
					}
					targets = append(targets, fmt.Sprintf("%+d", rel))
				}
				operands = fmt.Sprintf("default=%+d cases=[%s]", defRel, strings.Join(targets, ", "))
			case 0xEF:
				dtype, err := r.readU8()
				if err != nil {
					return err
				}
				nameIdx, err := r.readU30()
				if err != nil {
					return err
				}
				reg, err := r.readU8()
				if err != nil {
					return err
				}
				extra, err := r.readU30()
				if err != nil {
					return err
				}
				operands = fmt.Sprintf("type=%d name=%s r%d extra=%d", dtype, str(nameIdx), reg, extra)
			default:
				if name == "unknown_op" {
					state = "partial"
					diags = append(diags, fmt.Sprintf("Unknown AVM2 opcode 0x%02x at 0x%04x", op, offset))
					r.pos = len(body.Code)
				}
			}
			return nil
		}()

		if err != nil {
			state = "partial"
			diags = append(diags, fmt.Sprintf("%v at 0x%04x", err, offset))
			break
		}

		instructions = append(instructions, Avm2Instruction{
			Offset:   offset,
			Code:     op,
			Name:     name,
			Operands: operands,
		})
	}

	var lines []string
	for _, ins := range instructions {
		if ins.Operands == "" {
			lines = append(lines, fmt.Sprintf("%04x: %-18s (0x%02x)", ins.Offset, ins.Name, ins.Code))
		} else {
			lines = append(lines, fmt.Sprintf("%04x: %-18s %s", ins.Offset, ins.Name, ins.Operands))
		}
	}

	return Avm2Disassembly{
		State:            state,
		Pcode:            strings.Join(lines, "\n"),
		Instructions:     instructions,
		InstructionTotal: len(instructions),
		Diagnostics:      diags,
	}
}
