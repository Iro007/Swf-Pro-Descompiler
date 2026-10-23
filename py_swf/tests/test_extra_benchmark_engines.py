import json
from pathlib import Path

import pytest

from py_swf.avm1 import assemble_avm1
from py_swf.benchmark_engines import ENGINES, checked_tool, describe_engines
from py_swf.benchmark_worker import handle
from py_swf.benchmarks import quality, run_engine
from py_swf.swf_parser import SWFTag
from tests.conftest import make_synthetic_swf


def test_registry_does_not_call_disassemblers_source_decompilers(tmp_path):
    assert "source" not in ENGINES["flasm"].operations
    assert "source" not in ENGINES["rabcdasm"].operations
    assert ENGINES["flare"].languages==("as2",)
    assert ENGINES["pyflashkit"].languages==("as3",)
    assert ENGINES["pyflashkit"].operations==("source","disassemble")
    catalog=describe_engines(tmp_path / "missing.json")
    assert not next(e for e in catalog if e["id"]=="flare")["configured"]
    assert ENGINES["swfmill"].operations==("xml",)
    assert ENGINES["ruffle"].operations==("execute",)
    assert "source" not in ENGINES["swfextract"].operations
    assert ENGINES["flex_swfdump"].operations == ("disassemble",)
    assert ENGINES["flex_swfdump"].languages == ("as2", "as3")
    sothink=next(e for e in catalog if e["id"]=="sothink")
    assert "https://www.shankejingling.com/" in sothink["distribution_aliases"]


def test_registry_versions_keep_project_metadata_separate_from_pinned_rust_binary(tmp_path,monkeypatch):
    import hashlib
    import py_swf.benchmark_engines as registry
    project=tmp_path/'py_swf_editor';project.mkdir()
    (project/'pyproject.toml').write_text('[project]\nname="py_swf_editor"\nversion="0.4.2"\n',encoding='utf-8')
    executable=tmp_path/'frozen-rust.exe';executable.write_bytes(b'opaque pinned executable')
    config=tmp_path/'tools.json'
    config.write_text(json.dumps({'development_only':True,'tools':{'rust':{
        'executable':str(executable),'version':'frozen-0.2.0',
        'artifact_sha256':{str(executable):hashlib.sha256(executable.read_bytes()).hexdigest()}}}}))
    monkeypatch.setattr(registry,'ROOT',project)
    entries={row['id']:row for row in registry.describe_engines(config)}
    assert entries['native']['version']=='0.4.2'
    assert entries['rust']['version']=='frozen-0.2.0'
    assert 'not substituted' in entries['rust']['version_authority']


@pytest.mark.parametrize("operation", ["source", "decompile"])
def test_external_source_dispatch_accepts_both_protocol_aliases(operation, monkeypatch):
    import py_swf.benchmark_external_worker as adapter
    from tests.native_fixtures import as3_tag
    calls=[]
    def fake_invoke(command, cwd):
        calls.append(command)
        output=Path(command[command.index("--outdir")+1])
        output.mkdir()
        (output / "Test.as").write_text("package { public class Test {} }", encoding="utf-8")
        return b"", "Decompiled 1/1 classes", 1.0
    monkeypatch.setattr(adapter,"invoke",fake_invoke)
    config={"executable":"isolated-python.exe","adapter_mode":"cli_first_abc","version":"1.3.0","artifact_sha256":{"isolated-python.exe":"a"*64}}
    response=adapter.perform("pyflashkit",config,make_synthetic_swf([as3_tag()]),operation)
    assert calls[0][1:4]==["-I","-c",adapter.FLASHKIT_CLI]
    assert response["status"]=="output_unmapped" and response["artifacts"]
    assert response["units"]==[] and quality(response)["source_available"] is None
    assert response["compilation"]=="not_run" and response["behavior"]=="not_run"
    assert response["source_fidelity"]=="not_verified"
    assert "CLI startup" in response["time_scope"]


def test_pyflashkit_as2_and_unsupported_operations_do_not_invoke(monkeypatch):
    import py_swf.benchmark_external_worker as adapter
    def forbidden(*args):
        raise AssertionError("unsupported operation must not run a subprocess")
    monkeypatch.setattr(adapter,"invoke",forbidden)
    data=make_synthetic_swf([SWFTag(12,assemble_avm1("push 1\ntrace\nend"))])
    for operation in ("source","decompile","disassemble","compile"):
        result=adapter.perform("pyflashkit",{},data,operation)
        assert result["status"]=="not_applicable" and result["eligible_units"]==0


def test_pyflashkit_empty_source_is_failure_despite_zero_exit(monkeypatch):
    import py_swf.benchmark_external_worker as adapter
    from tests.native_fixtures import as3_tag
    monkeypatch.setattr(adapter,"invoke",lambda *args:(b"", "Decompiled 0/1 classes", 1.0))
    with pytest.raises(ValueError,match="no nonempty output"):
        adapter.perform("pyflashkit",{"executable":"isolated-python.exe","adapter_mode":"cli_first_abc"},make_synthetic_swf([as3_tag()]),"decompile")


def test_external_native_failure_keeps_return_code_and_diagnostics(tmp_path,monkeypatch):
    import subprocess
    import py_swf.benchmark_external_worker as adapter
    monkeypatch.setattr(adapter.subprocess,"run",lambda *args,**kwargs:subprocess.CompletedProcess(args[0],7,b"native stdout",b"native error"))
    with pytest.raises(adapter.ExternalToolError) as raised:
        adapter.invoke(["not-executed"],tmp_path)
    assert raised.value.returncode==7
    assert raised.value.stderr=="native error" and raised.value.stdout=="native stdout"


def test_pyflashkit_disassembly_snippet_is_valid_and_includes_all_methods():
    from py_swf.benchmark_external_worker import FLASHKIT_DISASSEMBLE
    compile(FLASHKIT_DISASSEMBLE,"pyflashkit-disassembly-adapter","exec")
    assert "enumerate(ws.abc_blocks)" in FLASHKIT_DISASSEMBLE
    assert "for body in abc.method_bodies" in FLASHKIT_DISASSEMBLE


def test_frozen_external_command_resolves_workspace_artifacts(tmp_path,monkeypatch):
    import sys
    import py_swf.benchmark_engines as registry
    references=tmp_path / ".dev-references"
    references.mkdir()
    executable=references / "python.exe"
    executable.write_bytes(b"pinned external runtime")
    import hashlib
    digest=hashlib.sha256(executable.read_bytes()).hexdigest()
    config=references / "extra-tools.json"
    historic="C:/old/py_swf/.dev-references/python.exe"
    config.write_text(json.dumps({"development_only":True,"tools":{"pyflashkit":{"executable":historic,"artifact_sha256":{historic:digest}}}}),encoding="utf-8")
    monkeypatch.setattr(registry,"ROOT",tmp_path)
    monkeypatch.setattr(sys,"frozen",True,raising=False)
    command=registry.external_command("pyflashkit",config)
    assert command[:2]==[sys.executable,"external-benchmark"]
    assert registry.checked_tool("pyflashkit",config)["executable"]==str(executable)


def test_local_installer_is_not_a_configured_engine_and_blocked_download_is_absent(tmp_path, monkeypatch):
    import py_swf.benchmark_engines as registry

    references=tmp_path / ".dev-references"
    references.mkdir()
    (references / "competing-tools-inventory.json").write_text(json.dumps({"tools":{
        "sothink":{"status":"installer_available","version":"7.4","sha256":"a"*64,"signature_status":"Valid"},
        "asv":{"status":"blocked_by_security","threat_name":"Program:AndroidOS/Multiverze","download_completed":False,"local_installer_available":False}
    }}),encoding="utf-8")
    monkeypatch.setattr(registry,"ROOT",tmp_path)
    catalog={item["id"]:item for item in describe_engines(tmp_path / "missing.json")}
    assert not catalog["sothink"]["configured"]
    assert catalog["sothink"]["installer_version"]=="7.4"
    assert catalog["sothink"]["signature_status"]=="Valid"
    assert "sin medición" in catalog["sothink"]["setup_diagnostic"]
    assert not catalog["asv"]["configured"]
    assert "La descarga no se completó" in catalog["asv"]["setup_diagnostic"]
    assert "falso positivo no demostrado" in catalog["asv"]["setup_diagnostic"]


def test_unknown_coverage_is_never_reported_as_zero_or_full():
    result=quality({"status":"output_unmapped","eligible_units":12,"units":[]})
    assert result["expected_units_reported"]==12
    assert result["source_available"] is result["disassembly_available"] is None
    assert result["semantic_parity"]=="not_established" and result["behavior_verified"]==0


def test_missing_tool_and_wrong_operation_retained_without_made_up_times(tmp_path):
    data=make_synthetic_swf([SWFTag(12,assemble_avm1("push 1\ntrace\nend"))])
    profile={"warmups":1,"hot_runs":3,"cold_runs":1}
    absent=run_engine("flare",data,profile,tool_config=tmp_path / "missing.json")
    assert absent["status"]=="not_configured" and absent["failures"]
    assert absent["quality"]["source_available"] is None
    assert absent["hot"]["median_ms"] is None
    unsupported=run_engine("flasm",data,profile,operation="source")
    assert unsupported["status"]=="not_applicable"
    assert unsupported["hot"]["median_ms"] is None
    assert not unsupported["failures"]


def test_binary_hash_mismatch_is_not_run(tmp_path):
    path=tmp_path / "tool.exe"
    path.write_bytes(b"not an executable")
    config=tmp_path / "tools.json"
    config.write_text(json.dumps({"development_only":True,"tools":{"flasm":{"executable":str(path),"artifact_sha256":{str(path):"0"*64}}}}))
    with pytest.raises(ValueError,match="hash mismatch"):
        checked_tool("flasm",config)


def test_flex_jar_must_be_hashed_before_running(tmp_path):
    import hashlib
    executable=tmp_path / 'java.exe'
    executable.write_bytes(b'pinned runtime')
    config=tmp_path / 'tools.json'
    config.write_text(json.dumps({'development_only':True,'tools':{'flex_swfdump':{
        'executable':str(executable),'library_jar':str(tmp_path/'unhashed.jar'),
        'artifact_sha256':{str(executable):hashlib.sha256(executable.read_bytes()).hexdigest()}}}}))
    with pytest.raises(ValueError,match='JAR must have a recorded artifact hash'):
        checked_tool('flex_swfdump',config)


@pytest.mark.parametrize('contents',[
    'Usage: swfdump', '<swf version="10"><DoABC2>2 Method Entries</DoABC2></swf>',
    '<swf version="10"><DoABC2>offset 10: 47 returnvoid </DoABC2>',
    '<swf version="10"><DoABC2>offset 10: 47 returnvoid </DoABC2><!-- Error: could not parse abc --></swf>',
])
def test_flex_zero_exit_does_not_make_metadata_or_parse_errors_disassembly(monkeypatch,contents):
    import py_swf.benchmark_external_worker as adapter
    from tests.native_fixtures import as3_tag
    def fake_invoke(command,cwd):
        Path(command[command.index('-out')+1]).write_text(contents,encoding='utf-8')
        return b'', '', 1.0
    monkeypatch.setattr(adapter,'invoke',fake_invoke)
    with pytest.raises(ValueError,match='swfdump'):
        adapter.perform('flex_swfdump',{'executable':'java','library_jar':'swfdump.jar'},
                        make_synthetic_swf([as3_tag()]),'disassemble')


def test_flex_instruction_file_remains_unmapped_and_is_not_source(monkeypatch):
    import py_swf.benchmark_external_worker as adapter
    from tests.native_fixtures import as3_tag
    calls=[]
    def fake_invoke(command,cwd):
        calls.append(command)
        Path(command[command.index('-out')+1]).write_text(
            '<swf version="10"><DoABC2>offset 10: 47 returnvoid </DoABC2></swf>',encoding='utf-8')
        return b'', '', 1.0
    monkeypatch.setattr(adapter,'invoke',fake_invoke)
    data=make_synthetic_swf([as3_tag()])
    response=adapter.perform('flex_swfdump',{'executable':'java','library_jar':'swfdump.jar'},data,'disassemble')
    assert '-asm' in calls[0] and '-abc' in calls[0] and '-showbytecode' in calls[0]
    assert response['status']=='output_unmapped' and response['artifact_unit_scope']=='file'
    assert response['eligible_units']==2 and response['units']==[] and response['artifacts']
    assert quality(response)['disassembly_available'] is None
    assert quality(response)['source_available'] is None
    assert adapter.perform('flex_swfdump',{},data,'source')['status']=='not_applicable'
    assert len(calls)==1


def test_disassembly_counts_only_verified_units_and_keeps_source_unknown():
    response={'ok':True,'operation':'disassemble','units':[
        {'generation':'disassembly','identity_verified':True,'disassembly':'available'},
        {'generation':'disassembly','identity_verified':False,'disassembly':'available'},
        {'generation':'disassembly','identity_verified':True,'disassembly':'partial'}]}
    assert quality(response)['disassembly_available']==1
    assert quality(response)['source_available'] is None


@pytest.mark.parametrize('engine,language',[('flasm','as2'),('flex_swfdump','as2'),('flex_swfdump','as3')])
def test_optional_real_new_adapters_keep_original_and_unknown_unit_coverage(engine,language):
    from py_swf.benchmark_external_worker import perform
    from tests.native_fixtures import as3_tag
    try:
        config=checked_tool(engine)
    except (OSError,ValueError):
        pytest.skip('Pinned optional engine unavailable: '+engine)
    data=make_synthetic_swf([as3_tag()] if language=='as3' else [SWFTag(12,assemble_avm1('push 1\ntrace\nend'))])
    before=bytes(data)
    response=perform(engine,config,data,'disassemble')
    assert response['status']=='output_unmapped' and response['artifacts']
    assert response['artifact_unit_scope']=='file' and response['units']==[]
    assert quality(response)['disassembly_available'] is None
    assert response['native_returncode']==0 and data==before


def test_native_disassembly_is_not_marked_as_source():
    import base64
    data=make_synthetic_swf([SWFTag(12,assemble_avm1("push 1\ntrace\nend"))])
    report=handle({"operation":"disassemble","swf_base64":base64.b64encode(data).decode()})
    q=quality(report)
    assert q["source_available"] is None and q["disassembly_available"]==1
    assert q["behavior_verified"]==0


