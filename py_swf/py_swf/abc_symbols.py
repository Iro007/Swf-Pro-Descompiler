"""Conservative declaration lookup across the ABC files in one SWF.

Package namespace kind and URI are both part of a public/internal name's
identity. Private namespace identities additionally retain their constant-pool
scope and namespace index; matching printable URIs never links private names.
"""

from dataclasses import dataclass
from typing import Sequence

from .avm2 import ABCFile


PACKAGE_NAMESPACE = 0x16
PACKAGE_INTERNAL_NAMESPACE = 0x17
PRIVATE_NAMESPACE = 0x05
_QNAME_KINDS = frozenset((0x07, 0x0D))


@dataclass(frozen=True)
class QNameIdentity:
    """A statically inspectable QName, preserving private pool identity."""

    multiname_index: int
    kind: int
    namespace_kind: int
    package: str
    name: str
    namespace_index: int
    pool_identity: int


@dataclass(frozen=True)
class SymbolDeclaration:
    abc_index: int
    class_index: int | None
    kind: str
    namespace_kind: int
    package: str
    name: str
    multiname_index: int


@dataclass(frozen=True)
class TraitOwner:
    kind: str
    index: int


@dataclass(frozen=True)
class MethodContext:
    """A unique declaring owner, never inferred from a printable method name."""
    abc_index: int
    method_index: int
    body_index: int | None
    owner: TraitOwner | None
    trait_name_index: int | None
    return_type: int
    param_types: tuple[int, ...]
    options: tuple[tuple[int, int], ...]
    diagnostics: tuple[str, ...] = ()


def inspect_qname(pool, multiname_index: int) -> QNameIdentity:
    """Return exact QName components, including private namespace identity.

    Runtime multinames and malformed pool references raise ``ValueError``.
    ``pool_identity`` and ``namespace_index`` are meaningful for private
    namespaces; their URI is intentionally not treated as an identity.
    """
    try:
        if (not isinstance(multiname_index, int) or isinstance(multiname_index, bool)
                or not 0 <= multiname_index < len(pool.multinames)):
            raise ValueError("Multiname index is outside the constant pool")
        multiname = pool.multinames[multiname_index]
        if not multiname or multiname.get("kind") not in _QNAME_KINDS:
            raise ValueError("Multiname is not a statically resolved QName")
        namespace_index = multiname["ns"]
        if (not isinstance(namespace_index, int) or isinstance(namespace_index, bool)
                or not 0 <= namespace_index < len(pool.namespaces)):
            raise ValueError("Namespace index is outside the constant pool")
        namespace = pool.namespaces[namespace_index]
        namespace_kind = namespace["kind"]
        package_index = namespace["name"]
        name_index = multiname["name"]
        if (not isinstance(package_index, int) or isinstance(package_index, bool)
                or not 0 <= package_index < len(pool.strings)
                or not isinstance(name_index, int) or isinstance(name_index, bool)
                or not 0 <= name_index < len(pool.strings)):
            raise ValueError("QName string index is outside the constant pool")
        package = pool.strings[package_index]
        name = pool.strings[name_index]
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError("QName contains an invalid constant-pool reference") from exc
    return QNameIdentity(
        multiname_index=multiname_index,
        kind=multiname["kind"],
        namespace_kind=namespace_kind,
        package=package,
        name=name,
        namespace_index=namespace_index,
        pool_identity=id(pool) if namespace_kind == PRIVATE_NAMESPACE else 0,
    )


class AbcSymbolIndex:
    """Index class/interface declarations from all ABCs in a single SWF."""

    def __init__(self, abcs: Sequence[ABCFile]):
        self.abcs = tuple(abcs)
        declarations: list[SymbolDeclaration] = []
        seen: set[SymbolDeclaration] = set()

        def add(abc_index: int, class_index: int, multiname_index: int) -> None:
            abc = self.abcs[abc_index]
            if (not isinstance(class_index, int) or isinstance(class_index, bool)
                    or not (0 <= class_index < len(abc.instances)
                            and class_index < len(abc.classes))):
                return
            try:
                identity = inspect_qname(abc.constant_pool, multiname_index)
            except ValueError:
                return
            if identity.namespace_kind not in (
                PACKAGE_NAMESPACE, PACKAGE_INTERNAL_NAMESPACE
            ):
                return
            kind = "interface" if abc.instances[class_index].flags & 0x04 else "class"
            declaration = SymbolDeclaration(
                abc_index=abc_index,
                class_index=class_index,
                kind=kind,
                namespace_kind=identity.namespace_kind,
                package=identity.package,
                name=identity.name,
                multiname_index=multiname_index,
            )
            if declaration not in seen:
                seen.add(declaration)
                declarations.append(declaration)

        for abc_index, abc in enumerate(self.abcs):
            for class_index, instance in enumerate(abc.instances):
                add(abc_index, class_index, instance.name)

            # Script traits expose class definitions. Validate both the trait's
            # multiname and its class-table reference before indexing it.
            for script in abc.scripts:
                for trait in script.traits:
                    if trait.kind_flags & 0x0F == 4:
                        add(abc_index, trait.class_idx, trait.name)

        self.declarations = tuple(declarations)
        self._types = {}
        for declaration in declarations:
            self._types.setdefault((declaration.namespace_kind, declaration.package, declaration.name), []).append(declaration)
        self._owners = {}
        for abc_index, abc in enumerate(self.abcs):
            for kind, table, init in (("instance", abc.instances, "iinit"), ("class", abc.classes, "cinit"), ("script", abc.scripts, "init")):
                for index, item in enumerate(table):
                    self._owners.setdefault((abc_index, getattr(item, init)), []).append((TraitOwner(kind, index), None))
                    for trait in item.traits:
                        member = trait.method if trait.kind_flags & 15 in (1, 2, 3) else trait.function if trait.kind_flags & 15 == 5 else None
                        if member is not None:
                            self._owners.setdefault((abc_index, member), []).append((TraitOwner(kind, index), trait.name))
        self._bodies = {}
        self.namespace_aliases = {}
        for abc_index, abc in enumerate(self.abcs):
            for body_index, body in enumerate(abc.method_bodies):
                self._bodies.setdefault((abc_index, body.method), []).append(body_index)
            for script in abc.scripts:
                for trait in script.traits:
                    if trait.kind_flags & 15 not in (0, 6) or trait.vkind != 8:
                        continue
                    try:
                        alias = inspect_qname(abc.constant_pool, trait.name)
                        namespace = abc.constant_pool.namespaces[trait.vindex]
                        uri = abc.constant_pool.strings[namespace["name"]]
                        if alias.kind == 7 and alias.namespace_kind in (0x16, 0x17) and namespace["kind"] == 8:
                            self.namespace_aliases.setdefault((8, uri), []).append((abc_index, alias))
                    except (ValueError, IndexError, KeyError):
                        continue

    def method_context(self, abc_index: int, method_index: int) -> MethodContext:
        abc = self.abcs[abc_index]
        if not 0 <= method_index < len(abc.methods):
            raise ValueError("Method index is outside the ABC")
        owners = self._owners.get((abc_index, method_index), [])
        bodies = self._bodies.get((abc_index, method_index), [])
        method = abc.methods[method_index]
        diagnostics = []
        if len(owners) != 1:
            diagnostics.append("Method has no unique declaring trait owner")
        if len(bodies) > 1:
            diagnostics.append("Method has duplicate bytecode bodies")
        return MethodContext(abc_index, method_index, bodies[0] if len(bodies) == 1 else None,
                             owners[0][0] if len(owners) == 1 else None,
                             owners[0][1] if len(owners) == 1 else None,
                             method.return_type, tuple(method.param_types),
                             tuple((option.get("val", 0), option.get("kind", 0)) for option in method.options),
                             tuple(diagnostics))

    def ancestors(self, abc_index: int, class_index: int) -> tuple[tuple[int, int], ...]:
        """Nearest-first hierarchy, stopping at an external or ambiguous base."""
        result, seen = [], {(abc_index, class_index)}
        while True:
            abc = self.abcs[abc_index]
            instance = abc.instances[class_index]
            try:
                package = inspect_qname(abc.constant_pool, instance.name).package
            except ValueError:
                return tuple(result)
            base = self.resolve_type(abc.constant_pool, instance.super_name, owner_package=package)
            if base is None:
                return tuple(result)
            key = (base.abc_index, base.class_index)
            if key in seen:
                raise ValueError("Cyclic declared class hierarchy")
            seen.add(key)
            result.append(key)
            abc_index, class_index = key

    def resolve_type(
        self,
        pool,
        multiname_index: int,
        *,
        owner_package: str | None = None,
    ) -> SymbolDeclaration | None:
        """Resolve only a unique declared public or package-internal QName.

        Internal declarations are visible only when ``owner_package`` exactly
        matches their package. Private/protected/explicit namespace names and
        runtime multinames are not cross-ABC exports. Duplicate declarations
        are preserved in the index and resolve to ``None``.
        """
        try:
            identity = inspect_qname(pool, multiname_index)
        except ValueError:
            return None
        if identity.namespace_kind == PRIVATE_NAMESPACE:
            return None
        if identity.namespace_kind not in (
            PACKAGE_NAMESPACE, PACKAGE_INTERNAL_NAMESPACE
        ):
            return None
        if (identity.namespace_kind == PACKAGE_INTERNAL_NAMESPACE
                and owner_package != identity.package):
            return None

        matches = self._types.get((identity.namespace_kind, identity.package, identity.name), [])
        return matches[0] if len(matches) == 1 else None
