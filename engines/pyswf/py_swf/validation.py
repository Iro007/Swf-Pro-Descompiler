"""Native container/ABC checks used before exporting a working SWF."""

from .swf_parser import SWFFile
from .avm2 import ABCFile
from .bytecode_validation import analyze_method, validate_avm1, BytecodeValidationError


def validate_swf_bytes(data):
    errors, warnings = [], []
    swf = SWFFile()
    try:
        swf.read_bytes(data, strict=True)
    except Exception as error:
        return {"valid": False, "errors": [str(error)], "warnings": []}
    if swf.signature == "FWS" and int.from_bytes(data[4:8], "little") != len(data):
        errors.append("FWS header length does not match the file length")
    if not swf.tags or swf.tags[-1].tag_type != 0:
        errors.append("Missing End tag")
    bytecode_complete = True
    def check_tags(tags, prefix="", depth=0):
        nonlocal bytecode_complete
        if depth > 32:
            errors.append("Nested sprite validation limit exceeded")
            return
        from .script_inventory import nested_tags, embedded_actions
        for index, tag in enumerate(tags):
            label = f"{prefix}{index}"
            if tag.parse_error:
                errors.append(f"Tag {label}: {tag.parse_error}")
            if tag.tag_type == 0 and tag.data:
                errors.append(f"Tag {label}: End tag must have an empty payload")
            try:
                if tag.tag_type == 39:
                    if len(tag.data) < 4:
                        raise ValueError("Truncated sprite header")
                    children = [child for _, child in nested_tags(tag.data[4:])]
                    if not children or children[-1].tag_type != 0:
                        raise ValueError("Missing sprite End tag")
                    check_tags(children, label + "/", depth + 1)
                elif tag.is_doabc:
                    parsed = tag.parse_doabc()
                    if parsed is None:
                        raise ValueError("Missing ABC payload")
                    abc = ABCFile()
                    abc.parse(parsed[2], strict=True)
                    for body in abc.method_bodies:
                        if not 0 <= body.method < len(abc.methods):
                            raise ValueError("Invalid method body reference")
                        try:
                            limits = analyze_method(abc, body)
                            if body.max_stack < limits.max_stack or body.local_count < limits.local_count or body.max_scope_depth < limits.max_scope_depth:
                                raise ValueError("Declared method stack/local/scope limits are insufficient")
                        except BytecodeValidationError as error:
                            if "Validation not implemented for" not in str(error):
                                raise
                            bytecode_complete = False
                            warnings.append(f"Tag {label}, method {body.method}: {error}")
                elif tag.tag_type in (12, 59):
                    if tag.tag_type == 59 and len(tag.data) < 2:
                        raise ValueError("Truncated DoInitAction id")
                    validate_avm1(tag.data[2:] if tag.tag_type == 59 else tag.data)
                elif tag.tag_type in (7, 34, 26, 70, 94):
                    for _, _, start, end, _ in embedded_actions(tag, swf.version):
                        validate_avm1(tag.data[start:end])
            except Exception as error:
                errors.append(f"Tag {label} ({tag.name}): {error}")
    check_tags(swf.tags)
    for index, tag in enumerate(swf.tags):
        if tag.parse_error:
            errors.append(f"Tag {index}: {tag.parse_error}")
    if swf.frame_rate <= 0:
        warnings.append("Non-positive frame rate")
    shown = sum(tag.tag_type == 1 for tag in swf.tags)
    if shown != swf.frame_count:
        warnings.append(f"Declared frames {swf.frame_count}, ShowFrame tags {shown}")
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "signature": swf.signature, "version": swf.version, "tag_count": len(swf.tags),
            "frame_count": swf.frame_count, "bytecode_complete": bytecode_complete,
            "verification_scope": "container, ABC structure and supported bytecode stack/scope; no AVM2 type/runtime verification"}
