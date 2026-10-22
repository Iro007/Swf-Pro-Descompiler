"""
Modelo de display list / timeline SWF y render de frames a SVG.

Procesa PlaceObject (4), PlaceObject2 (26), PlaceObject3 (70), RemoveObject
(5/28) y ShowFrame (1) para la timeline principal y para cada DefineSprite
(39), y compone el frame N transformando los characters (shapes, morphs,
textos, sprites anidados) con sus MATRIX, CXFORM, clip depth, blend mode,
filtros y visible flag.

Referencia: SWF File Format Spec v19, cap. "Display List" y "Color Transform".
"""
from .swf_parser import BitStream, SWFTag
from . import shapes, morph, text_fonts

PLACE_TAGS = (4, 26, 70)
REMOVE_TAGS = (5, 28)
SHOW_FRAME = 1

RENDERABLE_TAGS = shapes.SHAPE_TAG_TYPES + morph.MORPH_TAG_TYPES + text_fonts.TEXT_TAGS[:2] + (39,)


def _read_cxform(stream, with_alpha):
    """Lee CXFORM/CXFORMWITHALPHA y devuelve dict con multiplicadores y aditivos."""
    has_add = stream.read_bits(1)
    has_mult = stream.read_bits(1)
    nbits = stream.read_bits(4)
    channels = 4 if with_alpha else 3
    
    mult = [1.0] * channels
    add = [0] * channels
    
    if has_mult:
        for i in range(channels):
            mult[i] = stream.read_signed_bits(nbits) / 256.0
    if has_add:
        for i in range(channels):
            add[i] = stream.read_signed_bits(nbits)
    stream.align()
    
    return {"has_mult": has_mult, "has_add": has_add, "mult": mult, "add": add}


def _read_cxform_with_alpha(stream):
    return _read_cxform(stream, with_alpha=True)


def _read_cxform_no_alpha(stream):
    return _read_cxform(stream, with_alpha=False)


def _read_matrix_dict(stream):
    from .shapes import _read_matrix
    return _read_matrix(stream)


IDENTITY = {"scaleX": 1.0, "scaleY": 1.0, "rotateSkew0": 0.0, "rotateSkew1": 0.0,
            "translateX": 0, "translateY": 0}

DEFAULT_CXFORM = {"has_mult": False, "has_add": False, 
                  "mult": [1.0, 1.0, 1.0, 1.0], "add": [0, 0, 0, 0]}


BLEND_MODES = {
    0: "normal", 1: "layer", 2: "multiply", 3: "screen", 4: "lighten",
    5: "darken", 6: "difference", 7: "add", 8: "subtract", 9: "invert",
    10: "alpha", 11: "erase", 12: "overlay", 13: "hardlight",
}

BLEND_MODE_SVG = {
    "normal": "normal", "multiply": "multiply", "screen": "screen",
    "lighten": "lighten", "darken": "darken", "difference": "difference",
    "add": "plus-lighter", "subtract": "plus-darker", "invert": "difference",
    "alpha": "normal", "erase": "destination-out", "overlay": "overlay",
    "hardlight": "hard-light",
}


FILTER_TYPES = {
    0: "drop_shadow", 1: "blur", 2: "glow", 3: "bevel",
    4: "gradient_glow", 5: "convolution", 6: "color_matrix",
    7: "displacement_map",
}


def _read_u8(stream):
    return stream.read_bits(8)


def _skip_bytes(stream, count):
    for _ in range(max(0, count)):
        stream.read_bits(8)


def _read_filter_list(stream):
    """Read SWF FilterList and consume every filter payload.

    The previous implementation skipped only the filter id, which shifted the
    stream and made the following BlendMode/ClipDepth fields unreliable. The
    renderer still treats filters as an approximation, but parsing is now
    aligned and the filter metadata is available to callers.
    """
    filters = []
    try:
        count = _read_u8(stream)
        for _ in range(count):
            filter_id = _read_u8(stream)
            item = {"type": FILTER_TYPES.get(filter_id, f"unknown_{filter_id}")}
            if filter_id in (0, 2):  # DropShadowFilter / GlowFilter
                item["color"] = [_read_u8(stream) for _ in range(4)]
                _skip_bytes(stream, 4 + 4 + 4 + 4)  # blurX, blurY, angle, distance
                _skip_bytes(stream, 2 + 1)  # strength + flags
            elif filter_id == 1:  # BlurFilter
                _skip_bytes(stream, 4 + 4 + 1)
            elif filter_id == 3:  # BevelFilter
                item["shadow_color"] = [_read_u8(stream) for _ in range(4)]
                item["highlight_color"] = [_read_u8(stream) for _ in range(4)]
                _skip_bytes(stream, 4 + 4 + 4 + 4 + 2 + 1)
            elif filter_id in (4, 7):  # GradientGlow / GradientBevel
                num_colors = _read_u8(stream)
                item["num_colors"] = num_colors
                _skip_bytes(stream, num_colors * 4 + num_colors)
                _skip_bytes(stream, 4 + 4 + 4 + 4 + 2 + 1)
            elif filter_id == 5:  # ConvolutionFilter
                matrix_x = _read_u8(stream)
                matrix_y = _read_u8(stream)
                item["matrix_x"] = matrix_x
                item["matrix_y"] = matrix_y
                _skip_bytes(stream, matrix_x * matrix_y * 4 + 4 + 4 + 4 + 1)
            elif filter_id == 6:  # ColorMatrixFilter: 20 FLOATs
                _skip_bytes(stream, 20 * 4)
            else:
                item["opaque"] = True
                item["warning"] = "unknown filter payload"
            filters.append(item)
    except (EOFError, IndexError):
        pass
    return filters


def parse_place_tag(tag):
    """Devuelve dict con depth, char_id, matrix, ratio, move, cxform, clip_depth, name, blend_mode, filters, visible, cache_as_bitmap, class_name."""
    data = tag.data
    if tag.tag_type == 4:
        if len(data) < 4:
            return None
        char_id = int.from_bytes(data[0:2], "little")
        depth = int.from_bytes(data[2:4], "little")
        stream = BitStream(data[4:])
        matrix = _read_matrix_dict(stream)
        return {"depth": depth, "char_id": char_id, "matrix": matrix, "ratio": 0, "move": False,
                "cxform": DEFAULT_CXFORM, "clip_depth": None, "name": None,
                "blend_mode": "normal", "filters": [], "visible": True,
                "cache_as_bitmap": False, "class_name": None}

    if tag.tag_type in (26, 70):
        stream = BitStream(data)
        flags = stream.read_bits(8)
        has_clip_actions = bool(flags & 0x80)
        has_clip_depth = bool(flags & 0x40)
        has_name = bool(flags & 0x20)
        has_ratio = bool(flags & 0x10)
        has_cxform = bool(flags & 0x08)
        has_matrix = bool(flags & 0x04)
        has_char = bool(flags & 0x02)
        move = bool(flags & 0x01)

        has_class_name = has_image = has_filters = has_blend = False
        has_opaque_bg = has_visible = has_cache_as_bitmap = False
        
        if tag.tag_type == 70:
            flags2 = stream.read_bits(8)
            has_image = bool(flags2 & 0x10)
            has_class_name = bool(flags2 & 0x08) or (has_image and has_char)
            has_filters = bool(flags2 & 0x01)
            has_blend = bool(flags2 & 0x02)
            has_cache_as_bitmap = bool(flags2 & 0x04)

        depth = stream.read_u16_le()
        result = {"depth": depth, "move": move}

        if has_class_name:
            chars = []
            while True:
                c = stream.read_bits(8)
                if c == 0:
                    break
                chars.append(c)
            result["class_name"] = bytes(chars).decode("utf-8", errors="replace")

        if has_char:
            result["char_id"] = stream.read_u16_le()
        
        if has_matrix:
            result["matrix"] = _read_matrix_dict(stream)
        else:
            result["matrix"] = IDENTITY

        if has_cxform:
            result["cxform"] = _read_cxform_with_alpha(stream)
        else:
            result["cxform"] = DEFAULT_CXFORM

        if has_ratio:
            result["ratio"] = stream.read_u16_le()
        else:
            result["ratio"] = 0

        if has_name:
            chars = []
            while True:
                c = stream.read_bits(8)
                if c == 0:
                    break
                chars.append(c)
            result["name"] = bytes(chars).decode("utf-8", errors="replace")
        else:
            result["name"] = None

        if has_clip_depth:
            result["clip_depth"] = stream.read_u16_le()
        else:
            result["clip_depth"] = None

        if has_blend:
            blend_val = stream.read_bits(8)
            result["blend_mode"] = BLEND_MODES.get(blend_val, "normal")
        else:
            result["blend_mode"] = "normal"

        if has_filters:
            result["filters"] = _read_filter_list(stream)
        else:
            result["filters"] = []

        if has_visible:
            result["visible"] = bool(stream.read_bits(1))
        else:
            result["visible"] = True

        if has_cache_as_bitmap:
            result["cache_as_bitmap"] = bool(stream.read_bits(1))
        else:
            result["cache_as_bitmap"] = False

        if has_opaque_bg:
            # opaque background color (RGBA)
            stream.read_bits(32)
        
        return result
    return None


def _cxform_to_svg_filter(cxform, prefix="cx"):
    """Convierte CXFORM a SVG filter (feColorMatrix)."""
    if not cxform.get("has_mult") and not cxform.get("has_add"):
        return "", ""
    
    mult = cxform["mult"]
    add = cxform["add"]
    
    # SVG feColorMatrix: type="matrix" values="r0 r1 r2 r3 r4  g0 g1 g2 g3 g4  b0 b1 b2 b3 b4  a0 a1 a2 a3 a4"
    # SWF: output = input * mult + add
    # Normalize add to 0-1 range for SVG (add is in 0-255)
    add_norm = [a / 255.0 for a in add]
    
    matrix = [
        mult[0], 0, 0, 0, add_norm[0],
        0, mult[1], 0, 0, add_norm[1],
        0, 0, mult[2], 0, add_norm[2],
        0, 0, 0, mult[3], add_norm[3],
    ]
    
    filter_id = f"{prefix}_colormatrix"
    filter_def = (
        f"<filter id='{filter_id}' x='0' y='0' width='100%' height='100%'>"
        f"<feColorMatrix type='matrix' values='{' '.join(f'{v:.6f}' for v in matrix)}'/>"
        f"</filter>"
    )
    return filter_id, filter_def


def _blend_mode_to_svg(blend_mode):
    return BLEND_MODE_SVG.get(blend_mode, "normal")


def _filters_to_svg(filters, filter_id):
    """Approximate SWF blur/glow filters with a portable SVG filter."""
    primitives = []
    for item in filters or []:
        kind = item.get("type", "")
        if kind in {"blur", "glow", "drop_shadow", "bevel", "gradient_glow", "gradient_bevel"}:
            primitives.append("<feGaussianBlur stdDeviation='2'/>")
    if not primitives:
        return ""
    return f"<filter id='{filter_id}' x='-25%' y='-25%' width='150%' height='150%'>{''.join(primitives)}</filter>"


def _place_transform(m):
    """MATRIX de colocación → transform SVG (translate en twips → px)."""
    return (
        f"matrix({m['scaleX']:.6f} {m['rotateSkew0']:.6f} {m['rotateSkew1']:.6f} "
        f"{m['scaleY']:.6f} {m['translateX'] / 20.0:.3f} {m['translateY'] / 20.0:.3f})"
    )


class FrameRenderer:
    """Renderiza frames componiendo characters por id sobre la display list."""

    def __init__(self, tags, bitmap_resolver=None):
        self.tags = tags
        self.bitmap_resolver = bitmap_resolver
        self.by_id = {t.char_id: t for t in tags if t.char_id is not None}
        self.fonts = text_fonts.collect_fonts(tags)
        self._defs = []
        self._uid = 0
        self._clip_counter = 0

    def _next_prefix(self):
        self._uid += 1
        return f"c{self._uid}"

    def _next_clip_id(self):
        self._clip_counter += 1
        return f"clip_{self._clip_counter}"

    def render_character(self, char_id, ratio=0, depth_budget=8, clip_depth=None):
        tag = self.by_id.get(char_id)
        if tag is None or depth_budget <= 0:
            return ""
        
        # Verificar si este character está enmascarado por clip_depth
        if clip_depth is not None:
            # El clip_depth define hasta qué depth se aplica la máscara
            # En la práctica, el clip se maneja en el display list padre
            pass

        if tag.tag_type in shapes.SHAPE_TAG_TYPES:
            defs, body, bounds = shapes.shape_to_svg_fragment(
                tag, id_prefix=self._next_prefix(), bitmap_resolver=self.bitmap_resolver
            )
            self._defs.append(defs)
            return body
        if tag.tag_type in morph.MORPH_TAG_TYPES:
            defs, body, _ = morph.morph_to_svg_fragment(
                tag, ratio / 65535.0, id_prefix=self._next_prefix()
            )
            self._defs.append(defs)
            return body
        if tag.tag_type in text_fonts.TEXT_TAGS:
            text = text_fonts.parse_text_tag(tag)
            if text:
                body, _ = text_fonts.text_to_svg_fragment(text, self.fonts)
                return body
            return ""
        if tag.tag_type == 39:
            parsed = parse_sprite(tag)
            if not parsed:
                return ""
            _, _, inner = parsed
            frames = build_timeline(inner)
            if not frames:
                return ""
            return self.render_display(frames[0], depth_budget - 1, clip_depth)
        return ""

    def render_display(self, display, depth_budget=8, parent_clip_depth=None):
        parts = []
        current_clip_depth = parent_clip_depth
        clip_stack = []  # Stack de (clip_depth, clip_id)
        
        for depth in sorted(display):
            entry = display[depth]
            
            # Manejar clip depth
            if entry.get("clip_depth") is not None:
                # Este character define una máscara para depths >= clip_depth
                clip_id = self._next_clip_id()
                clip_stack.append((entry["clip_depth"], clip_id))
                current_clip_depth = entry["clip_depth"]
                # Renderizar el character que actúa como máscara
                mask_body = self.render_character(
                    entry["char_id"], entry.get("ratio", 0), depth_budget, current_clip_depth
                )
                if mask_body:
                    parts.append(f"<clipPath id='{clip_id}'>{mask_body}</clipPath>")
                # El character que define el clip no se renderiza normalmente (solo como máscara)
                continue
            
            # Verificar si estamos dentro de un clip
            active_clip = None
            for clip_d, clip_id in reversed(clip_stack):
                if depth >= clip_d:
                    active_clip = clip_id
                    break
            
            # Pop clips que ya no aplican
            while clip_stack and depth < clip_stack[-1][0]:
                clip_stack.pop()
            
            if not entry.get("visible", True):
                continue
            
            char_id = entry["char_id"]
            if char_id is None:
                continue
            
            body = self.render_character(char_id, entry.get("ratio", 0), depth_budget)
            if not body:
                continue
            
            # Construir atributos del grupo
            attrs = []
            
            # Transform
            transform = _place_transform(entry["matrix"])
            attrs.append(f"transform='{transform}'")
            
            # Color transform
            cxform = entry.get("cxform", DEFAULT_CXFORM)
            if cxform.get("has_mult") or cxform.get("has_add"):
                filter_id, filter_def = _cxform_to_svg_filter(cxform, f"cx_{char_id}")
                if filter_def:
                    self._defs.append(filter_def)
                    attrs.append(f"filter='url(#{filter_id})'")
            
            # Blend mode
            blend = entry.get("blend_mode", "normal")
            if blend != "normal":
                svg_blend = _blend_mode_to_svg(blend)
                attrs.append(f"mix-blend-mode='{svg_blend}'")

            if entry.get("filters"):
                filter_id = f"filter_{self._next_prefix()}"
                filter_def = _filters_to_svg(entry["filters"], filter_id)
                if filter_def:
                    self._defs.append(filter_def)
                    attrs.append(f"filter='url(#{filter_id})'")
            
            # Clip path
            if active_clip:
                attrs.append(f"clip-path='url(#{active_clip})'")
            
            # Opacity/visibility (handled by visible flag)
            
            attr_str = " ".join(attrs)
            parts.append(f"<g {attr_str}>{body}</g>")
        
        return "".join(parts)

    def render_frame_svg(self, display, stage_rect, background=None):
        self._defs = []
        self._clip_counter = 0
        body = self.render_display(display)
        width = max(1, round((stage_rect["xmax"] - stage_rect["xmin"]) / 20.0))
        height = max(1, round((stage_rect["ymax"] - stage_rect["ymin"]) / 20.0))
        ox, oy = stage_rect["xmin"] / 20.0, stage_rect["ymin"] / 20.0
        parts = [
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{ox:.2f} {oy:.2f} {width} {height}' "
            f"width='{width}' height='{height}'>"
        ]
        if background:
            parts.append(
                f"<rect x='{ox:.2f}' y='{oy:.2f}' width='{width}' height='{height}' fill='{background}'/>"
            )
        defs = "".join(d for d in self._defs if d)
        if defs:
            parts.append(f"<defs>{defs}</defs>")
        parts.append(body)
        parts.append("</svg>")
        return "".join(parts)


def get_background_color(tags):
    for tag in tags:
        if tag.tag_type == 9 and len(tag.data) >= 3:
            r, g, b = tag.data[0], tag.data[1], tag.data[2]
            return f"rgb({r},{g},{b})"
    return None


def render_frame(swf, frame_index, bitmap_resolver=None):
    """Renderiza el frame N (0-based) de la timeline principal como SVG."""
    frames = build_timeline(swf.tags)
    if not frames:
        return None, 0
    frame_index = max(0, min(frame_index, len(frames) - 1))
    renderer = FrameRenderer(swf.tags, bitmap_resolver=bitmap_resolver)
    svg = renderer.render_frame_svg(
        frames[frame_index], swf.rect, background=get_background_color(swf.tags)
    )
    return svg, len(frames)


def render_sprite_frame(swf, sprite_tag, frame_index, bitmap_resolver=None):
    """Renderiza el frame N de un DefineSprite como SVG (bounds = stage)."""
    parsed = parse_sprite(sprite_tag)
    if not parsed:
        return None, 0
    _, _, inner = parsed
    frames = build_timeline(inner)
    if not frames:
        return None, 0
    frame_index = max(0, min(frame_index, len(frames) - 1))
    renderer = FrameRenderer(swf.tags, bitmap_resolver=bitmap_resolver)
    svg = renderer.render_frame_svg(frames[frame_index], swf.rect)
    return svg, len(frames)


def build_timeline(tags):
    """
    Recorre los tags acumulando el estado de la display list por frame.
    Devuelve lista de frames; cada frame es dict {depth: entry_dict}.
    """
    frames = []
    display = {}
    for tag in tags:
        if tag.tag_type in PLACE_TAGS:
            place = parse_place_tag(tag)
            if not place:
                continue
            depth = place["depth"]
            prev = display.get(depth, {})
            entry = {
                "char_id": place.get("char_id", prev.get("char_id")),
                "matrix": place.get("matrix", prev.get("matrix", IDENTITY)),
                "ratio": place.get("ratio", prev.get("ratio", 0)),
                "cxform": place.get("cxform", prev.get("cxform", DEFAULT_CXFORM)),
                "clip_depth": place.get("clip_depth", prev.get("clip_depth")),
                "name": place.get("name", prev.get("name")),
                "blend_mode": place.get("blend_mode", prev.get("blend_mode", "normal")),
                "filters": place.get("filters", prev.get("filters", [])),
                "visible": place.get("visible", prev.get("visible", True)),
                "cache_as_bitmap": place.get("cache_as_bitmap", prev.get("cache_as_bitmap", False)),
                "class_name": place.get("class_name", prev.get("class_name")),
            }
            if entry["char_id"] is not None:
                display[depth] = entry
        elif tag.tag_type in REMOVE_TAGS:
            data = tag.data
            off = 2 if tag.tag_type == 5 else 0
            if len(data) >= off + 2:
                depth = int.from_bytes(data[off : off + 2], "little")
                display.pop(depth, None)
        elif tag.tag_type == SHOW_FRAME:
            frames.append({d: dict(e) for d, e in display.items()})
    if not frames and display:
        frames.append({d: dict(e) for d, e in display.items()})
    return frames


def parse_sprite(tag):
    """DefineSprite (39): devuelve (char_id, frame_count, tags_anidados)."""
    if tag.tag_type != 39 or len(tag.data) < 4:
        return None
    char_id = int.from_bytes(tag.data[0:2], "little")
    frame_count = int.from_bytes(tag.data[2:4], "little")
    inner = []
    data = tag.data
    offset = 4
    while offset + 2 <= len(data):
        header = int.from_bytes(data[offset : offset + 2], "little")
        tag_type = header >> 6
        tag_len = header & 0x3F
        offset += 2
        if tag_len == 0x3F:
            if offset + 4 > len(data):
                break
            tag_len = int.from_bytes(data[offset : offset + 4], "little")
            offset += 4
        inner.append(SWFTag(tag_type, data[offset : offset + tag_len]))
        offset += tag_len
        if tag_type == 0:
            break
    return char_id, frame_count, inner
