"""Optional immutable superclass metadata from the verified local player SDK.

Only declaration QNames, superclass references and constructor arities leave
the parser. No SDK method is executed, linked, or used as a guessed source body.
"""
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import sys
from types import MappingProxyType
import zipfile

from ..abc_symbols import inspect_qname
from ..avm2 import ABCFile
from ..swf_parser import SWFFile

PLAYERGLOBAL_32_SHA256 = "7d4d6168d27603cfb3b750302448e354e0bbc1bdd58f5d101c3dcf6891e9bb65"
VERIFIED_ERROR_NAMES = frozenset(("Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError"))


def type_key(pool, index):
    identity = inspect_qname(pool, index)
    if identity.kind != 7 or identity.namespace_kind not in (8, 0x16, 0x17):
        raise ValueError("Superclass has no statically representable public/internal QName")
    return (0x16 if identity.namespace_kind in (8, 0x16) else 0x17, identity.package, identity.name)


def _playerglobal_path():
    if getattr(sys, "frozen", False):
        # Frozen modules live in _MEI, not beside the suite's development SDK.
        # This is the same explicit workspace selector used by suite workers.
        workspace = os.environ.get("FLASH_SUITE_WORKSPACE")
        if not workspace:
            return None
        root = Path(workspace).resolve()
    else:
        root = Path(__file__).resolve().parents[3]
    return root / "toolpacks/flex/frameworks/libs/player/32.0/playerglobal.swc"


def external_hierarchy():
    path = _playerglobal_path()
    return _external_hierarchy(path) if path is not None else MappingProxyType({})


def external_error_constructors():
    """Exact blank-public Error providers and their SDK-declared arities."""
    path = _playerglobal_path()
    return _external_error_constructors(path) if path is not None else MappingProxyType({})


@lru_cache(maxsize=4)
def _external_hierarchy(path):
    return MappingProxyType({key: (record[0], record[1]) if record is not None else None
                             for key, record in _external_metadata(path).items()})


@lru_cache(maxsize=4)
def _external_error_constructors(path):
    metadata = _external_metadata(path)
    result = {}
    root = (0x16, "", "Object")
    error = (0x16, "", "Error")
    base = metadata.get(error)
    # Deriving a subtype from a same-named or unproven base is insufficient.
    if base is None or base[0] != root or base[1] or base[2] is None:
        return MappingProxyType({})
    for name in sorted(VERIFIED_ERROR_NAMES):
        key = (0x16, "", name)
        record = metadata.get(key)
        if record is not None and record[0] == (root if name == "Error" else error) and not record[1] and record[2] is not None:
            result[key] = record[2]
    return MappingProxyType(result)


@lru_cache(maxsize=4)
def _external_metadata(path):
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != PLAYERGLOBAL_32_SHA256:
            return MappingProxyType({})
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            member = archive.getinfo("library.swf")
            if member.file_size > 32 * 1024 * 1024:
                return MappingProxyType({})
            movie = SWFFile(); movie.read_bytes(archive.read(member), strict=True)
        declarations = {}
        for tag in movie.tags:
            if not tag.is_doabc:
                continue
            abc = ABCFile(); abc.parse(tag.parse_doabc()[2], strict=True)
            for instance in abc.instances:
                try:
                    key = type_key(abc.constant_pool, instance.name)
                    parent = type_key(abc.constant_pool, instance.super_name) if instance.super_name else None
                except ValueError:
                    continue
                constructor = None
                identity = inspect_qname(abc.constant_pool, instance.name)
                if identity.namespace_kind == 0x16 and not identity.package and identity.name in VERIFIED_ERROR_NAMES and not instance.flags & 4:
                    method = abc.methods[instance.iinit]
                    count, optional = len(method.param_types), len(method.options)
                    if optional <= count and method.return_type == 0:
                        constructor = (count - optional, None if method.flags & 4 else count)
                record = (parent, bool(instance.flags & 4), constructor)
                if key in declarations:
                    declarations[key] = None  # duplicate providers are ambiguous
                else:
                    declarations[key] = record
        return MappingProxyType(declarations)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return MappingProxyType({})


def captured_chain(abc, index, class_index):
    """Root-first exact superclass QNames, or a missing-contract error."""
    own = {}
    for declaration in index.declarations:
        provider = index.abcs[declaration.abc_index]
        key = type_key(provider.constant_pool, declaration.multiname_index)
        own.setdefault(key, []).append(declaration)
    instance = abc.instances[class_index]
    current = type_key(abc.constant_pool, instance.super_name) if instance.super_name else None
    seen, nearest = set(), []
    while current is not None:
        if current in seen:
            raise ValueError("Cyclic class capture hierarchy")
        seen.add(current); nearest.append(current)
        matches = own.get(current, [])
        if len(matches) > 1:
            raise ValueError("Ambiguous superclass declaration provider")
        if matches:
            declaration = matches[0]
            provider = index.abcs[declaration.abc_index]
            parent = provider.instances[declaration.class_index]
            if parent.flags & 4:
                raise ValueError("An interface cannot be captured as a superclass")
            current = type_key(provider.constant_pool, parent.super_name) if parent.super_name else None
            if current is None and nearest[-1] != (0x16, "", "Object"):
                raise ValueError("Declared class hierarchy has no proven Object root")
            continue
        if current == (0x16, "", "Object"):
            break  # intrinsic AVM2 root, only in the explicit blank public QName
        record = external_hierarchy().get(current)
        if record is None:
            raise ValueError("Superclass has no verified external hierarchy contract: " + ".".join(filter(None, current[1:])))
        parent, interface = record
        if interface:
            raise ValueError("An external interface cannot be used as superclass")
        current = parent
        if current is None and nearest[-1] != (0x16, "", "Object"):
            raise ValueError("External hierarchy has no proven Object root")
    return tuple(reversed(nearest))
