"""
Parseo de fuentes y textos SWF.

- DefineFont2 (48) / DefineFont3 (75): glifos (SHAPE simple) -> paths SVG.
- DefineText (11) / DefineText2 (33): registros de texto posicionado -> SVG
  usando los glifos de las fuentes referenciadas.
- DefineEditText (37): propiedades -> dict.

Los glifos viven en un EM square de 1024 unidades (DefineFont2) o 20480
(DefineFont3, que es la misma estructura multiplicada por 20).

Referencia: SWF File Format Spec v19, cap. "Fonts and Text".
"""
from .swf_parser import BitStream, parse_rect, SWFTag

FONT_TAGS = (10, 48, 75, 91)
TEXT_TAGS = (11, 33)

def _read_glyph_records(stream):
    """
    Lee los shape records de un glifo (SHAPE sin arrays de estilos: los glifos
    solo usan move/line/curve y fill style 1). Coordenadas en unidades EM crudas.
    Devuelve lista de subpaths [{cmd, x, y[, cx, cy]}].
    """
    num_fill_bits = stream.read_bits(4)
    num_line_bits = stream.read_bits(4)
    subpaths = []
    cur = []
    x = y = 0
    for _ in range(100000):
        if stream.byte_offset >= len(stream.data):
            break
        edge = stream.read_bits(1)
        if edge == 0:
            flags = stream.read_bits(5)
            if flags == 0:
                break
            if flags & 0x01:  # move
                nbits = stream.read_bits(5)
                x = stream.read_signed_bits(nbits)
                y = stream.read_signed_bits(nbits)
                if cur:
                    subpaths.append(cur)
                cur = [{"cmd": "move", "x": x, "y": y}]
            if flags & 0x02:
                stream.read_bits(num_fill_bits)
            if flags & 0x04:
                stream.read_bits(num_fill_bits)
            if flags & 0x08:
                stream.read_bits(num_line_bits)
            # flags & 0x10 (new styles) no aparece en glifos
        else:
            straight = stream.read_bits(1)
            nbits = stream.read_bits(4) + 2
            if straight:
                if stream.read_bits(1):  # general line
                    dx = stream.read_signed_bits(nbits)
                    dy = stream.read_signed_bits(nbits)
                elif stream.read_bits(1):  # vertical
                    dx, dy = 0, stream.read_signed_bits(nbits)
                else:
                    dx, dy = stream.read_signed_bits(nbits), 0
                x += dx
                y += dy
                if not cur:
                    cur = [{"cmd": "move", "x": x, "y": y}]
                else:
                    cur.append({"cmd": "line", "x": x, "y": y})
            else:
                cx = x + stream.read_signed_bits(nbits)
                cy = y + stream.read_signed_bits(nbits)
                x = cx + stream.read_signed_bits(nbits)
                y = cy + stream.read_signed_bits(nbits)
                if not cur:
                    cur = [{"cmd": "move", "x": cx, "y": cy}]
                cur.append({"cmd": "curve", "cx": cx, "cy": cy, "x": x, "y": y})
    if cur:
        subpaths.append(cur)
    return subpaths

def glyph_path_d(subpaths, scale=1.0, dx=0.0, dy=0.0):
    parts = []
    for sp in subpaths:
        for seg in sp:
            px = seg["x"] * scale + dx
            py = seg["y"] * scale + dy
            if seg["cmd"] == "move":
                parts.append(f"M {px:.2f} {py:.2f}")
            elif seg["cmd"] == "line":
                parts.append(f"L {px:.2f} {py:.2f}")
            else:
                cx = seg["cx"] * scale + dx
                cy = seg["cy"] * scale + dy
                parts.append(f"Q {cx:.2f} {cy:.2f} {px:.2f} {py:.2f}")
    return " ".join(parts)

def parse_font(tag):
    """Parsea DefineFont2 (48) / DefineFont3 (75). Devuelve dict o None."""
    if tag.tag_type not in (48, 75):
        return None
    data = tag.data
    if len(data) < 7:
        return None
    font_id = int.from_bytes(data[0:2], "little")
    flags = data[2]
    has_layout = bool(flags & 0x80)
    wide_offsets = bool(flags & 0x08)
    wide_codes = bool(flags & 0x04)
    italic = bool(flags & 0x02)
    bold = bool(flags & 0x01)
    # data[3] = langcode
    name_len = data[4]
    name = data[5 : 5 + name_len].decode("utf-8", errors="replace").rstrip("\x00")
    pos = 5 + name_len
    num_glyphs = int.from_bytes(data[pos : pos + 2], "little")
    pos += 2

    off_size = 4 if wide_offsets else 2
    table_base = pos
    offsets = []
    for i in range(num_glyphs + 1):  # num_glyphs offsets + code table offset
        off = int.from_bytes(data[pos : pos + off_size], "little")
        offsets.append(table_base + off)
        pos += off_size

    glyphs = []
    for i in range(num_glyphs):
        start, end = offsets[i], offsets[i + 1]
        try:
            glyphs.append(_read_glyph_records(BitStream(data[start:end])))
        except Exception:
            glyphs.append([])

    codes = []
    cpos = offsets[num_glyphs] if num_glyphs else pos
    code_size = 2 if (wide_codes or tag.tag_type == 75) else 1
    for i in range(num_glyphs):
        if cpos + code_size > len(data):
            break
        codes.append(int.from_bytes(data[cpos : cpos + code_size], "little"))
        cpos += code_size

    layout = None
    if has_layout and cpos + 6 <= len(data):
        layout = {
            "ascent": int.from_bytes(data[cpos : cpos + 2], "little"),
            "descent": int.from_bytes(data[cpos + 2 : cpos + 4], "little"),
            "leading": int.from_bytes(data[cpos + 4 : cpos + 6], "little", signed=True),
        }

    return {
        "font_id": font_id,
        "name": name,
        "italic": italic,
        "bold": bold,
        "num_glyphs": num_glyphs,
        "glyphs": glyphs,
        "codes": codes,
        # DefineFont3 usa el EM square multiplicado por 20
        "em_scale": 20480 if tag.tag_type == 75 else 1024,
        "layout": layout,
    }

def font_to_svg(font, columns=8, cell=64):
    """Renderiza todos los glifos de la fuente en una cuadrícula SVG."""
    n = font["num_glyphs"]
    if n == 0:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='32'><text x='4' y='20' fill='#888' font-size='12'>no glyphs</text></svg>"
    rows = (n + columns - 1) // columns
    width = columns * cell
    height = rows * cell
    scale = (cell * 0.62) / font["em_scale"]
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
    ]
    for i, glyph in enumerate(font["glyphs"]):
        col, row = i % columns, i // columns
        # baseline aproximada al 75% de la celda
        dx = col * cell + cell * 0.18
        dy = row * cell + cell * 0.75
        d = glyph_path_d(glyph, scale, dx, dy)
        if d:
            parts.append(f"<path d='{d}' fill='#e2e8f0'/>")
        code = font["codes"][i] if i < len(font["codes"]) else None
        if code:
            label = chr(code) if 32 <= code < 0x10000 else hex(code)
            label = {"<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;"}.get(label, label)
            parts.append(
                f"<text x='{col * cell + 3}' y='{row * cell + 12}' font-size='9' fill='#64748b' font-family='monospace'>{label}</text>"
            )
    parts.append("</svg>")
    return "".join(parts)

def parse_text_tag(tag):
    """Parsea DefineText (11) / DefineText2 (33). Devuelve dict o None."""
    if tag.tag_type not in TEXT_TAGS:
        return None
    with_alpha = tag.tag_type == 33
    data = tag.data
    char_id = int.from_bytes(data[0:2], "little")
    stream = BitStream(data[2:])
    bounds = parse_rect(stream)

    # MATRIX
    if stream.read_bits(1):  # has scale
        n = stream.read_bits(5)
        stream.read_signed_bits(n)
        stream.read_signed_bits(n)
    if stream.read_bits(1):  # has rotate
        n = stream.read_bits(5)
        stream.read_signed_bits(n)
        stream.read_signed_bits(n)
    n = stream.read_bits(5)
    tx = stream.read_signed_bits(n)
    ty = stream.read_signed_bits(n)
    stream.align()

    glyph_bits = stream.read_bits(8)
    advance_bits = stream.read_bits(8)

    records = []
    font_id = None
    color = None
    x_offset = y_offset = 0
    height = 240
    for _ in range(10000):
        flags = stream.read_bits(8)
        if flags == 0:
            break
        has_font = bool(flags & 0x08)
        has_color = bool(flags & 0x04)
        has_y = bool(flags & 0x02)
        has_x = bool(flags & 0x01)
        if has_font:
            font_id = stream.read_u16_le()
        if has_color:
            color = {
                "r": stream.read_bits(8),
                "g": stream.read_bits(8),
                "b": stream.read_bits(8),
                "a": stream.read_bits(8) if with_alpha else 255,
            }
        if has_x:
            x_offset = stream.read_s16_le()
        if has_y:
            y_offset = stream.read_s16_le()
        if has_font:
            height = stream.read_u16_le()
        glyph_count = stream.read_bits(8)
        glyphs = []
        for _ in range(glyph_count):
            idx = stream.read_bits(glyph_bits)
            adv = stream.read_signed_bits(advance_bits)
            glyphs.append({"index": idx, "advance": adv})
        stream.align()
        records.append({
            "font_id": font_id,
            "color": color,
            "x_offset": x_offset,
            "y_offset": y_offset,
            "height": height,
            "glyphs": glyphs,
        })
    return {"char_id": char_id, "bounds": bounds, "translate": (tx, ty), "records": records}

def text_to_svg_fragment(text, fonts):
    """Devuelve (body, bounds) del texto renderizado, para composición."""
    parts = []
    for rec in text["records"]:
        font = fonts.get(rec["font_id"])
        color = rec["color"] or {"r": 0, "g": 0, "b": 0, "a": 255}
        fill = f"rgba({color['r']},{color['g']},{color['b']},{color['a'] / 255.0:.3f})"
        pen_x = rec["x_offset"] / 20.0
        pen_y = rec["y_offset"] / 20.0
        height_px = rec["height"] / 20.0
        if font is None:
            parts.append(
                f"<text x='{pen_x:.2f}' y='{pen_y:.2f}' font-size='{height_px:.2f}' fill='{fill}'>"
                f"[font {rec['font_id']}?]</text>"
            )
            continue
        scale = height_px / font["em_scale"]  # px per EM unit
        for g in rec["glyphs"]:
            if 0 <= g["index"] < len(font["glyphs"]):
                d = glyph_path_d(font["glyphs"][g["index"]], scale, pen_x, pen_y)
                if d:
                    parts.append(f"<path d='{d}' fill='{fill}'/>")
            pen_x += g["advance"] / 20.0
    return "".join(parts), text["bounds"]


def text_to_svg(text, fonts):
    """
    Renderiza un DefineText parseado como SVG.
    `fonts` es {font_id: parse_font(...)} con las fuentes del archivo.
    """
    body, b = text_to_svg_fragment(text, fonts)
    ox, oy = b["xmin"] / 20.0, b["ymin"] / 20.0
    width = max(1, round((b["xmax"] - b["xmin"]) / 20.0))
    height = max(1, round((b["ymax"] - b["ymin"]) / 20.0))
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{ox:.2f} {oy:.2f} {width} {height}' "
        f"width='{width}' height='{height}'>{body}</svg>"
    )

def parse_edit_text(tag):
    """Parsea DefineEditText (37) a un dict de propiedades."""
    if tag.tag_type != 37:
        return None
    data = tag.data
    char_id = int.from_bytes(data[0:2], "little")
    stream = BitStream(data[2:])
    bounds = parse_rect(stream)
    f1 = stream.read_bits(8)
    f2 = stream.read_bits(8)
    has_text = bool(f1 & 0x80)
    word_wrap = bool(f1 & 0x40)
    multiline = bool(f1 & 0x20)
    password = bool(f1 & 0x10)
    read_only = bool(f1 & 0x08)
    has_color = bool(f1 & 0x04)
    has_max_len = bool(f1 & 0x02)
    has_font = bool(f1 & 0x01)
    has_font_class = bool(f2 & 0x80)
    auto_size = bool(f2 & 0x40)
    has_layout = bool(f2 & 0x20)
    no_select = bool(f2 & 0x10)
    border = bool(f2 & 0x08)
    was_static = bool(f2 & 0x04)
    html = bool(f2 & 0x02)
    use_outlines = bool(f2 & 0x01)

    result = {
        "char_id": char_id,
        "bounds": bounds,
        "word_wrap": word_wrap,
        "multiline": multiline,
        "password": password,
        "read_only": read_only,
        "auto_size": auto_size,
        "border": border,
        "html": html,
        "use_outlines": use_outlines,
        "no_select": no_select,
        "was_static": was_static,
    }
    if has_font:
        result["font_id"] = stream.read_u16_le()
    if has_font_class:
        chars = []
        while True:
            c = stream.read_bits(8)
            if c == 0:
                break
            chars.append(c)
        result["font_class"] = bytes(chars).decode("utf-8", errors="replace")
    if has_font:
        result["font_height"] = stream.read_u16_le()
    if has_color:
        result["color"] = {
            "r": stream.read_bits(8), "g": stream.read_bits(8),
            "b": stream.read_bits(8), "a": stream.read_bits(8),
        }
    if has_max_len:
        result["max_length"] = stream.read_u16_le()
    if has_layout:
        result["layout"] = {
            "align": stream.read_bits(8),
            "left_margin": stream.read_u16_le(),
            "right_margin": stream.read_u16_le(),
            "indent": stream.read_u16_le(),
            "leading": stream.read_s16_le(),
        }
    chars = []
    while True:
        try:
            c = stream.read_bits(8)
        except EOFError:
            break
        if c == 0:
            break
        chars.append(c)
    result["variable_name"] = bytes(chars).decode("utf-8", errors="replace")
    if has_text:
        chars = []
        while True:
            try:
                c = stream.read_bits(8)
            except EOFError:
                break
            if c == 0:
                break
            chars.append(c)
        result["initial_text"] = bytes(chars).decode("utf-8", errors="replace")
    return result

def collect_fonts(tags):
    """Devuelve {font_id: parsed_font} para todos los DefineFont2/3 del archivo."""
    fonts = {}
    for tag in tags:
        if tag.tag_type in (48, 75):
            try:
                font = parse_font(tag)
            except Exception:
                font = None
            if font:
                fonts[font["font_id"]] = font
    return fonts


def replace_edit_text_string(tag, new_text: str) -> SWFTag:
    """Reemplaza la cadena inicial de un tag DefineEditText (37) preservando
    estilos, fuente, alineación, márgenes y límites.
    """
    if tag.tag_type != 37 or len(tag.data) < 6:
        raise ValueError("Not a valid DefineEditText tag")

    data = tag.data
    stream = BitStream(data[2:])
    _bounds = parse_rect(stream)
    stream.align()
    f1_offset = 2 + stream.byte_offset

    f1 = data[f1_offset]
    f2 = data[f1_offset + 1]

    has_font = bool(f1 & 0x01)
    has_font_class = bool(f2 & 0x80)
    has_color = bool(f1 & 0x04)
    has_max_len = bool(f1 & 0x02)
    has_layout = bool(f2 & 0x20)

    pos = f1_offset + 2
    if has_font:
        pos += 2
    if has_font_class:
        while pos < len(data) and data[pos] != 0:
            pos += 1
        pos += 1
    if has_font:
        pos += 2
    if has_color:
        pos += 4
    if has_max_len:
        pos += 2
    if has_layout:
        pos += 9

    # Consumir variable_name
    while pos < len(data) and data[pos] != 0:
        pos += 1
    pos = min(pos + 1, len(data))

    var_end_offset = pos

    # Reensamblar con has_text = 1 y el nuevo texto codificado en UTF-8
    new_f1 = f1 | 0x80
    new_data = bytearray(data[:f1_offset])
    new_data.append(new_f1)
    new_data.extend(data[f1_offset + 1 : var_end_offset])
    new_data.extend(new_text.encode("utf-8") + b"\x00")

    return SWFTag(37, bytes(new_data))


