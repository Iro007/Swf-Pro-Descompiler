"""Bounded development workers: JSONL correlation, deadlines, tree RSS and cleanup."""
from __future__ import annotations

from collections import deque
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import uuid


class WorkerError(RuntimeError):
    pass


class WindowsJob:
    """OS-enforced aggregate committed-memory limit and kill-on-close.

    RSS reporting is separate: sampled physical memory is not committed memory.
    A worker receives no input until assignment to this job succeeds.
    """
    def __init__(self, pid, memory_bytes):
        size = ctypes.c_size_t
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong), ("flags", wintypes.DWORD), ("min_ws", size), ("max_ws", size), ("active_processes", wintypes.DWORD), ("affinity", size), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(key, ctypes.c_ulonglong) for key in ("read_count", "write_count", "other_count", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Io), ("process_memory", size), ("job_memory", size), ("peak_process", size), ("peak_job", size)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = Extended()
        information.basic.flags = 0x2000 | 0x200  # kill on close + job memory
        information.job_memory = memory_bytes
        process = self.kernel.OpenProcess(0x100 | 0x1, False, pid)
        try:
            if not process or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(information), ctypes.sizeof(information)) or not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise
        finally:
            if process:
                self.kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Worker:
    def __init__(self, command, *, cwd, environment=None, timeout=120, memory_bytes=2*1024**3, sample_interval=.02):
        import psutil
        self.psutil, self.timeout, self.sample_interval = psutil, timeout, sample_interval
        self.responses, self.logs = queue.Queue(), deque(maxlen=128)
        self.job = None
        self.peak_rss, self.samples, self.sample_errors = 0, 0, 0
        self.memory_bytes = memory_bytes
        self.sample_lock = threading.Lock()
        self.stop = threading.Event()
        self.home = tempfile.TemporaryDirectory(prefix="pyswf-reference-home-")
        self.process = None
        started = time.perf_counter()
        try:
            # These programs receive only explicit requests. Do not inherit
            # credentials or app server tokens in a research child environment.
            # The workspace selector is a local directory, required by frozen
            # adapters to resolve pinned development tools outside _MEI.
            env = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC", "LANG", "LC_ALL", "FLASH_SUITE_WORKSPACE")}
            env.update(environment or {})
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            command = [part.replace("{reference_home}", self.home.name) for part in command]
            self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name != "nt")
            if os.name == "nt":
                self.job = WindowsJob(self.process.pid, memory_bytes)
            else:
                # Sample-enforced bound on other hosts; explicitly not an OS
                # sandbox or the same enforcement authority as a Windows Job.
                self.job = None
            threading.Thread(target=self._read, daemon=True).start()
            threading.Thread(target=self._stderr, daemon=True).start()
            threading.Thread(target=self._sample, daemon=True).start()
            self.request({"operation": "ping"})
            self.startup_ms = (time.perf_counter() - started)*1000
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                self.responses.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self.responses.put(None)

    def _stderr(self):
        try:
            while chunk := self.process.stderr.read(2048):
                self.logs.append(chunk)
        except (OSError, ValueError):
            pass

    def _sample(self):
        root = self.psutil.Process(self.process.pid)
        while not self.stop.is_set():
            try:
                total = 0
                for process in [root, *root.children(recursive=True)]:
                    try:
                        total += process.memory_info().rss
                    except self.psutil.Error:
                        self.sample_errors += 1
                with self.sample_lock:
                    self.peak_rss = max(self.peak_rss, total)
                    self.samples += 1
                if total > self.memory_bytes and os.name != "nt":
                    self.process.kill()
                    break
            except self.psutil.Error:
                self.sample_errors += 1
            self.stop.wait(self.sample_interval)

    def request(self, payload):
        request = {**payload, "request_id": uuid.uuid4().hex}
        started = time.perf_counter()
        line = json.dumps(request, ensure_ascii=False) + "\n"
        encode_ms = (time.perf_counter()-started)*1000
        with self.sample_lock:
            self.peak_rss, self.samples, self.sample_errors = 0, 0, 0
        write_errors = []
        def write():
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
            except (OSError, ValueError) as error:
                write_errors.append(str(error))
                self.responses.put(None)
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        try:
            response_line = self.responses.get(timeout=max(.001, self.timeout-(time.perf_counter()-started)))
        except queue.Empty:
            self.close()
            raise WorkerError(f"Operation timed out after {self.timeout} seconds")
        if response_line is None:
            code = self.process.poll()
            logs = "".join(self.logs)[-5000:]
            raise WorkerError(f"Worker exited without a response (exit={code}); memory limit may have been reached. {logs} {';'.join(write_errors)}")
        decode_start = time.perf_counter()
        response = json.loads(response_line)
        decode_ms = (time.perf_counter()-decode_start)*1000
        if response.get("request_id") != request["request_id"]:
            self.close()
            raise WorkerError("JSONL response request_id mismatch")
        elapsed = (time.perf_counter()-started)*1000
        response["measurement"] = {"wall_ms": elapsed, "wire_encode_ms": encode_ms, "wire_decode_ms": decode_ms, "peak_tree_rss_bytes": self.peak_rss if self.samples else None, "memory_samples": self.samples, "memory_sample_errors": self.sample_errors, "sample_interval_ms": self.sample_interval*1000, "limit_enforcement": "windows_job_committed_memory" if self.job else "sampled_rss_watchdog"}
        return response

    def close(self):
        self.stop.set()
        if self.job:
            self.job.close()
            self.job = None
        elif self.process and self.process.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    import signal
                    os.killpg(self.process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                self.process.kill()
        if self.process:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
        self.home.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
