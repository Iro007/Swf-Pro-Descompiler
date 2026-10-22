"""Headless (Pillow) image export/replace tests."""
import io

from PIL import Image

from py_swf.resources import export_image, replace_image
from py_swf.swf_parser import SWFTag

def _sample_png():
    img = Image.new("RGBA", (4, 4))
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.putpixel((1, 0), (0, 255, 0, 128))
    img.putpixel((2, 0), (0, 0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

def test_lossless2_roundtrip():
    seed = SWFTag(36, (7).to_bytes(2, "little") + b"\x05" + b"\x00" * 5)
    tag = replace_image(seed, _sample_png(), "png")
    png, ext = export_image(tag)
    assert ext == "png"
    out = Image.open(io.BytesIO(png))
    assert out.size == (4, 4)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)
    assert out.getpixel((1, 0))[3] == 128

def test_lossless1_is_opaque():
    seed = SWFTag(20, (7).to_bytes(2, "little") + b"\x05" + b"\x00" * 5)
    tag = replace_image(seed, _sample_png(), "png")
    png, _ = export_image(tag)
    out = Image.open(io.BytesIO(png))
    assert all(out.getpixel((x, y))[3] == 255 for x in range(4) for y in range(4))

def test_jpeg3_preserves_alpha():
    seed = SWFTag(35, (9).to_bytes(2, "little") + b"\x00" * 4)
    tag = replace_image(seed, _sample_png(), "png")
    png, ext = export_image(tag)
    assert ext == "png"
    out = Image.open(io.BytesIO(png))
    assert out.getpixel((2, 0))[3] == 0
    assert out.getpixel((0, 0))[3] == 255

def test_jpeg2_reencodes_png_input():
    seed = SWFTag(21, (5).to_bytes(2, "little"))
    tag = replace_image(seed, _sample_png(), "png")
    data, ext = export_image(tag)
    assert ext == "jpg"
    assert Image.open(io.BytesIO(data)).format == "JPEG"
