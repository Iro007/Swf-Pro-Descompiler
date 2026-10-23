"""Read-only suite operations behind the single-window UI: tag_tree, raw, header and resource_preview."""
import base64
import io
import json
import zlib

import pytest

from py_swf import paths
from py_swf.avm1 import assemble_avm1
from py_swf.swf_parser import SWFTag
from py_swf.universal import UniversalSuite
from tests.conftest import make_synthetic_swf
from tests.test_swf import _build_triangle_shape_tag

CORPUS = paths.CORPUS / "parity-20261014" / "optimized" / "swf"
AS3_FWS = CORPUS / "dev-as3-closures" / "release.fws.swf"
AS2_FWS = CORPUS / "dev-as2-timeline" / "release.fws.swf"


def corpus_file(path):
    assert path.is_file(), f"El corpus sellado no contiene {path}"
    return path


@pytest.fixture
def make_suite(tmp_path):
    created = []

    def create(source):
        if isinstance(source, bytes):
            path = tmp_path / f"movie-{len(created)}.swf"
            path.write_bytes(source)
        else:
            path = corpus_file(source)
        suite = UniversalSuite(tmp_path / f"state-{len(created)}", tmp_path / "tools.json")
        suite.open(path)
        created.append(suite)
        return suite

    yield create
    for suite in created:
        suite.close()


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get("children", []))


def root(tree, key):
    return next((node for node in tree["nodes"] if node["id"] == key), None)


def place(depth, character):
    return SWFTag(26, bytes([0x02]) + depth.to_bytes(2, "little") + character.to_bytes(2, "little"))


def sprite(character, frames):
    body = b""
    for tags in frames:
        body += b"".join(tag.pack() for tag in tags) + SWFTag(1, b"").pack()
    body += SWFTag(0, b"").pack()
    return SWFTag(39, character.to_bytes(2, "little") + len(frames).to_bytes(2, "little") + body)


def red_square(character, size=4):
    raw = b"".join(b"\x00\xff\x00\x00" for _ in range(size * size))  # PIX24 is 0RGB
    return SWFTag(20, character.to_bytes(2, "little") + bytes([5]) + size.to_bytes(2, "little") * 2 + zlib.compress(raw))


def rich_movie():
    """Shape, image, sprite with two frames, font + font name, symbol class and AVM1 code in two timelines."""
    return make_synthetic_swf([
        SWFTag(9, b"\xff\xff\xff"),                                    # 0 SetBackgroundColor
        _build_triangle_shape_tag(),                                    # 1 DefineShape (1)
        red_square(2),                                                  # 2 DefineBitsLossless (2)
        sprite(3, [[place(1, 1)], [SWFTag(12, assemble_avm1("push 1\ntrace\nend")), SWFTag(28, (1).to_bytes(2, "little"))]]),  # 3
        SWFTag(75, (4).to_bytes(2, "little") + b"\x00" * 8),            # 4 DefineFont3 (4)
        SWFTag(88, (4).to_bytes(2, "little") + b"\x00" * 4),            # 5 DefineFontName for font 4
        SWFTag(76, b"\x01\x00\x03\x00Ball\x00"),                        # 6 SymbolClass: 3 -> Ball
        place(1, 3),                                                    # 7 PlaceObject2 in frame 1
        SWFTag(43, b"intro\x00"),                                       # 8 FrameLabel
        SWFTag(1, b""),                                                 # 9 ShowFrame
        SWFTag(12, assemble_avm1('push "hola"\ntrace\nend')),           # 10 DoAction in frame 2
        SWFTag(1, b""),                                                 # 11 ShowFrame
    ])


def test_as3_tree_groups_classes_and_accounts_for_every_unit(make_suite):
    suite = make_suite(AS3_FWS)
    tree = suite.dispatch("tag_tree", {})
    assert json.dumps(tree, allow_nan=False)
    assert tree["revision"] == suite.state()["revision"] and tree["filename"] == "release.fws.swf"
    assert not tree["truncated"] and tree["node_count"] == sum(1 for _ in walk(tree["nodes"]))
    assert [node["id"] for node in tree["nodes"]] == ["header", "folder:frames", "folder:others", "folder:scripts"]
    assert tree["nodes"][0]["kind"] == "header"
    ids = [node["id"] for node in walk(tree["nodes"])]
    assert len(ids) == len(set(ids)), "Cada nodo necesita una identidad única"
    owners = {node["id"]: node.get("folder") for node in walk(tree["nodes"]) if node["kind"] != "header"}
    assert all(owners.values()), "Cada nodo indica la carpeta que lo contiene"
    assert {owners[node["id"]] for node in walk(root(tree, "folder:scripts")["children"])} == {"scripts"}

    scripts = root(tree, "folder:scripts")
    package = scripts["children"][0]
    assert package["kind"] == "package" and package["label"] == "‹paquete predeterminado›"
    main = next(node for node in package["children"] if node["label"] == "Main")
    assert main["kind"] == "class" and main["document_id"] == "tag:6/class:0" and main["tag_index"] == 6
    roles = {member["role"] for member in main["members"]}
    assert {"constructor", "static_init", "method", "script_init"} <= roles
    assert next(member for member in main["members"] if member["name"] == "make")["role"] == "method"

    unit_ids = {unit.document_id for unit in suite.units()}
    listed = [member["unit_id"] for node in walk(tree["nodes"]) if node["kind"] == "class" for member in node["members"]]
    listed += [node["unit_id"] for node in walk(tree["nodes"]) if node["kind"] == "script"]
    assert sorted(listed) == sorted(unit_ids), "Cada unidad de código aparece exactamente una vez"
    assert any(node["label"] == "(otras funciones)" for node in package["children"])

    labels = [node["label"] for node in walk(root(tree, "folder:frames")["children"])]
    assert labels == ["Cuadro 1", "FrameLabel: Main"]
    assert {node["label"] for node in root(tree, "folder:others")["children"]} >= {"DoABC", "SymbolClass", "Metadata"}


def test_as2_tree_places_actions_under_their_frames(make_suite):
    suite = make_suite(AS2_FWS)
    tree = suite.dispatch("tag_tree", {})
    scripts = root(tree, "folder:scripts")
    frame = scripts["children"][0]
    assert frame["kind"] == "frame" and frame["label"] == "Cuadro 1" and frame["frame"] == 1
    action = frame["children"][0]
    assert action["kind"] == "script" and action["language"] == "as2" and action["unit_id"] == "tag:2/actions"
    assert [unit.document_id for unit in suite.units()] == [action["unit_id"]]


def test_synthetic_movie_follows_the_jpexs_folders(make_suite):
    suite = make_suite(rich_movie())
    tree = suite.dispatch("tag_tree", {})
    assert [node["id"] for node in tree["nodes"]] == [
        "header", "folder:shapes", "folder:sprites", "folder:images", "folder:fonts", "folder:frames", "folder:others", "folder:scripts"]
    assert "folder:texts" not in {node["id"] for node in tree["nodes"]}, "Las carpetas vacías se omiten"
    shape = root(tree, "folder:shapes")["children"][0]
    assert shape["label"] == "DefineShape (1)" and shape["character_id"] == 1 and shape["tag_index"] == 1
    image = root(tree, "folder:images")["children"][0]
    assert image["character_id"] == 2 and image["tag_type"] == 20

    sprite_node = root(tree, "folder:sprites")["children"][0]
    assert sprite_node["label"] == "DefineSprite (3)" and sprite_node["detail"] == "Ball" and sprite_node["child_count"] == 2
    first, second = sprite_node["children"]
    assert [first["kind"], first["label"], second["label"]] == ["frame", "Cuadro 1", "Cuadro 2"]
    assert first["children"][0]["label"] == "PlaceObject2 · profundidad 1 → 1"
    assert first["children"][0]["tag_path"] == [3, 0]
    assert [child["label"] for child in second["children"]] == ["RemoveObject2 · profundidad 1"]

    font = root(tree, "folder:fonts")["children"][0]
    assert [child["label"] for child in font["children"]] == ["DefineFontName"] and font["child_count"] == 1

    frames = root(tree, "folder:frames")["children"]
    assert [frame["label"] for frame in frames] == ["Cuadro 1", "Cuadro 2"] and frames[0]["detail"] == "intro"
    assert [child["label"] for child in frames[0]["children"]] == ["PlaceObject2 · profundidad 1 → 3", "FrameLabel: intro"]

    others = {node["label"] for node in root(tree, "folder:others")["children"]}
    assert {"SetBackgroundColor", "SymbolClass", "DoAction"} <= others

    scripts = root(tree, "folder:scripts")["children"]
    assert scripts[0]["id"] == "scripts:frame:main:2" and scripts[0]["children"][0]["unit_id"] == "tag:10/actions"
    nested = next(node for node in scripts if node["id"] == "scripts:sprite:3")
    assert nested["label"] == "DefineSprite (3)" and nested["children"][0]["label"] == "Cuadro 2"
    assert nested["children"][0]["children"][0]["unit_id"] == "tag:3/2/actions"
    ids = [node["id"] for node in walk(tree["nodes"])]
    assert len(ids) == len(set(ids))


def test_node_budget_truncates_instead_of_failing(make_suite):
    suite = make_suite(rich_movie())
    small = suite.dispatch("tag_tree", {"limit": 3})
    assert small["truncated"] and small["limit"] == 3
    content = [node for root_node in small["nodes"] if root_node["kind"] == "folder" for node in walk(root_node["children"])]
    assert len(content) == 3, "El límite cuenta todos los nodos bajo las carpetas"
    assert small["nodes"][0]["kind"] == "header" and small["nodes"][-1]["id"] == "folder:scripts"
    assert small["node_count"] == sum(1 for _ in walk(small["nodes"]))
    full = suite.dispatch("tag_tree", {})
    assert not full["truncated"] and full["node_count"] > small["node_count"]
    with pytest.raises(ValueError, match="Límite"):
        suite.dispatch("tag_tree", {"limit": "3"})
    assert suite.dispatch("tag_tree", {"limit": 10**9})["limit"] == 100_000


def test_tree_shrinks_instead_of_exceeding_the_worker_reply_limit(make_suite, monkeypatch):
    import py_swf.tag_tree as tree_module
    suite = make_suite(make_synthetic_swf([_build_triangle_shape_tag() for _ in range(600)]))
    full = suite.dispatch("tag_tree", {})
    size = len(json.dumps(full, ensure_ascii=False, separators=(",", ":")))
    assert not full["truncated"] and full["node_count"] > 600
    monkeypatch.setattr(tree_module, "MAX_TREE_JSON_BYTES", size // 3)
    smaller = suite.dispatch("tag_tree", {"limit": 19_999})  # a different request, so the cache is not reused
    assert smaller["truncated"] and smaller["limit"] < 600 and smaller["node_count"] < full["node_count"]
    assert len(json.dumps(smaller, ensure_ascii=False, separators=(",", ":"))) <= size // 3


def test_authored_sources_get_their_own_folder(make_suite):
    suite = make_suite(rich_movie())
    suite.project.source_documents.update({
        "project:source:Main.as": {"source": "package {}", "filename": "Main.as", "language": "as3"},
        "project:entry": {"source": "package {}", "filename": "Main.as", "language": "as3"},
        "tag:3/actions": {"source": "trace(1);", "engine": "python"},
    })
    folder = root(suite.dispatch("tag_tree", {}), "folder:sources")
    assert [(node["kind"], node["label"], node["source_id"]) for node in folder["children"]] == [("source", "Main.as", "project:source:Main.as")]


def test_raw_pages_tags_nested_tags_and_the_file(make_suite):
    suite = make_suite(rich_movie())
    movie = suite.movie()
    payload = movie.tags[7].data
    page = suite.dispatch("raw", {"tag_index": 7})
    assert page["total"] == len(payload) and page["length"] == len(payload) and page["offset"] == 0
    assert bytes.fromhex(page["hex"]) == payload and page["name"] == "PlaceObject2" and page["revision"] == suite.state()["revision"]
    middle = suite.dispatch("raw", {"tag_index": 7, "offset": 1, "limit": 2})
    assert bytes.fromhex(middle["hex"]) == payload[1:3] and middle["length"] == 2 and middle["total"] == len(payload)
    encoded = suite.dispatch("raw", {"tag_index": 7, "encoding": "base64"})
    assert base64.b64decode(encoded["base64"]) == payload and "hex" not in encoded
    assert suite.dispatch("raw", {"tag_index": 7, "offset": len(payload)})["length"] == 0
    nested = suite.dispatch("raw", {"tag_path": [3, 0]})
    assert nested["tag_path"] == [3, 0] and bytes.fromhex(nested["hex"]) == place(1, 1).data
    whole = suite.dispatch("raw", {"scope": "file", "limit": 8})
    assert bytes.fromhex(whole["hex"]) == suite.project.working[:8] and whole["total"] == len(suite.project.working)
    big = suite.dispatch("raw", {"scope": "file", "limit": 64 * 1024})
    assert big["length"] == min(64 * 1024, len(suite.project.working))


@pytest.mark.parametrize("arguments, message", [
    ({"tag_index": 999}, "Tag desconocido"), ({"tag_index": -1}, "Tag desconocido"), ({"tag_index": True}, "Tag desconocido"),
    ({"tag_index": 0, "offset": 10**6}, "fuera"), ({"tag_index": 0, "offset": -1}, "inválido"),
    ({"tag_index": 0, "limit": 64 * 1024 + 1}, "inválido"), ({"tag_index": 0, "limit": 0}, "inválido"),
    ({"tag_index": 0, "encoding": "utf8"}, "Codificación"), ({"tag_path": [0, 0]}, "sprite"),
    ({"tag_path": [3, 99]}, "anidado"), ({"tag_path": [3, 0, 0]}, "Ruta"), ({}, "Tag desconocido"),
])
def test_raw_rejects_bad_requests(make_suite, arguments, message):
    suite = make_suite(rich_movie())
    with pytest.raises(ValueError, match=message):
        suite.dispatch("raw", arguments)


@pytest.mark.parametrize("name, compression", [("release.fws.swf", "none"), ("release.cws.swf", "zlib"), ("release.zws.swf", "lzma")])
def test_header_reports_container_and_movie(make_suite, name, compression):
    suite = make_suite(corpus_file(CORPUS / "dev-as3-closures" / name))
    header = suite.dispatch("header", {})
    assert header["compression"] == compression and header["signature"] == name.split(".")[1].upper()
    assert header["version"] == 38 and header["file_length"] == 997 and header["file_size"] == len(suite.project.working)
    assert header["frame_size"] == {"xmin": 0, "xmax": 10000, "ymin": 0, "ymax": 7500, "width": 500.0, "height": 375.0}
    assert (header["frame_rate"], header["frame_count"], header["shown_frames"], header["tag_count"]) == (24.0, 1, 1, 10)
    assert header["has_as3"] and header["has_metadata"] and header["abc_blocks"] == 1 and header["script_count"] == 5
    assert header["file_attributes"]["actionscript3"] is True and "Apache Flex" in header["metadata"]
    assert header["sha256"] == header["revision"] == suite.state()["revision"]


def test_header_of_an_avm1_movie_has_no_as3(make_suite):
    header = make_suite(AS2_FWS).dispatch("header", {})
    assert header["version"] == 15 and not header["has_as3"] and not header["has_metadata"] and header["abc_blocks"] == 0
    assert header["metadata"] is None and header["tag_count"] == 5


def test_read_operations_do_not_change_the_session(make_suite):
    suite = make_suite(rich_movie())
    before = suite.state()
    for operation, arguments in (("tag_tree", {}), ("header", {}), ("raw", {"tag_index": 1}), ("resource_preview", {"tag_index": 1})):
        suite.dispatch(operation, arguments)
    after = suite.state()
    assert after["revision"] == before["revision"] and not after["dirty"] and not after["history"]["undo"]


def test_operations_need_an_open_movie(tmp_path):
    suite = UniversalSuite(tmp_path / "empty", tmp_path / "tools.json")
    for operation in ("tag_tree", "header", "raw", "resource_preview"):
        with pytest.raises(ValueError, match="Abre un SWF"):
            suite.dispatch(operation, {"tag_index": 0})
    suite.close()


def test_tree_and_header_follow_edits(make_suite):
    from py_swf.engines import NativePythonEngine
    suite = make_suite(NativePythonEngine().compile_classes("package { public class Counter { public function answer():int { return 7; } } }"))
    first, header = suite.dispatch("tag_tree", {}), suite.dispatch("header", {})
    unit = next(u for u in suite.units() if "answer" in u.name)
    suite.edit({"unit_id": unit.document_id, "code": "return 9;", "mode": "source", "base_revision": suite.state()["revision"]})
    second = suite.dispatch("tag_tree", {})
    assert second["revision"] == suite.state()["revision"] != first["revision"]
    assert [n["id"] for n in walk(second["nodes"])] == [n["id"] for n in walk(first["nodes"])]
    assert suite.dispatch("header", {})["sha256"] != header["sha256"]
    counter = next(node for node in walk(second["nodes"]) if node["kind"] == "class" and node["label"] == "Counter")
    assert any(member["name"] == "answer" for member in counter["members"])


def test_previews_of_shapes_images_and_sprites(make_suite):
    suite = make_suite(rich_movie())
    shape = suite.dispatch("resource_preview", {"tag_index": 1})
    assert (shape["kind"], shape["mime"], shape["encoding"]) == ("shape", "image/svg+xml", "text")
    assert shape["data"].startswith("<svg") and "rgba(255,0,0,1.000)" in shape["data"] and shape["name"] == "DefineShape"
    image = suite.dispatch("resource_preview", {"tag_index": 2})
    assert (image["kind"], image["mime"], image["encoding"], image["width"], image["height"]) == ("image", "image/png", "base64", 4, 4)
    from PIL import Image
    with Image.open(io.BytesIO(base64.b64decode(image["data"]))) as decoded:
        assert decoded.getpixel((0, 0))[:3] == (255, 0, 0)
    second = suite.dispatch("resource_preview", {"tag_index": 3, "frame": 1})
    assert (second["kind"], second["frame"], second["frame_count"]) == ("sprite", 1, 2) and second["data"].startswith("<svg")
    clamped = suite.dispatch("resource_preview", {"tag_index": 3, "frame": 40})
    assert clamped["frame"] == 1
    for arguments in ({"tag_index": 0}, {"tag_index": 99}, {"tag_index": 3, "frame": -1}, {"tag_index": 3, "frame": True}):
        with pytest.raises(ValueError):
            suite.dispatch("resource_preview", arguments)
