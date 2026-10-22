import pytest

from py_swf.avm2 import ABCFile, assemble_instructions, search_or_add_string
from py_swf.decompile.avm2_dec import decompile_method
from py_swf.editing import source_edit_reason
from tests.native_fixtures import as3_tag


def package_name_fixture(uri="sample.api", local="Widget", kind=0x16):
    seed = as3_tag()
    abc = ABCFile()
    abc.parse(seed.parse_doabc()[2])
    pool = abc.constant_pool
    namespace_name = search_or_add_string(pool, uri)
    local_name = search_or_add_string(pool, local)
    pool.namespaces.append({"kind": kind, "name": namespace_name})
    pool.multinames.append({"kind": 7, "ns": len(pool.namespaces) - 1, "name": local_name})
    index = len(pool.multinames) - 1
    return abc, index


def test_explicit_package_qname_reconstructs_as_qualified_global_name():
    abc, index = package_name_fixture()
    body = abc.method_bodies[1]
    body.code = assemble_instructions(abc.constant_pool, f"getlex #{index}\nreturnvalue")

    source, error = decompile_method(abc, body)

    assert error is None, error
    assert "return sample.api.Widget;" in source
    assert "Non-default/private namespaces" in source_edit_reason(abc, body)


def test_explicit_package_qname_on_object_receiver_is_not_flattened():
    abc, index = package_name_fixture()
    body = abc.method_bodies[1]
    body.code = assemble_instructions(abc.constant_pool, f"getlocal_1\ngetproperty #{index}\nreturnvalue")

    source, error = decompile_method(abc, body)

    assert source is None
    assert error and "Qualified package name requires a proven global receiver" in error


@pytest.mark.parametrize("kind", [0x17, 0x08, 0x19])
def test_non_package_namespaces_are_not_rewritten_as_package_paths(kind):
    abc, index = package_name_fixture(kind=kind)
    body = abc.method_bodies[1]
    body.code = assemble_instructions(abc.constant_pool, f"getlex #{index}\nreturnvalue")

    source, error = decompile_method(abc, body)

    assert source is None
    assert error and "qualified name" in error.lower()


@pytest.mark.parametrize("uri,local", [("sample.class", "Widget"), ("sample.api", "default")])
def test_unrepresentable_package_segments_remain_pcode_only(uri, local):
    abc, index = package_name_fixture(uri=uri, local=local)
    body = abc.method_bodies[1]
    body.code = assemble_instructions(abc.constant_pool, f"getlex #{index}\nreturnvalue")

    source, error = decompile_method(abc, body)

    assert source is None
    assert error and "qualified name" in error.lower()
