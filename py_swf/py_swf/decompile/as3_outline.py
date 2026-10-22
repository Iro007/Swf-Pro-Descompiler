"""
Esqueleto de código fuente AS3 a partir de la metadata ABC (sin analizar
bytecode): paquetes, clases/interfaces, herencia, campos y firmas de métodos.
"""
from ..avm2 import resolve_multiname, resolve_namespace

TRAIT_SLOT = 0
TRAIT_METHOD = 1
TRAIT_GETTER = 2
TRAIT_SETTER = 3
TRAIT_CLASS = 4
TRAIT_FUNCTION = 5
TRAIT_CONST = 6

_TYPE_KEYWORDS = {"": "*"}


def _type_name(pool, idx):
    if idx == 0:
        return "*"
    name = resolve_multiname(pool, idx)
    return name.split("::")[-1] if "::" in name else name


def _split_qname(pool, idx):
    """Devuelve (package, nombre_local) de un QName."""
    full = resolve_multiname(pool, idx)
    if "::" in full:
        pkg, local = full.rsplit("::", 1)
        return pkg, local
    return "", full


def _constant_repr(pool, vkind, vindex):
    if vkind == 0x01:  # utf8
        s = pool.strings[vindex] if vindex < len(pool.strings) else ""
        return '"' + s.replace('"', '\\"') + '"'
    if vkind == 0x03:
        return str(pool.integers[vindex]) if vindex < len(pool.integers) else "0"
    if vkind == 0x04:
        return str(pool.uintegers[vindex]) if vindex < len(pool.uintegers) else "0"
    if vkind == 0x06:
        return str(pool.doubles[vindex]) if vindex < len(pool.doubles) else "0"
    if vkind == 0x0A:
        return "false"
    if vkind == 0x0B:
        return "true"
    if vkind == 0x0C:
        return "null"
    if vkind == 0x00:
        return "undefined"
    if vkind in (0x08, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x05):
        return resolve_namespace(pool, vindex) or "namespace"
    return None


def _method_signature(abc, method_idx, name):
    pool = abc.constant_pool
    if method_idx >= len(abc.methods):
        return f"function {name}()"
    m = abc.methods[method_idx]
    params = []
    n_params = len(m.param_types)
    n_optional = len(m.options)
    for i, ptype in enumerate(m.param_types):
        pname = None
        if i < len(m.param_names):
            idx = m.param_names[i]
            if idx < len(pool.strings):
                pname = pool.strings[idx]
        pname = pname or f"param{i + 1}"
        entry = f"{pname}:{_type_name(pool, ptype)}"
        opt_idx = i - (n_params - n_optional)
        if 0 <= opt_idx < n_optional:
            opt = m.options[opt_idx]
            val = _constant_repr(pool, opt.get("kind", 0), opt.get("val", 0))
            if val is not None:
                entry += f" = {val}"
        params.append(entry)
    if m.flags & 0x04:  # NEED_REST
        params.append("... rest")
    ret = _type_name(pool, m.return_type)
    return f"function {name}({', '.join(params)}):{ret}"


def _slot_line(abc, trait, keyword):
    pool = abc.constant_pool
    _, name = _split_qname(pool, trait.name)
    line = f"{keyword} {name}:{_type_name(pool, trait.type_name)}"
    if trait.vkind:
        val = _constant_repr(pool, trait.vkind, trait.vindex)
        if val is not None:
            line += f" = {val}"
    return line + ";"


def _format_method_body(abc, method_idx, method_name, indent, method_body_map):
    if method_body_map and method_idx in method_body_map:
        mb = method_body_map[method_idx]
        try:
            from . import avm2_dec
            source, error = avm2_dec.decompile_method(abc, mb, method_name)
            if source and source.strip():
                body_lines = []
                for l in source.split("\n"):
                    if l.strip():
                        body_lines.append(f"{indent}    {l}")
                    else:
                        body_lines.append("")
                return "\n".join(body_lines)
            elif error:
                return f"{indent}    // {error}"
        except Exception as e:
            return f"{indent}    // Error: {e}"
    return ""


def _emit_traits(abc, traits, indent, static=False, lines=None, method_body_map=None):
    pool = abc.constant_pool
    prefix = "static " if static else ""
    for trait in traits:
        kind = trait.kind_flags & 0x0F
        if kind in (TRAIT_SLOT, TRAIT_CONST):
            keyword = "const" if kind == TRAIT_CONST else "var"
            lines.append(f"{indent}public {prefix}{_slot_line(abc, trait, keyword)}")
        elif kind in (TRAIT_METHOD, TRAIT_GETTER, TRAIT_SETTER):
            _, name = _split_qname(pool, trait.name)
            accessor = {TRAIT_GETTER: "get ", TRAIT_SETTER: "set "}.get(kind, "")
            sig = _method_signature(abc, trait.method, f"{accessor}{name}")
            lines.append(f"{indent}public {prefix}{sig}")
            lines.append(f"{indent}{{")
            body = _format_method_body(abc, trait.method, name, indent, method_body_map)
            if body:
                lines.append(body)
            lines.append(f"{indent}}}")
        elif kind == TRAIT_FUNCTION:
            _, name = _split_qname(pool, trait.name)
            sig = _method_signature(abc, trait.function, name)
            lines.append(f"{indent}{prefix}{sig}")
            lines.append(f"{indent}{{")
            body = _format_method_body(abc, trait.function, name, indent, method_body_map)
            if body:
                lines.append(body)
            lines.append(f"{indent}}}")


def outline_abc(abc, include_bodies=True):
    """Genera el código fuente AS3 completo y estructurado a partir del bytecode ABC."""
    pool = abc.constant_pool
    lines = []
    method_body_map = {mb.method: mb for mb in abc.method_bodies} if include_bodies else {}

    class_of_script = {}
    for si, script in enumerate(abc.scripts):
        for trait in script.traits:
            if (trait.kind_flags & 0x0F) == TRAIT_CLASS:
                class_of_script.setdefault(si, []).append(trait.class_idx)

    for si, script in enumerate(abc.scripts):
        class_indexes = class_of_script.get(si, [])
        for ci in class_indexes:
            if ci >= len(abc.instances):
                continue
            inst = abc.instances[ci]
            cls = abc.classes[ci] if ci < len(abc.classes) else None
            pkg, name = _split_qname(pool, inst.name)

            lines.append(f"package {pkg}".rstrip())
            lines.append("{")
            is_interface = bool(inst.flags & 0x04)
            keyword = "interface" if is_interface else "class"
            final = "final " if inst.flags & 0x02 else ""
            decl = f"    public {final}{keyword} {name}"
            if inst.super_name:
                super_name = _type_name(pool, inst.super_name)
                if super_name not in ("Object", "*"):
                    decl += f" extends {super_name}"
            if inst.interfaces:
                impl = ", ".join(_type_name(pool, i) for i in inst.interfaces)
                decl += f" {'extends' if is_interface else 'implements'} {impl}"
            lines.append(decl)
            lines.append("    {")
            if cls is not None:
                _emit_traits(abc, cls.traits, "        ", static=True, lines=lines, method_body_map=method_body_map)
            if not is_interface:
                ctor_sig = _method_signature(abc, inst.iinit, name)
                lines.append(f"        public {ctor_sig}")
                lines.append("        {")
                body = _format_method_body(abc, inst.iinit, name, "        ", method_body_map)
                if body:
                    lines.append(body)
                lines.append("        }")
            _emit_traits(abc, inst.traits, "        ", static=False, lines=lines, method_body_map=method_body_map)
            lines.append("    }")
            lines.append("}")
            lines.append("")

        # traits de script fuera de clases (funciones/vars de paquete)
        loose = [t for t in script.traits if (t.kind_flags & 0x0F) != TRAIT_CLASS]
        if loose:
            lines.append(f"// script {si} — declaraciones de paquete")
            _emit_traits(abc, loose, "", static=False, lines=lines, method_body_map=method_body_map)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
