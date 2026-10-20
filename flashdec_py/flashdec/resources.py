"""Native resource export with PySWF (used when JPEXS is absent or disabled)."""
from __future__ import annotations

import struct
from pathlib import Path

from .workspace import import_pyswf

import_pyswf()

from py_swf.resources import export_image, find_jpeg_tables  # noqa: E402
from py_swf.shapes import shape_to_svg  # noqa: E402
from py_swf.sounds import export_sound  # noqa: E402
from py_swf.text_fonts import font_to_svg, parse_edit_text, parse_font  # noqa: E402

IMAGE_TAGS = {6, 20, 21, 35, 36, 90}
SHAPE_TAGS = {2, 22, 32, 83}
SOUND_TAGS = {14}
FONT_TAGS = {10, 48, 75}
EDIT_TEXT_TAGS = {37}
BINARY_TAGS = {87}
SYMBOL_CLASS = 76


def export_all(swf, destination: Path) -> dict:
    """Write images/shapes/sounds/fonts/texts/binaryData; return counts and errors."""
    counts = {"images": 0, "shapes": 0, "sounds": 0, "fonts": 0, "texts": 0, "binaryData": 0, "symbolClass": 0}
    errors = []
    tables = find_jpeg_tables(swf.tags)

    def write(folder: str, name: str, data: bytes | str, key: str):
        target = destination / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            target.write_text(data, encoding="utf-8")
        else:
            target.write_bytes(data)
        counts[key] += 1

    for index, tag in enumerate(swf.tags):
        kind = tag.tag_type
        cid = tag.char_id if tag.char_id is not None else index
        try:
            if kind in IMAGE_TAGS:
                data, ext = export_image(tag, jpeg_tables=tables)
                if data is None:
                    raise ValueError("could not decode image")
                write("images", f"{cid}.{ext}", data, "images")
            elif kind in SHAPE_TAGS:
                write("shapes", f"{cid}.svg", shape_to_svg(tag), "shapes")
            elif kind in SOUND_TAGS:
                data, ext = export_sound(tag)
                if data is None:
                    raise ValueError("unsupported sound format")
                write("sounds", f"{cid}.{ext}", data, "sounds")
            elif kind in FONT_TAGS:
                font = parse_font(tag)
                if font:
                    write("fonts", f"{cid}.svg", font_to_svg(font), "fonts")
            elif kind in EDIT_TEXT_TAGS:
                text = parse_edit_text(tag) or {}
                body = text.get("initial_text", "")
                if body:
                    write("texts", f"{cid}.txt", body, "texts")
            elif kind in BINARY_TAGS and len(tag.data) >= 6:
                write("binaryData", f"{cid}.bin", tag.data[6:], "binaryData")
            elif kind == SYMBOL_CLASS and len(tag.data) >= 2:
                rows, pos = [], 2
                for _ in range(struct.unpack_from("<H", tag.data)[0]):
                    char_id = struct.unpack_from("<H", tag.data, pos)[0]
                    end = tag.data.index(b"\x00", pos + 2)
                    rows.append(f"{char_id};{tag.data[pos + 2:end].decode('utf-8', 'replace')}")
                    pos = end + 1
                write("symbolClass", "symbols.csv", "\n".join(rows) + "\n", "symbolClass")
        except Exception as error:
            errors.append({"tag": index, "type": tag.name, "error": str(error)})
    return {"counts": counts, "errors": errors}
