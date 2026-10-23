"""Nested inventory is byte-exact and opt-in; source and editing stay explicit."""
import hashlib
import json
from pathlib import Path

import pytest

from py_swf.benchmark_rust_worker import map_inventory
from py_swf.benchmark_worker import decompile
from py_swf.script_inventory import enumerate_scripts
from py_swf.swf_parser import SWFTag, decompress_body
from py_swf.unit_editing import edit_unit
from tests.conftest import make_synthetic_swf
from tests.test_rust_benchmark_worker import native_metadata


def action(opcode, payload, body=b""):
    return bytes([opcode]) + len(payload).to_bytes(2, "little") + payload + body


def function(name, body, *, function2=False):
    payload = name.encode() + b"\0" + (1 if function2 else 0).to_bytes(2, "little")
    if function2:
        payload += b"\x03\x00\x01\x02param\0"  # 3 registers, flags 0x100, parameter r2
    payload += len(body).to_bytes(2, "little")
    return action(0x8e if function2 else 0x9b, payload, body)


def pool(value):
    return action(0x88, b"\x01\0" + value.encode() + b"\0")


def normalized(data):
    return data[:8] + decompress_body(data[:3].decode(), data[8:], int.from_bytes(data[4:8], "little"))


@pytest.mark.parametrize("signature", ["FWS", "CWS", "ZWS"])
def test_exact_nested_offsets_pools_and_read_only_default(signature):
    leaf = function("leaf", b"\x96\x02\0\x08\0\x3e\0", function2=True)
    outer = function("outer", pool("local") + leaf + b"\0")
    code = pool("captured") + outer + function("sibling", b"\0") + b"\0"
    sprite = SWFTag(39, b"\x01\0\x01\0" + SWFTag(12, code).pack() + SWFTag(0, b"").pack())
    data = make_synthetic_swf([sprite], signature=signature)
    default = list(enumerate_scripts(data))
    units = list(enumerate_scripts(data, include_nested_avm1=True))
    assert len(default) == 1 and default[0].document_id == units[0].document_id
    assert [u.name for u in units[1:]] == ["outer", "leaf", "sibling"]
    assert [u.avm1_context["constants"] for u in units[1:]] == [["captured"], ["local"], ["captured"]]
    assert units[2].avm1_context["parameters"] == ["param"]
    assert units[2].avm1_context["registers"] == [2]
    assert units[2].avm1_context["register_count"] == 3
    assert units[2].avm1_context["flags"] == 0x100
    raw = normalized(data)
    for unit in units:
        start = unit.normalized_byte_start
        assert raw[start:start + len(unit.code)] == unit.code
        assert unit.code == unit.tag.data[unit.byte_start:unit.byte_end]
    for unit in units[1:]:
        assert unit.document_id.endswith(f"/function:{unit.normalized_byte_start:x}")
        assert unit.metadata()["read_only"]
        with pytest.raises(ValueError, match="Unknown or damaged"):
            edit_unit(data, unit.metadata(), "end")
    output = decompile(data, include_source=True)
    children = [u for u in output["units"] if u["kind"] == "function"]
    assert all(u["generation"] == "pcode" and not u["compiler_eligible"] for u in children)


def test_nested_declaration_context_mismatch_and_duplicate_remain_unverified():
    data = make_synthetic_swf([SWFTag(12, pool("actual") + function("f", b"\0") + b"\0")])
    child = list(enumerate_scripts(data, include_nested_avm1=True))[1].metadata()
    context = child["avm1_context"]
    native = {**native_metadata(child), "parameters": context["parameters"],
              "avm1_registers": context["registers"], "avm1_register_count": context["register_count"],
              "avm1_flags": context["flags"], "avm1_constants_sha256": context["constants_sha256"]}
    assert len(map_inventory([child], [native])[0]) == 1
    for field, value in [("avm1_constants_sha256", hashlib.sha256(b"wrong pool").hexdigest()),
                         ("parameters", ["wrong"]), ("avm1_flags", 1), ("byte_start", 0),
                         ("sha256", "0" * 64), ("tag_path", [42])]:
        matched, unmapped, missing = map_inventory([child], [{**native, field: value}])
        assert not matched and len(unmapped) == len(missing) == 1
    assert not map_inventory([child], [native, native])[0]


def test_truncated_or_invalid_nested_function_is_a_counted_diagnostic():
    valid = function("bad", b"\0", function2=True)
    for code in [valid[:-1], valid[:2], valid.replace(b"\x02param", b"\x04param")]:
        data = make_synthetic_swf([SWFTag(12, code)])
        units = list(enumerate_scripts(data, include_nested_avm1=True))
        assert not any(u.kind == "function" for u in units)
        assert any(u.diagnostic for u in units)
        assert units[0].code == code


def test_original_long_tag_header_is_not_repacked_for_function_offsets():
    code = function("f", b"\0") + b"\0"
    data = make_synthetic_swf([SWFTag(12, code)])
    ordinary = list(enumerate_scripts(data, include_nested_avm1=True))
    header = ordinary[0].normalized_byte_start - 2
    # A legal six-byte tag header can be used even for a short payload.
    rewritten = bytearray(data[:header] + (12 << 6 | 63).to_bytes(2, "little") + len(code).to_bytes(4, "little") + data[header + 2:])
    rewritten[4:8] = len(rewritten).to_bytes(4, "little")
    units = list(enumerate_scripts(bytes(rewritten), include_nested_avm1=True))
    assert units[0].document_id == ordinary[0].document_id
    assert units[1].code == ordinary[1].code
    assert units[1].normalized_byte_start == ordinary[1].normalized_byte_start + 4
    assert units[1].document_id.endswith(f"/function:{units[1].normalized_byte_start:x}")


def test_branch_skipping_pool_keeps_exact_bytes_but_marks_runtime_context_unproven():
    first, second = pool("first"), pool("second")
    body = function("inner", b"\0") + b"\x96\x02\0\x08\0\x3e\0"
    branch = action(0x9d, len(second).to_bytes(2, "little", signed=True))
    code = first + action(0x96, b"\x05\x01") + branch + second + function("f", body) + b"\0"
    data = make_synthetic_swf([SWFTag(12, code)])
    units = list(enumerate_scripts(data, include_nested_avm1=True))
    assert len(units) == 3
    for child in units[1:]:
        assert child.avm1_context["constants"] == ["second"]  # syntactic lineage only
        assert child.avm1_context["pool_runtime_verified"] is False
        assert child.diagnostic is None  # byte identity remains valid
        start = child.normalized_byte_start
        assert normalized(data)[start:start + len(child.code)] == child.code
    assert list(enumerate_scripts(data))[0].code == code


@pytest.mark.parametrize("two_pools", [False, True])
def test_entry_pool_before_loop_and_two_straight_line_pools_are_verified(two_pools):
    before = pool("first") + (pool("second") if two_pools else b"")
    fn = function("f", b"\x96\x02\0\x08\0\x3e\0")
    code = before + fn
    if not two_pools:
        code += action(0x99, (-len(fn) - 5).to_bytes(2, "little", signed=True))
    data = make_synthetic_swf([SWFTag(12, code + b"\0")])
    child = list(enumerate_scripts(data, include_nested_avm1=True))[1]
    assert child.avm1_context["pool_runtime_verified"] is True
    assert child.avm1_context["constants"] == ["second" if two_pools else "first"]


@pytest.mark.parametrize("name,containers,functions", [
    ("timeline_function_def", 6, 2), ("closure_scope", 6, 2), ("try_catch_finally", 1, 8),
])
def test_real_matched_corpus_functions_are_byte_exact(name, containers, functions):
    from py_swf import paths
    path = paths.CORPUS / "cloud-matched-micro-20261013" / f"ruffle_avm1_{name}.swf"
    if not path.is_file():
        pytest.skip("Matched Ruffle corpus is not available")
    data = path.read_bytes()
    units = list(enumerate_scripts(data, include_nested_avm1=True))
    assert len(list(enumerate_scripts(data))) == containers
    assert len(units) == containers + functions
    assert sum(u.kind == "function" for u in units) == functions
    assert not any(u.diagnostic for u in units)
    raw = normalized(data)
    for unit in units:
        start = unit.normalized_byte_start
        assert raw[start:start + len(unit.code)] == unit.code
        assert hashlib.sha256(unit.code).hexdigest() == unit.metadata()["bytecode_sha256"]
    assert len({u.document_id for u in units}) == len(units)
