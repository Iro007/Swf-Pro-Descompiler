"""Closeness to the reference decompiler: text similarity per verified unit, plus coverage."""
from py_swf import closeness
from py_swf.closeness import compare, normalize, similarity

A = '''/* banner */
package a {
  public class A {
    // comment
    function f():void { trace('hi'); var x:Number = 1.0; }
  }
}
'''
B = 'package a{public class A{function f():void{trace("hi");var x:Number=1;}}}'


def test_comments_whitespace_quotes_and_number_form_are_ignored():
    assert normalize(A) == normalize(B)
    assert similarity(A, B) == 1.0
    assert normalize('"// not a comment"') == ["S:// not a comment"]


def test_different_source_scores_below_one_and_empties_are_defined():
    assert 0.0 < similarity(A, B.replace("trace", "print")) < 1.0
    assert similarity("", "// only a comment") == 1.0
    assert similarity("", A) == 0.0 and similarity(A, "") == 0.0


def fake_inventory(monkeypatch, units):
    monkeypatch.setattr(closeness, "build_inventory", lambda data: {"units": [
        {"document_id": d, "scope": s, "bytecode_sha256": h, "identity_status": "byte_derived",
         "context": {"method_indices": [1, 2]}} for d, s, h in units]})


def unit(doc, sha, source, scope="class"):
    return {"document_id": doc, "scope": scope, "bytecode_sha256": sha, "source": source}


def test_alignment_drops_bytecode_mismatches_and_unknown_units(monkeypatch):
    fake_inventory(monkeypatch, [("c0", "class", "h0"), ("c1", "class", "h1"), ("c2", "class", "h2")])
    reference = [unit("c0", "h0", A), unit("c1", "h1", A), unit("c2", "WRONG", A), unit("zz", "hz", A)]
    engine = [unit("c0", "h0", B), unit("c1", "BAD", A), unit("c2", "h2", A)]
    result = compare(engine, reference, b"")
    assert result["reference_units"] == 2 and result["matched"] == 1
    assert result["excluded"] == {"engine": 1, "reference": 2}
    assert result["per_unit"] == [{"document_id": "c0", "scope": "class", "similarity": 1.0}]


def test_coverage_and_weighted_arithmetic(monkeypatch):
    fake_inventory(monkeypatch, [("c0", "class", "h0"), ("c1", "class", "h1"), ("c2", "class", "h2"), ("c3", "class", "h3")])
    reference = [unit(f"c{i}", f"h{i}", A) for i in range(4)]
    engine = [unit("c0", "h0", A), unit("c1", "h1", "")]
    result = compare(engine, reference, b"")
    assert result["coverage"] == 0.5 and result["closeness"] == 0.5 and result["weighted"] == 0.25


def test_scope_must_match_and_nothing_to_compare_gives_none(monkeypatch):
    fake_inventory(monkeypatch, [("c0", "class", "h0")])
    result = compare([unit("c0", "h0", A, scope="script")], [unit("c0", "h0", A)], b"")
    assert result["matched"] == 0 and result["closeness"] is None and result["coverage"] == 0.0
    empty = compare([], [], b"")
    assert empty["coverage"] is None and empty["weighted"] is None


def test_the_units_of_the_swf_that_can_be_documents_are_counted_without_any_reference_decompiler(monkeypatch):
    fake_inventory(monkeypatch, [("c0", "class", "h0"), ("s0", "script", "hs"), ("a0", "action", "ha"), ("m0", "method", "hm")])
    canonical = closeness.canonical_units(b"")
    assert set(canonical) == {("c0", "class"), ("s0", "script"), ("a0", "action"), ("m0", "method")}
    engine = [unit("c0", "h0", A), unit("s0", "WRONG", A, scope="script"), unit("a0", "ha", None, scope="action"), unit("zz", "hz", A)]
    assert closeness.inventory_coverage(engine, canonical) == {"units": 3, "with_source": 1}, "methods are inside their class, a wrong hash or no source is not a unit"
    assert closeness.inventory_coverage([], canonical) == {"units": 3, "with_source": 0}


def test_compare_can_reuse_the_units_of_the_swf_instead_of_building_the_inventory_again(monkeypatch):
    fake_inventory(monkeypatch, [("c0", "class", "h0")])
    canonical = closeness.canonical_units(b"")

    def refuse(data):
        raise AssertionError("the inventory was built a second time")
    monkeypatch.setattr(closeness, "build_inventory", refuse)
    assert compare([unit("c0", "h0", A)], [unit("c0", "h0", A)], b"", canonical)["closeness"] == 1.0


def test_documents_without_a_hash_are_verified_by_the_methods_they_own(monkeypatch):
    fake_inventory(monkeypatch, [("tag:3/class:0", "class", "h")])
    reference = [unit("tag:3/class:0", "h", A)]
    owned = {"document_id": "tag:3/class:0", "scope": "class", "source": B, "unit_ids": ["tag:3/method:1", "tag:3/method:2"]}
    assert compare([owned], reference, b"")["matched"] == 1
    wrong = {**owned, "unit_ids": ["tag:3/method:1"]}
    result = compare([wrong], reference, b"")
    assert result["matched"] == 0 and result["excluded"]["engine"] == 1
