from server.__main__ import main as server_main


def test_server_parser_help_is_callable(capsys):
    try:
        server_main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "FastAPI server" in capsys.readouterr().out
