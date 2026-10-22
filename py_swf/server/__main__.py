import argparse
import threading
import webbrowser

import uvicorn

def main(argv=None):
    parser = argparse.ArgumentParser(description="PySWF Studio FastAPI server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="do not open the default browser")
    args = parser.parse_args(argv)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(f"http://{args.host}:{args.port}",)).start()
    uvicorn.run("server.app:app", host=args.host, port=args.port)

if __name__ == "__main__":
    main()
