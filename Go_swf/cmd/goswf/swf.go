package main

import (
	"bytes"
	"compress/zlib"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"strings"
)

type bitReader struct {
	data   []byte
	bitPos int
}

func newBitReader(data []byte) *bitReader {
	return &bitReader{data: data, bitPos: 0}
}

func (br *bitReader) readBits(count int) (uint32, bool) {
	if count == 0 {
		return 0, true
	}
	var value uint32
	for i := 0; i < count; i++ {
		byteIdx := br.bitPos >> 3
		if byteIdx >= len(br.data) {
			return 0, false
		}
		bitIdx := 7 - (br.bitPos & 7)
		bit := (br.data[byteIdx] >> uint(bitIdx)) & 1
		value = (value << 1) | uint32(bit)
		br.bitPos++
	}
	return value, true
}

func (br *bitReader) readSBits(count int) (int32, bool) {
	if count == 0 {
		return 0, true
	}
	unsigned, ok := br.readBits(count)
	if !ok {
		return 0, false
	}
	signBit := uint32(1) << uint(count-1)
	if (unsigned & signBit) != 0 {
		return int32(unsigned | (^uint32(0) << uint(count))), true
	}
	return int32(unsigned), true
}

func (br *bitReader) bytesConsumed() int {
	return (br.bitPos + 7) >> 3
}

type SwfTag struct {
	Index       int
	TagPath     []int
	Code        uint16
	Name        string
	HeaderStart int
	DataStart   int
	DataLen     int
	LongHeader  bool
	SpriteId    *uint16
	FrameCount  *uint16
	Children    []SwfTag
}

type ScriptUnit struct {
	Id                  string   `json:"id"`
	TagIndex            int      `json:"tag_index"`
	TagPath             []int    `json:"tag_path"`
	TagCode             uint16   `json:"tag_code"`
	Language            string   `json:"language"`
	Kind                string   `json:"kind"`
	DisplayName         string   `json:"display_name"`
	ByteStart           int      `json:"byte_start"`
	ByteLen             int      `json:"byte_len"`
	Sha256              string   `json:"sha256"`
	Parameters          []string `json:"parameters"`
	Avm1Registers       []int    `json:"avm1_registers"`
	Avm1RegisterCount   *int     `json:"avm1_register_count,omitempty"`
	Avm1Flags           *int     `json:"avm1_flags,omitempty"`
	Avm1ConstantsSha256 *string  `json:"avm1_constants_sha256,omitempty"`
	Diagnostics         []string `json:"diagnostics"`
}

type unitPayload struct {
	kind       string // "avm1" | "avm2"
	bytes      []byte
	constants  []string
	params     []string
	registers  []int
	fnName     string
	flags      int
	abcIndex   int
	bodyIndex  int
}

type SwfSession struct {
	Path                string
	FileSha256          string
	NormalizedSha256    string
	Compression         string
	Version             byte
	FileLength          uint32
	StageWidthTwips     int32
	StageHeightTwips    int32
	FrameRate           float64
	FrameCount          uint16
	NormalizedBytes     []byte
	Tags                []SwfTag
	Symbols             map[uint16]string
	Diagnostics         []string
	Scripts             []ScriptUnit
	payloads            map[string]unitPayload
	abcFiles            []*AbcFile
}

func tagName(code uint16) string {
	switch code {
	case 0:
		return "End"
	case 1:
		return "ShowFrame"
	case 2:
		return "DefineShape"
	case 7:
		return "DefineButton"
	case 9:
		return "SetBackgroundColor"
	case 12:
		return "DoAction"
	case 26:
		return "PlaceObject2"
	case 34:
		return "DefineButton2"
	case 39:
		return "DefineSprite"
	case 43:
		return "FrameLabel"
	case 56:
		return "ExportAssets"
	case 59:
		return "DoInitAction"
	case 65:
		return "ScriptLimits"
	case 69:
		return "FileAttributes"
	case 70:
		return "PlaceObject3"
	case 72:
		return "DoABC"
	case 76:
		return "SymbolClass"
	case 77:
		return "Metadata"
	case 82:
		return "DoABC2"
	default:
		return "UnknownTag"
	}
}

func parseTagStream(data []byte, start int, end int, parentPath []int, diags *[]string, depth int) []SwfTag {
	var tags []SwfTag
	if depth > 8 {
		*diags = append(*diags, "DefineSprite nesting depth exceeded 8")
		return tags
	}
	pos := start
	index := 0
	for pos < end {
		headerStart := pos
		if pos+2 > end {
			*diags = append(*diags, fmt.Sprintf("Truncated tag header at byte %d", pos))
			break
		}
		recordHeader := binary.LittleEndian.Uint16(data[pos : pos+2])
		pos += 2
		code := recordHeader >> 6
		length := int(recordHeader & 0x3F)
		longHeader := false
		if length == 0x3F {
			longHeader = true
			if pos+4 > end {
				*diags = append(*diags, fmt.Sprintf("Truncated long tag length for tag %d at byte %d", code, headerStart))
				break
			}
			uLen := binary.LittleEndian.Uint32(data[pos : pos+4])
			pos += 4
			if uLen > 100*1024*1024 {
				*diags = append(*diags, fmt.Sprintf("Tag %d length %d exceeds safety limit", code, uLen))
				break
			}
			length = int(uLen)
		}
		dataStart := pos
		available := end - dataStart
		clampedLen := length
		if clampedLen > available {
			clampedLen = available
		}
		if length > available {
			*diags = append(*diags, fmt.Sprintf("Tag %d at byte %d extends %d bytes past stream", code, headerStart, length-available))
		}
		tagPath := make([]int, len(parentPath)+1)
		copy(tagPath, parentPath)
		tagPath[len(parentPath)] = index

		var spriteId *uint16
		var frameCount *uint16
		var children []SwfTag
		if code == 39 {
			if clampedLen >= 4 {
				sid := binary.LittleEndian.Uint16(data[dataStart : dataStart+2])
				fc := binary.LittleEndian.Uint16(data[dataStart+2 : dataStart+4])
				spriteId = &sid
				frameCount = &fc
				children = parseTagStream(data, dataStart+4, dataStart+clampedLen, tagPath, diags, depth+1)
			} else {
				*diags = append(*diags, fmt.Sprintf("DefineSprite tag at index %d shorter than 4-byte header", index))
			}
		}

		tags = append(tags, SwfTag{
			Index:       index,
			TagPath:     tagPath,
			Code:        code,
			Name:        tagName(code),
			HeaderStart: headerStart,
			DataStart:   dataStart,
			DataLen:     clampedLen,
			LongHeader:  longHeader,
			SpriteId:    spriteId,
			FrameCount:  frameCount,
			Children:    children,
		})
		pos = dataStart + clampedLen
		index++
		if code == 0 {
			break
		}
	}
	return tags
}

func parseSymbolMap(data []byte, tags []SwfTag, diags *[]string) map[uint16]string {
	symbols := make(map[uint16]string)
	for _, tag := range tags {
		if tag.Code != 76 && tag.Code != 56 {
			continue
		}
		slice := data[tag.DataStart : tag.DataStart+tag.DataLen]
		if len(slice) < 2 {
			*diags = append(*diags, fmt.Sprintf("Tag %d (%s) shorter than symbol count", tag.Index, tag.Name))
			continue
		}
		count := int(binary.LittleEndian.Uint16(slice[0:2]))
		pos := 2
		for i := 0; i < count; i++ {
			if pos+2 > len(slice) {
				*diags = append(*diags, fmt.Sprintf("Truncated symbol entry in tag %d", tag.Index))
				break
			}
			id := binary.LittleEndian.Uint16(slice[pos : pos+2])
			pos += 2
			end := pos
			for end < len(slice) && slice[end] != 0 {
				end++
			}
			if end >= len(slice) {
				*diags = append(*diags, fmt.Sprintf("Unterminated symbol name in tag %d", tag.Index))
				break
			}
			name := string(bytes.ToValidUTF8(slice[pos:end], []byte("\uFFFD")))
			pos = end + 1
			symbols[id] = name
		}
	}
	return symbols
}

func formatTagPrefix(tagPath []int) string {
	var parts []string
	for _, idx := range tagPath {
		parts = append(parts, fmt.Sprintf("tag:%d", idx))
	}
	return strings.Join(parts, "/")
}

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func openSwfFile(filePath string) (*SwfSession, error) {
	raw, err := os.ReadFile(filePath)
	if err != nil {
		return nil, err
	}
	return openSwfBytes(raw, filePath)
}

func openSwfBytes(rawBytes []byte, filePath string) (*SwfSession, error) {
	if len(rawBytes) < 8 {
		return nil, fmt.Errorf("SWF shorter than 8-byte header")
	}
	sig := string(rawBytes[0:3])
	if sig != "FWS" && sig != "CWS" && sig != "ZWS" {
		return nil, fmt.Errorf("Expected FWS, CWS or ZWS signature")
	}
	version := rawBytes[3]
	fileLength := binary.LittleEndian.Uint32(rawBytes[4:8])
	if fileLength < 8 || fileLength > 100*1024*1024 {
		return nil, fmt.Errorf("Declared SWF length %d is outside supported bounds", fileLength)
	}

	var diags []string
	var normalized []byte
	switch sig {
	case "FWS":
		normalized = make([]byte, len(rawBytes))
		copy(normalized, rawBytes)
		if len(normalized) != int(fileLength) {
			diags = append(diags, fmt.Sprintf("FWS header length %d differs from actual size %d", fileLength, len(normalized)))
		}
	case "CWS":
		zr, err := zlib.NewReader(bytes.NewReader(rawBytes[8:]))
		if err != nil {
			return nil, fmt.Errorf("Zlib decompression failed: %v", err)
		}
		body, err := io.ReadAll(zr)
		_ = zr.Close()
		if err != nil {
			return nil, fmt.Errorf("Zlib decompression failed: %v", err)
		}
		normalized = make([]byte, 8+len(body))
		copy(normalized[0:3], []byte("FWS"))
		normalized[3] = version
		copy(normalized[4:8], rawBytes[4:8])
		copy(normalized[8:], body)
		if len(normalized) != int(fileLength) {
			diags = append(diags, fmt.Sprintf("CWS decompressed size %d differs from declared %d", len(normalized), fileLength))
		}
	case "ZWS":
		expectedBody := int(fileLength) - 8
		if expectedBody < 0 {
			expectedBody = 0
		}
		body, err := decompressLzmaSwf(rawBytes, expectedBody)
		if err != nil {
			return nil, err
		}
		normalized = make([]byte, 8+len(body))
		copy(normalized[0:3], []byte("FWS"))
		normalized[3] = version
		copy(normalized[4:8], rawBytes[4:8])
		copy(normalized[8:], body)
		if len(normalized) != int(fileLength) {
			diags = append(diags, fmt.Sprintf("ZWS decompressed size %d differs from declared %d", len(normalized), fileLength))
		}
	}

	if len(normalized) < 12 {
		return nil, fmt.Errorf("Decompressed SWF shorter than stage header")
	}
	br := newBitReader(normalized[8:])
	nbits, ok := br.readBits(5)
	if !ok {
		return nil, fmt.Errorf("Truncated RECT nbits")
	}
	xMin, ok1 := br.readSBits(int(nbits))
	xMax, ok2 := br.readSBits(int(nbits))
	yMin, ok3 := br.readSBits(int(nbits))
	yMax, ok4 := br.readSBits(int(nbits))
	if !ok1 || !ok2 || !ok3 || !ok4 {
		return nil, fmt.Errorf("Truncated RECT bounds")
	}
	rectEnd := 8 + br.bytesConsumed()
	if rectEnd+4 > len(normalized) {
		return nil, fmt.Errorf("Truncated frame header after RECT")
	}
	frameRateRaw := binary.LittleEndian.Uint16(normalized[rectEnd : rectEnd+2])
	frameCount := binary.LittleEndian.Uint16(normalized[rectEnd+2 : rectEnd+4])
	tagStreamStart := rectEnd + 4

	tags := parseTagStream(normalized, tagStreamStart, len(normalized), []int{}, &diags, 0)
	symbols := parseSymbolMap(normalized, tags, &diags)

	session := &SwfSession{
		Path:             filePath,
		FileSha256:       sha256Hex(rawBytes),
		NormalizedSha256: sha256Hex(normalized),
		Compression:      sig,
		Version:          version,
		FileLength:       fileLength,
		StageWidthTwips:  xMax - xMin,
		StageHeightTwips: yMax - yMin,
		FrameRate:        float64(frameRateRaw) / 256.0,
		FrameCount:       frameCount,
		NormalizedBytes:  normalized,
		Tags:             tags,
		Symbols:          symbols,
		Diagnostics:      diags,
		Scripts:          []ScriptUnit{},
		payloads:         make(map[string]unitPayload),
		abcFiles:         []*AbcFile{},
	}

	session.walkScripts(tags, "")
	return session, nil
}

func (s *SwfSession) addAvm1Block(
	id string,
	tag *SwfTag,
	kind string,
	displayName string,
	byteStart int,
	slice []byte,
	unitDiags []string,
) {
	nested, nestedDiags := extractNestedAvm1Functions(id, byteStart, slice)
	allDiags := append([]string{}, unitDiags...)
	allDiags = append(allDiags, nestedDiags...)
	s.Scripts = append(s.Scripts, ScriptUnit{
		Id:            id,
		TagIndex:      tag.Index,
		TagPath:       tag.TagPath,
		TagCode:       tag.Code,
		Language:      "avm1",
		Kind:          kind,
		DisplayName:   displayName,
		ByteStart:     byteStart,
		ByteLen:       len(slice),
		Sha256:        sha256Hex(slice),
		Parameters:    []string{},
		Avm1Registers: []int{},
		Diagnostics:   allDiags,
	})
	s.payloads[id] = unitPayload{
		kind:      "avm1",
		bytes:     slice,
		constants: []string{},
		params:    nil,
		registers: []int{},
	}

	for _, fn := range nested {
		fnStart := byteStart + fn.BodyOffset
		fnBytes := slice[fn.BodyOffset : fn.BodyOffset+fn.BodyLen]
		fnId := fn.Id
		fnLabel := fn.Name
		if fnLabel == "" {
			fnLabel = fmt.Sprintf("<anonymous@0x%x>", fn.BodyOffset)
		}
		flagsCopy := fn.Flags
		constHashCopy := fn.ConstantsSha256
		params := fn.Parameters
		if params == nil {
			params = []string{}
		}
		regs := fn.Registers
		if regs == nil {
			regs = []int{}
		}
		s.Scripts = append(s.Scripts, ScriptUnit{
			Id:                  fnId,
			TagIndex:            tag.Index,
			TagPath:             tag.TagPath,
			TagCode:             tag.Code,
			Language:            "avm1",
			Kind:                "function",
			DisplayName:         fmt.Sprintf("%s :: function %s", displayName, fnLabel),
			ByteStart:           fnStart,
			ByteLen:             len(fnBytes),
			Sha256:              sha256Hex(fnBytes),
			Parameters:          params,
			Avm1Registers:       regs,
			Avm1RegisterCount:   fn.RegisterCount,
			Avm1Flags:           &flagsCopy,
			Avm1ConstantsSha256: &constHashCopy,
			Diagnostics:         []string{},
		})
		s.payloads[fnId] = unitPayload{
			kind:      "avm1",
			bytes:     fnBytes,
			constants: fn.Constants,
			params:    params,
			registers: regs,
			fnName:    fn.Name,
			flags:     fn.Flags,
		}
	}
}

func (s *SwfSession) walkScripts(tags []SwfTag, scopeLabel string) {
	data := s.NormalizedBytes
	for i := range tags {
		tag := &tags[i]
		tagSlice := data[tag.DataStart : tag.DataStart+tag.DataLen]
		prefix := formatTagPrefix(tag.TagPath)

		switch tag.Code {
		case 12:
			id := prefix + "/actions"
			disp := fmt.Sprintf("FrameAction (tag %d)", tag.Index)
			if scopeLabel != "" {
				disp = fmt.Sprintf("%s :: FrameAction (tag %d)", scopeLabel, tag.Index)
			}
			s.addAvm1Block(id, tag, "frame_action", disp, tag.DataStart, tagSlice, []string{})
		case 59:
			if len(tagSlice) < 2 {
				s.Diagnostics = append(s.Diagnostics, fmt.Sprintf("DoInitAction tag %d shorter than sprite ID", tag.Index))
				continue
			}
			spriteId := binary.LittleEndian.Uint16(tagSlice[0:2])
			actionSlice := tagSlice[2:]
			id := prefix + "/actions"
			symbol := fmt.Sprintf("sprite_%d", spriteId)
			if sym, ok := s.Symbols[spriteId]; ok {
				symbol = sym
			}
			disp := fmt.Sprintf("InitAction %s (tag %d)", symbol, tag.Index)
			if scopeLabel != "" {
				disp = fmt.Sprintf("%s :: InitAction %s (tag %d)", scopeLabel, symbol, tag.Index)
			}
			s.addAvm1Block(id, tag, "init_action", disp, tag.DataStart+2, actionSlice, []string{})
		case 7:
			sub := extractButtonActions(tagSlice)
			if sub != nil && sub.Length > 0 {
				buttonId := uint16(0)
				if len(tagSlice) >= 2 {
					buttonId = binary.LittleEndian.Uint16(tagSlice[0:2])
				}
				id := prefix + "/button:0"
				disp := fmt.Sprintf("Button %d actions (tag %d)", buttonId, tag.Index)
				if scopeLabel != "" {
					disp = fmt.Sprintf("%s :: Button %d actions (tag %d)", scopeLabel, buttonId, tag.Index)
				}
				actionBytes := tagSlice[sub.Offset : sub.Offset+sub.Length]
				s.addAvm1Block(id, tag, "button_action", disp, tag.DataStart+sub.Offset, actionBytes, []string{})
			}
		case 34:
			slices, bDiags := extractButton2Actions(tagSlice)
			buttonId := uint16(0)
			if len(tagSlice) >= 2 {
				buttonId = binary.LittleEndian.Uint16(tagSlice[0:2])
			}
			for condIdx, sub := range slices {
				id := fmt.Sprintf("%s/button2:%d", prefix, condIdx)
				disp := fmt.Sprintf("Button2 %d cond %d (tag %d)", buttonId, condIdx, tag.Index)
				if scopeLabel != "" {
					disp = fmt.Sprintf("%s :: Button2 %d cond %d (tag %d)", scopeLabel, buttonId, condIdx, tag.Index)
				}
				actionBytes := tagSlice[sub.Offset : sub.Offset+sub.Length]
				s.addAvm1Block(id, tag, "button_condition", disp, tag.DataStart+sub.Offset, actionBytes, bDiags)
			}
		case 26, 70:
			slices, cDiags := extractPlaceObjectActions(tagSlice, s.Version, tag.Code == 70)
			for clipIdx, sub := range slices {
				id := fmt.Sprintf("%s/clip:%d", prefix, clipIdx)
				disp := fmt.Sprintf("ClipAction %d (tag %d)", clipIdx, tag.Index)
				if scopeLabel != "" {
					disp = fmt.Sprintf("%s :: ClipAction %d (tag %d)", scopeLabel, clipIdx, tag.Index)
				}
				actionBytes := tagSlice[sub.Offset : sub.Offset+sub.Length]
				s.addAvm1Block(id, tag, "clip_action", disp, tag.DataStart+sub.Offset, actionBytes, cDiags)
			}
		case 39:
			sid := uint16(0)
			if tag.SpriteId != nil {
				sid = *tag.SpriteId
			}
			symbol := fmt.Sprintf("Sprite %d", sid)
			if sym, ok := s.Symbols[sid]; ok {
				symbol = sym
			}
			childScope := fmt.Sprintf("%s (tag %d)", symbol, tag.Index)
			if scopeLabel != "" {
				childScope = fmt.Sprintf("%s :: %s (tag %d)", scopeLabel, symbol, tag.Index)
			}
			s.walkScripts(tag.Children, childScope)
		case 72, 82:
			abc, err := parseDoAbc(tagSlice, tag.Code == 82)
			if err != nil {
				s.Diagnostics = append(s.Diagnostics, fmt.Sprintf("Failed to parse %s at tag %d: %v", tag.Name, tag.Index, err))
				continue
			}
			abcIndex := len(s.abcFiles)
			if len(abc.Bodies) == 0 {
				id := prefix + "/abc"
				abcSlice := tagSlice[abc.AbcHeaderOffset:]
				diags := append([]string{}, abc.Diagnostics...)
				s.Scripts = append(s.Scripts, ScriptUnit{
					Id:            id,
					TagIndex:      tag.Index,
					TagPath:       tag.TagPath,
					TagCode:       tag.Code,
					Language:      "avm2",
					Kind:          "abc_outline",
					DisplayName:   fmt.Sprintf("ABC block (tag %d)", tag.Index),
					ByteStart:     tag.DataStart + abc.AbcHeaderOffset,
					ByteLen:       len(abcSlice),
					Sha256:        sha256Hex(abcSlice),
					Parameters:    []string{},
					Avm1Registers: []int{},
					Diagnostics:   diags,
				})
				s.payloads[id] = unitPayload{
					kind:      "avm2_outline",
					abcIndex:  abcIndex,
					bodyIndex: -1,
				}
			}
			for bodyIndex := range abc.Bodies {
				body := &abc.Bodies[bodyIndex]
				traitLabel, hasTrait := abc.MethodNames[body.MethodIndex]
				var methodLabel string
				if hasTrait {
					methodLabel = traitLabel
				} else if body.MethodIndex < len(abc.Methods) && abc.Methods[body.MethodIndex].Name != "" {
					methodLabel = abc.Methods[body.MethodIndex].Name
				} else {
					methodLabel = fmt.Sprintf("method_%d", body.MethodIndex)
				}
				var displayName string
				if abc.AbcName != "" {
					displayName = fmt.Sprintf("%s :: %s", abc.AbcName, methodLabel)
				} else {
					displayName = methodLabel
				}
				id := fmt.Sprintf("%s/method:%d", prefix, body.MethodIndex)
				byteStart := tag.DataStart + abc.AbcHeaderOffset + body.CodeOffset
				diags := append([]string{}, abc.Diagnostics...)
				diags = append(diags, body.DecoderDiagnostics...)
				s.Scripts = append(s.Scripts, ScriptUnit{
					Id:            id,
					TagIndex:      tag.Index,
					TagPath:       tag.TagPath,
					TagCode:       tag.Code,
					Language:      "avm2",
					Kind:          "abc_method",
					DisplayName:   displayName,
					ByteStart:     byteStart,
					ByteLen:       len(body.Code),
					Sha256:        sha256Hex(body.Code),
					Parameters:    []string{},
					Avm1Registers: []int{},
					Diagnostics:   diags,
				})
				s.payloads[id] = unitPayload{
					kind:      "avm2",
					abcIndex:  abcIndex,
					bodyIndex: bodyIndex,
				}
			}
			s.abcFiles = append(s.abcFiles, abc)
		}
	}
}

func (s *SwfSession) findUnit(unitId string) (*ScriptUnit, error) {
	for i := range s.Scripts {
		if s.Scripts[i].Id == unitId {
			return &s.Scripts[i], nil
		}
	}
	return nil, fmt.Errorf("Unknown script unit '%s'", unitId)
}

func sliceStringByChars(str string, offset int, limit int) (string, int) {
	runes := []rune(str)
	total := len(runes)
	start := offset
	if start > total {
		start = total
	}
	effLimit := limit
	if effLimit <= 0 {
		effLimit = 65536
	}
	if effLimit > 65536 {
		effLimit = 65536
	}
	end := start + effLimit
	if end > total {
		end = total
	}
	return string(runes[start:end]), total
}

func (s *SwfSession) disassemble(unitId string, offset int, limit int) (map[string]any, error) {
	unit, err := s.findUnit(unitId)
	if err != nil {
		return nil, err
	}
	payload, ok := s.payloads[unitId]
	if !ok {
		return nil, fmt.Errorf("Script unit '%s' has no payload", unitId)
	}
	var state, pcode string
	var instructionTotal int
	var diags []string
	if payload.kind == "avm1" {
		dis := disassembleAvm1(payload.bytes, payload.constants)
		state = dis.State
		pcode = dis.Pcode
		instructionTotal = dis.InstructionTotal
		diags = dis.Diagnostics
	} else if payload.kind == "avm2_outline" {
		abc := s.abcFiles[payload.abcIndex]
		state = "outline_only"
		pcode = fmt.Sprintf("; ABC signatures: %d methods, no method bodies\n", len(abc.Methods))
		instructionTotal = 0
		diags = append([]string{}, abc.Diagnostics...)
	} else {
		abc := s.abcFiles[payload.abcIndex]
		body := &abc.Bodies[payload.bodyIndex]
		dis := disassembleAvm2(abc, body)
		state = dis.State
		pcode = dis.Pcode
		instructionTotal = dis.InstructionTotal
		diags = dis.Diagnostics
	}
	chunk, pcodeTotal := sliceStringByChars(pcode, offset, limit)
	effLimit := limit
	if effLimit <= 0 {
		effLimit = 65536
	}
	if effLimit > 65536 {
		effLimit = 65536
	}
	return map[string]any{
		"unit_id":           unit.Id,
		"language":          unit.Language,
		"state":             state,
		"offset":            offset,
		"limit":             effLimit,
		"pcode_total":       pcodeTotal,
		"pcode":             chunk,
		"instruction_total": instructionTotal,
		"diagnostics":       diags,
	}, nil
}

func (s *SwfSession) decompile(unitId string) (DecompileResult, error) {
	unit, err := s.findUnit(unitId)
	if err != nil {
		return DecompileResult{}, err
	}
	payload, ok := s.payloads[unitId]
	if !ok {
		return DecompileResult{}, fmt.Errorf("Script unit '%s' has no payload", unitId)
	}
	var res DecompileResult
	if payload.kind == "avm1" {
		res = decompileAvm1(unit.Id, payload.bytes, payload.constants, payload.params, payload.registers, payload.fnName, payload.flags)
	} else if payload.kind == "avm2_outline" {
		abc := s.abcFiles[payload.abcIndex]
		pcode := fmt.Sprintf("; ABC signatures: %d methods, no method bodies\n", len(abc.Methods))
		res = DecompileResult{
			UnitId:      unit.Id,
			Language:    "avm2",
			State:       "pcode_only",
			Source:      nil,
			Pcode:       pcode,
			PcodeTotal:  len(pcode),
			Diagnostics: append([]string{}, abc.Diagnostics...),
		}
	} else {
		abc := s.abcFiles[payload.abcIndex]
		body := &abc.Bodies[payload.bodyIndex]
		res = decompileAvm2(unit.Id, abc, body, unit.DisplayName)
	}
	chunk, total := sliceStringByChars(res.Pcode, 0, 65536)
	res.Pcode = chunk
	res.PcodeTotal = total
	return res, nil
}
