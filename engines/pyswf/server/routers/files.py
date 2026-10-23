from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pathlib import Path
import tempfile
import hashlib

from py_swf.swf_parser import collect_symbol_names
from py_swf.project import SwfProject
from ..sessions import registry
from ..config import MAX_RAW_TAG_BYTES, MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/files", tags=["files"])

TWIPS = 20.0

class FileInfo(BaseModel):
    session_id: str
    filename: str
    signature: str
    version: int
    frame_rate: float
    frame_count: int
    width: float
    height: float
    tag_count: int
    dirty: bool = False

class TagInfo(BaseModel):
    index: int
    code: int
    name: str
    size: int
    char_id: int | None = None
    symbol_name: str | None = None
    parse_error: str | None = None


class SnapshotRequest(BaseModel):
    name: str

class ClassSourceRequest(BaseModel):
    source: str = Field(min_length=1,max_length=1_000_000)
    filename: str = Field(default="program.swf",min_length=1,max_length=255)
    compression: str = "CWS"

def get_session(sid):
    try:
        return registry.get(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session id")

def file_info(sid, session):
    swf = session.swf
    rect = swf.rect
    return FileInfo(
        session_id=sid,
        filename=session.filename,
        signature=swf.signature,
        version=swf.version,
        frame_rate=swf.frame_rate,
        frame_count=swf.frame_count,
        width=(rect["xmax"] - rect["xmin"]) / TWIPS,
        height=(rect["ymax"] - rect["ymin"]) / TWIPS,
        tag_count=len(swf.tags),
        dirty=session.dirty,
    )

@router.post("/from-source",response_model=FileInfo)
def create_from_class_source(req: ClassSourceRequest):
    from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
    from py_swf.class_documents import program_record
    from py_swf.validation import validate_swf_bytes
    try:
        compiled=compile_class_program(req.source)
        data=class_program_swf(compiled,req.compression)
        validation=validate_swf_bytes(data)
        if not validation["valid"]:
            raise ValueError("Generated SWF did not validate")
        filename=Path(req.filename).name
        if not filename.lower().endswith(".swf"):
            filename+=".swf"
        sid=registry.open(filename,data)
        session=registry.get(sid)
        session.source_documents["tag:1/script:0"]=program_record(compiled,session.swf.tags[1],1,session.swf.save_bytes())
        return file_info(sid,session)
    except (ValueError,SyntaxError,UnicodeError,RecursionError) as error:
        raise HTTPException(status_code=422,detail=f"Native class compilation failed: {error}")

@router.post("", response_model=FileInfo)
async def upload_file(file: UploadFile):
    from server.config import read_upload
    data = await read_upload(file)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"SWF exceeds the {MAX_UPLOAD_BYTES} byte upload limit")
    try:
        sid = registry.open(file.filename or "untitled.swf", data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse SWF: {exc}")
    return file_info(sid, registry.get(sid))


@router.post("/projects", response_model=FileInfo)
async def upload_project(file: UploadFile):
    """Open a .pyswfproj archive using its working SWF."""
    from server.config import read_upload
    data = await read_upload(file)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Project exceeds the {MAX_UPLOAD_BYTES} byte upload limit")
    with tempfile.TemporaryDirectory(prefix="py-swf-project-") as temp:
        path = Path(temp) / "project.pyswfproj"
        path.write_bytes(data)
        try:
            project = SwfProject.from_archive(path)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid project: {exc}")
    sid = registry.open(project.filename, project.working)
    session = registry.get(sid)
    session.original_bytes = project.original
    original = session.swf.__class__()
    try:
        original.read_bytes(project.original)
        session._original_canonical = original.save_bytes()
    except (ValueError, EOFError):
        # A repaired project can preserve an unparseable original for recovery.
        session._original_canonical = project.original
    session.dirty = session.swf.save_bytes() != session._original_canonical
    session._snapshots = {name: snapshot.data for name, snapshot in project.snapshots.items()}
    session.history = list(project.history)
    session.source_documents = dict(project.source_documents)
    session.suite_metadata = dict(project.suite_metadata)
    session.toolchains = dict(project.toolchains)
    return file_info(sid, session)

@router.get("/{sid}", response_model=FileInfo)
def get_file(sid: str):
    return file_info(sid, get_session(sid))

@router.get("/{sid}/tags", response_model=list[TagInfo])
def list_tags(sid: str):
    session = get_session(sid)
    symbol_names = collect_symbol_names(session.swf.tags)
    return [
        TagInfo(
            index=i,
            code=tag.tag_type,
            name=tag.name,
            size=len(tag.data),
            char_id=tag.char_id,
            symbol_name=symbol_names.get(tag.char_id) if tag.char_id is not None else None,
            parse_error=tag.parse_error,
        )
        for i, tag in enumerate(session.swf.tags)
    ]

def get_tag(session, index):
    tags = session.swf.tags
    if index < 0 or index >= len(tags):
        raise HTTPException(status_code=404, detail="Tag index out of range")
    return tags[index]

@router.get("/{sid}/tags/{index}/raw")
def tag_raw(sid: str, index: int, offset: int = 0, length: int | None = None):
    tag = get_tag(get_session(sid), index)
    data = tag.data[offset : offset + length if length is not None else None]
    return Response(content=data, media_type="application/octet-stream")

@router.get("/{sid}/download")
def download(sid: str):
    session = get_session(sid)
    data = session.swf.save_bytes()
    validation = SwfProject(filename=session.filename, original=session.original_bytes, working=data).validate()
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail=f"Refusing invalid SWF export: {validation['error']}")
    return Response(
        content=data,
        media_type="application/x-shockwave-flash",
        headers={"Content-Disposition": f'attachment; filename="{session.filename}"'},
    )


@router.get("/{sid}/history")
def history(sid: str):
    session = get_session(sid)
    return {
        "undo": len(session._undo),
        "redo": len(session._redo),
        "snapshots": sorted(session._snapshots),
        "entries": session.history[-100:],
        "dirty": session.dirty,
    }


@router.post("/{sid}/undo")
def undo(sid: str):
    session = get_session(sid)
    if not session.undo():
        raise HTTPException(status_code=409, detail="Nothing to undo")
    return {"ok": True, "dirty": session.dirty}


@router.post("/{sid}/redo")
def redo(sid: str):
    session = get_session(sid)
    if not session.redo():
        raise HTTPException(status_code=409, detail="Nothing to redo")
    return {"ok": True, "dirty": session.dirty}


@router.post("/{sid}/snapshot")
def create_snapshot(sid: str, request: SnapshotRequest):
    session = get_session(sid)
    try:
        name = session.snapshot(request.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "name": name}


@router.post("/{sid}/snapshot/restore")
def restore_snapshot(sid: str, request: SnapshotRequest):
    session = get_session(sid)
    try:
        session.restore_snapshot(request.name)
    except KeyError:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {"ok": True, "dirty": session.dirty}


@router.get("/{sid}/snapshot/compare")
def compare_snapshots(sid: str, left: str, right: str | None = None):
    """Compare snapshot contents without changing the current working SWF."""
    session = get_session(sid)
    with session._lock:
        if left not in session._snapshots or (right is not None and right not in session._snapshots):
            raise HTTPException(status_code=404, detail="Snapshot not found")
        before_bytes = session._snapshots[left]
        after_bytes = session._snapshots[right] if right is not None else session.swf.save_bytes()
    before = session.swf.__class__()
    after = session.swf.__class__()
    before.read_bytes(before_bytes)
    after.read_bytes(after_bytes)
    changed = []
    for index in range(max(len(before.tags), len(after.tags))):
        old = before.tags[index] if index < len(before.tags) else None
        new = after.tags[index] if index < len(after.tags) else None
        if old is not None and new is not None and old.tag_type == new.tag_type and old.data == new.data:
            continue
        changed.append({
            "index": index, "name": (new or old).name,
            "change": "added" if old is None else "removed" if new is None else "modified",
            "before_size": len(old.data) if old else 0,
            "after_size": len(new.data) if new else 0,
        })
    header_changed = any(getattr(before, key) != getattr(after, key) for key in
                         ("signature", "version", "rect", "frame_rate", "frame_count"))
    return {
        "left": left, "right": right,
        "identical": before.save_bytes() == after.save_bytes(),
        "header_changed": header_changed, "changed_tags": changed,
        "before_sha256": hashlib.sha256(before_bytes).hexdigest(),
        "after_sha256": hashlib.sha256(after_bytes).hexdigest(),
    }


@router.get("/{sid}/project")
def download_project(sid: str):
    session = get_session(sid)
    with session._lock:
        import copy
        project = SwfProject(
            filename=session.filename,
            original=session.original_bytes,
            working=session.swf.save_bytes(),
            history=list(session.history),
            engine="native",
            source_documents=copy.deepcopy(session.source_documents),
            suite_metadata=copy.deepcopy(session.suite_metadata),
            toolchains=copy.deepcopy(session.toolchains),
        )
        for name, data in session._snapshots.items():
            project.snapshots[name] = project.snapshot(name)
            project.snapshots[name].data = data
    with tempfile.TemporaryDirectory(prefix="py-swf-project-") as temp:
        try:
            path = project.to_archive(Path(temp) / f"{Path(session.filename).stem}.pyswfproj")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        data = path.read_bytes()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{Path(session.filename).stem}.pyswfproj"'},
    )

@router.get("/{sid}/search")
def search_file(sid: str, q: str = ""):
    session = get_session(sid)
    query = q.strip().lower()
    if not query:
        return {"results": []}

    results = []
    symbol_names = collect_symbol_names(session.swf.tags)

    for i, tag in enumerate(session.swf.tags):
        tag_name = tag.name.lower()
        symbol_name = (symbol_names.get(tag.char_id) or "").lower() if tag.char_id else ""

        if query in tag_name or (symbol_name and query in symbol_name):
            results.append({
                "index": i,
                "code": tag.tag_type,
                "name": tag.name,
                "match_type": "symbol" if symbol_name and query in symbol_name else "tag",
                "snippet": symbol_names.get(tag.char_id) or tag.name,
                "char_id": tag.char_id,
            })
            continue

        if tag.tag_type == 82:
            try:
                parsed = tag.parse_doabc()
                if parsed:
                    _, _, abc_bytes = parsed
                    from py_swf.avm2 import ABCFile
                    abc = ABCFile()
                    abc.parse(abc_bytes)
                    for s in abc.constant_pool.strings:
                        if query in s.lower():
                            results.append({
                                "index": i,
                                "code": tag.tag_type,
                                "name": tag.name,
                                "match_type": "string",
                                "snippet": s[:80],
                                "char_id": tag.char_id,
                            })
                            break
            except Exception:
                pass
        elif tag.tag_type in (12, 59):
            try:
                from py_swf.avm1 import disassemble_avm1
                offset = 2 if tag.tag_type == 59 else 0
                disasm = disassemble_avm1(tag.data[offset:])
                if query in disasm.lower():
                    results.append({
                        "index": i,
                        "code": tag.tag_type,
                        "name": tag.name,
                        "match_type": "script",
                        "snippet": f"Script match in {tag.name}",
                        "char_id": tag.char_id,
                    })
            except Exception:
                pass
        elif tag.tag_type == 37:
            try:
                from py_swf.text_fonts import parse_edit_text
                parsed_text = parse_edit_text(tag)
                if parsed_text:
                    txt = parsed_text.get("initial_text", "")
                    var = parsed_text.get("variable_name", "")
                    if (txt and query in txt.lower()) or (var and query in var.lower()):
                        results.append({
                            "index": i,
                            "code": tag.tag_type,
                            "name": tag.name,
                            "match_type": "text",
                            "snippet": txt[:80] if query in txt.lower() else f"var {var}",
                            "char_id": tag.char_id,
                        })
            except Exception:
                pass

    return {"results": results[:100]}


@router.delete("/{sid}")
def close_file(sid: str):
    registry.close(sid)
    return {"ok": True}
