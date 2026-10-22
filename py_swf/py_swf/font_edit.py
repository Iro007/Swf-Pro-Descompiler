"""
Editor de fuentes SWF: manipulación de glifos (DefineFont2/3).
Permite editar paths de glifos, agregar/eliminar glifos, cambiar códigos.
"""
import io
from .swf_parser import BitStream, SWFTag
from .text_fonts import (
    parse_font, font_to_svg, FONT_TAGS, _read_glyph_records, glyph_path_d,
    parse_text_tag, text_to_svg, collect_fonts,
)
from .shapes import _read_matrix, _read_rgba, _read_style_count, _read_fill_style, _read_line_style_array, _read_fill_style_array


def _write_glyph_records(writer, subpaths):
    """Escribe los shape records de un glifo."""
    writer.write_bits(1, 4)  # num_fill_bits (solo fill style 1)
    writer.write_bits(0, 4)  # num_line_bits
    
    x = y = 0
    for subpath in subpaths:
        if not subpath:
            continue
        # MoveTo
        first = subpath[0]
        if first["cmd"] != "move":
            continue
        move_x = int(round(first["x"]))
        move_y = int(round(first["y"]))
        
        move_bits = max(2, max(
            move_x.bit_length(), move_y.bit_length(),
            abs(move_x).bit_length(), abs(move_y).bit_length()
        ) + 1)
        
        writer.write_bits(0, 1)  # style change
        writer.write_bits(0x03, 5)  # moveTo + fillStyle0
        writer.write_bits(move_bits, 5)
        writer.write_signed_bits(move_x, move_bits)
        writer.write_signed_bits(move_y, move_bits)
        writer.write_bits(1, 1)  # glyph fill style 1
        x, y = move_x, move_y
        
        # Resto de segmentos
        for seg in subpath[1:]:
            if seg["cmd"] == "line":
                x2 = int(round(seg["x"]))
                y2 = int(round(seg["y"]))
                dx = x2 - x
                dy = y2 - y
                nbits = max(2, max(
                    dx.bit_length(), dy.bit_length(),
                    abs(dx).bit_length(), abs(dy).bit_length()
                ) + 1)
                writer.write_bits(1, 1)  # edge
                writer.write_bits(1, 1)  # straight
                if nbits > 17:
                    raise ValueError("Glyph edge exceeds the signed 17-bit coordinate delta")
                writer.write_bits(nbits - 2, 4)
                writer.write_bits(1, 1)  # general line follows NumBits
                writer.write_signed_bits(dx, nbits)
                writer.write_signed_bits(dy, nbits)
                x, y = x2, y2
            elif seg["cmd"] == "curve":
                cx = int(round(seg["cx"]))
                cy = int(round(seg["cy"]))
                x2 = int(round(seg["x"]))
                y2 = int(round(seg["y"]))
                cx_delta = cx - x
                cy_delta = cy - y
                ax_delta = x2 - cx
                ay_delta = y2 - cy
                nbits = max(2, max(
                    cx_delta.bit_length(), cy_delta.bit_length(),
                    ax_delta.bit_length(), ay_delta.bit_length(),
                    abs(cx_delta).bit_length(), abs(cy_delta).bit_length(),
                    abs(ax_delta).bit_length(), abs(ay_delta).bit_length()
                ) + 1)
                writer.write_bits(1, 1)  # edge
                writer.write_bits(0, 1)  # curved
                if nbits > 17:
                    raise ValueError("Glyph curve exceeds the signed 17-bit coordinate delta")
                writer.write_bits(nbits - 2, 4)
                writer.write_signed_bits(cx_delta, nbits)
                writer.write_signed_bits(cy_delta, nbits)
                writer.write_signed_bits(ax_delta, nbits)
                writer.write_signed_bits(ay_delta, nbits)
                x, y = x2, y2
    
    # EndShapeRecord
    writer.write_bits(0, 1)
    writer.write_bits(0, 5)
    writer.align()


class FontWriter:
    """Reconstructor de tags DefineFont2/3."""
    def __init__(self):
        self.data = bytearray()
        self.bit_offset = 0
    
    def write_bits(self, val, nbits):
        val = val & ((1 << nbits) - 1)
        for i in range(nbits):
            bit = (val >> (nbits - 1 - i)) & 1
            byte_idx = self.bit_offset // 8
            if byte_idx >= len(self.data):
                self.data.append(0)
            shift = 7 - (self.bit_offset % 8)
            self.data[byte_idx] |= (bit << shift)
            self.bit_offset += 1
    
    def write_signed_bits(self, val, nbits):
        if val < 0:
            val = (1 << nbits) + val
        self.write_bits(val, nbits)
    
    def align(self):
        if self.bit_offset % 8 != 0:
            self.bit_offset += 8 - (self.bit_offset % 8)
    
    def write_u16_le(self, val):
        self.align()
        self.data.extend(val.to_bytes(2, "little"))
    
    def write_u32_le(self, val):
        self.align()
        self.data.extend(val.to_bytes(4, "little"))
    
    def write_bytes(self, b):
        self.align()
        self.data.extend(b)
    
    def get_bytes(self):
        self.align()
        return bytes(self.data)


def rebuild_font_tag(parsed, tag_type):
    """
    Reconstruye un tag DefineFont2 (48) / DefineFont3 (75) a partir de la estructura parsed.
    """
    writer = FontWriter()
    
    # Font ID
    writer.write_u16_le(parsed["font_id"])
    
    # Flags
    flags = 0
    if parsed.get("has_layout"):
        flags |= 0x80
    if parsed.get("wide_offsets"):
        flags |= 0x08
    if parsed.get("wide_codes"):
        flags |= 0x04
    if parsed.get("italic"):
        flags |= 0x02
    if parsed.get("bold"):
        flags |= 0x01
    writer.write_bits(flags, 8)
    
    # Langcode (reserved)
    writer.write_bits(0, 8)
    
    # Name
    name_bytes = parsed["name"].encode("utf-8")
    writer.write_bits(len(name_bytes), 8)
    writer.write_bytes(name_bytes)
    
    # Num glyphs
    num_glyphs = parsed["num_glyphs"]
    writer.write_u16_le(num_glyphs)
    
    # Offsets table
    wide_offsets = parsed.get("wide_offsets", False)
    off_size = 4 if wide_offsets else 2
    
    # First pass: write glyph data to calculate offsets
    glyph_data_list = []
    for glyph in parsed["glyphs"]:
        gw = FontWriter()
        _write_glyph_records(gw, glyph)
        glyph_data_list.append(gw.get_bytes())
    
    # Calculate offsets
    offsets = [0]
    pos = 0
    for gd in glyph_data_list:
        pos += len(gd)
        offsets.append(pos)
    
    # Add code table offset
    code_table_offset = pos
    offsets.append(code_table_offset)
    
    table_base = 0  # relative to after offsets table
    
    # Write offsets
    for off in offsets:
        if wide_offsets:
            writer.write_u32_le(off)
        else:
            writer.write_u16_le(off)
    
    # Write glyph data
    for gd in glyph_data_list:
        writer.write_bytes(gd)
    
    # Write code table
    wide_codes = parsed.get("wide_codes", False) or tag_type == 75
    code_size = 2 if wide_codes else 1
    for code in parsed["codes"]:
        if wide_codes:
            writer.write_u16_le(code)
        else:
            writer.write_bits(code, 8)
    writer.align()
    
    # Layout (if present)
    if parsed.get("has_layout") and parsed.get("layout"):
        layout = parsed["layout"]
        writer.write_u16_le(layout.get("ascent", 0))
        writer.write_u16_le(layout.get("descent", 0))
        writer.write_u16_le(layout.get("leading", 0) & 0xFFFF)
    
    return SWFTag(tag_type, writer.get_bytes())


def update_glyph_path(parsed, glyph_idx, new_subpaths):
    """Actualiza los paths de un glifo."""
    if 0 <= glyph_idx < len(parsed["glyphs"]):
        parsed["glyphs"][glyph_idx] = new_subpaths
    return parsed


def add_glyph(parsed, subpaths, code):
    """Agrega un nuevo glifo."""
    parsed["glyphs"].append(subpaths)
    parsed["codes"].append(code)
    parsed["num_glyphs"] = len(parsed["glyphs"])
    return parsed


def delete_glyph(parsed, glyph_idx):
    """Elimina un glifo."""
    if 0 <= glyph_idx < len(parsed["glyphs"]):
        parsed["glyphs"].pop(glyph_idx)
        parsed["codes"].pop(glyph_idx)
        parsed["num_glyphs"] = len(parsed["glyphs"])
    return parsed


def update_glyph_code(parsed, glyph_idx, new_code):
    """Actualiza el código de carácter de un glifo."""
    if 0 <= glyph_idx < len(parsed["codes"]):
        parsed["codes"][glyph_idx] = new_code
    return parsed


def update_font_properties(parsed, name=None, italic=None, bold=None):
    """Actualiza propiedades de la fuente."""
    if name is not None:
        parsed["name"] = name
    if italic is not None:
        parsed["italic"] = italic
    if bold is not None:
        parsed["bold"] = bold
    return parsed
