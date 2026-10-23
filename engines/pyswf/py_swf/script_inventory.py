"""Byte-accurate inventory of scripts, including nested timelines and events.

Container parsing is strict. A damaged/unsupported container becomes a counted
diagnostic unit, never an invisible omission from the coverage denominator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .avm2 import ABCFile, build_method_mapping
from .swf_parser import BitStream, SWFFile, SWFTag, decompress_body, parse_rect


@dataclass
class ScriptUnit:
    document_id: str
    tag_path: tuple[int, ...]
    language: str
    kind: str
    name: str
    code: bytes = b""
    tag: SWFTag | None = None
    abc: Any = None
    body: Any = None
    body_index: int | None = None
    method_index: int | None = None
    byte_start: int = 0
    byte_end: int = 0
    event_flags: int | None = None
    diagnostic: str | None = None
    # Nested functions are inventory-only until their enclosing action sizes,
    # branch offsets and captured context can be edited transactionally.
    avm1_context: dict | None = None
    normalized_byte_start: int | None = None

    def metadata(self):
        result = {"document_id": self.document_id, "tag_path": list(self.tag_path), "language": self.language, "kind": self.kind, "name": self.name, "body_index": self.body_index, "method_index": self.method_index, "byte_start": self.byte_start, "byte_end": self.byte_end, "bytecode_bytes": len(self.code), "bytecode_sha256": hashlib.sha256(self.code).hexdigest(), "event_flags": self.event_flags, "diagnostic": self.diagnostic}
        if self.normalized_byte_start is not None:
            result["normalized_byte_start"] = self.normalized_byte_start
        result['scope'] = 'diagnostic' if self.diagnostic else 'method' if self.language == 'as3' else 'function' if self.kind == 'function' else 'action'
        if self.avm1_context is not None:
            result.update(avm1_context=self.avm1_context, read_only=True,
                          source_edit_reason="Nested AVM1 function bodies require enclosing action-size and branch updates")
        return result


class Cursor:
    def __init__(self, data, pos=0):
        self.data, self.pos = data, pos

    def take(self, length):
        if length < 0 or self.pos + length > len(self.data):
            raise ValueError("Truncated script container")
        value = self.data[self.pos:self.pos + length]
        self.pos += length
        return value

    def uint(self, width):
        return int.from_bytes(self.take(width), "little")

    def string(self):
        end = self.data.find(b"\0", self.pos)
        if end < 0:
            raise ValueError("Unterminated container string")
        self.take(end + 1 - self.pos)

    def bits(self, reader):
        stream = BitStream(self.data[self.pos:])
        reader(stream)
        stream.align()
        self.take(stream.byte_offset)


def skip_matrix(stream):
    if stream.read_bits(1):
        size = stream.read_bits(5)
        stream.read_bits(size * 2)
    if stream.read_bits(1):
        size = stream.read_bits(5)
        stream.read_bits(size * 2)
    size = stream.read_bits(5)
    stream.read_bits(size * 2)


def skip_cxform(stream):
    add, multiply, size = stream.read_bits(1), stream.read_bits(1), stream.read_bits(4)
    stream.read_bits(size * 4 * (add + multiply))


def skip_filters(cursor):
    for _ in range(cursor.uint(1)):
        kind = cursor.uint(1)
        if kind in (0, 1, 2, 3, 6):
            cursor.take({0: 23, 1: 9, 2: 15, 3: 27, 6: 80}[kind])
        elif kind in (4, 7):
            cursor.take(cursor.uint(1) * 5 + 19)
        elif kind == 5:
            width, height = cursor.uint(1), cursor.uint(1)
            cursor.take(13 + 4 * width * height)
        else:
            raise ValueError(f"Unknown filter {kind}; cannot safely locate clip actions")


def embedded_actions(tag, version):
    data, kind = tag.data, tag.tag_type
    cursor = Cursor(data)
    if kind == 7:
        cursor.take(2)
        while cursor.uint(1):
            cursor.take(4)
            cursor.bits(skip_matrix)
        if cursor.pos < len(data):
            yield "button", 0, cursor.pos, len(data), None
    elif kind == 34:
        cursor.take(3)
        action_offset = cursor.uint(2)
        if not action_offset:
            return
        cursor.pos = 3 + action_offset
        if cursor.pos < 5 or cursor.pos >= len(data):
            raise ValueError("Invalid Button2 action offset")
        ordinal = 0
        while cursor.pos < len(data):
            start = cursor.pos
            size, flags = cursor.uint(2), cursor.uint(2)
            end = start + size if size else len(data)
            if end < cursor.pos or end > len(data):
                raise ValueError("Invalid ButtonCondAction size")
            yield "button2", ordinal, cursor.pos, end, flags
            cursor.pos = end
            ordinal += 1
            if not size:
                break
    elif kind in (26, 70, 94):
        flags = cursor.uint(1)
        extra = cursor.uint(1) if kind in (70, 94) else 0
        cursor.take(2)  # depth
        if extra & 8 or extra & 16 and flags & 2:
            cursor.string()
        if flags & 2:
            cursor.take(2)
        if flags & 4:
            cursor.bits(skip_matrix)
        if flags & 8:
            cursor.bits(skip_cxform)
        if flags & 16:
            cursor.take(2)
        if flags & 32:
            cursor.string()
        if flags & 64:
            cursor.take(2)
        if extra & 1:
            skip_filters(cursor)
        if extra & 2:
            cursor.take(1)
        if extra & 4:
            cursor.take(1)
        if extra & 32:
            cursor.take(1)
        if extra & 64:
            cursor.take(4)
        if not flags & 128:
            return
        width = 4 if version >= 6 else 2
        cursor.take(2 + width)  # reserved and AllEventFlags
        ordinal = 0
        while True:
            event = cursor.uint(width)
            if not event:
                break
            size = cursor.uint(4)
            end = cursor.pos + size
            if event & 0x20000:
                cursor.take(1)  # KeyCode is not bytecode
            if end < cursor.pos or end > len(data):
                raise ValueError("Invalid ClipActionRecord size")
            yield "clip", ordinal, cursor.pos, end, event
            cursor.pos = end
            ordinal += 1


def nested_tags(data):
    cursor, index = Cursor(data), 0
    while cursor.pos < len(data):
        record = cursor.uint(2)
        kind, size = record >> 6, record & 63
        if size == 63:
            size = cursor.uint(4)
        yield index, SWFTag(kind, cursor.take(size))
        index += 1
        if kind == 0:
            if cursor.pos != len(data):
                raise ValueError("Data after sprite End tag")
            break


def _normalized_tag_offsets(data, max_depth):
    """Locate payloads in the original decompressed stream, retaining headers.

    Repacking SWFTag would normalize long headers and move function identities.
    This independent walk therefore uses the original FWS/CWS/ZWS bytes.
    """
    body = decompress_body(data[:3].decode("ascii"), data[8:], int.from_bytes(data[4:8], "little"))
    stream = BitStream(body)
    parse_rect(stream)
    offsets = {}
    def walk(payload, base, parent=()):
        if len(parent) > max_depth:
            raise ValueError("Script container nesting limit exceeded")
        cursor, index = Cursor(payload), 0
        while cursor.pos < len(payload):
            record = cursor.uint(2)
            kind, size = record >> 6, record & 63
            if size == 63:
                size = cursor.uint(4)
            start = base + cursor.pos
            value = cursor.take(size)
            path = (*parent, index)
            offsets[path] = start
            if kind == 39 and len(value) >= 4:
                walk(value[4:], start + 4, path)
            index += 1
            if kind == 0:
                break
    start = stream.byte_offset + 4  # frame rate and frame count
    walk(body[start:], 8 + start)
    return offsets


def _nested_avm1_functions(parent, tag_start, *, max_depth=64):
    """Strict byte-layout discovery; never infer a function from a Rust listing.

    ActionLength covers the header of Function/Function2/With/Try; their bodies
    follow that payload. Pool metadata records linear declaration lineage;
    branches explicitly make runtime capture unproven. Changes inside a
    function never leak into its enclosing list during this structural walk.
    """
    def string(cursor):
        start = cursor.pos
        cursor.string()
        return cursor.data[start:cursor.pos - 1].decode("utf-8")
    def verified_pool_lineage(code, inherited):
        cursor, pools, first_branch, actions = Cursor(code), [], None, 0
        while cursor.pos < len(code):
            actions += 1
            if actions > 100000:
                raise ValueError("Nested AVM1 instruction discovery limit exceeded")
            offset, opcode = cursor.pos, cursor.uint(1)
            if opcode == 0:
                break
            if opcode < 0x80:
                continue
            payload = cursor.take(cursor.uint(2))
            if opcode in (0x99, 0x9d, 0x8a, 0x8d) and first_branch is None:
                first_branch = offset
            if opcode == 0x88:
                pools.append(offset)
            if opcode in (0x9b, 0x8e, 0x94):
                if len(payload) < 2:
                    raise ValueError("Truncated inline AVM1 body header")
                cursor.take(int.from_bytes(payload[-2:], "little"))
            elif opcode == 0x8f:
                if len(payload) < 7:
                    raise ValueError("Truncated AVM1 Try header")
                cursor.take(sum(int.from_bytes(payload[p:p + 2], "little") for p in (1, 3, 5)))
        return inherited and not (first_branch is not None and
            (len(pools) > 1 or any(p > first_branch for p in pools)))
    def walk(unit, code, relative_base, initial_pool, depth, inherited_verified=True):
        if depth >= max_depth:
            raise ValueError("Nested AVM1 function depth limit exceeded")
        pool_verified = verified_pool_lineage(code, inherited_verified)
        cursor, pool, actions = Cursor(code), list(initial_pool), 0
        while cursor.pos < len(code):
            actions += 1
            if actions > 100000:
                raise ValueError("Nested AVM1 instruction discovery limit exceeded")
            opcode = cursor.uint(1)
            if opcode == 0:
                return
            if opcode < 0x80:
                continue
            header = Cursor(cursor.take(cursor.uint(2)))
            if opcode == 0x88:
                count = header.uint(2)
                pool = [string(header) for _ in range(count)]
            elif opcode in (0x9b, 0x8e):
                name, count = string(header), header.uint(2)
                registers, register_count, flags = [], None, 0
                if opcode == 0x8e:
                    register_count, flags = header.uint(1), header.uint(2)
                    if flags & ~0x1ff:
                        raise ValueError("Reserved AVM1 Function2 flags")
                parameters = []
                for _ in range(count):
                    if opcode == 0x8e:
                        register = header.uint(1)
                        if register > register_count:
                            raise ValueError("AVM1 parameter register exceeds register_count")
                        registers.append(register)
                    parameters.append(string(header))
                size = header.uint(2)
                if header.pos != len(header.data):
                    raise ValueError("Unexpected AVM1 function header bytes")
                start = relative_base + cursor.pos
                body = cursor.take(size)
                absolute = tag_start + start
                child = ScriptUnit(unit.document_id + f"/function:{absolute:x}", unit.tag_path,
                    "as2", "function", name or f"anonymous_{absolute:x}", body, unit.tag,
                    byte_start=start, byte_end=start + size, event_flags=unit.event_flags,
                    normalized_byte_start=absolute,
                    avm1_context={"parameters": parameters, "registers": registers,
                        "register_count": register_count, "flags": flags, "constants": list(pool),
                        "pool_runtime_verified": pool_verified,
                        "constants_sha256": hashlib.sha256(json.dumps(pool, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()})
                yield child
                yield from walk(child, body, start, pool, depth + 1, pool_verified)
            elif opcode == 0x94:
                size = header.uint(2)
                if header.pos != len(header.data):
                    raise ValueError("Unexpected AVM1 With header bytes")
                start = relative_base + cursor.pos
                yield from walk(unit, cursor.take(size), start, pool, depth + 1, pool_verified)
            elif opcode == 0x8f:
                flags = header.uint(1)
                sizes = [header.uint(2) for _ in range(3)]
                if flags & ~7 or not flags & 1 and sizes[1] or not flags & 2 and sizes[2]:
                    raise ValueError("AVM1 Try sizes conflict with flags")
                header.uint(1) if flags & 4 else string(header)
                if header.pos != len(header.data):
                    raise ValueError("Unexpected AVM1 Try header bytes")
                for size in sizes:
                    start = relative_base + cursor.pos
                    yield from walk(unit, cursor.take(size), start, pool, depth + 1, pool_verified)
            else:
                continue  # Other long actions contain no inline action bodies.
            if header.pos != len(header.data):
                raise ValueError("Unexpected AVM1 discovery payload bytes")
    yield from walk(parent, parent.code, parent.byte_start, [], 0)


def enumerate_scripts(movie: SWFFile | bytes, *, max_depth=32, max_units=100000, include_nested_avm1=False):
    # Preserve editing/project inventory semantics. The opt-in is used by
    # benchmarks and requires source bytes for exact normalized offsets.
    offsets = None
    if include_nested_avm1 and not isinstance(movie, bytes):
        raise ValueError("Nested AVM1 inventory requires original SWF bytes")
    if isinstance(movie, bytes):
        source = movie
        parsed = SWFFile()
        parsed.read_bytes(movie)
        movie = parsed
        if include_nested_avm1:
            offsets = _normalized_tag_offsets(source, max_depth)
    count, discovered_bytes = 0, 0
    def walk(tags, parent=()):
        nonlocal count, discovered_bytes
        if len(parent) > max_depth:
            raise ValueError("Script container nesting limit exceeded")
        for index, tag in tags:
            path = (*parent, index)
            prefix = "tag:" + "/".join(map(str, path))
            try:
                if tag.parse_error:
                    raise ValueError(tag.parse_error)
                if tag.tag_type == 39:
                    if len(tag.data) < 4:
                        raise ValueError("Truncated sprite header")
                    yield from walk(nested_tags(tag.data[4:]), path)
                    continue
                units = []
                if tag.is_doabc:
                    parsed = tag.parse_doabc()
                    if parsed is None:
                        raise ValueError("Malformed ABC tag")
                    abc = ABCFile()
                    abc.parse(parsed[2])
                    names = build_method_mapping(abc)
                    for body_index, body in enumerate(abc.method_bodies):
                        method_index = body.method
                        units.append(ScriptUnit(prefix + f"/method:{method_index}", path, "as3", "method", names.get(method_index, f"method_{method_index}"), body.code, tag, abc, body, body_index, method_index, byte_end=len(body.code)))
                elif tag.tag_type in (12, 59):
                    start = 2 if tag.tag_type == 59 else 0
                    if len(tag.data) < start:
                        raise ValueError("Truncated DoInitAction id")
                    units.append(ScriptUnit(prefix + "/actions", path, "as2", "init" if start else "frame", tag.name, tag.data[start:], tag, byte_start=start, byte_end=len(tag.data)))
                elif tag.tag_type in (7, 34, 26, 70, 94):
                    for kind, ordinal, start, end, event in embedded_actions(tag, movie.version):
                        units.append(ScriptUnit(prefix + f"/{kind}:{ordinal}", path, "as2", kind, f"{tag.name}:{kind}:{ordinal}", tag.data[start:end], tag, byte_start=start, byte_end=end, event_flags=event))
                for unit in units:
                    if offsets is not None and unit.language == "as2":
                        unit.normalized_byte_start = offsets[path] + unit.byte_start
                    count += 1
                    if include_nested_avm1:
                        discovered_bytes += len(unit.code)
                        if discovered_bytes > 256 * 1024**2:
                            raise ValueError("Script inventory copied byte budget exceeded")
                    if count > max_units:
                        raise ValueError("Script inventory unit limit exceeded")
                    yield unit
                    if include_nested_avm1 and unit.language == "as2":
                        for child in _nested_avm1_functions(unit, offsets[path]):
                            count += 1
                            discovered_bytes += len(child.code)
                            if discovered_bytes > 256 * 1024**2:
                                raise ValueError("Script inventory copied byte budget exceeded")
                            if count > max_units:
                                raise ValueError("Script inventory unit limit exceeded")
                            yield child
            except (ValueError, IndexError, EOFError, KeyError, UnicodeError) as error:
                count += 1
                if count > max_units:
                    raise ValueError("Script inventory unit limit exceeded")
                yield ScriptUnit(prefix + "/diagnostic", path, "unknown", "diagnostic", tag.name, tag=tag, diagnostic=str(error))
    yield from walk(enumerate(movie.tags))
