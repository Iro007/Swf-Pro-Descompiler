"""Capability registry for development comparisons, not application engines."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
if getattr(sys, "frozen", False):
    from .suite_tools import PROJECT
    ROOT = PROJECT


@dataclass(frozen=True)
class BenchmarkEngine:
    id: str
    name: str
    category: str
    operations: tuple
    languages: tuple
    website: str
    color: str
    license: str


ENGINES = {
    "rust": BenchmarkEngine("rust", "RustSWF", "decompiler", ("source", "disassemble"), ("as2", "as3"), "", "#b66a38", "See RUST SWF/LICENSE and THIRD_PARTY_NOTICES.md"),
    "native": BenchmarkEngine("native","PySWF","decompiler",("source","disassemble"),("as2","as3"),"","#167252","GPL-3.0"),
    "jpexs": BenchmarkEngine("jpexs","JPEXS 26.3.0","decompiler",("source",),("as2","as3"),"https://github.com/jindrapetrik/jpexs-decompiler","#7961b6","GPL-3.0 / LGPL-3.0"),
    "pyflashkit": BenchmarkEngine("pyflashkit","pyflashkit 1.3.0","decompiler",("source","disassemble"),("as3",),"https://github.com/bitalizer/pyflashkit","#448899","MIT; isolated development runtime only"),
    "flare": BenchmarkEngine("flare","Flare 0.6","decompiler",("source",),("as2",),"https://www.nowrap.de/flare.html","#ba781c","Copyrighted freeware; not bundled or adapted"),
    "flasm": BenchmarkEngine("flasm","Flasm 1.62","disassembler",("disassemble",),("as2",),"https://www.nowrap.de/flasm.html","#287dac","BSD-style; retain distribution notices"),
    "rabcdasm": BenchmarkEngine("rabcdasm","RABCDAsm 1.18","disassembler",("disassemble",),("as3",),"https://github.com/CyberShadow/RABCDAsm","#b05277","GPL-3.0-or-later; component exceptions"),
    "flex_swfdump": BenchmarkEngine("flex_swfdump","Apache Flex swfdump 4.16.1","disassembler",("disassemble",),("as2","as3"),"https://github.com/apache/flex-sdk","#526fa8","Apache-2.0; retain SDK LICENSE and NOTICE for dependencies"),
    "sothink": BenchmarkEngine("sothink","Sothink SWF Decompiler","commercial_decompiler",("source",),("as2","as3"),"https://www.sothink.com/product/flashdecompiler/","#47748b","Commercial; no user license/trial installed"),
    "trillix": BenchmarkEngine("trillix","Flash Decompiler Trillix","commercial_decompiler",("source",),("as2","as3"),"https://www.eltima.com/products/flashdecompiler/","#a56349","Commercial; no user license/trial installed"),
    "decompiler_com": BenchmarkEngine("decompiler_com","Decompiler.com","remote_service",("source",),("as2","as3"),"https://www.decompiler.com/swf","#566fab","Remote service; public corpus only with explicit approval"),
    "as3_sorcerer": BenchmarkEngine("as3_sorcerer","AS3 Sorcerer 6.78","restricted_decompiler",("source",),("as3",),"https://www.buraks.com/as3sorcerer/","#865eb3","Restricted free personal non-commercial license advertised by publisher; not open source"),
    "asv": BenchmarkEngine("asv","Action Script Viewer (ASV)","restricted_decompiler",("source",),("as2","as3"),"https://www.buraks.com/asv/","#827e38","Restricted free personal non-commercial license advertised by publisher; not open source"),
    "swfmill": BenchmarkEngine("swfmill","swfmill","xml_converter",("xml",),("as2","as3"),"https://www.swfmill.org/","#638b3f","GPL; XML conversion is not ActionScript decompilation"),
    "swfextract": BenchmarkEngine("swfextract","swfextract (SWFTools)","resource_extractor",("extract",),("as2","as3"),"https://www.swftools.org/","#49907e","GPL; resource extraction is not source decompilation"),
    "flashdec_py": BenchmarkEngine("flashdec_py","flashdec-py 1.0 (PySWF + JPEXS)","hybrid_decompiler",("source",),("as2","as3"),"","#2f8f6f","GPL-3.0; orchestrates PySWF and JPEXS, best file per script"),
    "flashdec_rs": BenchmarkEngine("flashdec_rs","flashdec-rs 1.0 (RustSWF + JPEXS)","hybrid_decompiler",("source",),("as2","as3"),"","#d8622a","GPL-3.0; orchestrates RustSWF and JPEXS, best file per script"),
    "ruffle": BenchmarkEngine("ruffle","Ruffle","runtime",("execute",),("as2","as3"),"https://ruffle.rs/","#d17b35","MIT OR Apache-2.0; independent execution reference, not decompiler"),
}


def tools_config(path=None):
    path = Path(path) if path else ROOT / ".dev-references/extra-tools.json"
    if not path.is_file():
        return {}
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("development_only") is not True:
        raise ValueError("External benchmark tools must be marked development-only")
    return config.get("tools", {})


def checked_tool(engine, config_path=None):
    config = tools_config(config_path).get(engine)
    if not config:
        raise ValueError(f"{engine} is not configured; no result has been measured")
    from .development_paths import normalize_development_config
    manifest = Path(config_path).resolve() if config_path else ROOT / ".dev-references/extra-tools.json"
    config = normalize_development_config(config, ROOT, manifest.parent)
    if not config.get("executable") or not config.get("artifact_sha256") or config["executable"] not in config["artifact_sha256"]:
        raise ValueError("Executable must have a recorded artifact hash before it is run")
    if engine == "flex_swfdump" and (not config.get("library_jar") or config["library_jar"] not in config["artifact_sha256"]):
        raise ValueError("Apache Flex swfdump JAR must have a recorded artifact hash before it is run")
    for filename, expected in config["artifact_sha256"].items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
            raise ValueError("External benchmark artifact hash mismatch: " + filename)
    return config


def default_rust_executable():
    """Prefer the portable peer or current delivery; retain legacy fallbacks.

    Each candidate is confined to its own allowed root after resolution. A
    portable application may be outside the development workspace, but its
    peer must remain beside that application's executable.
    """
    workspace=ROOT.parent.resolve()
    candidates=[]
    if getattr(sys,'frozen',False):
        portable=Path(sys.executable).resolve().parent
        candidates.append((portable / 'rustswf.exe',portable))
    candidates.extend((workspace / relative,workspace) for relative in (
        'RUST SWF/dist/FlashUniversalStudio-Analysis-20261014/rustswf.exe',
        'RUST SWF/dist/FlashUniversalStudio-Parity-20261014/rustswf.exe',
        'RUST SWF/dist/FlashUniversalStudio-Engines/rustswf.exe',
        'RUST SWF/dist/RustSWFStudio/rustswf.exe'))
    for candidate,allowed in candidates:
        executable = candidate.resolve()
        if executable.is_relative_to(allowed) and executable.is_file():
            return executable
    raise ValueError("Rust CLI unavailable in the shared workspace")


def describe_engines(config_path=None):
    inventory_path=ROOT / ".dev-references/competing-tools-inventory.json"
    try:
        inventory=json.loads(inventory_path.read_text(encoding="utf-8")).get("tools",{}) if inventory_path.is_file() else {}
    except (OSError,ValueError):
        inventory={}
    from .workspace_tools import discover_workspace_tools
    discovered = {item["id"]: item for item in discover_workspace_tools(ROOT.parent)["tools"]}
    result = []
    for engine in ENGINES.values():
        record = asdict(engine)
        record["output_scope"]={"native":"Method/action source or instruction listings; class compilation remains partial",
                                "jpexs":"Class/script source reconstructed by pinned reference bridge",
                                "rabcdasm":"Whole ABC structural assembly dump; includes traits and per-class files",
                                "flasm":"AVM1 assembly with timeline/event containers",
                                "flex_swfdump":"SWFX file with AVM1 assembly and AVM2 ABC bytecode listings; canonical unit coverage remains unknown",
                                "pyflashkit":"Pinned public API for every ABC block, class and method; separate scopes with exact bytecode identity checks. Historical cli_first_abc mode remains available.",
                                "flare":"Consolidated legacy ActionScript text, no canonical unit mapping",
                                "flashdec_py":"Hybrid project export: one .as file per class/script, best of PySWF and JPEXS by error-marker score; file-level, no canonical unit mapping",
                                "flashdec_rs":"Hybrid project export: one .as file per class/script, best of RustSWF and JPEXS by error-marker score; file-level, no canonical unit mapping"}.get(engine.id,"Not yet measured in a reproducible local adapter")
        record["configured"] = engine.id == "native"
        if engine.id == "native":
            try:
                metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
                if metadata.get('name') == 'py_swf_editor' and isinstance(metadata.get('version'), str):
                    record.update(version=metadata['version'], version_authority='Project pyproject.toml; source snapshot is identified separately by implementation_sha256')
            except (OSError, ValueError, KeyError):
                record['version_authority'] = 'Project version metadata unavailable; no binary version inferred'
        if engine.id == "rust":
            try:
                if config_path and tools_config(config_path).get("rust"):
                    config = checked_tool("rust", config_path)
                    record.update(configured=True, version=config.get('version'), executable=config["executable"], artifact_sha256=list(config["artifact_sha256"].values()),
                                  version_authority='Pinned executable configuration; local Rust source version is not substituted')
                else:
                    executable = default_rust_executable()
                    record.update(configured=True, executable=str(executable), artifact_sha256=[hashlib.sha256(executable.read_bytes()).hexdigest()])
                record["output_scope"] = "Partial method/action source and instruction listings; no ActionScript compiler or verified behavior equivalence"
                record["binary_source_match"] = "not_verified"
            except (OSError, ValueError, KeyError) as error:
                record["setup_diagnostic"] = str(error)
        if engine.id=="jpexs":
            from .benchmarks import reference_command
            from .benchmark_process import WorkerError
            try:
                reference_command(ROOT / ".dev-references/toolchain.json")
                record.update(configured=True, version='26.3.0', commit='b2ef2d3d3f44291a4bdfa899c986bc39a89fa6aa',
                              version_authority='Reference command validated the pinned 26.3.0 toolchain and all recorded artifact hashes')
            except (OSError, ValueError, KeyError, WorkerError) as error:
                record["configured"] = False
                record["setup_diagnostic"] = str(error)
        if engine.id in ("flasm","flare","rabcdasm","pyflashkit","flex_swfdump","flashdec_py","flashdec_rs"):
            try:
                config = checked_tool(engine.id,config_path)
                record.update(configured=True,version=config.get("version"),artifact_sha256=list(config["artifact_sha256"].values()))
                record['provenance'] = {key:config[key] for key in ('archive','publisher_page','hash_authority','license','commit') if key in config}
            except (OSError,ValueError,KeyError) as error:
                record["setup_diagnostic"] = str(error)
        if engine.id in ("sothink","trillix"):
            record["setup_diagnostic"]="Pendiente de licencia y adaptador reproducible. El usuario confirmó que no dispone de una instalación autorizada."
        if engine.id in ("as3_sorcerer","asv"):
            record["setup_diagnostic"]="Candidato con licencia restringida; instalación y automatización aún no verificadas. No hay mediciones."
        if engine.id in ("swfmill","swfextract","ruffle"):
            record["setup_diagnostic"]="Referencia de otra operación. No se ejecutó ni se mezcla con la recuperación de fuente ActionScript."
        if engine.id=="trillix":
            record["distribution_aliases"]=["https://flash-swf-decompiler.uptodown.com/windows"]
            record["alias_note"]="Uptodown lists Eltima Software's Flash SWF Decompiler; do not count a distribution portal as an independent engine."
        if engine.id=="sothink":
            record["distribution_aliases"]=["https://www.shankejingling.com/"]
            record["alias_note"]="Regional Sothink/硕思闪客精灵 product-family candidate; binary/version equivalence not verified. Not counted as another independent engine."
        if engine.id=="decompiler_com":
            record["setup_diagnostic"]="Servicio separado de los procesos locales; requiere prueba observada y consentimiento por corpus. No se mide su RAM remota."
            if (ROOT / "reports/decompiler-com-evidence.json").is_file():
                record["observed"]=True
                record["setup_diagnostic"]="Dos SWF públicos observados con permiso: salida descargada, sin compilación, equivalencia ni versión de motor verificada. Sin subida automática."
        if engine.id in inventory:
            item=inventory[engine.id]
            record["local_availability"]=item["status"]
            record["installer_version"]=item.get("version")
            record["installer_sha256"]=item.get("sha256")
            record["signature_status"]=item.get("signature_status")
            if item["status"]=="installed_pending_verification":
                record.update(installed_version=item.get("installed_version"),installed_sha256=item.get("installed_sha256"),license_state=item.get("license_state","not_observed"))
                record["setup_diagnostic"]="Instalación detectada, versión "+item["installed_version"]+". Exportación, restricciones de prueba y licencia efectiva aún no verificadas; no hay mediciones reproducibles."
                if item.get("documented_trial_limit"):
                    record["documented_trial_limit"]=item["documented_trial_limit"]
                    record["trial_limit_source"]=item.get("trial_limit_source")
                    record["setup_diagnostic"]+=" "+item["documented_trial_limit"]
            if item["status"]=="blocked_by_security":
                record["setup_diagnostic"]="Descarga bloqueada por Defender: "+item["threat_name"]+". No ejecutado; falso positivo no demostrado. No se omite la protección."
                if item.get("download_completed") is False:
                    record["setup_diagnostic"]+=" La descarga no se completó y el instalador no está disponible localmente; los complementos no sustituyen al programa."
            elif item["status"]=="installer_available":
                record["setup_diagnostic"]="Instalador disponible, versión "+item["version"]+". No se ejecutó, instaló ni aceptó licencia; todavía sin medición."
        local = discovered.get(engine.id)
        if local:
            record["workspace_availability"] = local["status"]
            record["local_candidates"] = local["candidates"]
        result.append(record)
    return result


def external_command(engine, config_path=None):
    config = checked_tool(engine,config_path)
    prefix = [sys.executable,"external-benchmark"] if getattr(sys,"frozen",False) else [sys.executable,"-m","py_swf.benchmark_external_worker"]
    return [*prefix,"--engine",engine,
            "--config",str(Path(config_path).resolve() if config_path else ROOT / ".dev-references/extra-tools.json")]
