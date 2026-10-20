"""Local, capability-specific tools for Flash Universal Studio (GPL-3.0).

Executables run as argument arrays, on disposable input copies. Configuration
records fingerprints; installers are never treated as usable engines.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(os.environ.get("FLASH_SUITE_WORKSPACE", Path(__file__).resolve().parents[2])).resolve()
PROJECT = ROOT / "py_swf_editor"
if not PROJECT.is_dir() and not getattr(sys, "frozen", False):
    PROJECT = Path(__file__).resolve().parents[1]
    ROOT = PROJECT.parent


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_tool(command, *, cwd=None, env=None, timeout=110):
    """Bound logs without shell interpolation and kill descendants on timeout."""
    if not command or any(not isinstance(part, str) for part in command):
        raise ValueError("Invalid tool command")
    with tempfile.TemporaryFile() as log:
        child = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                 start_new_session=os.name != "nt")
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                import signal
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise ValueError("La herramienta agotó el tiempo permitido") from None
        log.seek(0, 2)
        length = log.tell()
        log.seek(max(0, length - 65536))
        text = log.read().decode("utf-8", errors="replace")
        if child.returncode:
            raise ValueError(f"La herramienta terminó con código {child.returncode}: {text}")
        return text


DEFINITIONS = {
    "java": ("Java · módulos JPEXS/Flex", "dependency", []),
    "python": ("PySWF", "native", ["decompile", "edit", "assemble", "compile_project", "compile_saved", "source_edit", "resource-bundle", "scripts", "timeline", "resources", "resource_edit", "benchmark"]),
    "rust": ("RustSWF", "native", ["decompile", "disassemble", "capabilities", "abc_metadata", "roundtrip"]),
    "jpexs": ("JPEXS", "decompiler", ["decompile", "edit", "compile_project", "scripts", "fla", "xfl", "xml"]),
    "flex": ("Apache Flex / mxmlc", "compiler", ["compile_project"]),
    "mtasc": ("MTASC · AS2", "compiler", ["compile_project"]),
    "haxe": ("Haxe · SWF", "compiler", ["compile_project"]),
    "haxeflixel": ("HaxeFlixel / OpenFL / Lime", "compiler", ["compile_project"]),
    "flasm": ("Flasm · AVM1", "assembler", ["disassemble", "assemble"]),
    "flare": ("Flare · AS1/AS2", "decompiler", ["scripts"]),
    "rabcdasm": ("RABCDAsm · AVM2", "assembler", ["disassemble", "assemble"]),
    "swfmill": ("swfmill", "converter", ["xml"]),
    "swfextract": ("SWFTools · swfextract", "extractor", ["extract"]),
    "ruffle": ("Ruffle", "runtime", ["preview"]),
    "sothink": ("Sothink SWF Decompiler", "assisted", ["launch", "import_result"]),
    "trillix": ("Flash Decompiler Trillix", "assisted", ["launch", "import_result"]),
    "asv": ("Action Script Viewer", "assisted", ["launch", "import_result"]),
    "as3_sorcerer": ("AS3 Sorcerer", "assisted", ["launch", "import_result"]),
}


class ToolRegistry:
    def __init__(self, config_path=None):
        self.path = Path(config_path or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FlashUniversalStudio" / "tools.json")
        self.config = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        self.config.setdefault("schema_version", 1)
        self.config.setdefault("tools", {})
        self.tools = self.config["tools"]
        self.discover()

    def discover(self):
        refs = PROJECT / ".dev-references"
        packs = ROOT / "toolpacks"
        executable_dir = Path(sys.executable).parent
        candidates = {
            "rust": [executable_dir / "rustswf.exe", ROOT / "RUST SWF/dist/FlashUniversalStudio/rustswf.exe",
                     ROOT / "RUST SWF/target/x86_64-pc-windows-gnu/release/rustswf.exe"],
            "jpexs": [packs / "jpexs/ffdec.jar", refs / "ffdec/ffdec.jar"],
            "flex": [packs / "flex/lib/mxmlc.jar"],
            "haxe": list((packs / "haxe").glob("**/haxe.exe")),
            "haxeflixel": list((packs / "haxe").glob("**/haxelib.exe")),
            "rabcdasm": [refs / "extra-tools/rabcdasm-1.18-complete/rabcdasm.exe"],
            "flasm": [refs / "extra-tools/flasm-1.62/flasm.exe"],
            "ruffle": [ROOT / "RUST SWF/web/public/ruffle/ruffle.js"],
        }
        for key in DEFINITIONS:
            if key == "python":
                continue
            if key in self.tools and Path(self.tools[key].get("path", "")).is_file():
                continue
            paths = candidates.get(key, [])
            found = next((p for p in paths if p.is_file()), None)
            name = {"haxeflixel": "haxelib", "flex": "mxmlc", "rust": "rustswf"}.get(key, key)
            on_path = shutil.which(name)
            if not found and on_path and Path(on_path).suffix.lower() not in (".bat", ".cmd"):
                found = Path(on_path)
            if found:
                self.tools[key] = {"path": str(found.resolve()), "sha256": fingerprint(found), "version": self._version(key, found)}
        if "java" not in self.tools:
            java = next(iter((refs / "jdk").glob("*/bin/java.exe")), None) or next(iter((packs / "java").glob("*/bin/java.exe")), None)
            if java:
                self.tools["java"] = {"path": str(java.resolve()), "sha256": fingerprint(java)}

    def _version(self, key, path):
        if key == "jpexs":
            return "26.3.0" if path.parent == PROJECT / ".dev-references/ffdec" else "configured"
        if key == "rabcdasm":
            return "1.18" if "1.18" in str(path) else "configured"
        if key == "haxe":
            try:
                return run_tool([str(path), "--version"], env=self.environment(), timeout=5).strip()
            except (OSError, ValueError):
                return "not_verified"
        return "configured"

    def environment(self):
        env = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC", "LOCALAPPDATA", "USERPROFILE", "HOME")}
        paths = [str(Path(c["path"]).parent) for c in self.tools.values() if c.get("path")]
        paths += [str(p) for p in (ROOT / "toolpacks/neko").glob("neko*") if p.is_dir()]
        env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
        env["HAXELIB_PATH"] = str(ROOT / "toolpacks/haxelib")
        env["PYTHONIOENCODING"] = "utf-8"
        if "haxe" in self.tools:
            std = Path(self.tools["haxe"]["path"]).parent / "std"
            if std.is_dir():
                env["HAXE_STD_PATH"] = str(std)
        return env

    def executable(self, key):
        config = self.tools.get(key, {})
        path = Path(config.get("path", ""))
        if not path.is_file():
            raise ValueError(f"{key}: herramienta local no disponible; configúrala en Herramientas")
        if config.get("sha256") != fingerprint(path):
            raise ValueError(f"{key}: cambió el ejecutable; vuelve a configurarlo para comprobar su procedencia")
        return str(path.resolve())

    def configure(self, key, path):
        if key not in DEFINITIONS and key != "java":
            raise ValueError("Herramienta desconocida")
        path = Path(path).resolve(strict=True)
        if not path.is_file() or path.suffix.lower() in (".bat", ".cmd", ".ps1"):
            raise ValueError("Selecciona el ejecutable o JAR, no un script o instalador")
        if "setup" in path.name.lower() or "installer" in path.name.lower():
            raise ValueError("Un instalador no es un motor disponible")
        self.tools[key] = {"path": str(path), "sha256": fingerprint(path), "version": self._version(key, path)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return self.catalog()

    def catalog(self):
        result = []
        for key, (name, category, operations) in DEFINITIONS.items():
            operations = list(operations)
            config = self.tools.get(key, {})
            available = key == "python" or bool(config.get("path") and Path(config["path"]).is_file())
            detail = "" if available else "Módulo opcional: configura una instalación local compatible"
            if key in ("jpexs", "flex") and "java" not in self.tools:
                available, detail = False, "Se requiere un Java local configurado"
            if key == "jpexs" and available and not (ROOT / "toolpacks/jpexs-bridge/UniversalBridge.class").is_file():
                operations = [operation for operation in operations if operation not in ("decompile", "edit", "compile_project")]
                detail = "CLI de exportación disponible; falta el módulo JPEXS para descompilar y editar"
            if key == "haxeflixel":
                available = available and "haxe" in self.tools and (ROOT / "toolpacks/haxelib/flixel/6,2,0").is_dir()
                detail = "Perfil 6.2.0 / OpenFL 9.5.2 / Lime 8.3.2; build flash" if available else "Se requieren Haxe y las bibliotecas del perfil Flash"
            if key == "ruffle" and (os.environ.get("FLASH_SUITE_RUFFLE_WEB") == "1" or config.get("path", "").endswith("ruffle.js")):
                available, detail = True, "Ruffle web integrado · 0.7.1 · ejecución separada de compilación"
            if category == "assisted":
                detail = "Instalación del usuario; lanzamiento e importación asistida, sin automatización certificada"
            result.append({"id": key, "name": name, "category": category, "operations": operations,
                           "available": available, "path": sys.executable if key == "python" else config.get("path"), "version": sys.version.split()[0] if key == "python" else config.get("version"),
                           "sha256": config.get("sha256"), "detail": detail})
        return {"protocol_version": 1, "engines": result, "default_engine": "auto"}

    def java_command(self):
        return [self.executable("java"), "-Xmx1024m", "-Djava.awt.headless=true", "-jar", self.executable("jpexs")]
