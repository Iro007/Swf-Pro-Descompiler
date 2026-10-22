"""
API de plugins/scripts para extensibilidad.
Permite escribir scripts Python que interactúan con el SWF.
"""
import asyncio
import inspect
import json
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class PluginContext:
    """Contexto disponible para los plugins."""
    swf: Any  # SWFFile
    session_id: str
    api: Any  # Acceso a funciones de la API interna


class PluginRegistry:
    """Registro de plugins y hooks."""
    
    def __init__(self):
        self.plugins: Dict[str, "Plugin"] = {}
        self.hooks: Dict[str, List[Callable]] = {
            "on_load": [],
            "on_save": [],
            "on_tag_parse": [],
            "on_export": [],
            "on_decompile": [],
        }
    
    def register(self, plugin: "Plugin"):
        self.plugins[plugin.name] = plugin
        # Obtener hooks de la clase
        class_hooks = getattr(plugin.__class__, '_class_hooks', {})
        for hook_name, hook_fn in class_hooks.items():
            if hook_name in self.hooks:
                self.hooks[hook_name].append(hook_fn)
    
    def unregister(self, name: str):
        if name in self.plugins:
            plugin = self.plugins.pop(name)
            class_hooks = getattr(plugin.__class__, '_class_hooks', {})
            for hook_name, hook_fn in class_hooks.items():
                if hook_name in self.hooks and hook_fn in self.hooks[hook_name]:
                    self.hooks[hook_name].remove(hook_fn)
    
    async def trigger(self, hook_name: str, context: PluginContext, *args, **kwargs):
        """Ejecuta todos los hooks registrados para un evento."""
        results = []
        for hook_fn in self.hooks.get(hook_name, []):
            try:
                if inspect.iscoroutinefunction(hook_fn):
                    result = await hook_fn(context, *args, **kwargs)
                else:
                    result = hook_fn(context, *args, **kwargs)
                results.append(result)
            except Exception as e:
                print(f"Plugin hook error in {hook_name}: {e}")
                traceback.print_exc()
        return results


class Plugin:
    """Base class para plugins."""
    
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.hooks: Dict[str, Callable] = {}
    
    @classmethod
    def hook(cls, name: str):
        """Decorator para registrar hooks."""
        def decorator(fn):
            # Almacenar en el diccionario de hooks de la clase
            if not hasattr(cls, '_class_hooks'):
                cls._class_hooks = {}
            cls._class_hooks[name] = fn
            return fn
        return decorator


# Instancia global del registry
plugin_registry = PluginRegistry()


# ===== API Functions para uso en plugins =====

def get_tag_by_id(swf, char_id: int):
    """Busca un tag por character ID."""
    for tag in swf.tags:
        if tag.char_id == char_id:
            return tag
    return None


def get_tags_by_type(swf, tag_type: int):
    """Obtiene todos los tags de un tipo dado."""
    return [tag for tag in swf.tags if tag.tag_type == tag_type]


def find_symbol_name(swf, char_id: int):
    """Busca el nombre de símbolo para un character ID."""
    from ..swf_parser import collect_symbol_names
    names = collect_symbol_names(swf.tags)
    return names.get(char_id)


def iter_sprites(swf):
    """Itera sobre todos los DefineSprite en el SWF."""
    from ..timeline import parse_sprite
    for tag in swf.tags:
        if tag.tag_type == 39:
            parsed = parse_sprite(tag)
            if parsed:
                yield parsed


def iter_frames(swf):
    """Itera sobre frames de la timeline principal."""
    from ..timeline import build_timeline
    frames = build_timeline(swf.tags)
    for i, frame in enumerate(frames):
        yield i, frame


def export_resource(tag, swf_tags):
    """Exporta un recurso (imagen, sonido, etc.) a bytes."""
    from .. import resources, sounds, text_fonts, shapes, morph
    
    if tag.tag_type in (6, 20, 21, 35, 36, 90):
        jpeg_tables = resources.find_jpeg_tables(swf_tags)
        return resources.export_image(tag, jpeg_tables=jpeg_tables)
    elif tag.tag_type == 14:
        return sounds.export_sound(tag)
    elif tag.tag_type in (48, 75):
        font = text_fonts.parse_font(tag)
        if font:
            return text_fonts.font_to_svg(font).encode("utf-8"), "svg"
    elif tag.tag_type in (2, 22, 32, 83):
        bitmap_resolver = make_bitmap_resolver(swf_tags)
        return shapes.shape_to_svg(tag, bitmap_resolver=bitmap_resolver).encode("utf-8"), "svg"
    elif tag.tag_type in (46, 84):
        return morph.morph_to_svg(tag, ratio=0.0).encode("utf-8"), "svg"
    return None, None


def make_bitmap_resolver(swf_tags):
    """Crea resolver de bitmap fills."""
    from .. import resources
    from PIL import Image
    import io
    
    jpeg_tables = resources.find_jpeg_tables(swf_tags)
    by_id = {t.char_id: t for t in swf_tags if t.tag_type in (6, 20, 21, 35, 36, 90) and t.char_id is not None}
    
    def resolve(char_id):
        tag = by_id.get(char_id)
        if tag is None:
            return None
        data, ext = resources.export_image(tag, jpeg_tables=jpeg_tables)
        if data is None:
            return None
        try:
            img = Image.open(io.BytesIO(data))
            if ext != "png":
                buf = io.BytesIO()
                img.save(buf, "PNG")
                data = buf.getvalue()
            return data, img.width, img.height
        except Exception:
            return None
    return resolve


def decompile_script(tag, swf_tags):
    """Decompila un tag de script."""
    from ..decompile import avm1_dec, avm2_dec
    from ..avm2 import ABCFile, build_method_mapping, disassemble_instructions
    from ..swf_parser import SWFTag
    
    if tag.tag_type in (12, 59):
        offset = 2 if tag.tag_type == 59 else 0
        return avm1_dec.decompile_avm1(tag.data[offset:])
    elif tag.tag_type == 82:
        parsed = tag.parse_doabc()
        if not parsed:
            return None, "Malformed DoABC"
        _, name, abc_bytes = parsed
        abc = ABCFile()
        abc.parse(abc_bytes)
        mapping = build_method_mapping(abc)
        results = []
        for i, mb in enumerate(abc.method_bodies):
            method_name = mapping.get(mb.method, f"method_{mb.method}")
            source, error = avm2_dec.decompile_method(abc, mb, method_name)
            if source is None:
                source = disassemble_instructions(abc.constant_pool, mb.code)
            results.append((method_name, source, error))
        return results, None
    return None, "Not a script tag"


# ===== Built-in Plugins =====

class BatchExportPlugin(Plugin):
    """Plugin para exportación en lote de recursos."""
    
    def __init__(self):
        super().__init__("batch_export", "Batch export resources")
    
    @Plugin.hook("on_export")
    async def batch_export(self, context: PluginContext, tag_types=None, output_dir="exports"):
        import os
        os.makedirs(output_dir, exist_ok=True)
        
        swf = context.swf
        exported = []
        
        for tag in swf.tags:
            if tag_types and tag.tag_type not in tag_types:
                continue
            
            data, ext = export_resource(tag, swf.tags)
            if data:
                name = f"{tag.name}_{tag.char_id or 'unknown'}.{ext}"
                path = os.path.join(output_dir, name)
                if isinstance(data, str):
                    data = data.encode("utf-8")
                with open(path, "wb") as f:
                    f.write(data)
                exported.append({"tag": tag.tag_type, "char_id": tag.char_id, "path": path})
        
        return {"exported": exported}


class SpriteSheetPlugin(Plugin):
    """Plugin para generar sprite sheets automáticamente."""
    
    def __init__(self):
        super().__init__("sprite_sheet", "Auto-generate sprite sheets")
    
    @Plugin.hook("on_export")
    async def generate_sprite_sheet(self, context: PluginContext, cols=8, padding=2):
        from ..fla_export import export_sprite_sheet
        png_bytes, meta = export_sprite_sheet(context.swf, cols=cols, padding=padding)
        if png_bytes:
            path = f"spritesheet_{context.session_id}.png"
            with open(path, "wb") as f:
                f.write(png_bytes)
            return {"path": path, "metadata": json.loads(meta)}
        return None


class ValidationPlugin(Plugin):
    """Plugin para validar SWF al cargar."""
    
    def __init__(self):
        super().__init__("validation", "Validate SWF structure")
    
    @Plugin.hook("on_load")
    async def validate(self, context: PluginContext):
        errors = []
        warnings = []
        
        swf = context.swf
        
        # Check for parse errors
        for i, tag in enumerate(swf.tags):
            if tag.parse_error:
                warnings.append(f"Tag {i} ({tag.name}): {tag.parse_error}")
        
        # Check frame count
        if swf.frame_count == 0:
            warnings.append("Frame count is 0")
        
        # Check for missing End tag
        if not swf.tags or swf.tags[-1].tag_type != 0:
            errors.append("Missing End tag")
        
        # Check for duplicate char_ids
        char_ids = {}
        for i, tag in enumerate(swf.tags):
            if tag.char_id is not None:
                if tag.char_id in char_ids:
                    warnings.append(f"Duplicate char_id {tag.char_id} at tags {char_ids[tag.char_id]} and {i}")
                else:
                    char_ids[tag.char_id] = i
        
        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


class ScriptAnalyzerPlugin(Plugin):
    """Plugin para analizar scripts automáticamente."""
    
    def __init__(self):
        super().__init__("script_analyzer", "Analyze ActionScript for issues")
    
    @Plugin.hook("on_tag_parse")
    async def analyze_script(self, context: PluginContext, tag):
        if tag.tag_type not in (12, 59, 82):
            return None
        
        results, error = decompile_script(tag, context.swf.tags)
        if error:
            return {"tag": tag.index, "error": error}
        
        # Análisis básico: buscar patrones sospechosos
        issues = []
        if isinstance(results, list):  # AVM2
            for method_name, source, err in results:
                if err:
                    issues.append(f"{method_name}: {err}")
                # Buscar patrones comunes de ofuscación
                if "eval" in source or "Function(" in source:
                    issues.append(f"{method_name}: Dynamic code execution detected")
        else:  # AVM1
            if "eval" in results or "Function(" in results:
                issues.append("Dynamic code execution detected")
        
        return {"tag": tag.index, "issues": issues} if issues else None


# Registrar plugins built-in
plugin_registry.register(BatchExportPlugin())
plugin_registry.register(SpriteSheetPlugin())
plugin_registry.register(ValidationPlugin())
plugin_registry.register(ScriptAnalyzerPlugin())


# ===== API REST para plugins =====

def get_plugin_api_router():
    """Retorna router FastAPI para gestión de plugins."""
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel
    
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])
    
    class PluginInfo(BaseModel):
        name: str
        description: str
        hooks: List[str]
    
    @router.get("", response_model=List[PluginInfo])
    def list_plugins():
        return [
            PluginInfo(
                name=p.name,
                description=p.description,
                hooks=list(p.hooks.keys()),
            )
            for p in plugin_registry.plugins.values()
        ]
    
    @router.post("/run/{hook_name}")
    async def run_hook(hook_name: str, session_id: str, payload: dict = {}):
        from server.routers.files import get_session
        session = get_session(session_id)
        context = PluginContext(swf=session.swf, session_id=session_id, api=None)
        results = await plugin_registry.trigger(hook_name, context, **payload)
        return {"results": results}
    
    class CustomPluginRequest(BaseModel):
        name: str
        code: str  # Python code defining a Plugin subclass
    
    @router.post("/load")
    async def load_custom_plugin(req: CustomPluginRequest):
        import os
        if os.environ.get("PY_SWF_ENABLE_CUSTOM_PLUGINS") != "1":
            raise HTTPException(
                status_code=403,
                detail="Custom plugin loading is disabled for security. Set PY_SWF_ENABLE_CUSTOM_PLUGINS=1 to enable."
            )
        # Ejecutar código del plugin en sandbox limitado
        local_ns = {"Plugin": Plugin, "plugin_registry": plugin_registry}
        try:
            exec(req.code, {"__builtins__": {}}, local_ns)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Plugin code error: {e}")
        
        # Buscar instancia de Plugin en el namespace
        for val in local_ns.values():
            if isinstance(val, Plugin):
                plugin_registry.register(val)
                return {"ok": True, "name": val.name}
        
        raise HTTPException(status_code=400, detail="No Plugin subclass found in code")
    
    @router.delete("/{name}")
    def unload_plugin(name: str):
        if name not in plugin_registry.plugins:
            raise HTTPException(status_code=404, detail="Plugin not found")
        plugin_registry.unregister(name)
        return {"ok": True}
    
    return router


# ===== Scripting Console (para uso interactivo) =====

class ScriptingConsole:
    """Consola de scripting para automatización interactiva."""
    
    def __init__(self, session_id: str):
        from server.routers.files import registry
        self.session = registry.get(session_id)
        self.locals = {
            "swf": self.session.swf,
            "session_id": session_id,
            "tags": self.session.swf.tags,
            # Funciones de utilidad
            "get_tag": lambda tid: get_tag_by_id(self.session.swf, tid),
            "get_tags": lambda tt: get_tags_by_type(self.session.swf, tt),
            "find_name": lambda cid: find_symbol_name(self.session.swf, cid),
            "iter_sprites": lambda: iter_sprites(self.session.swf),
            "iter_frames": lambda: iter_frames(self.session.swf),
            "export": lambda tag: export_resource(tag, self.session.swf.tags),
            "decompile": lambda tag: decompile_script(tag, self.session.swf.tags),
            "save": lambda: self.session.swf.save_file(f"modified_{self.session.filename}"),
        }
    
    def run(self, code: str):
        """Ejecuta código en el contexto de la consola."""
        import os
        if os.environ.get("PY_SWF_ENABLE_SCRIPTING_CONSOLE") != "1":
            return {"ok": False, "error": "Scripting console is disabled for security. Set PY_SWF_ENABLE_SCRIPTING_CONSOLE=1 to enable."}
        try:
            exec(code, {"__builtins__": __builtins__}, self.locals)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}
    
    def eval(self, expr: str):
        """Evalúa una expresión."""
        import os
        if os.environ.get("PY_SWF_ENABLE_SCRIPTING_CONSOLE") != "1":
            return {"ok": False, "error": "Scripting console is disabled for security. Set PY_SWF_ENABLE_SCRIPTING_CONSOLE=1 to enable."}
        try:
            result = eval(expr, {"__builtins__": __builtins__}, self.locals)
            return {"ok": True, "result": repr(result)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def create_scripting_console(session_id: str) -> ScriptingConsole:
    """Factory para crear consola de scripting."""
    return ScriptingConsole(session_id)