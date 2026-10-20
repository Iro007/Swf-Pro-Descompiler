"""Lossless Font2/3 edit boundary: retain flags, language, codes and layout.

Only the requested glyph is reassembled. Offset tables are relative to their
own beginning and contain NumGlyphs offsets plus exactly one CodeTableOffset.
"""
from copy import deepcopy
from .swf_parser import BitStream, SWFTag, make_rect
from .text_fonts import _read_glyph_records
from .font_edit import FontWriter, _write_glyph_records


def read_font(tag):
    if tag.tag_type not in (48, 75):
        raise ValueError("Solo DefineFont2/DefineFont3 admiten esta edición")
    data = tag.data
    if len(data) < 7:
        raise ValueError("Fuente truncada")
    flags, name_size = data[2], data[4]
    count_at = 5 + name_size
    if count_at + 2 > len(data):
        raise ValueError("Nombre de fuente truncado")
    count = int.from_bytes(data[count_at:count_at + 2], "little")
    if count > 8192:
        raise ValueError("La fuente supera el presupuesto de glifos")
    base, width = count_at + 2, 4 if flags & 8 else 2
    table_size = (count + 1) * width
    if base + table_size > len(data):
        raise ValueError("Tabla de offsets de fuente truncada")
    offsets = [int.from_bytes(data[base + i * width:base + (i + 1) * width], "little") for i in range(count + 1)]
    if offsets[0] < table_size or offsets != sorted(offsets) or base + offsets[-1] > len(data):
        raise ValueError("Offsets de glifos fuera del archivo")
    code_start = base + offsets[-1]
    code_width = 2 if flags & 4 or tag.tag_type == 75 else 1
    code_end = code_start + count * code_width
    if code_end > len(data):
        raise ValueError("Tabla de caracteres truncada")
    codes = [int.from_bytes(data[code_start + i * code_width:code_start + (i + 1) * code_width], "little") for i in range(count)]
    raw = [data[base + offsets[i]:base + offsets[i + 1]] for i in range(count)]
    glyphs = []
    for glyph in raw:
        if not glyph:
            raise ValueError("Glifo sin bytecode")
        glyphs.append(_read_glyph_records(BitStream(glyph)))
    layout = None
    if flags & 128:
        if code_end + 6 + 2 * count > len(data):
            raise ValueError("Layout de fuente truncado")
        layout = {"ascent": int.from_bytes(data[code_end:code_end + 2], "little"),
                  "descent": int.from_bytes(data[code_end + 2:code_end + 4], "little"),
                  "leading": int.from_bytes(data[code_end + 4:code_end + 6], "little", signed=True)}
    return {"font_id": int.from_bytes(data[:2], "little"), "name": data[5:count_at].decode("utf-8", errors="strict"),
            "num_glyphs": count, "bold": bool(flags & 1), "italic": bool(flags & 2), "em_scale": 20480 if tag.tag_type == 75 else 1024,
            "codes": codes, "glyphs": glyphs, "layout": layout,
            "_flags": flags, "_base": base, "_offsets": offsets, "_width": width, "_raw": raw,
            "_code_start": code_start, "_code_end": code_end}


def properties(tag, values):
    font = read_font(tag)
    data, flags = tag.data, font["_flags"]
    for key, bit in (("bold", 1), ("italic", 2)):
        if key in values:
            if not isinstance(values[key], bool):
                raise ValueError("Propiedad de fuente booleana inválida")
            flags = flags | bit if values[key] else flags & ~bit
    name = values.get("font_name", font["name"]).encode("utf-8")
    if len(name) > 255:
        raise ValueError("Nombre de fuente superior a 255 bytes")
    # Moving the name shifts the whole offset table; its relative offsets and
    # every glyph/code/layout byte remain unchanged.
    tail = bytearray(data[font["_base"] - 2:])
    for key, start, signed in (("ascent", 0, False), ("descent", 2, False), ("leading", 4, True)):
        if key not in values:
            continue
        if font["layout"] is None:
            raise ValueError("La fuente no contiene métricas de layout")
        value = int(values[key])
        valid = -32768 <= value <= 32767 if signed else 0 <= value <= 65535
        if not valid:
            raise ValueError("Métrica de fuente fuera de rango")
        position = font["_code_end"] - (font["_base"] - 2) + start
        tail[position:position + 2] = value.to_bytes(2, "little", signed=signed)
    candidate = SWFTag(tag.tag_type, data[:2] + bytes([flags, data[3], len(name)]) + name + bytes(tail))
    read_font(candidate)
    return candidate


def move_point(tag, glyph_index, point_index, x, y):
    font = read_font(tag)
    if not 0 <= glyph_index < font["num_glyphs"]:
        raise ValueError("Glifo fuera de rango")
    glyph = deepcopy(font["glyphs"][glyph_index])
    points = [point for path in glyph for point in path]
    if not 0 <= point_index < len(points):
        raise ValueError("Punto fuera de rango")
    if not (-2**27 <= x <= 2**27 and -2**27 <= y <= 2**27):
        raise ValueError("Coordenadas de glifo fuera de rango")
    points[point_index].update(x=int(x), y=int(y))
    writer = FontWriter()
    _write_glyph_records(writer, glyph)
    raw = list(font["_raw"])
    raw[glyph_index] = writer.get_bytes()
    width = font["_width"]
    if (font["num_glyphs"] + 1) * width + sum(map(len, raw)) > 65535:
        width = 4
    flags = font["_flags"] | 8 if width == 4 else font["_flags"] & ~8
    cursor = (font["num_glyphs"] + 1) * width
    offsets = []
    for value in raw:
        offsets.append(cursor)
        cursor += len(value)
    offsets.append(cursor)
    layout_tail = tag.data[font["_code_start"]:]
    if font["layout"]:
        # Font bounds follow the character table, six metric bytes and the
        # advance table. Retain unedited records and kerning data exactly.
        code_bytes = font["_code_end"] - font["_code_start"]
        position = code_bytes + 6 + 2 * font["num_glyphs"]
        begin, end = None, None
        for i in range(font["num_glyphs"]):
            if position >= len(layout_tail):
                raise ValueError("Rectángulo de glifo truncado")
            count = layout_tail[position] >> 3
            length = (5 + count * 4 + 7) // 8
            if count > 31 or position + length > len(layout_tail):
                raise ValueError("Bounds de fuente fuera de rango")
            if i == glyph_index:
                begin, end = position, position + length
            position += length
        coords = [(point["x"], point["y"]) for path in glyph for point in path]
        coords += [(point["cx"], point["cy"]) for path in glyph for point in path if "cx" in point]
        rect = make_rect(min(v[0] for v in coords), max(v[0] for v in coords), min(v[1] for v in coords), max(v[1] for v in coords))
        layout_tail = layout_tail[:begin] + rect + layout_tail[end:]
    prefix = bytearray(tag.data[:font["_base"]])
    prefix[2] = flags
    result = SWFTag(tag.tag_type, bytes(prefix) + b"".join(v.to_bytes(width, "little") for v in offsets) + b"".join(raw) + layout_tail)
    read_font(result)
    return result
