"""Static previews of single characters for the display panel (no ActionScript is executed)."""
from __future__ import annotations

import base64
import io

from . import morph, resources, shapes, text_fonts
from .fla_export import make_bitmap_resolver
from .timeline import render_sprite_frame

MAX_PREVIEW_BYTES = 8 * 1024**2
IMAGE_TAGS = (6, 20, 21, 35, 36, 90)
MAX_FONT_GLYPHS = 512


def _svg_result(kind, svg, **extra):
    if len(svg.encode("utf-8")) > MAX_PREVIEW_BYTES:
        raise ValueError("La vista previa supera el límite de 8 MiB")
    return {"kind": kind, "mime": "image/svg+xml", "encoding": "text", "data": svg, **extra}


def preview_tag(movie, tag, *, frame=0, ratio=0):
    """Return {kind, mime, encoding, data, ...} for an image, shape, morph, sprite, text or font tag."""
    code = tag.tag_type
    if code in IMAGE_TAGS:
        data, extension = resources.export_image(tag, jpeg_tables=resources.find_jpeg_tables(movie.tags))
        if data is None:
            raise ValueError("No se pudo decodificar la imagen de este tag")
        if len(data) > MAX_PREVIEW_BYTES:
            raise ValueError("La imagen supera el límite de 8 MiB para la vista previa")
        result = {"kind": "image", "mime": "image/png" if extension == "png" else "image/jpeg", "encoding": "base64",
                  "data": base64.b64encode(data).decode("ascii")}
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                result.update(width=image.width, height=image.height)
        except Exception:  # The bytes are still displayable without dimensions.
            pass
        return result
    if code in shapes.SHAPE_TAG_TYPES:
        return _svg_result("shape", shapes.shape_to_svg(tag, bitmap_resolver=make_bitmap_resolver(movie.tags)))
    if code in morph.MORPH_TAG_TYPES:
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 65535:
            raise ValueError("Razón de morph inválida")
        return _svg_result("morph", morph.morph_to_svg(tag, ratio=ratio / 65535.0), ratio=ratio)
    if code == 39:
        if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0:
            raise ValueError("Cuadro inválido")
        svg, count = render_sprite_frame(movie, tag, frame, make_bitmap_resolver(movie.tags))
        if svg is None:
            raise ValueError("El sprite no tiene cuadros que mostrar")
        return _svg_result("sprite", svg, frame=min(frame, count - 1), frame_count=count)
    if code in text_fonts.TEXT_TAGS:
        parsed = text_fonts.parse_text_tag(tag)
        if not parsed:
            raise ValueError("Texto no interpretable")
        return _svg_result("text", text_fonts.text_to_svg(parsed, text_fonts.collect_fonts(movie.tags)))
    if code in (48, 75):
        font = text_fonts.parse_font(tag)
        if not font:
            raise ValueError("Fuente no interpretable")
        font = {**font, "glyphs": font["glyphs"][:MAX_FONT_GLYPHS], "codes": font["codes"][:MAX_FONT_GLYPHS],
                "num_glyphs": min(font["num_glyphs"], MAX_FONT_GLYPHS)}
        svg = text_fonts.font_to_svg(font)
        # The sheet draws light glyphs; give it a dark ground so it reads on any viewer background.
        svg = svg.replace(">", "><rect width='100%' height='100%' fill='#1b2430'/>", 1)
        return _svg_result("font", svg, glyphs=font["num_glyphs"])
    raise ValueError("Este tag no tiene vista previa estática")
