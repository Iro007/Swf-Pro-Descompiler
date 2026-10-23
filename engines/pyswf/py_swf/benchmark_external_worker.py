"""Isolated CLI adapters. Every operation uses a new SWF copy and output folder."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import shutil
import uuid
import re

from .benchmark_engines import ENGINES, checked_tool
from .script_inventory import enumerate_scripts
from .swf_parser import SWFFile
from .canonical_inventory import build_inventory, attach_observations


# Run the official entry point under the external venv's isolated interpreter.
# No import of flashkit takes place inside the PySWF application environment.
FLASHKIT_CLI = "from flashkit.cli import main; main()"
FLASHKIT_API_ALL_ABC = r'''
from pathlib import Path
import hashlib, json, sys
from flashkit.workspace import Workspace
from flashkit.decompile import decompile_class, decompile_method, list_classes
from flashkit.abc import decode_instructions, resolve_instructions
ws = Workspace()
resource = ws.load_swf(sys.argv[1])
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
operation = sys.argv[3]
blocks = []
for tag_index, tag in enumerate(resource.swf_tags):
    raw = None
    if tag.tag_type == 72:
        raw = tag.payload
    elif tag.tag_type == 82:
        end = tag.payload.index(0, 4)
        raw = tag.payload[end + 1:]
    if raw:
        blocks.append((tag_index, hashlib.sha256(raw).hexdigest()))
rows = []
for abc_index, abc in enumerate(ws.abc_blocks):
    tag_index, abc_hash = blocks[abc_index]
    folder = out / ('tag-%d-abc-%d-%s' % (tag_index, abc_index, abc_hash[:12]))
    folder.mkdir()
    by_method = {body.method: body for body in abc.method_bodies}
    if operation == 'source':
        for summary in list_classes(abc):
            idx = summary.index
            inst, cls = abc.instances[idx], abc.classes[idx]
            methods = {inst.iinit, cls.cinit}
            for trait in list(inst.traits) + list(cls.traits):
                if trait.kind in (1, 2, 3): methods.add(trait.method_idx)
                elif trait.kind == 5: methods.add(trait.function_idx)
            aggregate = b''.join(len(by_method[m].code).to_bytes(4, 'little') + by_method[m].code for m in sorted(methods) if m in by_method)
            row = {'document_id': 'tag:%d/class:%d' % (tag_index, idx), 'scope': 'class', 'abc_sha256': abc_hash,
                   'class_index': idx, 'name': summary.full_name, 'bytecode_sha256': hashlib.sha256(aggregate).hexdigest(), 'bytecode_bytes': len(aggregate)}
            try:
                source = decompile_class(abc, class_index=idx)
                if not source.strip(): raise ValueError('Empty class source')
                file = folder / ('class-%d.as' % idx); file.write_text(source, encoding='utf-8')
                row.update(generation='available', source_sha256=hashlib.sha256(source.encode()).hexdigest(), output_file=file.relative_to(out).as_posix())
            except Exception as error:
                row.update(generation='failed', diagnostic=str(error))
            rows.append(row)
    for body_index, body in enumerate(abc.method_bodies):
        row = {'document_id': 'tag:%d/method:%d' % (tag_index, body.method), 'scope': 'method',
               'abc_sha256': abc_hash, 'method_index': body.method, 'body_index': body_index,
               'bytecode_sha256': hashlib.sha256(body.code).hexdigest(), 'bytecode_bytes': len(body.code)}
        try:
            if operation == 'source':
                source = decompile_method(abc, method_idx=body.method, include_signature=False)
                generation, suffix = 'available', '.as'
            else:
                instructions = resolve_instructions(abc, decode_instructions(body.code))
                source = '\n'.join(str(item) for item in instructions)
                generation, suffix = 'disassembly', '.asm'
            if not source.strip(): raise ValueError('Empty method output')
            file = folder / ('method-%d-body-%d%s' % (body.method, body_index, suffix))
            file.write_text(source, encoding='utf-8')
            row.update(generation=generation, source_sha256=hashlib.sha256(source.encode()).hexdigest(), output_file=file.relative_to(out).as_posix())
            row['source_kind']='method_body' if operation=='source' else 'instruction_listing'
            if operation != 'source': row['disassembly'] = 'available'
        except Exception as error:
            row.update(generation='failed', diagnostic=str(error))
        rows.append(row)
(out / 'observations.json').write_text(json.dumps({'rows': rows, 'abc_blocks': len(ws.abc_blocks)}), encoding='utf-8')
'''
FLASHKIT_DISASSEMBLE = r"""
from pathlib import Path
import sys
from flashkit.workspace import Workspace
from flashkit.abc import decode_instructions, resolve_instructions
ws = Workspace()
ws.load_swf(sys.argv[1])
if not ws.abc_blocks:
    raise ValueError('No ABC blocks were loaded; disassembly was not produced')
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
for block_index, abc in enumerate(ws.abc_blocks):
    for body in abc.method_bodies:
        lines = [f'ABC {block_index} method {body.method} code_bytes={len(body.code)}']
        for instruction in resolve_instructions(abc, decode_instructions(body.code)):
            lines.append(f'{instruction.offset:06x} {instruction.mnemonic} {", ".join(instruction.operands)}')
        (out / f'abc-{block_index}-method-{body.method}.asm').write_text('\n'.join(lines) + '\n', encoding='utf-8')
"""
TIME_SCOPE = "Fresh canonical inventory, temporary SWF/output I/O, external interpreter/CLI startup, generation and output catalogue; adapter startup excluded in hot samples"


class ExternalToolError(ValueError):
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout.decode("utf-8", errors="replace")[-4000:]
        self.stderr = stderr.decode("utf-8", errors="replace")[-4000:]
        super().__init__(f"Tool exit {returncode}: " + (self.stderr or self.stdout))


def invoke(command,cwd,timeout=110):
    # Parent worker applies a job/memory limit and deadline to this exact tree.
    started=time.perf_counter()
    completed=subprocess.run(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),timeout=timeout)
    if completed.returncode:
        raise ExternalToolError(completed.returncode,completed.stdout,completed.stderr)
    return completed.stdout,completed.stderr.decode("utf-8",errors="replace")[-4000:],(time.perf_counter()-started)*1000


def resource_operation(engine,config,data,operation):
    """Tag-level resource extraction or XML round trip (benchmark_resource_tools)."""
    from .benchmark_resource_tools import extract, xml_roundtrip
    start=time.perf_counter()
    units,cli_ms=(extract if operation=="extract" else xml_roundtrip)(config["executable"],data)
    generate_ms=(time.perf_counter()-start)*1000
    available=sum(u["generation"]=="available" for u in units)
    if not units:
        return {"ok":True,"status":"not_applicable","units":[],"eligible_units":0,"operation":operation,
                "diagnostic":"No resource tags to extract" if operation=="extract" else "Empty SWF"}
    return {"ok":True,"status":"measured","operation":operation,"units":units,"unit_observations":units,
            "eligible_units":len(units),"available_units":available,"generate_ms":generate_ms,"external_cli_ms":cli_ms,
            "temporary_io_and_catalogue_ms":max(0,generate_ms-cli_ms),"time_scope":TIME_SCOPE,"native_returncode":0,
            "tool_fingerprint":{"version":config.get("version"),"executable_sha256":config.get("artifact_sha256",{}).get(config["executable"]),"license":config.get("license")},
            "output_summary":{"units":len(units),"available":available,"operation":operation},
            "process_mode":"CLI starts on every unit; hot excludes adapter startup, not CLI startup"}


def perform(engine,config,data,operation,include_source=False):
    operation = "source" if operation == "decompile" else operation
    if operation not in ENGINES[engine].operations:
        return {"ok":True,"status":"not_applicable","units":[],"eligible_units":0,"diagnostic":"Operation is outside this tool's capabilities"}
    if operation in ("extract","xml"):
        return resource_operation(engine,config,data,operation)
    start=time.perf_counter()
    movie=SWFFile()
    movie.read_bytes(data)
    canonical=build_inventory(data)
    inventory=list(enumerate_scripts(data,include_nested_avm1=True))
    eligible=[unit for unit in inventory if unit.language in ENGINES[engine].languages]
    parse_ms=(time.perf_counter()-start)*1000
    if not eligible:
        return {"ok":True,"status":"not_applicable","units":[],"eligible_units":0,"artifacts":[]}
    start=time.perf_counter()
    artifacts,warnings,cli_ms,observations,raw_sources,extra_summary=[],[],0,[],[],{}
    adapter_mode=config.get('adapter_mode','api_all_abc' if engine=='pyflashkit' else 'cli')
    with tempfile.TemporaryDirectory(prefix="pyswf-cli-operation-") as folder:
        folder=Path(folder)
        input_file=folder / "input.swf"
        input_file.write_bytes(data)
        if engine=="rabcdasm":
            # ABC extraction is charged to the operation. Nested tag paths stay
            # explicit; a nonempty .asasm file does NOT prove complete coverage.
            seen=set()
            for unit in eligible:
                if unit.tag_path in seen:
                    continue
                seen.add(unit.tag_path)
                abc_path=folder / ("abc-"+"-".join(map(str,unit.tag_path))+".abc")
                abc_path.write_bytes(unit.tag.parse_doabc()[2])
                _,warning,elapsed=invoke([config["executable"],str(abc_path)],folder)
                cli_ms+=elapsed
                warnings.append(warning)
            files=list(folder.rglob("*.asasm"))
        elif engine=="flasm":
            text,warning,elapsed=invoke([config["executable"],"-d",str(input_file)],folder)
            cli_ms+=elapsed
            warnings.append(warning)
            path=folder / "output.flm"
            path.write_bytes(text)
            files=[path]
        elif engine=="flex_swfdump":
            path=folder / "output.swfx"
            command=[config["executable"],"-Djava.awt.headless=true","-Xmx384m","-jar",config["library_jar"],
                     "-asm","-abc","-showbytecode","-showoffset","-noglyphs","-out",str(path),str(input_file)]
            _,warning,elapsed=invoke(command,folder)
            cli_ms+=elapsed
            warnings.append(warning)
            text=path.read_text(encoding="utf-8",errors="replace") if path.is_file() else ""
            # swfdump can return zero after printing a parsing error. A header,
            # constant pool, usage text or arbitrary nonempty file is insufficient.
            avm1=bool(re.search(r"absolute=\d+,relative=\d+,code=\d+\s+\S+",text))
            avm2=bool(re.search(r"offset \d+:\s+(?:[0-9A-Fa-f]{2}\s+)+[a-z][a-z0-9_]*\s",text))
            if "<swf " not in text or "</swf>" not in text or not (avm1 or avm2):
                raise ValueError("Apache Flex swfdump emitted no complete SWFX instruction listing")
            if re.search(r"<!--\s*Error: could not parse abc|Exception in thread|java\.lang\.",text):
                raise ValueError("Apache Flex swfdump reported an ABC parsing error; file output is not a successful disassembly")
            files=[path]
        elif engine=="flare":
            stdout,warning,elapsed=invoke([config["executable"],str(input_file)],folder)
            cli_ms+=elapsed
            warnings.append(warning)
            # Flare reports decompilation warnings on stdout as well as stderr.
            # Keep them: an output file alone is no evidence of source fidelity.
            warnings.append(stdout.decode('latin-1').strip())
            files=list(folder.glob("*.flr"))
            if include_source:
                for file in files:
                    contents=file.read_bytes()
                    if len(contents)>4*1024**2:
                        raise ValueError('Flare source evidence exceeds its bounded 4 MiB output limit')
                    raw_sources.append({'file':file.relative_to(folder).as_posix(),
                        'sha256':hashlib.sha256(contents).hexdigest(),'bytes':len(contents),
                        'encoding':'latin-1','source':contents.decode('latin-1')})
        elif engine=="pyflashkit":
            output = folder / "output"
            if adapter_mode == 'api_all_abc':
                command = [config['executable'],'-I','-c',FLASHKIT_API_ALL_ABC,str(input_file),str(output),operation]
                suffix = '*.as' if operation == 'source' else '*.asm'
            elif adapter_mode == 'cli_first_abc' and operation == "source":
                command = [config["executable"],"-I","-c",FLASHKIT_CLI,"decompile",str(input_file),"--all","--outdir",str(output)]
                suffix = "*.as"
            elif adapter_mode == 'cli_first_abc':
                command = [config["executable"],"-I","-c",FLASHKIT_DISASSEMBLE,str(input_file),str(output)]
                suffix = "*.asm"
            else:
                raise ValueError('Unknown pyflashkit adapter mode: ' + adapter_mode)
            _,warning,elapsed=invoke(command,folder)
            cli_ms+=elapsed
            warnings.append(warning)
            files=sorted(output.rglob(suffix))
            metadata=output/'observations.json'
            if adapter_mode=='api_all_abc':
                if not metadata.is_file():
                    raise ValueError('pyflashkit API emitted no observation JSON; output identities cannot be verified')
                raw=json.loads(metadata.read_text(encoding='utf-8'))
                if include_source:
                    for row in raw['rows']:
                        if row.get('output_file'):
                            source_path=(output/row['output_file']).resolve()
                            if not source_path.is_relative_to(output.resolve()) or source_path.stat().st_size>4*1024**2:
                                raise ValueError('External source evidence is outside its bounded output folder')
                            row['source']=source_path.read_text(encoding='utf-8')
                observations=attach_observations(canonical,raw['rows'],engine=engine,adapter_mode=adapter_mode)
        else:
            raise ValueError("Unsupported external engine")
        for file in files:
            contents=file.read_bytes()
            if contents.strip():
                artifacts.append({"file":file.relative_to(folder).as_posix(),"sha256":hashlib.sha256(contents).hexdigest(),"bytes":len(contents)})
        if config.get('evidence_root'):
            evidence=Path(config['evidence_root']).resolve()/engine/(hashlib.sha256(data).hexdigest()[:16]+'-'+uuid.uuid4().hex)
            evidence.mkdir(parents=True,exist_ok=False)
            for artifact in artifacts:
                original=folder/artifact['file'];target=evidence/artifact['file']
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
                artifact['artifact_path']=str(target)
            (evidence/'observations.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2),encoding='utf-8')
        if not artifacts and not observations:
            raise ValueError("Tool produced no nonempty output; not counted as success")
    generate_ms=(time.perf_counter()-start)*1000
    mapping_verified=bool(observations) and all(u['identity_verified'] for u in observations)
    return {"ok":True,"status":"measured" if mapping_verified else "output_unmapped","units":observations,"unit_observations":observations,
            "canonical_inventory":canonical,"canonical_inventory_sha256":canonical['sha256'],
            "inventory_status":"verified" if mapping_verified else "unknown", "adapter_mode":adapter_mode,
            "artifact_unit_scope":"canonical_unit" if mapping_verified else "abc" if engine=="rabcdasm" else "file",
            "include_scope":sorted({u.get('scope') for u in observations}) or ['file'],"eligible_units":len(eligible),"parse_ms":parse_ms,
            "generate_ms":generate_ms,"external_cli_ms":cli_ms,"temporary_io_and_catalogue_ms":max(0,generate_ms-cli_ms),"artifacts":artifacts,"warnings":[w for w in warnings if w],
            "raw_sources":raw_sources,
            "source_unit_coverage":"identity_verified" if mapping_verified else "not_verified","disassembly_unit_coverage":"identity_verified" if mapping_verified else "not_verified",
            "behavior":"not_run","compilation":"not_run","source_fidelity":"not_verified",
            "native_returncode":0,"time_scope":TIME_SCOPE,
            "tool_fingerprint":{"version":config.get("version"),"commit":config.get("commit"),"executable_sha256":config.get("artifact_sha256",{}).get(config["executable"]),"license":config.get("license")},
            "output_summary":{"files":len(artifacts),"bytes":sum(item["bytes"] for item in artifacts),"operation":operation,**extra_summary},
            "process_mode":"CLI starts on every operation; hot excludes adapter startup, not CLI startup",
            "diagnostic":None if mapping_verified else "File-level output is measured; exact canonical identities are unavailable or partial. Unit coverage is not claimed."}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--engine",choices=("flare","flasm","rabcdasm","pyflashkit","flex_swfdump","swfmill","swfextract"),required=True)
    parser.add_argument("--config",required=True)
    args=parser.parse_args()
    config=checked_tool(args.engine,args.config)
    for line in sys.stdin:
        request={}
        try:
            request=json.loads(line)
            if request["operation"]=="ping":
                result={"ok":True}
            else:
                start=time.perf_counter()
                data=base64.b64decode(request["swf_base64"],validate=True)
                decode_ms=(time.perf_counter()-start)*1000
                result=perform(args.engine,config,data,request["operation"],request.get('include_source',False))
                result["input_decode_ms"]=decode_ms
        except ExternalToolError as error:
            result={"ok":False,"status":"failed","diagnostic":str(error),"units":[],"native_returncode":error.returncode,
                    "failure_origin":"external_process","failure_stage":"tool_operation","time_scope":TIME_SCOPE,
                    "stdout_tail":error.stdout,"stderr_tail":error.stderr}
        except Exception as error:
            result={"ok":False,"status":"failed","diagnostic":str(error),"units":[],"failure_origin":"adapter","failure_stage":"parse_or_output_validation","time_scope":TIME_SCOPE}
        result.update(request_id=request.get("request_id"),engine=args.engine)
        start=time.perf_counter()
        json.dumps(result,ensure_ascii=False)
        result["serialize_ms"]=(time.perf_counter()-start)*1000
        sys.stdout.write(json.dumps(result,ensure_ascii=False)+"\n")
        sys.stdout.flush()


if __name__=="__main__":
    main()
