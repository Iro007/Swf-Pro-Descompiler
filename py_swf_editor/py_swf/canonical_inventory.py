"""Byte-derived benchmark identities, independent of source reconstruction.

Offsets use the original decompressed SWF layout (including the eight-byte
header). ABC decoding here deliberately does not use ABCFile or native method
names: a second parser can disagree without silently changing the denominator.
Class/script coverage and method/action coverage always have separate scopes.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json

from .script_inventory import Cursor, embedded_actions, _nested_avm1_functions, ScriptUnit
from .swf_parser import BitStream, SWFTag, decompress_body, parse_rect

SCOPES = ("method", "action", "function", "class", "script", "diagnostic")
OFFSET_BASIS = "decompressed_swf_including_8_byte_header"


def digest(value):
    return hashlib.sha256(value).hexdigest()


class LayoutReader(Cursor):
    def u30(self):
        value = 0
        for shift in range(0, 35, 7):
            byte = self.uint(1)
            if shift == 28 and byte & 0xf0:
                raise ValueError("ABC variable integer exceeds 32 bits")
            value |= (byte & 0x7f) << shift
            if not byte & 0x80:
                return value
        raise ValueError("ABC unterminated variable integer")

    def count(self):
        value = self.u30()
        if value > 100000:
            raise ValueError("ABC inventory count limit exceeded")
        return value


def abc_layout(data):
    """Read structural ownership and precise body spans without decompilation."""
    r = LayoutReader(data)
    minor, major = r.uint(2), r.uint(2)
    if (major, minor) not in ((46, 16), (46, 17), (47, 16)):
        raise ValueError(f"Unsupported ABC layout {major}.{minor}")
    for _ in range(2):
        for _ in range(max(0, r.count() - 1)):
            r.u30()
    r.take(max(0, r.count() - 1) * 8)
    if major == 46 and minor == 17:
        r.take(max(0, r.count() - 1) * 16)
    if major >= 47:
        r.take(max(0, r.count() - 1) * 4)
    strings = [""]
    for _ in range(max(0, r.count() - 1)):
        strings.append(r.take(r.u30()).decode("utf-8", errors="replace"))
    namespaces = [(0, 0)]
    for _ in range(max(0, r.count() - 1)):
        namespaces.append((r.uint(1), r.u30()))
    for _ in range(max(0, r.count() - 1)):
        for _ in range(r.count()):
            r.u30()
    names = [None]
    for _ in range(max(0, r.count() - 1)):
        kind = r.uint(1)
        if kind in (7, 13):
            ns, name = r.u30(), r.u30()
            namespace = strings[namespaces[ns][1]]
            names.append((namespace + "::" if namespace else "") + strings[name])
        elif kind in (15, 16):
            names.append(strings[r.u30()])
        elif kind in (17, 18):
            names.append(None)
        elif kind in (9, 14):
            names.append(strings[r.u30()]); r.u30()
        elif kind in (27, 28):
            names.append(None); r.u30()
        elif kind == 29:
            base = r.u30()
            for _ in range(r.count()):
                r.u30()
            # TypeName may reference a multiname appearing later in the pool.
            # Ownership uses QName class names; retain this structural identity
            # without assuming a topological pool order.
            names.append('typename:' + str(base))
        else:
            raise ValueError(f"Unknown ABC multiname kind {kind}")
    method_count = r.count()
    for _ in range(method_count):
        count = r.count(); r.u30()
        for _ in range(count):
            r.u30()
        r.u30(); flags = r.uint(1)
        if flags & 8:
            for _ in range(r.count()):
                r.u30(); r.uint(1)
        if flags & 128:
            for _ in range(count):
                r.u30()
    for _ in range(r.count()):
        r.u30()
        for _ in range(r.count()):
            r.u30(); r.u30()

    def traits():
        methods, classes = [], []
        for _ in range(r.count()):
            name, flags = r.u30(), r.uint(1)
            kind = flags & 15
            if kind in (0, 6):
                r.u30(); r.u30()
                if r.u30():
                    r.uint(1)
            elif kind in (1, 2, 3, 5):
                r.u30(); methods.append(r.u30())
            elif kind == 4:
                r.u30(); classes.append(r.u30())
            else:
                raise ValueError(f"Unknown ABC trait kind {kind}")
            if flags & 64:
                for _ in range(r.count()):
                    r.u30()
        return methods, classes

    classes = []
    for index in range(r.count()):
        name = r.u30(); r.u30(); flags = r.uint(1)
        if flags & 8:
            r.u30()
        for _ in range(r.count()):
            r.u30()
        initializer = r.u30(); members, _ = traits()
        classes.append({"index": index, "name": names[name], "methods": [initializer, *members]})
    for item in classes:
        initializer = r.u30(); members, _ = traits()
        item["methods"].extend([initializer, *members])
    scripts = []
    for index in range(r.count()):
        initializer = r.u30(); members, class_indices = traits()
        scripts.append({"index": index, "methods": [initializer, *members], "classes": class_indices})
    if any(method >= method_count for item in [*classes,*scripts] for method in item['methods']):
        raise ValueError('ABC owner refers to a missing method')
    if any(index >= len(classes) for item in scripts for index in item['classes']):
        raise ValueError('ABC script refers to a missing class')
    bodies = []
    for index in range(r.count()):
        method = r.u30()
        limits = [r.u30() for _ in range(4)]
        length = r.u30(); start = r.pos
        code = r.take(length)
        exceptions = [[r.u30() for _ in range(5)] for _ in range(r.count())]
        traits()
        if method >= method_count:
            raise ValueError("ABC method body index exceeds method table")
        bodies.append({"body_index": index, "method_index": method, "start": start,
                       "code": code, "limits": limits, "exceptions": exceptions})
    if r.pos != len(data):
        raise ValueError("Trailing bytes after ABC inventory")
    if len({b["method_index"] for b in bodies}) != len(bodies):
        raise ValueError("Duplicate ABC method bodies")
    return {"major": major, "minor": minor, "classes": classes, "scripts": scripts, "bodies": bodies}


def build_inventory(data, *, max_depth=32, max_units=100000):
    input_hash, units, blocks, diagnostics = digest(data), [], [], []

    def add(document_id, path, language, scope, code=b"", offset=None, **extra):
        if len(units) >= max_units:
            raise ValueError("Canonical inventory unit limit exceeded")
        record = {"unit_id": input_hash + ":" + document_id, "document_id": document_id,
                  "input_sha256": input_hash, "tag_path": list(path), "language": language,
                  "scope": scope, "parent_unit_id": None, "abc_sha256": None,
                  "method_index": None, "body_index": None, "bytecode_sha256": digest(code),
                  "bytecode_bytes": len(code), "bytecode_offset": offset,
                  "offset_basis": OFFSET_BASIS, "context": {}, "identity_status": "byte_derived", **extra}
        units.append(record)
        return record

    def walk(payload, base, parent=()):
        if len(parent) > max_depth:
            raise ValueError("Canonical SWF nesting limit exceeded")
        r, ordinal = Cursor(payload), 0
        while r.pos < len(payload):
            header = r.uint(2); kind, length = header >> 6, header & 63
            if length == 63:
                length = r.uint(4)
            offset = base + r.pos; value = r.take(length)
            path = (*parent, ordinal); prefix = "tag:" + "/".join(map(str, path))
            ordinal += 1
            try:
                if kind == 39:
                    if len(value) < 4:
                        raise ValueError("Truncated sprite header")
                    walk(value[4:], offset + 4, path)
                elif kind in (72, 82):
                    cursor = Cursor(value)
                    if kind == 82:
                        cursor.take(4); cursor.string()
                    abc = value[cursor.pos:]; layout = abc_layout(abc); abc_hash = digest(abc)
                    blocks.append({"tag_path": list(path), "abc_index": len(blocks), "abc_sha256": abc_hash,
                                   "byte_offset": offset + cursor.pos, "bytes": len(abc),
                                   "script_class_indices": {str(s['index']):s['classes'] for s in layout['scripts']}})
                    by_method = {b["method_index"]: b for b in layout["bodies"]}
                    owners = {}
                    for scope, collection in (("class", layout["classes"]), ("script", layout["scripts"])):
                        for owner in collection:
                            members = sorted(set(owner["methods"]))
                            owned = [by_method[m] for m in members if m in by_method]
                            # Aggregate spans are length framed, never a source hash.
                            aggregate = b"".join(len(b["code"]).to_bytes(4, "little") + b["code"] for b in owned)
                            parent_record = add(prefix + f"/{scope}:{owner['index']}", path, "as3", scope, aggregate,
                                abc_sha256=abc_hash, class_index=owner["index"] if scope == "class" else None,
                                script_index=owner["index"] if scope == "script" else None,
                                name=owner.get("name"), context={"method_indices": members, "body_indices": [b["body_index"] for b in owned],
                                                            "aggregate_hash_basis": "u32_length_framed_method_bytes"})
                            for method in members:
                                owners.setdefault(method, []).append(parent_record["unit_id"])
                    for body in layout["bodies"]:
                        context = {"limits": body["limits"], "exceptions": body["exceptions"],
                                   "owner_unit_ids": owners.get(body["method_index"], [])}
                        add(prefix + f"/method:{body['method_index']}", path, "as3", "method", body["code"],
                            offset + cursor.pos + body["start"], abc_sha256=abc_hash,
                            method_index=body["method_index"], body_index=body["body_index"], context=context,
                            parent_unit_id=context["owner_unit_ids"][0] if len(context["owner_unit_ids"]) == 1 else None)
                elif kind in (12, 59, 7, 34, 26, 70, 94):
                    tag = SWFTag(kind, value)
                    containers = [("actions", 0, 2 if kind == 59 else 0, len(value), None)] if kind in (12, 59) else embedded_actions(tag, data[3])
                    for container, index, start, end, event in containers:
                        doc = prefix + ("/actions" if container == "actions" else f"/{container}:{index}")
                        record = add(doc, path, "as2", "action", value[start:end], offset + start,
                                     context={"event_flags": event, "container": container})
                        structural = ScriptUnit(doc, path, "as2", container, doc, value[start:end], tag,
                                                byte_start=start, byte_end=end, event_flags=event)
                        for child in _nested_avm1_functions(structural, offset):
                            add(child.document_id, path, "as2", "function", child.code, child.normalized_byte_start,
                                parent_unit_id=input_hash + ':' + child.document_id.rsplit('/function:', 1)[0],
                                context=child.avm1_context or {}, name=child.name)
            except (ValueError, EOFError, IndexError, KeyError, UnicodeError) as error:
                diagnostics.append({"tag_path": list(path), "diagnostic": str(error)})
                add(prefix + "/diagnostic", path, "unknown", "diagnostic", value, offset,
                    identity_status="diagnostic", diagnostic=str(error))
            if kind == 0:
                if r.pos != len(payload):
                    raise ValueError("Data after SWF End tag")
                return

    try:
        if len(data) < 8 or data[:3] not in (b"FWS", b"CWS", b"ZWS"):
            raise ValueError("Invalid SWF header")
        body = decompress_body(data[:3].decode("ascii"), data[8:], int.from_bytes(data[4:8], "little"))
        stream = BitStream(body); parse_rect(stream)
        start = stream.byte_offset + 4
        walk(body[start:], start + 8)
    except (ValueError, EOFError, IndexError, KeyError, UnicodeError) as error:
        diagnostics.append({"tag_path": [], "diagnostic": str(error)})
        add("container/diagnostic", (), "unknown", "diagnostic", data, 0,
            identity_status="diagnostic", diagnostic=str(error))
    encoded = json.dumps(units, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return {"schema_version": 1, "sha256": digest(encoded), "sha256_basis": 'byte-derived units before code-origin annotations', "input_sha256": input_hash,
            "offset_basis": OFFSET_BASIS, "units": units, "abc_blocks": blocks,
            "scope_denominators": dict(Counter(u["scope"] for u in units)), "diagnostics": diagnostics,
            "status": "partial" if diagnostics else "byte_derived", "cross_validation": "not_run"}


def attach_observations(inventory, observations, *, engine=None, adapter_mode=None):
    """Attach only unique exact hashes; preserve disagreeing output as unknown."""
    canonical = {u["document_id"]: u for u in inventory["units"]}
    counts = Counter(u.get("document_id") for u in observations)
    output = []
    for observation in observations:
        document_id = observation.get("document_id")
        unit = canonical.get(document_id)
        verified = (unit is not None and counts[document_id] == 1
                    and observation.get("identity_verified") is not False
                    and observation.get("bytecode_sha256") == unit["bytecode_sha256"]
                    and observation.get("bytecode_bytes", unit["bytecode_bytes"]) == unit["bytecode_bytes"]
                    and all(observation.get(key, unit.get(key)) == unit.get(key)
                            for key in ("tag_path", "abc_sha256", "method_index", "body_index", "scope"))
                    and unit["identity_status"] != "diagnostic")
        result = {**(unit or {}), **observation, "identity_verified": verified,
                  "inventory_status": "verified" if verified else "disagreement",
                  "adapter_mode": adapter_mode, "engine": engine,
                  "output_sha256": observation.get("source_sha256"),
                  "quality_dimensions": {key: observation.get(key, "not_run" if key in ("compilation", "behavior") else "not_verified")
                                         for key in ("syntax", "compilation", "swf_validation", "behavior", "source_fidelity")}}
        result['quality_dimensions'].update(identity_mapping='verified' if verified else 'disagreement',
                                             source_generation=observation.get('generation','unknown'),
                                             container_parse=unit.get('identity_status','unknown') if unit else 'unknown')
        if not verified:
            result.pop("unit_id", None)
        output.append(result)
    return output


def cross_validate(inventory, external_units):
    """Compare the independent byte walk with an independent engine inventory."""
    expected = [u for u in inventory["units"] if u["scope"] in ("method", "action", "function")]
    normalized = []
    for unit in external_units:
        normalized.append({**unit, "document_id": unit.get("document_id", unit.get("id")),
                           "bytecode_sha256": unit.get("bytecode_sha256", unit.get("sha256")),
                           "bytecode_bytes": unit.get("bytecode_bytes", unit.get("byte_len"))})
    attached = attach_observations(inventory, normalized)
    expected_by_id = {u['document_id']: u for u in expected}
    external_by_id = {u['document_id']: u for u in normalized}
    for observed in attached:
        canonical = expected_by_id.get(observed.get('document_id'))
        if canonical is None:
            continue
        external = external_by_id[observed['document_id']]
        language = {'avm1': 'as2', 'avm2': 'as3'}.get(external.get('language'), external.get('language'))
        if language != canonical['language']:
            observed['identity_verified'] = False
        if canonical['scope'] == 'function':
            context = canonical['context']
            fields = {'parameters': 'parameters', 'registers': 'avm1_registers',
                      'register_count': 'avm1_register_count', 'flags': 'avm1_flags',
                      'constants_sha256': 'avm1_constants_sha256'}
            if external.get('byte_start') != canonical['bytecode_offset'] or any(
                    external.get(destination) != context.get(source) for source, destination in fields.items()):
                observed['identity_verified'] = False
    verified = {u.get("document_id") for u in attached if u["identity_verified"]}
    missing = sorted(u["document_id"] for u in expected if u["document_id"] not in verified)
    discordant = [u.get("document_id") for u in attached if not u["identity_verified"]]
    return {"status": "verified" if not (missing or discordant or inventory["diagnostics"]) else "disagreement",
            "verified_units": len(verified), "missing_units": missing, "discordant_units": discordant}
