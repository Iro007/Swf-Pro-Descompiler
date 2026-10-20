"""Reproducible, Python-only verification on curated real SWFs.

The bounded reference interpreter is test-only, not a production Flash player.
Source reference files are recorded as provenance, never fed to the decompiler.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from py_swf.avm1 import assemble_avm1
from py_swf.avm2 import ABCFile, MethodInfo, MethodBodyInfo, assemble_instructions, build_method_mapping
from py_swf.compiler import compile_actionscript
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.editing import source_edit_reason
from py_swf.engines import NativePythonEngine
from py_swf.storage import write_swf
from py_swf.swf_parser import SWFFile
from tests.native_control_vm import run_bytes, FlashError, Thrown


def compiled(text, target):
    assembly = compile_actionscript(text, target)
    if target == "as2":
        return assemble_avm1(assembly), None, None
    abc, body = ABCFile(), MethodBodyInfo()
    abc.methods = [MethodInfo()]
    body.code = assemble_instructions(abc.constant_pool, assembly, exception_table=body.exceptions)
    return body.code, abc, body


def behavior(code, target, abc=None, body=None):
    result = run_bytes(code, target, abc=abc, body=body)
    return result.traces, result.value, str(result.error) if result.raised else None, result.raised


def locate(movie, suffix):
    for tag_index, tag in enumerate(movie.tags):
        if tag.is_doabc:
            abc = ABCFile()
            abc.parse(tag.parse_doabc()[2])
            mapping = build_method_mapping(abc)
            for body_index, body in enumerate(abc.method_bodies):
                if mapping.get(body.method, "").endswith(suffix):
                    return tag_index, body_index, abc, body
    raise AssertionError("Missing real corpus method: " + suffix)


def verify(corpus, folder):
    selected = [("as2/as2.swf", tag, None) for tag in (261, 271, 283, 289, 317)]
    selected += [(f"as3_new/bin/as3_new.{variant}.swf", None, test + ".run") for variant in ("flex", "air") for test in ("TestFor", "TestForBreak", "TestForContinue", "TestSwitch", "TestSwitchDefault")]
    selected += [("as3_cross_compile/bin/as3_cross_compile.flex.swf", None, test + ".run") for test in ("TestTryCatch", "TestTryFinally", "TestTryFinallyReturn", "TestTryFinallyNoCatch")]
    engine, rows = NativePythonEngine(), []
    for ordinal, (relative, tag_index, method) in enumerate(selected):
        original = (corpus / relative).read_bytes()
        movie = SWFFile()
        movie.read_bytes(original)
        target = "as3" if method else "as2"
        body_index, abc, body = 0, None, None
        if method:
            tag_index, body_index, abc, body = locate(movie, method)
            source, error = decompile_method(abc, body)
            code = body.code
            reference = corpus / ("as3_new/src/tests/" if relative.startswith("as3_new") else "as3_cross_compile/src/tests/") / (method.removesuffix(".run") + ".as")
        else:
            code = movie.tags[tag_index].data
            source, error = decompile_avm1(code)
            reference = corpus / "as2/as2/DOMDocument.xml"
        assert error is None, f"{relative} {method or tag_index}: {error}"
        new_code, new_abc, new_body = compiled(source, target)
        assert behavior(code, target, abc, body) == behavior(new_code, target, new_abc, new_body)
        source_path = folder / f"{ordinal:02d}-{method or tag_index}.as"
        source_path.write_text(source, encoding="utf-8")
        reason = source_edit_reason(abc, body) if method else None
        full_edit = reason is None
        saved = engine.compile_source(original, tag_index, source, body_index) if full_edit else original
        saved_path = folder / f"{ordinal:02d}.swf"
        write_swf(saved_path, saved)
        reopened = SWFFile()
        reopened.read_bytes(saved_path.read_bytes())
        assert len(movie.tags) == len(reopened.tags)
        assert all(a.tag_type == b.tag_type and (a.data == b.data or i == tag_index and full_edit) for i, (a, b) in enumerate(zip(movie.tags, reopened.tags)))
        if method:
            parsed = ABCFile()
            parsed.parse(reopened.tags[tag_index].parse_doabc()[2])
            after_source, after_error = decompile_method(parsed, parsed.method_bodies[body_index])
            after_code, after_abc, after_body = parsed.method_bodies[body_index].code, parsed, parsed.method_bodies[body_index]
        else:
            after_code, after_abc, after_body = reopened.tags[tag_index].data, None, None
            after_source, after_error = decompile_avm1(after_code)
        assert after_error is None, after_error
        assert behavior(after_code, target, after_abc, after_body) == behavior(code, target, abc, body)
        assert (corpus / relative).read_bytes() == original
        row = {"swf": relative, "sha256": hashlib.sha256(original).hexdigest(), "tag_index": tag_index,
               "method": method, "language": target, "structured": True, "behavior_roundtrip": True,
               "saved_reopened": True, "full_movie_source_edit": full_edit, "source_edit_guard": reason,
               "other_tags_preserved": True, "original_preserved": True, "source": str(source_path),
               "source_reference": str(reference.relative_to(corpus)), "reference_used_by_decompiler": False}
        if method and "Try" in method:
            injected = FlashError("injected")
            def run_injected(data, abc_file, method_body):
                events = []
                def trace(value):
                    events.append(value)
                    if value == "in try":
                        raise Thrown(injected)
                result = run_bytes(data, "as3", abc=abc_file, body=method_body, variables={"trace": trace})
                return events, result.value, result.error, result.raised
            assert run_injected(code, abc, body) == run_injected(new_code, new_abc, new_body)
            row["injected_exception_equivalent"] = True
        rows.append(row)
        print(f"PASS {target} {relative} {method or ('tag ' + str(tag_index))}", flush=True)
    return {"engine": "native", "java_or_jpexs_executed": False, "samples": len(rows), "files": len({r['swf'] for r in rows}),
            "full_movie_source_edits": sum(r["full_movie_source_edit"] for r in rows), "cases": rows,
            "artifacts": str(folder), "runtime_limit": "Bounded test interpreter, not a complete Flash runtime"}


def main():
    parser = argparse.ArgumentParser()
    from py_swf.corpus import corpus_root
    parser.add_argument("--corpus", type=Path, default=corpus_root())
    parser.add_argument("--output", type=Path, default=Path("reports/native-decompile-real-swf.json"))
    parser.add_argument("--url", help="Also verify real source display in the running local UI")
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix="pyswf-decompile-real-"))
    report = verify(args.corpus, folder)
    if args.url:
        report["real_browser"] = verify_browser(args.url, args.corpus, folder)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))


def verify_browser(url, corpus, folder):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        expect.set_options(timeout=30000)
        try:
            page.goto(url, wait_until="networkidle")
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files(str((corpus / "as3_cross_compile/bin/as3_cross_compile.flex.swf").resolve()))
            expect(page.get_by_role("heading", name="as3_cross_compile.flex.swf", exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("DoABC")
            # The actual explorer advertises tag identity in title, including
            # resource names. This selector is based on the existing DOM.
            page.locator('.tree-tag[title$="tag #22"]').click()
            page.get_by_label("Sección de fuente", exact=True).select_option(label="tests::TestTryFinally.run")
            expect(page.locator(".code-lines")).to_contain_text("try {")
            expect(page.locator(".code-lines")).to_contain_text("catch (e:Error)")
            expect(page.locator(".code-lines")).to_contain_text("finally {")
            expect(page.locator(".code-lines")).to_contain_text('trace("after")')
            expect(page.get_by_role("button", name="Editar fuente", exact=True)).to_be_disabled()
            page.screenshot(path=str(folder / "real-as3-finally.png"))
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files(str((corpus / "as2/as2.swf").resolve()))
            expect(page.get_by_role("heading", name="as2.swf", exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("DoAction")
            page.locator('.tree-tag[title$="tag #317"]').click()
            expect(page.locator(".code-lines")).to_contain_text("try {")
            expect(page.locator(".code-lines")).to_contain_text("finally {")
            expect(page.locator(".code-lines")).to_contain_text("Infinity")
            expect(page.get_by_role("button", name="Editar fuente", exact=True)).to_be_enabled()
            page.screenshot(path=str(folder / "real-as2-finally.png"))
            assert not errors, errors
            print("PASS real AS2/AS3 structured source in browser", flush=True)
            return True
        except Exception:
            page.screenshot(path=str(folder / "real-browser-failure.png"))
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
