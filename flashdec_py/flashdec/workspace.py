"""Locate the PRO FLASH DESCOMPILER workspace and make PySWF importable."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def find_workspace() -> Path | None:
    if os.environ.get("FLASHDEC_WORKSPACE"):
        return Path(os.environ["FLASHDEC_WORKSPACE"])
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "py_swf_editor" / "py_swf").is_dir():
            return parent
    return None


def import_pyswf() -> None:
    try:
        import py_swf  # noqa: F401
        return
    except ImportError:
        pass
    root = find_workspace()
    if root is None:
        raise ImportError("PySWF not found: set FLASHDEC_WORKSPACE or install py_swf_editor")
    sys.path.insert(0, str(root / "py_swf_editor"))
