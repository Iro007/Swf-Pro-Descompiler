"""Tests for the native XFL exporter."""
import io
import zipfile
from pathlib import Path

from py_swf.export_xfl import export_xfl
from py_swf.swf_parser import SWFFile, SWFTag


def test_export_xfl_creates_valid_structure(tmp_path):
    movie = SWFFile()
    movie.version, movie.signature = 10, "FWS"
    movie.rect = {"xmin": 0, "xmax": 16000, "ymin": 0, "ymax": 12000}
    movie.frame_rate = 30.0
    movie.frame_count = 5
    # Tag 9: SetBackgroundColor (#336699)
    movie.tags = [
        SWFTag(9, bytes([0x33, 0x66, 0x99])),
        SWFTag(1, b""),  # ShowFrame
        SWFTag(0, b""),  # End
    ]

    out_dir = tmp_path / "xfl_project"
    res_path = export_xfl(movie, out_dir)
    assert Path(res_path).is_dir()
    dom_doc = (out_dir / "DOMDocument.xml").read_text(encoding="utf-8")
    assert '<DOMDocument' in dom_doc
    assert 'width="800"' in dom_doc
    assert 'height="600"' in dom_doc
    assert 'frameRate="30.0"' in dom_doc
    assert 'backgroundColor="#336699"' in dom_doc
    assert 'duration="5"' in dom_doc


def test_export_xfl_as_zip(tmp_path):
    movie = SWFFile()
    movie.version, movie.signature = 10, "FWS"
    movie.rect = {"xmin": 0, "xmax": 11000, "ymin": 0, "ymax": 8000}
    movie.frame_rate = 24.0
    movie.frame_count = 1
    movie.tags = [SWFTag(0, b"")]

    out_zip = tmp_path / "archive.zip"
    res_path = export_xfl(movie, out_zip, as_zip=True)
    assert Path(res_path).is_file()
    with zipfile.ZipFile(res_path, "r") as zf:
        names = zf.namelist()
        assert "DOMDocument.xml" in names
