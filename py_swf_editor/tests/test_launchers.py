from desktop import free_port
from server.__main__ import main as server_main


def test_free_port_returns_requested_port_when_available():
    port = free_port(0)
    assert isinstance(port, int)
    assert port > 0


def test_server_parser_help_is_callable(capsys):
    try:
        server_main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "FastAPI server" in capsys.readouterr().out
