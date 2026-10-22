package main

import (
	"fmt"
	"strings"
)

type DecompileResult struct {
	UnitId      string   `json:"unit_id"`
	Language    string   `json:"language"`
	State       string   `json:"state"`
	Source      *string  `json:"source"`
	Pcode       string   `json:"pcode"`
	PcodeTotal  int      `json:"pcode_total"`
	Diagnostics []string `json:"diagnostics"`
}

func decompileAvm1(unitId string, bytesSlice []byte, initialConstants []string, paramNames []string, registers []int, fnName string, flags int) DecompileResult {
	listing := disassembleAvm1(bytesSlice, initialConstants)
	diags := append([]string{}, listing.Diagnostics...)
	if listing.State != "decoded" {
		return pcodeOnly(unitId, "avm1", listing.Pcode, diags, "Disassembly is partial; structured source omitted")
	}

	regParams := make(map[int]string)
	regIdx := 1
	if (flags & 0x01) != 0 {
		regParams[regIdx] = "this"
		regIdx++
	}
	if (flags & 0x04) != 0 {
		regParams[regIdx] = "arguments"
		regIdx++
	}
	if (flags & 0x10) != 0 {
		regParams[regIdx] = "super"
		regIdx++
	}
	if (flags & 0x40) != 0 {
		regParams[regIdx] = "_root"
		regIdx++
	}
	if (flags & 0x80) != 0 {
		regParams[regIdx] = "_parent"
		regIdx++
	}
	if (flags & 0x100) != 0 {
		regParams[regIdx] = "_global"
		regIdx++
	}
	for i := 0; i < len(paramNames) && i < len(registers); i++ {
		if registers[i] > 0 && paramNames[i] != "" {
			regParams[registers[i]] = paramNames[i]
		}
	}

	statements, errMsg := decompileAvm1Instructions(unitId, listing.Instructions, regParams, &diags)
	if errMsg != "" {
		return fallbackAvm1(unitId, listing.Pcode, diags, errMsg)
	}

	var src string
	if paramNames != nil {
		body := "    // empty action block"
		if len(statements) > 0 {
			var indented []string
			for _, s := range statements {
				indented = append(indented, "    "+s)
			}
			body = strings.Join(indented, "\n")
		}
		header := fmt.Sprintf("function(%s)", strings.Join(paramNames, ", "))
		if fnName != "" && !strings.HasPrefix(fnName, "anonymous_") && !strings.HasPrefix(fnName, "<anonymous") {
			header = fmt.Sprintf("function %s(%s)", fnName, strings.Join(paramNames, ", "))
		}
		src = fmt.Sprintf("%s {\n%s\n}\n", header, body)
	} else {
		body := "// empty action block"
		if len(statements) > 0 {
			body = strings.Join(statements, "\n")
		}
		src = fmt.Sprintf("%s\n", body)
	}

	return DecompileResult{
		UnitId:      unitId,
		Language:    "avm1",
		State:       "structured",
		Source:      &src,
		Pcode:       listing.Pcode,
		PcodeTotal:  len(listing.Pcode),
		Diagnostics: diags,
	}
}

func decompileAvm1Instructions(unitId string, instructions []Avm1Instruction, regParams map[int]string, diags *[]string) ([]string, string) {
	var stack []string
	var statements []string
	pop := func() (string, bool) {
		if len(stack) == 0 {
			return "", false
		}
		v := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		return v, true
	}

	for idx := 0; idx < len(instructions); idx++ {
		ins := instructions[idx]
		switch ins.Code {
		case 0x00:
			// End
		case 0x88:
			// ConstantPool
		case 0x96:
			for _, item := range splitOperands(ins.Operands) {
				stack = append(stack, normalizeAvm1Atom(item, regParams))
			}
		case 0x1C:
			name, ok := pop()
			if !ok {
				return nil, "Stack underflow in GetVariable"
			}
			stack = append(stack, stripStringIdentifier(name))
		case 0x1D:
			val, ok1 := pop()
			name, ok2 := pop()
			if !ok1 || !ok2 {
				return nil, "Stack underflow in SetVariable"
			}
			statements = append(statements, fmt.Sprintf("%s = %s;", stripStringIdentifier(name), val))
		case 0x3C:
			val, ok1 := pop()
			name, ok2 := pop()
			if !ok1 || !ok2 {
				return nil, "Stack underflow in DefineLocal"
			}
			statements = append(statements, fmt.Sprintf("var %s = %s;", stripStringIdentifier(name), val))
		case 0x41:
			name, ok := pop()
			if !ok {
				return nil, "Stack underflow in DefineLocal2"
			}
			statements = append(statements, fmt.Sprintf("var %s;", stripStringIdentifier(name)))
		case 0x4E:
			member, ok1 := pop()
			obj, ok2 := pop()
			if !ok1 || !ok2 {
				return nil, "Stack underflow in GetMember"
			}
			ident := stripStringIdentifier(member)
			if strings.HasPrefix(member, "\"") {
				stack = append(stack, fmt.Sprintf("%s.%s", obj, ident))
			} else {
				stack = append(stack, fmt.Sprintf("%s[%s]", obj, member))
			}
		case 0x4F:
			val, ok1 := pop()
			member, ok2 := pop()
			obj, ok3 := pop()
			if !ok1 || !ok2 || !ok3 {
				return nil, "Stack underflow in SetMember"
			}
			ident := stripStringIdentifier(member)
			if strings.HasPrefix(member, "\"") {
				statements = append(statements, fmt.Sprintf("%s.%s = %s;", obj, ident, val))
			} else {
				statements = append(statements, fmt.Sprintf("%s[%s] = %s;", obj, member, val))
			}
		case 0x26:
			val, ok := pop()
			if !ok {
				return nil, "Stack underflow in Trace"
			}
			statements = append(statements, fmt.Sprintf("trace(%s);", val))
		case 0x06:
			statements = append(statements, "play();")
		case 0x07:
			statements = append(statements, "stop();")
		case 0x04:
			statements = append(statements, "nextFrame();")
		case 0x05:
			statements = append(statements, "prevFrame();")
		case 0x09:
			statements = append(statements, "stopAllSounds();")
		case 0x3E:
			val, ok := pop()
			if !ok {
				return nil, "Stack underflow in Return"
			}
			statements = append(statements, fmt.Sprintf("return %s;", val))
		case 0x17:
			val, ok := pop()
			if !ok {
				return nil, "Stack underflow in Pop"
			}
			statements = append(statements, fmt.Sprintf("%s;", val))
		case 0x0A, 0x47, 0x21:
			if !binaryOp(&stack, "+") {
				return nil, "Stack underflow in Add"
			}
		case 0x0B:
			if !binaryOp(&stack, "-") {
				return nil, "Stack underflow in Subtract"
			}
		case 0x0C:
			if !binaryOp(&stack, "*") {
				return nil, "Stack underflow in Multiply"
			}
		case 0x0D:
			if !binaryOp(&stack, "/") {
				return nil, "Stack underflow in Divide"
			}
		case 0x3F:
			if !binaryOp(&stack, "%") {
				return nil, "Stack underflow in Modulo"
			}
		case 0x0E, 0x49, 0x13:
			if !binaryOp(&stack, "==") {
				return nil, "Stack underflow in Equals"
			}
		case 0x66:
			if !binaryOp(&stack, "===") {
				return nil, "Stack underflow in StrictEquals"
			}
		case 0x0F, 0x48, 0x29:
			if !binaryOp(&stack, "<") {
				return nil, "Stack underflow in Less"
			}
		case 0x67, 0x68:
			if !binaryOp(&stack, ">") {
				return nil, "Stack underflow in Greater"
			}
		case 0x12:
			v, ok := pop()
			if !ok {
				return nil, "Stack underflow in Not"
			}
			stack = append(stack, fmt.Sprintf("(!%s)", v))
		case 0x3D:
			name, ok1 := pop()
			countStr, ok2 := pop()
			if !ok1 || !ok2 {
				return nil, "Stack underflow in CallFunction"
			}
			var count int
			if _, err := fmt.Sscanf(countStr, "%d", &count); err != nil || count < 0 || count > 64 {
				return nil, "Non-literal CallFunction argument count"
			}
			var args []string
			for i := 0; i < count; i++ {
				a, ok := pop()
				if !ok {
					return nil, "Stack underflow in CallFunction args"
				}
				args = append(args, a)
			}
			stack = append(stack, fmt.Sprintf("%s(%s)", stripStringIdentifier(name), strings.Join(args, ", ")))
		case 0x52:
			method, ok1 := pop()
			obj, ok2 := pop()
			countStr, ok3 := pop()
			if !ok1 || !ok2 || !ok3 {
				return nil, "Stack underflow in CallMethod"
			}
			var count int
			if _, err := fmt.Sscanf(countStr, "%d", &count); err != nil || count < 0 || count > 64 {
				return nil, "Non-literal CallMethod argument count"
			}
			var args []string
			for i := 0; i < count; i++ {
				a, ok := pop()
				if !ok {
					return nil, "Stack underflow in CallMethod args"
				}
				args = append(args, a)
			}
			stack = append(stack, fmt.Sprintf("%s.%s(%s)", obj, stripStringIdentifier(method), strings.Join(args, ", ")))
		case 0x42:
			countStr, ok := pop()
			if !ok {
				return nil, "Stack underflow in InitArray"
			}
			var count int
			if _, err := fmt.Sscanf(countStr, "%d", &count); err != nil || count < 0 || count > 256 {
				return nil, "Non-literal InitArray count"
			}
			var elems []string
			for i := 0; i < count; i++ {
				e, ok := pop()
				if !ok {
					return nil, "Stack underflow in InitArray elements"
				}
				elems = append(elems, e)
			}
			stack = append(stack, fmt.Sprintf("[%s]", strings.Join(elems, ", ")))
		case 0x43:
			countStr, ok := pop()
			if !ok {
				return nil, "Stack underflow in InitObject"
			}
			var count int
			if _, err := fmt.Sscanf(countStr, "%d", &count); err != nil || count < 0 || count > 256 {
				return nil, "Non-literal InitObject count"
			}
			var pairs []string
			for i := 0; i < count; i++ {
				v, ok1 := pop()
				k, ok2 := pop()
				if !ok1 || !ok2 {
					return nil, "Stack underflow in InitObject pairs"
				}
				pairs = append(pairs, fmt.Sprintf("%s: %s", stripStringIdentifier(k), v))
			}
			stack = append(stack, fmt.Sprintf("{%s}", strings.Join(pairs, ", ")))
		case 0x87:
			val, ok := pop()
			if !ok {
				return nil, "Stack underflow in StoreRegister"
			}
			regName := strings.TrimSpace(ins.Operands)
			if r, ok := regParams[func() int { var n int; fmt.Sscanf(regName, "r:%d", &n); return n }()]; ok {
				regName = r
			}
			statements = append(statements, fmt.Sprintf("%s = %s;", regName, val))
			stack = append(stack, regName)
		case 0x4C:
			val, ok := pop()
			if !ok {
				return nil, "Stack underflow in PushDuplicate"
			}
			stack = append(stack, val, val)
		case 0x4D:
			a, ok1 := pop()
			b, ok2 := pop()
			if !ok1 || !ok2 {
				return nil, "Stack underflow in StackSwap"
			}
			stack = append(stack, a, b)
		case 0x50:
			v, ok := pop()
			if !ok {
				return nil, "Stack underflow in Increment"
			}
			stack = append(stack, fmt.Sprintf("(%s + 1)", v))
		case 0x51:
			v, ok := pop()
			if !ok {
				return nil, "Stack underflow in Decrement"
			}
			stack = append(stack, fmt.Sprintf("(%s - 1)", v))
		case 0x18:
			v, ok := pop()
			if !ok {
				return nil, "Stack underflow in ToInteger"
			}
			stack = append(stack, fmt.Sprintf("int(%s)", v))
		case 0x4A:
			v, ok := pop()
			if !ok {
				return nil, "Stack underflow in ToNumber"
			}
			stack = append(stack, fmt.Sprintf("Number(%s)", v))
		case 0x4B:
			v, ok := pop()
			if !ok {
				return nil, "Stack underflow in ToString"
			}
			stack = append(stack, fmt.Sprintf("String(%s)", v))
		case 0x60:
			if !binaryOp(&stack, "&") {
				return nil, "Stack underflow in BitAnd"
			}
		case 0x61:
			if !binaryOp(&stack, "|") {
				return nil, "Stack underflow in BitOr"
			}
		case 0x62:
			if !binaryOp(&stack, "^") {
				return nil, "Stack underflow in BitXor"
			}
		case 0x63:
			if !binaryOp(&stack, "<<") {
				return nil, "Stack underflow in BitLShift"
			}
		case 0x64:
			if !binaryOp(&stack, ">>") {
				return nil, "Stack underflow in BitRShift"
			}
		case 0x65:
			if !binaryOp(&stack, ">>>") {
				return nil, "Stack underflow in BitURShift"
			}
		case 0x40:
			ctor, ok1 := pop()
			countStr, ok2 := pop()
			if !ok1 || !ok2 {
				return nil, "Stack underflow in NewObject"
			}
			var count int
			if _, err := fmt.Sscanf(countStr, "%d", &count); err != nil || count < 0 || count > 64 {
				return nil, "Non-literal NewObject argument count"
			}
			var args []string
			for i := 0; i < count; i++ {
				a, ok := pop()
				if !ok {
					return nil, "Stack underflow in NewObject args"
				}
				args = append(args, a)
			}
			stack = append(stack, fmt.Sprintf("new %s(%s)", stripStringIdentifier(ctor), strings.Join(args, ", ")))
		case 0x9B, 0x8E:
			if ins.FnBody != nil {
				params := ins.FnParams
				if params == nil {
					params = []string{}
				}
				sub := decompileAvm1(unitId+"/fn", ins.FnBody, ins.Constants, params, ins.FnRegisters, ins.FnName, ins.FnFlags)
				if sub.Source != nil {
					fnSrc := strings.TrimSpace(*sub.Source)
					if ins.FnName != "" {
						statements = append(statements, fnSrc)
					} else {
						stack = append(stack, fnSrc)
					}
					break
				}
			}
			stack = append(stack, "function() { /* nested function */ }")
		case 0x9D:
			cond, ok := pop()
			if !ok {
				return nil, "Stack underflow in If"
			}
			negCond := fmt.Sprintf("(!%s)", cond)
			if strings.HasPrefix(cond, "(!") && strings.HasSuffix(cond, ")") {
				negCond = cond[2 : len(cond)-1]
			}
			target := ins.TargetOffset
			if target > ins.Offset {
				targetIdx := len(instructions)
				for k := idx + 1; k < len(instructions); k++ {
					if instructions[k].Offset >= target {
						targetIdx = k
						break
					}
				}
				prevIdx := targetIdx - 1
				if prevIdx > idx && instructions[prevIdx].Code == 0x99 && instructions[prevIdx].TargetOffset <= ins.Offset {
					bodyStmts, errMsg := decompileAvm1Instructions(unitId, instructions[idx+1:prevIdx], regParams, diags)
					if errMsg != "" {
						return nil, errMsg
					}
					var indented []string
					for _, s := range bodyStmts {
						indented = append(indented, "    "+s)
					}
					statements = append(statements, fmt.Sprintf("while (%s) {\n%s\n}", negCond, strings.Join(indented, "\n")))
					idx = prevIdx
					break
				}
				bodyStmts, errMsg := decompileAvm1Instructions(unitId, instructions[idx+1:targetIdx], regParams, diags)
				if errMsg != "" {
					return nil, errMsg
				}
				var indented []string
				for _, s := range bodyStmts {
					indented = append(indented, "    "+s)
				}
				statements = append(statements, fmt.Sprintf("if (%s) {\n%s\n}", negCond, strings.Join(indented, "\n")))
				idx = targetIdx - 1
				break
			}
			statements = append(statements, fmt.Sprintf("if (%s) { /* branch %s */ }", cond, ins.Operands))
		case 0x99:
			// Jump handled by structured while/if
		default:
			return nil, fmt.Sprintf("Unsupported AVM1 action %s (0x%02x) for structured emission", ins.Name, ins.Code)
		}
	}

	if len(stack) > 0 {
		*diags = append(*diags, fmt.Sprintf("%d leftover stack value(s) emitted as comments", len(stack)))
		for _, v := range stack {
			statements = append(statements, fmt.Sprintf("/* stack: %s */", v))
		}
	}
	return statements, ""
}

func decompileAvm2(unitId string, abc *AbcFile, body *AbcMethodBody, displayName string) DecompileResult {
	listing := disassembleAvm2(abc, body)
	diags := append([]string{}, listing.Diagnostics...)
	if listing.State != "decoded" {
		return pcodeOnly(unitId, "avm2", listing.Pcode, diags, "AVM2 disassembly is partial; structured source omitted")
	}

	var stack []string
	var statements []string
	pop := func() (string, bool) {
		if len(stack) == 0 {
			return "", false
		}
		v := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		return v, true
	}

	for _, ins := range listing.Instructions {
		switch ins.Code {
		case 0x02, 0x09, 0xEF, 0xF0, 0xF1, 0x30, 0x1D:
			// no-op / scope / debug
		case 0xD0:
			stack = append(stack, "this")
		case 0xD1:
			stack = append(stack, "loc1")
		case 0xD2:
			stack = append(stack, "loc2")
		case 0xD3:
			stack = append(stack, "loc3")
		case 0x62:
			stack = append(stack, strings.Replace(ins.Operands, "r", "loc", 1))
		case 0xD4, 0xD5, 0xD6, 0xD7:
			val, ok := pop()
			if !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in setlocal")
			}
			reg := ins.Code - 0xD4
			statements = append(statements, fmt.Sprintf("var loc%d = %s;", reg, val))
		case 0x63:
			val, ok := pop()
			if !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in setlocal")
			}
			statements = append(statements, fmt.Sprintf("var %s = %s;", strings.Replace(ins.Operands, "r", "loc", 1), val))
		case 0x20:
			stack = append(stack, "null")
		case 0x21:
			stack = append(stack, "undefined")
		case 0x26:
			stack = append(stack, "true")
		case 0x27:
			stack = append(stack, "false")
		case 0x28:
			stack = append(stack, "NaN")
		case 0x24, 0x25, 0x2C, 0x2D, 0x2E, 0x2F:
			stack = append(stack, ins.Operands)
		case 0x5D, 0x5E, 0x60:
			stack = append(stack, ins.Operands)
		case 0x66:
			obj, ok := pop()
			if !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in getproperty")
			}
			stack = append(stack, fmt.Sprintf("%s.%s", obj, ins.Operands))
		case 0x61, 0x68:
			val, ok1 := pop()
			obj, ok2 := pop()
			if !ok1 || !ok2 {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in set/initproperty")
			}
			if obj == "this" {
				statements = append(statements, fmt.Sprintf("%s = %s;", ins.Operands, val))
			} else {
				statements = append(statements, fmt.Sprintf("%s.%s = %s;", obj, ins.Operands, val))
			}
		case 0x46, 0x4C, 0x4A, 0x4F:
			name, argc, ok := parseNameAndArgc(ins.Operands)
			if !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Invalid call operands")
			}
			args := make([]string, argc)
			for i := argc - 1; i >= 0; i-- {
				a, ok := pop()
				if !ok {
					return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in call args")
				}
				args[i] = a
			}
			receiver, ok := pop()
			if !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in call receiver")
			}
			var call string
			if ins.Code == 0x4A {
				call = fmt.Sprintf("new %s(%s)", name, strings.Join(args, ", "))
			} else if receiver == name || receiver == "this" {
				call = fmt.Sprintf("%s(%s)", name, strings.Join(args, ", "))
			} else {
				call = fmt.Sprintf("%s.%s(%s)", receiver, name, strings.Join(args, ", "))
			}
			if ins.Code == 0x4F {
				statements = append(statements, call+";")
			} else {
				stack = append(stack, call)
			}
		case 0x49:
			argc, ok := parseArgc(ins.Operands)
			if !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Invalid constructsuper")
			}
			args := make([]string, argc)
			for i := argc - 1; i >= 0; i-- {
				a, ok := pop()
				if !ok {
					return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in constructsuper args")
				}
				args[i] = a
			}
			if _, ok := pop(); !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in constructsuper receiver")
			}
			statements = append(statements, fmt.Sprintf("super(%s);", strings.Join(args, ", ")))
		case 0xA0, 0xC5:
			if !binaryOp(&stack, "+") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in add")
			}
		case 0xA1, 0xC6:
			if !binaryOp(&stack, "-") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in subtract")
			}
		case 0xA2, 0xC7:
			if !binaryOp(&stack, "*") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in multiply")
			}
		case 0xA3:
			if !binaryOp(&stack, "/") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in divide")
			}
		case 0xA4:
			if !binaryOp(&stack, "%") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in modulo")
			}
		case 0xAB:
			if !binaryOp(&stack, "==") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in equals")
			}
		case 0xAC:
			if !binaryOp(&stack, "===") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in strictequals")
			}
		case 0xAD:
			if !binaryOp(&stack, "<") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in lessthan")
			}
		case 0xAE:
			if !binaryOp(&stack, "<=") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in lessequals")
			}
		case 0xAF:
			if !binaryOp(&stack, ">") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in greaterthan")
			}
		case 0xB0:
			if !binaryOp(&stack, ">=") {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in greaterequals")
			}
		case 0x70, 0x73, 0x74, 0x75, 0x76, 0x77, 0x80, 0x82, 0x85:
			// coercion
		case 0x29:
			val, ok := pop()
			if !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in pop")
			}
			statements = append(statements, val+";")
		case 0x47:
			statements = append(statements, "return;")
		case 0x48:
			val, ok := pop()
			if !ok {
				return fallbackAvm2(unitId, listing.Pcode, diags, "Stack underflow in returnvalue")
			}
			statements = append(statements, fmt.Sprintf("return %s;", val))
		default:
			return fallbackAvm2(unitId, listing.Pcode, diags, fmt.Sprintf("Unsupported AVM2 opcode %s (0x%02x) for structured emission", ins.Name, ins.Code))
		}
	}

	var indented []string
	for _, s := range statements {
		indented = append(indented, "    "+s)
	}
	src := fmt.Sprintf("// GoSWF structured AVM2 reconstruction\nfunction %s() {\n%s\n}\n", sanitizeMethodName(displayName), strings.Join(indented, "\n"))
	return DecompileResult{
		UnitId:      unitId,
		Language:    "avm2",
		State:       "structured",
		Source:      &src,
		Pcode:       listing.Pcode,
		PcodeTotal:  len(listing.Pcode),
		Diagnostics: diags,
	}
}

func pcodeOnly(unitId, language, pcode string, diags []string, reason string) DecompileResult {
	diags = append(diags, reason)
	return DecompileResult{
		UnitId:      unitId,
		Language:    language,
		State:       "pcode_only",
		Source:      nil,
		Pcode:       pcode,
		PcodeTotal:  len(pcode),
		Diagnostics: diags,
	}
}

func fallbackAvm1(unitId, pcode string, diags []string, reason string) DecompileResult {
	return pcodeOnly(unitId, "avm1", pcode, diags, reason)
}

func fallbackAvm2(unitId, pcode string, diags []string, reason string) DecompileResult {
	return pcodeOnly(unitId, "avm2", pcode, diags, reason)
}

func binaryOp(stack *[]string, op string) bool {
	if len(*stack) < 2 {
		return false
	}
	rhs := (*stack)[len(*stack)-1]
	lhs := (*stack)[len(*stack)-2]
	*stack = (*stack)[:len(*stack)-2]
	*stack = append(*stack, fmt.Sprintf("(%s %s %s)", lhs, op, rhs))
	return true
}

func splitOperands(operands string) []string {
	var out []string
	var cur strings.Builder
	inString := false
	escaped := false
	for i := 0; i < len(operands); i++ {
		c := operands[i]
		if escaped {
			cur.WriteByte(c)
			escaped = false
		} else if c == '\\' && inString {
			cur.WriteByte(c)
			escaped = true
		} else if c == '"' {
			inString = !inString
			cur.WriteByte(c)
		} else if c == ',' && !inString {
			t := strings.TrimSpace(cur.String())
			if t != "" {
				out = append(out, t)
			}
			cur.Reset()
		} else {
			cur.WriteByte(c)
		}
	}
	t := strings.TrimSpace(cur.String())
	if t != "" {
		out = append(out, t)
	}
	return out
}

func normalizeAvm1Atom(atom string, regParams map[int]string) string {
	if strings.HasPrefix(atom, "c:") {
		start := strings.IndexByte(atom, '(')
		if start != -1 && strings.HasSuffix(atom, ")") {
			return atom[start+1 : len(atom)-1]
		}
	}
	if strings.HasPrefix(atom, "r:") {
		var reg int
		if _, err := fmt.Sscanf(atom[2:], "%d", &reg); err == nil {
			if pname, ok := regParams[reg]; ok {
				return pname
			}
		}
		return "reg" + atom[2:]
	}
	return atom
}

func stripStringIdentifier(value string) string {
	if len(value) >= 2 && strings.HasPrefix(value, "\"") && strings.HasSuffix(value, "\"") {
		inner := value[1 : len(value)-1]
		if isIdentifier(inner) {
			return inner
		}
	}
	return value
}

func isIdentifier(s string) bool {
	if len(s) == 0 {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if i == 0 {
			if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || c == '$') {
				return false
			}
		} else {
			if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '$') {
				return false
			}
		}
	}
	return true
}

func parseNameAndArgc(operands string) (string, int, bool) {
	idx := strings.LastIndex(operands, " argc=")
	if idx == -1 {
		return "", 0, false
	}
	name := operands[:idx]
	var argc int
	if _, err := fmt.Sscanf(operands[idx+6:], "%d", &argc); err != nil {
		return "", 0, false
	}
	return name, argc, true
}

func parseArgc(operands string) (int, bool) {
	if !strings.HasPrefix(operands, "argc=") {
		return 0, false
	}
	var argc int
	if _, err := fmt.Sscanf(operands[5:], "%d", &argc); err != nil {
		return 0, false
	}
	return argc, true
}

func sanitizeMethodName(name string) string {
	var b strings.Builder
	for i := 0; i < len(name); i++ {
		c := name[i]
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' {
			b.WriteByte(c)
		} else {
			b.WriteByte('_')
		}
	}
	return b.String()
}
