import pytest

from py_swf.abc_symbols import AbcSymbolIndex, inspect_qname
from py_swf.avm2 import ABCFile, ClassInfo, InstanceInfo, ScriptInfo, TraitsInfo


def add_qname(pool, namespace_kind, package, name):
    package_index = pool.strings.index(package) if package in pool.strings else len(pool.strings)
    if package_index == len(pool.strings):
        pool.strings.append(package)
    name_index = pool.strings.index(name) if name in pool.strings else len(pool.strings)
    if name_index == len(pool.strings):
        pool.strings.append(name)
    namespace_index = len(pool.namespaces)
    pool.namespaces.append({"kind": namespace_kind, "name": package_index})
    pool.multinames.append({"kind": 0x07, "ns": namespace_index, "name": name_index})
    return len(pool.multinames) - 1


def declared_abc(namespace_kind, package, name, *, interface=False, script_trait=False):
    abc = ABCFile()
    qname = add_qname(abc.constant_pool, namespace_kind, package, name)
    instance = InstanceInfo()
    instance.name = qname
    instance.flags = 0x04 if interface else 0
    abc.instances = [instance]
    abc.classes = [ClassInfo()]
    abc.scripts = [ScriptInfo()]
    if script_trait:
        trait = TraitsInfo()
        trait.name = qname
        trait.kind_flags = 4
        trait.class_idx = 0
        abc.scripts[0].traits = [trait]
    return abc, qname


def test_public_qname_resolves_and_duplicate_declarations_stay_ambiguous():
    provider, _ = declared_abc(0x16, "tests", "e", script_trait=True)
    requester = ABCFile()
    reference = add_qname(requester.constant_pool, 0x16, "tests", "e")
    index = AbcSymbolIndex([provider, provider])
    assert len(index.declarations) == 2
    assert index.resolve_type(requester.constant_pool, reference) is None
    unique = AbcSymbolIndex([provider])
    resolved = unique.resolve_type(requester.constant_pool, reference)
    assert resolved is not None
    assert (resolved.abc_index, resolved.class_index, resolved.kind) == (0, 0, "class")


def test_private_qname_with_same_uri_as_public_never_resolves():
    provider, _ = declared_abc(0x16, "tests", "e")
    requester = ABCFile()
    private = add_qname(requester.constant_pool, 0x05, "tests", "e")
    identity = inspect_qname(requester.constant_pool, private)
    assert identity.namespace_kind == 0x05
    assert identity.pool_identity == id(requester.constant_pool)
    assert AbcSymbolIndex([provider]).resolve_type(requester.constant_pool, private) is None


def test_internal_namespace_requires_matching_owner_package():
    provider, _ = declared_abc(0x17, "alpha", "Hidden")
    requester = ABCFile()
    same = add_qname(requester.constant_pool, 0x17, "alpha", "Hidden")
    other = add_qname(requester.constant_pool, 0x17, "beta", "Hidden")
    index = AbcSymbolIndex([provider])
    assert index.resolve_type(requester.constant_pool, same) is None
    assert index.resolve_type(requester.constant_pool, same, owner_package="beta") is None
    assert index.resolve_type(requester.constant_pool, other, owner_package="beta") is None
    assert index.resolve_type(requester.constant_pool, same, owner_package="alpha") is not None


def test_interface_and_script_class_trait_are_indexed_once():
    provider, qname = declared_abc(0x16, "api", "Renderable", interface=True, script_trait=True)
    index = AbcSymbolIndex([provider])
    assert len(index.declarations) == 1
    requester = ABCFile()
    reference = add_qname(requester.constant_pool, 0x16, "api", "Renderable")
    declaration = index.resolve_type(requester.constant_pool, reference)
    assert declaration.kind == "interface"
    assert declaration.multiname_index == qname


def test_undefined_external_and_invalid_class_trait_are_not_bindings():
    requester = ABCFile()
    reference = add_qname(requester.constant_pool, 0x16, "external", "Missing")
    provider, _ = declared_abc(0x16, "other", "Existing", script_trait=True)
    bad_trait = TraitsInfo()
    bad_trait.name = provider.instances[0].name
    bad_trait.kind_flags = 4
    bad_trait.class_idx = 99
    provider.scripts[0].traits.append(bad_trait)
    index = AbcSymbolIndex([provider])
    assert index.resolve_type(requester.constant_pool, reference) is None
    assert len(index.declarations) == 1


def test_negative_pool_indexes_are_rejected_not_python_negative_indexed():
    provider, _ = declared_abc(0x16, "pkg", "Present")
    index = AbcSymbolIndex([provider])
    with pytest.raises(ValueError, match="outside"):
        inspect_qname(provider.constant_pool, -1)
    assert index.resolve_type(provider.constant_pool, -1) is None
