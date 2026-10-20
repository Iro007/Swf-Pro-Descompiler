"""Desktop launcher for the local web application.

With ``pywebview`` installed it opens a native window. Without it, it falls
back to the default browser so the core application remains dependency-light.
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser
import sys
import os
import tempfile
from pathlib import Path

import uvicorn


def free_port(preferred: int) -> int:
    if preferred:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main(argv=None):
    if sys.stdout is None or sys.stderr is None:
        logs = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "PySWFStudio" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stream = (logs / "studio.log").open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    parser = argparse.ArgumentParser(description="PySWF Studio desktop launcher")
    parser.add_argument("--browser", action="store_true", help="use the external browser")
    parser.add_argument("--headless", action="store_true", help="serve locally without opening a window (automation/tests)")
    parser.add_argument("--verify-window", action="store_true", help="load and verify the native webview invisibly, then exit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    # The default native engine never requires a Java/JPEXS installation.
    from server import app as app_module
    if not (app_module._web_dist / "index.html").is_file():
        raise RuntimeError("The web UI is missing. Run npm run build in web before starting PySWF Studio")
    port = free_port(args.port)
    url = f"http://{args.host}:{port}"
    config = uvicorn.Config("server.app:app", host=args.host, port=port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        raise RuntimeError("The local server could not start; check the port and logs")

    if args.browser or args.headless:
        if not args.headless:
            webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
            thread.join(timeout=5)
        return 0

    try:
        import webview
    except ImportError:
        if args.verify_window:
            server.should_exit = True
            thread.join(timeout=5)
            raise RuntimeError("pywebview is unavailable")
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        return 0

    window = webview.create_window("PySWF Studio", url, min_size=(1100, 700), hidden=args.verify_window)
    verified = []
    def verify_window():
        try:
            if not window.events.loaded.wait(20):
                raise RuntimeError("Native webview did not load")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                brand = window.evaluate_js("document.querySelector('.brand')?.textContent || ''")
                if "PySWF" in brand:
                    verified.append(True)
                    break
                time.sleep(.1)
        finally:
            window.destroy()
    webview.start(verify_window if args.verify_window else None)
    server.should_exit = True
    thread.join(timeout=5)
    if args.verify_window and not verified:
        raise RuntimeError("Native window verification failed")
    if args.verify_window:
        print("PASS native window: PySWF Studio UI loaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
