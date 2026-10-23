"""Common engine facade used by the API, CLI and future desktop workers.

The facade deliberately deals in bytes and plain dictionaries. That keeps the
native Python implementation independent from FastAPI and gives the JPEXS
adapter a stable boundary while its Java implementation evolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import resources, robust_parser
from .avm1 import assemble_avm1, disassemble_avm1
from .avm2 import ABCFile, assemble_instructions, build_method_mapping, disassemble_instructions
from .decompile.backends import (
    JPEXSBackend,
    compare_decompilation,
    decompile_with_fallback,
)
from .swf_parser import SWFFile


SCRIPT_TAGS = {12, 59, 72, 82}
IMAGE_TAGS = {6, 20, 21, 35, 36, 90}


class EngineOperationError(RuntimeError):
    """An operation is unsupported or failed in a selected engine."""


@dataclass(frozen=True)
class EngineCapabilities:
    """Advertised feature matrix for one engine."""

    name: str
    operations: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "operations": dict(self.operations)}


def _parse(data: bytes) -> SWFFile:
    swf = SWFFile()
    swf.read_bytes(data)
    return swf


def _inspect(swf: SWFFile, filename: str = "untitled.swf") -> dict[str, Any]:
    rect = swf.rect
    return {
        "filename": filename,
        "signature": swf.signature,
        "version": swf.version,
        "frame_rate": swf.frame_rate,
        "frame_count": swf.frame_count,
        "rect": dict(rect),
        "width": (rect["xmax"] - rect["xmin"]) / 20.0,
        "height": (rect["ymax"] - rect["ymin"]) / 20.0,
        "tag_count": len(swf.tags),
    }


def _tag(swf: SWFFile, index: int):
    if index < 0 or index >= len(swf.tags):
        raise EngineOperationError(f"Tag index out of range: {index}")
    return swf.tags[index]


def _script_tag(swf: SWFFile, index: int):
    tag = _tag(swf, index)
    if tag.tag_type not in SCRIPT_TAGS:
        raise EngineOperationError("Tag is not an ActionScript tag")
    return tag


class DecompilerEngine:
    """Stable byte-oriented contract shared by native and delegated engines."""

    name = "base"
    capabilities = EngineCapabilities("base", {})

    def inspect_swf(self, swf_bytes: bytes, filename: str = "untitled.swf") -> dict[str, Any]:
        return _inspect(_parse(swf_bytes), filename)

    def list_tags(self, swf_bytes: bytes) -> list[dict[str, Any]]:
        swf = _parse(swf_bytes)
        return [
            {
                "index": index,
                "code": tag.tag_type,
                "name": tag.name,
                "size": len(tag.data),
                "char_id": tag.char_id,
                "parse_error": tag.parse_error,
            }
            for index, tag in enumerate(swf.tags)
        ]

    def decompile(self, swf_bytes: bytes, tag_index: int, filename: str = "untitled.swf") -> dict[str, Any]:
        raise NotImplementedError

    def disassemble(self, swf_bytes: bytes, tag_index: int) -> dict[str, Any]:
        swf = _parse(swf_bytes)
        tag = _script_tag(swf, tag_index)
        if tag.tag_type in (12, 59):
            offset = 2 if tag.tag_type == 59 else 0
            return {
                "kind": "avm1",
                "scripts": [{"body_index": 0, "name": tag.name, "code": disassemble_avm1(tag.data[offset:])}],
            }
        parsed = tag.parse_doabc()
        if parsed is None:
            raise EngineOperationError("Malformed DoABC tag")
        _, _, abc_bytes = parsed
        abc = ABCFile()
        abc.parse(abc_bytes)
        mapping = build_method_mapping(abc)
        return {
            "kind": "avm2",
            "scripts": [
                {
                    "body_index": body_index,
                    "name": mapping.get(body.method, f"method_{body.method}"),
                    "code": disassemble_instructions(abc.constant_pool, body.code, exact=True, exceptions=body.exceptions),
                }
                for body_index, body in enumerate(abc.method_bodies)
            ],
        }

    def assemble(self, swf_bytes: bytes, tag_index: int, code: str, body_index: int = 0) -> bytes:
        from .editing import edit_script
        swf = _parse(swf_bytes)
        replacement, _ = edit_script(_script_tag(swf, tag_index), code, body_index, swf_version=swf.version)
        swf.tags[tag_index] = replacement
        return self.save_swf(swf.save_bytes())

    def compile_source(self, swf_bytes: bytes, tag_index: int, source: str, body_index: int = 0) -> bytes:
        from .editing import edit_script
        swf = _parse(swf_bytes)
        replacement, _ = edit_script(_script_tag(swf, tag_index), source, body_index, source=True, swf_version=swf.version)
        swf.tags[tag_index] = replacement
        return self.save_swf(swf.save_bytes())

    def edit_unit(self, swf_bytes: bytes, locator: str | dict, code: str, *, source: bool = False) -> bytes:
        from .unit_editing import edit_unit
        return edit_unit(swf_bytes, locator, code, source=source)

    def export_resource(self, swf_bytes: bytes, tag_index: int) -> tuple[bytes, str]:
        from . import sounds, shapes, morph, text_fonts
        swf = _parse(swf_bytes)
        tag = _tag(swf, tag_index)
        if tag.tag_type in IMAGE_TAGS:
            data, extension = resources.export_image(tag, jpeg_tables=resources.find_jpeg_tables(swf.tags))
        elif tag.tag_type == 14:
            data, extension = sounds.export_sound(tag)
        elif tag.tag_type == 87 and len(tag.data) >= 6:
            return tag.data[6:], "bin"
        elif tag.tag_type == 37:
            text = text_fonts.parse_edit_text(tag)
            return text["initial_text"].encode("utf-8"), "txt"
        elif tag.tag_type in (2, 22, 32, 83):
            from .fla_export import make_bitmap_resolver
            return shapes.shape_to_svg(tag, bitmap_resolver=make_bitmap_resolver(swf.tags)).encode("utf-8"), "svg"
        elif tag.tag_type in (46, 84):
            return morph.morph_to_svg(tag).encode("utf-8"), "svg"
        else:
            raise EngineOperationError("Native export is not supported for this tag")
        if data is None:
            raise EngineOperationError("Could not decode resource")
        return data, extension

    def replace_resource(self, swf_bytes: bytes, tag_index: int, resource: bytes, extension: str) -> bytes:
        from . import sounds, text_fonts
        swf = _parse(swf_bytes)
        tag = _tag(swf, tag_index)
        if tag.tag_type in IMAGE_TAGS:
            replacement = resources.replace_image(tag, resource, extension)
        elif tag.tag_type == 14:
            replacement = sounds.replace_define_sound(tag, resource, extension)
        elif tag.tag_type == 37:
            text = resource.decode("utf-8")
            if "\0" in text:
                raise EngineOperationError("SWF text cannot contain null bytes")
            replacement = text_fonts.replace_edit_text_string(tag, text)
        elif tag.tag_type == 87 and len(tag.data) >= 6:
            replacement = type(tag)(87, tag.data[:6] + resource)
        else:
            raise EngineOperationError("Native replacement is not supported for this tag")
        swf.tags[tag_index] = replacement
        return self.save_swf(swf.save_bytes())

    def save_swf(self, swf_bytes: bytes) -> bytes:
        data = _parse(swf_bytes).save_bytes()
        validation = self.validate(data)
        if not validation["valid"]:
            raise EngineOperationError("Invalid SWF: " + "; ".join(validation["errors"]))
        return data

    def validate(self, swf_bytes: bytes) -> dict[str, Any]:
        from .validation import validate_swf_bytes
        return validate_swf_bytes(swf_bytes)

    def export_fla(self, swf_bytes: bytes) -> bytes:
        raise EngineOperationError(f"{self.name} does not expose FLA export through the common facade")

    def compile_classes(self, source: str, compression: str = "CWS") -> bytes:
        raise EngineOperationError(f"{self.name} does not support native class-program compilation")


class NativePythonEngine(DecompilerEngine):
    name = "native"
    capabilities = EngineCapabilities(
        name,
        {
            "inspect_swf": "native",
            "list_tags": "native",
            "decompile": "partial",
            "disassemble": "native",
            "assemble": "native",
            "edit_unit": "native",
            "compile_source": "partial",
            "compile_classes": "partial",
            "export_resource": "partial",
            "replace_resource": "partial",
            "save_swf": "native",
            "export_fla": "unsupported",
            "validate": "native",
        },
    )

    def compile_classes(self, source: str, compression: str = "CWS", *, sources=None, document_class=None, width=550, height=400, frame_rate=24) -> bytes:
        from .compiler.class_compiler import compile_class_program,class_program_swf
        return self.save_swf(class_program_swf(compile_class_program(source, sources=sources),compression, document_class=document_class, width=width, height=height, frame_rate=frame_rate))

    def decompile(self, swf_bytes: bytes, tag_index: int, filename: str = "untitled.swf") -> dict[str, Any]:
        swf = _parse(swf_bytes)
        tag = _script_tag(swf, tag_index)
        return decompile_with_fallback(tag, swf_bytes, filename, engine="native").as_dict()


class JPEXSJavaEngine(DecompilerEngine):
    name = "jpexs"
    capabilities = EngineCapabilities(
        name,
        {
            "inspect_swf": "unsupported",
            "list_tags": "unsupported",
            "decompile": "delegated",
            "disassemble": "unsupported",
            "assemble": "unsupported",
            "edit_unit": "unsupported",
            "compile_source": "unsupported",
            "export_resource": "unsupported",
            "replace_resource": "unsupported",
            "save_swf": "unsupported",
            "export_fla": "unsupported",
            "validate": "unsupported",
        },
    )

    def __init__(self, backend: JPEXSBackend | None = None):
        self.backend = backend or JPEXSBackend()

    def decompile(self, swf_bytes: bytes, tag_index: int, filename: str = "untitled.swf") -> dict[str, Any]:
        swf = _parse(swf_bytes)
        tag = _script_tag(swf, tag_index)
        return self.backend.decompile(tag, swf_bytes, filename).as_dict()

    def close(self):
        self.backend.close()

    def _unsupported(self, *args, **kwargs):
        raise EngineOperationError("The optional JPEXS adapter currently supports decompilation only; select native for editing")

    inspect_swf = list_tags = disassemble = assemble = compile_source = edit_unit = _unsupported
    export_resource = replace_resource = save_swf = export_fla = validate = _unsupported


def engine_capabilities() -> list[dict[str, Any]]:
    return [NativePythonEngine.capabilities.as_dict(), JPEXSJavaEngine.capabilities.as_dict()]


def get_engine(name: str = "native") -> DecompilerEngine:
    if name == "native":
        return NativePythonEngine()
    if name == "jpexs":
        return JPEXSJavaEngine()
    if name == "auto":
        jpexs = JPEXSJavaEngine()
        if jpexs.backend.available:
            return jpexs
        return NativePythonEngine()
    raise EngineOperationError(f"Unknown engine: {name}")


def compare_engines(swf_bytes: bytes, tag_index: int, filename: str = "untitled.swf") -> dict[str, Any]:
    swf = _parse(swf_bytes)
    tag = _script_tag(swf, tag_index)
    return compare_decompilation(tag, swf_bytes, filename)
