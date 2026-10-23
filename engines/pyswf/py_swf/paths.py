"""Single source of truth for where things live in the Flash suite workspace.

Layout (relative to WORKSPACE):

    engines/   one folder per engine (engines/pyswf is this project)
    corpus/    SWF inputs used by tests and benchmarks
    vendor/    downloaded or installed third-party material, never tracked:
               references/ (pinned tool manifests and tools), toolpacks/,
               installers/, jpexs-source/, third_party/

The workspace is FLASH_SUITE_WORKSPACE when set, otherwise it is derived from
this file (engines/pyswf/py_swf/paths.py -> three levels above the package).
A frozen (PyInstaller) worker keeps resolving relative to its own bundle
location unless the host sets FLASH_SUITE_WORKSPACE, which the app always does.
FLASH_ENGINES_DIR and FLASH_VENDOR_DIR override those two folders.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

_HERE = Path(__file__).resolve()


def _default_workspace():
    if getattr(sys, "frozen", False):
        return _HERE.parents[2]
    return _HERE.parents[3]


WORKSPACE = Path(os.environ.get("FLASH_SUITE_WORKSPACE") or _default_workspace()).resolve()
ENGINES = Path(os.environ.get("FLASH_ENGINES_DIR") or WORKSPACE / "engines")
CORPUS = WORKSPACE / "corpus"
VENDOR = Path(os.environ.get("FLASH_VENDOR_DIR") or WORKSPACE / "vendor")
REFERENCES = VENDOR / "references"
TOOLPACKS = VENDOR / "toolpacks"
PROJECT = ENGINES / "pyswf"
if not PROJECT.is_dir() and not getattr(sys, "frozen", False):
    PROJECT = _HERE.parents[1]
