"""Atomic native SWF file export. Existing input files are never implicit targets."""

import os
from pathlib import Path
import tempfile

from .validation import validate_swf_bytes


def write_swf(path, data, *, original=None, overwrite=False):
    destination = Path(path).resolve()
    if original is not None and destination == Path(original).resolve():
        raise ValueError("Export to a different path; the original SWF is protected")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {destination}; choose another filename")
    validation = validate_swf_bytes(data)
    if not validation["valid"]:
        raise ValueError("Invalid SWF export: " + "; ".join(validation["errors"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".pyswf-export-", suffix=".tmp", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return validation
