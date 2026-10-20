"""Transactional editing of inventory units, including nested AVM1 events."""
import hashlib

from .editing import edit_script
from .script_inventory import Cursor, enumerate_scripts
from .swf_parser import SWFFile, SWFTag
from .validation import validate_swf_bytes


def resolve_unit(movie, locator):
    if isinstance(locator, str):
        document_id, expected = locator, None
    elif isinstance(locator, dict):
        document_id, expected = locator.get("document_id"), locator.get("bytecode_sha256")
    else:
        raise ValueError("Unit locator must be a document_id or inventory metadata")
    if not isinstance(document_id, str):
        raise ValueError("Unit locator requires document_id")
    matches = [unit for unit in enumerate_scripts(movie) if unit.document_id == document_id]
    if len(matches) != 1 or matches[0].diagnostic:
        raise ValueError("Unknown or damaged script unit: " + document_id)
    unit = matches[0]
    if expected is not None and expected != hashlib.sha256(unit.code).hexdigest():
        raise ValueError("Script unit is stale; refresh the inventory before editing")
    return unit


def _replace_nested(data, path, replacement):
    """Retain every unedited tag record byte for byte, including long headers."""
    cursor, index = Cursor(data), 0
    while cursor.pos < len(data):
        begin = cursor.pos
        record = cursor.uint(2)
        kind, size = record >> 6, record & 63
        if size == 63:
            size = cursor.uint(4)
        payload = cursor.take(size)
        if index == path[0]:
            if len(path) > 1:
                if kind != 39 or len(payload) < 4:
                    raise ValueError("Unit path traverses a non-sprite tag")
                replacement = SWFTag(39, payload[:4] + _replace_nested(payload[4:], path[1:], replacement))
            return data[:begin] + replacement.pack() + data[cursor.pos:]
        if kind == 0:
            break
        index += 1
    raise ValueError("Script unit tag path is out of range")


def _embedded_tag(unit, code):
    tag, start, end = unit.tag, unit.byte_start, unit.byte_end
    payload = bytearray(tag.data)
    if unit.kind == "button2":
        size_position = start - 4
        size = int.from_bytes(payload[size_position:size_position + 2], "little")
        if size:
            adjusted = size + len(code) - (end - start)
            if not 4 <= adjusted <= 65535:
                raise ValueError("ButtonCondAction exceeds its UI16 size limit")
            payload[size_position:size_position + 2] = adjusted.to_bytes(2, "little")
    elif unit.kind == "clip":
        has_key = bool(unit.event_flags & 0x20000)
        size_position = start - 4 - has_key
        payload[size_position:size_position + 4] = (len(code) + has_key).to_bytes(4, "little")
    elif unit.kind != "button":
        raise ValueError("Unsupported embedded script container")
    return SWFTag(tag.tag_type, bytes(payload[:start]) + code + bytes(payload[end:]))


def edit_unit(swf_bytes, locator, code, *, source=False):
    """Return a validated SWF; no caller-owned bytes or objects are modified.

    locator is an inventory document_id or metadata dict with document_id and
    optional bytecode_sha256. code is source text when source=True, otherwise
    exact P-code. The identity addresses a method or an event, not a whole ABC.
    """
    movie = SWFFile()
    movie.read_bytes(swf_bytes)
    unit = resolve_unit(movie, locator)
    if unit.kind in ("button", "button2", "clip"):
        replacement, _ = edit_script(SWFTag(12, unit.code), code, source=source, swf_version=movie.version)
        replacement = _embedded_tag(unit, replacement.data)
    else:
        replacement, _ = edit_script(unit.tag, code, unit.body_index or 0, source=source, swf_version=movie.version)
    outer = unit.tag_path[0]
    if len(unit.tag_path) > 1:
        parent = movie.tags[outer]
        replacement = SWFTag(39, parent.data[:4] + _replace_nested(parent.data[4:], unit.tag_path[1:], replacement))
    movie.tags[outer] = replacement
    result = movie.save_bytes()
    validation = validate_swf_bytes(result)
    if not validation["valid"]:
        raise ValueError("Invalid edited SWF: " + "; ".join(validation["errors"]))
    reopened = SWFFile()
    reopened.read_bytes(result, strict=True)
    resolve_unit(reopened, unit.document_id)
    return result
