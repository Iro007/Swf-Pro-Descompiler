import hmac
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routers import benchmarks, export, files
from py_swf.plugins import get_plugin_api_router

app = FastAPI(title="py_swf_editor", version="0.1.0")

_origins = [item.strip() for item in os.environ.get(
    "PY_SWF_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",") if item.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def optional_api_auth(request, call_next):
    """Protect hosted API deployments without imposing local setup."""
    expected = os.environ.get("PY_SWF_API_TOKEN")
    if expected and request.url.path.startswith("/api/"):
        supplied = request.headers.get("X-PySWF-Token", "")
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)

app.include_router(files.router)
app.include_router(export.router)
app.include_router(benchmarks.router)
app.include_router(get_plugin_api_router())

# Serve the built web UI when available (web/dist after `npm run build`)
_web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _web_dist.is_dir():
    app.mount("/", StaticFiles(directory=_web_dist, html=True), name="web")
