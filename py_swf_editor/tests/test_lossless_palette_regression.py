"""Spec-shaped fixtures: the palette-size byte sits outside the zlib stream."""
import io
import struct
import zlib

import pytest
from PIL import Image

from py_swf.resources import export_image
from py_swf.swf_parser import SWFTag


def palette_tag(tag_type, colors, indices, *, width=3, height=2, count=None):
    count = len(colors) if count is None else count
    header = struct.pack("<HBHHB", 7, 3, width, height, count - 1)
    return SWFTag(tag_type, header + zlib.compress(b"".join(colors) + indices))


@pytest.mark.parametrize("tag_type,colors,expected", [
    (20, [bytes((255, 0, 0)), bytes((0, 255, 0))],
     [(255, 0, 0, 255), (0, 255, 0, 255)]),
    (36, [bytes((128, 0, 0, 128)), bytes((0, 255, 0, 255))],
     [(255, 0, 0, 128), (0, 255, 0, 255)]),
])
def test_palette_rgb_rgba_and_padding(tag_type, colors, expected):
    # Padding bytes are deliberately invalid palette indices; they must be skipped.
    tag = palette_tag(tag_type, colors, bytes((0, 1, 0, 99, 1, 0, 1, 99)))
    output, extension = export_image(tag)
    assert extension == "png"
    image = Image.open(io.BytesIO(output)).convert("RGBA")
    assert image.size == (3, 2)
    pixels = [image.getpixel((x, y)) for y in range(2) for x in range(3)]
    assert pixels == [expected[i] for i in (0, 1, 0, 1, 0, 1)]


@pytest.mark.parametrize("tag_type,color_size", [(20, 3), (36, 4)])
def test_palette_truncation_is_rejected(tag_type, color_size):
    colors = [bytes(color_size), bytes((255,) * color_size)]
    tag = palette_tag(tag_type, colors, bytes((0, 1, 0, 0, 1, 0, 1)))
    assert export_image(tag) == (None, None)


def test_palette_count_larger_than_available_data_is_rejected():
    tag = palette_tag(20, [bytes((255, 0, 0)), bytes((0, 255, 0))],
                      bytes((0, 1, 0, 0, 1, 0, 1, 0)), count=256)
    assert export_image(tag) == (None, None)


def test_palette_out_of_range_pixel_index_is_rejected():
    tag = palette_tag(20, [bytes((255, 0, 0)), bytes((0, 255, 0))],
                      bytes((0, 2, 0, 0, 1, 0, 1, 0)))
    assert export_image(tag) == (None, None)


def test_palette_truncated_compressed_stream_is_rejected():
    tag = palette_tag(20, [bytes((255, 0, 0)), bytes((0, 255, 0))],
                      bytes((0, 1, 0, 0, 1, 0, 1, 0)))
    tag.data = tag.data[:-3]
    assert export_image(tag) == (None, None)
