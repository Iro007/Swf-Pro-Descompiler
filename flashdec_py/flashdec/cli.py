"""flashdec-py: descompilador SWF híbrido (PySWF + JPEXS, gana el mejor archivo)."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, jpexs
from .merge import merge

ENGINES = ("best", "native", "jpexs")
INPUT_SUFFIXES = {".swf", ".swc", ".spl"}


def _read_input(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() == ".swc" or data[:2] == b"PK":
        with zipfile.ZipFile(path) as archive:
            return archive.read("library.swf")
    return data


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _copy_resources(jpexs_dir: Path, destination: Path) -> dict:
    counts = {}
    for child in sorted(jpexs_dir.iterdir()):
        if child.is_dir() and child.name != "scripts":
            files = [p for p in child.rglob("*") if p.is_file()]
            if files:
                shutil.copytree(child, destination / child.name, dirs_exist_ok=True)
                counts[child.name] = len(files)
    return counts


def decompile_file(source: Path, out_dir: Path, args, tool, log) -> dict:
    started = time.perf_counter()
    data = _read_input(source)
    report = {"input": str(source), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
              "flashdec": f"flashdec-py {__version__}", "engine_mode": args.engine, "engines": {}}
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="flashdec-") as temp:
        temp = Path(temp)
        swf_copy = temp / "input.swf"  # engines work on a copy, never on the original
        swf_copy.write_bytes(data)

        jpexs_result = None
        jpexs_thread = None
        want_jpexs = args.engine in ("best", "jpexs") and tool is not None
        if args.engine == "jpexs" and tool is None:
            raise SystemExit("JPEXS not found: use --java/--jpexs-jar or FLASHDEC_JAVA/FLASHDEC_JPEXS_JAR")
        if want_jpexs:
            holder = {}

            def run_jpexs():
                holder["result"] = jpexs.run(tool, swf_copy, temp / "jpexs", resources=args.resources,
                                             timeout=args.timeout, deobfuscate=args.deobfuscate,
                                             memory=args.java_memory)
            jpexs_thread = threading.Thread(target=run_jpexs, daemon=True)
            jpexs_thread.start()

        native_result = None
        if args.engine in ("best", "native"):
            from . import native
            try:
                native_result = native.decompile(data)
                report["header"] = native_result.header
                report["engines"]["native"] = {"seconds": round(native_result.seconds, 3),
                                               "scripts": len(native_result.scripts),
                                               "errors": native_result.errors}
            except Exception as error:
                report["engines"]["native"] = {"error": f"{type(error).__name__}: {error}"}
                log(f"  native: {error}")
        if jpexs_thread is not None:
            jpexs_thread.join()
            jpexs_result = holder.get("result")
            report["engines"]["jpexs"] = {"tool": tool.describe(), "seconds": round(jpexs_result.seconds, 3),
                                          "scripts": len(jpexs_result.scripts), "error": jpexs_result.error}
            if jpexs_result.error:
                report["engines"]["jpexs"]["log_tail"] = jpexs_result.log_tail
                log(f"  jpexs: {jpexs_result.error}")
        elif args.engine == "best":
            report["engines"]["jpexs"] = {"error": "not available"}

        prefer = [args.prefer] + [e for e in ("jpexs", "native") if e != args.prefer]
        choices = merge(native_result.scripts if native_result else [],
                        jpexs_result.scripts if jpexs_result else None, prefer)
        # When JPEXS covered the movie, files only PySWF produced are usually
        # file-internal classes JPEXS already writes inside their host class.
        jpexs_ok = jpexs_result is not None and not jpexs_result.error and jpexs_result.scripts
        for choice in choices:
            folder = "extra/native_only" if (choice.only_native and jpexs_ok) else "scripts"
            _write(out_dir / folder / choice.path, choice.text)

        if native_result and args.pcode:
            for script in native_result.scripts:
                if script.pcode:
                    _write(out_dir / "pcode" / (script.path[:-3] + ".pcode"), script.pcode)

        if args.keep_engines:
            if native_result:
                for script in native_result.scripts:
                    _write(out_dir / "engines/native" / script.path, script.source)
            if jpexs_result:
                for path, text in jpexs_result.scripts.items():
                    _write(out_dir / "engines/jpexs" / path, text)

        if args.resources:
            if jpexs_result and jpexs_result.resource_dir and not jpexs_result.error and args.engine != "native":
                report["resources"] = {"engine": "jpexs",
                                       "counts": _copy_resources(jpexs_result.resource_dir, out_dir / "resources")}
            elif args.engine != "jpexs":
                from . import native, resources
                report["resources"] = {"engine": "native",
                                       **resources.export_all(native.load(swf_copy), out_dir / "resources")}

    extra = [c for c in choices if c.only_native and jpexs_ok]
    main = [c for c in choices if c not in extra]
    by_engine = {}
    for choice in main:
        by_engine[choice.engine] = by_engine.get(choice.engine, 0) + 1
    clean = sum(1 for c in main if (c.scores.get(c.engine) and c.scores[c.engine].value >= 100))
    report["summary"] = {"scripts": len(main), "chosen_by_engine": by_engine, "clean_scripts": clean,
                         "extra_native_only": len(extra),
                         "seconds": round(time.perf_counter() - started, 3)}
    report["scripts"] = [c.as_dict() for c in choices]
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write(out_dir / "REPORT.md", _markdown(report))
    return report


def _markdown(report: dict) -> str:
    s = report["summary"]
    lines = [f"# flashdec: {Path(report['input']).name}", "",
             f"- SHA-256: `{report['sha256']}`",
             f"- Scripts: {s['scripts']} ({s['clean_scripts']} without error markers)",
             f"- Chosen by engine: {', '.join(f'{k}={v}' for k, v in s['chosen_by_engine'].items()) or 'none'}",
             f"- Time: {s['seconds']} s", "", "| Script | Engine | native | jpexs |", "|---|---|---|---|"]
    for item in report["scripts"]:
        cells = []
        for engine in ("native", "jpexs"):
            value = item["scores"].get(engine)
            cells.append("—" if value is None else str(value["value"]))
        engine = item["engine"] + (" (extra)" if item["only_native"] and s["extra_native_only"] else "")
        lines.append(f"| `{item['path']}` | {engine} | {cells[0]} | {cells[1]} |")
    return "\n".join(lines) + "\n"


def _inputs(path: Path):
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix.lower() in INPUT_SUFFIXES and p.is_file())
    return [path]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flashdec-py", description=__doc__)
    p.add_argument("input", type=Path, help="archivo SWF/SWC o carpeta (recursiva)")
    p.add_argument("-o", "--output", type=Path, help="carpeta de salida (por defecto <entrada>_flashdec)")
    p.add_argument("-e", "--engine", choices=ENGINES, default="best",
                   help="best (defecto) = PySWF + JPEXS y se queda el mejor archivo")
    p.add_argument("--prefer", choices=("jpexs", "native"), default="jpexs",
                   help="motor que gana en empate (defecto: jpexs)")
    p.add_argument("--no-resources", dest="resources", action="store_false", help="solo scripts")
    p.add_argument("--no-pcode", dest="pcode", action="store_false", help="no escribir listados P-code")
    p.add_argument("--keep-engines", action="store_true", help="conservar también la salida cruda de cada motor")
    p.add_argument("--deobfuscate", action="store_true", help="activar la desofuscación automática de JPEXS")
    p.add_argument("--timeout", type=int, default=900, help="límite de JPEXS por SWF, segundos (defecto 900)")
    p.add_argument("--java", help="java para JPEXS")
    p.add_argument("--jpexs-jar", help="ffdec-cli.jar / ffdec.jar")
    p.add_argument("--java-memory", default="2g", help="heap de JPEXS (defecto 2g)")
    p.add_argument("-j", "--jobs", type=int, default=1, help="SWF en paralelo al procesar carpetas")
    p.add_argument("--json", action="store_true", help="imprimir el resumen en JSON")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"flashdec-py {__version__}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = (lambda *_: None) if args.quiet else (lambda m: print(m, file=sys.stderr, flush=True))
    if not args.input.exists():
        print(f"no existe: {args.input}", file=sys.stderr)
        return 2
    files = _inputs(args.input)
    if not files:
        print("no se encontraron SWF", file=sys.stderr)
        return 2
    tool = jpexs.locate(args.java, args.jpexs_jar) if args.engine != "native" else None
    if tool:
        log(f"JPEXS: {tool.describe()}")
    elif args.engine == "best":
        log("JPEXS no encontrado: solo motor nativo PySWF")
    output = args.output or args.input.with_name(args.input.stem + "_flashdec")
    single = len(files) == 1 and not args.input.is_dir()

    def one(path: Path):
        target = output if single else output / path.relative_to(args.input).with_suffix("")
        log(f"> {path}")
        try:
            report = decompile_file(path, target, args, tool, log)
            s = report["summary"]
            log(f"  {s['scripts']} scripts ({s['clean_scripts']} limpios) {s['chosen_by_engine']} "
                f"en {s['seconds']} s -> {target}")
            return {"input": str(path), "output": str(target), **s}
        except Exception as error:
            log(f"  ERROR: {type(error).__name__}: {error}")
            return {"input": str(path), "error": f"{type(error).__name__}: {error}"}

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(one, files))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if any("error" in r for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
