"""
DefineMorphShape (46) / DefineMorphShape2 (84): parseo e interpolación.

Un morph shape define una forma inicial y una final; el reproductor interpola
con un ratio 0..1. Aquí parseamos ambos juegos de edges, los emparejamos
edge a edge (promoviendo rectas a curvas cuando hace falta) e interpolamos
coordenadas, colores y anchos para renderizar el SVG en un ratio dado.

Referencia: SWF File Format Spec v19, cap. "Morph shapes".
"""
from .swf_parser import BitStream
from .shapes import (
    FILL_SOLID, FILL_LINEAR_GRADIENT, FILL_RADIAL_GRADIENT, FILL_FOCAL_RADIAL_GRADIENT,
    _read_matrix, _read_rgba, _read_style_count, _color_to_css, _gradient_def,
)

MORPH_TAG_TYPES = (46, 84)


def _read_morph_gradient(stream):
    spread = stream.read_bits(2)
    interpolation = stream.read_bits(2)
    count = stream.read_bits(4)
    stops = []
    for _ in range(count):
        start_ratio = stream.read_bits(8)
        start_color = _read_rgba(stream)
        end_ratio = stream.read_bits(8)
        end_color = _read_rgba(stream)
        stops.append({
            "start_ratio": start_ratio, "start_color": start_color,
            "end_ratio": end_ratio, "end_color": end_color,
        })
    return {"spread": spread, "interpolation": interpolation, "stops": stops}


def _read_morph_fill_style(stream):
    fill_type = stream.read_bits(8)
    style = {"type": fill_type}
    if fill_type == FILL_SOLID:
        style["start_color"] = _read_rgba(stream)
        style["end_color"] = _read_rgba(stream)
    elif fill_type in (FILL_LINEAR_GRADIENT, FILL_RADIAL_GRADIENT, FILL_FOCAL_RADIAL_GRADIENT):
        style["start_matrix"] = _read_matrix(stream)
        style["end_matrix"] = _read_matrix(stream)
        style["gradient"] = _read_morph_gradient(stream)
        if fill_type == FILL_FOCAL_RADIAL_GRADIENT:
            style["start_focal"] = stream.read_s16_le() / 256.0
            style["end_focal"] = stream.read_s16_le() / 256.0
    else:  # bitmap fills 0x40-0x43
        style["bitmapId"] = stream.read_u16_le()
        style["start_matrix"] = _read_matrix(stream)
        style["end_matrix"] = _read_matrix(stream)
    return style


def _read_morph_line_styles(stream, is_morph2):
    count = _read_style_count(stream)
    styles = []
    for _ in range(count):
        start_width = stream.read_u16_le()
        end_width = stream.read_u16_le()
        if is_morph2:
            stream.read_bits(2)  # start cap
            join_style = stream.read_bits(2)
            has_fill = stream.read_bits(1)
            stream.read_bits(1)  # no hscale
            stream.read_bits(1)  # no vscale
            stream.read_bits(1)  # pixel hinting
            stream.read_bits(5)  # reserved
            stream.read_bits(1)  # no close
            stream.read_bits(2)  # end cap
            if join_style == 2:
                stream.read_u16_le()  # miter limit
            if has_fill:
                fill = _read_morph_fill_style(stream)
                start_color = fill.get("start_color", {"r": 0, "g": 0, "b": 0, "a": 255})
                end_color = fill.get("end_color", start_color)
            else:
                start_color = _read_rgba(stream)
                end_color = _read_rgba(stream)
        else:
            start_color = _read_rgba(stream)
            end_color = _read_rgba(stream)
        styles.append({
            "start_width": start_width, "end_width": end_width,
            "start_color": start_color, "end_color": end_color,
        })
    return styles


def _read_edges(stream):
    """
    Lee shape records devolviendo una lista plana de segmentos en twips:
      ("move", x, y, styles|None) — styles = (fill0, fill1, line) si cambió
      ("line", x0, y0, x1, y1)
      ("curve", x0, y0, cx, cy, x1, y1)
    """
    num_fill_bits = stream.read_bits(4)
    num_line_bits = stream.read_bits(4)
    segments = []
    x = y = 0
    fill0 = fill1 = line = 0
    for _ in range(200000):
        if stream.byte_offset >= len(stream.data):
            break
        edge = stream.read_bits(1)
        if edge == 0:
            flags = stream.read_bits(5)
            if flags == 0:
                break
            styles_changed = False
            if flags & 0x01:  # move
                nbits = stream.read_bits(5)
                x = stream.read_signed_bits(nbits)
                y = stream.read_signed_bits(nbits)
            if flags & 0x02:
                fill0 = stream.read_bits(num_fill_bits)
                styles_changed = True
            if flags & 0x04:
                fill1 = stream.read_bits(num_fill_bits)
                styles_changed = True
            if flags & 0x08:
                line = stream.read_bits(num_line_bits)
                styles_changed = True
            # los morph shapes no usan new-styles (flags & 0x10)
            if flags & 0x01 or styles_changed:
                segments.append(("move", x, y, (fill0, fill1, line)))
        else:
            straight = stream.read_bits(1)
            nbits = stream.read_bits(4) + 2
            x0, y0 = x, y
            if straight:
                if stream.read_bits(1):
                    x += stream.read_signed_bits(nbits)
                    y += stream.read_signed_bits(nbits)
                elif stream.read_bits(1):
                    y += stream.read_signed_bits(nbits)
                else:
                    x += stream.read_signed_bits(nbits)
                segments.append(("line", x0, y0, x, y))
            else:
                cx = x + stream.read_signed_bits(nbits)
                cy = y + stream.read_signed_bits(nbits)
                x = cx + stream.read_signed_bits(nbits)
                y = cy + stream.read_signed_bits(nbits)
                segments.append(("curve", x0, y0, cx, cy, x, y))
    return segments


def parse_morph_tag(tag):
    if tag.tag_type not in MORPH_TAG_TYPES:
        return None
    data = tag.data
    char_id = int.from_bytes(data[0:2], "little")
    stream = BitStream(data[2:])

    def read_rect():
        nbits = stream.read_bits(5)
        r = {
            "xmin": stream.read_signed_bits(nbits),
            "xmax": stream.read_signed_bits(nbits),
            "ymin": stream.read_signed_bits(nbits),
            "ymax": stream.read_signed_bits(nbits),
        }
        stream.align()
        return r

    start_bounds = read_rect()
    end_bounds = read_rect()
    if tag.tag_type == 84:
        read_rect()  # start edge bounds
        read_rect()  # end edge bounds
        stream.read_bits(8)  # reserved + scaling flags

    # offset (u32 LE) al inicio de EndEdges, relativo al byte siguiente
    lo = stream.read_u16_le()
    hi = stream.read_u16_le()
    end_edges_offset = lo | (hi << 16)
    offset_base = stream.byte_offset

    fill_styles = []
    count = _read_style_count(stream)
    for _ in range(count):
        fill_styles.append(_read_morph_fill_style(stream))
    line_styles = _read_morph_line_styles(stream, tag.tag_type == 84)

    start_segments = _read_edges(stream)

    end_stream = BitStream(stream.data[offset_base + end_edges_offset:])
    end_segments = _read_edges(end_stream)

    return {
        "char_id": char_id,
        "start_bounds": start_bounds,
        "end_bounds": end_bounds,
        "fill_styles": fill_styles,
        "line_styles": line_styles,
        "start_segments": start_segments,
        "end_segments": end_segments,
    }


def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp_color(c0, c1, t):
    return {k: int(round(_lerp(c0[k], c1[k], t))) for k in ("r", "g", "b", "a")}


def _lerp_matrix(m0, m1, t):
    return {k: _lerp(m0[k], m1[k], t) for k in m0}


def _pair_segments(start_segments, end_segments, t):
    """Interpola las listas de segmentos. El stream final solo aporta moveTos y
    edges; los cambios de estilo se toman del inicial."""
    out = []
    j = 0
    for seg in start_segments:
        # avanzar el cursor del stream final hasta el próximo segmento comparable
        end_seg = end_segments[j] if j < len(end_segments) else None
        if seg[0] == "move":
            if end_seg and end_seg[0] == "move":
                x = _lerp(seg[1], end_seg[1], t)
                y = _lerp(seg[2], end_seg[2], t)
                j += 1
            else:
                x, y = seg[1], seg[2]
            out.append(("move", x, y, seg[3]))
            continue
        while end_seg and end_seg[0] == "move":
            j += 1
            end_seg = end_segments[j] if j < len(end_segments) else None
        if end_seg is None:
            out.append(seg)
            continue
        # promover recta a curva si una de las dos es curva
        def as_curve(s):
            if s[0] == "curve":
                return s
            _, x0, y0, x1, y1 = s
            return ("curve", x0, y0, (x0 + x1) / 2, (y0 + y1) / 2, x1, y1)

        if seg[0] == "line" and end_seg[0] == "line":
            out.append((
                "line",
                _lerp(seg[1], end_seg[1], t), _lerp(seg[2], end_seg[2], t),
                _lerp(seg[3], end_seg[3], t), _lerp(seg[4], end_seg[4], t),
            ))
        else:
            c0, c1 = as_curve(seg), as_curve(end_seg)
            out.append(tuple(
                ["curve"] + [_lerp(c0[i], c1[i], t) for i in range(1, 7)]
            ))
        j += 1
    return out


def morph_to_svg_fragment(tag, ratio=0.0, id_prefix="m"):
    """Como morph_to_svg pero devuelve (defs, body, bounds) para composición."""
    parsed = parse_morph_tag(tag)
    if parsed is None:
        return "", "", None
    t = max(0.0, min(1.0, ratio))

    b0, b1 = parsed["start_bounds"], parsed["end_bounds"]
    bounds = {k: _lerp(b0[k], b1[k], t) for k in b0}
    width = max(1, round((bounds["xmax"] - bounds["xmin"]) / 20.0))
    height = max(1, round((bounds["ymax"] - bounds["ymin"]) / 20.0))
    ox, oy = bounds["xmin"] / 20.0, bounds["ymin"] / 20.0

    segments = _pair_segments(parsed["start_segments"], parsed["end_segments"], t)

    # agrupar por estilo activo, como en shapes.py
    groups = []
    cur = {"styles": (0, 0, 0), "d": []}
    for seg in segments:
        if seg[0] == "move":
            if seg[3] != cur["styles"] and cur["d"]:
                groups.append(cur)
                cur = {"styles": seg[3], "d": []}
            elif seg[3] != cur["styles"]:
                cur["styles"] = seg[3]
            cur["d"].append(f"M {seg[1] / 20.0:.2f} {seg[2] / 20.0:.2f}")
        elif seg[0] == "line":
            cur["d"].append(f"L {seg[3] / 20.0:.2f} {seg[4] / 20.0:.2f}")
        else:
            cur["d"].append(
                f"Q {seg[3] / 20.0:.2f} {seg[4] / 20.0:.2f} {seg[5] / 20.0:.2f} {seg[6] / 20.0:.2f}"
            )
    if cur["d"]:
        groups.append(cur)

    fill_styles = parsed["fill_styles"]
    line_styles = parsed["line_styles"]
    defs = []
    parts = []
    for gi, group in enumerate(groups):
        fill0, fill1, line = group["styles"]
        fill_idx = fill1 or fill0
        fill_css = "none"
        if fill_idx and 1 <= fill_idx <= len(fill_styles):
            style = fill_styles[fill_idx - 1]
            if style["type"] == FILL_SOLID:
                fill_css = _color_to_css(_lerp_color(style["start_color"], style["end_color"], t))
            elif "gradient" in style:
                interp = {
                    "type": style["type"],
                    "matrix": _lerp_matrix(style["start_matrix"], style["end_matrix"], t),
                    "gradient": {
                        "spread": style["gradient"]["spread"],
                        "focalPoint": 0.0,
                        "stops": [
                            {
                                "ratio": _lerp(s["start_ratio"], s["end_ratio"], t),
                                "color": _lerp_color(s["start_color"], s["end_color"], t),
                            }
                            for s in style["gradient"]["stops"]
                        ],
                    },
                }
                def_id = f"{id_prefix}_g{gi}"
                defs.append(_gradient_def(interp, def_id))
                fill_css = f"url(#{def_id})"
            else:
                fill_css = "#999999"

        stroke_attrs = "stroke='none'"
        if line and 1 <= line <= len(line_styles):
            ls = line_styles[line - 1]
            color = _lerp_color(ls["start_color"], ls["end_color"], t)
            w = max(0.05, _lerp(ls["start_width"], ls["end_width"], t) / 20.0)
            stroke_attrs = f"stroke='{_color_to_css(color)}' stroke-width='{w:.2f}'"

        parts.append(
            f"<path d='{' '.join(group['d'])}' fill='{fill_css}' fill-rule='evenodd' {stroke_attrs}/>"
        )

    return "".join(defs), "".join(parts), bounds


def morph_to_svg(tag, ratio=0.0):
    """Renderiza el morph shape interpolado en `ratio` (0..1) como SVG."""
    defs, body, bounds = morph_to_svg_fragment(tag, ratio)
    if bounds is None:
        return "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    width = max(1, round((bounds["xmax"] - bounds["xmin"]) / 20.0))
    height = max(1, round((bounds["ymax"] - bounds["ymin"]) / 20.0))
    ox, oy = bounds["xmin"] / 20.0, bounds["ymin"] / 20.0
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{ox:.2f} {oy:.2f} {width} {height}' "
        f"width='{width}' height='{height}'>"
    ]
    if defs:
        parts.append(f"<defs>{defs}</defs>")
    parts.append(body)
    parts.append("</svg>")
    return "".join(parts)
