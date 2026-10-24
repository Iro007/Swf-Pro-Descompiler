"""One task per benchmark category other than decompilers. Each returns rows of
the same shape: {engine, family, case, ok, detail, reason, seconds}. ok is True/False, or
None when the task does not apply (the reason says why). seconds is the time of the tool's
own step (not of the Ruffle check that judges it), None when the tool did not finish.

compilers      compile the family's own source; Ruffle must print the expected trace
disassemblers  disassemble -> assemble -> Ruffle prints the original's trace
converters     SWF -> XML -> SWF (swfmill), same trace afterwards
extractors     every resource tag extracted by swfextract decodes
"""
from __future__ import annotations

import re
import shutil
import tempfile
import time
from pathlib import Path

from py_swf.benchmark_engines import checked_tool
from py_swf.benchmark_resource_tools import extract, xml_roundtrip
from py_swf.suite_tools import run_tool
from py_swf.swf_parser import SWFFile, SWFTag
from py_swf.validation import validate_swf_bytes
from run_semantic_benchmarks import runtime_check


def row(engine, family, case, ok=None, detail="", reason="", seconds=None):
    return {"engine": engine, "family": family, "case": case, "ok": ok, "detail": detail, "reason": reason,
            "seconds": None if seconds is None else round(seconds, 3)}


def tool(name, ctx):
    """Executable of a hash-checked tool, or (None, why not)."""
    try:
        return Path(checked_tool(name, ctx["tool_config"])["executable"]), None
    except (ValueError, OSError, KeyError) as error:
        return None, "no instalado: " + str(error).splitlines()[0][:160]


def same_trace(original, candidate, case, ctx):
    """Ruffle prints the case's expected trace for both SWFs? -> (ok, reason)."""
    valid = validate_swf_bytes(candidate)
    if not valid["valid"]:
        return False, "SWF inválido: " + str(valid.get("errors") or valid)[:160]
    with tempfile.TemporaryDirectory(prefix="fs-bench-") as folder:
        folder = Path(folder)
        built = folder / "candidate.swf"
        built.write_bytes(candidate)
        result = runtime_check(original, built, {"expected_trace": case["expected_trace"], "ticks": 1},
                               ctx["runtime"], folder / "run")
    if result.get("status") == "passed_bounded_trace":
        return True, ""
    return False, result.get("diagnostic") or {"behavior_mismatch": "la traza difiere del original",
                                              "runtime_reference_unsupported": "Ruffle no reproduce ni el original"}.get(result.get("status"), str(result.get("status")))


# --- converters -------------------------------------------------------------

def converters(case, original, ctx):
    executable, why = tool("swfmill", ctx)
    if not executable:
        return [row("swfmill", case["family_id"], case["relative"], reason=why)]
    data = original.read_bytes()
    with tempfile.TemporaryDirectory(prefix="fs-swfmill-") as folder:
        folder = Path(folder)
        (folder / "in.swf").write_bytes(data)
        started = time.perf_counter()
        try:
            run_tool([str(executable), "swf2xml", "in.swf", "m.xml"], cwd=folder, timeout=60)
            run_tool([str(executable), "xml2swf", "m.xml", "out.swf"], cwd=folder, timeout=60)
            rebuilt = (folder / "out.swf").read_bytes()
        except (ValueError, OSError) as error:
            return [row("swfmill", case["family_id"], case["relative"], False, reason=str(error).splitlines()[0][:200])]
        seconds = time.perf_counter() - started
    units, _ = xml_roundtrip(str(executable), data)
    same = sum(unit["generation"] == "available" for unit in units)
    ok, reason = same_trace(original, rebuilt, case, ctx)
    return [row("swfmill", case["family_id"], case["relative"], ok, "%d/%d tags idénticos" % (same, len(units)), reason, seconds)]


# --- extractors -------------------------------------------------------------

def extractors(case, original, ctx):
    executable, why = tool("swfextract", ctx)
    if not executable:
        return [row("swfextract", case["family_id"], case["relative"], reason=why)]
    started = time.perf_counter()
    units, _ = extract(str(executable), original.read_bytes())
    seconds = time.perf_counter() - started
    if not units:
        return [row("swfextract", case["family_id"], case["relative"], reason="el SWF no contiene recursos extraíbles")]
    valid = sum(unit["generation"] == "available" for unit in units)
    reasons = sorted({unit["diagnostic"] for unit in units if unit.get("diagnostic")})
    return [row("swfextract", case["family_id"], case["relative"], valid == len(units), "%d/%d" % (valid, len(units)), "; ".join(reasons)[:200], seconds)]


# --- disassemblers: round trip ---------------------------------------------

def _flasm(executable, original, data, case, ctx):
    with tempfile.TemporaryDirectory(prefix="fs-flasm-") as folder:
        folder = Path(folder)
        movie = folder / "input.swf"
        movie.write_bytes(data)
        try:
            listing = run_tool([str(executable), "-d", str(movie)], cwd=folder)
            pattern = r"(?im)^\s*movie\s+(['\"])(.*?)\1"
            text = re.sub(pattern, lambda _: "movie '" + movie.as_posix() + "'", listing, count=1)
            (folder / "input.flm").write_text(text, encoding="utf-8")
            run_tool([str(executable), "-a", str(folder / "input.flm")], cwd=folder)
        except (ValueError, OSError) as error:
            return None, str(error).splitlines()[0][:200]
        return movie.read_bytes(), ""


def _rabcdasm(executable, original, data, case, ctx):
    movie = SWFFile()
    movie.read_bytes(data)
    assembler = executable.with_name("rabcasm.exe")
    if not assembler.is_file():
        return None, "falta rabcasm junto a rabcdasm"
    with tempfile.TemporaryDirectory(prefix="fs-rabcdasm-") as folder:
        folder = Path(folder)
        for index, tag in enumerate(movie.tags):
            if tag.tag_type not in (72, 82):
                continue
            flags, name, abc = tag.parse_doabc()
            work = folder / str(index)
            work.mkdir()
            (work / "input.abc").write_bytes(abc)
            try:
                run_tool([str(executable), "input.abc"], cwd=work)
                main = next((work / "input").glob("*.main.asasm"))
                run_tool([str(assembler), str(main)], cwd=work)
                rebuilt = next((work / "input").glob("*.abc")).read_bytes()
            except (ValueError, OSError, StopIteration) as error:
                return None, "ABC %d: %s" % (index, str(error).splitlines()[0][:160])
            payload = flags.to_bytes(4, "little") + name.encode("utf-8") + b"\0" + rebuilt if tag.tag_type == 82 else rebuilt
            movie.tags[index] = SWFTag(tag.tag_type, payload)
        return movie.save_bytes(), ""


def disassemblers(case, original, ctx):
    data = original.read_bytes()
    family, relative = case["family_id"], case["relative"]
    rows = []
    for name, language, task in (("flasm", "as2", _flasm), ("rabcdasm", "as3", _rabcdasm)):
        if case["language"] != language:
            rows.append(row(name, family, relative, reason="no lee " + case["language"].upper()))
            continue
        executable, why = tool(name, ctx)
        if not executable:
            rows.append(row(name, family, relative, reason=why))
            continue
        started = time.perf_counter()
        rebuilt, reason = task(executable, original, data, case, ctx)
        seconds = time.perf_counter() - started
        if rebuilt is None:
            rows.append(row(name, family, relative, False, reason=reason))
            continue
        ok, reason = same_trace(original, rebuilt, case, ctx)
        rows.append(row(name, family, relative, ok, reason=reason, seconds=seconds))
    for name in ("flex_swfdump", "pyflashkit", "native", "rust", "js", "go", "asm"):
        rows.append(row(name, family, relative, reason="lista pero no reensambla: no hay ida y vuelta"))
    return rows


# --- compilers --------------------------------------------------------------

def compilers(family, project, ctx):
    """family: manifest family; project: its source folder inside the corpus."""
    entry = project / family["entry"]
    rows = []
    case = {"expected_trace": family["expected_trace"]}
    original = ctx["original_of"](family["family_id"])

    def done(name, built, reason="", seconds=None):
        if built is None:
            rows.append(row(name, family["family_id"], family["entry"], False, reason=reason))
        else:
            ok, why = same_trace(original, built, case, ctx)
            rows.append(row(name, family["family_id"], family["entry"], ok, reason=why, seconds=seconds))

    def skip(name, reason):
        rows.append(row(name, family["family_id"], family["entry"], reason=reason))

    registry = ctx["registry"]
    suffix = entry.suffix.lower()
    with tempfile.TemporaryDirectory(prefix="fs-compile-") as folder:
        out = Path(folder) / "out.swf"

        def run(command, env=None):
            started = time.perf_counter()
            try:
                run_tool(command, cwd=folder, env=env, timeout=110)
                return out.read_bytes(), "", time.perf_counter() - started
            except (ValueError, OSError) as error:
                return None, str(error).splitlines()[0][:200]

        # Flex: AS3 sources
        if family["language"] == "as3" and suffix == ".as":
            try:
                compiler = Path(registry.executable("flex"))
                frameworks = compiler.parent.parent / "frameworks"
                env = registry.environment()
                env["PLAYERGLOBAL_HOME"] = str(frameworks / "libs/player")
                done("flex", *run([registry.executable("java"), "-Xmx1024m", "-jar", str(compiler), "+flexlib=" + str(frameworks),
                                   "+playerglobalHome=" + str(frameworks / "libs/player"), "-target-player=32.0", "-debug=false",
                                   "-omit-trace-statements=false", "-output=" + str(out), "-source-path=" + str(project), str(entry)], env))
            except (OSError, ValueError) as error:
                skip("flex", "no instalado: " + str(error).splitlines()[0][:160])
        else:
            skip("flex", "no lee " + ("AS2" if family["language"] == "as2" else suffix or "este proyecto"))
        # Rascal: AS2
        if family["language"] == "as2":
            rascal = ctx["rascal"]
            if rascal.is_file():
                done("rascal", *run([str(rascal), str(entry), "--output", str(out), "--swf-version", "15", "--frame-rate", "30"]))
            else:
                skip("rascal", "no instalado")
        else:
            skip("rascal", "solo lee AS2")
        # Haxe: .hx sources
        if suffix == ".hx":
            try:
                done("haxe", *run([registry.executable("haxe"), "-cp", str(project), "-main", entry.stem, "--swf", str(out), "--swf-version", "11.8"],
                                  registry.environment()))
            except (OSError, ValueError) as error:
                skip("haxe", "no instalado: " + str(error).splitlines()[0][:160])
        else:
            skip("haxe", "solo lee Haxe")
        # PySWF: native class compiler for AS3
        if family["language"] == "as3" and suffix == ".as":
            try:
                from py_swf.compiler.class_compiler import class_program_swf, compile_class_program
                started = time.perf_counter()
                sources = {p.relative_to(project).as_posix(): p.read_text(encoding="utf-8") for p in project.rglob("*.as")
                           if "builds" not in p.relative_to(project).parts and p != entry}
                compiled = compile_class_program(entry.read_text(encoding="utf-8"), sources=sources)
                main = next((n for n in compiled.class_names if n.rsplit(".", 1)[-1] == entry.stem), None)
                if main is None:
                    raise ValueError("sin clase principal " + entry.stem)
                built = class_program_swf(compiled, document_class=main)
                done("native", built, "", time.perf_counter() - started)
            except Exception as error:  # the native compiler rejects what it cannot handle
                done("native", None, str(error).splitlines()[0][:200] if str(error) else type(error).__name__)
        else:
            skip("native", "el compilador propio solo lee clases AS3 (.as)")
    return rows
