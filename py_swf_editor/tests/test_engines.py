import pytest

from py_swf.engines import (
    EngineOperationError,
    NativePythonEngine,
    compare_engines,
    engine_capabilities,
)
from tests.conftest import make_synthetic_swf
from tests.test_api import _doaction_tag


def test_native_engine_common_facade_roundtrip():
    tag = _doaction_tag()
    source = make_synthetic_swf([tag])
    engine = NativePythonEngine()
    assert engine.inspect_swf(source)["signature"] == "FWS"
    assert engine.list_tags(source)[0]["name"] == "DoAction"
    listing = engine.disassemble(source, 0)
    assert listing["kind"] == "avm1"
    rewritten = engine.assemble(source, 0, listing["scripts"][0]["code"].replace("hola", "adios"))
    assert "adios" in engine.disassemble(rewritten, 0)["scripts"][0]["code"]
    assert engine.validate(rewritten)["valid"]


def test_native_engine_rejects_wrong_resource_kind():
    source = make_synthetic_swf([_doaction_tag()])
    with pytest.raises(EngineOperationError):
        NativePythonEngine().export_resource(source, 0)


def test_native_engine_compiles_object_class_program():
    engine=NativePythonEngine()
    swf=engine.compile_classes('public class Arithmetic { public function run():int { return 42; } }')
    assert engine.validate(swf)["valid"]
    assert any(t["code"]==82 for t in engine.list_tags(swf))
    assert engine.capabilities.operations["compile_classes"]=="partial"


def test_capabilities_are_machine_readable():
    names = {item["name"] for item in engine_capabilities()}
    assert names == {"native", "jpexs"}
    assert any(item["operations"]["decompile"] == "partial" for item in engine_capabilities())


def test_compare_without_jpexs_returns_structured_failure(monkeypatch):
    monkeypatch.delenv("PY_SWF_FFDEC", raising=False)
    monkeypatch.delenv("PY_SWF_FFDEC_JAR", raising=False)
    result = compare_engines(make_synthetic_swf([_doaction_tag()]), 0)
    assert result["engine"] == "compare"
    assert result["native"]["ok"] is True
    assert result["jpexs"]["ok"] is False
