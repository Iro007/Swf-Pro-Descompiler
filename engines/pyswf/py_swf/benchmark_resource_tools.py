"""Benchmark operations other than source/disassembly: resource extraction and
SWF<->XML round trips. Every unit is a tag of the input, so counts stay exact.

extract: one swfextract call per resource tag; an output only counts when it is
         valid (images decode, binaryData equals the tag payload, sounds and
         fonts are non-empty).
xml:     swf2xml then xml2swf; a tag counts when the rebuilt SWF has the same
         tag code and bytes at the same position.
"""
from __future__ import annotations

import hashlib
import io
import subprocess
import tempfile
import time
from pathlib import Path

from .swf_parser import SWFFile

# tag code -> (swfextract flag, output extension, resource kind)
EXTRACTABLE = {
    6: ("-j", "jpg", "image"), 21: ("-j", "jpg", "image"), 35: ("-j", "jpg", "image"), 90: ("-j", "jpg", "image"),
    20: ("-p", "png", "image"), 36: ("-p", "png", "image"),
    14: ("-s", "wav", "sound"),
    10: ("-F", "swf", "font"), 48: ("-F", "swf", "font"), 75: ("-F", "swf", "font"),
    87: ("-b", "bin", "binary"),
}
TIMEOUT = 60


def _run(command, cwd):
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return completed, (time.perf_counter() - started) * 1000


def _valid_output(kind, tag, data):
    if not data:
        return False, "empty output"
    if kind == "image":
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                image.load()
            return True, None
        except Exception as error:  # Pillow raises many types for bad images
            return False, "image does not decode: " + type(error).__name__
    if kind == "binary":
        return (data == tag.data[6:], None if data == tag.data[6:] else "bytes differ from DefineBinaryData payload")
    return True, None


def extract(executable, data):
    movie = SWFFile()
    movie.read_bytes(data)
    targets = [(index, tag) for index, tag in enumerate(movie.tags) if tag.tag_type in EXTRACTABLE and tag.char_id is not None]
    units, cli_ms = [], 0.0
    with tempfile.TemporaryDirectory(prefix="pyswf-extract-") as folder:
        folder = Path(folder)
        (folder / "input.swf").write_bytes(data)
        for index, tag in targets:
            flag, extension, kind = EXTRACTABLE[tag.tag_type]
            output = folder / f"{tag.char_id}.{extension}"
            row = {"document_id": f"tag:{index}", "scope": "resource", "resource_kind": kind,
                   "tag_code": tag.tag_type, "character_id": tag.char_id}
            try:
                completed, elapsed = _run([executable, flag, str(tag.char_id), "-o", output.name, "input.swf"], folder)
                cli_ms += elapsed
                payload = output.read_bytes() if output.is_file() else b""
                valid, reason = _valid_output(kind, tag, payload)
                row.update(generation="available" if valid else "failed", output_bytes=len(payload),
                           output_sha256=hashlib.sha256(payload).hexdigest() if payload else None,
                           diagnostic=reason or (completed.stderr.decode("utf-8", "replace")[-300:] if not valid else None))
            except subprocess.TimeoutExpired:
                row.update(generation="failed", diagnostic=f"timed out after {TIMEOUT}s")
            units.append(row)
    return units, cli_ms


def xml_roundtrip(executable, data):
    movie = SWFFile()
    movie.read_bytes(data)
    original = [(tag.tag_type, bytes(tag.data)) for tag in movie.tags]
    units, cli_ms = [], 0.0
    with tempfile.TemporaryDirectory(prefix="pyswf-xml-") as folder:
        folder = Path(folder)
        (folder / "input.swf").write_bytes(data)
        first, a = _run([executable, "swf2xml", "input.swf", "movie.xml"], folder)
        second, b = _run([executable, "xml2swf", "movie.xml", "rebuilt.swf"], folder)
        cli_ms = a + b
        rebuilt = []
        diagnostic = None
        try:
            again = SWFFile()
            again.read_bytes((folder / "rebuilt.swf").read_bytes())
            rebuilt = [(tag.tag_type, bytes(tag.data)) for tag in again.tags]
        except Exception as error:
            diagnostic = "rebuilt SWF unreadable: " + str(error)[:200] + " " + (second.stderr or first.stderr).decode("utf-8", "replace")[-200:]
        for index, (code, payload) in enumerate(original):
            same = index < len(rebuilt) and rebuilt[index] == (code, payload)
            units.append({"document_id": f"tag:{index}", "scope": "tag", "tag_code": code,
                          "generation": "available" if same else "failed",
                          "diagnostic": None if same else (diagnostic or "tag differs after the XML round trip")})
    return units, cli_ms
