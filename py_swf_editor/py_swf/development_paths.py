"""Resolve development artifact paths after the project is moved.

Hashes remain those recorded in the manifest; no download or hash replacement
takes place. Paths under .dev-references always use the current local copy.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path, PureWindowsPath
import os


def resolve_development_path(value, project_root, config_dir=None):
    if not isinstance(value, str) or not value:
        raise ValueError("Development artifact path must be a nonempty string")
    root = Path(project_root).resolve()
    base = Path(config_dir).resolve() if config_dir is not None else root
    portable = PureWindowsPath(value)
    parts = portable.parts
    if ".." in parts:
        raise ValueError("Parent traversal is not allowed in development artifact paths")
    markers = [i for i, part in enumerate(parts) if part.casefold() == ".dev-references"]
    if len(markers) > 1:
        raise ValueError("Ambiguous development artifact path")
    if markers:
        target = (root / ".dev-references" / Path(*parts[markers[0] + 1:])).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Development artifact escapes the project")
        return target
    target = Path(value.replace("\\", "/"))
    if not target.is_absolute():
        if portable.is_absolute():
            raise ValueError("Foreign absolute development artifact path")
        target = base / target
    target = target.resolve()
    # A custom manifest may own artifacts beside itself. The shared workspace
    # also includes the sibling Rust distribution.
    if not (target.is_relative_to(root.parent) or target.is_relative_to(base)):
        raise ValueError("Development artifact is outside the workspace and manifest directory")
    return target


def normalize_development_config(config, project_root, config_dir=None):
    result = deepcopy(config)
    for key in ("java", "javac", "ffdec_jar", "library_jar", "json_jar", "executable", "assembler"):
        if result.get(key):
            result[key] = str(resolve_development_path(result[key], project_root, config_dir))
    if result.get("reference_classpath"):
        classpath = result["reference_classpath"]
        separator = ";" if ";" in classpath else os.pathsep
        entries = classpath.split(separator)
        if any(not entry for entry in entries):
            raise ValueError("Empty development classpath entry")
        result["reference_classpath"] = os.pathsep.join(
            str(resolve_development_path(entry, project_root, config_dir)) for entry in entries
        )
    if "artifact_sha256" in result:
        hashes = {}
        for filename, expected in result["artifact_sha256"].items():
            path = str(resolve_development_path(filename, project_root, config_dir))
            if path in hashes and hashes[path] != expected:
                raise ValueError("Conflicting development artifact hashes after relocation")
            hashes[path] = expected
        result["artifact_sha256"] = hashes
    return result
