import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
import copy

from py_swf.swf_parser import SWFFile
from py_swf.validation import validate_swf_bytes

MAX_SESSIONS = 8

class OpenSwf:
    def __init__(self, filename, swf, original_bytes=None):
        self.filename = filename
        self.swf = swf
        self.original_bytes = original_bytes if original_bytes is not None else swf.save_bytes()
        self._original_canonical = swf.save_bytes()
        self.dirty = False
        self._undo = []
        self._redo = []
        self._undo_documents = []
        self._redo_documents = []
        self._snapshots = {}
        self.history = []
        self.source_documents = {}
        self.suite_metadata = {}
        self.toolchains = {}
        self._lock = threading.RLock()

    def capture_before_mutation(self, operation="edit", metadata=None):
        with self._lock:
            self._undo.append(self.swf.save_bytes())
            self._undo_documents.append(copy.deepcopy(self.source_documents))
            self._redo.clear()
            self._redo_documents.clear()
            self.history.append({
                "operation": operation,
                "at": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata or {},
            })
            # Keep memory bounded while retaining useful undo history.
            if len(self._undo) > 20:
                self._undo.pop(0)
                self._undo_documents.pop(0)

    def commit_tag(self, index, tag, operation="edit", metadata=None):
        """Validate on a copy; append history only after a successful edit."""
        with self._lock:
            current = self.swf.tags[index]
            if current.tag_type == tag.tag_type and current.data == tag.data:
                return False
            candidate = copy.copy(self.swf)
            candidate.tags = list(self.swf.tags)
            candidate.tags[index] = tag
            data = candidate.save_bytes()
            validation = validate_swf_bytes(data)
            if not validation["valid"]:
                raise ValueError("Invalid candidate SWF: " + "; ".join(validation["errors"]))
            reopened = SWFFile()
            reopened.read_bytes(data)
            self.capture_before_mutation(operation, metadata)
            self.swf = reopened
            self.dirty = data != self._original_canonical
            return True

    def undo(self):
        with self._lock:
            if not self._undo:
                return False
            self._redo.append(self.swf.save_bytes())
            self._redo_documents.append(copy.deepcopy(self.source_documents))
            previous = self._undo.pop()
            restored = SWFFile()
            restored.read_bytes(previous)
            self.swf = restored
            self.source_documents=self._undo_documents.pop() if self._undo_documents else {}
            self.dirty = self.swf.save_bytes() != self._original_canonical
            self.history.append({"operation": "undo", "at": datetime.now(timezone.utc).isoformat(), "metadata": {}})
            return True

    def redo(self):
        with self._lock:
            if not self._redo:
                return False
            self._undo.append(self.swf.save_bytes())
            self._undo_documents.append(copy.deepcopy(self.source_documents))
            following = self._redo.pop()
            restored = SWFFile()
            restored.read_bytes(following)
            self.swf = restored
            self.source_documents=self._redo_documents.pop() if self._redo_documents else {}
            self.dirty = self.swf.save_bytes() != self._original_canonical
            self.history.append({"operation": "redo", "at": datetime.now(timezone.utc).isoformat(), "metadata": {}})
            return True

    def snapshot(self, name):
        with self._lock:
            if not name.strip():
                raise ValueError("Snapshot name cannot be empty")
            self._snapshots[name.strip()] = self.swf.save_bytes()
            return name.strip()

    def restore_snapshot(self, name):
        with self._lock:
            if name not in self._snapshots:
                raise KeyError(name)
            self.capture_before_mutation("restore_snapshot", {"name": name})
            restored = SWFFile()
            restored.read_bytes(self._snapshots[name])
            self.swf = restored
            self.dirty = self.swf.save_bytes() != self._original_canonical
            return True

class SessionRegistry:
    """In-memory LRU registry of open SWF files, keyed by session id."""

    def __init__(self, max_sessions=MAX_SESSIONS):
        self._sessions = OrderedDict()
        self._max = max_sessions
        self._lock = threading.Lock()

    def open_swf(self, filename, swf, original_bytes=None):
        sid = uuid.uuid4().hex
        with self._lock:
            self._sessions[sid] = OpenSwf(filename, swf, original_bytes)
            while len(self._sessions) > self._max:
                self._sessions.popitem(last=False)
        return sid

    def open(self, filename, file_bytes):
        swf = SWFFile()
        swf.read_bytes(file_bytes)
        return self.open_swf(filename, swf, file_bytes)

    def get(self, sid):
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                raise KeyError(sid)
            self._sessions.move_to_end(sid)
            return session

    def close(self, sid):
        with self._lock:
            self._sessions.pop(sid, None)

registry = SessionRegistry()
