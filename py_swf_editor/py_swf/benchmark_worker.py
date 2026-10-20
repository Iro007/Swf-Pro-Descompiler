"""Fresh-parse JSONL worker for development benchmarks (never executes SWF code)."""
from __future__ import annotations

import base64
from copy import copy, deepcopy
import hashlib
import json
import sys
import time

from .avm1 import disassemble_avm1
from .avm2 import disassemble_instructions
from .bytecode_validation import decode_avm2
from .compiler.as_compiler import Parser, tokenize
from .decompile.avm1_dec import decompile_avm1
from .decompile.avm2_dec import decompile_method
from .decompile.as3_outline import outline_abc
from .decompile.lift import avm1_instructions
from .editing import source_edit_reason
from .script_inventory import enumerate_scripts
from .symbols import MethodBindings
from .swf_parser import SWFFile
from .canonical_inventory import build_inventory, attach_observations


def count_actions(code, depth=0):
    if depth > 32:
        raise ValueError("Nested action instruction-count limit exceeded")
    instructions = avm1_instructions(code)
    return len(instructions) + sum(count_actions(i.args[1],depth+1) for i in instructions if i.op in ("define_function", "define_function2", "with"))


def decompile(data, include_source=False, operation="source"):
    start = time.perf_counter()
    swf = SWFFile()
    swf.read_bytes(data)
    units = list(enumerate_scripts(data, include_nested_avm1=True))
    inventory = build_inventory(data)
    canonical_by_id = {u['document_id']:u for u in inventory['units']}
    syntax_abcs = {}
    from .abc_symbols import AbcSymbolIndex
    for unit in units:
        if unit.language == 'as3' and not hasattr(unit.abc, '_native_symbol_index'):
            unit.abc._native_symbol_index = AbcSymbolIndex([unit.abc])
    parse_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    results, documents, seen_abc = [], [], set()
    for unit in units:
        record = unit.metadata()
        record.update(syntax="not_checked", compilation="not_run", swf_validation="not_run", behavior="not_run", source_fidelity="not_verified")
        if unit.diagnostic:
            record["generation"] = "failed"
            results.append(record)
            continue
        try:
            if unit.kind == "function" and operation != "disassemble":
                # The inventory proves bytes and declaration-time context.
                # An isolated child has no proven outer scope/activation; keep
                # its exact P-code instead of claiming reconstructed source.
                text = disassemble_avm1(unit.code)
                record.update(generation="pcode", compiler_eligible=False,
                              diagnostic="Isolated AVM1 function source requires captured runtime scope verification",
                              source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                              source_characters=len(text), source_utf8_bytes=len(text.encode("utf-8")))
                if include_source:
                    record["source"] = text
                results.append(record)
                continue
            if operation == "disassemble":
                text = disassemble_instructions(unit.abc.constant_pool,unit.code,exact=True,exceptions=unit.body.exceptions) if unit.language=="as3" else disassemble_avm1(unit.code)
                record.update(generation="disassembly",disassembly="available",source_fidelity="not_applicable",
                              source_sha256=hashlib.sha256(text.encode()).hexdigest(),source_characters=len(text))
                if include_source:
                    record["source"]=text
                results.append(record)
                continue
            if unit.language == "as3":
                from .decompile.script_initializers import script_declaration_owner
                declaration_owner=script_declaration_owner(unit.abc,unit.body)
                text, error = decompile_method(unit.abc, unit.body)
                instruction_count = len(decode_avm2(unit.code))
                if error:
                    text = disassemble_instructions(unit.abc.constant_pool, unit.code, exact=True, exceptions=unit.body.exceptions)
                reason = source_edit_reason(unit.abc, unit.body, symbol_index=unit.abc._native_symbol_index)
                if unit.tag_path not in seen_abc:
                    seen_abc.add(unit.tag_path)
                    outline = outline_abc(unit.abc)
                    documents.append({"document_id": "tag:" + "/".join(map(str, unit.tag_path)) + "/outline", "generation": "outline_only", "source_sha256": hashlib.sha256(outline.encode()).hexdigest(), **({"source": outline} if include_source else {})})
                    from .class_documents import reconstruct_abc_documents
                    for document in reconstruct_abc_documents(unit.abc):
                        scope = document['scope']
                        index = document.get('class_index') if scope == 'class' else document.get('script_index')
                        document_id = 'tag:' + '/'.join(map(str, unit.tag_path)) + f'/{scope}:{index}'
                        canonical = canonical_by_id.get(document_id)
                        if canonical is None:
                            continue
                        source = document.get('source') or ''
                        record_document = {**canonical,
                            'generation': 'available' if document['completeness'] == 'complete' else 'partial_source',
                            'completeness': document['completeness'], 'diagnostics': document.get('diagnostics', []),
                            'diagnostic': document.get('error'), 'source_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                            'source_characters': len(source), 'source_utf8_bytes': len(source.encode('utf-8')),
                            'compiler_eligible': False, 'compiler_eligibility': document.get('compiler_eligibility'),
                            'syntax': 'not_checked', 'compilation': 'not_run', 'behavior': 'not_run',
                            'swf_validation': 'not_run', 'source_fidelity': 'not_verified'}
                        if include_source:
                            record_document['source'] = source
                        results.append(record_document)
                        documents.append(record_document)
            else:
                declaration_owner=None
                text, error = decompile_avm1(unit.code)
                instruction_count = count_actions(unit.code)
                reason = None
            record.update(generation="pcode" if error else "available", diagnostic=error, instruction_count=instruction_count, source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), source_characters=len(text), source_utf8_bytes=len(text.encode("utf-8")))
            record['source_kind']='pcode' if error else 'method_body' if unit.language=='as3' else 'action_source'
            if declaration_owner is not None:
                document_id='tag:'+'/'.join(map(str,unit.tag_path))+f'/script:{declaration_owner}'
                record.update(generation='represented_by_document',represented_by_document=document_id,
                              source_document=document_id,pcode_available=True,source_kind='pcode')
                if include_source:record['pcode']=text
            if not error:
                try:
                    # Syntax checks must use the unit's dialect and a copied
                    # binding context. Runtime-name parsing may append pool
                    # entries; never mutate the freshly parsed input ABC here.
                    if unit.language == 'as3':
                        if unit.tag_path not in syntax_abcs:
                            syntax_abc=copy(unit.abc)
                            syntax_abc.constant_pool=deepcopy(unit.abc.constant_pool)
                            syntax_abcs[unit.tag_path]=syntax_abc
                        syntax_abc=syntax_abcs[unit.tag_path]
                        context=MethodBindings(syntax_abc,unit.body,symbol_index=unit.abc._native_symbol_index)
                        pool_lengths={name:len(value) for name,value in vars(syntax_abc.constant_pool).items() if isinstance(value,list)}
                    else:
                        context=None
                    try:
                        Parser(tokenize(text),unit.language,context).parse_program()
                    finally:
                        if context is not None:
                            for name,length in pool_lengths.items():
                                del getattr(syntax_abc.constant_pool,name)[length:]
                    record["syntax"] = "native_subset_supported"
                except (SyntaxError, ValueError) as unsupported:
                    record["syntax"] = "native_subset_unsupported"
                    reason = reason or str(unsupported)
            record["compiler_eligible"] = not (error or reason or len(unit.tag_path) != 1 or unit.kind not in ("method", "frame", "init"))
            record["source_edit_reason"] = reason
            if include_source:
                record["source"] = text
        except Exception as error:
            record.update(generation="failed", diagnostic=str(error))
        results.append(record)
    generate_ms = (time.perf_counter() - start)*1000
    canonical_start = time.perf_counter()
    results = attach_observations(inventory, results, engine='native', adapter_mode='native_byte_units')
    parse_ms += (time.perf_counter() - canonical_start)*1000
    return {"ok": True, "engine": "native", "parse_ms": parse_ms, "generate_ms": generate_ms,
            "canonical_inventory": inventory, "canonical_inventory_sha256": inventory['sha256'],
            "inventory_status": 'verified' if all(u['identity_verified'] for u in results) else 'disagreement',
            "adapter_mode": 'native_byte_units', "include_scope": sorted({u['scope'] for u in results}),
            "eligible_units": len(units), "units": results, "unit_observations": results, "documents": documents, "signature": swf.signature, "swf_version": swf.version, "tags": len(swf.tags), "decoded_script_bytes": sum(len(u.code) for u in units)}


def handle(request):
    if request.get("operation") == "ping":
        return {"ok": True, "engine": "native", "python": sys.version.split()[0]}
    if request.get("operation") not in ("decompile", "compile", "disassemble"):
        raise ValueError("Unsupported benchmark worker operation")
    start = time.perf_counter()
    data = base64.b64decode(request["swf_base64"], validate=True)
    decode_ms = (time.perf_counter() - start)*1000
    if request["operation"] == "compile":
        from .engines import NativePythonEngine
        from .validation import validate_swf_bytes
        unit = next((u for u in enumerate_scripts(data) if u.document_id == request["document_id"]), None)
        if unit is None or len(unit.tag_path) != 1 or unit.kind not in ("method", "frame", "init"):
            raise ValueError("Document is not supported by the current native compiler")
        candidate = NativePythonEngine().compile_source(data, unit.tag_path[0], request["source"], unit.body_index or 0)
        validation = validate_swf_bytes(candidate)
        if not validation["valid"]:
            raise ValueError("Native candidate failed SWF validation")
        reopened = SWFFile()
        reopened.read_bytes(candidate)
        return {"ok": True, "engine": "native", "compilation": "passed", "swf_reopened": True, "swf_validation": "passed", "behavior": "not_run", "swf_base64": base64.b64encode(candidate).decode()}
    result = decompile(data, request.get("include_source", False), "disassemble" if request["operation"]=="disassemble" else "source")
    result["input_decode_ms"] = decode_ms
    return result


def main():
    protocol = sys.stdout
    sys.stdout = sys.stderr
    for line in sys.stdin:
        request_id = ""
        try:
            request = json.loads(line)
            request_id = request.get("request_id", "")
            result = handle(request)
        except Exception as error:
            result = {"ok": False, "diagnostic": str(error), "engine": "native"}
        result["request_id"] = request_id
        start = time.perf_counter()
        json.dumps(result, ensure_ascii=False)
        result["serialize_ms"] = (time.perf_counter()-start)*1000
        protocol.write(json.dumps(result, ensure_ascii=False) + "\n")
        protocol.flush()


if __name__ == "__main__":
    main()
