"""Portable editable project archives for SWF work.

The archive deliberately keeps the original SWF immutable.  The working SWF,
manifest and history are separate entries so a failed export can never erase
the user's source file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import tempfile
import zipfile

from .swf_parser import SWFFile
from .validation import validate_swf_bytes

FORMAT_VERSION = 3
MAX_SOURCE_DOCUMENT_BYTES = 16*1024**2
MAX_PROJECT_BYTES = int(os.environ.get("PY_SWF_MAX_PROJECT_BYTES", 512 * 1024**2))
MAX_PROJECT_ENTRIES = 4096
MAX_MANIFEST_BYTES = 8 * 1024**2


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProjectSnapshot:
    name: str
    data: bytes
    created_at: str = field(default_factory=_now)


@dataclass
class SwfProject:
    filename: str
    original: bytes
    working: bytes
    history: list[dict] = field(default_factory=list)
    snapshots: dict[str, ProjectSnapshot] = field(default_factory=dict)
    engine: str = "native"
    diagnostics: list[dict] = field(default_factory=list)
    source_documents: dict[str,dict] = field(default_factory=dict)
    suite_metadata: dict = field(default_factory=dict)
    toolchains: dict = field(default_factory=dict)

    def validate(self, data: bytes | None = None) -> dict:
        candidate = self.working if data is None else data
        validation = validate_swf_bytes(candidate)
        return {**validation, "error": "; ".join(validation["errors"]) or None, "sha256": _sha256(candidate)}

    def apply(self, new_data: bytes, operation: str, metadata: dict | None = None) -> dict:
        validation = self.validate(new_data)
        if not validation["valid"]:
            raise ValueError(f"Refusing invalid SWF: {validation['error']}")
        if new_data == self.working:
            return validation
        self.history.append({
            "operation": operation,
            "at": _now(),
            "before": _sha256(self.working),
            "after": _sha256(new_data),
            "metadata": metadata or {},
        })
        self.working = new_data
        return validation

    def snapshot(self, name: str) -> ProjectSnapshot:
        if not name.strip():
            raise ValueError("Snapshot name cannot be empty")
        snapshot = ProjectSnapshot(name=name.strip(), data=self.working)
        self.snapshots[snapshot.name] = snapshot
        return snapshot

    def restore_snapshot(self, name: str) -> dict:
        snapshot = self.snapshots.get(name)
        if snapshot is None:
            raise KeyError(name)
        return self.apply(snapshot.data, "restore_snapshot", {"name": name})

    def to_archive(self, path: str | Path) -> Path:
        """Write atomically and validate before replacing an existing archive."""
        validation = self.validate()
        if not validation["valid"]:
            raise ValueError(f"Refusing to archive invalid working SWF: {validation['error']}")
        for snapshot in self.snapshots.values():
            snapshot_validation = self.validate(snapshot.data)
            if not snapshot_validation["valid"]:
                raise ValueError(f"Refusing invalid snapshot {snapshot.name}: {snapshot_validation['error']}")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        documents=json.dumps(self.source_documents,ensure_ascii=False,sort_keys=True,allow_nan=False).encode("utf-8")
        if len(documents)>MAX_SOURCE_DOCUMENT_BYTES:
            raise ValueError("Project source documents exceed the size limit")
        manifest = {
            "format_version": FORMAT_VERSION,
            "filename": self.filename,
            "created_at": _now(),
            "engine": self.engine,
            "original_sha256": _sha256(self.original),
            "working_sha256": _sha256(self.working),
            "history": self.history,
            "snapshots": [
                {"name": s.name, "created_at": s.created_at, "sha256": _sha256(s.data)}
                for s in self.snapshots.values()
            ],
            "diagnostics": self.diagnostics,
            "source_documents_sha256":_sha256(documents),
            "suite_metadata": self.suite_metadata,
            "toolchains": self.toolchains,
        }
        if not isinstance(self.suite_metadata, dict) or not isinstance(self.toolchains, dict):
            raise ValueError("Invalid project suite metadata/toolchains")
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        expanded_size = len(manifest_bytes) + len(documents) + len(self.original) + len(self.working) + sum(len(s.data) for s in self.snapshots.values())
        if len(manifest_bytes) > MAX_MANIFEST_BYTES or expanded_size > MAX_PROJECT_BYTES or len(self.snapshots) + 4 > MAX_PROJECT_ENTRIES:
            raise ValueError("Project exceeds the manifest/expanded-size budget")
        fd, temp_name = tempfile.mkstemp(prefix=".pyswfproj-", suffix=".tmp", dir=destination.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", manifest_bytes)
                archive.writestr("original.swf", self.original)
                archive.writestr("working.swf", self.working)
                archive.writestr("source-documents.json", documents)
                for name, snapshot in self.snapshots.items():
                    safe = hashlib.sha256(name.encode("utf-8")).hexdigest()
                    archive.writestr(f"snapshots/{safe}.swf", snapshot.data)
            os.replace(temp_path, destination)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return destination

    @classmethod
    def from_archive(cls, path: str | Path) -> "SwfProject":
        source = Path(path)
        with zipfile.ZipFile(source, "r") as archive:
            from .swf_parser import MAX_DECOMPRESSED_BYTES
            entries = archive.infolist()
            if len(entries) > MAX_PROJECT_ENTRIES or sum(item.file_size for item in entries) > MAX_PROJECT_BYTES:
                raise ValueError("Project archive exceeds the entry/expanded-size budget")
            if len({item.filename for item in entries}) != len(entries):
                raise ValueError("Project archive contains duplicate entries")
            for item in entries:
                limit = MAX_MANIFEST_BYTES if item.filename == "manifest.json" else MAX_SOURCE_DOCUMENT_BYTES if item.filename == "source-documents.json" else MAX_DECOMPRESSED_BYTES
                if item.file_size > limit:
                    raise ValueError("Project archive entry exceeds the size limit: " + item.filename)
            names = set(archive.namelist())
            required = {"manifest.json", "original.swf", "working.swf"}
            if not required.issubset(names):
                raise ValueError("Invalid .pyswfproj: missing required entries")
            manifest = json.loads(archive.read("manifest.json"))
            version=manifest.get("format_version")
            if version not in (1,2,FORMAT_VERSION):
                raise ValueError("Unsupported project format version")
            documents={}
            if version>=2:
                if "source-documents.json" not in names or archive.getinfo("source-documents.json").file_size>MAX_SOURCE_DOCUMENT_BYTES:
                    raise ValueError("Project source documents missing or oversized")
                raw_documents=archive.read("source-documents.json")
                if _sha256(raw_documents)!=manifest.get("source_documents_sha256"):
                    raise ValueError("Project source document checksum mismatch")
                documents=json.loads(raw_documents)
                if not isinstance(documents,dict) or any(not isinstance(k,str) or not isinstance(v,dict) for k,v in documents.items()):
                    raise ValueError("Invalid project source documents")
                if any("source" in item and not isinstance(item["source"],str) for item in documents.values()):
                    raise ValueError("Invalid saved source text")
            original = archive.read("original.swf")
            working = archive.read("working.swf")
            project = cls(
                filename=manifest.get("filename", "untitled.swf"),
                original=original,
                working=working,
                history=manifest.get("history", []),
                engine=manifest.get("engine", "auto"),
                diagnostics=manifest.get("diagnostics", []),
                source_documents=documents,
                suite_metadata=manifest.get("suite_metadata", {}) if version >= 3 else {},
                toolchains=manifest.get("toolchains", {}) if version >= 3 else {},
            )
            if not isinstance(project.suite_metadata, dict) or not isinstance(project.toolchains, dict):
                raise ValueError("Invalid project suite metadata/toolchains")
            if manifest.get("original_sha256") != _sha256(original):
                raise ValueError("Project original checksum mismatch")
            if manifest.get("working_sha256") != _sha256(working):
                raise ValueError("Project working checksum mismatch")
            if not project.validate(working)["valid"]:
                raise ValueError("Project contains an invalid working SWF")
            snapshot_meta = {item["name"]: item for item in manifest.get("snapshots", [])}
            # to_archive names each entry after its snapshot name, so snapshots
            # with identical bytes stay distinct. Hash matching remains only for
            # entries written under another naming scheme.
            unclaimed = sorted(item for item in names if item.startswith("snapshots/") and item.endswith(".swf"))
            pending = []
            for name, meta in snapshot_meta.items():
                entry = f"snapshots/{hashlib.sha256(name.encode('utf-8')).hexdigest()}.swf"
                if entry not in names:
                    pending.append((name, meta))
                    continue
                data = archive.read(entry)
                if meta.get("sha256") != _sha256(data):
                    raise ValueError("Project snapshot checksum mismatch: " + name)
                unclaimed.remove(entry)
                project.snapshots[name] = ProjectSnapshot(name=name, data=data, created_at=meta.get("created_at", _now()))
            for name, meta in pending:
                for entry in unclaimed:
                    data = archive.read(entry)
                    if meta.get("sha256") == _sha256(data):
                        unclaimed.remove(entry)
                        project.snapshots[name] = ProjectSnapshot(name=name, data=data, created_at=meta.get("created_at", _now()))
                        break
            return project
