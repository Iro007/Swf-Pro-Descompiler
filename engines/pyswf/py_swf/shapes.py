"""
Parser de shapes vectoriales SWF (DefineShape, DefineShape2/3/4).

Convierte los ShapeRecords (edges) del formato binario SWF en:
  - una lista de "grupos" de estilo (fill0, fill1, line, subpaths)
  - path data en formato SVG, listo para exportar o previsualizar.

Referencia: SWF File Format Spec v19, secciones "Shapes" (cap. 6) y
"Shape Records" (Straight/Curved Edge, Style Change Record).
"""

from .swf_parser import BitStream

SHAPE_TAG_TYPES = (2, 22, 32, 83)  # DefineShape, DefineShape2, DefineShape3, DefineShape4

FILL_SOLID = 0x00
FILL_LINEAR_GRADIENT = 0x10
FILL_RADIAL_GRADIENT = 0x12
FILL_FOCAL_RADIAL_GRADIENT = 0x13
FILL_REPEATING_BITMAP = 0x40
FILL_CLIPPED_BITMAP = 0x41
FILL_NON_SMOOTHED_REPEATING_BITMAP = 0x42
FILL_NON_SMOOTHED_CLIPPED_BITMAP = 0x43

# Tags que usan color RGBA (con alpha) en sus fill/line styles.
# DefineShape (2) y DefineShape2 (22) -> RGB puro (sin alpha)
# DefineShape3 (32) y DefineShape4 (83) -> RGBA
_RGBA_SHAPE_TAGS = (32, 83)
_LINESTYLE2_SHAPE_TAGS = (83,)


def _read_rgb(stream):
    r = stream.read_bits(8)
    g = stream.read_bits(8)
    b = stream.read_bits(8)
    return {"r": r, "g": g, "b": b, "a": 255}


def _read_rgba(stream):
    r = stream.read_bits(8)
    g = stream.read_bits(8)
    b = stream.read_bits(8)
    a = stream.read_bits(8)
    return {"r": r, "g": g, "b": b, "a": a}


def _read_color(stream, with_alpha):
    return _read_rgba(stream) if with_alpha else _read_rgb(stream)


def _read_matrix(stream):
    """MATRIX record (spec 6.2.2): scale/rotate/translate, all optional."""
    m = {"scaleX": 1.0, "scaleY": 1.0, "rotateSkew0": 0.0, "rotateSkew1": 0.0,
         "translateX": 0, "translateY": 0}
    has_scale = stream.read_bits(1)
    if has_scale:
        n = stream.read_bits(5)
        m["scaleX"] = stream.read_signed_bits(n) / 65536.0
        m["scaleY"] = stream.read_signed_bits(n) / 65536.0
    has_rotate = stream.read_bits(1)
    if has_rotate:
        n = stream.read_bits(5)
        m["rotateSkew0"] = stream.read_signed_bits(n) / 65536.0
        m["rotateSkew1"] = stream.read_signed_bits(n) / 65536.0
    n_translate = stream.read_bits(5)
    m["translateX"] = stream.read_signed_bits(n_translate)
    m["translateY"] = stream.read_signed_bits(n_translate)
    stream.align()
    return m


def _read_gradient(stream, with_alpha, focal=False):
    spread = stream.read_bits(2)
    interpolation = stream.read_bits(2)
    num_gradients = stream.read_bits(4)
    stops = []
    for _ in range(num_gradients):
        ratio = stream.read_bits(8)
        color = _read_color(stream, with_alpha)
        stops.append({"ratio": ratio, "color": color})
    result = {"stops": stops, "spread": spread, "interpolation": interpolation}
    if focal:
        result["focalPoint"] = stream.read_s16_le() / 256.0  # FIXED8 little-endian
    return result


def _read_fill_style(stream, with_alpha):
    fill_type = stream.read_bits(8)
    style = {"type": fill_type}
    if fill_type == FILL_SOLID:
        style["color"] = _read_color(stream, with_alpha)
    elif fill_type in (FILL_LINEAR_GRADIENT, FILL_RADIAL_GRADIENT, FILL_FOCAL_RADIAL_GRADIENT):
        style["matrix"] = _read_matrix(stream)
        style["gradient"] = _read_gradient(stream, with_alpha, focal=(fill_type == FILL_FOCAL_RADIAL_GRADIENT))
    elif fill_type in (FILL_REPEATING_BITMAP, FILL_CLIPPED_BITMAP,
                       FILL_NON_SMOOTHED_REPEATING_BITMAP, FILL_NON_SMOOTHED_CLIPPED_BITMAP):
        style["bitmapId"] = stream.read_u16_le()
        style["matrix"] = _read_matrix(stream)
    else:
        raise ValueError(f"Unsupported shape fill style 0x{fill_type:02x}")
    return style


def _read_style_count(stream, *, extended=True):
    """Style arrays use UI8 count, or 0xFF + UI16 for >254 styles (spec 6.3.2)."""
    count = stream.read_bits(8)
    if count == 0xFF and extended:
        count = stream.read_u16_le()
    return count


def _read_fill_style_array(stream, with_alpha, *, extended=True):
    count = _read_style_count(stream,extended=extended)
    return [_read_fill_style(stream, with_alpha) for _ in range(count)]


def _read_line_style_array(stream, with_alpha, use_line_style2):
    count = _read_style_count(stream)
    styles = []
    for _ in range(count):
        if use_line_style2:
            width = stream.read_u16_le()
            start_cap = stream.read_bits(2)
            join_style = stream.read_bits(2)
            has_fill = stream.read_bits(1)
            no_hscale = stream.read_bits(1)
            no_vscale = stream.read_bits(1)
            pixel_hinting = stream.read_bits(1)
            reserved = stream.read_bits(5)
            no_close = stream.read_bits(1)
            end_cap = stream.read_bits(2)
            miter_limit = stream.read_u16_le() if join_style == 2 else None
            entry = {"width": width, "startCap": start_cap, "joinStyle": join_style,
                     "noHScale": bool(no_hscale), "noVScale": bool(no_vscale),
                     "pixelHinting": bool(pixel_hinting), "noClose": bool(no_close),
                     "endCap": end_cap, "reserved": reserved}
            if miter_limit is not None:
                entry["miterLimit"] = miter_limit
            if has_fill:
                entry["fillStyle"] = _read_fill_style(stream, True)
            else:
                entry["color"] = _read_color(stream, True)
            styles.append(entry)
        else:
            width = stream.read_u16_le()
            color = _read_color(stream, with_alpha)
            styles.append({"width": width, "color": color})
    return styles


def parse_shape_tag(tag):
    """
    Parsea un tag DefineShape/2/3/4 completo.
    Devuelve dict: {shape_id, bounds, fill_styles, line_styles, groups}
    donde cada "group" es una corrida de edges entre cambios de estilo:
      {fill_style0, fill_style1, line_style, subpaths}
    y cada subpath es una lista de comandos:
      {"cmd": "move", "x":.., "y":..}
      {"cmd": "line", "x":.., "y":..}
      {"cmd": "curve", "cx":.., "cy":.., "x":.., "y":..}
    Coordenadas ya convertidas de twips a pixels (1 twip = 1/20 px).
    """
    with_alpha = tag.tag_type in _RGBA_SHAPE_TAGS
    use_line_style2 = tag.tag_type in _LINESTYLE2_SHAPE_TAGS
    data = tag.data
    if len(data) < 2:
        return {"shape_id": 0, "bounds": None, "fill_styles": [], "line_styles": [], "groups": []}

    shape_id = int.from_bytes(data[0:2], "little")
    stream = BitStream(data[2:])

    # ShapeBounds RECT
    nbits = stream.read_bits(5)
    xmin = stream.read_signed_bits(nbits)
    xmax = stream.read_signed_bits(nbits)
    ymin = stream.read_signed_bits(nbits)
    ymax = stream.read_signed_bits(nbits)
    stream.align()
    bounds = {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}

    edge_bounds, shape_flags = None, None
    if tag.tag_type == 83:
        # DefineShape4 adds: EdgeBounds RECT, then a flags byte
        # (reserved(5) + UsesFillWindingRule(1) + UsesNonScalingStrokes(1) + UsesScalingStrokes(1))
        eb_nbits = stream.read_bits(5)
        edge_bounds = dict(zip(("xmin","xmax","ymin","ymax"),(stream.read_signed_bits(eb_nbits) for _ in range(4))))
        stream.align()
        shape_flags = stream.read_bits(8)

    fill_styles = _read_fill_style_array(stream, with_alpha,extended=(tag.tag_type!=2))
    line_styles = _read_line_style_array(stream, with_alpha, use_line_style2)

    num_fill_bits = stream.read_bits(4)
    num_line_bits = stream.read_bits(4)

    groups = []
    cur_group = {"fill_style0": 0, "fill_style1": 0, "line_style": 0, "subpaths": []}
    cur_subpath = []
    cur_x, cur_y = 0, 0
    # Tras un new-styles record los índices refieren al array nuevo; acumulamos
    # todos los estilos y desplazamos los índices con estos offsets.
    fill_base = 0
    line_base = 0

    def flush_subpath():
        if cur_subpath:
            cur_group["subpaths"].append(cur_subpath)

    max_records = 200000
    for _ in range(max_records):
        if stream.byte_offset >= len(stream.data):
            break
        edge_flag = stream.read_bits(1)
        if edge_flag == 0:
            state_flags = stream.read_bits(5)
            if state_flags == 0:
                # EndShapeRecord
                break

            state_new_styles = bool(state_flags & 0x10)
            state_line_style = bool(state_flags & 0x08)
            state_fill_style1 = bool(state_flags & 0x04)
            state_fill_style0 = bool(state_flags & 0x02)
            state_move_to = bool(state_flags & 0x01)

            if state_fill_style0 or state_fill_style1 or state_line_style or state_new_styles:
                flush_subpath()
                if cur_group["subpaths"]:
                    groups.append(cur_group)
                cur_subpath = []
                cur_group = {"fill_style0":cur_group["fill_style0"],"fill_style1":cur_group["fill_style1"],"line_style":cur_group["line_style"],"subpaths":[]}
            if state_move_to:
                move_bits = stream.read_bits(5)
                move_x = stream.read_signed_bits(move_bits)
                move_y = stream.read_signed_bits(move_bits)
                flush_subpath()
                cur_subpath = []
                cur_x, cur_y = move_x, move_y
                cur_subpath.append({"cmd": "move", "x": cur_x / 20.0, "y": cur_y / 20.0})

            if state_fill_style0:
                idx = stream.read_bits(num_fill_bits)
                cur_group["fill_style0"] = idx + fill_base if idx else 0
            if state_fill_style1:
                idx = stream.read_bits(num_fill_bits)
                cur_group["fill_style1"] = idx + fill_base if idx else 0
            if state_line_style:
                idx = stream.read_bits(num_line_bits)
                cur_group["line_style"] = idx + line_base if idx else 0

            if state_new_styles:
                flush_subpath()
                if cur_group["subpaths"]:
                    groups.append(cur_group)
                cur_subpath = []
                stream.align()  # los arrays de estilos empiezan alineados a byte
                fill_base = len(fill_styles)
                line_base = len(line_styles)
                fill_styles = fill_styles + _read_fill_style_array(stream, with_alpha,extended=(tag.tag_type!=2))
                line_styles = line_styles + _read_line_style_array(stream, with_alpha, use_line_style2)
                num_fill_bits = stream.read_bits(4)
                num_line_bits = stream.read_bits(4)
                cur_group = {"fill_style0":cur_group["fill_style0"],"fill_style1":cur_group["fill_style1"],"line_style":cur_group["line_style"],"subpaths":[]}
        else:
            if not cur_subpath:
                cur_subpath = [{"cmd":"move","x":cur_x/20.0,"y":cur_y/20.0}]
            straight_flag = stream.read_bits(1)
            num_bits = stream.read_bits(4) + 2
            if straight_flag:
                general_line = stream.read_bits(1)
                dx = dy = 0
                if general_line:
                    dx = stream.read_signed_bits(num_bits)
                    dy = stream.read_signed_bits(num_bits)
                else:
                    vert = stream.read_bits(1)
                    if vert:
                        dy = stream.read_signed_bits(num_bits)
                    else:
                        dx = stream.read_signed_bits(num_bits)
                cur_x += dx
                cur_y += dy
                if not cur_subpath:
                    cur_subpath.append({"cmd": "move", "x": cur_x / 20.0, "y": cur_y / 20.0})
                else:
                    cur_subpath.append({"cmd": "line", "x": cur_x / 20.0, "y": cur_y / 20.0})
            else:
                cx_delta = stream.read_signed_bits(num_bits)
                cy_delta = stream.read_signed_bits(num_bits)
                ax_delta = stream.read_signed_bits(num_bits)
                ay_delta = stream.read_signed_bits(num_bits)
                ctrl_x = cur_x + cx_delta
                ctrl_y = cur_y + cy_delta
                cur_x = ctrl_x + ax_delta
                cur_y = ctrl_y + ay_delta
                if not cur_subpath:
                    cur_subpath.append({"cmd": "move", "x": ctrl_x / 20.0, "y": ctrl_y / 20.0})
                cur_subpath.append({
                    "cmd": "curve",
                    "cx": ctrl_x / 20.0, "cy": ctrl_y / 20.0,
                    "x": cur_x / 20.0, "y": cur_y / 20.0
                })

    flush_subpath()
    if cur_group["subpaths"]:
        groups.append(cur_group)

    result = {
        "shape_id": shape_id,
        "bounds": bounds,
        "fill_styles": fill_styles,
        "line_styles": line_styles,
        "groups": groups,
    }
    if edge_bounds is not None:
        result.update(edge_bounds=edge_bounds,shape_flags=shape_flags)
    return result


def _color_to_css(color, default="#000000"):
    if not color:
        return default
    a = color.get("a", 255) / 255.0
    return f"rgba({color['r']},{color['g']},{color['b']},{a:.3f})"


# El "gradient square" SWF va de -16384 a 16384 twips en espacio de gradiente;
# la MATRIX del estilo lo lleva a twips de la shape. Nuestros paths están en px
# (twips/20), así que componemos scale(1/20) con la matriz.
_GRADIENT_HALF = 16384

_SPREAD_METHODS = {0: "pad", 1: "reflect", 2: "repeat"}


def _matrix_to_svg(m, divisor=20.0):
    return (
        f"matrix({m['scaleX'] / divisor:.6f} {m['rotateSkew0'] / divisor:.6f} "
        f"{m['rotateSkew1'] / divisor:.6f} {m['scaleY'] / divisor:.6f} "
        f"{m['translateX'] / divisor:.3f} {m['translateY'] / divisor:.3f})"
    )


def _gradient_def(style, def_id):
    grad = style["gradient"]
    spread = _SPREAD_METHODS.get(grad.get("spread", 0), "pad")
    transform = _matrix_to_svg(style["matrix"])
    stops = "".join(
        f"<stop offset='{s['ratio'] / 255.0:.4f}' stop-color='rgb({s['color']['r']},{s['color']['g']},{s['color']['b']})' "
        f"stop-opacity='{s['color'].get('a', 255) / 255.0:.3f}'/>"
        for s in grad["stops"]
    )
    common = f"gradientUnits='userSpaceOnUse' spreadMethod='{spread}' gradientTransform='{transform}'"
    if style["type"] == FILL_LINEAR_GRADIENT:
        return (
            f"<linearGradient id='{def_id}' x1='{-_GRADIENT_HALF}' y1='0' "
            f"x2='{_GRADIENT_HALF}' y2='0' {common}>{stops}</linearGradient>"
        )
    fx_attr = ""
    if style["type"] == FILL_FOCAL_RADIAL_GRADIENT:
        fx = grad.get("focalPoint", 0.0) * _GRADIENT_HALF
        fx_attr = f"fx='{fx:.1f}' fy='0' "
    return (
        f"<radialGradient id='{def_id}' cx='0' cy='0' r='{_GRADIENT_HALF}' "
        f"{fx_attr}{common}>{stops}</radialGradient>"
    )


def _bitmap_pattern_def(style, def_id, bitmap_resolver):
    """Bitmap fill como <pattern>. `bitmap_resolver(char_id)` debe devolver
    (png_bytes, width, height) o None. El patrón siempre repite (la variante
    'clipped' se aproxima)."""
    if bitmap_resolver is None:
        return None
    resolved = bitmap_resolver(style.get("bitmapId"))
    if not resolved:
        return None
    png_bytes, width, height = resolved
    import base64

    b64 = base64.b64encode(png_bytes).decode("ascii")
    transform = _matrix_to_svg(style["matrix"])
    return (
        f"<pattern id='{def_id}' patternUnits='userSpaceOnUse' width='{width}' height='{height}' "
        f"patternTransform='{transform}'>"
        f"<image href='data:image/png;base64,{b64}' width='{width}' height='{height}'/></pattern>"
    )


_LINE_CAPS = {0: "round", 1: "butt", 2: "square"}
_LINE_JOINS = {0: "round", 1: "bevel", 2: "miter"}


def _subpath_to_svg_d(subpath):
    parts = []
    for seg in subpath:
        if seg["cmd"] == "move":
            parts.append(f"M {seg['x']:.2f} {seg['y']:.2f}")
        elif seg["cmd"] == "line":
            parts.append(f"L {seg['x']:.2f} {seg['y']:.2f}")
        elif seg["cmd"] == "curve":
            parts.append(f"Q {seg['cx']:.2f} {seg['cy']:.2f} {seg['x']:.2f} {seg['y']:.2f}")
    return " ".join(parts)


def shape_to_svg_fragment(tag, id_prefix="s", bitmap_resolver=None):
    """
    Renderiza un DefineShape/2/3/4 como (defs, body, bounds) para componer en
    un documento SVG mayor. Gradientes y bitmaps se emiten como defs reales.
    """
    parsed = parse_shape_tag(tag)
    bounds = parsed["bounds"]
    if not bounds:
        return "", "", None

    fill_styles = parsed["fill_styles"]
    line_styles = parsed["line_styles"]

    defs = []
    fill_css_cache = {}

    def fill_css_for(fill_idx):
        if not fill_idx or not (1 <= fill_idx <= len(fill_styles)):
            return "none"
        if fill_idx in fill_css_cache:
            return fill_css_cache[fill_idx]
        style = fill_styles[fill_idx - 1]
        css = "#999999"
        if style["type"] == FILL_SOLID:
            css = _color_to_css(style.get("color"))
        elif style["type"] in (FILL_LINEAR_GRADIENT, FILL_RADIAL_GRADIENT, FILL_FOCAL_RADIAL_GRADIENT):
            def_id = f"{id_prefix}_g{fill_idx}_{len(defs)}"
            defs.append(_gradient_def(style, def_id))
            css = f"url(#{def_id})"
        elif style["type"] in (FILL_REPEATING_BITMAP, FILL_CLIPPED_BITMAP,
                               FILL_NON_SMOOTHED_REPEATING_BITMAP, FILL_NON_SMOOTHED_CLIPPED_BITMAP):
            def_id = f"{id_prefix}_b{fill_idx}_{len(defs)}"
            pattern = _bitmap_pattern_def(style, def_id, bitmap_resolver)
            if pattern:
                defs.append(pattern)
                css = f"url(#{def_id})"
        fill_css_cache[fill_idx] = css
        return css

    body = []
    for group in parsed["groups"]:
        d = " ".join(_subpath_to_svg_d(sp) for sp in group["subpaths"] if sp)
        if not d:
            continue

        fill_css = fill_css_for(group["fill_style1"] or group["fill_style0"])

        stroke_attrs = "stroke='none'"
        line_idx = group["line_style"]
        if line_idx and 1 <= line_idx <= len(line_styles):
            lstyle = line_styles[line_idx - 1]
            color = lstyle.get("color")
            if color is None and "fillStyle" in lstyle:
                color = lstyle["fillStyle"].get("color")
            stroke_css = _color_to_css(color)
            stroke_width = max(0.05, lstyle.get("width", 20) / 20.0)
            cap = _LINE_CAPS.get(lstyle.get("startCap", 0), "round")
            join = _LINE_JOINS.get(lstyle.get("joinStyle", 0), "round")
            stroke_attrs = (
                f"stroke='{stroke_css}' stroke-width='{stroke_width:.2f}' "
                f"stroke-linecap='{cap}' stroke-linejoin='{join}'"
            )

        body.append(f"<path d='{d}' fill='{fill_css}' fill-rule='evenodd' {stroke_attrs}/>")

    return "".join(defs), "".join(body), bounds


def shape_to_svg(tag, background=None, bitmap_resolver=None):
    """
    Renderiza un tag DefineShape/2/3/4 como documento SVG (string).
    Cada "group" se dibuja como <path> propio; el relleno usado es fill_style1
    (convención estándar: el lado derecho del edge es el interior de la región),
    con fallback a fill_style0 si fill1 es 0 (sin estilo).
    `bitmap_resolver(char_id) -> (png_bytes, w, h) | None` habilita bitmap fills.
    """
    defs, body, bounds = shape_to_svg_fragment(tag, bitmap_resolver=bitmap_resolver)
    if bounds is None:
        return "<svg xmlns='http://www.w3.org/2000/svg'></svg>"

    width = max(1, round((bounds["xmax"] - bounds["xmin"]) / 20.0))
    height = max(1, round((bounds["ymax"] - bounds["ymin"]) / 20.0))
    ox = bounds["xmin"] / 20.0
    oy = bounds["ymin"] / 20.0

    svg_parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{ox:.2f} {oy:.2f} {width} {height}' "
        f"width='{width}' height='{height}'>"
    ]
    if background:
        svg_parts.append(f"<rect x='{ox:.2f}' y='{oy:.2f}' width='{width}' height='{height}' fill='{background}'/>")
    if defs:
        svg_parts.append(f"<defs>{defs}</defs>")
    svg_parts.append(body)
    svg_parts.append("</svg>")
    return "".join(svg_parts)
