"""Deterministic corpus of damaged SWF files for the robustness benchmark (tools/bench.py --only robustness).

Every case is derived from a SWF of the sealed parity corpus by one kind of damage (truncated or corrupted, nothing else),
written to corpus/damaged/ with a manifest that gives the source file, the damage kind, its parameters, the seed and the
sha256 of the result, and the byte ranges of the original the damage reaches (what the benchmark needs to know which
units are still intact).

    python tools/make_damaged_corpus.py            # writes corpus/damaged/ and its manifest
    python tools/make_damaged_corpus.py --check    # regenerates in memory and fails if corpus/damaged/ differs

The same plan and seed give the same bytes on any machine and Python version: the random choices come from SHA-256 in
counter mode (`Stream`), not from `random`, and the generator never recompresses anything (compressed sources are only
damaged byte by byte), so no zlib build can change the output.

Damage kinds (docs/BENCHMARKS_CODIGO.md, Robustez):

    truncate_header   cut inside the header (signature, length, rectangle, frame rate or frame count)
    truncate_tag      cut in the middle of a tag that is not code
    truncate_code     cut in the middle of the code tag (DoABC or DoAction)
    bad_length        wrong file length in the header
    bad_compression   broken zlib stream in a CWS file (flipped bytes, bad zlib header or bad checksum)
    bad_tag_length    wrong length field in a tag record
    flip_code         random bytes flipped inside the body of the code tag
    flip_noncode      random bytes flipped inside the body of a tag that is not code
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zlib

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from py_swf import paths
from py_swf.canonical_inventory import build_inventory
from py_swf.robustness import CODE_TAGS, decompressed, tag_spans

SEED = 20261024
CORPUS_ID = "damaged-20261024"
SOURCE_ROOT = "parity-20261014/optimized"
OFFSET_BASIS = "decompressed_swf_including_8_byte_header"
DEFAULT_OUT = paths.CORPUS / "damaged"

KINDS = {
    "truncate_header": "Truncado dentro de la cabecera",
    "truncate_tag": "Truncado en mitad de un tag",
    "truncate_code": "Truncado en mitad del código",
    "bad_length": "Longitud de archivo errónea",
    "bad_compression": "Flujo zlib roto (CWS)",
    "bad_tag_length": "Longitud de tag errónea",
    "flip_code": "Bytes alterados en un tag de código",
    "flip_noncode": "Bytes alterados en un tag que no es código",
}

FAMILIES = ("dev-as2-timeline", "dev-as2-captures", "dev-as3-classes-namespaces", "dev-as3-closures",
            "dev-as3-control-handlers", "dev-haxe-application")
AS2, CAPTURES, CLASSES, CLOSURES, HANDLERS, HAXE = FAMILIES

# (kind, family, options). One line per case; the id is kind-family, with -2, -3 for the repeats.
PLAN = (
    ("truncate_header", AS2, {"cut": 3}),
    ("truncate_header", CAPTURES, {"cut": 6}),
    ("truncate_header", CLASSES, {"cut": 8}),
    ("truncate_header", CLOSURES, {"cut": 11}),
    ("truncate_header", HANDLERS, {"cut": "first-3"}),
    ("truncate_header", HAXE, {"cut": "first-1"}),

    ("truncate_tag", AS2, {"which": "first"}),
    ("truncate_tag", CAPTURES, {"which": "last"}),
    ("truncate_tag", CLASSES, {"which": "last"}),
    ("truncate_tag", CLOSURES, {"which": "last"}),
    ("truncate_tag", HANDLERS, {"which": "first"}),
    ("truncate_tag", HAXE, {"which": "last"}),

    ("truncate_code", AS2, {"between": [0.3, 0.7]}),
    ("truncate_code", CAPTURES, {"between": [0.3, 0.7]}),
    ("truncate_code", CLASSES, {"between": [0.3, 0.7]}),
    ("truncate_code", CLOSURES, {"between": [0.3, 0.7]}),
    ("truncate_code", HANDLERS, {"between": [0.3, 0.7]}),
    ("truncate_code", HAXE, {"between": [0.2, 0.45]}),
    ("truncate_code", HAXE, {"between": [0.55, 0.85]}),

    ("bad_length", AS2, {"length": "larger"}),
    ("bad_length", CAPTURES, {"length": "smaller"}),
    ("bad_length", CLASSES, {"length": "zero"}),
    ("bad_length", CLOSURES, {"length": "huge"}),
    ("bad_length", HANDLERS, {"length": "larger"}),
    ("bad_length", HAXE, {"length": "smaller"}),

    ("bad_compression", AS2, {"how": "flip_stream", "flips": 2, "at": [0.25, 0.75]}),
    ("bad_compression", CAPTURES, {"how": "bad_checksum"}),
    ("bad_compression", CLASSES, {"how": "flip_stream", "flips": 2, "at": [0.8, 0.95]}),
    ("bad_compression", CLOSURES, {"how": "bad_zlib_header"}),
    ("bad_compression", HANDLERS, {"how": "flip_stream", "flips": 3, "at": [0.25, 0.75]}),
    ("bad_compression", HAXE, {"how": "flip_stream", "flips": 2, "at": [0.8, 0.95]}),

    ("bad_tag_length", AS2, {"target": "code", "length": "longer"}),
    ("bad_tag_length", CAPTURES, {"target": "code", "length": "shorter"}),
    ("bad_tag_length", CLASSES, {"target": "code", "length": "longer"}),
    ("bad_tag_length", CLOSURES, {"target": "noncode", "which": "last", "length": "shorter"}),
    ("bad_tag_length", HANDLERS, {"target": "noncode", "which": "last", "length": "longer"}),
    ("bad_tag_length", HAXE, {"target": "noncode", "which": "last", "length": "shorter"}),

    ("flip_code", AS2, {"flips": 1, "where": "code"}),
    ("flip_code", CAPTURES, {"flips": 2, "where": "code"}),
    ("flip_code", CLASSES, {"flips": 1, "where": "code"}),
    ("flip_code", CLASSES, {"flips": 3, "where": "anywhere"}),
    ("flip_code", CLOSURES, {"flips": 2, "where": "code"}),
    ("flip_code", HANDLERS, {"flips": 1, "where": "code"}),
    ("flip_code", HAXE, {"flips": 1, "where": "code"}),
    ("flip_code", HAXE, {"flips": 4, "where": "anywhere"}),

    ("flip_noncode", AS2, {"flips": 1}),
    ("flip_noncode", CAPTURES, {"flips": 1}),
    ("flip_noncode", CLASSES, {"flips": 2}),
    ("flip_noncode", CLOSURES, {"flips": 1}),
    ("flip_noncode", HANDLERS, {"flips": 2}),
    ("flip_noncode", HAXE, {"flips": 3}),
)


class Stream:
    """Deterministic randomness: SHA-256 of the label and a counter. Nothing depends on the Python version."""

    def __init__(self, *label):
        self.label = ":".join(map(str, label)).encode("utf-8")
        self.counter, self.buffer = 0, b""

    def take(self, count):
        while len(self.buffer) < count:
            self.buffer += hashlib.sha256(self.label + b"#" + str(self.counter).encode("ascii")).digest()
            self.counter += 1
        taken, self.buffer = self.buffer[:count], self.buffer[count:]
        return taken

    def below(self, limit):
        """A uniform integer in [0, limit): rejection sampling over 32-bit words, so there is no modulo bias."""
        if limit <= 0:
            raise ValueError("below() needs a positive limit")
        top = (1 << 32) - (1 << 32) % limit
        while True:
            value = int.from_bytes(self.take(4), "big")
            if value < top:
                return value % limit


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def merged(ranges):
    """Sorted ranges with the overlapping or touching ones joined."""
    joined = []
    for low, high in sorted(ranges):
        if joined and low <= joined[-1][1]:
            joined[-1][1] = max(joined[-1][1], high)
        else:
            joined.append([low, high])
    return joined


def flip_bytes(buffer, stream, count, low, high):
    """Change `count` distinct bytes with offsets in [low, high): xor with a random non-zero value."""
    chosen = []
    while len(chosen) < min(count, high - low):
        position = low + stream.below(high - low)
        if position not in chosen:
            chosen.append(position)
    for position in sorted(chosen):
        buffer[position] ^= 1 + stream.below(255)
    return sorted(chosen)


def layout(data):
    """The tags of an uncompressed source: (spans of the top-level tags in order, offset of the first tag)."""
    spans, first = tag_spans(decompressed(data))
    ordered = [(path[0], *spans[path]) for path in sorted(spans) if len(path) == 1]
    return ordered, first


def code_blocks(data):
    """The byte ranges of the code itself: one per AS3 method body, one per AS2 action, in file order."""
    units = build_inventory(data)["units"]
    return sorted((u["bytecode_offset"], u["bytecode_offset"] + u["bytecode_bytes"]) for u in units
                  if u["scope"] in ("method", "action") and u["bytecode_offset"] is not None and u["bytecode_bytes"] >= 2)


# --- the damage kinds: each returns (damaged bytes, parameters, damaged ranges of the original) ---------------------

def truncate_header(data, options, stream):
    tags, first = layout(data)
    cut = options["cut"]
    if isinstance(cut, str):  # "first-3": that many bytes before the first tag
        cut = first - int(cut.split("-", 1)[1])
    if not 0 < cut < first:
        raise ValueError("a header cut must fall inside the header (0 < %d < %d)" % (cut, first))
    return data[:cut], {"cut": cut, "header_bytes": first}, [[cut, len(data)]]


def truncate_inside(data, tag, stream):
    index, record, payload, end, code = tag
    if end - payload < 2:
        raise ValueError("a tag needs two payload bytes to be cut inside")
    cut = payload + 1 + stream.below(end - payload - 1)
    return data[:cut], {"tag_index": index, "tag_code": code, "cut": cut}, [[cut, len(data)]]


def truncate_tag(data, options, stream):
    tags, first = layout(data)
    candidates = [tag for tag in tags if tag[4] not in CODE_TAGS and tag[3] - tag[2] >= 2]
    if not candidates:
        raise ValueError("the source has no tag that is not code and can be cut")
    return truncate_inside(data, candidates[0 if options["which"] == "first" else -1], stream)


def truncate_code(data, options, stream):
    """Cut in the middle of a code block (a method body or an action), taken from the stretch of blocks `between`."""
    blocks = code_blocks(data)
    low = int(len(blocks) * options["between"][0])
    high = max(low + 1, int(len(blocks) * options["between"][1]))
    chosen = low + stream.below(high - low)
    start, end = blocks[chosen]
    cut = start + 1 + stream.below(end - start - 1)
    return data[:cut], {"block": chosen, "blocks": len(blocks), "cut": cut, "between": options["between"]}, [[cut, len(data)]]


def bad_length(data, options, stream):
    real = int.from_bytes(data[4:8], "little")
    value = {"larger": real + 4096 + stream.below(4096), "smaller": real // 2 + stream.below(max(1, real // 4)),
             "zero": 0, "huge": 0xFFFFFFFF}[options["length"]]
    return data[:4] + value.to_bytes(4, "little") + data[8:], {"length": options["length"], "declared": value, "real": real}, [[4, 8]]


def bad_compression(data, options, stream):
    if data[:3] != b"CWS":
        raise ValueError("a broken compression stream needs a CWS source")
    body, total = bytearray(data[8:]), len(zlib.decompress(data[8:])) + 8
    how = options["how"]
    if how == "bad_zlib_header":
        body[0] ^= 0x01
        return data[:8] + bytes(body), {"how": how, "offset": 8}, [[8, total]]
    if how == "bad_checksum":
        position = len(body) - 1 - stream.below(4)
        body[position] ^= 1 + stream.below(255)
        return data[:8] + bytes(body), {"how": how, "offset": 8 + position}, []
    deflate = len(body) - 6  # the bytes between the two-byte zlib header and the four-byte checksum
    low = 2 + int(deflate * options["at"][0])
    high = max(low + options["flips"], 2 + int(deflate * options["at"][1]))
    positions = flip_bytes(body, stream, options["flips"], low, high)
    # Everything the decoder had produced before the first changed byte is as the original; the rest is lost.
    kept = len(zlib.decompressobj().decompress(data[8:8 + positions[0]]))
    return data[:8] + bytes(body), {"how": how, "offsets": [8 + p for p in positions], "intact_decompressed": kept}, [[8 + kept, total]]


def bad_tag_length(data, options, stream):
    tags, first = layout(data)
    wanted = [tag for tag in tags if (tag[4] in CODE_TAGS) == (options["target"] == "code") and tag[3] - tag[2] >= 2]
    if not wanted:
        raise ValueError("the source has no tag to give a wrong length")
    index, record, payload, end, code = wanted[-1 if options.get("which") == "last" else 0]
    length, long_form = end - payload, payload - record == 6
    delta = 3 + stream.below(5)
    wrong = length + delta if options["length"] == "longer" else max(0, length - delta)
    out = bytearray(data)
    if long_form:
        out[record + 2:record + 6] = wrong.to_bytes(4, "little")
    else:
        wrong = min(wrong, 62)  # 63 would announce the long form: that is another kind of damage
        out[record:record + 2] = ((code << 6) | wrong).to_bytes(2, "little")
    # The records after this one are read from the wrong place, so everything from this record on is unreliable.
    return bytes(out), {"tag_index": index, "tag_code": code, "long_form": long_form, "length": length, "declared": wrong}, [[record, len(data)]]


def flip_code(data, options, stream):
    """Change bytes of the code tag: `where` is "code" for the bytes of the method bodies (or the action), "anywhere" for
    any byte of the tag payload (constant pool and tables included)."""
    tags, first = layout(data)
    index, record, payload, end, code = [tag for tag in tags if tag[4] in CODE_TAGS][0]
    buffer = bytearray(data)
    if options["where"] == "code":
        blocks = code_blocks(data)
        total = sum(high - low for low, high in blocks)
        chosen = []
        while len(chosen) < min(options["flips"], total):
            offset = stream.below(total)
            for low, high in blocks:
                if offset < high - low:
                    position = low + offset
                    break
                offset -= high - low
            if position not in chosen:
                chosen.append(position)
        positions = sorted(chosen)
        for position in positions:
            buffer[position] ^= 1 + stream.below(255)
    else:
        positions = flip_bytes(buffer, stream, options["flips"], payload, end)
    return bytes(buffer), {"tag_index": index, "tag_code": code, "where": options["where"], "offsets": positions}, merged([[p, p + 1] for p in positions])


def flip_noncode(data, options, stream):
    tags, first = layout(data)
    candidates = [tag for tag in tags if tag[4] not in CODE_TAGS and tag[4] not in (0, 1) and tag[3] > tag[2]]
    index, record, payload, end, code = candidates[stream.below(len(candidates))]
    buffer = bytearray(data)
    positions = flip_bytes(buffer, stream, options["flips"], payload, end)
    return bytes(buffer), {"tag_index": index, "tag_code": code, "offsets": positions}, merged([[p, p + 1] for p in positions])


DAMAGE = {"truncate_header": truncate_header, "truncate_tag": truncate_tag, "truncate_code": truncate_code,
          "bad_length": bad_length, "bad_compression": bad_compression, "bad_tag_length": bad_tag_length,
          "flip_code": flip_code, "flip_noncode": flip_noncode}


# --- the corpus ----------------------------------------------------------------------------------------------------

def case_ids():
    seen, ids = {}, []
    for kind, family, _ in PLAN:
        seen[(kind, family)] = seen.get((kind, family), 0) + 1
        ids.append("%s-%s" % (kind.replace("_", "-"), family) + ("" if seen[(kind, family)] == 1 else "-%d" % seen[(kind, family)]))
    return ids


def build(corpus=None):
    """Every damaged file of the plan: {id: (bytes, manifest entry)} plus the manifest header. Reads only the sealed corpus."""
    corpus = Path(corpus or paths.CORPUS)
    root = corpus / SOURCE_ROOT
    parity = json.loads((root / "manifest.json").read_bytes())
    by_path = {case["relative"]: case for case in parity["cases"]}
    files, cases = {}, []
    for case_id, (kind, family, options) in zip(case_ids(), PLAN):
        compressed = kind == "bad_compression"
        relative = "swf/%s/release.%s.swf" % (family, "cws" if compressed else "fws")
        source = by_path[relative]
        data = (root / relative).read_bytes()
        if sha256(data) != source["sha256"]:
            raise ValueError("The source changed since the corpus was sealed: " + relative)
        seed = int.from_bytes(hashlib.sha256(("%d:%s" % (SEED, case_id)).encode("utf-8")).digest()[:8], "big")
        damaged, params, ranges = DAMAGE[kind](data, options, Stream(seed))
        if damaged == data:
            raise ValueError("The damage left the source unchanged: " + case_id)
        files[case_id + ".swf"] = damaged
        cases.append({"id": case_id, "relative": case_id + ".swf", "kind": kind, "family_id": family, "language": source["language"],
                      "source": "%s/%s" % (SOURCE_ROOT, relative), "source_sha256": source["sha256"], "source_bytes": source["bytes"],
                      "options": options, "params": params, "seed": seed, "bytes": len(damaged), "sha256": sha256(damaged),
                      "damage": {"basis": OFFSET_BASIS, "ranges": ranges}})
    manifest = {"schema_version": 1, "corpus_id": CORPUS_ID, "generator": "engines/pyswf/tools/make_damaged_corpus.py", "seed": SEED,
                "prng": "sha256-counter", "source_corpus": SOURCE_ROOT,
                "source_manifest_sha256": sha256((root / "manifest.json").read_bytes()),
                "damage_kinds": KINDS, "cases": cases}
    return files, manifest


def encode(manifest):
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")


def write(out, corpus=None):
    files, manifest = build(corpus)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.swf"):
        if stale.name not in files:
            stale.unlink()
    for name, data in files.items():
        (out / name).write_bytes(data)
    text = encode(manifest)
    (out / "manifest.json").write_bytes(text)
    (out / "manifest.sha256").write_text(sha256(text) + "\n", encoding="ascii")
    return manifest


def check(out, corpus=None):
    """Differences between a regeneration in memory and what is on disk (an empty list when they are the same)."""
    files, manifest = build(corpus)
    out = Path(out)
    problems = []
    for name, data in files.items():
        if not (out / name).is_file():
            problems.append("falta " + name)
        elif (out / name).read_bytes() != data:
            problems.append("difiere " + name)
    problems += ["sobra " + path.name for path in out.glob("*.swf") if path.name not in files]
    if not (out / "manifest.json").is_file() or (out / "manifest.json").read_bytes() != encode(manifest):
        problems.append("difiere manifest.json")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="carpeta de salida (por defecto corpus/damaged)")
    parser.add_argument("--check", action="store_true", help="no escribe: comprueba que la carpeta es lo que genera el plan")
    args = parser.parse_args()
    if args.check:
        problems = check(args.out)
        for problem in problems:
            print(problem)
        print("corpus dañado: " + ("coincide con el plan" if not problems else "%d diferencias" % len(problems)))
        return 1 if problems else 0
    manifest = write(args.out)
    kinds = {}
    for case in manifest["cases"]:
        kinds[case["kind"]] = kinds.get(case["kind"], 0) + 1
    print("%d casos en %s" % (len(manifest["cases"]), args.out), json.dumps(kinds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
