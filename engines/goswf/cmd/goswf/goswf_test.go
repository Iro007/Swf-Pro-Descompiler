package main

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestOpenAvm1DefineFunction2(t *testing.T) {
	swfPath := filepath.Join("..", "..", "..", "..", "benchmark_inputs", "as2_definefunc_regs.swf")
	s, err := openSwfFile(swfPath)
	if err != nil {
		t.Fatalf("openSwfFile failed: %v", err)
	}
	if len(s.Scripts) < 2 {
		t.Fatalf("expected >= 2 script units, got %d", len(s.Scripts))
	}
	foundFn := false
	for _, u := range s.Scripts {
		if u.Kind == "function" {
			foundFn = true
			if u.Avm1ConstantsSha256 == nil || len(*u.Avm1ConstantsSha256) != 64 {
				t.Fatalf("expected 64-char constants sha256, got %v", u.Avm1ConstantsSha256)
			}
			dec, err := s.decompile(u.Id)
			if err != nil {
				t.Fatalf("decompile failed: %v", err)
			}
			if dec.State != "structured" || dec.Source == nil || !strings.Contains(*dec.Source, "function") {
				t.Fatalf("expected structured function source, got state=%s", dec.State)
			}
		}
	}
	if !foundFn {
		t.Fatalf("expected at least one nested function unit")
	}
}

func TestOpenAvm2DoAbc(t *testing.T) {
	swfPath := filepath.Join("..", "..", "..", "..", "benchmark_inputs", "as3_new.flex.swf")
	s, err := openSwfFile(swfPath)
	if err != nil {
		t.Fatalf("openSwfFile failed: %v", err)
	}
	if len(s.Scripts) == 0 {
		t.Fatalf("expected > 0 AVM2 method units")
	}
	u := s.Scripts[0]
	if u.Language != "avm2" {
		t.Fatalf("expected avm2 language, got %s", u.Language)
	}
	dec, err := s.decompile(u.Id)
	if err != nil {
		t.Fatalf("decompile failed: %v", err)
	}
	if dec.State != "structured" || dec.Source == nil || !strings.Contains(*dec.Source, "function") {
		t.Fatalf("expected structured AVM2 source, got state=%s", dec.State)
	}
}
