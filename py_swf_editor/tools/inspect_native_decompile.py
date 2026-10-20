"""Inspect real corpus bytecode without Java/JPEXS (development diagnostic)."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from py_swf.avm1 import disassemble_avm1
from py_swf.avm2 import ABCFile, build_method_mapping, disassemble_instructions
from py_swf.swf_parser import SWFFile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--match", default="TestFor.run,TestSwitch.run,TestTry.run,TestTryFinally.run")
    parser.add_argument("--tag", type=int)
    parser.add_argument("--method", type=int, nargs="+")
    parser.add_argument("--list-as2", action="store_true")
    parser.add_argument("--source", action="store_true")
    parser.add_argument("--graph", action="store_true")
    args = parser.parse_args()
    movie = SWFFile()
    movie.read_bytes(args.path.read_bytes())
    frame = 0
    for tag_index, tag in enumerate(movie.tags):
        if tag.tag_type == 1:
            frame += 1
        if tag.is_doabc:
            abc = ABCFile()
            abc.parse(tag.parse_doabc()[2])
            names = build_method_mapping(abc)
            for index, body in enumerate(abc.method_bodies):
                name = names.get(body.method, "")
                if args.method is not None and body.method not in args.method:
                    continue
                if args.method is None and args.tag != tag_index and not any(part in name for part in args.match.split(",")):
                    continue
                print(f"\nMETHOD {name} tag={tag_index} body={index} method={body.method} initial_scope={body.init_scope_depth}")
                print(disassemble_instructions(abc.constant_pool, body.code, exceptions=body.exceptions))
                if args.source:
                    from py_swf.decompile.avm2_dec import decompile_method
                    print("SOURCE", decompile_method(abc, body))
        elif tag.tag_type in (12, 59):
            offset = 2 if tag.tag_type == 59 else 0
            code = disassemble_avm1(tag.data[offset:])
            if args.list_as2:
                print(f"TAG {tag_index} frame={frame} kind={tag.tag_type} length={len(tag.data)} try={'try ' in code} {code[:160]!r}")
            elif args.tag == tag_index or any(part in code for part in args.match.split(",")):
                print(f"\nTAG {tag_index} frame={frame} kind={tag.tag_type} length={len(tag.data)}")
                print(code)
                if args.source:
                    from py_swf.decompile.avm1_dec import decompile_avm1
                    print("SOURCE", decompile_avm1(tag.data[offset:]))
                if args.graph:
                    from py_swf.decompile.ir import blocks_for, render
                    from py_swf.decompile.lift import Lifter, avm1_instructions
                    from py_swf.decompile.structure import Structurer
                    lift = Lifter("as2")
                    blocks = blocks_for(avm1_instructions(tag.data[offset:]))
                    lift.lift_graph(blocks)
                    structurer = Structurer(blocks)
                    for pc, block in blocks.items():
                        print("BLOCK", pc, block.term, block.successors, render(block.condition) if block.condition else "", [s.source() for s in block.statements])
                    print("LOOPS", structurer.loops)
                    try:
                        structurer.region(min(blocks))
                    except Exception as error:
                        print("FAILED", error, "claimed", structurer.claimed, "controls", structurer.controls)


if __name__ == "__main__":
    main()
