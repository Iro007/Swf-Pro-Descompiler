import io
import zlib
try:
    from PIL import Image
except ImportError:
    Image = None
from .swf_parser import SWFTag

def _require_pillow():
    if Image is None:
        raise RuntimeError("Pillow is required for image operations. Install it with: pip install Pillow")

def _png_bytes(img):
    _require_pillow()
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

def _strip_jpeg_junk(data):
    """Pre-SWF8 files may prepend an erroneous EOI+SOI (FF D9 FF D8) pair
    before the real JPEG stream; decoders reject it, so strip it."""
    junk = b"\xff\xd9\xff\xd8"
    while data[:4] == junk or data[2:6] == junk:
        if data[:4] == junk:
            data = data[4:]
        else:
            data = data[:2] + data[6:]
    return data

def _unpremultiply(px):
    """In-place un-premultiply of an RGBA bytearray (SWF stores premultiplied)."""
    for i in range(3, len(px), 4):
        a = px[i]
        if 0 < a < 255:
            base = i - 3
            px[base] = min(255, px[base] * 255 // a)
            px[base + 1] = min(255, px[base + 1] * 255 // a)
            px[base + 2] = min(255, px[base + 2] * 255 // a)

def find_jpeg_tables(tags):
    """Returns the shared JPEGTables (tag 8) payload, or None."""
    for tag in tags:
        if tag.tag_type == 8 and tag.data:
            return _strip_jpeg_junk(tag.data)
    return None

def _merge_jpeg_tables(tables, jpeg_data):
    """DefineBits (6) stores scan data whose tables live in the JPEGTables tag:
    both are full SOI..EOI streams; splice tables before the image's SOS data."""
    if not tables or len(tables) <= 4:
        return jpeg_data
    # tables = SOI + table segments + EOI; jpeg_data = SOI + frame/scan + EOI
    return tables[:-2] + jpeg_data[2:]

def _jpeg_with_alpha(jpeg_bytes, alpha_compressed):
    """Decode a JPEG and merge a zlib-compressed 8-bit alpha plane (JPEG3/4)."""
    try:
        alpha_bytes = zlib.decompress(alpha_compressed)
    except Exception:
        return jpeg_bytes, "jpg"
    try:
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    except Exception:
        return None, None
    width, height = img.size
    expected = width * height
    if len(alpha_bytes) < expected:
        alpha_bytes = alpha_bytes + b"\xff" * (expected - len(alpha_bytes))
    img.putalpha(Image.frombytes("L", (width, height), alpha_bytes[:expected]))
    return _png_bytes(img), "png"

def export_image(tag, jpeg_tables=None):
    """
    Exports the image resource in a SWFTag to standard image bytes (PNG or JPG).
    `jpeg_tables`: shared JPEGTables payload, required to decode DefineBits (6).
    Returns (image_bytes, format_ext) or (None, None).
    """
    if tag.tag_type in (20, 36):  # DefineBitsLossless (20), DefineBitsLossless2 (36)
        _require_pillow()
        if len(tag.data) < 8:
            return None, None

        fmt = tag.data[2]
        width = int.from_bytes(tag.data[3:5], "little")
        height = int.from_bytes(tag.data[5:7], "little")
        is_lossless2 = (tag.tag_type == 36)

        # Format 3's ColorTableSize byte precedes the compressed bitmap data.
        colormap_count = tag.data[7] + 1 if fmt == 3 else None
        compressed_data = tag.data[8:] if fmt == 3 else tag.data[7:]
        try:
            raw_data = zlib.decompress(compressed_data)
        except Exception:
            return None, None

        if fmt == 5:  # 32-bit ARGB (0RGB for Lossless 1)
            n = width * height * 4
            if len(raw_data) < n:
                raw_data = raw_data + b"\x00" * (n - len(raw_data))
            out_pixels = bytearray(n)
            # SWF stores [A, R, G, B]; reorder to RGBA
            out_pixels[0::4] = raw_data[1:n:4]
            out_pixels[1::4] = raw_data[2:n:4]
            out_pixels[2::4] = raw_data[3:n:4]
            if is_lossless2:
                out_pixels[3::4] = raw_data[0:n:4]
                _unpremultiply(out_pixels)
            else:
                out_pixels[3::4] = b"\xff" * (width * height)

            img = Image.frombytes("RGBA", (width, height), bytes(out_pixels))
            return _png_bytes(img), "png"

        elif fmt == 3:  # 8-bit colormapped
            # Lossless color table is RGB, Lossless2 is RGBA
            color_size = 4 if is_lossless2 else 3
            table_end = colormap_count * color_size
            row_padded_size = (width + 3) & ~3
            if (width == 0 or height == 0
                    or len(raw_data) < table_end + row_padded_size * height):
                return None, None
            color_table = raw_data[:table_end]
            pixel_indices = raw_data[table_end:]

            out_pixels = bytearray(width * height * 4)
            # Each row of indices is padded to a 4-byte boundary
            for y in range(height):
                for x in range(width):
                    idx_in_data = y * row_padded_size + x
                    if idx_in_data >= len(pixel_indices):
                        break
                    color_idx = pixel_indices[idx_in_data]

                    if color_idx < colormap_count:
                        if is_lossless2:
                            r = color_table[color_idx*4]
                            g = color_table[color_idx*4+1]
                            b = color_table[color_idx*4+2]
                            a = color_table[color_idx*4+3]
                            if 0 < a < 255:
                                r = min(255, r * 255 // a)
                                g = min(255, g * 255 // a)
                                b = min(255, b * 255 // a)
                        else:
                            r = color_table[color_idx*3]
                            g = color_table[color_idx*3+1]
                            b = color_table[color_idx*3+2]
                            a = 255
                    else:
                        return None, None

                    pixel_idx = (y * width + x) * 4
                    out_pixels[pixel_idx] = r
                    out_pixels[pixel_idx+1] = g
                    out_pixels[pixel_idx+2] = b
                    out_pixels[pixel_idx+3] = a

            img = Image.frombytes("RGBA", (width, height), bytes(out_pixels))
            return _png_bytes(img), "png"

    elif tag.tag_type == 6:  # DefineBits (scan data only, tables in JPEGTables)
        if len(tag.data) < 2:
            return None, None
        return _merge_jpeg_tables(jpeg_tables, _strip_jpeg_junk(tag.data[2:])), "jpg"

    elif tag.tag_type == 21:  # DefineBitsJPEG2
        if len(tag.data) < 2:
            return None, None
        return _strip_jpeg_junk(tag.data[2:]), "jpg"

    elif tag.tag_type == 35:  # DefineBitsJPEG3 (JPEG with alpha)
        if len(tag.data) < 6:
            return None, None
        alpha_offset = int.from_bytes(tag.data[2:6], "little")
        jpeg_bytes = _strip_jpeg_junk(tag.data[6 : 6 + alpha_offset])
        if not tag.data[6 + alpha_offset :]:
            return jpeg_bytes, "jpg"
        return _jpeg_with_alpha(jpeg_bytes, tag.data[6 + alpha_offset :])

    elif tag.tag_type == 90:  # DefineBitsJPEG4 (JPEG3 + u16 deblock param)
        if len(tag.data) < 8:
            return None, None
        alpha_offset = int.from_bytes(tag.data[2:6], "little")
        jpeg_bytes = _strip_jpeg_junk(tag.data[8 : 8 + alpha_offset])
        if not tag.data[8 + alpha_offset :]:
            return jpeg_bytes, "jpg"
        return _jpeg_with_alpha(jpeg_bytes, tag.data[8 + alpha_offset :])

    return None, None

def replace_image(tag, new_image_bytes, extension):
    """
    Replaces the image in a tag with a new image.
    Returns a new SWFTag object.
    """
    _require_pillow()
    char_id = int.from_bytes(tag.data[0:2], "little")

    if tag.tag_type in (20, 36):
        is_lossless2 = (tag.tag_type == 36)

        try:
            img = Image.open(io.BytesIO(new_image_bytes)).convert("RGBA")
        except Exception as exc:
            raise ValueError("Failed to load image data") from exc

        width, height = img.size
        rgba = img.tobytes()
        n = width * height * 4
        raw_pixels = bytearray(n)
        # SWF stores [A, R, G, B]
        raw_pixels[1::4] = rgba[0::4]
        raw_pixels[2::4] = rgba[1::4]
        raw_pixels[3::4] = rgba[2::4]
        if is_lossless2:
            raw_pixels[0::4] = rgba[3::4]
            # Premultiply color channels as SWF expects
            for i in range(0, n, 4):
                a = raw_pixels[i]
                if a < 255:
                    raw_pixels[i+1] = raw_pixels[i+1] * a // 255
                    raw_pixels[i+2] = raw_pixels[i+2] * a // 255
                    raw_pixels[i+3] = raw_pixels[i+3] * a // 255
        else:
            raw_pixels[0::4] = b"\xff" * (width * height)

        compressed = zlib.compress(bytes(raw_pixels))

        # character_id (UI16) + format (UI8) + width (UI16) + height (UI16) + pixels
        tag_data = bytearray()
        tag_data.extend(char_id.to_bytes(2, "little"))
        tag_data.append(5)  # format 5 (ARGB)
        tag_data.extend(width.to_bytes(2, "little"))
        tag_data.extend(height.to_bytes(2, "little"))
        tag_data.extend(compressed)

        return SWFTag(tag.tag_type, bytes(tag_data))

    elif tag.tag_type == 21:
        # Replace JPG data directly (re-encode non-JPEG input)
        if extension.lower() not in ("jpg", "jpeg"):
            try:
                img = Image.open(io.BytesIO(new_image_bytes)).convert("RGB")
            except Exception as exc:
                raise ValueError("Failed to load image data") from exc
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            new_image_bytes = buf.getvalue()
        tag_data = bytearray()
        tag_data.extend(char_id.to_bytes(2, "little"))
        tag_data.extend(new_image_bytes)
        return SWFTag(21, bytes(tag_data))

    elif tag.tag_type == 35:
        # DefineBitsJPEG3
        try:
            img = Image.open(io.BytesIO(new_image_bytes)).convert("RGBA")
        except Exception as exc:
            raise ValueError("Failed to load image data") from exc

        width, height = img.size
        alpha_compressed = zlib.compress(img.getchannel("A").tobytes())

        # Composite over white for the opaque JPEG plane
        opaque = Image.new("RGB", (width, height), (255, 255, 255))
        opaque.paste(img, mask=img.getchannel("A"))
        buf = io.BytesIO()
        opaque.save(buf, "JPEG", quality=90)
        jpeg_bytes = buf.getvalue()

        # character_id (UI16) + alpha_data_offset (UI32) + jpeg_data + alpha_data
        tag_data = bytearray()
        tag_data.extend(char_id.to_bytes(2, "little"))
        tag_data.extend(len(jpeg_bytes).to_bytes(4, "little"))
        tag_data.extend(jpeg_bytes)
        tag_data.extend(alpha_compressed)

        return SWFTag(35, bytes(tag_data))

    return tag
