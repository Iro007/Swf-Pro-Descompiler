import json
import pytest

from py_swf.cli import build_parser, cmd_inspect, cmd_validate
from tests.conftest import make_synthetic_swf
from tests.test_swf import _build_triangle_shape_tag


def test_cli_exposes_automation_commands():
    parser = build_parser()
    assert parser.parse_args(["inspect", "movie.swf"]).command == "inspect"
    assert parser.parse_args(["compare", "movie.swf"]).command == "compare"
    assert parser.parse_args(["replace", "movie.swf", "--tag", "1", "--resource", "x.png"]).command == "replace"
    assert parser.parse_args(["batch", "in", "out"]).command == "batch"


def test_cli_validate_returns_json(tmp_path, capsys):
    path = tmp_path / "test.swf"
    path.write_bytes(make_synthetic_swf([_build_triangle_shape_tag()]))
    assert cmd_validate(type("Args", (), {"input": path})()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is True


def test_cli_compiles_native_class_program_without_overwriting_sources(tmp_path,capsys):
    from py_swf.cli import main
    from py_swf.swf_parser import SWFFile
    source=tmp_path/"A.as"
    text='package demo { public class A { public function run():int { return 42; } } }'
    source.write_text(text,encoding="utf-8")
    output=tmp_path/"A.swf"
    assert main(["compile-classes",str(source),"--output",str(output),"--compression","ZWS"])==0
    assert json.loads(capsys.readouterr().out)["classes"]==["demo.A"]
    swf=SWFFile()
    swf.read_bytes(output.read_bytes())
    assert swf.signature=="ZWS" and source.read_text(encoding="utf-8")==text
    before=output.read_bytes()
    with pytest.raises(SystemExit):
        main(["compile-classes",str(source),"--output",str(output)])
    assert output.read_bytes()==before
