"""DefineButton/DefineButton2 parsing and lightweight state previews."""

from __future__ import annotations

from . import shapes
from .avm1 import disassemble_avm1
from .swf_parser import BitStream

BUTTON_TAG_TYPES = (7, 34)
_STATE_NAMES = ("up", "over", "down", "hit")


def _read_record(data: bytes, pos: int, tag_type: int):
    if pos >= len(data):
        return None, pos
    flags = data[pos]
    pos += 1
    if flags == 0:
        return None, pos
    if pos + 4 > len(data):
        raise EOFError("truncated ButtonRecord")
    character_id = int.from_bytes(data[pos:pos + 2], "little")
    depth = int.from_bytes(data[pos + 2:pos + 4], "little")
    pos += 4
    stream = BitStream(data[pos:])
    matrix = shapes._read_matrix(stream)
    pos += stream.byte_offset
    record = {
        "flags": flags,
        "character_id": character_id,
        "depth": depth,
        "matrix": matrix,
        "states": [],
        "has_filter_list": bool(tag_type == 34 and flags & 0x08),
        "has_blend_mode": bool(tag_type == 34 and flags & 0x04),
    }
    if tag_type == 7:
        bits = (0x01, 0x02, 0x04, 0x08)
    else:
        bits = (0x10, 0x20, 0x40, 0x80)
    record["states"] = [name for name, bit in zip(_STATE_NAMES, bits) if flags & bit]
    # FilterList and BlendMode are optional extensions in ButtonRecord2. Keep
    # their bytes as opaque data for now instead of desynchronising the parser.
    if tag_type == 34 and record["has_filter_list"]:
        if pos >= len(data):
            raise EOFError("truncated ButtonFilterList")
        filter_count = data[pos]
        pos += 1
        for _ in range(filter_count):
            if pos >= len(data):
                raise EOFError("truncated button filter")
            filter_type = data[pos]
            pos += 1
            lengths = {0: 9 * 4, 1: 4 * 4, 2: 15 * 4, 3: 7 * 4, 4: 13 * 4,
                       5: 9 * 4, 6: 4 * 4, 7: 5 * 4, 8: 1 * 4, 9: 1 * 4}
            size = lengths.get(filter_type)
            if size is None or pos + size > len(data):
                # Unknown filters cannot be safely skipped; preserve the tail.
                record["opaque_filter_data"] = data[pos - 1:]
                pos = len(data)
                break
            pos += size
    if tag_type == 34 and record["has_blend_mode"]:
        if pos >= len(data):
            raise EOFError("truncated button blend mode")
        record["blend_mode"] = data[pos]
        pos += 1
    return record, pos


def parse_button(tag):
    if tag.tag_type not in BUTTON_TAG_TYPES or len(tag.data) < 2:
        return None
    data = tag.data
    character_id = int.from_bytes(data[:2], "little")
    pos = 2
    action_offset = 0
    if tag.tag_type == 34:
        if pos + 3 > len(data):
            return None
        flags = data[pos]
        action_offset = int.from_bytes(data[pos + 1:pos + 3], "little")
        pos += 3
    records = []
    try:
        while pos < len(data):
            record, pos = _read_record(data, pos, tag.tag_type)
            if record is None:
                break
            records.append(record)
    except EOFError as exc:
        return {
            "character_id": character_id,
            "flags": flags if tag.tag_type == 34 else 0,
            "action_offset": action_offset,
            "records": records,
            "actions": "",
            "parse_error": str(exc),
        }
    actions = ""
    if tag.tag_type == 34 and pos < len(data):
        action_bytes = data[pos:]
        try:
            actions = disassemble_avm1(action_bytes)
        except Exception:
            actions = action_bytes.hex(" ")
    return {
        "character_id": character_id,
        "flags": flags if tag.tag_type == 34 else 0,
        "action_offset": action_offset,
        "records": records,
        "actions": actions,
        "parse_error": None,
    }


def state_records(button: dict, state: str = "up"):
    if state not in _STATE_NAMES:
        raise ValueError(f"Unknown button state: {state}")
    return [record for record in button["records"] if state in record["states"]]


def state_character_ids(button: dict, state: str = "up"):
    return [record["character_id"] for record in state_records(button, state)]
