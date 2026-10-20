"""Browse existing benchmark snapshots. Never launch a decompiler from HTTP."""
import json
from pathlib import Path
import re
from functools import lru_cache
from threading import Lock

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from py_swf.benchmark_report import public_report, render_dashboard
from py_swf.benchmark_overview import build_overview, render_overview

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])
ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports"
BUNDLED = ROOT / "benchmark_data" / "benchmark.json"
MAX_REPORT_BYTES = 32 * 1024**2
_report_lock=Lock()


def _paths():
    paths = {}
    if REPORT_ROOT.is_dir():
        base = REPORT_ROOT.resolve()
        for folder in sorted(REPORT_ROOT.iterdir()):
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", folder.name):
                continue
            path = (folder / "benchmark.json").resolve()
            if path.is_relative_to(base) and path.is_file():
                paths[folder.name] = path
    if BUNDLED.is_file():
        paths["bundled"] = BUNDLED
        for report_id in ("disassembly","remote","audit"):
            additional=BUNDLED.parent / report_id / "benchmark.json"
            if additional.is_file():
                paths["bundled-"+report_id]=additional
    return paths


@lru_cache(maxsize=24)
def _cached_report(filename,mtime_ns,size):
    report=json.loads(Path(filename).read_text(encoding="utf-8"))
    if report.get("schema_version") != 1 or not isinstance(report.get("cases"),list):
        raise ValueError("Unsupported report schema")
    # HTTP surfaces only need summaries, not thousands of bytecode/source units.
    return public_report(report)


def _load(path):
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("Report exceeds the dashboard size limit")
    stat=path.stat()
    with _report_lock:
        return _cached_report(str(path.resolve()),stat.st_mtime_ns,stat.st_size)


def _report(report_id):
    path = _paths().get(report_id)
    if path is None:
        raise HTTPException(404, "Benchmark report not found")
    try:
        return _load(path)
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(422, "Benchmark report is unreadable") from error


@router.get("")
def list_reports():
    rows = []
    for report_id, path in _paths().items():
        try:
            report = _load(path)
            rows.append({"id": report_id, "created_at": report.get("created_at", ""),
                         "profile": report.get("profile", "unknown"), "files": len(report["cases"]),
                         "operation": report.get("operation","source"),
                         "suitable_for_comparison": report.get("measurement_context", {}).get("suitable_for_comparison", True)})
        except (OSError, ValueError, TypeError):
            continue
    return {"reports": sorted(rows, key=lambda r: r["created_at"], reverse=True)}


def _overview():
    reports=[]
    for report_id,path in _paths().items():
        try:
            reports.append((report_id,_load(path)))
        except (OSError,ValueError,TypeError):
            continue
    return build_overview(reports)


@router.get("/overview/dashboard", response_class=HTMLResponse)
def comparison_dashboard():
    response=HTMLResponse(render_overview(_overview()))
    response.headers["Cache-Control"]="no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Content-Security-Policy"]="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    return response


@router.get("/overview/summary")
def comparison_summary():
    return JSONResponse(_overview(),headers={"Cache-Control":"no-store"})


@router.get('/catalog')
@router.get("/catalog/summary")
def catalog_summary(campaign: str | None = None, operation: str | None = None,
                    engine: str | None = None, scope: str | None = None,
                    family_id: str | None = None, split: str | None = None):
    from py_swf.universal import benchmark_catalog_snapshot
    try:
        filters = {key: value for key, value in {'campaign': campaign, 'operation': operation,
                   'engine': engine, 'scope': scope, 'family_id': family_id, 'split': split}.items() if value is not None}
        return JSONResponse(benchmark_catalog_snapshot(filters), headers={"Cache-Control": "no-store"})
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(422, "Benchmark catalog is unreadable") from error


@router.get('/tools')
def benchmark_tools():
    from py_swf.benchmark_engines import describe_engines
    return JSONResponse({'engines': describe_engines(), 'scope': 'Read-only adapter inventory'},
                        headers={'Cache-Control': 'no-store'})


@router.get('/catalog/units')
def catalog_units(validation_id: str | None = None, campaign: str | None = None,
                  unit_id: str | None = None, scope: str | None = None,
                  engine: str | None = None, offset: int = 0, limit: int = 50,
                  include_metadata: bool = False):
    from py_swf.universal import benchmark_catalog_details
    options = {key: value for key, value in locals().items() if value is not None and key != 'benchmark_catalog_details'}
    try:
        return JSONResponse(benchmark_catalog_details(options, 'units'), headers={'Cache-Control': 'no-store'})
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(422, 'Invalid unit catalog query') from error


@router.get('/catalog/evidence')
def catalog_evidence(campaign: str | None = None, evidence_id: str | None = None,
                     kind: str | None = None, offset: int = 0, limit: int = 50,
                     include_metadata: bool = False):
    from py_swf.universal import benchmark_catalog_details
    options = {key: value for key, value in locals().items() if value is not None and key != 'benchmark_catalog_details'}
    try:
        return JSONResponse(benchmark_catalog_details(options, 'evidence'), headers={'Cache-Control': 'no-store'})
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(422, 'Invalid evidence catalog query') from error


@router.get("/catalog/dashboard", response_class=HTMLResponse)
def catalog_dashboard():
    from py_swf.benchmark_catalog import render_catalog_html
    from py_swf.universal import benchmark_catalog_snapshot
    try:
        snapshot = benchmark_catalog_snapshot({"limit": 0})
        if not snapshot["available"]:
            raise HTTPException(404, "Benchmark catalog is not imported yet")
        response = HTMLResponse(render_catalog_html(snapshot["database"]))
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(422, "Benchmark catalog is unreadable") from error
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    return response


@router.get("/{report_id}/dashboard", response_class=HTMLResponse)
def dashboard(report_id: str):
    try:
        page = render_dashboard(_report(report_id))
    except (KeyError, ValueError, TypeError) as error:
        raise HTTPException(422, "Benchmark report is incomplete") from error
    response = HTMLResponse(page)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    return response


@router.get("/{report_id}/summary")
def summary(report_id: str):
    return JSONResponse(public_report(_report(report_id)), headers={"Cache-Control": "no-store"})
