"""Transactional multi-engine Flash suite. No web server or Java requirement.

The original, working bytes and recovery project are separate. A failed tool,
compile, validation or export cannot advance the live revision/history.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from .avm1 import disassemble_avm1
from .avm2 import disassemble_instructions
from .decompile.avm1_dec import decompile_avm1
from .decompile.avm2_dec import decompile_method
from .editing import source_edit_reason
from .engines import NativePythonEngine
from .project import SwfProject, ProjectSnapshot
from .script_inventory import enumerate_scripts
from .storage import write_swf
from .swf_parser import SWFFile, SWFTag
from .suite_tools import ToolRegistry, ROOT, PROJECT, fingerprint, run_tool
from .validation import validate_swf_bytes

MAX_INPUT = 100 * 1024**2
MAX_SOURCE = 2 * 1024**2
SUITE_VERSION = "0.3.0"
MAX_BENCHMARK_REPORT = 32 * 1024**2
MAX_BENCHMARK_SNAPSHOT = 4 * 1024**2
MAX_BENCHMARK_FILES = 500
MAX_BENCHMARK_DEPTH = 3
MAX_BENCHMARK_RECENT = 20


def _benchmark_validate(report):
    """Validate measured JSON before it reaches charts; null remains unmeasured."""
    import math
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        raise ValueError("Esquema de benchmark no compatible; se requiere schema_version 1")
    if not isinstance(report.get("cases"), list):
        raise ValueError("El benchmark requiere una lista de casos")
    nodes = 0
    def visit(value, depth):
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > 500_000:
            raise ValueError("El benchmark supera el límite de estructura JSON")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("El benchmark contiene números no finitos")
        if isinstance(value, dict):
            for item in value.values():
                visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
    visit(report, 0)

    def count(value):
        return value is None or (type(value) is int and value >= 0)

    def measurement(value):
        return value is None or (type(value) in (int, float) and value >= 0)

    for key in ("created_at", "profile", "operation"):
        if key in report and report[key] is not None and not isinstance(report[key], str):
            raise ValueError("Metadatos de benchmark inválidos: " + key)
    for key in ("completed_files", "scheduled_files"):
        if not count(report.get(key)):
            raise ValueError("Recuento de benchmark inválido: " + key)
    quality_counts = ("expected_units_reported", "source_available", "disassembly_available", "pcode", "failed",
                      "syntax_native_subset_supported", "compiled_verified", "behavior_verified")
    for case in report["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("relative"), str) or not case["relative"] or not isinstance(case.get("engines"), list):
            raise ValueError("Caso de benchmark inválido")
        if "bytes" in case and not count(case["bytes"]):
            raise ValueError("Tamaño de caso inválido")
        for engine in case["engines"]:
            if not isinstance(engine, dict) or not isinstance(engine.get("engine"), str) or not engine["engine"]:
                raise ValueError("Motor de benchmark inválido")
            for key in ("quality", "cold", "hot"):
                if not isinstance(engine.get(key), dict):
                    raise ValueError("Resumen de motor inválido: " + key)
            for key in quality_counts:
                if key in engine["quality"] and not count(engine["quality"][key]):
                    raise ValueError("Recuento de calidad inválido: " + key)
            for phase in (engine["cold"], engine["hot"]):
                for key in ("attempted", "successful"):
                    if key in phase and not count(phase[key]):
                        raise ValueError("Recuento de mediciones inválido: " + key)
                for key in ("median_ms", "p95_ms", "peak_tree_rss_bytes"):
                    if key in phase and not measurement(phase[key]):
                        raise ValueError("Medición de benchmark inválida: " + key)
            for key in ("request_latency_ms", "hot_worker_startup_ms"):
                if key in engine and not measurement(engine[key]):
                    raise ValueError("Medición de motor inválida: " + key)
            if "compilation_audit" in engine and not isinstance(engine["compilation_audit"], dict):
                raise ValueError("Auditoría de compilación inválida")


def _benchmark_read(path):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Selecciona un archivo JSON de benchmark")
    try:
        resolved = Path(path).resolve(strict=True)
        if resolved.suffix.lower() != ".json":
            raise ValueError("Selecciona un archivo JSON de benchmark")
        data = read_bounded(resolved, MAX_BENCHMARK_REPORT)
        def reject_constant(value):
            raise ValueError("Número JSON no finito: " + value)
        report = json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant)
        _benchmark_validate(report)
        compact = _benchmark_compact(report)
        if len(json.dumps(compact, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > MAX_BENCHMARK_SNAPSHOT:
            raise ValueError("El resumen de benchmark supera 4 MiB; selecciona un reporte con menos casos")
        return resolved, compact
    except OSError as error:
        raise ValueError("No se puede leer el archivo de benchmark") from error
    except RecursionError as error:
        raise ValueError("El benchmark supera el límite de estructura JSON") from error


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_bounded(path, limit=MAX_INPUT):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Archivo inexistente o demasiado grande")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("El archivo creció y superó el límite de entrada")
    return data


def publish(path, data):
    """Non-clobber publication, including a concurrent destination creator."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (".suite-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            os.rename(temporary, path)  # Windows rename refuses existing paths
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


class UniversalSuite:
    def __init__(self, state_dir=None, tools_path=None):
        self.directory = Path(state_dir or os.environ.get("FLASH_SUITE_STATE_DIR") or tempfile.mkdtemp(prefix="flash-suite-")).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.registry = ToolRegistry(tools_path)
        self.project = None
        self.origin = None
        self.undo_stack = []
        self.redo_stack = []
        self.cache = {}
        self.java_worker = None
        self.rust_worker = None
        self._inventory = None
        self.last_compile = None
        recovery = self.directory / "session.pyswfproj"
        if recovery.is_file():
            self._restore(SwfProject.from_archive(recovery))

    def close(self):
        for worker in (self.java_worker, self.rust_worker):
            if worker:
                worker.close()

    def require_project(self):
        if self.project is None:
            raise ValueError("Abre un SWF o un proyecto primero")
        return self.project

    def movie(self):
        movie = SWFFile()
        movie.read_bytes(self.require_project().working)
        return movie

    def units(self):
        if self._inventory is None:
            self._inventory = list(enumerate_scripts(self.require_project().working))
        return self._inventory

    def unit(self, identity):
        result = next((u for u in self.units() if u.document_id == identity), None)
        if result is None:
            raise ValueError("Unidad inexistente; actualiza el inventario")
        if result.diagnostic:
            raise ValueError(result.diagnostic)
        return result

    def _restore(self, project):
        self.project = project
        metadata = getattr(project, "suite_metadata", {})
        self.origin = metadata.get("origin")
        self.undo_stack = [(project.snapshots[key].data, metadata.get("undo_sources", {}).get(key, {})) for key in metadata.get("undo", []) if key in project.snapshots]
        self.redo_stack = [(project.snapshots[key].data, metadata.get("redo_sources", {}).get(key, {})) for key in metadata.get("redo", []) if key in project.snapshots]
        project.snapshots = {key: value for key, value in project.snapshots.items() if not key.startswith("suite.")}
        self.cache.clear()
        self._inventory = None
        self._write_working()

    def _write_working(self):
        target = self.directory / "working.swf"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(self.project.working)
        os.replace(temporary, target)

    def _persist(self):
        self._write_working()
        if not validate_swf_bytes(self.project.working)["valid"]:
            return  # inspection of a malformed movie must not claim a valid project
        saved = deepcopy(self.project)
        saved.snapshots = {key: value for key, value in saved.snapshots.items() if not key.startswith("suite.")}
        metadata = dict(getattr(saved, "suite_metadata", {}))
        metadata.update(protocol_version=1, origin=self.origin, revision=sha(saved.working), undo=[], redo=[], undo_sources={}, redo_sources={})
        for prefix, stack in (("undo", self.undo_stack), ("redo", self.redo_stack)):
            for i, (data, sources) in enumerate(stack):
                key = f"suite.{prefix}.{i}"
                saved.snapshots[key] = ProjectSnapshot(key, data)
                metadata[prefix].append(key)
                metadata[prefix + "_sources"][key] = sources
        saved.suite_metadata = metadata
        saved.toolchains = {key: dict(value) for key, value in self.registry.tools.items()}
        saved.to_archive(self.directory / "session.pyswfproj")
        self.project.suite_metadata = metadata

    def state(self):
        project = self.require_project()
        movie = self.movie()
        return {"protocol_version": 1, "filename": project.filename, "working_path": str(self.directory / "working.swf"),
                "source_path": self.origin, "revision": sha(project.working), "source_revision": self.source_revision(),
                "source_pending": any(record.get("pending") for record in project.source_documents.values()),
                "dirty": project.working != project.original or any(record.get("pending") for record in project.source_documents.values()),
                "script_count": len(self.units()), "history": {"undo": bool(self.undo_stack), "redo": bool(self.redo_stack)},
                "snapshots": [key for key in project.snapshots if not key.startswith("suite.")],
                "signature": movie.signature, "frame_count": movie.frame_count,
                "validation": validate_swf_bytes(project.working), "state_dir": str(self.directory)}

    def open(self, path):
        path = Path(path).resolve(strict=True)
        recovery = self.directory / "session.pyswfproj"
        if self.project is not None and recovery.is_file():
            archived = self.directory / "documents" / (sha(self.project.working) + ".pyswfproj")
            if not archived.is_file():
                archived.parent.mkdir(exist_ok=True)
                shutil.copyfile(recovery, archived)
        if path.suffix.lower() in (".pyswfproj", ".flashproj"):
            self._restore(SwfProject.from_archive(path))
        else:
            data = read_bounded(path)
            parsed = SWFFile()
            parsed.read_bytes(data)
            self.project = SwfProject(path.name, data, data, engine="auto")
            self.origin = str(path)
            self.undo_stack, self.redo_stack = [], []
            self.cache.clear()
            self._inventory = None
        self._persist()
        return self.state()

    def commit(self, candidate, operation, *, sources=None):
        project = self.require_project()
        validation = validate_swf_bytes(candidate)
        if not validation["valid"]:
            raise ValueError("El candidato no es válido: " + "; ".join(validation["errors"]))
        old_project, old_undo, old_redo = deepcopy(project), list(self.undo_stack), list(self.redo_stack)
        try:
            if candidate != project.working or (sources is not None and sources != project.source_documents):
                self.undo_stack.append((project.working, deepcopy(project.source_documents)))
                while len(self.undo_stack) > 16 or sum(len(d) for d, _ in self.undo_stack) > 256 * 1024**2:
                    self.undo_stack.pop(0)
                self.redo_stack = []
            project.apply(candidate, operation)
            if sources is not None and sources != old_project.source_documents and candidate == old_project.working:
                project.history.append({"operation": operation, "at": datetime.now(timezone.utc).isoformat(),
                                        "before": sha(candidate), "after": sha(candidate),
                                        "metadata": {"source_before": sha(json.dumps(old_project.source_documents, sort_keys=True, ensure_ascii=False).encode("utf-8")),
                                                     "source_after": sha(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode("utf-8"))}})
            if sources is not None:
                project.source_documents = sources
            self.cache.clear()
            self._inventory = None
            self._persist()
        except Exception:
            self.project, self.undo_stack, self.redo_stack = old_project, old_undo, old_redo
            self._inventory = None
            self._write_working()
            raise
        return self.state()

    def source_revision(self):
        return sha(json.dumps(self.require_project().source_documents, sort_keys=True, ensure_ascii=False).encode("utf-8"))

    def _authored_documents(self):
        """Resolve the entry alias of older v3 archives without changing them."""
        documents = deepcopy(self.require_project().source_documents)
        entry = documents.get("project:entry")
        if not entry:
            return documents
        filename = entry.get("filename", "").replace("\\", "/")
        candidates = [document["filename"] for key, document in documents.items()
                      if key.startswith("project:source:") and document.get("filename")
                      and Path(document["filename"]).name == Path(filename).name
                      and document.get("source") == entry.get("source")]
        if filename not in candidates and candidates:
            historical = str(self.project.suite_metadata.get("entry", "")).replace("\\", "/")
            matches = [name for name in candidates if historical.endswith("/" + name)]
            if len(matches) == 1:
                filename = matches[0]
            elif len(candidates) == 1:
                filename = candidates[0]
            else:
                raise ValueError("La entrada guardada es ambigua; conserva las fuentes y selecciona un proyecto explícito")
        entry["filename"] = filename
        return documents

    def source_edit(self, args):
        project = self.require_project()
        if args.get("base_revision") != sha(project.working):
            raise ValueError("El SWF cambió; conserva el borrador y actualiza")
        key = args["id"]
        record = project.source_documents.get(key)
        if not record or not key.startswith("project:"):
            raise ValueError("Este documento se edita desde su unidad de bytecode")
        if args.get("base_source_sha256") != sha(record.get("source", "").encode("utf-8")):
            raise ValueError("La fuente cambió; conserva el borrador y vuelve a cargarla")
        text = args["source"]
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_SOURCE:
            raise ValueError("Fuente inválida o superior al límite de 2 MiB")
        sources = self._authored_documents()
        # project:entry and project:source:<filename> are aliases of the same
        # authored file. They must never disagree when rebuilding a project.
        filename = sources[key]["filename"]
        for identity, document in sources.items():
            if identity.startswith("project:") and document.get("filename") == filename:
                document.update(source=text, pending=True)
        return self.commit(project.working, "source_edit", sources=sources)

    def compile_saved(self, args):
        project = self.require_project()
        metadata = project.suite_metadata
        profile = args.get("profile") or metadata.get("compile_profile")
        original_documents = self._authored_documents()
        entry = original_documents.get("project:entry")
        if not entry or not profile:
            raise ValueError("El proyecto no contiene una entrada y un perfil de compilación guardados")
        output = Path(args["output"]).resolve()
        if output.exists() or (self.origin and output == Path(self.origin).resolve()):
            raise ValueError("Selecciona una salida nueva; el original está protegido")
        with tempfile.TemporaryDirectory(prefix="flash-saved-source-") as temporary:
            folder = Path(temporary).resolve()
            documents = {}
            for key, document in original_documents.items():
                if not key.startswith("project:") or not document.get("filename"):
                    continue
                filename = document["filename"]
                if filename in documents and document.get("source") != documents[filename].get("source"):
                    raise ValueError("Las fuentes guardadas discrepan para el mismo archivo")
                documents[filename] = document
            for filename, document in documents.items():
                relative = Path(filename.replace("\\", "/"))
                target = (folder / relative).resolve()
                if relative.is_absolute() or not target.is_relative_to(folder) or ":" in filename:
                    raise ValueError("Ruta de fuente fuera del proyecto")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(document["source"], encoding="utf-8")
            entry_relative = metadata.get("entry_relative", entry["filename"])
            source_path = (folder / entry_relative).resolve()
            if not source_path.is_relative_to(folder) or not source_path.is_file():
                raise ValueError("No se encuentra la entrada guardada del proyecto")
            rebuilt = UniversalSuite(folder / "session", self.registry.path)
            rebuilt.registry = self.registry
            try:
                result = rebuilt.compile_project({"entry": str(source_path), "profile": profile,
                                                  "main_class": args.get("main_class") or metadata.get("main_class"),
                                                  "output": str(folder / "rebuilt.swf"), "debug": metadata.get("debug", False),
                                                  **{name: metadata[name] for name in ("width", "height", "frame_rate") if name in metadata}})
                candidate = rebuilt.project.working
                compile_metadata = {key: value for key, value in rebuilt.project.suite_metadata.items()
                                    if key in ("compile_profile", "compiler", "entry_relative", "main_class", "debug", "width", "height", "frame_rate")}
            finally:
                rebuilt.close()
        for key, document in original_documents.items():
            if not key.startswith("project:"):
                continue  # A rebuilt program does not prove old edited units still match.
            document.pop("pending", None)
            document["working_swf_sha256"] = sha(candidate)
        # Rebuilding creates a new program from the saved source tree. It does
        # not patch or replace the user's original file or external source tree.
        publish(output, candidate)
        old_metadata = deepcopy(project.suite_metadata)
        try:
            project.suite_metadata.update(compile_metadata)
            next_state = self.commit(candidate, "compile_saved", sources=original_documents)
        except Exception as error:
            self.project.suite_metadata = old_metadata
            raise ValueError(f"SWF generado en {output}, pero la sesión no pudo aplicar el cambio: {error}") from error
        return {**next_state, "path": str(output), "engine": result["engine"], "log": result["log"],
                "validation": result["validation"], "behavior": "not_run"}

    def scripts(self, args):
        query = str(args.get("query", "")).lower()
        units = [u for u in self.units() if query in f"{u.name} {u.document_id}".lower()]
        offset, limit = max(0, int(args.get("offset", 0))), min(500, max(1, int(args.get("limit", 200))))
        return {"total": len(units), "offset": offset, "items": [{**u.metadata(), "id": u.document_id} for u in units[offset:offset + limit]]}

    def pcode(self, unit):
        if unit.language == "as3":
            return disassemble_instructions(unit.abc.constant_pool, unit.code, exact=True, exceptions=unit.body.exceptions)
        return disassemble_avm1(unit.code)

    def _native_source(self, unit):
        if unit.language == "as3":
            text, error = decompile_method(unit.abc, unit.body, unit.name)
            reason = source_edit_reason(unit.abc, unit.body)
        else:
            text, error = decompile_avm1(unit.code)
            reason = None
        return {"engine": "python", "source": text if not error else None, "pcode": self.pcode(unit),
                "diagnostics": [message for message in (error, reason) if message], "editable": reason is None,
                "source_fidelity": "reconstructed" if not error else "unavailable", "behavior": "not_run"}

    def _rust_unit(self, unit):
        from .benchmark_rust_worker import RustWorker
        if self.rust_worker is None:
            path = self.registry.executable("rust")
            self.rust_worker = RustWorker(path, fingerprint(path), timeout=25)
        deadline = time.perf_counter() + 25
        self.rust_worker.call("open", {"path": str(self.directory / "working.swf")}, deadline)
        offset, target = 0, None
        while target is None:
            page = self.rust_worker.call("scripts", {"offset": offset, "limit": 500}, deadline)
            for candidate in page["items"]:
                if (candidate.get("id") == unit.document_id and candidate.get("sha256") == sha(unit.code)
                        and candidate.get("tag_path") == list(unit.tag_path)
                        and {"avm1": "as2", "avm2": "as3"}.get(candidate.get("language"), candidate.get("language")) == unit.language
                        and candidate.get("byte_len") == len(unit.code) and candidate.get("event_flags") == unit.event_flags):
                    target = candidate
                    break
            offset += len(page["items"])
            if not page["items"] or offset >= page["total"]:
                break
        if target is None:
            raise ValueError("RustSWF no devolvió una unidad con identidad, ubicación y bytecode coincidentes")
        return target["id"], deadline

    def _rust_source(self, unit):
        identity, deadline = self._rust_unit(unit)
        result = self.rust_worker.call("decompile", {"id": identity}, deadline)
        return {"engine": "rust", "source": result.get("source"), "pcode": result.get("pcode", self.pcode(unit)),
                "editable": False, "diagnostics": result.get("diagnostics", []), "behavior": "not_run"}

    def _java(self, operation, *, data=None, **args):
        from .benchmark_process import Worker
        if self.java_worker is None:
            jar = Path(self.registry.executable("jpexs"))
            bridge = ROOT / "toolpacks/jpexs-bridge"
            if not (bridge / "UniversalBridge.class").is_file():
                raise ValueError("Falta el módulo de aplicación JPEXS; ejecuta tools/setup_suite_tools.py")
            lock = json.loads((ROOT / "toolpacks/lock.json").read_text(encoding="utf-8"))["tools"]["jpexs-bridge"]
            if lock["jar_sha256"] != fingerprint(jar):
                raise ValueError("JPEXS cambió después de construir el módulo")
            for name, expected in lock["classes"].items():
                if fingerprint(bridge / name) != expected:
                    raise ValueError("El módulo JPEXS cambió después de su construcción")
            for name, expected in lock.get("libraries", {}).items():
                if fingerprint(jar.parent / "lib" / name) != expected:
                    raise ValueError("Cambió una dependencia JPEXS: " + name)
            classpath = os.pathsep.join(map(str, [bridge, jar, jar.parent / "lib/*"]))
            command = [self.registry.executable("java"), "-Xmx1024m", "-Djava.awt.headless=true",
                       "-Duser.home={reference_home}", "-Dpyswf.playerglobal=" + str(jar.parent / "flashlib/playerglobal32_0.swc"),
                       "-cp", classpath, "UniversalBridge"]
            self.java_worker = Worker(command, cwd=bridge, environment=self.registry.environment(), timeout=110)
        response = self.java_worker.request({"operation": operation, "swf_base64": base64.b64encode(data if data is not None else self.require_project().working).decode(), **args})
        if not response.get("ok"):
            raise ValueError(response.get("diagnostic", "JPEXS rechazó la operación"))
        return response

    def _jpexs_source(self, unit):
        key = (sha(self.project.working), "jpexs-all")
        if key not in self.cache:
            self.cache[key] = self._java("decompile", include_source=True)
        all_sources = self.cache[key]
        record = next((r for r in all_sources["units"] if r.get("document_id") == unit.document_id and r.get("bytecode_sha256") == sha(unit.code)), None)
        if record is None:
            raise ValueError("JPEXS no devolvió una unidad con identidad y hash coincidentes")
        document = next((d for d in all_sources.get("documents", []) if d["document_id"] == record.get("source_document")), None)
        return {"engine": "jpexs", "source": record.get("source") if record.get("generation") != "failed" else None,
                "pcode": self.pcode(unit), "editable": unit.language == "as2" or document is not None,
                "source_document": record.get("source_document"), "document_source": document.get("source") if document else None,
                "diagnostics": [record["diagnostic"]] if record.get("diagnostic") else [], "behavior": "not_run"}

    def decompile(self, args):
        unit = self.unit(args["unit_id"])
        engine = args.get("engine", "auto")
        providers = {"python": self._native_source, "native": self._native_source, "rust": self._rust_source, "jpexs": self._jpexs_source}
        errors = []
        for selected in (["python", "rust", "jpexs"] if engine == "auto" else [engine]):
            try:
                result = providers[selected](unit)
                result.update(revision=sha(self.project.working), unit_id=unit.document_id)
                if engine != "auto" or result["source"] is not None:
                    result["fallback_diagnostics"] = errors
                    saved = self.project.source_documents.get(unit.document_id, {})
                    if saved.get("working_swf_sha256") == sha(self.project.working):
                        result["saved_source"] = saved.get("source")
                    return result
                errors += result.get("diagnostics", [])
            except Exception as error:
                errors.append(f"{selected}: {error}")
                if engine != "auto":
                    raise
        return {"engine": "python", "source": None, "pcode": self.pcode(unit), "editable": False,
                "diagnostics": errors, "revision": sha(self.project.working), "behavior": "not_run"}

    def code_documents(self, args):
        """Read reconstructed declarations; no source/history is saved here."""
        from .decompile.backends import NativeBackend
        from .source_document import native_source_documents
        project = self.require_project()
        identity = args.get('document_id')
        match = re.fullmatch(r'tag:(\d+(?:/\d+)*)/(class|script):(\d+)', identity) if isinstance(identity, str) else None
        path = tuple(map(int, match.group(1).split('/'))) if match else tuple(args.get('tag_path', ()))
        if not path or any(not isinstance(index, int) or isinstance(index, bool) or index < 0 for index in path):
            raise ValueError('Selecciona tag_path o una identidad de clase/script')
        unit = next((u for u in self.units() if u.tag_path == path and u.abc is not None and not u.diagnostic), None)
        if unit is None:
            raise ValueError('No se encuentra un ABC válido en ese tag')
        engine = {'auto': 'native', 'python': 'native'}.get(args.get('engine', 'native'), args.get('engine', 'native'))
        if engine not in ('native', 'rust'):
            raise ValueError('Esta operación reconstruye documentos nativos PySWF/RustSWF')
        key = ('code_documents', sha(project.working), engine, path)
        if key not in self.cache:
            prefix = 'tag:' + '/'.join(map(str, path))
            result = NativeBackend().decompile(unit.tag, project.working, project.filename).as_dict()
            native = native_source_documents(unit.tag, '/'.join(map(str, path)), project.working, result=result)
            sections = {section['scope'] + ':' + str(section[section['scope'] + '_index']): section
                        for section in result['sections'] if section.get('scope') in ('class', 'script')}
            items = []
            for record in native:
                if record['scope'] not in ('class', 'script'):
                    continue
                suffix = record['document_id'].split('/')[-1]
                section = sections[suffix]
                items.append({**record, 'source': section['source'], 'source_engine': 'native',
                              'compiler_engine': 'native', 'compiler_delegated': False,
                              'compilation': 'not_run', 'behavior': 'not_run'})
            if engine == 'rust':
                rust_id, deadline = self._rust_unit(unit)
                request = {'id': rust_id}
                # The immutable SDK proves inherited scopes; its bytes remain
                # a development reference, not a delegated decompiler.
                from .decompile.hierarchy_contract import PLAYERGLOBAL_32_SHA256
                sdk = Path(ROOT) / 'toolpacks/flex/frameworks/libs/player/32.0/playerglobal.swc'
                if sdk.is_file() and fingerprint(sdk) == PLAYERGLOBAL_32_SHA256:
                    request.update(hierarchy_swc=str(sdk), hierarchy_sha256=PLAYERGLOBAL_32_SHA256)
                response = self.rust_worker.call('decompile_document', request, deadline)
                outputs = response.get('documents', [])
                for item in items:
                    owner = item['symbol_context'][item['scope'] + '_index']
                    selected = [d for d in outputs if d.get(item['scope'] + '_index') == owner]
                    complete = response.get('state') == 'structured'
                    if item['scope'] == 'class':
                        complete = complete and len(selected) == 1
                    else:
                        expected = [trait.class_idx for trait in unit.abc.scripts[owner].traits
                                    if trait.kind_flags & 15 == 4]
                        # A script is the complete ordered group of its classes.
                        # Returning its first class would silently drop siblings.
                        complete = complete and bool(expected) and sorted(d.get('class_index') for d in selected) == sorted(expected)
                        order = response.get('initialization_order', [])
                        selected.sort(key=lambda d: order.index(d['class_index']) if d['class_index'] in order else len(order))
                    source = '\n\n'.join(d['source'] for d in selected) if complete else None
                    if source is not None and len(source.encode('utf-8')) > MAX_SOURCE:
                        source, complete = None, False
                        item['diagnostics'].append('El documento agrupado supera el límite de 2 MiB; se conserva como P-code')
                    item.update(source=source,
                                source_engine='rust', compiler_delegated=True,
                                completeness='complete' if complete else 'partial',
                                hierarchy_source=response.get('hierarchy_source'),
                                diagnostics=[*item['diagnostics'], *[str(d) for d in response.get('diagnostics', [])]])
                    item['source_sha256'] = sha((item['source'] or '').encode('utf-8'))
                    item['compiler_eligibility'] = {'eligible': bool(complete and item['compiler_eligibility']['eligible']),
                                                   'reason': item['compiler_eligibility'].get('reason') or (None if complete else 'RustSWF conserva el documento como P-code')}
                    item['editable'] = item['compiler_eligibility']['eligible']
            self.cache[key] = items
        items = self.cache[key]
        if match:
            selected = next((item for item in items if item['document_id'] == identity), None)
            if selected is None:
                raise ValueError('Documento de código inexistente')
            return {**selected, 'revision': sha(project.working)}
        offset, limit = args.get('offset', 0), args.get('limit', 50)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= 200:
            raise ValueError('Paginación inválida')
        page = [{key: value for key, value in item.items() if args.get('include_source') or key != 'source'} for item in items[offset:offset + limit]]
        return {'items': page, 'total': len(items), 'offset': offset, 'limit': limit,
                'revision': sha(project.working), 'source_engine': engine, 'compiler_engine': 'native'}

    def compile_code_document(self, args):
        """Stage and validate a whole ABC using the shared native compiler."""
        from .class_documents import compile_reconstructed_document
        from .unit_editing import _replace_nested
        project = self.require_project()
        if args.get('base_revision') != sha(project.working):
            raise ValueError('La revisión cambió; actualiza el documento')
        document = self.code_documents({'document_id': args.get('document_id'), 'engine': 'native'})
        if args.get('base_source_sha256') != document['original_source_sha256'] or args.get('tag_sha256') != document['tag_sha256']:
            raise ValueError('La fuente o el tag cambiaron; conserva el borrador y actualiza')
        code = args.get('source')
        if not isinstance(code, str) or len(code.encode('utf-8')) > MAX_SOURCE:
            raise ValueError('Fuente inválida o demasiado grande')
        path = tuple(map(int, document['document_id'].split('/class:')[0].split('/script:')[0][4:].split('/')))
        unit = next(u for u in self.units() if u.tag_path == path and u.abc is not None)
        replacement, diagnostics = compile_reconstructed_document(unit.tag, document, code)
        movie = self.movie()
        outer = path[0]
        if len(path) > 1:
            parent = movie.tags[outer]
            replacement = SWFTag(39, parent.data[:4] + _replace_nested(parent.data[4:], path[1:], replacement))
        movie.tags[outer] = replacement
        candidate = movie.save_bytes()
        validation = validate_swf_bytes(candidate)
        if not validation['valid']:
            raise ValueError('El compilador produjo un candidato inválido')
        output = args.get('output')
        if not output and not args.get('commit'):
            raise ValueError('La recompilación necesita salida nueva o commit explícito')
        result = {'source_engine': args.get('source_engine', 'native'), 'compiler_engine': 'native',
                  'compiler_delegated': args.get('source_engine') == 'rust', 'diagnostics': diagnostics,
                  'input_sha256': sha(project.working), 'candidate_sha256': sha(candidate),
                  'validation': validation, 'behavior': 'not_run', 'committed': False}
        if output:
            write_swf(output, candidate, original=self.origin)
            result['path'] = str(Path(output).resolve())
        if args.get('commit'):
            result.update(self.commit(candidate, 'compile_code_document'))
            result['committed'] = True
        return result

    def compare(self, args):
        results = []
        for engine in ("python", "rust", "jpexs"):
            try:
                results.append(self.decompile({**args, "engine": engine}))
            except Exception as error:
                results.append({"engine": engine, "source": None, "pcode": "", "editable": False, "diagnostics": [str(error)], "status": "unavailable_or_failed"})
        return {"results": results, "behavior": "not_run", "parity": False, "superiority": False}

    def edit(self, args):
        if args.get("base_revision") != sha(self.require_project().working):
            raise ValueError("El documento cambió; conserva tu borrador y actualiza la revisión")
        code = args["code"]
        if not isinstance(code, str) or len(code.encode("utf-8")) > MAX_SOURCE:
            raise ValueError("La fuente supera el límite del editor")
        unit = self.unit(args["unit_id"])
        if args.get("engine") == "jpexs" and args.get("mode") == "source":
            locator = args.get("source_document") if unit.language == "as3" else unit.document_id
            if not locator:
                raise ValueError("Selecciona la clase completa JPEXS para recompilar AS3")
            response = self._java("compile", document_id=locator, source=code)
            candidate = base64.b64decode(response["swf_base64"], validate=True)
        else:
            candidate = NativePythonEngine().edit_unit(self.project.working, {"document_id": unit.document_id, "bytecode_sha256": sha(unit.code)}, code, source=args.get("mode") == "source")
        sources = deepcopy(self.project.source_documents)
        if args.get("mode") == "source":
            sources[unit.document_id] = {"source": code, "engine": args.get("engine", "python"), "working_swf_sha256": sha(candidate)}
        return self.commit(candidate, "suite_edit", sources=sources)

    def history(self, redo=False):
        source, destination = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not source:
            raise ValueError("No hay cambios para " + ("rehacer" if redo else "deshacer"))
        current = (self.project.working, deepcopy(self.project.source_documents))
        data, documents = source[-1]
        validation = validate_swf_bytes(data)
        if not validation["valid"]:
            raise ValueError("Snapshot inválido")
        source.pop()
        destination.append(current)
        self.project.working, self.project.source_documents = data, deepcopy(documents)
        self.cache.clear()
        self._inventory = None
        self._persist()
        return self.state()

    def compile_project(self, args):
        entry = Path(args["entry"]).resolve(strict=True)
        profile = args.get("profile", "auto-as3")
        if profile == "haxeflixel" and entry.is_dir():
            descriptor = next((entry / name for name in ("Project.xml", "project.xml") if (entry / name).is_file()), None)
            if descriptor is None:
                raise ValueError("El proyecto HaxeFlixel requiere Project.xml")
            entry = descriptor
        source_root_hint = entry if entry.is_dir() else entry.parent
        if entry.is_file() and entry.suffix.lower() == ".as":
            package = re.search(r"\bpackage\s+([A-Za-z_][\w.]*)\s*\{", read_bounded(entry, MAX_SOURCE).decode("utf-8-sig"))
            parts = package.group(1).split(".") if package else []
            if parts and list(entry.parent.parts[-len(parts):]) == parts:
                source_root_hint = entry.parent.parents[len(parts) - 1]
        output = Path(args["output"]).resolve()
        if output.exists() or output == entry or (self.origin and output == Path(self.origin)):
            raise ValueError("Selecciona una salida nueva para conservar los originales")
        log = ""
        if profile in ("native-as2", "as2-jpexs", "auto-as2"):
            from .swf_parser import make_rect
            movie = SWFFile()
            movie.version, movie.signature = 8, "CWS"
            movie.rect = {"xmin": 0, "xmax": 16000, "ymin": 0, "ymax": 12000}
            movie.frame_count, movie.frame_rate = 1, 30
            movie.tags = [SWFTag(12, b"\x00"), SWFTag(1, b""), SWFTag(0, b"")]
            blank = movie.save_bytes()
            text = read_bounded(entry, MAX_SOURCE).decode("utf-8-sig")
            if profile in ("native-as2", "auto-as2"):
                try:
                    candidate = NativePythonEngine().compile_source(blank, 0, text)
                    engine = "python"
                except (ValueError, SyntaxError) as error:
                    if profile != "auto-as2":
                        raise
                    log = f"Compilador nativo: {error}\n"
                    profile = "as2-jpexs"
            if profile == "as2-jpexs":
                response = self._java("compile", data=blank, document_id="tag:0/actions", source=text)
                candidate = base64.b64decode(response["swf_base64"], validate=True)
                engine = "jpexs"
        if profile in ("native-as3", "auto-as3"):
            try:
                text = read_bounded(entry, MAX_SOURCE).decode("utf-8-sig")
                sources = {}
                for file in source_root_hint.rglob("*.as"):
                    if file == entry or any(p.lower() in (".git", "export", "node_modules", "target") for p in file.relative_to(source_root_hint).parts):
                        continue
                    if len(sources) >= 64:
                        raise ValueError("Más de 64 fuentes: usa un perfil de compilador externo")
                    sources[file.relative_to(source_root_hint).as_posix()] = read_bounded(file, MAX_SOURCE).decode("utf-8-sig")
                if sum(len(s.encode("utf-8")) for s in sources.values()) > 8 * 1024**2:
                    raise ValueError("Proyecto fuente superior al presupuesto nativo")
                qualified_main = (package.group(1) + "." if entry.suffix.lower() == ".as" and package else "") + entry.stem
                document_class = args.get("main_class") or (qualified_main if re.search(r"\bextends\s+(?:flash\.display\.)?(?:Sprite|MovieClip)\b", text) else None)
                candidate = NativePythonEngine().compile_classes(text, sources=sources or None, document_class=document_class,
                                                                width=int(args.get("width", 800)), height=int(args.get("height", 600)), frame_rate=float(args.get("frame_rate", 30)))
                engine = "python"
            except (ValueError, SyntaxError) as error:
                if profile != "auto-as3":
                    raise
                log = f"Compilador nativo: {error}\n"
                profile = "as3-flex"
        if profile not in ("native-as3", "auto-as3", "native-as2", "auto-as2", "as2-jpexs"):
            with tempfile.TemporaryDirectory(prefix="flash-build-") as temporary:
                staged = Path(temporary)
                result = staged / "result.swf"
                if profile == "as3-flex":
                    compiler = Path(self.registry.executable("flex"))
                    frameworks = compiler.parent.parent / "frameworks"
                    command = [self.registry.executable("java"), "-Xmx1024m", "-jar", str(compiler), "+flexlib=" + str(frameworks), "+playerglobalHome=" + str(frameworks / "libs/player"), "-target-player=32.0", "-output=" + str(result), "-source-path=" + str(source_root_hint), str(entry)]
                    if args.get("debug"):
                        command[5:5] = ["-debug=true", "-omit-trace-statements=false"]
                    engine = "flex"
                elif profile == "as2-mtasc":
                    command = [self.registry.executable("mtasc"), "-cp", str(entry.parent), "-swf", str(result), "-header", "800:600:30", "-main", str(entry)]
                    engine = "mtasc"
                elif profile == "haxe":
                    command = [self.registry.executable("haxe")]
                    if entry.suffix.lower() == ".hxml":
                        content = read_bounded(entry, MAX_SOURCE).decode("utf-8-sig")
                        if re.search(r"(?m)^\s*(--cmd|--run|--interp|--macro|--next|--each)\b", content):
                            raise ValueError("Este HXML contiene comandos/macros o varias compilaciones; usa un perfil de proyecto explícito")
                        command += [str(entry), "--swf", str(result)]
                    elif entry.suffix.lower() == ".hx":
                        command += ["-cp", str(entry.parent), "-main", entry.stem, "--swf", str(result)]
                    else:
                        raise ValueError("Haxe requiere un .hx o .hxml")
                    if args.get("debug"):
                        command += ["--debug"]
                    engine = "haxe"
                elif profile == "haxeflixel":
                    project_root = entry if entry.is_dir() else entry.parent
                    if not (project_root / "Project.xml").is_file() and not (project_root / "project.xml").is_file():
                        raise ValueError("El proyecto HaxeFlixel requiere Project.xml")
                    # Project.xml controls output paths: build in a bounded copy
                    # so compilation cannot overwrite an existing build.
                    build = staged / "project"
                    self._stage_project(project_root, build)
                    command = [self.registry.executable("haxeflixel"), "run", "lime", "build", "flash", "-debug" if args.get("debug") else "-release"]
                    engine = "haxeflixel"
                else:
                    raise ValueError("Perfil de compilación desconocido")
                cwd = build if profile == "haxeflixel" else entry.parent
                environment = self.registry.environment()
                if profile == "as3-flex":
                    environment["PLAYERGLOBAL_HOME"] = str(frameworks / "libs/player")
                log += run_tool(command, cwd=cwd, env=environment, timeout=110)
                if profile == "haxeflixel":
                    from xml.etree import ElementTree
                    descriptor = build / entry.name if entry.is_file() else build / "Project.xml"
                    app = ElementTree.parse(descriptor).getroot().find("app")
                    stem = app.attrib.get("file", "Main") if app is not None else "Main"
                    outputs = [p for p in build.rglob("*.swf") if p.stem == stem]
                    if len(outputs) != 1:
                        raise ValueError("El perfil no produjo exactamente un SWF de aplicación; archivos: " + str([p.relative_to(build).as_posix() for p in build.rglob("*.swf")]) + "; log: " + log[-4000:])
                    result = outputs[0]
                candidate = read_bounded(result)
        validation = validate_swf_bytes(candidate)
        if not validation["valid"]:
            if args.get("diagnostic_output"):
                diagnostic = Path(args["diagnostic_output"]).resolve()
                publish(diagnostic, candidate)
            raise ValueError("El compilador produjo un SWF inválido: " + "; ".join(validation["errors"]))
        authored_documents = {}
        source_root = source_root_hint
        suffixes = (".hx", ".hxml", ".xml") if profile in ("haxe", "haxeflixel") else (".as",)
        source_bytes = 0
        for file in sorted(source_root.rglob("*")):
            if not file.is_file() or file.suffix.lower() not in suffixes or any(part.lower() in ("export", ".git", "target", "node_modules", ".venv") for part in file.relative_to(source_root).parts):
                continue
            source = read_bounded(file, MAX_SOURCE).decode("utf-8-sig")
            source_bytes += len(source.encode("utf-8"))
            if source_bytes > 8 * 1024**2 or len(authored_documents) >= 256:
                raise ValueError("Las fuentes del proyecto superan el presupuesto de archivo; reduce el proyecto antes de compilar en la suite")
            name = file.relative_to(source_root).as_posix()
            authored_documents["project:source:" + name] = {"source": source, "filename": name, "language": "haxe" if file.suffix.lower() == ".hx" else "project" if file.suffix.lower() in (".xml", ".hxml") else "as2" if "as2" in profile else "as3", "working_swf_sha256": sha(candidate)}
        write_swf(output, candidate, original=self.origin)
        self.open(output)
        self.project.source_documents.update(authored_documents)
        self.project.suite_metadata = {**getattr(self.project, "suite_metadata", {}), "compile_profile": profile, "compiler": engine,
                                      "entry": str(entry), "entry_relative": entry.relative_to(source_root).as_posix(),
                                      "main_class": args.get("main_class"), "debug": bool(args.get("debug")),
                                      "width": int(args.get("width", 800)), "height": int(args.get("height", 600)),
                                      "frame_rate": float(args.get("frame_rate", 30))}
        if entry.is_file() and entry.suffix.lower() in (".as", ".hx", ".hxml", ".xml"):
            authored = read_bounded(entry, MAX_SOURCE).decode("utf-8-sig")
            self.project.source_documents["project:entry"] = {"source": authored, "filename": entry.relative_to(source_root).as_posix(), "language": "haxe" if profile in ("haxe", "haxeflixel") else "as2" if "as2" in profile else "as3", "working_swf_sha256": sha(candidate)}
        if engine == "python" and profile in ("native-as3", "auto-as3"):
            for name, source in (sources or {}).items():
                self.project.source_documents["project:source:" + name] = {"source": source, "filename": name, "language": "as3", "working_swf_sha256": sha(candidate)}
        self._persist()
        return {**self.state(), "path": str(output), "engine": engine, "validation": validation, "log": log[-65536:], "behavior": "not_run"}

    @staticmethod
    def _stage_project(source, destination):
        total = 0
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if any(part.lower() in (".git", ".venv", "node_modules", "export", "target") for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError("El proyecto contiene enlaces externos; usa una copia de fuentes sin enlaces")
            if path.is_file():
                total += path.stat().st_size
                if total > 256 * 1024**2:
                    raise ValueError("El proyecto supera el presupuesto de staging")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)

    def export(self, args):
        project = self.require_project()
        format_ = args["format"]
        destination = Path(args["path"]).resolve()
        if format_ == "resource-bundle":
            from .fla_export import export_resource_bundle
            if destination.is_dir():
                destination /= f"resources-{uuid.uuid4().hex[:8]}.zip"
            data = export_resource_bundle(self.movie())
            if isinstance(data, tuple):
                data = data[0]
            return {"path": publish(destination, data), "engine": "python", "animate_compatible": False, "diagnostics": []}
        if format_ in ("fla", "xfl", "scripts", "xml"):
            with tempfile.TemporaryDirectory(prefix="flash-export-") as temporary:
                folder = Path(temporary)
                input_ = folder / "input.swf"
                input_.write_bytes(project.working)
                target = folder / "result.xml" if format_ == "xml" else folder / "out"
                command = self.registry.java_command() + (["-swf2xml", str(input_), str(target)] if format_ == "xml" else ["-export", "script" if format_ == "scripts" else format_, str(target), str(input_)])
                log = run_tool(command, cwd=folder, env=self.registry.environment())
                if format_ == "xml":
                    return {"path": publish(destination, read_bounded(target)), "engine": "jpexs", "log": log, "diagnostics": []}
                files = [p for p in target.rglob("*") if p.is_file()]
                if not files:
                    raise ValueError("JPEXS no produjo archivos; no se registra como exportación correcta")
                if format_ == "fla" and destination.suffix.lower() == ".fla":
                    flas = [p for p in files if p.suffix.lower() == ".fla"]
                    if len(flas) != 1:
                        raise ValueError("La exportación no produjo un FLA único")
                    published = publish(destination, read_bounded(flas[0]))
                else:
                    if destination.exists():
                        destination /= f"export-{format_}-{uuid.uuid4().hex[:8]}"
                    destination.mkdir(parents=True, exist_ok=False)
                    for file in files:
                        target_file = destination / file.relative_to(target)
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(file, target_file)
                    published = str(destination)
                return {"path": published, "engine": "jpexs", "log": log, "diagnostics": [], "animate_compatibility": "exported_not_opened" if format_ in ("fla", "xfl") else None}
        raise ValueError("Formato de exportación no soportado")

    def timeline(self, args):
        from .timeline import build_timeline, render_frame
        from .fla_export import make_bitmap_resolver
        movie = self.movie()
        frames = build_timeline(movie.tags)
        index = max(0, min(int(args.get("frame", 0)), max(0, len(frames) - 1)))
        svg, rendered_count = render_frame(movie, index, make_bitmap_resolver(movie.tags))
        return {"frame": index, "frame_count": rendered_count, "display_list": [{"depth": depth, **value} for depth, value in frames[index].items()] if frames else [],
                "svg": svg, "actionscript_executed": False}

    def resource_edit(self, args):
        if args.get("base_revision") != sha(self.require_project().working):
            raise ValueError("El documento cambió")
        movie = self.movie()
        if args.get("operation") == "header":
            movie.frame_rate = float(args.get("frame_rate", movie.frame_rate))
            if not 0 < movie.frame_rate <= 120:
                raise ValueError("FPS fuera de rango")
            return self.commit(movie.save_bytes(), "header_edit")
        index = int(args["tag_index"])
        if not 0 <= index < len(movie.tags):
            raise ValueError("Tag desconocido")
        tag = movie.tags[index]
        if args.get("operation") == "replace-image":
            if tag.tag_type not in (20, 21, 35, 36):
                raise ValueError("Este formato de imagen no admite reemplazo nativo")
            from PIL import Image
            with Image.open(args["path"]) as image:
                if image.width * image.height > 16 * 1024**2:
                    raise ValueError("Imagen superior al límite de 16 megapíxeles")
            from .resources import replace_image
            replacement = replace_image(tag, read_bounded(args["path"]), Path(args["path"]).suffix.lstrip("."))
            if replacement is None:
                raise ValueError("Imagen incompatible")
            movie.tags[index] = replacement
        elif args.get("operation") == "replace-sound":
            from .sounds import replace_define_sound
            if tag.tag_type != 14:
                raise ValueError("Selecciona un DefineSound")
            movie.tags[index] = replace_define_sound(tag, read_bounded(args["path"]), Path(args["path"]).suffix.lstrip("."))
        elif args.get("operation") == "text":
            from .text_fonts import replace_edit_text_string
            if tag.tag_type != 37:
                raise ValueError("Selecciona un texto editable DefineEditText")
            movie.tags[index] = replace_edit_text_string(tag, str(args["text"]))
        elif args.get("operation") == "shape-point":
            from .shapes import parse_shape_tag
            from .shape_edit import update_shape_vertex, rebuild_shape_tag
            if tag.tag_type not in (2, 22, 32, 83):
                raise ValueError("Selecciona una forma vectorial")
            parsed = parse_shape_tag(tag)
            if "point_index" in args:
                points = self._shape_points(parsed)
                index_ = int(args["point_index"])
                if not 0 <= index_ < len(points):
                    raise ValueError("Vértice fuera de rango")
                point = points[index_]
                group, subpath, vertex = point["group"], point["subpath"], point["vertex"]
            else:
                group, subpath, vertex = int(args["group"]), int(args["subpath"]), int(args["vertex"])
            import math
            x, y = float(args["x"]), float(args["y"])
            if not math.isfinite(x) or not math.isfinite(y) or abs(x) > 2**24 or abs(y) > 2**24:
                raise ValueError("Coordenadas de forma fuera de rango")
            update_shape_vertex(parsed, group, subpath, vertex, x, y)
            # Expand bounds conservatively; retain existing stroke margins.
            tx, ty = round(x * 20), round(y * 20)
            for bounds in (parsed["bounds"], parsed.get("edge_bounds")):
                if bounds is not None:
                    bounds.update(xmin=min(bounds["xmin"], tx), xmax=max(bounds["xmax"], tx),
                                  ymin=min(bounds["ymin"], ty), ymax=max(bounds["ymax"], ty))
            replacement = rebuild_shape_tag(parsed, tag.tag_type)
            movie.tags[index] = replacement
        elif args.get("operation") == "font-glyph":
            from .font_documents import move_point
            movie.tags[index] = move_point(tag, int(args["glyph_index"]), int(args["point_index"]), int(args["x"]), int(args["y"]))
        elif args.get("operation") == "font-properties":
            from .font_documents import properties
            movie.tags[index] = properties(tag, args)
        else:
            raise ValueError("Operación de recurso no soportada")
        return self.commit(movie.save_bytes(), "resource_edit")

    @staticmethod
    def _shape_points(parsed):
        result = []
        for g, group in enumerate(parsed["groups"]):
            for s, path in enumerate(group["subpaths"]):
                for v, point in enumerate(path):
                    result.append({"index": len(result), "group": g, "subpath": s, "vertex": v, **point})
        if len(result) > 10000:
            raise ValueError("La forma supera el presupuesto del editor de vértices")
        return result

    def resource_info(self, args):
        movie = self.movie()
        index = int(args["tag_index"])
        if not 0 <= index < len(movie.tags):
            raise ValueError("Recurso fuera de rango")
        tag = movie.tags[index]
        result = {"kind": "raw", "parsed": {}, "revision": sha(self.project.working), "tag_index": index}
        if tag.tag_type in (2, 22, 32, 83):
            from .shapes import parse_shape_tag
            parsed = parse_shape_tag(tag)
            result.update(kind="shape", parsed=parsed, points=self._shape_points(parsed), coordinate_units="pixels")
        elif tag.tag_type in (48, 75):
            from .font_documents import read_font
            font = read_font(tag)
            result.update(kind="font", parsed={key: value for key, value in font.items() if not key.startswith("_") and key != "glyphs"},
                          coordinate_units="font units", glyphs=[{"index": g, "code": font["codes"][g],
                          "points": [{"index": i, **point} for i, point in enumerate(point for path in glyph for point in path)]}
                          for g, glyph in enumerate(font["glyphs"])])
        elif tag.tag_type == 37:
            from .text_fonts import parse_edit_text
            result.update(kind="text", parsed=parse_edit_text(tag))
        elif tag.tag_type == 14:
            from .sounds import parse_define_sound
            result.update(kind="sound", parsed={key: value for key, value in (parse_define_sound(tag) or {}).items() if not isinstance(value, bytes)})
        elif tag.tag_type in (6, 20, 21, 35, 36, 90):
            result.update(kind="image", parsed={"character_id": tag.char_id, "bytes": len(tag.data), "type": tag.tag_type})
        return result

    def resources(self, args):
        movie = self.movie()
        query = str(args.get("query", "")).lower()
        items = [{"id": f"tag:{i}", "index": i, "tag_index": i, "name": tag.name, "type": tag.tag_type, "formats": [], "bytes": len(tag.data), "character_id": tag.char_id} for i, tag in enumerate(movie.tags) if query in f"{i} {tag.name} {tag.char_id}".lower()]
        for item in items:
            item["formats"] = ["png"] if item["type"] in (20, 35, 36) else ["jpeg"] if item["type"] == 21 else ["svg"] if item["type"] in (2, 22, 32, 83) else []
        offset = max(0, int(args.get("offset", 0)))
        limit = min(500, max(1, int(args.get("limit", 200))))
        return {"total": len(items), "offset": offset, "items": items[offset:offset + limit]}

    def tool_operation(self, args):
        """Whole-file assembly formats stay distinct from ActionScript source."""
        key, operation = args["id"], args["operation"]
        if key == "rust":
            if operation == "capabilities":
                return json.loads(run_tool([self.registry.executable("rust"), "capabilities"], timeout=25))
            if operation == "abc_metadata":
                identity, deadline = self._rust_unit(self.unit(args["unit_id"]))
                return self.rust_worker.call(operation, {"id": identity, "offset": int(args.get("offset", 0)), "limit": int(args.get("limit", 200))}, deadline)
            if operation == "roundtrip":
                destination = Path(args["path"]).resolve()
                if destination.exists() or (self.origin and destination == Path(self.origin).resolve()):
                    raise ValueError("Selecciona una salida nueva; el original está protegido")
                result = json.loads(run_tool([self.registry.executable("rust"), "roundtrip", str(self.directory / "working.swf"),
                                              str(destination), str(args.get("compression", "original"))], timeout=25))
                if not result.get("normalized_bytes_identical"):
                    raise ValueError("RustSWF no confirmó conservación de la imagen SWF normalizada")
                return {**result, "engine": "rust", "behavior": "not_run"}
            raise ValueError("Operación RustSWF desconocida")
        with tempfile.TemporaryDirectory(prefix="flash-module-") as temporary:
            folder = Path(temporary)
            input_ = folder / "input.swf"
            input_.write_bytes(self.require_project().working)
            if key == "flasm" and operation == "disassemble":
                log = run_tool([self.registry.executable(key), "-d", str(input_)], cwd=folder)
                return {"engine": key, "text": log, "mapping": "file_level", "behavior": "not_run"}
            if key == "flasm" and operation == "assemble":
                if args.get("base_revision") != sha(self.require_project().working):
                    raise ValueError("El documento cambió antes del ensamblado")
                text = read_bounded(args["entry"], MAX_SOURCE).decode("utf-8-sig")
                if re.search(r"(?im)^\s*#include\b", text):
                    raise ValueError("Importa un listado Flasm completo sin includes externos")
                pattern = r"(?im)^\s*movie\s+(['\"])(.*?)\1"
                if len(list(re.finditer(pattern, text))) != 1:
                    raise ValueError("El listado Flasm debe identificar exactamente una película")
                text = re.sub(pattern, lambda _: "movie '" + input_.as_posix() + "'", text, count=1)
                assembly = folder / "input.flm"
                assembly.write_text(text, encoding="utf-8")
                log = run_tool([self.registry.executable(key), "-a", str(assembly)], cwd=folder)
                return {**self.commit(read_bounded(input_), "flasm_assemble", sources={}), "engine": key, "log": log}
            if key == "rabcdasm" and operation == "disassemble":
                unit = self.unit(args["unit_id"])
                if unit.language != "as3":
                    raise ValueError("RABCDAsm requiere un ABC AS3")
                abc_file = folder / "input.abc"
                abc_file.write_bytes(unit.tag.parse_doabc()[2])
                log = run_tool([self.registry.executable(key), str(abc_file)], cwd=folder)
                destination = Path(args["path"]).resolve() / ("rabcdasm-" + uuid.uuid4().hex[:8])
                destination.mkdir(parents=True, exist_ok=False)
                artifacts = []
                for file in folder.rglob("*.asasm"):
                    target = destination / file.relative_to(folder)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, target)
                    artifacts.append({"file": str(target), "sha256": fingerprint(target)})
                if not artifacts:
                    raise ValueError("RABCDAsm no produjo archivos")
                return {"path": str(destination), "engine": key, "artifacts": artifacts, "log": log, "mapping": "ABC_structure", "behavior": "not_run"}
            if key == "rabcdasm" and operation == "assemble":
                if args.get("base_revision") != sha(self.require_project().working):
                    raise ValueError("El documento cambió antes del ensamblado")
                unit = self.unit(args["unit_id"])
                if unit.language != "as3":
                    raise ValueError("Selecciona un método del ABC que se va a reemplazar")
                entry = Path(args["entry"]).resolve(strict=True)
                staged = folder / "assembly"
                self._stage_project(entry.parent, staged)
                assembler = Path(self.registry.executable(key)).with_name("rabcasm.exe" if os.name == "nt" else "rabcasm")
                log = run_tool([str(assembler), str(staged / entry.name)], cwd=staged)
                files = list(staged.glob("*.abc"))
                if len(files) != 1:
                    raise ValueError("El ensamblador no produjo un ABC único")
                abc_bytes = read_bounded(files[0])
                flags, name, _ = unit.tag.parse_doabc()
                payload = flags.to_bytes(4, "little") + name.encode("utf-8") + b"\0" + abc_bytes if unit.tag.tag_type == 82 else abc_bytes
                replacement = SWFTag(unit.tag.tag_type, payload)
                movie = self.movie()
                index = unit.tag_path[0]
                if len(unit.tag_path) > 1:
                    from .unit_editing import _replace_nested
                    parent = movie.tags[index]
                    replacement = SWFTag(39, parent.data[:4] + _replace_nested(parent.data[4:], unit.tag_path[1:], replacement))
                movie.tags[index] = replacement
                result = self.commit(movie.save_bytes(), "rabcdasm_assemble", sources={})
                return {**result, "engine": key, "log": log}
            if key == "swfmill" and operation == "xml":
                xml = folder / "result.xml"
                log = run_tool([self.registry.executable(key), "swf2xml", str(input_), str(xml)], cwd=folder)
                return {"path": publish(args["path"], read_bounded(xml)), "engine": key, "log": log}
            if key == "flare" and operation == "scripts":
                log = run_tool([self.registry.executable(key), str(input_)], cwd=folder)
                files = list(folder.glob("*.flr"))
                if len(files) != 1:
                    raise ValueError("Flare no produjo un listado único")
                return {"path": publish(args["path"], read_bounded(files[0])), "engine": key, "log": log, "mapping": "not_verified"}
        raise ValueError("Esta operación del módulo no está implementada")

    def dispatch(self, operation, args):
        if not isinstance(args, dict):
            raise ValueError("Argumentos inválidos")
        if operation == "ping":
            return {"protocol_version": 1, "application": "Flash Universal Studio", "version": SUITE_VERSION,
                    "engines": {"python": "PySWF", "rust": "RustSWF"}}
        if operation == "engines":
            return self.registry.catalog()
        if operation == "configure_engine":
            self.close()
            self.java_worker = self.rust_worker = None
            return self.registry.configure(args["id"], args["path"])
        if operation in ("open", "load_project"):
            return self.open(args["path"])
        if operation == "state":
            return self.state()
        if operation == "scripts":
            return self.scripts(args)
        if operation == "resources":
            return self.resources(args)
        if operation == "resource_info":
            return self.resource_info(args)
        if operation == "source_files":
            documents = self.require_project().source_documents
            if args.get("id"):
                record = documents[args["id"]]
                return {"id": args["id"], **record, "editable": args["id"].startswith("project:"),
                        "source_sha256": sha(record.get("source", "").encode("utf-8"))}
            return {"files": [{"id": key, "filename": value.get("filename", key), "language": value.get("language", "as"), "bytes": len(value.get("source", "").encode("utf-8"))} for key, value in documents.items()]}
        if operation == "source_edit":
            return self.source_edit(args)
        if operation == "compile_saved":
            return self.compile_saved(args)
        if operation == "decompile":
            return self.decompile(args)
        if operation == 'code_documents':
            return self.code_documents(args)
        if operation == 'compile_code_document':
            return self.compile_code_document(args)
        if operation == "compare":
            return self.compare(args)
        if operation == "edit":
            return self.edit(args)
        if operation in ("undo", "redo"):
            return self.history(operation == "redo")
        if operation == "validate":
            return validate_swf_bytes(read_bounded(args["path"]) if args.get("path") else self.require_project().working)
        if operation == "export_swf":
            write_swf(args["path"], self.require_project().working, original=self.origin)
            return {**self.state(), "path": str(Path(args["path"]).resolve())}
        if operation == "save_project":
            path = Path(args["path"]).resolve()
            if path.exists() or (self.origin and path == Path(self.origin)):
                raise ValueError("Selecciona un proyecto nuevo")
            self._persist()
            # Recovery archive also carries persisted undo/redo and toolchains.
            publish(path, read_bounded(self.directory / "session.pyswfproj", 512 * 1024**2))
            return {**self.state(), "path": str(path), "format_version": 3}
        if operation == "compile_project":
            return self.compile_project(args)
        if operation == "export":
            return self.export(args)
        if operation == "timeline":
            return self.timeline(args)
        if operation == "preview_data":
            data = self.require_project().working
            if len(data) > 5 * 1024**2:
                raise ValueError("La previsualización integrada admite SWF de hasta 5 MiB por transporte; usa el runtime externo para archivos mayores")
            return {"data": base64.b64encode(data).decode("ascii"), "filename": self.project.filename, "sha256": sha(data)}
        if operation == "resource_edit":
            return self.resource_edit(args)
        if operation == "tool_operation":
            return self.tool_operation(args)
        if operation == 'benchmark_tools':
            from .benchmark_engines import describe_engines
            return {'engines': describe_engines(args.get('tool_config')), 'scope': 'Read-only local adapter discovery; no decompilation executed'}
        if operation == 'bench_results':
            from .bench_results import read_bench
            return read_bench(args.get('run'))
        if operation == "snapshot":
            name = str(args["name"]).strip()
            if name.startswith("suite."):
                raise ValueError("Nombre reservado para historial")
            self.require_project().snapshot(name)
            self._persist()
            return self.state()
        if operation == "restore_snapshot":
            candidate = self.require_project().snapshots[args["name"]].data
            return self.commit(candidate, "restore_snapshot", sources={})
        if operation == "launch_tool":
            key = args["id"]
            if key not in ("sothink", "trillix", "asv", "as3_sorcerer", "ruffle"):
                raise ValueError("Esta herramienta se utiliza mediante su módulo integrado")
            command = [self.registry.executable(key)]
            if key == "ruffle":
                command.append(str(self.directory / "working.swf"))
            child = subprocess.Popen(command, env=self.registry.environment())
            return {"pid": child.pid, "engine": key, "status": "launched_not_verified"}
        if operation == "import_result":
            data = read_bounded(args["path"], 8 * 1024**2)
            return {"engine": args["id"], "text": data.decode("utf-8-sig"), "sha256": sha(data), "mapping": "not_verified", "behavior": "not_run"}
        raise ValueError("Operación de suite desconocida: " + str(operation))
