"""Capability registry for development comparisons, not application engines."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib

from . import paths

WORKSPACE = paths.WORKSPACE
REFERENCES = paths.REFERENCES
PROJECT = paths.PROJECT


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


def engines_directory():
    """engines/ holds one folder per engine with its engine.json manifest."""
    override = os.environ.get("FLASH_ENGINES_DIR")
    return Path(override) if override else paths.ENGINES


def load_engines(directory=None):
    """Read every engines/*/engine.json; a manifest is the only place an engine is declared."""
    directory = Path(directory) if directory else engines_directory()
    manifests = []
    for path in sorted(directory.glob("*/engine.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = {"id", "name", "category", "operations", "languages"} - data.keys()
        if missing:
            raise ValueError(f"{path}: missing {sorted(missing)}")
        manifests.append(data)
    manifests.sort(key=lambda m: (m.get("order", 999), m["id"]))
    result = {}
    for m in manifests:
        if m["id"] in result:
            raise ValueError("Duplicate engine id: " + m["id"])
        result[m["id"]] = BenchmarkEngine(m["id"], m["name"], m["category"], tuple(m["operations"]), tuple(m["languages"]),
                                          m.get("website", ""), m.get("color", "#566"), m.get("license", ""))
    return result


ENGINES = load_engines()


def tools_config(path=None):
    path = Path(path) if path else REFERENCES / "extra-tools.json"
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
    manifest = Path(config_path).resolve() if config_path else REFERENCES / "extra-tools.json"
    config = normalize_development_config(config, WORKSPACE, manifest.parent)
    if not config.get("executable") or not config.get("artifact_sha256") or config["executable"] not in config["artifact_sha256"]:
        raise ValueError("Executable must have a recorded artifact hash before it is run")
    if engine == "flex_swfdump" and (not config.get("library_jar") or config["library_jar"] not in config["artifact_sha256"]):
        raise ValueError("Apache Flex swfdump JAR must have a recorded artifact hash before it is run")
    for filename, expected in config["artifact_sha256"].items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
            raise ValueError("External benchmark artifact hash mismatch: " + filename)
    return config


RUST_EXECUTABLE_CANDIDATES = ('dist/FlashUniversalStudio/rustswf.exe',
                              'target/x86_64-pc-windows-gnu/release/rustswf.exe')

PEER_EXECUTABLE_CANDIDATES = {
    "rust": RUST_EXECUTABLE_CANDIDATES,
    "js": ('engines/jsswf/dist/jsswf.exe', 'dist/FlashUniversalStudio/jsswf.exe'),
    "go": ('engines/goswf/dist/goswf.exe', 'dist/FlashUniversalStudio/goswf.exe'),
    "asm": ('engines/asmswf/dist/asmswf.exe', 'dist/FlashUniversalStudio/asmswf.exe'),
}


def default_peer_executable(engine="rust"):
    workspace = WORKSPACE.resolve()
    candidates = []
    exe_name = {"rust": "rustswf.exe", "js": "jsswf.exe", "go": "goswf.exe", "asm": "asmswf.exe"}.get(engine, f"{engine}swf.exe")
    if getattr(sys, 'frozen', False):
        portable = Path(sys.executable).resolve().parent
        candidates.append((portable / exe_name, portable))
    candidates.extend((workspace / relative, workspace) for relative in PEER_EXECUTABLE_CANDIDATES.get(engine, ()))
    for candidate, allowed in candidates:
        executable = candidate.resolve()
        if executable.is_relative_to(allowed) and executable.is_file():
            return executable
    raise ValueError(f"{engine.upper()} CLI unavailable in the shared workspace")


def default_rust_executable():
    """Prefer the portable peer or current delivery; retain legacy fallbacks.

    Each candidate is confined to its own allowed root after resolution. A
    portable application may be outside the development workspace, but its
    peer must remain beside that application's executable.
    """
    return default_peer_executable("rust")


def describe_engines(config_path=None):
    inventory_path=REFERENCES / "competing-tools-inventory.json"
    try:
        inventory=json.loads(inventory_path.read_text(encoding="utf-8")).get("tools",{}) if inventory_path.is_file() else {}
    except (OSError,ValueError):
        inventory={}
    from .workspace_tools import discover_workspace_tools
    discovered = {item["id"]: item for item in discover_workspace_tools(WORKSPACE)["tools"]}
    result = []
    for engine in ENGINES.values():
        record = asdict(engine)
        record["output_scope"]={"native":"Method/action source or instruction listings; class compilation remains partial",
                                "jpexs":"Class/script source reconstructed by pinned reference bridge",
                                "rabcdasm":"Whole ABC structural assembly dump; includes traits and per-class files",
                                "flasm":"AVM1 assembly with timeline/event containers",
                                "flex_swfdump":"SWFX file with AVM1 assembly and AVM2 ABC bytecode listings; canonical unit coverage remains unknown",
                                "pyflashkit":"Pinned public API for every ABC block, class and method; separate scopes with exact bytecode identity checks. Historical cli_first_abc mode remains available.",
                                "flare":"Consolidated legacy ActionScript text, no canonical unit mapping"}.get(engine.id,"Not yet measured in a reproducible local adapter")
        record["configured"] = engine.id == "native"
        if engine.id == "native":
            try:
                metadata = tomllib.loads((PROJECT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
                if metadata.get('name') == 'py_swf_editor' and isinstance(metadata.get('version'), str):
                    record.update(version=metadata['version'], version_authority='Project pyproject.toml; source snapshot is identified separately by implementation_sha256')
            except (OSError, ValueError, KeyError):
                record['version_authority'] = 'Project version metadata unavailable; no binary version inferred'
        if engine.id in ("rust", "js", "go", "asm"):
            try:
                if config_path and tools_config(config_path).get(engine.id):
                    config = checked_tool(engine.id, config_path)
                    record.update(configured=True, version=config.get('version'), executable=config["executable"], artifact_sha256=list(config["artifact_sha256"].values()),
                                  version_authority='Pinned executable configuration; local Rust source version is not substituted')
                else:
                    executable = default_rust_executable() if engine.id == "rust" else default_peer_executable(engine.id)
                    record.update(configured=True, version="0.3.0", executable=str(executable), artifact_sha256=[hashlib.sha256(executable.read_bytes()).hexdigest()])
                record["output_scope"] = "Partial method/action source and instruction listings; no ActionScript compiler or verified behavior equivalence"
                record["binary_source_match"] = "not_verified"
            except (OSError, ValueError, KeyError) as error:
                record["setup_diagnostic"] = str(error)
        if engine.id=="jpexs":
            from .benchmarks import reference_command
            from .benchmark_process import WorkerError
            try:
                reference_command(REFERENCES / "toolchain.json")
                record.update(configured=True, version='26.3.0', commit='b2ef2d3d3f44291a4bdfa899c986bc39a89fa6aa',
                              version_authority='Reference command validated the pinned 26.3.0 toolchain and all recorded artifact hashes')
            except (OSError, ValueError, KeyError, WorkerError) as error:
                record["configured"] = False
                record["setup_diagnostic"] = str(error)
        if engine.id in ("flasm","flare","rabcdasm","pyflashkit","flex_swfdump","swfmill","swfextract"):
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
        if engine.id=="ruffle":
            record["setup_diagnostic"]="Runtime de referencia: ejecuta originales y recompilados en la campaña semántica (Escenarios coincidentes); no se clasifica contra descompiladores."
        if engine.id in ("swfmill","swfextract") and not record.get("configured"):
            record["setup_diagnostic"]="Operación propia (ida y vuelta XML / extracción de recursos). Ejecute tools/setup_swf_utilities.py para instalarla y medirla."
        if engine.id=="trillix":
            record["distribution_aliases"]=["https://flash-swf-decompiler.uptodown.com/windows"]
            record["alias_note"]="Uptodown lists Eltima Software's Flash SWF Decompiler; do not count a distribution portal as an independent engine."
        if engine.id=="sothink":
            record["distribution_aliases"]=["https://www.shankejingling.com/"]
            record["alias_note"]="Regional Sothink/硕思闪客精灵 product-family candidate; binary/version equivalence not verified. Not counted as another independent engine."
        if engine.id=="decompiler_com":
            record["setup_diagnostic"]="Servicio separado de los procesos locales; requiere prueba observada y consentimiento por corpus. No se mide su RAM remota."
            if (PROJECT / "reports/decompiler-com-evidence.json").is_file():
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
            "--config",str(Path(config_path).resolve() if config_path else REFERENCES / "extra-tools.json")]
