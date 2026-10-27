"""Per-unit fidelity: a unit is compiled alone, spliced into the original SWF and judged by the trace Ruffle prints."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bench  # noqa: E402
import bench_units as units  # noqa: E402
from run_semantic_benchmarks import method_records, native_documents, save_documents  # noqa: E402

from py_swf import paths  # noqa: E402
from py_swf.closeness import canonical_units  # noqa: E402
from py_swf.editing import source_edit_reason  # noqa: E402
from py_swf.script_inventory import enumerate_scripts  # noqa: E402
from py_swf.unit_editing import edit_unit  # noqa: E402
from py_swf.universal import UniversalSuite  # noqa: E402

CASES = paths.CORPUS / "parity-20261014/optimized"
CLASSES = CASES / "swf/dev-as3-classes-namespaces/release.fws.swf"
CAPTURES = CASES / "swf/dev-as2-captures/release.fws.swf"
needs_corpus = pytest.mark.skipif(not CLASSES.is_file(), reason="sealed parity corpus unavailable")
needs_ruffle = pytest.mark.skipif(not bench.RUNTIME.is_file(), reason="the headless Ruffle harness is not installed")
EXPECTED = ["PARITY_NAMESPACE_7"]


# --- reading a method written as a declaration -------------------------------------------------------------------------

JPEXS = "      public function add(param1:int) : int\n      {\n         this.total += param1;\n         return this.total;\n      }\n"


def test_a_declaration_becomes_its_body_and_its_parameters_become_aliases_of_the_registers():
    parameters, body = units.unwrap_method(JPEXS)
    assert parameters == [("param1", "int", False)]
    assert body.strip() == "this.total += param1;\n         return this.total;"
    assert units.method_body(JPEXS) == "var param1:int = local1;\n" + body
    assert units.method_body(JPEXS, {"param1"}) == body, "the ABC already names that register"


def test_modifiers_getters_banners_and_plain_bodies_are_all_read():
    assert units.unwrap_method("localns function label() : String\n{\n return \"model\";\n}\n")[0] == []
    assert units.unwrap_method("public function get value() : int { return this.total; }")[1].strip() == "return this.total;"
    # GoSWF and AsmSWF open with a comment, and RustSWF and JsSWF give the method a made-up name
    banner = "// AsmSWF (x86_64 GAS) structured AVM2 reconstruction\nfunction () {\n    return;\n}\n"
    assert units.unwrap_method(banner) == ([], "\n    return;\n")
    assert units.unwrap_method("/* a block\ncomment */\nfunction class_init() {\n\n}\n")[0] == []
    assert units.method_body("function instance_init(arg1) {\n    total = arg1;\n}\n") == "var arg1 = local1;\n\n    total = arg1;\n"
    # PySWF's own method source is a body already
    native = "super();\nthis.total = local1;\nreturn;"
    assert units.unwrap_method(native) == (None, native) and units.method_body(native) == native
    assert units.unwrap_method("var f = function() { return 1; };\nreturn f();")[0] is None, "a closure is not the declaration of the method"


def test_default_values_rest_parameters_generics_and_braces_in_strings_do_not_confuse_the_reader():
    text = 'function f(a:int, b:String = "x,)", v:Vector.<int>, ...rest):void {\n  trace("}{");\n  return;\n}'
    parameters, body = units.unwrap_method(text)
    assert parameters == [("a", "int", False), ("b", "String", False), ("v", "Vector.<int>", False), ("rest", None, True)]
    assert 'trace("}{");' in body
    assert units.method_body(text) == "var a:int = local1;\nvar b:String = local2;\nvar v = local3;\nvar rest = local4;\n" + body
    assert units.method_body("function f(local1:int, x:int) { return x; }").startswith("var x:int = local2;\n"), "local1 already is the register"
    assert units.unwrap_method("function f(a {") == (None, "function f(a {"), "an unclosed declaration is left as it is"


def test_what_the_single_unit_path_cannot_take_for_anyone_is_named_in_spanish():
    assert units.scope_reason("Existing activation closures require exact P-code") == "cierres con activación"
    assert units.scope_reason("Non-default/private namespaces require exact P-code to preserve binding") == "espacios de nombres privados o explícitos"
    assert units.scope_reason("Runtime multinames require exact P-code") == "multinombres de ejecución"
    assert units.scope_reason("Declared type source is shadowed by a member or parameter") == "tipos ocultos por un miembro o parámetro"
    assert units.scope_reason("Original bytecode requires exact P-code: x") == "código que exige P-code exacto"


# --- the single-unit path accepts initializers when asked --------------------------------------------------------------

@needs_corpus
def test_constructors_and_initializers_are_protected_unless_the_caller_judges_the_result_itself():
    data = CLASSES.read_bytes()
    constructor = next(unit for unit in enumerate_scripts(data) if unit.name == "app::Model.constructor")
    assert "Constructors and initializers" in source_edit_reason(constructor.abc, constructor.body)
    assert source_edit_reason(constructor.abc, constructor.body, allow_initializers=True) is None
    locator = {"document_id": constructor.document_id, "bytecode_sha256": hashlib.sha256(constructor.code).hexdigest()}
    with pytest.raises(ValueError, match="Constructors and initializers"):
        edit_unit(data, locator, "super();\nthis.total = local1;\nreturn;", source=True)
    spliced = edit_unit(data, locator, "super();\nthis.total = local1;\nreturn;", source=True, allow_initializers=True)
    assert spliced != data and len(spliced) > 0


# --- what to judge ------------------------------------------------------------------------------------------------------

@needs_corpus
def test_the_plan_is_made_from_the_swfs_own_inventory_and_is_the_same_every_time():
    data = CLASSES.read_bytes()
    canonical = canonical_units(data)
    plan = units.plan_case(data, canonical, 0)
    kinds = Counter(unit["kind"] for unit in plan["units"])
    assert kinds == {"class": 2, "method": 8} and plan["total"] == 10
    assert plan["out_of_scope"] == {"espacios de nombres privados o explícitos": 3}
    classes = {unit["id"]: unit["parts"] for unit in plan["units"] if unit["kind"] == "class"}
    assert classes["tag:6/class:1"] == ["tag:6/method:%d" % i for i in (3, 4, 5, 6, 7, 8)], "a class is judged through its editable methods"
    assert plan["methods"] == sorted({part for parts in classes.values() for part in parts} | {u["id"] for u in plan["units"] if u["kind"] == "method"})
    assert plan == units.plan_case(data, canonical, 0)
    capped = units.plan_case(data, canonical, 4)
    assert len(capped["units"]) == 4 and capped["total"] == 10
    assert capped["units"] == units.plan_case(data, canonical, 4)["units"]
    assert capped["units"][0] == plan["units"][0] and capped["units"][-1] == plan["units"][-1], "an even spread from the first to the last"
    assert len(units.plan_case(data, canonical, 50)["units"]) == 10, "a cap above the total takes everything"


@needs_corpus
def test_an_as2_action_is_a_unit_and_its_nested_functions_travel_inside_it():
    data = CAPTURES.read_bytes()
    plan = units.plan_case(data, canonical_units(data), 20)
    assert [(unit["kind"], unit["id"]) for unit in plan["units"]] == [("action", "tag:2/actions")]
    assert plan["methods"] == [], "no method source is kept for AS2: the action document is the unit" and plan["total"] == 1


# --- compiling a unit alone and splicing it -----------------------------------------------------------------------------

def request(tmp_path, data, stage, tasks):
    original = tmp_path / "original.swf"
    original.write_bytes(data)
    return {"stage": stage, "original": str(original), "folder": str(tmp_path), "units": tasks}


def source_file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


@needs_corpus
def test_a_method_is_compiled_alone_and_spliced_into_the_original_leaving_everything_else(tmp_path):
    data = CLASSES.read_bytes()
    good = source_file(tmp_path, "add.as", "function add(param1:int) : int\n{\n this.total = this.total + param1;\n return this.total;\n}\n")
    bad = source_file(tmp_path, "bad.as", "this is not ActionScript (")
    tasks = [{"id": "m5", "kind": "method", "parts": [{"id": "tag:6/method:5", "source_file": good}]},
             {"id": "m8", "kind": "method", "parts": [{"id": "tag:6/method:8", "source_file": bad}]},
             {"id": "m99", "kind": "method", "parts": [{"id": "tag:6/method:99", "source_file": good}]},
             {"id": "ctor", "kind": "method", "parts": [{"id": "tag:6/method:4", "source_file": source_file(tmp_path, "ctor.as", "function Model(param1:int) { super(); this.total = param1; }")}]}]
    results = {item["id"]: item for item in units.splice_stage(request(tmp_path, data, "splice", tasks))}
    assert results["m5"]["ok"] and results["ctor"]["ok"], results
    assert not results["m8"]["ok"] and "SyntaxError" in results["m8"]["reason"]
    assert not results["m99"]["ok"] and "no existe" in results["m99"]["reason"]
    candidate = Path(results["m5"]["file"]).read_bytes()
    assert candidate != data and candidate[:3] == b"FWS"
    # everything but the DoABC tag that holds the method is byte for byte the original's
    before, after = enumerate_scripts(data), enumerate_scripts(candidate)
    same = {u.document_id: u.code for u in before if u.document_id != "tag:6/method:5"}
    assert {u.document_id: u.code for u in after if u.document_id != "tag:6/method:5"} == same, "the other methods are untouched"


@needs_corpus
def test_a_class_is_the_splice_of_all_its_methods_together(tmp_path):
    data = CLASSES.read_bytes()
    texts = {"tag:6/method:5": "this.total = this.total + local1;\nreturn this.total;", "tag:6/method:7": "return this.total;"}
    parts = [{"id": name, "source_file": source_file(tmp_path, name.split(":")[-1] + ".as", text)} for name, text in texts.items()]
    result = units.splice_stage(request(tmp_path, data, "splice", [{"id": "tag:6/class:1", "kind": "class", "parts": parts}]))[0]
    assert result["ok"]
    after = {u.document_id: u.code for u in enumerate_scripts(Path(result["file"]).read_bytes())}
    before = {u.document_id: u.code for u in enumerate_scripts(data)}
    changed = {name for name in after if after[name] != before[name]}
    assert changed and changed <= set(texts), "only the spliced methods can have changed"


@needs_corpus
def test_a_part_that_fails_makes_the_whole_class_unit_fail_to_compile(tmp_path):
    data = CLASSES.read_bytes()
    parts = [{"id": "tag:6/method:7", "source_file": source_file(tmp_path, "ok.as", "return this.total;")},
             {"id": "tag:6/method:8", "source_file": source_file(tmp_path, "no.as", "this.total += local1;")}]
    result = units.splice_stage(request(tmp_path, data, "splice", [{"id": "c", "kind": "class", "parts": parts}]))[0]
    assert not result["ok"] and "SyntaxError" in result["reason"]


@needs_corpus
def test_a_probe_puts_a_marker_trace_at_the_entry_of_a_unit(tmp_path):
    data = CLASSES.read_bytes()
    tasks = [{"id": name, "parts": [{"id": name}]} for name in ("tag:6/method:5", "tag:6/method:7")]
    results = units.splice_stage(request(tmp_path, data, "probe", tasks))
    assert all(item["ok"] for item in results)
    probe = Path(results[0]["file"]).read_bytes()
    assert units.MARKER.encode() in probe and units.MARKER.encode() not in data
    assert {u.document_id: u.code for u in enumerate_scripts(probe) if u.document_id != "tag:6/method:5"} == {
        u.document_id: u.code for u in enumerate_scripts(data) if u.document_id != "tag:6/method:5"}
    (tmp_path / "as2").mkdir()
    action = units.splice_stage(request(tmp_path / "as2", CAPTURES.read_bytes(), "probe", [{"id": "a", "parts": [{"id": "tag:2/actions"}]}]))[0]
    assert action["ok"] and units.MARKER.encode() in Path(action["file"]).read_bytes()


# --- the oracle ----------------------------------------------------------------------------------------------------------

@needs_corpus
@needs_ruffle
def test_ruffle_tells_the_units_the_trace_runs_from_the_ones_it_never_runs(tmp_path):
    data = CLASSES.read_bytes()
    oracle = units.TraceOracle(bench.RUNTIME, EXPECTED)
    assert oracle.available() and oracle.run({"original": data}) == {"original": True}
    names = ["tag:6/method:%d" % i for i in (4, 5, 7, 8)]
    built = {item["id"]: item for item in units.splice_stage(request(tmp_path, data, "probe", [{"id": n, "parts": [{"id": n}]} for n in names]))}
    verdicts = oracle.run({name: Path(built[name]["file"]).read_bytes() for name in names})
    # the marker breaks the expected trace exactly when the method runs: the constructor and add() run, the accessors do not
    assert verdicts == {"tag:6/method:4": False, "tag:6/method:5": False, "tag:6/method:7": True, "tag:6/method:8": True}
    broken = bytearray(data)
    broken[data.index(b"PARITY_NAMESPACE_")] ^= 0x01
    assert oracle.run({"changed": bytes(broken), "same": data}) == {"changed": False, "same": True}
    assert units.TraceOracle(tmp_path / "missing.exe", EXPECTED).available() is False


# --- judging an engine: PySWF's own source, end to end ------------------------------------------------------------------

@needs_corpus
@needs_ruffle
def test_the_source_of_an_engine_is_judged_unit_by_unit_against_the_original_trace(tmp_path):
    data = CLASSES.read_bytes()
    canonical = canonical_units(data)
    plan = units.plan_case(data, canonical, 0)
    work = tmp_path / "native"
    work.mkdir()
    (work / "input.swf").write_bytes(data)
    suite = UniversalSuite(work / "state", work / "tools.json")
    try:
        suite.open(work / "input.swf")
        job = {"source": {"documents": save_documents(native_documents(suite, "native"), work),
                          "methods": save_documents(method_records("native", suite, None, plan["methods"]), work, "methods")}}
    finally:
        suite.close()
    case = {"family_id": "dev-as3-classes-namespaces", "relative": "swf/dev-as3-classes-namespaces/release.fws.swf", "language": "as3", "expected_trace": EXPECTED}
    rows = units.evaluate_case(case, data, canonical, plan, {"native": job}, bench.RUNTIME, tmp_path / "units", workers=2)
    assert len(rows) == 1
    row = rows[0]
    assert row["engine"] == "native" and row["measured"] and row["family"] == "dev-as3-classes-namespaces"
    assert row["units"]["different"] == 0 and row["units"]["no_compile"] == 0, row["details"]
    assert row["units"]["same"] >= 4 and row["units"]["not_run"] >= 3
    assert sum(row["units"].values()) == 10 == sum(sum(kind.values()) for kind in row["by_kind"].values())
    never_run = {detail["unit"] for detail in row["details"] if detail["outcome"] == "not_run"}
    assert {"tag:6/method:6", "tag:6/method:7", "tag:6/method:8"} <= never_run, "the accessors and label() are never called by the trace"
    assert units.line_counts(row["units"])["fidelity"] == 1.0


@needs_corpus
@needs_ruffle
def test_a_source_that_does_not_compile_or_behaves_differently_is_counted_against_its_engine(tmp_path):
    data = CLASSES.read_bytes()
    canonical = canonical_units(data)
    plan = units.plan_case(data, canonical, 0)
    work = tmp_path / "liar"
    (work / "methods").mkdir(parents=True)
    records = []
    texts = {"tag:6/method:5": "return 12345;",  # add(): executed, and it returns something else
             "tag:6/method:4": "this.total = local1;\nreturn;",  # the constructor without its super() call: refused by the compiler
             "tag:6/method:7": "return 0;"}  # the getter: never executed
    for index, (name, text) in enumerate(texts.items()):
        path = work / "methods" / ("%04d.as" % index)
        path.write_text(text, encoding="utf-8")
        records.append({"document_id": name, "scope": "method", "bytecode_sha256": canonical[(name, "method")]["sha"], "generation": "available", "source_file": str(path)})
    case = {"family_id": "f", "relative": "swf/x.swf", "language": "as3", "expected_trace": EXPECTED}
    row = units.evaluate_case(case, data, canonical, plan, {"liar": {"source": {"methods": records, "documents": []}}}, bench.RUNTIME, tmp_path / "units")[0]
    outcomes = {detail["unit"]: detail["outcome"] for detail in row["details"]}
    assert row["units"] == {"same": 0, "different": 1, "no_compile": 1, "not_run": 1}, row
    assert outcomes == {"tag:6/method:5": "different", "tag:6/method:4": "no_compile", "tag:6/method:7": "not_run"}
    assert "super" in next(d["reason"] for d in row["details"] if d["outcome"] == "no_compile")
    assert units.line_counts(row["units"])["fidelity"] == 0.0 and units.line_counts(row["units"])["judged"] == 1, "no_compile is not judged"


def test_source_with_the_wrong_hash_or_that_is_not_source_is_not_judged():
    canonical = {("tag:6/method:5", "method"): {"sha": "good", "methods": set()}, ("tag:6/class:1", "class"): {"sha": "c", "methods": {"tag:6/method:5"}},
                 ("tag:2/actions", "action"): {"sha": "a", "methods": set()}}
    job = {"source": {"methods": [
        {"document_id": "tag:6/method:5", "bytecode_sha256": "good", "generation": "available", "source_file": "m.as"},
        {"document_id": "tag:6/method:5", "bytecode_sha256": "other", "generation": "available", "source_file": "x.as"},
        {"document_id": "tag:6/method:6", "bytecode_sha256": "good", "generation": "available", "source_file": "y.as"},
        {"document_id": "tag:6/method:5", "bytecode_sha256": "good", "generation": "pcode", "source_file": "p.as"}],
        "documents": [{"document_id": "tag:6/class:1", "scope": "class", "bytecode_sha256": "c", "source_file": "c.as"},
                      {"document_id": "tag:6/class:1", "scope": "class", "bytecode_sha256": "wrong", "source_file": "d.as"},
                      {"document_id": "tag:2/actions", "scope": "action", "bytecode_sha256": "a", "source_file": "a.as"},
                      {"document_id": "tag:2/actions", "scope": "action", "bytecode_sha256": "a"}]}}
    methods, classes, actions = units.engine_sources(job, canonical)
    assert methods == {"tag:6/method:5": "m.as"} and classes == {"tag:6/class:1"} and actions == {"tag:2/actions": "a.as"}


# --- the table -----------------------------------------------------------------------------------------------------------

def row(engine, case, same=0, different=0, no_compile=0, not_run=0, measured=True, kinds=None, reason=""):
    counts = {"same": same, "different": different, "no_compile": no_compile, "not_run": not_run}
    return {"engine": engine, "family": "f", "case": case, "language": "as3", "measured": measured, **({"units": counts, "by_kind": kinds or {k: dict.fromkeys(counts, 0) for k in units.KINDS}, "details": []} if measured else {}), "reason": reason}


def test_the_table_counts_fidelity_by_kind_and_ranks_only_engines_judged_on_the_same_cases():
    kinds = lambda **found: {k: {"same": 0, "different": 0, "no_compile": 0, "not_run": 0, **found.get(k, {})} for k in units.KINDS}  # noqa: E731
    rows = [row("native", "a", same=6, not_run=3, kinds=kinds(method={"same": 4, "not_run": 3})),
            row("native", "b", same=1, different=1, no_compile=2, kinds=kinds(method={"same": 1, "different": 1, "no_compile": 2})),
            row("jpexs", "a", same=2, no_compile=2), row("jpexs", "b", same=0),
            row("flare", "a", same=1, kinds=kinds(action={"same": 1})), row("flare", "b", measured=False, reason="no lee AS3")]
    result = units.table(rows, {"units": 7, "reasons": {"cierres con activación": 7}})
    by = {line["engine"]: line for line in result["engines"]}
    assert (by["native"]["same"], by["native"]["different"], by["native"]["no_compile"], by["native"]["not_run"]) == (7, 1, 2, 3)
    assert (by["native"]["evaluated"], by["native"]["judged"]) == (13, 8) and by["native"]["fidelity"] == 7 / 8
    assert by["native"]["by_kind"]["method"]["fidelity"] == 5 / 6 and by["native"]["by_kind"]["class"]["fidelity"] is None
    assert by["jpexs"]["fidelity"] == 1.0 and by["jpexs"]["cases"] == 2
    assert by["flare"]["cases"] == 1 and by["flare"]["fidelity"] == 1.0 and by["flare"]["reasons"] == ["no lee AS3"]
    assert [by[e]["comparable"] for e in ("native", "jpexs", "flare")] == [True, True, False]
    assert [line["engine"] for line in result["engines"]] == ["jpexs", "native", "flare"]
    assert result["winner"] is None and "sin ganador" in result["note"], "a lead of 0.2 over 4 judged units is not one unit"
    assert result["corpus_cases"] == 2 and result["out_of_scope"]["units"] == 7 and set(result["kinds"]) == set(units.KINDS)


def test_an_engine_with_nothing_judged_has_no_fidelity_and_a_clear_lead_names_a_winner():
    rows = [row("native", "a", same=10), row("jpexs", "a", same=2, different=8), row("rust", "a", not_run=5)]
    result = units.table(rows)
    by = {line["engine"]: line for line in result["engines"]}
    assert by["rust"]["fidelity"] is None and by["rust"]["judged"] == 0 and by["rust"]["evaluated"] == 5
    assert result["winner"] == "native" and [line["engine"] for line in result["engines"]][-1] == "rust"
    assert json.loads(json.dumps(result)) == result


def test_an_engine_that_never_ran_is_a_row_with_its_reason_and_no_cases():
    result = units.table([row("native", "a", same=1), row("pyflashkit", "a", measured=False, reason="no lee AS2")])
    line = next(l for l in result["engines"] if l["engine"] == "pyflashkit")
    assert line["cases"] == 0 and line["fidelity"] is None and line["reasons"] == ["no lee AS2"]
