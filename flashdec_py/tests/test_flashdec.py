import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flashdec.cli import main  # noqa: E402
from flashdec.merge import merge  # noqa: E402
from flashdec.native import NativeScript, make_file_name  # noqa: E402
from flashdec.scoring import balanced, score  # noqa: E402

CORPUS = ROOT.parent / "benchmark_inputs" / "parity-20261014" / "swf"


def test_score_clean_and_markers():
    assert score("var a = 1;").value == 100
    assert score(None) is None and score("  \n") is None
    assert score("§§push(1);").value == 70
    assert score("// Incomplete function f(); original method:1 retained as P-code").value == 70
    assert score("function f() {").value == 60  # unbalanced


def test_balanced_ignores_strings_and_comments():
    assert balanced('trace("}"); // {\n/* ( */ var x = [1];')
    assert not balanced("if (a) { b(;")


def test_merge_prefers_better_score_then_preference():
    native = [NativeScript("Main.as", "class Main {}"), NativeScript("A.as", "class A {}")]
    jpexs = {"Main.as": "class Main { §§goto(1); }", "A.as": "class A {}", "B.as": "class B {}"}
    choices = {c.path: c for c in merge(native, jpexs, ["jpexs", "native"])}
    assert choices["Main.as"].engine == "native"
    assert choices["A.as"].engine == "jpexs"
    assert choices["B.as"].engine == "jpexs" and not choices["B.as"].only_native


def test_merge_pairs_event_handlers_by_container():
    native = [NativeScript("DefineButton2_3/BUTTONCONDACTION_0.as", "on(release){}", pairable=True)]
    jpexs = {"DefineButton2_3/on(release).as": "on(release){\n}"}
    [choice] = merge(native, jpexs, ["jpexs", "native"])
    assert choice.path == "DefineButton2_3/on(release).as"
    assert choice.native_path == "DefineButton2_3/BUTTONCONDACTION_0.as"


def test_make_file_name():
    assert make_file_name('a:b*c') == "a_b_c"


@pytest.mark.skipif(not CORPUS.is_dir(), reason="corpus not present")
@pytest.mark.parametrize("family,expected", [
    ("dev-as2-timeline", "frame_1/DoAction.as"),
    ("dev-as3-classes-namespaces", "app/Model.as"),
])
def test_native_end_to_end(tmp_path, family, expected):
    swf = CORPUS / family / "release.fws.swf"
    assert main([str(swf), "-o", str(tmp_path), "-e", "native", "-q"]) == 0
    assert (tmp_path / "scripts" / expected).is_file()
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["summary"]["scripts"] >= 1
    assert (tmp_path / "pcode").is_dir()
