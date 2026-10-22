package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"strings"
)

type Avm1Instruction struct {
	Offset       int
	Code         byte
	Name         string
	Operands     string
	TargetOffset int
	FnName       string
	FnParams     []string
	FnRegisters  []int
	FnFlags      int
	FnBody       []byte
	Constants    []string
}

type Avm1Disassembly struct {
	State            string
	Pcode            string
	Instructions     []Avm1Instruction
	InstructionTotal int
	Diagnostics      []string
}

type EmbeddedSlice struct {
	Offset int
	Length int
}

type NestedAvm1Function struct {
	Id              string
	Name            string
	BodyOffset      int
	BodyLen         int
	Parameters      []string
	Registers       []int
	RegisterCount   *int
	Flags           int
	Constants       []string
	ConstantsSha256 string
}

func actionName(code byte) string {
	switch code {
	case 0x00:
		return "End"
	case 0x04:
		return "NextFrame"
	case 0x05:
		return "PrevFrame"
	case 0x06:
		return "Play"
	case 0x07:
		return "Stop"
	case 0x08:
		return "ToggleQuality"
	case 0x09:
		return "StopSounds"
	case 0x0A:
		return "Add"
	case 0x0B:
		return "Subtract"
	case 0x0C:
		return "Multiply"
	case 0x0D:
		return "Divide"
	case 0x0E:
		return "Equals"
	case 0x0F:
		return "Less"
	case 0x10:
		return "And"
	case 0x11:
		return "Or"
	case 0x12:
		return "Not"
	case 0x13:
		return "StringEquals"
	case 0x14:
		return "StringLength"
	case 0x15:
		return "StringExtract"
	case 0x17:
		return "Pop"
	case 0x18:
		return "ToInteger"
	case 0x1C:
		return "GetVariable"
	case 0x1D:
		return "SetVariable"
	case 0x20:
		return "SetTarget2"
	case 0x21:
		return "StringAdd"
	case 0x22:
		return "GetProperty"
	case 0x23:
		return "SetProperty"
	case 0x24:
		return "CloneSprite"
	case 0x25:
		return "RemoveSprite"
	case 0x26:
		return "Trace"
	case 0x27:
		return "StartDrag"
	case 0x28:
		return "EndDrag"
	case 0x29:
		return "StringLess"
	case 0x2A:
		return "Throw"
	case 0x2B:
		return "CastOp"
	case 0x2C:
		return "ImplementsOp"
	case 0x30:
		return "RandomNumber"
	case 0x31:
		return "MBStringLength"
	case 0x32:
		return "CharToAscii"
	case 0x33:
		return "AsciiToChar"
	case 0x34:
		return "GetTime"
	case 0x35:
		return "MBStringExtract"
	case 0x36:
		return "MBCharToAscii"
	case 0x37:
		return "MBAsciiToChar"
	case 0x3A:
		return "Delete"
	case 0x3B:
		return "Delete2"
	case 0x3C:
		return "DefineLocal"
	case 0x3D:
		return "CallFunction"
	case 0x3E:
		return "Return"
	case 0x3F:
		return "Modulo"
	case 0x40:
		return "NewObject"
	case 0x41:
		return "DefineLocal2"
	case 0x42:
		return "InitArray"
	case 0x43:
		return "InitObject"
	case 0x44:
		return "TypeOf"
	case 0x45:
		return "TargetPath"
	case 0x46:
		return "Enumerate"
	case 0x47:
		return "Add2"
	case 0x48:
		return "Less2"
	case 0x49:
		return "Equals2"
	case 0x4A:
		return "ToNumber"
	case 0x4B:
		return "ToString"
	case 0x4C:
		return "PushDuplicate"
	case 0x4D:
		return "StackSwap"
	case 0x4E:
		return "GetMember"
	case 0x4F:
		return "SetMember"
	case 0x50:
		return "Increment"
	case 0x51:
		return "Decrement"
	case 0x52:
		return "CallMethod"
	case 0x53:
		return "NewMethod"
	case 0x54:
		return "InstanceOf"
	case 0x55:
		return "Enumerate2"
	case 0x60:
		return "BitAnd"
	case 0x61:
		return "BitOr"
	case 0x62:
		return "BitXor"
	case 0x63:
		return "BitLShift"
	case 0x64:
		return "BitRShift"
	case 0x65:
		return "BitURShift"
	case 0x66:
		return "StrictEquals"
	case 0x67:
		return "Greater"
	case 0x68:
		return "StringGreater"
	case 0x69:
		return "Extends"
	case 0x81:
		return "GotoFrame"
	case 0x83:
		return "GetURL"
	case 0x87:
		return "StoreRegister"
	case 0x88:
		return "ConstantPool"
	case 0x8A:
		return "WaitForFrame"
	case 0x8B:
		return "SetTarget"
	case 0x8C:
		return "GotoLabel"
	case 0x8D:
		return "WaitForFrame2"
	case 0x8E:
		return "DefineFunction2"
	case 0x8F:
		return "Try"
	case 0x94:
		return "With"
	case 0x96:
		return "Push"
	case 0x99:
		return "Jump"
	case 0x9A:
		return "GetURL2"
	case 0x9B:
		return "DefineFunction"
	case 0x9D:
		return "If"
	case 0x9E:
		return "Call"
	case 0x9F:
		return "GotoFrame2"
	default:
		return "UnknownAction"
	}
}

func readCStringLossy(buf []byte, pos int) (string, int) {
	end := pos
	for end < len(buf) && buf[end] != 0 {
		end++
	}
	s := string(bytes.ToValidUTF8(buf[pos:end], []byte("\uFFFD")))
	next := end
	if end < len(buf) {
		next = end + 1
	}
	return s, next
}

func readCStringStrict(buf []byte, pos int) (string, int, bool) {
	end := pos
	for end < len(buf) && buf[end] != 0 {
		end++
	}
	if end >= len(buf) {
		return "", len(buf), false
	}
	s := string(bytes.ToValidUTF8(buf[pos:end], []byte("\uFFFD")))
	return s, end + 1, true
}

func compactJsonStringArray(items []string) []byte {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(items)
	b := buf.Bytes()
	if len(b) > 0 && b[len(b)-1] == '\n' {
		b = b[:len(b)-1]
	}
	return b
}

func decodePushOperands(payload []byte, constants []string) (string, []string) {
	pos := 0
	var items []string
	var diags []string
	for pos < len(payload) {
		kind := payload[pos]
		pos++
		switch kind {
		case 0:
			s, next := readCStringLossy(payload, pos)
			pos = next
			b, _ := json.Marshal(s)
			items = append(items, string(b))
		case 1:
			if pos+4 > len(payload) {
				diags = append(diags, "Truncated Push float32")
				pos = len(payload)
				break
			}
			v := math.Float32frombits(binary.LittleEndian.Uint32(payload[pos : pos+4]))
			pos += 4
			items = append(items, fmt.Sprintf("%g", v))
		case 2:
			items = append(items, "null")
		case 3:
			items = append(items, "undefined")
		case 4:
			if pos >= len(payload) {
				diags = append(diags, "Truncated Push register")
				break
			}
			items = append(items, fmt.Sprintf("r:%d", payload[pos]))
			pos++
		case 5:
			if pos >= len(payload) {
				diags = append(diags, "Truncated Push boolean")
				break
			}
			if payload[pos] != 0 {
				items = append(items, "true")
			} else {
				items = append(items, "false")
			}
			pos++
		case 6:
			if pos+8 > len(payload) {
				diags = append(diags, "Truncated Push float64")
				pos = len(payload)
				break
			}
			lo := binary.LittleEndian.Uint32(payload[pos : pos+4])
			hi := binary.LittleEndian.Uint32(payload[pos+4 : pos+8])
			pos += 8
			bits := (uint64(lo) << 32) | uint64(hi)
			v := math.Float64frombits(bits)
			items = append(items, fmt.Sprintf("%g", v))
		case 7:
			if pos+4 > len(payload) {
				diags = append(diags, "Truncated Push int32")
				pos = len(payload)
				break
			}
			v := int32(binary.LittleEndian.Uint32(payload[pos : pos+4]))
			pos += 4
			items = append(items, fmt.Sprintf("%d", v))
		case 8:
			if pos >= len(payload) {
				diags = append(diags, "Truncated Push constant8")
				break
			}
			idx := int(payload[pos])
			pos++
			if idx < len(constants) {
				b, _ := json.Marshal(constants[idx])
				items = append(items, fmt.Sprintf("c:%d(%s)", idx, string(b)))
			} else {
				items = append(items, fmt.Sprintf("c:%d", idx))
			}
		case 9:
			if pos+2 > len(payload) {
				diags = append(diags, "Truncated Push constant16")
				pos = len(payload)
				break
			}
			idx := int(binary.LittleEndian.Uint16(payload[pos : pos+2]))
			pos += 2
			if idx < len(constants) {
				b, _ := json.Marshal(constants[idx])
				items = append(items, fmt.Sprintf("c:%d(%s)", idx, string(b)))
			} else {
				items = append(items, fmt.Sprintf("c:%d", idx))
			}
		default:
			diags = append(diags, fmt.Sprintf("Unknown Push value type %d", kind))
			pos = len(payload)
		}
	}
	return strings.Join(items, ", "), diags
}

func disassembleAvm1(bytesSlice []byte, initialConstants []string) Avm1Disassembly {
	pos := 0
	var constants []string
	if len(initialConstants) > 0 {
		constants = append(constants, initialConstants...)
	}
	var instructions []Avm1Instruction
	var diags []string
	state := "decoded"

	for pos < len(bytesSlice) {
		offset := pos
		code := bytesSlice[pos]
		pos++
		length := 0
		if code >= 0x80 {
			if pos+2 > len(bytesSlice) {
				state = "partial"
				diags = append(diags, fmt.Sprintf("Truncated AVM1 action length at 0x%04x", offset))
				break
			}
			length = int(binary.LittleEndian.Uint16(bytesSlice[pos : pos+2]))
			pos += 2
			if pos+length > len(bytesSlice) {
				state = "partial"
				diags = append(diags, fmt.Sprintf("Truncated AVM1 action payload 0x%02x at 0x%04x", code, offset))
				break
			}
		}
		payload := bytesSlice[pos : pos+length]
		pos += length

		var operands string
		var targetOffset int
		var fnNameOut string
		var fnParamsOut []string
		var fnRegistersOut []int
		var fnFlagsOut int
		var fnBodyOut []byte
		switch code {
		case 0x88:
			if len(payload) >= 2 {
				count := int(binary.LittleEndian.Uint16(payload[0:2]))
				constants = nil
				p := 2
				for i := 0; i < count && p < len(payload); i++ {
					s, next := readCStringLossy(payload, p)
					p = next
					constants = append(constants, s)
				}
				var preview []string
				for i := 0; i < len(constants) && i < 8; i++ {
					b, _ := json.Marshal(constants[i])
					preview = append(preview, fmt.Sprintf("%d:%s", i, string(b)))
				}
				operands = fmt.Sprintf("count=%d [%s]", len(constants), strings.Join(preview, ", "))
			} else {
				state = "partial"
				operands = "truncated"
			}
		case 0x96:
			ops, d := decodePushOperands(payload, constants)
			if len(d) > 0 {
				state = "partial"
				diags = append(diags, d...)
			}
			operands = ops
		case 0x99, 0x9D:
			if len(payload) >= 2 {
				rel := int16(binary.LittleEndian.Uint16(payload[0:2]))
				target := pos + int(rel)
				targetOffset = target
				operands = fmt.Sprintf("%+d -> 0x%04x", rel, target)
			} else {
				state = "partial"
				operands = "truncated"
			}
		case 0x81:
			if len(payload) >= 2 {
				f := binary.LittleEndian.Uint16(payload[0:2])
				operands = fmt.Sprintf("frame=%d", f)
			}
		case 0x83:
			url, next := readCStringLossy(payload, 0)
			target, _ := readCStringLossy(payload, next)
			bUrl, _ := json.Marshal(url)
			bTarget, _ := json.Marshal(target)
			operands = fmt.Sprintf("url=%s target=%s", string(bUrl), string(bTarget))
		case 0x8B, 0x8C:
			s, _ := readCStringLossy(payload, 0)
			b, _ := json.Marshal(s)
			operands = string(b)
		case 0x87:
			if len(payload) >= 1 {
				operands = fmt.Sprintf("r:%d", payload[0])
			}
		case 0x9B:
			name, p := readCStringLossy(payload, 0)
			if p+2 <= len(payload) {
				paramCount := int(binary.LittleEndian.Uint16(payload[p : p+2]))
				p += 2
				var params []string
				for i := 0; i < paramCount && p < len(payload); i++ {
					s, next := readCStringLossy(payload, p)
					p = next
					params = append(params, s)
				}
				codeSize := uint16(0)
				if p+2 <= len(payload) {
					codeSize = binary.LittleEndian.Uint16(payload[p : p+2])
				}
				if pos+int(codeSize) <= len(bytesSlice) {
					fnBodyOut = bytesSlice[pos : pos+int(codeSize)]
					pos += int(codeSize)
				}
				fnNameOut = name
				fnParamsOut = params
				fnName := name
				if fnName == "" {
					fnName = "<anonymous>"
				}
				operands = fmt.Sprintf("%s(%s) body=%dB", fnName, strings.Join(params, ", "), codeSize)
			} else {
				operands = "truncated"
			}
		case 0x8E:
			name, p := readCStringLossy(payload, 0)
			if p+7 <= len(payload) {
				numParams := int(binary.LittleEndian.Uint16(payload[p : p+2]))
				regCount := payload[p+2]
				flags := binary.LittleEndian.Uint16(payload[p+3 : p+5])
				p += 5
				var params []string
				var rawParams []string
				var rawRegs []int
				for i := 0; i < numParams && p < len(payload); i++ {
					reg := payload[p]
					p++
					pname, next := readCStringLossy(payload, p)
					p = next
					params = append(params, fmt.Sprintf("r%d:%s", reg, pname))
					rawParams = append(rawParams, pname)
					rawRegs = append(rawRegs, int(reg))
				}
				codeSize := uint16(0)
				if p+2 <= len(payload) {
					codeSize = binary.LittleEndian.Uint16(payload[p : p+2])
				}
				if pos+int(codeSize) <= len(bytesSlice) {
					fnBodyOut = bytesSlice[pos : pos+int(codeSize)]
					pos += int(codeSize)
				}
				fnNameOut = name
				fnParamsOut = rawParams
				fnRegistersOut = rawRegs
				fnFlagsOut = int(flags)
				fnName := name
				if fnName == "" {
					fnName = "<anonymous>"
				}
				operands = fmt.Sprintf("%s(%s) regs=%d flags=0x%04x body=%dB", fnName, strings.Join(params, ", "), regCount, flags, codeSize)
			} else {
				operands = "truncated"
			}
		default:
			if len(payload) > 0 {
				var hexParts []string
				for _, b := range payload {
					hexParts = append(hexParts, fmt.Sprintf("%02x", b))
				}
				operands = "0x" + strings.Join(hexParts, "")
			}
		}

		name := actionName(code)
		activeConsts := make([]string, len(constants))
		copy(activeConsts, constants)
		instructions = append(instructions, Avm1Instruction{
			Offset:       offset,
			Code:         code,
			Name:         name,
			Operands:     operands,
			TargetOffset: targetOffset,
			FnName:       fnNameOut,
			FnParams:     fnParamsOut,
			FnRegisters:  fnRegistersOut,
			FnFlags:      fnFlagsOut,
			FnBody:       fnBodyOut,
			Constants:    activeConsts,
		})
		if code == 0x00 {
			break
		}
	}

	var lines []string
	for _, ins := range instructions {
		if ins.Operands == "" {
			lines = append(lines, fmt.Sprintf("%04x: %-18s (0x%02x)", ins.Offset, ins.Name, ins.Code))
		} else {
			lines = append(lines, fmt.Sprintf("%04x: %-18s %s", ins.Offset, ins.Name, ins.Operands))
		}
	}

	return Avm1Disassembly{
		State:            state,
		Pcode:            strings.Join(lines, "\n"),
		Instructions:     instructions,
		InstructionTotal: len(instructions),
		Diagnostics:      diags,
	}
}

func skipButtonRecords(data []byte, pos int, isButton2 bool) (int, bool) {
	for {
		if pos >= len(data) {
			return pos, false
		}
		flags := data[pos]
		pos++
		if flags == 0 {
			return pos, true
		}
		if pos+4 > len(data) {
			return pos, false
		}
		pos += 4
		nextPos, ok := skipMatrix(data, pos)
		if !ok {
			return pos, false
		}
		pos = nextPos
		if isButton2 {
			nextPos, ok = skipCxformWithAlpha(data, pos)
			if !ok {
				return pos, false
			}
			pos = nextPos
		}
		if (flags & 0x10) != 0 {
			nextPos, ok = skipFilterList(data, pos)
			if !ok {
				return pos, false
			}
			pos = nextPos
		}
		if (flags & 0x20) != 0 {
			if pos >= len(data) {
				return pos, false
			}
			pos++
		}
	}
}

func skipMatrix(data []byte, pos int) (int, bool) {
	br := newBitReader(data[pos:])
	hasScale, ok := br.readBits(1)
	if !ok {
		return pos, false
	}
	if hasScale == 1 {
		bits, ok := br.readBits(5)
		if !ok {
			return pos, false
		}
		if _, ok := br.readBits(int(bits) * 2); !ok {
			return pos, false
		}
	}
	hasRotate, ok := br.readBits(1)
	if !ok {
		return pos, false
	}
	if hasRotate == 1 {
		bits, ok := br.readBits(5)
		if !ok {
			return pos, false
		}
		if _, ok := br.readBits(int(bits) * 2); !ok {
			return pos, false
		}
	}
	transBits, ok := br.readBits(5)
	if !ok {
		return pos, false
	}
	if _, ok := br.readBits(int(transBits) * 2); !ok {
		return pos, false
	}
	return pos + br.bytesConsumed(), true
}

func skipCxform(data []byte, pos int, withAlpha bool) (int, bool) {
	br := newBitReader(data[pos:])
	hasAdd, ok := br.readBits(1)
	if !ok {
		return pos, false
	}
	hasMult, ok := br.readBits(1)
	if !ok {
		return pos, false
	}
	nbits, ok := br.readBits(4)
	if !ok {
		return pos, false
	}
	channels := 3
	if withAlpha {
		channels = 4
	}
	if hasMult == 1 {
		if _, ok := br.readBits(int(nbits) * channels); !ok {
			return pos, false
		}
	}
	if hasAdd == 1 {
		if _, ok := br.readBits(int(nbits) * channels); !ok {
			return pos, false
		}
	}
	return pos + br.bytesConsumed(), true
}

func skipCxformWithAlpha(data []byte, pos int) (int, bool) {
	return skipCxform(data, pos, true)
}

func skipFilterList(data []byte, pos int) (int, bool) {
	if pos >= len(data) {
		return pos, false
	}
	count := int(data[pos])
	pos++
	for i := 0; i < count; i++ {
		if pos >= len(data) {
			return pos, false
		}
		filterId := data[pos]
		pos++
		var skip int
		switch filterId {
		case 0:
			skip = 23
		case 1:
			skip = 9
		case 2:
			skip = 15
		case 3:
			skip = 27
		case 4:
			if pos >= len(data) {
				return pos, false
			}
			colors := int(data[pos])
			pos++
			skip = colors*5 + 19
		case 5:
			if pos+2 > len(data) {
				return pos, false
			}
			matrixX := int(data[pos])
			matrixY := int(data[pos+1])
			pos += 2
			skip = matrixX*matrixY*4 + 13
		case 6:
			skip = 80
		case 7:
			if pos >= len(data) {
				return pos, false
			}
			colors := int(data[pos])
			pos++
			skip = colors*5 + 27
		default:
			return pos, false
		}
		if pos+skip > len(data) {
			return pos, false
		}
		pos += skip
	}
	return pos, true
}

func extractButtonActions(data []byte) *EmbeddedSlice {
	if len(data) < 3 {
		return nil
	}
	pos, ok := skipButtonRecords(data, 2, false)
	if !ok || pos >= len(data) {
		return nil
	}
	return &EmbeddedSlice{Offset: pos, Length: len(data) - pos}
}

func extractButton2Actions(data []byte) ([]EmbeddedSlice, []string) {
	var diags []string
	var slices []EmbeddedSlice
	if len(data) < 5 {
		return slices, []string{"DefineButton2 shorter than header"}
	}
	actionOffset := int(binary.LittleEndian.Uint16(data[3:5]))
	if actionOffset == 0 {
		return slices, diags
	}
	pos := 3 + actionOffset
	if pos > len(data) {
		return slices, []string{"DefineButton2 action_offset exceeds tag payload"}
	}
	for pos < len(data) {
		if pos+4 > len(data) {
			diags = append(diags, "DefineButton2 condition header truncated")
			break
		}
		condSize := int(binary.LittleEndian.Uint16(data[pos : pos+2]))
		actionStart := pos + 4
		var next int
		if condSize == 0 {
			next = len(data)
		} else {
			next = pos + condSize
		}
		if next > len(data) || next < actionStart {
			diags = append(diags, "DefineButton2 condition size out of bounds")
			break
		}
		if next > actionStart {
			slices = append(slices, EmbeddedSlice{Offset: actionStart, Length: next - actionStart})
		}
		if condSize == 0 {
			break
		}
		pos = next
	}
	return slices, diags
}

func extractPlaceObjectActions(data []byte, swfVersion byte, isPlace3 bool) ([]EmbeddedSlice, []string) {
	var diags []string
	var slices []EmbeddedSlice
	if len(data) < 3 {
		return slices, diags
	}
	flags := data[0]
	flags2 := byte(0)
	if isPlace3 {
		if len(data) < 4 {
			return slices, []string{"PlaceObject3 shorter than header"}
		}
		flags2 = data[1]
	}
	if (flags & 0x80) == 0 {
		return slices, diags
	}
	pos := 3
	if isPlace3 {
		pos = 4
	}
	if isPlace3 && ((flags2&0x08) != 0 || ((flags2&0x10) != 0 && (flags&0x02) != 0)) {
		_, next, ok := readCStringStrict(data, pos)
		if !ok {
			return slices, []string{"PlaceObject3 class name truncated"}
		}
		pos = next
	}
	if (flags & 0x02) != 0 {
		if pos+2 > len(data) {
			return slices, []string{"PlaceObject character_id truncated"}
		}
		pos += 2
	}
	if (flags & 0x04) != 0 {
		next, ok := skipMatrix(data, pos)
		if !ok {
			return slices, []string{"PlaceObject matrix truncated"}
		}
		pos = next
	}
	if (flags & 0x08) != 0 {
		next, ok := skipCxformWithAlpha(data, pos)
		if !ok {
			return slices, []string{"PlaceObject color transform truncated"}
		}
		pos = next
	}
	if (flags & 0x10) != 0 {
		if pos+2 > len(data) {
			return slices, []string{"PlaceObject ratio truncated"}
		}
		pos += 2
	}
	if (flags & 0x20) != 0 {
		_, next, ok := readCStringStrict(data, pos)
		if !ok {
			return slices, []string{"PlaceObject name truncated"}
		}
		pos = next
	}
	if (flags & 0x40) != 0 {
		if pos+2 > len(data) {
			return slices, []string{"PlaceObject clip_depth truncated"}
		}
		pos += 2
	}
	if isPlace3 {
		if (flags2 & 0x01) != 0 {
			next, ok := skipFilterList(data, pos)
			if !ok {
				return slices, []string{"PlaceObject3 filter list truncated"}
			}
			pos = next
		}
		if (flags2 & 0x02) != 0 {
			if pos >= len(data) {
				return slices, []string{"PlaceObject3 blend_mode truncated"}
			}
			pos++
		}
		if (flags2 & 0x04) != 0 {
			if pos >= len(data) {
				return slices, []string{"PlaceObject3 bitmap_cache truncated"}
			}
			pos++
		}
		if (flags2 & 0x20) != 0 {
			if pos >= len(data) {
				return slices, []string{"PlaceObject3 visible truncated"}
			}
			pos++
		}
		if (flags2 & 0x40) != 0 {
			if pos+4 > len(data) {
				return slices, []string{"PlaceObject3 background_color truncated"}
			}
			pos += 4
		}
	}

	var eventFlagsSize int
	if swfVersion >= 6 {
		eventFlagsSize = 4
	} else {
		eventFlagsSize = 2
	}
	if pos+2+eventFlagsSize > len(data) {
		return slices, []string{"CLIPACTIONS header truncated"}
	}
	pos += 2 + eventFlagsSize
	for pos < len(data) {
		if pos+eventFlagsSize > len(data) {
			diags = append(diags, "ClipActionRecord event flags truncated")
			break
		}
		var flagsVal uint32
		if eventFlagsSize == 4 {
			flagsVal = binary.LittleEndian.Uint32(data[pos : pos+4])
		} else {
			flagsVal = uint32(binary.LittleEndian.Uint16(data[pos : pos+2]))
		}
		pos += eventFlagsSize
		if flagsVal == 0 {
			break
		}
		if pos+4 > len(data) {
			diags = append(diags, "ClipActionRecord size truncated")
			break
		}
		recordSize := int(binary.LittleEndian.Uint32(data[pos : pos+4]))
		pos += 4
		recordEnd := pos + recordSize
		if recordEnd > len(data) {
			diags = append(diags, "ClipActionRecord exceeds tag payload")
			break
		}
		actionStart := pos
		if swfVersion >= 6 && (flagsVal&0x00020000) != 0 {
			if actionStart >= recordEnd {
				diags = append(diags, "ClipEventKeyPress keyCode truncated")
				break
			}
			actionStart++
		}
		if recordEnd > actionStart {
			slices = append(slices, EmbeddedSlice{Offset: actionStart, Length: recordEnd - actionStart})
		}
		pos = recordEnd
	}
	return slices, diags
}

func extractNestedAvm1Functions(parentId string, baseByteStart int, data []byte) ([]NestedAvm1Function, []string) {
	var out []NestedAvm1Function
	var diags []string
	walkNestedAvm1(parentId, baseByteStart, data, 0, len(data), []string{}, &out, &diags, 0)
	return out, diags
}

func walkNestedAvm1(
	parentId string,
	baseByteStart int,
	data []byte,
	start int,
	end int,
	inheritedConstants []string,
	out *[]NestedAvm1Function,
	diags *[]string,
	depth int,
) {
	if depth > 32 {
		*diags = append(*diags, "AVM1 nested function recursion limit exceeded")
		return
	}
	pos := start
	activeConstants := make([]string, len(inheritedConstants))
	copy(activeConstants, inheritedConstants)

	for pos < end {
		offset := pos
		code := data[pos]
		pos++
		if code == 0x00 {
			break
		}
		length := 0
		if code >= 0x80 {
			if pos+2 > end {
				*diags = append(*diags, fmt.Sprintf("Truncated AVM1 action header at 0x%04x", offset))
				break
			}
			length = int(binary.LittleEndian.Uint16(data[pos : pos+2]))
			pos += 2
			if pos+length > end {
				*diags = append(*diags, fmt.Sprintf("Truncated AVM1 action payload 0x%02x at 0x%04x", code, offset))
				break
			}
		}
		payloadStart := pos
		payloadEnd := pos + length
		payload := data[payloadStart:payloadEnd]
		pos = payloadEnd

		if code == 0x88 && len(payload) >= 2 {
			count := int(binary.LittleEndian.Uint16(payload[0:2]))
			nextConstants := make([]string, 0, count)
			p := 2
			for i := 0; i < count && p < len(payload); i++ {
				s, next := readCStringLossy(payload, p)
				p = next
				nextConstants = append(nextConstants, s)
			}
			activeConstants = nextConstants
		} else if code == 0x9B {
			name, p, ok := readCStringStrict(payload, 0)
			if !ok || p+2 > len(payload) {
				*diags = append(*diags, fmt.Sprintf("Truncated DefineFunction header at 0x%04x", offset))
				continue
			}
			numParams := int(binary.LittleEndian.Uint16(payload[p : p+2]))
			p += 2
			params := make([]string, 0, numParams)
			valid := true
			for i := 0; i < numParams; i++ {
				if p >= len(payload) {
					valid = false
					break
				}
				pname, next, ok := readCStringStrict(payload, p)
				if !ok {
					valid = false
					break
				}
				p = next
				params = append(params, pname)
			}
			if !valid || p+2 > len(payload) {
				*diags = append(*diags, fmt.Sprintf("Truncated DefineFunction parameters at 0x%04x", offset))
				continue
			}
			codeSize := int(binary.LittleEndian.Uint16(payload[p : p+2]))
			bodyOffset := payloadEnd
			bodyEnd := bodyOffset + codeSize
			if bodyEnd > end {
				*diags = append(*diags, fmt.Sprintf("DefineFunction body at 0x%04x exceeds enclosing action block", offset))
				break
			}
			pos = bodyEnd
			constantsJson := compactJsonStringArray(activeConstants)
			sum := sha256.Sum256(constantsJson)
			fnConstants := make([]string, len(activeConstants))
			copy(fnConstants, activeConstants)
			childId := fmt.Sprintf("%s/function:%x", parentId, baseByteStart+bodyOffset)
			*out = append(*out, NestedAvm1Function{
				Id:              childId,
				Name:            name,
				BodyOffset:      bodyOffset,
				BodyLen:         codeSize,
				Parameters:      params,
				Registers:       []int{},
				RegisterCount:   nil,
				Flags:           0,
				Constants:       fnConstants,
				ConstantsSha256: hex.EncodeToString(sum[:]),
			})
			walkNestedAvm1(childId, baseByteStart, data, bodyOffset, bodyEnd, activeConstants, out, diags, depth+1)
		} else if code == 0x8E {
			name, p, ok := readCStringStrict(payload, 0)
			if !ok || p+5 > len(payload) {
				*diags = append(*diags, fmt.Sprintf("Truncated DefineFunction2 header at 0x%04x", offset))
				continue
			}
			numParams := int(binary.LittleEndian.Uint16(payload[p : p+2]))
			regCount := int(payload[p+2])
			flags := int(binary.LittleEndian.Uint16(payload[p+3 : p+5]))
			p += 5
			params := make([]string, 0, numParams)
			registers := make([]int, 0, numParams)
			valid := true
			for i := 0; i < numParams; i++ {
				if p >= len(payload) {
					valid = false
					break
				}
				reg := int(payload[p])
				p++
				pname, next, ok := readCStringStrict(payload, p)
				if !ok {
					valid = false
					break
				}
				p = next
				registers = append(registers, reg)
				params = append(params, pname)
			}
			if !valid || p+2 > len(payload) {
				*diags = append(*diags, fmt.Sprintf("Truncated DefineFunction2 parameters at 0x%04x", offset))
				continue
			}
			codeSize := int(binary.LittleEndian.Uint16(payload[p : p+2]))
			bodyOffset := payloadEnd
			bodyEnd := bodyOffset + codeSize
			if bodyEnd > end {
				*diags = append(*diags, fmt.Sprintf("DefineFunction2 body at 0x%04x exceeds enclosing action block", offset))
				break
			}
			pos = bodyEnd
			constantsJson := compactJsonStringArray(activeConstants)
			sum := sha256.Sum256(constantsJson)
			fnConstants := make([]string, len(activeConstants))
			copy(fnConstants, activeConstants)
			rcCopy := regCount
			childId := fmt.Sprintf("%s/function:%x", parentId, baseByteStart+bodyOffset)
			*out = append(*out, NestedAvm1Function{
				Id:              childId,
				Name:            name,
				BodyOffset:      bodyOffset,
				BodyLen:         codeSize,
				Parameters:      params,
				Registers:       registers,
				RegisterCount:   &rcCopy,
				Flags:           flags,
				Constants:       fnConstants,
				ConstantsSha256: hex.EncodeToString(sum[:]),
			})
			walkNestedAvm1(childId, baseByteStart, data, bodyOffset, bodyEnd, activeConstants, out, diags, depth+1)
		} else if code == 0x94 && len(payload) == 2 {
			wsize := int(binary.LittleEndian.Uint16(payload[0:2]))
			bodyOffset := payloadEnd
			bodyEnd := bodyOffset + wsize
			if bodyEnd > end {
				break
			}
			pos = bodyEnd
			walkNestedAvm1(parentId, baseByteStart, data, bodyOffset, bodyEnd, activeConstants, out, diags, depth+1)
		} else if code == 0x8F && len(payload) >= 7 {
			sizes := [3]int{
				int(binary.LittleEndian.Uint16(payload[1:3])),
				int(binary.LittleEndian.Uint16(payload[3:5])),
				int(binary.LittleEndian.Uint16(payload[5:7])),
			}
			for _, sz := range sizes {
				bOff := pos
				bEnd := bOff + sz
				if bEnd > end {
					pos = end
					break
				}
				pos = bEnd
				walkNestedAvm1(parentId, baseByteStart, data, bOff, bEnd, activeConstants, out, diags, depth+1)
			}
		}
	}
}
