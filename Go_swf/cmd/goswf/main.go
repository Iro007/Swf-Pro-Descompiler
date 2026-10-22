package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"time"
)

type WorkerRequest struct {
	RequestId string         `json:"request_id"`
	Operation string         `json:"operation"`
	Args      map[string]any `json:"args"`
}

func (s *SwfSession) infoMap() map[string]any {
	return map[string]any{
		"path":               s.Path,
		"sha256":             s.FileSha256,
		"normalized_sha256":  s.NormalizedSha256,
		"compression":        s.Compression,
		"version":            s.Version,
		"file_length":        s.FileLength,
		"stage_width_twips":  s.StageWidthTwips,
		"stage_height_twips": s.StageHeightTwips,
		"frame_rate":         s.FrameRate,
		"frame_count":        s.FrameCount,
		"tag_count":          len(s.Tags),
		"script_count":       len(s.Scripts),
		"symbol_count":       len(s.Symbols),
		"abc_count":          len(s.abcFiles),
		"diagnostic_count":   len(s.Diagnostics),
	}
}

func toInt(v any, def int) int {
	switch val := v.(type) {
	case float64:
		return int(val)
	case int:
		return val
	default:
		return def
	}
}

func executeOperation(session **SwfSession, operation string, args map[string]any) (any, error) {
	if args == nil {
		args = map[string]any{}
	}
	switch operation {
	case "open":
		pathStr, _ := args["path"].(string)
		if pathStr == "" {
			return nil, fmt.Errorf("Missing 'path' argument")
		}
		opened, err := openSwfFile(pathStr)
		if err != nil {
			return nil, err
		}
		*session = opened
		return map[string]any{
			"info":        opened.infoMap(),
			"diagnostics": opened.Diagnostics,
		}, nil
	case "info":
		if *session == nil {
			return nil, fmt.Errorf("No SWF session is open")
		}
		return (*session).infoMap(), nil
	case "scripts":
		if *session == nil {
			return nil, fmt.Errorf("No SWF session is open")
		}
		s := *session
		offset := toInt(args["offset"], 0)
		limit := toInt(args["limit"], 200)
		if limit <= 0 {
			limit = 1
		}
		if limit > 500 {
			limit = 500
		}
		total := len(s.Scripts)
		start := offset
		if start > total {
			start = total
		}
		end := start + limit
		if end > total {
			end = total
		}
		return map[string]any{
			"offset": start,
			"limit":  limit,
			"total":  total,
			"items":  s.Scripts[start:end],
		}, nil
	case "disassemble":
		if *session == nil {
			return nil, fmt.Errorf("No SWF session is open")
		}
		unitId, _ := args["id"].(string)
		if unitId == "" {
			return nil, fmt.Errorf("Missing 'id' argument")
		}
		offset := toInt(args["offset"], 0)
		limit := toInt(args["limit"], 0)
		return (*session).disassemble(unitId, offset, limit)
	case "decompile":
		if *session == nil {
			return nil, fmt.Errorf("No SWF session is open")
		}
		unitId, _ := args["id"].(string)
		if unitId == "" {
			return nil, fmt.Errorf("Missing 'id' argument")
		}
		return (*session).decompile(unitId)
	default:
		return nil, fmt.Errorf("Unknown worker operation '%s'", operation)
	}
}

func writeJsonLine(v any) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	_, _ = os.Stdout.Write(buf.Bytes())
}

func runWorker() {
	var session *SwfSession
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64*1024), 16*1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var req WorkerRequest
		if err := json.Unmarshal(line, &req); err != nil {
			writeJsonLine(map[string]any{
				"request_id": nil,
				"error":      fmt.Sprintf("Invalid JSONL request: %v", err),
				"elapsed_ms": 0.0,
			})
			continue
		}
		start := time.Now()
		res, err := executeOperation(&session, req.Operation, req.Args)
		elapsedMs := float64(time.Since(start).Nanoseconds()) / 1e6
		if err != nil {
			writeJsonLine(map[string]any{
				"request_id": req.RequestId,
				"error":      err.Error(),
				"elapsed_ms": elapsedMs,
			})
		} else {
			writeJsonLine(map[string]any{
				"request_id": req.RequestId,
				"result":     res,
				"elapsed_ms": elapsedMs,
			})
		}
	}
}

func main() {
	args := os.Args[1:]
	if len(args) == 0 || args[0] == "worker" {
		runWorker()
		return
	}
	cmd := args[0]
	switch cmd {
	case "info":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "Usage: goswf info <swf>")
			os.Exit(2)
		}
		s, err := openSwfFile(args[1])
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		b, _ := json.MarshalIndent(map[string]any{
			"info":        s.infoMap(),
			"diagnostics": s.Diagnostics,
		}, "", "  ")
		fmt.Println(string(b))
	case "scripts":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "Usage: goswf scripts <swf>")
			os.Exit(2)
		}
		s, err := openSwfFile(args[1])
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		b, _ := json.MarshalIndent(s.Scripts, "", "  ")
		fmt.Println(string(b))
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", cmd)
		os.Exit(2)
	}
}
