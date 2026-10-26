"""Managed class-program documents proven against their current ABC bytes."""
import hashlib
import math

from .compiler.class_compiler import compile_class_program
from .swf_parser import SWFTag


def _source_type(abc, index, *, shadow_names=()):
    from .avm2 import resolve_multiname
    from .symbols import source_identifier
    if not index:
        return "*"
    from .abc_symbols import inspect_qname
    identity = inspect_qname(abc.constant_pool, index)
    if identity.namespace_kind not in (8, 0x16, 0x17):
        raise ValueError("Private/protected type QName needs a proven source qualification")
    text = resolve_multiname(abc.constant_pool, index).replace("::", ".")
    if text not in ("*", "void") and "." not in text and text in shadow_names:
        raise ValueError("Declared source type is shadowed by an owner member or parameter: " + text)
    if text not in ("*", "void") and not all(source_identifier(part) for part in text.split(".")):
        raise ValueError("Type cannot be represented without its exact namespace")
    return text


def _source_constant(abc, kind, index):
    from .script_strings import quote_string
    pool = abc.constant_pool
    if kind in (0, 10, 11, 12):
        return {0: "undefined", 10: "false", 11: "true", 12: "null"}[kind]
    values = {1: pool.strings, 3: pool.integers, 4: pool.uintegers, 6: pool.doubles}
    if kind not in values or not 0 <= index < len(values[kind]):
        raise ValueError("Constant has no representable source value")
    value = values[kind][index]
    if kind == 1:
        return quote_string(value)
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    return repr(value)


def _source_metadata(abc, indices, indent):
    from .script_strings import quote_string
    from .symbols import source_identifier
    result = []
    for index in indices:
        metadata = abc.metadata[index]
        name = abc.constant_pool.strings[metadata.name]
        if not source_identifier(name):
            raise ValueError("Metadata name cannot be represented in source")
        items = []
        for key, value in metadata.items:
            key = abc.constant_pool.strings[key]
            value = quote_string(abc.constant_pool.strings[value])
            if key and not source_identifier(key):
                raise ValueError("Metadata key cannot be represented in source")
            items.append((key + "=" if key else "") + value)
        result.append(indent + "[" + name + ("(" + ", ".join(items) + ")" if items else "") + "]")
    return result


def _source_signature(abc, index, text, *, constructor=False, shadow_names=()):
    from .symbols import source_identifier
    method, pool = abc.methods[index], abc.constant_pool
    shadow_names = set(shadow_names) | {pool.strings[index] for index in method.param_names if 0 < index < len(pool.strings)}
    if method.flags & 1:
        raise ValueError("Implicit arguments object requires signature-aware reconstruction")
    params = []
    for offset, typ in enumerate(method.param_types):
        name_index = method.param_names[offset] if offset < len(method.param_names) else 0
        name = pool.strings[name_index] if name_index else f"local{offset + 1}"
        if not source_identifier(name):
            raise ValueError("Parameter name cannot be represented in source")
        param = name + ":" + _source_type(abc, typ, shadow_names=shadow_names)
        optional = offset - len(method.param_types) + len(method.options)
        if optional >= 0:
            option = method.options[optional]
            param += " = " + _source_constant(abc, option["kind"], option["val"])
        params.append(param)
    if method.flags & 4:
        params.append("...local" + str(len(method.param_types) + 1))
    return "function " + text + "(" + ", ".join(params) + ")" + ("" if constructor else ":" + _source_type(abc, method.return_type, shadow_names=shadow_names))


def reconstruct_class_sections(abc, *, method_sections=None, class_indices=None):
    """Reconstruct declarations with bodies, marking unsupported members partial.

    No failed body is replaced by `{}`. Complete means every original class
    unit is represented; editable additionally requires exact ABC reproduction.
    """
    from .abc_symbols import inspect_qname
    from .abc_symbols import AbcSymbolIndex
    from .symbols import source_identifier
    from .decompile.native import decompile_as3
    if not hasattr(abc, "_native_symbol_index"):
        abc._native_symbol_index = AbcSymbolIndex([abc])
    bodies = {}
    for body_index, body in enumerate(abc.method_bodies):
        bodies.setdefault(body.method, []).append((body_index, body))
    cached = {section.get("method_index", abc.method_bodies[section["body_index"]].method if section.get("body_index") is not None else None): section
              for section in (method_sections or [])}
    body_type_cache = {}
    lexical_type_cache = {}

    def declared_body_type(multiname_index):
        if multiname_index not in lexical_type_cache:
            from .decompile.hierarchy_contract import external_hierarchy, type_key
            declaration = abc._native_symbol_index.resolve_type(abc.constant_pool, multiname_index)
            if declaration is not None:
                lexical_type_cache[multiname_index] = True
            else:
                try:
                    lexical_type_cache[multiname_index] = external_hierarchy().get(type_key(abc.constant_pool, multiname_index)) is not None
                except ValueError:
                    lexical_type_cache[multiname_index] = False
        return lexical_type_cache[multiname_index]

    def body_types(method_index):
        """Exact type operands and bindings, including reachable closure bodies."""
        if method_index in body_type_cache:
            return body_type_cache[method_index]
        from .bytecode_validation import decode_avm2
        method = abc.methods[method_index]
        references = {method.return_type, *method.param_types}
        bindings = {abc.constant_pool.strings[name] for name in method.param_names if 0 < name < len(abc.constant_pool.strings)}
        children = set()
        choices = bodies.get(method_index, [])
        if len(choices) == 1:
            body = choices[0][1]
            bindings.update(f"local{n}" for n in range(1, body.local_count))
            for trait in body.traits:
                if trait.kind_flags & 15 in (0, 6):
                    references.add(trait.type_name)
                    bindings.add(inspect_qname(abc.constant_pool, trait.name).name)
            for handler in body.exceptions:
                references.add(handler.exc_type)
                if handler.var_name:
                    bindings.add(inspect_qname(abc.constant_pool, handler.var_name).name)
            # Import evidence comes from decoded QName operands, never source
            # spelling or every unused name in the constant pool.
            try:
                instructions = decode_avm2(body.code)
            except ValueError:
                instructions = ()  # ordinary body recovery records the error
            for instruction in instructions:
                op, args = instruction.name, instruction.args
                if op in ("coerce", "astype", "istype"):
                    references.add(args[0])
                elif op in ("getlex", "constructprop"):
                    if declared_body_type(args[0]):
                        references.add(args[0])
                elif op == "newfunction":
                    children.add(args[0])
                elif op == "debug" and args[0] == 1 and 0 < args[1] < len(abc.constant_pool.strings):
                    bindings.add(abc.constant_pool.strings[args[1]])
        for reference in references:
            _source_type(abc, reference)
        result = (references, bindings, children)
        body_type_cache[method_index] = result
        return result

    result = []
    def body_source(method_index, diagnostics):
        choices = bodies.get(method_index, [])
        if len(choices) != 1:
            diagnostics.append(f"method:{method_index}: missing or duplicate bytecode body")
            return None
        if method_index in cached:
            section = cached[method_index]
            if section.get("error"):
                diagnostics.append(f"method:{method_index}: {section['error']}")
                return None
            return section["source"]
        try:
            return decompile_as3(abc, choices[0][1])
        except (ValueError, IndexError, KeyError) as error:
            diagnostics.append(f"method:{method_index}: {error}")
            return None
    for ci, instance in enumerate(abc.instances):
        if class_indices is not None and ci not in class_indices:
            continue
        diagnostics, unit_ids, lines = [], [], []
        if ci < len(abc.classes):
            declared_methods = [instance.iinit, abc.classes[ci].cinit]
            for trait in [*instance.traits, *abc.classes[ci].traits]:
                kind = trait.kind_flags & 15
                if kind in (1, 2, 3):
                    declared_methods.append(trait.method)
                elif kind == 5:
                    declared_methods.append(trait.function)
            unit_ids = [f"method:{index}" for index in declared_methods if index in bodies]
        try:
            identity = inspect_qname(abc.constant_pool, instance.name)
            if identity.kind != 7 or identity.namespace_kind not in (0x16, 0x17) or not source_identifier(identity.name) or any(not source_identifier(p) for p in identity.package.split(".") if p):
                raise ValueError("Class identity cannot be represented in AS3 source")
            is_interface = bool(instance.flags & 4)
            lines = ["package" + (" " + identity.package if identity.package else ""), "{"]
            shadow_names, type_indices = set(), [instance.super_name, *instance.interfaces]
            from .symbols import name_identity
            for trait in [*instance.traits, *abc.classes[ci].traits]:
                try:
                    shadow_names.add(name_identity(abc.constant_pool, trait.name).local)
                except (ValueError, IndexError, KeyError):
                    continue
                if trait.kind_flags & 15 in (0, 6):
                    type_indices.append(trait.type_name)
                elif trait.kind_flags & 15 in (1, 2, 3):
                    method = abc.methods[trait.method]
                    type_indices.extend([method.return_type, *method.param_types])
            type_indices.extend(abc.methods[instance.iinit].param_types)
            todo, seen = list(declared_methods), set()
            body_shadow_names = set(shadow_names)
            while todo:
                method_index = todo.pop()
                if method_index in seen:
                    continue
                seen.add(method_index)
                references, bindings, children = body_types(method_index)
                type_indices.extend(references)
                body_shadow_names.update(bindings)
                todo.extend(children - seen)
            imports = set()
            for type_index in type_indices:
                if not type_index:
                    continue
                try:
                    reference = inspect_qname(abc.constant_pool, type_index)
                except ValueError:
                    continue
                if reference.namespace_kind == 0x16 and reference.package and (reference.package, reference.name) != (identity.package, identity.name):
                    if reference.name == identity.name or {reference.name, reference.package.split(".")[0]} & body_shadow_names:
                        raise ValueError("Public type import is shadowed by a class, member or lexical binding: " + _source_type(abc, type_index))
                    imports.add(_source_type(abc, type_index))
            if len({name.rsplit(".", 1)[-1] for name in imports}) != len(imports):
                raise ValueError("Public type imports have an unsupported same-name collision")
            lines.extend("    import " + name + ";" for name in sorted(imports))
            from .script_strings import quote_string
            namespace_modifiers = {}
            for trait in [*instance.traits, *abc.classes[ci].traits]:
                try:
                    member_identity = inspect_qname(abc.constant_pool, trait.name)
                except ValueError:
                    continue
                if member_identity.namespace_kind != 8 or not member_identity.package:
                    continue
                choices = [alias for _, alias in abc._native_symbol_index.namespace_aliases.get((8, member_identity.package), [])
                           if alias.package == identity.package and source_identifier(alias.name)]
                names = {alias.name for alias in choices}
                if len(names) == 1:
                    alias = choices[0]
                    namespace_modifiers[member_identity.package] = alias.name
                    declaration = "    " + ("public" if alias.namespace_kind == 0x16 else "internal") + " namespace " + alias.name + " = " + quote_string(member_identity.package) + ";"
                    if declaration not in lines:
                        lines.append(declaration)
            class_traits = [trait for script in abc.scripts for trait in script.traits if trait.kind_flags & 15 == 4 and trait.class_idx == ci]
            if len(class_traits) > 1:
                diagnostics.append("Class has multiple script installation traits")
            if class_traits:
                lines.extend(_source_metadata(abc, class_traits[0].metadata, "    "))
            modifiers = ["public" if identity.namespace_kind == 0x16 else "internal"]
            if instance.flags & 2:
                modifiers.append("final")
            if not instance.flags & 1 and not is_interface:
                modifiers.append("dynamic")
            declaration = "    " + " ".join(modifiers) + (" interface " if is_interface else " class ") + identity.name
            if instance.super_name and _source_type(abc, instance.super_name) != "Object":
                declaration += " extends " + _source_type(abc, instance.super_name)
            if instance.interfaces:
                declaration += (" extends " if is_interface else " implements ") + ", ".join(_source_type(abc, n) for n in instance.interfaces)
            lines.extend((declaration, "    {"))
            cls = abc.classes[ci]
            def method_lines(method_index, text, prefix, *, constructor=False, abstract=False):
                unit_ids.append(f"method:{method_index}")
                signature = _source_signature(abc, method_index, text, constructor=constructor, shadow_names=shadow_names)
                if abstract:
                    return ["        " + prefix + signature + ";"]
                source = body_source(method_index, diagnostics)
                if source is None:
                    method_info = abc.methods[method_index] if 0 <= method_index < len(abc.methods) else None
                    ret_type = _source_type(abc, method_info.return_type, shadow_names=shadow_names) if method_info and method_info.return_type else "*"
                    if ret_type in ("int", "uint", "Number"):
                        ret_stmt = "return 0;"
                    elif ret_type == "Boolean":
                        ret_stmt = "return false;"
                    elif ret_type == "void":
                        ret_stmt = "return;"
                    elif ret_type == "*":
                        ret_stmt = "return undefined;"
                    else:
                        ret_stmt = "return null;"
                    return [
                        "        // Incomplete " + signature + f"; original method:{method_index} retained as P-code",
                        "        " + prefix + signature,
                        "        {",
                        f"            {ret_stmt}",
                        "        }",
                    ]
                body_lines = source.splitlines()
                if body_lines and body_lines[-1].strip() == "return;" and (constructor or abc.methods[method_index].return_type and _source_type(abc, abc.methods[method_index].return_type) == "void"):
                    body_lines.pop()
                return ["        " + prefix + signature, "        {", *("            " + line for line in body_lines), "        }"]
            def trait_lines(traits, static):
                for trait in traits:
                    try:
                        member = inspect_qname(abc.constant_pool, trait.name)
                        if member.kind != 7:
                            raise ValueError("Attribute QName is not a normal declaration name")
                        if not source_identifier(member.name):
                            raise ValueError("Member name cannot be represented in source")
                        visibility = {0x16: "public", 0x17: "internal", 5: "private", 0x18: "protected", 0x1A: "protected"}.get(member.namespace_kind)
                        if is_interface and member.namespace_kind == 8 and member.package == (identity.package + ":" if identity.package else "") + identity.name:
                            visibility = "public"
                        elif member.namespace_kind == 8 and member.package in namespace_modifiers:
                            visibility = namespace_modifiers[member.package]
                        if visibility is None:
                            raise ValueError("Explicit member namespace requires a namespace declaration")
                        lines.extend(_source_metadata(abc, trait.metadata, "        "))
                        mods = [visibility]
                        if static:
                            mods.append("static")
                        if trait.kind_flags & 0x20:
                            mods.append("override")
                        if trait.kind_flags & 0x10:
                            mods.append("final")
                        prefix = " ".join(mods) + " "
                        kind = trait.kind_flags & 15
                        if kind in (0, 6):
                            field = prefix + ("const " if kind == 6 else "var ") + member.name + ":" + _source_type(abc, trait.type_name, shadow_names=shadow_names)
                            if trait.vindex:
                                field += " = " + _source_constant(abc, trait.vkind, trait.vindex)
                            lines.append("        " + field + ";")
                        elif kind in (1, 2, 3):
                            accessor = {1: "", 2: "get ", 3: "set "}[kind]
                            lines.extend(method_lines(trait.method, accessor + member.name, prefix, abstract=is_interface))
                        else:
                            raise ValueError("Nested class/function trait cannot be represented by this declaration tier")
                    except (ValueError, IndexError, KeyError) as error:
                        diagnostics.append(f"trait:{trait.name}: {error}")
                        lines.append(f"        // Incomplete trait:{trait.name}; original declaration retained")
            trait_lines(cls.traits, True)
            unit_ids.append(f"method:{cls.cinit}")
            static_source = body_source(cls.cinit, diagnostics)
            if static_source is not None:
                static_lines = static_source.splitlines()
                if static_lines and static_lines[-1].strip() == "return;":
                    static_lines.pop()
                if any(line.strip() and not line.strip().startswith("//") for line in static_lines):
                    lines.extend(("        // Static initializer (cinit), in original instruction order", "        {", *("            " + line for line in static_lines), "        }"))
            elif not is_interface:
                lines.append(f"        // Incomplete static initializer method:{cls.cinit}; P-code retained")
            if not is_interface:
                lines.extend(method_lines(instance.iinit, identity.name, "public ", constructor=True))
            trait_lines(instance.traits, False)
            lines.extend(("    }", "}"))
        except (ValueError, IndexError, KeyError) as error:
            diagnostics.append(str(error))
            lines = [f"// Incomplete class:{ci}; original declarations and bytecode retained"]
        result.append({"name": f"class:{ci}", "scope": "class", "kind": "class", "class_index": ci,
                       "source": "\n".join(lines) + "\n", "error": None if not diagnostics else "; ".join(diagnostics),
                       "completeness": "complete" if not diagnostics else "partial", "unit_ids": list(dict.fromkeys(unit for unit in unit_ids if int(unit.split(":", 1)[1]) in bodies)),
                       "diagnostics": diagnostics, "editable_source": False, "replaceable_source": False,
                       "compiler_eligibility": {"eligible": False, "reason": "Whole ABC exact reproduction must be verified before replacement"},
                       "source_edit_reason": "Whole ABC exact reproduction must be verified before replacement"})
    return result


def reconstruct_abc_documents(abc, *, method_sections=None):
    from .decompile.script_initializers import declaration_order
    classes = reconstruct_class_sections(abc, method_sections=method_sections)
    scripts = []
    for si, script in enumerate(abc.scripts):
        bodies = [body for body in abc.method_bodies if body.method == script.init]
        diagnostics = []
        original_units = [f"method:{script.init}"]
        for trait in script.traits:
            if trait.kind_flags & 15 == 4 and 0 <= trait.class_idx < len(classes):
                original_units.extend(classes[trait.class_idx]["unit_ids"])
        try:
            if len(bodies) != 1:
                raise ValueError("Script initializer has no unique bytecode body")
            order = declaration_order(abc, bodies[0])
            selected = [classes[index] for index in order]
            diagnostics.extend(message for section in selected for message in section["diagnostics"])
            source = "\n".join(section["source"] for section in selected)
            from .compiler.class_parser import parse_class_program
            from .abc_symbols import inspect_qname
            from .script_strings import quote_string
            existing_namespaces = set()
            if source and not diagnostics:
                existing_namespaces = {(item.package, item.name) for item in parse_class_program(source).namespaces}
            for trait in script.traits:
                if trait.kind_flags & 15 not in (0, 6) or trait.vkind != 8:
                    continue
                alias = inspect_qname(abc.constant_pool, trait.name)
                if (alias.package, alias.name) in existing_namespaces:
                    continue
                namespace = abc.constant_pool.namespaces[trait.vindex]
                if namespace["kind"] != 8 or alias.namespace_kind not in (0x16, 0x17):
                    raise ValueError("Script namespace declaration has no explicit URI identity")
                uri = abc.constant_pool.strings[namespace["name"]]
                declaration = "package" + (" " + alias.package if alias.package else "") + " { " + ("public" if alias.namespace_kind == 0x16 else "internal") + " namespace " + alias.name + " = " + quote_string(uri) + "; }\n"
                source = declaration + source
                existing_namespaces.add((alias.package, alias.name))
            units = original_units
        except (ValueError, IndexError, KeyError) as error:
            diagnostics.append(str(error))
            source = f"// Incomplete script:{si}; original initializer and declarations retained as P-code\n"
            units = original_units
        eligible = False
        reason = "Incomplete source requires exact P-code" if diagnostics else "Whole ABC exact reproduction must be verified before replacement"
        if not diagnostics and len(abc.scripts) == 1:
            try:
                eligible = compile_class_program(source).abc_bytes() == abc.serialize()
            except (ValueError, SyntaxError, UnicodeError):
                pass
            if eligible:
                reason = None
        scripts.append({"name": f"script:{si}", "scope": "script", "kind": "script", "script_index": si,
                        "source": source, "error": None if not diagnostics else "; ".join(diagnostics),
                        "completeness": "complete" if not diagnostics else "partial", "unit_ids": list(dict.fromkeys(units)),
                        "diagnostics": diagnostics, "editable_source": eligible, "replaceable_source": eligible,
                        "compiler_eligibility": {"eligible": eligible, "reason": reason}, "source_edit_reason": reason})
    if len(scripts) == 1 and scripts[0]["compiler_eligibility"]["eligible"]:
        for section in classes:
            section["editable_source"] = section["replaceable_source"] = True
            section["compiler_eligibility"] = {"eligible": True, "reason": None}
            section["source_edit_reason"] = None
    return [*classes, *scripts]


def compile_reconstructed_document(tag, document, source):
    """Compile an exact admitted class/script on a copy, with immutable guards.

    This path accepts both engines' normal AS3 declaration syntax. It never
    grants admission from an engine label, caller-provided completeness, or
    a saved checksum: the current native source must reproduce the entire
    original ABC byte for byte before body/value edits are permitted.
    """
    from .avm2 import ABCFile
    from .bytecode_validation import analyze_method
    parsed = tag.parse_doabc()
    if parsed is None or tag.tag_type not in (72, 82):
        raise ValueError("Class/script compilation requires a DoABC tag")
    if not isinstance(document, dict) or not isinstance(source, str):
        raise ValueError("A source document locator and source text are required")
    tag_hash = hashlib.sha256(tag.data).hexdigest()
    if document.get("tag_sha256") != tag_hash:
        raise ValueError("Source document is stale; its original ABC tag changed")
    abc = ABCFile()
    abc.parse(parsed[2], strict=True)
    sections = reconstruct_abc_documents(abc)
    document_id = document.get("document_id", "")
    suffix = document_id.rsplit("/", 1)[-1]
    matches = [section for section in sections if suffix == section["scope"] + ":" + str(section[section["scope"] + "_index"])]
    if len(matches) != 1:
        raise ValueError("Unknown class/script source document")
    selected = matches[0]
    source_hash = hashlib.sha256(selected["source"].encode("utf-8")).hexdigest()
    if document.get("original_source_sha256") != source_hash:
        raise ValueError("Source document is stale; its original source identity changed")
    if selected["completeness"] != "complete" or not selected["compiler_eligibility"]["eligible"]:
        raise ValueError("The complete source does not exactly reproduce this ABC; opaque code is protected")
    original_script = next(section for section in sections if section["scope"] == "script")
    original = compile_class_program(original_script["source"])
    if original.abc_bytes() != parsed[2]:
        raise ValueError("Original source failed exact ABC reproduction")
    if selected["scope"] == "class":
        class_sections = [section for section in sections if section["scope"] == "class"]
        replacement_source = "\n".join(source if section is selected else section["source"] for section in class_sections)
    else:
        replacement_source = source
    compiled = compile_class_program(replacement_source)
    if compiled.abi_sha256 != original.abi_sha256:
        raise ValueError("Declaration/signature changes require semantic relinking; body/value edits only")
    candidate = compiled.abc_bytes()
    reopened = ABCFile()
    reopened.parse(candidate, strict=True)
    for body in reopened.method_bodies:
        analyze_method(reopened, body)
    flags, name, _ = parsed
    payload = candidate if tag.tag_type == 72 else flags.to_bytes(4, "little") + name.encode("utf-8") + b"\0" + candidate
    replacement = SWFTag(tag.tag_type, payload)
    return replacement, {"engine": "native", "scope": selected["scope"], "document_id": document_id,
                         "original_tag_sha256": tag_hash, "tag_sha256": hashlib.sha256(payload).hexdigest(),
                         "original_source_sha256": source_hash, "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                         "unit_ids": selected["unit_ids"], "complete": True, "exact_original_roundtrip": True,
                         "validated_method_bodies": len(reopened.method_bodies)}


def program_record(compiled,tag,tag_index,swf_bytes):
    return {"document_id":f"tag:{tag_index}/script:0","kind":"script","source":compiled.source,
            "working_swf_sha256":hashlib.sha256(swf_bytes).hexdigest(),"tag_sha256":hashlib.sha256(tag.data).hexdigest(),
            "native_class_program":True,"abi_sha256":compiled.abi_sha256,"class_names":compiled.class_names,
            "sources":dict(compiled.sources)}


def resolve_program(tag,record):
    if not record or record.get("native_class_program") is not True or tag.tag_type!=82:
        return None
    if record.get("tag_sha256")!=hashlib.sha256(tag.data).hexdigest():
        return None
    source=record.get("source")
    if not isinstance(source,str):
        return None
    try:
        compiled=compile_class_program(source, sources=record.get("sources"))
        parsed=tag.parse_doabc()
        # Project metadata is untrusted. A label/checksum alone never grants
        # permission to rebuild an opaque ABC and drop its unknown bodies.
        if not parsed or compiled.abc_bytes()!=parsed[2]:
            return None
        return compiled
    except (ValueError,SyntaxError,UnicodeError):
        return None


def program_section(tag,tag_index,swf_bytes,record):
    compiled=resolve_program(tag,record)
    if compiled is None:
        return None
    return {"name":"Programa AS3 · fuente original","source":compiled.source,"saved_source":compiled.source,
            "error":None,"editable_source":True,"replaceable_source":True,"source_edit_reason":None,
            "source_origin":"authored_verified","source_document":{
                "document_id":f"tag:{tag_index}/script:0","kind":"script","base_swf_sha256":hashlib.sha256(swf_bytes).hexdigest(),
                "editable":True,"diagnostics":compiled.diagnostics,"symbol_context":{"script_index":0,"abc_major":46,"abc_minor":16},
                "source_bytecode_map":[],"map_status":"not_available"}}


def replace_program(tag,record,source):
    original=resolve_program(tag,record)
    if original is None:
        raise ValueError("The saved program does not reproduce this ABC; opaque code is protected")
    compiled=compile_class_program(source, sources=record.get("sources"))
    if compiled.abi_sha256!=original.abi_sha256:
        raise ValueError("Declaration/signature changes require semantic relinking; this tier allows body and literal field-value edits only")
    flags,name,_=tag.parse_doabc()
    return SWFTag(82,flags.to_bytes(4,"little")+name.encode("utf-8")+b"\0"+compiled.abc_bytes()),compiled
