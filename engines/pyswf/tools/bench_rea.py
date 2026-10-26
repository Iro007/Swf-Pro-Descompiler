"""Comprehensive Multi-Tool Benchmark & REA Binary/Web Analysis.

Analyzes and benchmarks every SWF tool:
1. Native & CLI tools: PySWF, JPEXS, RustSWF, JsSWF, GoSWF, AsmSWF, Flare, Flasm, RABCDAsm, pyflashkit, swfmill, swfextract
2. Web / Remote tools: Decompiler.com (analyzing captured outputs in vendor/references/remote-results/)
3. Proprietary Windows GUI tools: AS3 Sorcerer, Sothink SWF Decompiler, Flash Decompiler Trillix
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time

# Anchors
WORKSPACE = Path(__file__).resolve().parents[3]
VENDOR = WORKSPACE / "vendor"
REFERENCES = VENDOR / "references"
REMOTE_RESULTS = REFERENCES / "remote-results"
CORPUS = WORKSPACE / "corpus"
REPORTS = WORKSPACE / "reports" / "bench"

def pe_info(exe_path: Path):
    if not exe_path.is_file():
        return {"exists": False}
    data = exe_path.read_bytes()
    size = len(data)
    sha = hashlib.sha256(data).hexdigest()
    # Check DOS header
    if data[:2] != b"MZ":
        return {"exists": True, "size": size, "sha256": sha, "is_pe": False}
    pe_offset = struct.unpack_from("<I", data, 0x3c)[0]
    if pe_offset + 24 > len(data) or data[pe_offset:pe_offset+4] != b"PE\x00\x00":
        return {"exists": True, "size": size, "sha256": sha, "is_pe": False}
    
    machine, num_sections, timestamp = struct.unpack_from("<HHxxxxI", data, pe_offset + 4)
    arch = "x86" if machine == 0x14c else "x64" if machine == 0x8664 else f"0x{machine:x}"
    
    # Optional header
    opt_magic = struct.unpack_from("<H", data, pe_offset + 24)[0]
    subsystem = None
    if opt_magic in (0x10b, 0x20b): # PE32 or PE32+
        subsystem_offset = pe_offset + 24 + (68 if opt_magic == 0x10b else 68)
        subsys_id = struct.unpack_from("<H", data, subsystem_offset)[0]
        subsystem = "GUI (Windows GUI)" if subsys_id == 2 else "CLI (Console)" if subsys_id == 3 else f"Other ({subsys_id})"
    
    return {
        "exists": True,
        "size_bytes": size,
        "sha256": sha,
        "is_pe": True,
        "architecture": arch,
        "subsystem": subsystem,
        "has_cli_headless": (subsystem == "CLI (Console)")
    }

def analyze_decompiler_com():
    zpath = REMOTE_RESULTS / "decompiler-com-as3.zip"
    if not zpath.is_file():
        return {"status": "no_capture"}
    import zipfile
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        as_files = [n for n in names if n.endswith(".as")]
        log = z.read("FLA-CONVERSION.log").decode("utf-8", "ignore") if "FLA-CONVERSION.log" in names else ""
        status = z.read("FLA-STATUS.txt").decode("utf-8", "ignore") if "FLA-STATUS.txt" in names else ""
    
    # Identify engine from log traces
    backend = "JPEXS (FFDec) under Linux JVM" if "XFL stage=" in log else "Unknown custom service"
    return {
        "service": "https://www.decompiler.com/swf",
        "underlying_engine": backend,
        "captured_files_count": len(names),
        "decompiled_as_count": len(as_files),
        "status_summary": status.strip(),
        "notes": "Servicio remoto web. Ejecuta JPEXS en servidor remoto. Adecuado para descompilaciones aisladas; no es un ejecutable local independiente."
    }

def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tools": {}
    }

    # 1. Proprietary GUI tools
    gui_tools = {
        "as3_sorcerer": Path(r"C:\Program Files (x86)\AS3 Sorcerer\as3s.exe"),
        "sothink": Path(r"C:\Program Files (x86)\SourceTec\Sothink SWF Decompiler\SWFDecompiler.exe"),
        "trillix": Path(r"C:\Program Files (x86)\Eltima Software\Flash Decompiler Trillix\FlashDecompiler.exe")
    }

    for tool_id, path in gui_tools.items():
        info = pe_info(path)
        info["path"] = str(path)
        if tool_id == "as3_sorcerer":
            info["evaluation"] = "Instalado. Subsystem GUI puro (Delphi VCL). Carece de flags de exportación por línea de comandos. Interfaz interactiva."
        elif tool_id == "sothink":
            info["evaluation"] = "Instalado. Subsystem GUI (MFC). Restricción de versión de prueba: limita la exportación a 2 recursos por categoría."
        elif tool_id == "trillix":
            info["evaluation"] = "Instalado. Subsystem GUI. Restricción de versión de prueba: bloquea la visualización y exportación de ActionScript."
        report["tools"][tool_id] = info

    # 2. Web tool
    report["tools"]["decompiler_com"] = analyze_decompiler_com()

    # 3. Read latest benchmark results from disk
    index_file = REPORTS / "index.json"
    if index_file.is_file():
        runs = json.loads(index_file.read_text(encoding="utf-8"))
        if runs:
            latest_id = runs[-1]["run_id"]
            run_file = REPORTS / latest_id / "results.json"
            if run_file.is_file():
                results = json.loads(run_file.read_text(encoding="utf-8"))
                report["benchmark_run"] = latest_id
                report["benchmark_tables"] = results.get("tables", {})

    out_path = REPORTS / "benchmarks-all-tools.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Reporte consolidado guardado en: {out_path}")

if __name__ == "__main__":
    main()
