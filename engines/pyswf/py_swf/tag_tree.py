"""JPEXS-style tag tree, header summary and raw tag pages for the single-window UI.

Everything here is read-only and works on an already parsed movie plus the
script inventory of the same revision. Node identities are stable for a given
revision so the interface can keep its expansion and selection state.
"""
from __future__ import annotations

import base64
import hashlib

from .avm2 import resolve_multiname, resolve_namespace
from .script_inventory import nested_tags
from .swf_parser import collect_symbol_names

DEFAULT_TREE_NODES = 20_000
MAX_TREE_NODES = 100_000
# The JSONL worker refuses replies above 8 MiB; the tree shrinks its node budget instead of failing.
MAX_TREE_JSON_BYTES = 6 * 1024**2
DEFAULT_RAW_PAGE = 4096
MAX_RAW_PAGE = 64 * 1024
MAX_METADATA_CHARS = 64 * 1024

# Same top-level order as the JPEXS tag tree (header first, scripts last).
FOLDER_ORDER = ("shapes", "morphshapes", "sprites", "texts", "images", "movies", "sounds", "buttons",
                "fonts", "binaryData", "frames", "others", "scripts", "sources")
FOLDER_LABELS = {
    "shapes": "Formas", "morphshapes": "Formas morf.", "sprites": "Sprites", "texts": "Textos",
    "images": "Imágenes", "movies": "Películas", "sounds": "Sonidos", "buttons": "Botones",
    "fonts": "Fuentes", "binaryData": "Datos binarios", "frames": "Cuadros", "others": "Otros",
    "scripts": "Scripts", "sources": "Fuentes del proyecto",
}
FOLDER_TYPES = {
    "shapes": {2, 22, 32, 83}, "morphshapes": {46, 84}, "sprites": {39}, "texts": {11, 33, 37},
    "images": {6, 20, 21, 35, 36, 90}, "movies": {38, 60}, "sounds": {14}, "buttons": {7, 34},
    "fonts": {10, 48, 75, 91}, "binaryData": {87},
}
TYPE_FOLDER = {code: folder for folder, codes in FOLDER_TYPES.items() for code in codes}
SOUND_STREAM_HEADS = {18, 45}
# Control tags that belong to a frame of a timeline (ShowFrame closes the frame).
FRAME_TAGS = {3, 4, 5, 15, 19, 26, 28, 43, 49, 61, 66, 70, 89, 94}
# Tags keyed by a character id that hang below their character in JPEXS.
MAPPED_TAGS = {13, 17, 23, 62, 73, 74, 78, 88}
FILE_ATTRIBUTE_FLAGS = (("use_network", 0x01), ("actionscript3", 0x08), ("has_metadata", 0x10),
                        ("use_gpu", 0x20), ("use_direct_blit", 0x40))
COMPRESSION = {"FWS": "none", "CWS": "zlib", "ZWS": "lzma"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class _Budget:
    """Counts nodes so a gigantic movie cannot produce an unbounded reply."""

    def __init__(self, limit):
        self.limit = limit
        self.count = 0
        self.truncated = False

    def take(self):
        if self.count >= self.limit:
            self.truncated = True
            return False
        self.count += 1
        return True


def _u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], "little") if len(data) >= offset + 2 else None


def _frame_numbers(types):
    """1-based frame number of every tag of a timeline (ShowFrame closes a frame)."""
    numbers, frame = [], 1
    for code in types:
        numbers.append(frame)
        if code == 1:
            frame += 1
    return numbers


def _split_frames(entries):
    """Group (path, tag) pairs into frames: [(number, [(path, tag)])], control tags only."""
    frames, current, number = [], [], 1
    for path, tag in entries:
        if tag.tag_type == 0:
            break
        if tag.tag_type == 1:
            frames.append((number, current))
            current, number = [], number + 1
        elif tag.tag_type in FRAME_TAGS:
            current.append((path, tag))
    if current:
        frames.append((number, current))
    return frames


def _frame_label_text(tag):
    return tag.data.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _control_label(tag):
    """Short label for a timeline control tag (depth and character when cheap to read)."""
    code, data = tag.tag_type, tag.data
    if code == 43:
        return f"FrameLabel: {_frame_label_text(tag)}"
    depth = {4: _u16(data, 2), 5: _u16(data, 2), 26: _u16(data, 1), 28: _u16(data, 0),
             70: _u16(data, 2), 94: _u16(data, 2)}.get(code)
    label = tag.name
    if depth is not None:
        label += f" · profundidad {depth}"
    if code == 4 or (code == 26 and len(data) >= 5 and data[0] & 0x02):
        character = _u16(data, 0 if code == 4 else 3)
        if character is not None:
            label += f" → {character}"
    return label


def _node(node_id, label, kind, **fields):
    result = {"id": node_id, "label": label, "kind": kind}
    result.update({key: value for key, value in fields.items() if value is not None})
    return result


def _tag_node(path, tag, symbols=None, *, character=False):
    node = _node("tag:" + "/".join(map(str, path)), tag.name, "tag", tag_index=path[0], tag_type=tag.tag_type,
                 tag_name=tag.name, bytes=len(tag.data), error=tag.parse_error)
    if len(path) > 1:
        node["tag_path"] = list(path)
    identity = tag.char_id if character else None
    if identity is not None:
        node["character_id"] = identity
        node["label"] = f"{tag.name} ({identity})"
        if symbols and symbols.get(identity):
            node["detail"] = symbols[identity]
    elif tag.tag_type in FRAME_TAGS:
        node["label"] = _control_label(tag)
    else:
        node["detail"] = f"#{path[-1]}"
    return node


def _frame_node(timeline, number, entries, budget):
    """A frame with its control tags as children; `timeline` is 'main' or 'tag:<index>'."""
    label = next((_frame_label_text(tag) for _, tag in entries if tag.tag_type == 43), None)
    node = _node(f"frame:{timeline}:{number}", f"Cuadro {number}", "frame", frame=number, detail=label,
                 child_count=len(entries))
    if timeline != "main":
        node["tag_index"] = int(timeline.split(":", 1)[1])
    children = []
    for path, tag in entries:
        if not budget.take():
            break
        children.append(_tag_node(path, tag))
    if children:
        node["children"] = children
    return node


def _split_name(pool, index):
    """(package, local name) of a QName multiname; other kinds keep the readable form."""
    try:
        multiname = pool.multinames[index]
    except IndexError:
        multiname = None
    if multiname and multiname.get("kind") in (0x07, 0x0D):
        package = resolve_namespace(pool, multiname["ns"])
        name_index = multiname["name"]
        name = pool.strings[name_index] if name_index < len(pool.strings) else f"str_{name_index}"
        return package, name
    package, _, name = resolve_multiname(pool, index).rpartition("::")
    return package, name


def _script_node(unit, *, label=None, detail=None):
    node = _node("script:" + unit.document_id, label or unit.name, "script", unit_id=unit.document_id,
                 language=unit.language, tag_index=unit.tag_path[0] if unit.tag_path else None,
                 detail=detail, bytes=len(unit.code))
    if unit.diagnostic:
        node["error"] = unit.diagnostic
    return node


def _packages(root_children, default_label, prefix="pkg:"):
    """Return find-or-create for nested package nodes of dotted names ('' is the default package)."""
    cache = {}

    def find(name):
        if name in cache:
            return cache[name]
        parts = [part for part in name.split(".") if part]
        parent_children, path, node = root_children, "", None
        if not parts:
            node = _node(prefix, default_label, "package", children=[])
            parent_children.append(node)
        for part in parts:
            path = f"{path}.{part}" if path else part
            node = cache.get(path)
            if node is None:
                node = _node(prefix + path, part, "package", children=[])
                parent_children.append(node)
                cache[path] = node
            parent_children = node["children"]
        cache[name] = node
        return node

    return find


def _method_traits(traits):
    """(trait, method index, role) for the traits that own a method body."""
    for trait in traits:
        kind = trait.kind_flags & 0x0F
        if kind == 1:
            yield trait, trait.method, "method"
        elif kind == 2:
            yield trait, trait.method, "getter"
        elif kind == 3:
            yield trait, trait.method, "setter"
        elif kind == 5:
            yield trait, trait.function, "function"


def _sort_packages(nodes):
    """Drop packages left empty by the node budget; packages first, then classes, the anonymous bucket last."""
    for item in nodes:
        if item["kind"] == "package":
            _sort_packages(item["children"])
            item["child_count"] = len(item["children"])
    nodes[:] = [item for item in nodes if item["kind"] != "package" or item["children"]]
    nodes.sort(key=lambda item: (item["kind"] != "package", item["label"].startswith("("), item["label"].lower()))


def _count(nodes):
    return sum(1 + _count(node.get("children", ())) for node in nodes)


def _stamp_folder(nodes, key):
    """Every node says which top-level folder owns it (the interface picks icons from it)."""
    for node in nodes:
        node.setdefault("folder", key)
        _stamp_folder(node.get("children", ()), key)


def _trim(nodes, allowance, state):
    """Keep at most `allowance` nodes (depth-first, in place); returns the allowance left."""
    kept = []
    for node in nodes:
        if allowance <= 0:
            state["dropped"] = True
            break
        allowance -= 1
        kept.append(node)
        if node.get("children"):
            allowance = _trim(node["children"], allowance, state)
    nodes[:] = kept
    return allowance


def _as3_tree(units, budget, scripts_children):
    """Packages → classes (with their members) for every ABC block of the movie."""
    blocks = {}
    for unit in units:
        if unit.language == "as3" and not unit.diagnostic and unit.abc is not None:
            blocks.setdefault(unit.tag_path, []).append(unit)
    package = _packages(scripts_children, "‹paquete predeterminado›")
    classes = methods = 0
    for path, block in sorted(blocks.items()):
        abc = block[0].abc
        pool = abc.constant_pool
        by_method = {unit.method_index: unit for unit in block}
        claimed = set()
        prefix = "tag:" + "/".join(map(str, path))

        def claim(method, role, class_name, members):
            unit = by_method.get(method)
            if unit is None or method in claimed:
                return
            claimed.add(method)
            name = unit.name[len(class_name) + 1:] if unit.name.startswith(class_name + ".") else unit.name
            members.append({"unit_id": unit.document_id, "name": name, "role": role})

        class_nodes = {}
        for number, instance in enumerate(abc.instances):
            package_name, local = _split_name(pool, instance.name)
            full = resolve_multiname(pool, instance.name)
            members = []
            claim(instance.iinit, "constructor", full, members)
            if number < len(abc.classes):
                claim(abc.classes[number].cinit, "static_init", full, members)
            for _, method, role in _method_traits(instance.traits):
                claim(method, role, full, members)
            if number < len(abc.classes):
                for _, method, role in _method_traits(abc.classes[number].traits):
                    claim(method, "static_" + role, full, members)
            node = _node(f"class:{prefix}/class:{number}", local or full, "class", language="as3", tag_index=path[0],
                         document_id=f"{prefix}/class:{number}", members=members, detail=package_name or None,
                         child_count=len(members))
            if len(path) > 1:
                node["tag_path"] = list(path)
            class_nodes[number] = (package_name, node)
        for script in abc.scripts:
            owned_classes = [trait.class_idx for trait in script.traits if trait.kind_flags & 0x0F == 4]
            owner = class_nodes.get(owned_classes[0]) if owned_classes else None
            if owner is not None:
                claim(script.init, "script_init", "", owner[1]["members"])
                owner[1]["child_count"] = len(owner[1]["members"])
                continue
            for trait, method, _ in _method_traits(script.traits):
                unit = by_method.get(method)
                if unit is None or method in claimed or not budget.take():
                    continue
                claimed.add(method)
                package_name, local = _split_name(pool, trait.name)
                package(package_name)["children"].append(_script_node(unit, label=local or unit.name, detail="función de paquete"))
                methods += 1
        for package_name, node in class_nodes.values():
            if budget.take():
                package(package_name)["children"].append(node)
                classes += 1
                methods += len(node["members"])
        leftovers = [unit for unit in block if unit.method_index not in claimed]
        if leftovers and budget.take():
            members = [{"unit_id": unit.document_id, "name": unit.name, "role": "other"} for unit in leftovers]
            package("")["children"].append(
                _node(f"class:{prefix}/other", "(otras funciones)", "class", language="as3", tag_index=path[0],
                      members=members, child_count=len(members), detail="métodos anónimos y cierres"))
            methods += len(members)
    _sort_packages(scripts_children)
    return classes, methods


def _identity(tag):
    return tag.char_id


def _as2_tree(units, movie, symbols, budget, scripts_children):
    """Frame/sprite/button/init-action nodes holding the AVM1 script units."""
    tags = movie.tags
    main_numbers = _frame_numbers([tag.tag_type for tag in tags])
    frames, sprites, buttons, inits, sprite_numbers = {}, {}, {}, [], {}
    count = 0

    def numbers_of_sprite(index):
        if index not in sprite_numbers:
            try:
                sprite_numbers[index] = _frame_numbers([tag.tag_type for _, tag in nested_tags(tags[index].data[4:])])
            except (ValueError, IndexError):
                sprite_numbers[index] = []
        return sprite_numbers[index]

    def describe(unit):
        suffix = unit.document_id.rsplit("/", 1)[-1]
        ordinal = int(suffix.split(":", 1)[1]) + 1 if ":" in suffix else 1
        if unit.kind in ("button", "button2"):
            flags = f"condiciones 0x{unit.event_flags:04x}" if unit.event_flags is not None else None
            return f"Acción de botón {ordinal}", flags
        if unit.kind == "clip":
            return f"Acción de clip {ordinal}", (f"eventos 0x{unit.event_flags:x}" if unit.event_flags is not None else None)
        return (unit.tag.name if unit.tag is not None else unit.name), None

    for unit in units:
        if unit.language != "as2" or unit.diagnostic or not budget.take():
            continue
        count += 1
        label, detail = describe(unit)
        node = _script_node(unit, label=label, detail=detail)
        code = unit.tag.tag_type if unit.tag is not None else None
        top = unit.tag_path[0]
        if code in (7, 34):
            holder = buttons.get(top)
            if holder is None:
                identity = _identity(unit.tag)
                holder = _node(f"scripts:button:{top}", f"{unit.tag.name} ({identity})", "tag", tag_index=top, tag_type=code,
                               tag_name=unit.tag.name, character_id=identity, detail=symbols.get(identity), children=[])
                buttons[top] = holder
            holder["children"].append(node)
        elif code == 59:
            inits.append((unit, node))
        elif len(unit.tag_path) == 1:
            number = main_numbers[top] if top < len(main_numbers) else 1
            frame = frames.get(number)
            if frame is None:
                frame = _node(f"scripts:frame:main:{number}", f"Cuadro {number}", "frame", frame=number, children=[])
                frames[number] = frame
            frame["children"].append(node)
        else:
            holder = sprites.get(top)
            if holder is None:
                identity = _identity(tags[top])
                holder = {"node": _node(f"scripts:sprite:{top}", f"{tags[top].name} ({identity})", "tag", tag_index=top,
                                        tag_type=39, tag_name=tags[top].name, character_id=identity,
                                        detail=symbols.get(identity), children=[]), "frames": {}}
                sprites[top] = holder
            numbers = numbers_of_sprite(top)
            number = numbers[unit.tag_path[1]] if unit.tag_path[1] < len(numbers) else 1
            frame = holder["frames"].get(number)
            if frame is None:
                frame = _node(f"scripts:frame:tag:{top}:{number}", f"Cuadro {number}", "frame", frame=number,
                              tag_index=top, children=[])
                holder["frames"][number] = frame
                holder["node"]["children"].append(frame)
            frame["children"].append(node)

    grouped = [frames[number] for number in sorted(frames)]
    grouped += [sprites[key]["node"] for key in sorted(sprites)]
    grouped += [buttons[key] for key in sorted(buttons)]
    if inits:
        holder = _node("scripts:init", "Inicializadores", "package", children=[])
        package = _packages(holder["children"], "", prefix="scripts:init:")
        for unit, node in inits:
            identity = _u16(unit.tag.data, 0)
            export = symbols.get(identity, "")
            if export.startswith("__Packages.") and len(export) > len("__Packages."):
                *folders, leaf = export.split(".")[1:]
                node["label"], node["detail"] = leaf, "clase AS2"
                (package(".".join(folders)) if folders else holder)["children"].append(node)
            else:
                node["label"] = f"DoInitAction ({identity})"
                node["detail"] = export or None
                holder["children"].append(node)
        _sort_packages(holder["children"])
        grouped.append(holder)
    for node in grouped:
        node["child_count"] = len(node["children"])
        for child in node["children"]:
            if "children" in child:
                child["child_count"] = len(child["children"])
    scripts_children.extend(grouped)
    return count


def _sources_folder(source_documents):
    documents = source_documents or {}
    authored = {document.get("filename") for key, document in documents.items() if key.startswith("project:source:")}
    files = []
    for key, document in documents.items():
        filename = document.get("filename", key)
        if not key.startswith("project:") or (key == "project:entry" and filename in authored):
            continue
        language = str(document.get("language", "as"))
        files.append(_node("source:" + key, filename, "source", source_id=key, language=language, editable=True,
                           bytes=len(document.get("source", "").encode("utf-8")), detail=language.upper()))
    files.sort(key=lambda item: item["label"].lower())
    return files


def build_tag_tree(movie, units, source_documents=None, *, limit=DEFAULT_TREE_NODES):
    """Return {'nodes', 'node_count', 'truncated', 'limit'} for the JPEXS-like tree of `movie`."""
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("Límite de nodos inválido")
    limit = max(1, min(MAX_TREE_NODES, limit))
    budget = _Budget(limit)
    tags = movie.tags
    symbols = collect_symbol_names(tags)

    # Scripts are built first so they keep priority when the budget is small.
    scripts = []
    classes, methods = _as3_tree(units, budget, scripts)
    actions = _as2_tree(units, movie, symbols, budget, scripts)
    for unit in units:
        if unit.diagnostic and budget.take():
            scripts.append(_script_node(unit, label=f"{unit.name} (error)"))

    folders = {key: [] for key in FOLDER_ORDER}
    characters, mapped = {}, []
    has_stream_blocks = any(tag.tag_type == 19 for tag in tags)
    for index, tag in enumerate(tags):
        code = tag.tag_type
        if code in (0, 1) or code in FRAME_TAGS:
            continue
        if code in MAPPED_TAGS:
            mapped.append((index, tag))
            continue
        folder = TYPE_FOLDER.get(code) or ("sounds" if code in SOUND_STREAM_HEADS and has_stream_blocks else "others")
        if not budget.take():
            continue
        node = _tag_node((index,), tag, symbols, character=folder != "others")
        if code == 39:
            try:
                nested = [((index, child_index), child) for child_index, child in nested_tags(tag.data[4:])]
            except ValueError as error:
                node["error"] = str(error)
                nested = []
            sprite_frames = _split_frames(nested)
            node["child_count"] = len(sprite_frames)
            children = []
            for number, entries in sprite_frames:
                if not budget.take():
                    break
                children.append(_frame_node(f"tag:{index}", number, entries, budget))
            if children:
                node["children"] = children
        if folder != "others" and tag.char_id is not None:
            characters[tag.char_id] = node
        folders[folder].append(node)
    for index, tag in mapped:
        if not budget.take():
            continue
        node = _tag_node((index,), tag)
        parent = characters.get(_u16(tag.data, 0))
        if parent is None:
            folders["others"].append(node)
        else:
            parent.setdefault("children", []).append(node)
            parent["child_count"] = len(parent["children"])
    for number, entries in _split_frames([((index,), tag) for index, tag in enumerate(tags)]):
        if not budget.take():
            break
        folders["frames"].append(_frame_node("main", number, entries, budget))
    folders["scripts"] = scripts
    folders["sources"] = [node for node in _sources_folder(source_documents) if budget.take()]

    # Containers (packages, frames) are cheap but not free: enforce the limit exactly, scripts first.
    state, allowance = {"dropped": False}, limit
    for key in ("scripts", "sources", *(key for key in FOLDER_ORDER if key not in ("scripts", "sources"))):
        allowance = _trim(folders[key], allowance, state)

    roots = [_node("header", "Cabecera", "header")]
    for key in FOLDER_ORDER:
        if not folders[key]:
            continue
        detail = None
        if key == "scripts":
            detail = " · ".join(part for part in (f"{classes} clases" if classes else "", f"{methods} métodos" if methods else "",
                                                  f"{actions} acciones" if actions else "") if part) or None
        _stamp_folder(folders[key], key)
        roots.append(_node(f"folder:{key}", FOLDER_LABELS[key], "folder", folder=key, children=folders[key],
                           child_count=len(folders[key]), detail=detail))
    return {"nodes": roots, "node_count": _count(roots), "truncated": budget.truncated or state["dropped"], "limit": limit}


def _int_argument(args, name, default, *, minimum, maximum):
    value = args.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"Argumento inválido: {name}")
    return value


def resolve_tag(movie, args):
    """Find the tag a request refers to: top-level `tag_index` or a `tag_path` into a sprite."""
    path = args.get("tag_path")
    if path is None:
        index = args.get("tag_index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(movie.tags):
            raise ValueError("Tag desconocido")
        return (index,), movie.tags[index]
    if (not isinstance(path, list) or not 1 <= len(path) <= 2
            or any(not isinstance(part, int) or isinstance(part, bool) or part < 0 for part in path)):
        raise ValueError("Ruta de tag inválida")
    if path[0] >= len(movie.tags):
        raise ValueError("Tag desconocido")
    parent = movie.tags[path[0]]
    if len(path) == 1:
        return (path[0],), parent
    if parent.tag_type != 39:
        raise ValueError("La ruta no apunta a un sprite")
    for child_index, child in nested_tags(parent.data[4:]):
        if child_index == path[1]:
            return tuple(path), child
    raise ValueError("Tag anidado desconocido")


def read_raw(movie, working, args):
    """One page of a tag payload (or of the whole file with scope='file')."""
    offset = _int_argument(args, "offset", 0, minimum=0, maximum=2**40)
    limit = _int_argument(args, "limit", DEFAULT_RAW_PAGE, minimum=1, maximum=MAX_RAW_PAGE)
    encoding = args.get("encoding", "hex")
    if encoding not in ("hex", "base64"):
        raise ValueError("Codificación desconocida: usa hex o base64")
    result = {"offset": offset, "limit": limit, "encoding": encoding}
    if args.get("scope") == "file":
        payload = working
        result.update(scope="file", name="Archivo SWF")
    else:
        path, tag = resolve_tag(movie, args)
        payload = tag.data
        result.update(scope="tag", tag_index=path[0], name=tag.name, type=tag.tag_type)
        if len(path) > 1:
            result["tag_path"] = list(path)
    total = len(payload)
    if offset > total:
        raise ValueError("Desplazamiento fuera del contenido")
    page = payload[offset:offset + limit]
    result.update(total=total, length=len(page))
    if encoding == "hex":
        result["hex"] = page.hex()
    else:
        result["base64"] = base64.b64encode(page).decode("ascii")
    return result


def read_header(movie, working, *, filename="", script_count=None):
    """Container and movie summary shown by the header node."""
    if len(working) < 8:
        raise ValueError("Cabecera SWF incompleta")
    signature = working[:3].decode("ascii", errors="replace")
    rect = movie.rect
    attributes = next((tag for tag in movie.tags if tag.tag_type == 69 and tag.data), None)
    flags = attributes.data[0] if attributes else None
    metadata = next((tag for tag in movie.tags if tag.tag_type == 77), None)
    abc_blocks = sum(1 for tag in movie.tags if tag.tag_type in (72, 82))
    result = {
        "filename": filename, "signature": signature, "compression": COMPRESSION.get(signature, "unknown"),
        "version": working[3], "file_length": int.from_bytes(working[4:8], "little"), "file_size": len(working),
        "frame_size": {"xmin": rect["xmin"], "xmax": rect["xmax"], "ymin": rect["ymin"], "ymax": rect["ymax"],
                       "width": (rect["xmax"] - rect["xmin"]) / 20.0, "height": (rect["ymax"] - rect["ymin"]) / 20.0},
        "frame_rate": movie.frame_rate, "frame_count": movie.frame_count,
        "shown_frames": sum(1 for tag in movie.tags if tag.tag_type == 1),
        "tag_count": len(movie.tags), "abc_blocks": abc_blocks,
        "has_as3": bool(flags is not None and flags & 0x08) or abc_blocks > 0,
        "has_metadata": metadata is not None or bool(flags is not None and flags & 0x10),
        "file_attributes": ({name: bool(flags & bit) for name, bit in FILE_ATTRIBUTE_FLAGS} if flags is not None else None),
        "metadata": (metadata.data.split(b"\0", 1)[0].decode("utf-8", errors="replace")[:MAX_METADATA_CHARS]
                     if metadata is not None else None),
        "sha256": sha(working),
    }
    if script_count is not None:
        result["script_count"] = script_count
    return result
