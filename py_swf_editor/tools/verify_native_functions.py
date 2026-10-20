"""Trusted real corpus: closure behavior, P-code edit/export/reopen and UI.

No Java or original .as files are used by reconstruction. V8 verification is
limited to selected JS-compatible fixtures, not a complete Flash runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from py_swf.decompile.avm1_dec import decompile_avm1
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.engines import NativePythonEngine, EngineOperationError
from py_swf.storage import write_swf
from py_swf.swf_parser import SWFFile, SWFTag
from tests.native_control_vm import run_bytes
from tests.native_source_subset import run_trusted_subset
from tools.verify_native_decompile import locate


def verify(corpus, folder):
    rows = []
    for variant in ("flex", "air"):
        path = corpus / f"as3_new/bin/as3_new.{variant}.swf"
        data = path.read_bytes()
        movie = SWFFile()
        movie.read_bytes(data)
        for method in ("TestInnerFunctions.run", "TestInlineFunctions.run", "TestInlineFunctions2.run"):
            tag, index, abc, body = locate(movie, method)
            text, error = decompile_method(abc, body)
            assert error is None, error
            args = ["hello"] if method == "TestInnerFunctions.run" else []
            before = run_bytes(body.code, "as3", abc=abc, body=body, arguments=args)
            after = run_trusted_subset(text, variables={"a": "hello"} if args else None)
            assert not before.raised and before.traces == after["traces"]
            source_file = folder / f"{variant}-{method}.as"
            source_file.write_text(text, encoding="utf-8")
            rows.append(dict(swf=str(path), sha256=hashlib.sha256(data).hexdigest(), tag=tag, body=index, method=method, behavior_equivalent=True, source=str(source_file)))
        assert path.read_bytes() == data
    engine = NativePythonEngine()
    edits = []
    for relative, tag, body_index, old, new, root_method in [("as2/as2.swf", 292, 0, "hello ", "updated ", None), ("as3_new/bin/as3_new.flex.swf", None, None, "value2", "updated", "TestInlineFunctions.run")]:
        path = corpus / relative
        original = path.read_bytes()
        if root_method:
            movie=SWFFile();movie.read_bytes(original)
            tag, _, _, _ = locate(movie,root_method)
        scripts = engine.disassemble(original, tag)["scripts"]
        if root_method:
            matches=[script['body_index'] for script in scripts
                     if any('pushstring ' in line and line.endswith('// '+json.dumps(old))
                            for line in script['code'].splitlines())]
            assert len(matches)==1, f"Expected one {root_method} closure with literal {old!r}: {matches}"
            body_index=matches[0]
        assembly = next(s["code"] for s in scripts if s["body_index"] == body_index)
        lines = assembly.splitlines()
        changed = False
        for i, line in enumerate(lines):
            if "pushstring " in line and line.endswith("// " + json.dumps(old)):
                lines[i] = "pushstring " + json.dumps(new)
                changed = True
            elif root_method is None and json.dumps(old) in line:
                lines[i] = line.replace(json.dumps(old), json.dumps(new))
                changed = True
        assert changed, assembly
        output = folder / ("edited-as3.swf" if root_method else "edited-as2.swf")
        edited_assembly="\n".join(lines)
        baseline=engine.validate(original)
        edit_input=original
        isolated=False
        full_export_rejected=False
        full_export_error=None
        if not baseline['valid']:
            # Keep the strict failure visible. This fixture contains unrelated
            # methods the current verifier rejects; it is not evidence that a
            # Flash runtime rejects the original program.
            assert root_method=='TestInlineFunctions.run', baseline['errors']
            try:
                engine.assemble(original,tag,edited_assembly,body_index)
            except EngineOperationError as error:
                full_export_rejected=True
                full_export_error=str(error)
            else:
                raise AssertionError('Strictly rejected corpus SWF was unexpectedly exportable')
            assert not output.exists() and path.read_bytes()==original
            library=SWFFile()
            library.signature,library.version=movie.signature,movie.version
            library.rect,library.frame_rate=movie.rect,movie.frame_rate
            # The complete real ABC is unchanged; only its host container is
            # isolated for the bounded method-edit/export contract.
            attributes=next((t for t in movie.tags if t.tag_type==69),SWFTag(69,b'\x08\0\0\0'))
            background=next((t for t in movie.tags if t.tag_type==9),SWFTag(9,b'\x12\x34\x56'))
            library.tags=[attributes,background,movie.tags[tag],SWFTag(1,b''),SWFTag(0,b'')]
            edit_input=library.save_bytes()
            assert engine.validate(edit_input)['valid']
            assert library.tags[2].data==movie.tags[tag].data
            tag=2
            isolated=True
        candidate = engine.assemble(edit_input,tag,edited_assembly,body_index)
        write_swf(output, candidate)
        before, after = SWFFile(), SWFFile()
        before.read_bytes(edit_input)
        after.read_bytes(output.read_bytes())
        assert len(before.tags) == len(after.tags)
        assert all(a.data == b.data and a.tag_type == b.tag_type for i, (a, b) in enumerate(zip(before.tags, after.tags)) if i != tag)
        if root_method:
            _, _, abc, body = locate(after, root_method)
            text, error = decompile_method(abc, body)
            result = run_bytes(body.code, "as3", abc=abc, body=body)
            assert result.traces == ["updatedcc", "hello,hello"]
        else:
            text, error = decompile_avm1(after.tags[tag].data)
            result = run_bytes(after.tags[tag].data, "as2")
            assert result.traces == ["functionTest", "updated friend! 7"]
        assert error is None and '"updated' in text
        assert path.read_bytes() == original
        edits.append(dict(swf=relative,original_sha256=hashlib.sha256(original).hexdigest(),
                          edit_input_sha256=hashlib.sha256(edit_input).hexdigest(),output=str(output),
                          edit_mode="P-code",saved_reopened=True,other_tags_preserved=True,
                          other_tags_scope="isolated ABC library fixture" if isolated else "complete original SWF",
                          source_snapshot_isolated=isolated,original_preserved=True,traces=result.traces,
                          baseline_validation_valid=baseline['valid'],baseline_errors=baseline['errors'],
                          full_source_export_rejected=full_export_rejected,full_source_export_error=full_export_error,
                          baseline_status="strict validator rejects current real SWF" if not baseline['valid'] else "strict validation passed"))
    return dict(java_used=False, engine="native", semantic_cases=rows, real_pcode_edits=edits, artifacts=str(folder), limits="Selected trusted JavaScript-compatible fixtures, not a Flash runtime. When the current strict validator rejects unrelated methods in the real corpus SWF, full export stays blocked and the complete selected real ABC is tested in an isolated library fixture. Dynamic with scopes and opaque pre-existing AVM2 activation metadata remain protected.")


def browser_verify(url, corpus, folder):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=dict(width=1500, height=1000))
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        expect.set_options(timeout=30000)
        try:
            page.goto(url, wait_until="networkidle")
            # Read-back DOM before relying on the explorer's existing titles.
            assert page.get_by_label("Abrir archivo SWF", exact=True).count() == 1
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files(str((corpus / "as3_new/bin/as3_new.flex.swf").resolve()))
            expect(page.get_by_role("heading", name="as3_new.flex.swf", exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("146")
            expect(page.locator('.tree-tag[title$="tag #146"]')).to_be_visible()
            assert any(title.endswith("tag #146") for title in page.locator(".tree-tag").evaluate_all("items=>items.map(e=>e.title)"))
            page.locator('.tree-tag[title$="tag #146"]').click()
            page.get_by_label("Sección de fuente", exact=True).select_option(label="tests::TestInlineFunctions.run")
            code = page.locator(".code-lines")
            expect(code).to_contain_text("function(aParam:String)")
            expect(code).to_contain_text("function(bParam:String)")
            expect(code).to_contain_text("first + second")
            expect(page.get_by_role("button", name="Editar fuente", exact=True)).to_be_disabled()
            expect(page.locator(".script-help")).to_contain_text("Fuente reconstruida de lectura")
            page.screenshot(path=str(folder / "real-as3-closures.png"))
            page.get_by_label("Abrir archivo SWF", exact=True).set_input_files(str((corpus / "as2/as2.swf").resolve()))
            expect(page.get_by_role("heading", name="as2.swf", exact=True)).to_be_visible()
            page.get_by_label("Buscar recursos", exact=True).fill("544")
            page.locator('.tree-tag[title$="tag #544"]').click()
            expect(code).to_contain_text("with (_root.something)")
            expect(code).to_contain_text("with (subvar)")
            expect(page.get_by_role("button", name="Editar fuente", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="Reemplazar cuerpo AS", exact=True)).to_be_enabled()
            expect(page.locator(".script-help")).to_contain_text("Fuente reconstruida de lectura")
            page.screenshot(path=str(folder / "real-as2-with.png"))
            assert not errors, errors
            return dict(passed=True, console_errors=errors)
        except Exception:
            page.screenshot(path=str(folder / "browser-failure.png"))
            raise
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    from py_swf.corpus import corpus_root
    parser.add_argument("--corpus", type=Path, default=corpus_root())
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path, default=Path("reports/native-functions-real-swf.json"))
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix="pyswf-native-functions-"))
    report = verify(args.corpus, folder)
    if args.url:
        report["browser"] = browser_verify(args.url, args.corpus, folder)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "semantic_cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
