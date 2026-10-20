"""PySWF as an in-process engine.

Script files use the same relative paths as JPEXS so the merger can compare
both engines file by file:
  AS3 classes            pkg/sub/Name.as
  AS1/2 frame actions    frame_N/DoAction.as
  sprite frame actions   DefineSprite_ID[_export]/frame_N/DoAction.as
  AS2 classes            __Packages/pkg/Name.as  (DoInitAction with export name)
  other init actions     frame_N/DoInitAction.as
  button / clip events   <container>/<event file>, paired by directory order
"""
from __future__ import annotations

import hashlib
import re
import struct
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .workspace import import_pyswf

import_pyswf()

from py_swf.avm1 import disassemble_avm1  # noqa: E402
from py_swf.avm2 import ABCFile, build_method_mapping, disassemble_instructions  # noqa: E402
from py_swf.decompile import avm1_dec  # noqa: E402
from py_swf.decompile.as3_outline import _split_qname  # noqa: E402
from py_swf.abc_symbols import AbcSymbolIndex  # noqa: E402
from py_swf.class_documents import reconstruct_abc_documents  # noqa: E402
from py_swf.script_inventory import embedded_actions, nested_tags  # noqa: E402
from py_swf.swf_parser import SWFFile  # noqa: E402

TAG_SHOW_FRAME, TAG_DO_ACTION, TAG_DO_INIT_ACTION = 1, 12, 59
TAG_DEFINE_SPRITE, TAG_EXPORT_ASSETS, TAG_SYMBOL_CLASS = 39, 56, 76
TAG_DO_ABC, TAG_DO_ABC2 = 72, 82
EVENT_TAGS = {7: "DefineButton", 34: "DefineButton2", 26: "PlaceObject2", 70: "PlaceObject3", 94: "PlaceObject4"}
_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def make_file_name(name: str) -> str:
    """Mirror of JPEXS Helper.makeFileName for the characters that matter on disk."""
    cleaned = _INVALID.sub("_", name).strip()
    return cleaned or "_"


@dataclass
class NativeScript:
    path: str
    source: str
    pcode: str = ""
    language: str = "as2"
    error: str | None = None
    pairable: bool = False  # path is a guess; merger may pair it by directory order
    # Canonical benchmark identities this file covers, with the evidence our own
    # parse derived (action bytecode hash, or ABC hash + class name).
    units: list[dict] = field(default_factory=list)


@dataclass
class NativeResult:
    scripts: list[NativeScript] = field(default_factory=list)
    header: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _export_names(tags) -> dict[int, str]:
    names = {}
    for tag in tags:
        if tag.tag_type == TAG_EXPORT_ASSETS and len(tag.data) >= 2:
            data, pos = tag.data, 2
            for _ in range(struct.unpack_from("<H", data)[0]):
                if pos + 2 > len(data):
                    break
                char_id = struct.unpack_from("<H", data, pos)[0]
                end = data.find(b"\x00", pos + 2)
                if end < 0:
                    break
                names.setdefault(char_id, data[pos + 2:end].decode("utf-8", "replace"))
                pos = end + 1
    return names


def _avm1(code: bytes) -> tuple[str, str, str | None]:
    try:
        pcode = disassemble_avm1(code)
    except Exception as error:  # damaged bytecode still deserves a file
        pcode = f"; disassembly failed: {error}\n"
    try:
        source, error = avm1_dec.decompile_avm1(code)
    except Exception as exc:
        source, error = None, f"{type(exc).__name__}: {exc}"
    if error or not source:
        source = f"// Native reconstruction incomplete: {error or 'empty output'}\n" + (source or "")
    return source, pcode, error


class _Paths:
    def __init__(self):
        self.used: set[str] = set()

    def unique(self, path: str) -> str:
        """Same duplicate rule as JPEXS AS2ScriptExporter: name, name_2, name_3..."""
        stem, dot, ext = path.rpartition(".")
        candidate, i = path, 1
        while candidate in self.used:
            i += 1
            candidate = f"{stem}_{i}{dot}{ext}"
        self.used.add(candidate)
        return candidate


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tag_id(path: tuple) -> str:
    return "tag:" + "/".join(map(str, path))


def _walk_avm1(result: NativeResult, tags, version: int, exports: dict[int, str], prefix: str, paths: _Paths, parent: tuple = ()):
    frame = 1
    for ordinal_in_parent, tag in tags:
        kind = tag.tag_type
        tag_id = _tag_id((*parent, ordinal_in_parent))
        try:
            if kind == TAG_SHOW_FRAME:
                frame += 1
            elif kind == TAG_DO_ACTION:
                source, pcode, error = _avm1(tag.data)
                path = paths.unique(f"{prefix}frame_{frame}/DoAction.as")
                units = [{"document_id": tag_id + "/actions", "bytecode_sha256": _sha(tag.data)}]
                result.scripts.append(NativeScript(path, source, pcode, "as2", error, units=units))
            elif kind == TAG_DO_INIT_ACTION and len(tag.data) >= 2:
                sprite_id = struct.unpack_from("<H", tag.data)[0]
                source, pcode, error = _avm1(tag.data[2:])
                export = exports.get(sprite_id)
                if export:
                    parts = [make_file_name(p) for p in export.split(".")]
                    path = "/".join(parts) + ".as"
                else:
                    path = f"{prefix}frame_{frame}/DoInitAction.as"
                units = [{"document_id": tag_id + "/actions", "bytecode_sha256": _sha(tag.data[2:])}]
                result.scripts.append(NativeScript(paths.unique(path), source, pcode, "as2", error, units=units))
            elif kind == TAG_DEFINE_SPRITE and len(tag.data) >= 4:
                sprite_id = struct.unpack_from("<H", tag.data)[0]
                name = f"DefineSprite_{sprite_id}"
                if sprite_id in exports:
                    name += "_" + exports[sprite_id]
                _walk_avm1(result, nested_tags(tag.data[4:]), version, exports,
                           f"{make_file_name(name)}/", paths, (*parent, ordinal_in_parent))
            elif kind in EVENT_TAGS:
                events = list(embedded_actions(tag, version))
                if not events:
                    continue
                if kind in (7, 34):
                    char_id = struct.unpack_from("<H", tag.data)[0]
                    container = f"{EVENT_TAGS[kind]}_{char_id}"
                    if char_id in exports:
                        container += "_" + exports[char_id]
                    container = make_file_name(container) + "/"
                    label = "BUTTONCONDACTION"
                else:
                    container = f"{prefix}frame_{frame}/{EVENT_TAGS[kind]}_{tag.char_id or 0}/"
                    label = "CLIPACTIONRECORD"
                for event_kind, ordinal, start, end, _ in events:
                    code = tag.data[start:end]
                    source, pcode, error = _avm1(code)
                    path = paths.unique(f"{container}{label}_{ordinal}.as")
                    units = [{"document_id": f"{tag_id}/{event_kind}:{ordinal}", "bytecode_sha256": _sha(code)}]
                    result.scripts.append(NativeScript(path, source, pcode, "as2", error, pairable=True, units=units))
        except Exception as error:
            result.errors.append(f"{prefix}{tag.name}: {error}")


def _abc_bytes(tag) -> bytes | None:
    parsed = tag.parse_doabc()
    return None if parsed is None else parsed[2]


def _walk_avm2(result: NativeResult, tags, paths: _Paths):
    for index, tag in tags:
        if tag.tag_type not in (TAG_DO_ABC, TAG_DO_ABC2):
            continue
        try:
            abc_bytes = _abc_bytes(tag)
            if abc_bytes is None:
                raise ValueError("malformed DoABC tag")
            abc = ABCFile()
            abc.parse(abc_bytes)
            mapping = build_method_mapping(abc)
            method_pcode = {}
            for body in abc.method_bodies:
                name = mapping.get(body.method, f"method_{body.method}")
                try:
                    listing = disassemble_instructions(abc.constant_pool, body.code, exact=True, exceptions=body.exceptions)
                except Exception as error:
                    listing = f"; disassembly failed: {error}"
                method_pcode[body.method] = f"; method {body.method}: {name}\n{listing}\n"
            # Class documents without the editor's per-method compiler checks.
            abc._native_symbol_index = AbcSymbolIndex([abc])
            sections = reconstruct_abc_documents(abc)
            for section in sections:
                if section.get("scope") != "class":
                    continue
                ci = section["class_index"]
                pkg, name = _split_qname(abc.constant_pool, abc.instances[ci].name)
                parts = [make_file_name(p) for p in pkg.split(".") if p] + [make_file_name(name)]
                source = section.get("source") or ""
                error = section.get("error")
                if error or section.get("completeness") != "complete":
                    source = f"// Native reconstruction incomplete: {error or section.get('completeness')}\n" + source
                pcode = "".join(method_pcode.get(int(u.split(":")[1]), "")
                                for u in section.get("unit_ids", []) if u.startswith("method:"))
                path = paths.unique("/".join(parts) + ".as")
                units = [{"document_id": f"{_tag_id((index,))}/class:{ci}", "abc_sha256": _sha(abc_bytes),
                          "name": f"{pkg}::{name}" if pkg else name}]
                result.scripts.append(NativeScript(path, source, pcode, "as3", error, units=units))
        except Exception as error:
            result.errors.append(f"tag {index} {tag.name}: {type(error).__name__}: {error}")
            result.errors.append(traceback.format_exc(limit=3))


def decompile(data: bytes) -> NativeResult:
    started = time.perf_counter()
    result = NativeResult()
    swf = SWFFile()
    swf.read_bytes(data)
    result.header = {
        "signature": swf.signature, "version": swf.version, "frame_rate": swf.frame_rate,
        "frame_count": swf.frame_count,
        "width": (swf.rect["xmax"] - swf.rect["xmin"]) / 20.0,
        "height": (swf.rect["ymax"] - swf.rect["ymin"]) / 20.0,
        "tag_count": len(swf.tags),
    }
    tags = list(enumerate(swf.tags))
    paths = _Paths()
    _walk_avm2(result, tags, paths)
    _walk_avm1(result, tags, swf.version, _export_names(swf.tags), "", paths)
    result.seconds = time.perf_counter() - started
    return result


def load(path: Path) -> SWFFile:
    swf = SWFFile()
    swf.read_bytes(path.read_bytes())
    return swf
