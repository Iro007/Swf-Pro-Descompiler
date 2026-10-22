"""Runtime limits for local and hosted deployments."""

from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(1, value)


MAX_UPLOAD_BYTES = _int_env("PY_SWF_MAX_UPLOAD_BYTES", 100 * 1024 * 1024)
MAX_RAW_TAG_BYTES = _int_env("PY_SWF_MAX_RAW_TAG_BYTES", 100 * 1024 * 1024)


async def read_upload(file, limit=None):
    """Read bounded chunks; every endpoint shares the same upload budget."""
    from fastapi import HTTPException
    limit = MAX_UPLOAD_BYTES if limit is None else limit
    chunks, size = [], 0
    while True:
        chunk = await file.read(min(1024 * 1024, limit - size + 1))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail=f"Upload exceeds the {limit} byte limit")
        chunks.append(chunk)
