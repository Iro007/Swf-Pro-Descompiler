"""ABC identities and conservative bindings for source edits in an existing class.

Private namespaces are identities, not URI strings: equal printable names do
not make namespaces from two different classes interchangeable.
"""
from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class NamespaceIdentity:
    kind: int
    uri: str
    private_index: int | None = None
    pool_scope: int | None = None


@dataclass(frozen=True)
class NameIdentity:
    kind: int
    local: str
    namespaces: tuple[NamespaceIdentity, ...]
    pool_index: int = field(compare=False)

    @property
    def private(self):
        return any(namespace.kind == 5 for namespace in self.namespaces)

    @property
    def package_qualified(self):
        return (self.kind == 7 and len(self.namespaces) == 1
                and self.namespaces[0].kind == 0x16 and bool(self.namespaces[0].uri))

    @property
    def nonpublic(self):
        return any(namespace.kind in (5, 0x17, 0x18, 0x1A) for namespace in self.namespaces)


def name_identity(pool, index):
    if type(index) is not int or not 0 < index < len(pool.multinames):
        raise ValueError("Name index is outside the constant pool")
    multiname = pool.multinames[index]
    if not multiname or multiname["kind"] not in (7, 9, 13, 14):
        raise ValueError("A runtime name has no statically resolved identity")
    indices = (multiname["ns"],) if "ns" in multiname else tuple(pool.ns_sets[multiname["ns_set"]])
    namespaces = tuple(NamespaceIdentity(pool.namespaces[n]["kind"], pool.strings[pool.namespaces[n]["name"]],
                                         n if pool.namespaces[n]["kind"] == 5 else None,
                                         id(pool) if pool.namespaces[n]["kind"] == 5 else None) for n in indices)
    return NameIdentity(multiname["kind"], pool.strings[multiname["name"]], namespaces, index)


_RESERVED = frozenset("this super class interface function var const return throw if else for while do switch case default try catch finally new delete with namespace package public private protected internal static override true false null undefined".split())


def source_identifier(text):
    return bool(re.fullmatch(r"[A-Za-z_$][\w$]*", text)) and text not in _RESERVED


class MethodBindings:
    """Resolve only declared, unambiguous private members of a proven owner."""
    def __init__(self, abc, body, *, symbol_index=None):
        self.pool = abc.constant_pool
        from .abc_symbols import AbcSymbolIndex
        symbol_index = symbol_index or AbcSymbolIndex([abc])
        # Low-level CFG callers may supply a body without a method table.
        # It has no admissible owner/signature, but pure expressions can still
        # be reconstructed. Parsed/editable ABCs require a real method record.
        context = symbol_index.method_context(0, body.method) if 0 <= body.method < len(abc.methods) else None
        self.symbol_index = symbol_index
        owners = []
        if context is not None and context.owner is not None:
            owner = context.owner
            table = {"instance": abc.instances, "class": abc.classes, "script": abc.scripts}[owner.kind]
            owners.append((owner.kind, owner.index, table[owner.index].traits))
        self.owner = owners[0][:2] if len(owners) == 1 and owners[0][0] != "script" else None
        self.members = {}
        self.type_shadow_names = set()
        for owner in owners:
            for trait in owner[2]:
                try:
                    if trait.kind_flags & 15 != 4:
                        self.type_shadow_names.add(name_identity(self.pool, trait.name).local)
                except (ValueError, KeyError, IndexError):
                    continue
        self.inherited_members = {}
        self.static = self.owner is not None and self.owner[0] == "class"
        self.class_name = None
        self.owner_package = None
        if self.owner is not None:
            from .abc_symbols import AbcSymbolIndex, inspect_qname
            try:
                class_identity = inspect_qname(self.pool, abc.instances[self.owner[1]].name)
                self.class_name = ".".join(filter(None, (class_identity.package, class_identity.name)))
                self.owner_package = class_identity.package
            except (ValueError, IndexError):
                pass
            for trait in [*abc.instances[self.owner[1]].traits, *abc.classes[self.owner[1]].traits] if self.owner[1] < len(abc.classes) else abc.instances[self.owner[1]].traits:
                try:
                    self.type_shadow_names.add(name_identity(self.pool, trait.name).local)
                except (ValueError, KeyError, IndexError):
                    continue
            for trait in owners[0][2]:
                try:
                    identity = name_identity(self.pool, trait.name)
                except (ValueError, KeyError, IndexError):
                    continue
                self.members.setdefault(identity.local, {})[identity] = identity
            # Protected identities remain attached to their declaring base.
            # Only a verified hierarchy can make that exact namespace visible.
            if not self.static:
                for abc_index, class_index in symbol_index.ancestors(0, self.owner[1]):
                    for trait in abc.instances[class_index].traits:
                        try:
                            identity = name_identity(self.pool, trait.name)
                        except (ValueError, KeyError, IndexError):
                            continue
                        namespace = identity.namespaces[0] if len(identity.namespaces) == 1 else None
                        if namespace is None or namespace.kind == 5 or namespace.kind == 0x17 and namespace.uri != self.owner_package:
                            continue
                        self.inherited_members.setdefault(identity.local, {})[identity] = identity
                        self.type_shadow_names.add(identity.local)
                        # An overriding local declaration supersedes its base,
                        # but explicit getsuper still consults inherited_members.
                        if identity.local not in self.members:
                            self.members.setdefault(identity.local, {})[identity] = identity

    @staticmethod
    def _owns(traits, method):
        return any((trait.kind_flags & 15 in (1, 2, 3) and trait.method == method)
                   or (trait.kind_flags & 15 == 5 and trait.function == method) for trait in traits)

    def identity(self, index):
        return name_identity(self.pool, index)

    def type_source_shadowed(self, text):
        return "." not in text and "::" not in text and text in self.type_shadow_names

    def require_private(self, index):
        identity = self.identity(index)
        choices = self.members.get(identity.local, {})
        if identity.kind != 7 or len(identity.namespaces) != 1 or not identity.private:
            raise ValueError("Only static private QNames can be resolved in this context")
        if self.owner is None or identity not in choices:
            raise ValueError("Private namespace has no proven declaring-class binding; use P-code")
        if len(choices) != 1:
            raise ValueError("Ambiguous private/public member name; use exact P-code")
        if not source_identifier(identity.local):
            raise ValueError("Private member name cannot be represented in ActionScript source")
        return identity

    def require_owned(self, index, *, inherited=False):
        identity = self.identity(index)
        choices = (self.inherited_members if inherited else self.members).get(identity.local, {})
        if identity.kind != 7 or len(identity.namespaces) != 1 or self.owner is None or identity not in choices:
            raise ValueError("Namespace has no proven declaring-class/hierarchy binding; use P-code")
        if len(choices) != 1 or not source_identifier(identity.local):
            raise ValueError("Ambiguous or unrepresentable member namespace; use exact P-code")
        return identity

    def super_operand(self, text):
        if self.static:
            raise ValueError("super is not available in static methods")
        choices = self.inherited_members.get(text, {})
        if len(choices) != 1:
            raise ValueError("Unresolved or ambiguous super member; use exact P-code")
        identity = next(iter(choices))
        self.require_owned(identity.pool_index, inherited=True)
        return f"#{identity.pool_index}"

    def resolve_local_type(self, text, local_name=None):
        from .abc_symbols import inspect_qname
        choices = []
        for index in range(1, len(self.pool.multinames)):
            try:
                identity = inspect_qname(self.pool, index)
            except ValueError:
                continue
            full = ".".join(filter(None, (identity.package, identity.name)))
            if identity.namespace_kind in (0x16, 0x17) and (full == text or identity.name == text and identity.package in ("", self.owner_package)):
                if identity.namespace_kind != 0x17 or identity.package == self.owner_package:
                    choices.append(index)
        unique = {name_identity(self.pool, index): index for index in choices}
        if len(unique) != 1:
            raise ValueError("Type has no unique exact QName in this ABC: " + text)
        return f"#{next(iter(unique.values()))}"

    def qualified_operand(self, text):
        if "." not in text:
            return None
        try:
            return self.resolve_local_type(text)
        except ValueError:
            return None

    def namespace_alias(self, identity):
        if len(identity.namespaces) != 1:
            return None
        namespace = identity.namespaces[0]
        choices = [alias for _, alias in self.symbol_index.namespace_aliases.get((namespace.kind, namespace.uri), [])
                   if alias.package == self.owner_package and source_identifier(alias.name)]
        names = {alias.name for alias in choices}
        return next(iter(names)) if len(names) == 1 else None

    def namespace_operand(self, text):
        alias_name, local = text.rsplit("::", 1)
        if not source_identifier(local):
            raise ValueError("Namespace member name cannot be represented in source")
        choices = []
        if alias_name == "public":
            for index in range(1, len(self.pool.multinames)):
                try:
                    identity = name_identity(self.pool, index)
                except (ValueError, IndexError, KeyError):
                    continue
                if identity.kind == 7 and identity.local == local and len(identity.namespaces) == 1 and identity.namespaces[0].kind in (8, 0x16) and not identity.namespaces[0].uri:
                    choices.append(index)
            if not choices:
                raise ValueError("Explicit public qualification has no exact blank public QName")
            return f"#{choices[0]}"
        for (kind, uri), aliases in self.symbol_index.namespace_aliases.items():
            if any(alias.name == alias_name and alias.package == self.owner_package for _, alias in aliases):
                for index in range(1, len(self.pool.multinames)):
                    try:
                        identity = name_identity(self.pool, index)
                    except (ValueError, IndexError, KeyError):
                        continue
                    if identity.kind == 7 and identity.local == local and len(identity.namespaces) == 1 and identity.namespaces[0].kind == kind and identity.namespaces[0].uri == uri:
                        choices.append(index)
        unique = {name_identity(self.pool, index): index for index in choices}
        if len(unique) != 1:
            raise ValueError("Namespace qualification has no unique exact QName")
        return f"#{next(iter(unique.values()))}"

    def operand(self, text, *, receiver=None):
        if "::" in text:
            return self.namespace_operand(text)
        if receiver is None and "." in text:
            operand = self.qualified_operand(text)
            if operand is None:
                raise ValueError("Qualified source name has no exact QName binding: " + text)
            return operand
        choices = self.members.get(text, {})
        bound = [identity for identity in choices if identity.nonpublic]
        if not bound:
            return text
        if len(choices) != 1:
            raise ValueError("Ambiguous private/public member name; use exact P-code")
        identity = self.require_owned(bound[0].pool_index)
        permitted = (None, self.class_name, (self.class_name or "").rsplit(".", 1)[-1]) if self.static else (None, "this")
        if receiver not in permitted:
            raise ValueError("Non-public member requires a proven this or declaring-class receiver; use P-code")
        return f"#{identity.pool_index}"
