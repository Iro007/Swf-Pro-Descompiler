"""Command line interface for automation and batch workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .decompile.backends import compare_decompilation, decompile_with_fallback
from .engines import EngineOperationError, NativePythonEngine
from .resources import export_image, find_jpeg_tables
from .swf_parser import SWFFile
from .storage import write_swf


def _load(path: Path) -> SWFFile:
    swf = SWFFile()
    swf.read_file(path)
    return swf


def cmd_inspect(args) -> int:
    swf = _load(Path(args.input))
    print(json.dumps({
        "filename": str(args.input),
        "signature": swf.signature,
        "version": swf.version,
        "frame_rate": swf.frame_rate,
        "frame_count": swf.frame_count,
        "rect": swf.rect,
        "tags": [
            {"index": i, "code": tag.tag_type, "name": tag.name, "size": len(tag.data), "char_id": tag.char_id}
            for i, tag in enumerate(swf.tags)
        ],
    }, ensure_ascii=False, indent=2))
    return 0


def _script_tags(swf):
    return [(i, tag) for i, tag in enumerate(swf.tags) if tag.tag_type in (12, 59, 72, 82)]


def cmd_decompile(args) -> int:
    path = Path(args.input)
    swf = _load(path)
    results = []
    for index, tag in _script_tags(swf):
        if args.tag is not None and index != args.tag:
            continue
        if args.engine == "compare":
            result = compare_decompilation(tag, swf.save_bytes(), path.name)
        else:
            result = decompile_with_fallback(tag, swf.save_bytes(), path.name, args.engine).as_dict()
        result["tag_index"] = index
        results.append(result)
    if not results:
        raise ValueError("No script tags matched")
    output = json.dumps(results[0] if args.tag is not None else results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


def cmd_export_images(args) -> int:
    source = Path(args.input)
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    swf = _load(source)
    tables = find_jpeg_tables(swf.tags)
    count = 0
    for index, tag in enumerate(swf.tags):
        if tag.tag_type not in {6, 20, 21, 35, 36, 90}:
            continue
        data, extension = export_image(tag, jpeg_tables=tables)
        if data is None:
            continue
        char_id = tag.char_id if tag.char_id is not None else index
        (destination / f"{char_id}_{index}.{extension}").write_bytes(data)
        count += 1
    print(json.dumps({"exported": count, "output": str(destination)}))
    return 0


def cmd_list_tags(args) -> int:
    swf = _load(Path(args.input))
    print(json.dumps([
        {"index": i, "code": tag.tag_type, "name": tag.name, "size": len(tag.data), "char_id": tag.char_id}
        for i, tag in enumerate(swf.tags)
    ], ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args) -> int:
    path = Path(args.input)
    result = NativePythonEngine().validate(path.read_bytes())
    print(json.dumps({"filename": str(path), **result}, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


def cmd_compare(args) -> int:
    args.engine = "compare"
    return cmd_decompile(args)


def cmd_replace(args) -> int:
    source = Path(args.input)
    destination = Path(args.output) if args.output else source.with_name(source.stem + ".edited.swf")
    if destination.resolve() == source.resolve():
        raise ValueError("Refusing to overwrite the source SWF; pass a different --output")
    data = source.read_bytes()
    replacement = Path(args.resource).read_bytes()
    result = NativePythonEngine().replace_resource(data, args.tag, replacement, Path(args.resource).suffix.lstrip("."))
    validation = NativePythonEngine().validate(result)
    if not validation["valid"]:
        raise ValueError("Replacement produced an invalid SWF: " + "; ".join(validation["errors"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_swf(destination, result, original=source)
    print(json.dumps({"input": str(source), "output": str(destination), "valid": True}, ensure_ascii=False))
    return 0


def cmd_edit_script(args) -> int:
    source = Path(args.input)
    patch = Path(args.source or args.pcode).read_text(encoding="utf-8")
    engine = NativePythonEngine()
    data = source.read_bytes()
    updated = engine.compile_source(data, args.tag, patch, args.body) if args.source else engine.assemble(data, args.tag, patch, args.body)
    destination = args.output or source.with_name(source.stem + ".edited.swf")
    validation = write_swf(destination, updated, original=source)
    print(json.dumps({"engine": "native", "output": str(destination), "validation": validation}, ensure_ascii=False, indent=2))
    return 0


def _export_one(path: Path, destination: Path) -> dict:
    swf = _load(path)
    destination.mkdir(parents=True, exist_ok=True)
    exported = 0
    errors = []
    tables = find_jpeg_tables(swf.tags)
    for index, tag in enumerate(swf.tags):
        if tag.tag_type not in {6, 20, 21, 35, 36, 90}:
            continue
        try:
            data, extension = export_image(tag, jpeg_tables=tables)
            if data is None:
                raise ValueError("could not decode image")
            char_id = tag.char_id if tag.char_id is not None else index
            (destination / f"{char_id}_{index}.{extension}").write_bytes(data)
            exported += 1
        except Exception as exc:
            errors.append({"tag": index, "error": str(exc)})
    return {"input": str(path), "images": exported, "errors": errors}


def cmd_export(args) -> int:
    if args.type != "images":
        raise ValueError("The native CLI currently supports --type images; use the HTTP API for other resources")
    result = _export_one(Path(args.input), Path(args.output))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["errors"] else 1


def cmd_batch(args) -> int:
    source = Path(args.input)
    destination = Path(args.output)
    if not source.is_dir():
        raise ValueError(f"Input directory does not exist: {source}")
    reports = []
    for path in sorted(source.rglob("*.swf")):
        relative = path.relative_to(source)
        try:
            report = _export_one(path, destination / relative.with_suffix(""))
            report["valid"] = True
        except Exception as exc:
            report = {"input": str(path), "valid": False, "error": str(exc)}
        reports.append(report)
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "pyswf-batch-report.json"
    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"files": len(reports), "report": str(report_path)}, ensure_ascii=False))
    return 0 if all(item.get("valid") and not item.get("errors") for item in reports) else 1


def cmd_benchmark(args) -> int:
    from .benchmarks import run_benchmark
    report = run_benchmark(args.corpus, profile=args.profile, engines=tuple(e.strip() for e in args.engines.split(',')), output=args.output, seed=args.seed, limit=args.limit, config_path=args.reference_config,tool_config=args.tool_config,operation=args.operation)
    print(json.dumps({"completed_files": report["completed_files"], "scheduled_files": report["scheduled_files"], "claims": report["claims"], "output": str(args.output or "reports/decompiler-benchmark")}, ensure_ascii=False, indent=2))
    return 1 if report.get("cancelled") or report["completed_files"] != report["scheduled_files"] or any(r["failures"] for c in report["cases"] for r in c["engines"]) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyswf", description="SWF inspector and decompiler")
    sub = parser.add_subparsers(dest="command", required=True)
    classes=sub.add_parser("compile-classes",help="compile the bounded native AS3 class-source tier into a SWF")
    classes.add_argument("source",type=Path)
    classes.add_argument("--output",type=Path,required=True)
    classes.add_argument("--compression",choices=("FWS","CWS","ZWS"),default="CWS")
    def cmd_classes(args):
        from .compiler.class_compiler import compile_class_program,class_program_swf
        from .storage import write_swf
        compiled=compile_class_program(args.source.read_text(encoding="utf-8"))
        write_swf(args.output,class_program_swf(compiled,args.compression),original=args.source)
        print(json.dumps({"classes":compiled.class_names,"output":str(args.output),"diagnostics":compiled.diagnostics},ensure_ascii=False,indent=2))
        return 0
    classes.set_defaults(func=cmd_classes)
    benchmark = sub.add_parser("benchmark", help="development-only decompilation benchmarks with quality diagnostics")
    benchmark.add_argument("corpus", type=Path)
    benchmark.add_argument("--profile", choices=("smoke", "full"), default="smoke")
    benchmark.add_argument("--engines", default="native,jpexs")
    benchmark.add_argument("--operation",choices=("source","disassemble","extract","xml"),default="source")
    benchmark.add_argument("--tool-config",type=Path)
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument("--limit", type=int)
    benchmark.add_argument("--seed", type=int, default=1729)
    benchmark.add_argument("--reference-config", type=Path)
    benchmark.set_defaults(func=cmd_benchmark)
    catalog = sub.add_parser("benchmark-catalog", help="import measured local/cloud campaigns into a reproducible SQLite catalog")
    catalog.add_argument("sources", nargs="*", type=Path, help="benchmark.json reports or measured cloud ZIP bundles")
    catalog.add_argument("--database", type=Path, default=Path(__file__).resolve().parents[2] / "reports/benchmark-catalog/benchmark.sqlite")
    catalog.add_argument("--cloud-sha256", help="expected SHA-256 for the cloud ZIP source")
    catalog.add_argument("--html", type=Path, help="export a standalone measured-campaign dashboard")
    catalog.add_argument("--summary", action="store_true", help="include catalog summary after import")
    def cmd_catalog(args):
        from .benchmark_catalog import import_local_report, import_cloud_bundle, catalog_summary, export_catalog_html
        imports = []
        for source in args.sources:
            if source.suffix.lower() == ".zip":
                imports.append(import_cloud_bundle(args.database, source, expected_sha256=args.cloud_sha256))
            elif source.suffix.lower() == ".json":
                imports.append(import_local_report(args.database, source))
            else:
                raise ValueError("Las fuentes del catálogo deben ser informes JSON o paquetes ZIP")
        result = {"database": str(args.database.resolve()), "imports": imports}
        if args.html:
            export_catalog_html(args.database, args.html)
            result["html_path"] = str(args.html.resolve())
        if args.summary or not args.sources:
            result["catalog"] = catalog_summary(args.database)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    catalog.set_defaults(func=cmd_catalog)
    audit=sub.add_parser("audit-corpus",help="complete fresh source inventory comparison, not a speed benchmark")
    audit.add_argument("corpus",type=Path)
    audit.add_argument("--output",type=Path)
    audit.add_argument("--engines",default="native,jpexs")
    audit.add_argument("--limit",type=int)
    audit.add_argument("--reference-config",type=Path)
    audit.add_argument("--compile-documents",action="store_true",help="audit every enumerated compilation target, including protected and failed units")
    audit.add_argument("--compilation-budget",type=float,default=300,help="maximum compilation work seconds per file/engine, with unattempted targets retained")
    def cmd_audit(args):
        from .corpus_audit import run_audit
        report=run_audit(args.corpus,output=args.output,engines=tuple(args.engines.split(",")),limit=args.limit,config_path=args.reference_config,compile_documents=args.compile_documents,compilation_budget_seconds=args.compilation_budget)
        print(json.dumps(report["audit_summary"],ensure_ascii=False,indent=2))
        compilation=report["audit_summary"].get("compilation_by_engine",{}).values()
        return int(bool(report.get("cancelled") or report["uncompleted_files"] or report["audit_summary"]["operation_failure_files"] or any(e["failed"] or e["attempted"]<e["eligible"] or e["compiled"]!=e["validated_reopened"] for e in compilation)))
    audit.set_defaults(func=cmd_audit)
    imported=sub.add_parser("benchmark-import",help="import public-corpus GUI source exports as untimed, unmapped observations")
    imported.add_argument("--engine",choices=("sothink","trillix","as3_sorcerer"),required=True)
    imported.add_argument("--input",type=Path,required=True)
    imported.add_argument("--exports",type=Path,required=True)
    imported.add_argument("--output",type=Path,required=True)
    imported.add_argument("--manifest",type=Path,default=Path(__file__).resolve().parents[1] / "Competing_tools/benchmark_inputs/manifest.json")
    imported.add_argument("--edition",choices=("unknown","trial","licensed"),default="unknown")
    def cmd_import(args):
        from .benchmark_import import import_exports
        report=import_exports(args.engine,args.input,args.exports,args.output,args.manifest,edition=args.edition)
        print(json.dumps(report["cases"][0]["engines"][0]["manual_export_observation"],ensure_ascii=False,indent=2))
        return 0
    imported.set_defaults(func=cmd_import)
    tools = sub.add_parser("benchmark-tools",help="list development comparison tools and their actual capabilities")
    def show_tools(args):
        from .benchmark_engines import describe_engines
        print(json.dumps({"tools":describe_engines(args.tool_config)},ensure_ascii=False,indent=2))
        return 0
    tools.add_argument("--tool-config",type=Path)
    tools.set_defaults(func=show_tools)
    workspace = sub.add_parser("workspace-tools", help="discover local comparison tools beside Python and Rust without running them")
    workspace.add_argument("--root", type=Path)
    workspace.add_argument("--output", type=Path)
    def show_workspace(args):
        from .workspace_tools import discover_workspace_tools
        report = discover_workspace_tools(args.root)
        from .benchmark_engines import describe_engines
        report["benchmark_engines"] = describe_engines()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    workspace.set_defaults(func=show_workspace)
    inspect = sub.add_parser("inspect", help="inspect SWF metadata and tags")
    inspect.add_argument("input", type=Path)
    inspect.set_defaults(func=cmd_inspect)
    decompile = sub.add_parser("decompile", help="decompile all or one script tag")
    decompile.add_argument("input", type=Path)
    decompile.add_argument("--tag", type=int)
    decompile.add_argument("--engine", choices=("auto", "native", "jpexs", "compare"), default="native")
    decompile.add_argument("--output", type=Path)
    decompile.set_defaults(func=cmd_decompile)
    compare = sub.add_parser("compare", help="compare native and JPEXS decompilation")
    compare.add_argument("input", type=Path)
    compare.add_argument("--tag", type=int)
    compare.add_argument("--output", type=Path)
    compare.set_defaults(func=cmd_compare)
    tags = sub.add_parser("list-tags", help="list SWF tags as JSON")
    tags.add_argument("input", type=Path)
    tags.set_defaults(func=cmd_list_tags)
    validate = sub.add_parser("validate", help="validate an SWF without modifying it")
    validate.add_argument("input", type=Path)
    validate.set_defaults(func=cmd_validate)
    edit = sub.add_parser("edit-script", help="compile source or assemble P-code with PySWF")
    edit.add_argument("input", type=Path)
    edit.add_argument("--tag", type=int, required=True)
    edit.add_argument("--body", type=int, default=0)
    patch = edit.add_mutually_exclusive_group(required=True)
    patch.add_argument("--source", type=Path)
    patch.add_argument("--pcode", type=Path)
    edit.add_argument("--output", type=Path)
    edit.set_defaults(func=cmd_edit_script)
    export = sub.add_parser("export", help="export resources")
    export.add_argument("input", type=Path)
    export.add_argument("output", type=Path)
    export.add_argument("--type", choices=("images",), default="images")
    export.set_defaults(func=cmd_export)
    replace = sub.add_parser("replace", help="replace an image tag and write a new SWF")
    replace.add_argument("input", type=Path)
    replace.add_argument("--tag", type=int, required=True)
    replace.add_argument("--resource", type=Path, required=True)
    replace.add_argument("--output", type=Path)
    replace.set_defaults(func=cmd_replace)
    batch = sub.add_parser("batch", help="export bitmap resources from a directory")
    batch.add_argument("input", type=Path)
    batch.add_argument("output", type=Path)
    batch.set_defaults(func=cmd_batch)
    images = sub.add_parser("export-images", help="export bitmap tags")
    images.add_argument("input", type=Path)
    images.add_argument("output", type=Path)
    images.set_defaults(func=cmd_export_images)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
