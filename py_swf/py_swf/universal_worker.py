"""Versioned application JSONL protocol; stdout is exclusively transport."""
from __future__ import annotations
import json
import sys

MAX_LINE = 8 * 1024 * 1024


def utf8_streams():
    """The protocol and CLI output are UTF-8 whatever the console code page.

    Piped standard streams otherwise use the locale encoding (cp1252 on most
    Windows installs), which corrupts paths and messages with accents.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main():
    utf8_streams()
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from .suite_cli import main as cli_main
        return cli_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "native-benchmark":
        from .benchmark_worker import main as benchmark_main
        return benchmark_main()
    if len(sys.argv) > 1 and sys.argv[1] == "rust-benchmark":
        from .benchmark_rust_worker import main as benchmark_main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        return benchmark_main()
    if len(sys.argv) > 1 and sys.argv[1] == "external-benchmark":
        from .benchmark_external_worker import main as benchmark_main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        return benchmark_main()
    from .universal import UniversalSuite
    if len(sys.argv) > 1 and sys.argv[1] not in ("worker", "request"):
        raise SystemExit("Uso: FlashSuiteWorker worker | request OP JSON | cli OPERACIÓN [OPCIONES]")
    suite = UniversalSuite()
    if len(sys.argv) > 1 and sys.argv[1] == "request":
        try:
            print(json.dumps(suite.dispatch(sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}), ensure_ascii=False))
            return 0
        finally:
            suite.close()
    protocol = sys.stdout
    sys.stdout = sys.stderr
    while True:
        line = sys.stdin.readline(MAX_LINE + 1)
        if not line:
            break
        request_id = None
        try:
            if len(line) > MAX_LINE or not line.endswith("\n"):
                raise ValueError("La solicitud supera el límite JSONL")
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("protocol_version", 1) != 1:
                raise ValueError("Protocolo no compatible")
            request_id = request.get("id")
            result = suite.dispatch(request.get("op", ""), request.get("args", {}))
            response = {"id": request_id, "ok": True, "protocol_version": 1, "result": result}
        except Exception as error:
            response = {"id": request_id, "ok": False, "protocol_version": 1, "error": str(error)}
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
        if len(encoded) > MAX_LINE:
            encoded = json.dumps({"id": request_id, "ok": False, "error": "Respuesta demasiado grande; exporta el resultado o utiliza paginación"})
        protocol.write(encoded + "\n")
        protocol.flush()
    suite.close()


if __name__ == "__main__":
    raise SystemExit(main())
