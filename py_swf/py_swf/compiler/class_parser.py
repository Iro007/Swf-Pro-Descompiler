"""Structural parser for the initial ActionScript class-source subset.

This module intentionally parses declarations only. Method bodies and literal
initializers are retained as source slices for the existing body compiler.
It is not a complete ActionScript grammar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass
class MetadataDecl:
    name: str
    items: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ParameterDecl:
    name: str
    type_name: str = "*"
    default_source: str | None = None
    rest: bool = False


@dataclass
class FieldDecl:
    name: str
    type_name: str = "*"
    visibility: str = "internal"
    modifiers: tuple[str, ...] = ()
    constant: bool = False
    value_source: str | None = None
    metadata: list[MetadataDecl] = field(default_factory=list)


@dataclass
class MethodDecl:
    name: str
    visibility: str = "internal"
    modifiers: tuple[str, ...] = ()
    accessor: str | None = None
    parameters: list[ParameterDecl] = field(default_factory=list)
    return_type: str = "*"
    body_source: str | None = None
    metadata: list[MetadataDecl] = field(default_factory=list)


@dataclass
class InitializerDecl:
    """Ordered class initialization code, represented by a bare class block."""
    body_source: str


@dataclass
class NamespaceDecl:
    name: str
    value_source: str
    visibility: str = "public"
    package: str = ""


@dataclass
class ClassDecl:
    name: str
    visibility: str = "internal"
    modifiers: tuple[str, ...] = ()
    super_name: str | None = None
    interfaces: list[str] = field(default_factory=list)
    members: list[FieldDecl | MethodDecl | InitializerDecl] = field(default_factory=list)
    metadata: list[MetadataDecl] = field(default_factory=list)
    package: str = ""
    imports: list[str] = field(default_factory=list)


@dataclass
class Program:
    package: str = ""
    imports: list[str] = field(default_factory=list)
    classes: list[ClassDecl] = field(default_factory=list)
    namespaces: list[NamespaceDecl] = field(default_factory=list)


@dataclass(frozen=True)
class _Token:
    value: str
    kind: str
    start: int
    end: int
    line: int


_MULTI = ("...", "::", "..", "==", "!=", "<=", ">=", "&&", "||", "++", "--")
_IDENT_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$"
_IDENT_CONT = _IDENT_START + "0123456789"
_MODIFIERS = {"public", "private", "protected", "internal", "static", "override", "final", "dynamic"}


def _lex(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    line = 1
    while i < len(source):
        start = i
        char = source[i]
        if char.isspace():
            if char == "\n":
                line += 1
            i += 1
            continue
        if source.startswith("//", i):
            end = source.find("\n", i + 2)
            i = len(source) if end < 0 else end
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise SyntaxError(f"Unterminated block comment at line {line}")
            line += source[i:end + 2].count("\n")
            i = end + 2
            continue
        if char in "\"'":
            quote = char
            i += 1
            while i < len(source):
                if source[i] == "\\":
                    if i + 1 >= len(source):
                        raise SyntaxError(f"Unterminated escape in string at line {line}")
                    if source[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                if source[i] == "\n":
                    raise SyntaxError(f"Unterminated string at line {line}")
                i += 1
            else:
                raise SyntaxError(f"Unterminated string at line {line}")
            tokens.append(_Token(source[start:i], "string", start, i, line))
            continue
        if char == "<" and not (tokens and tokens[-1].value == ".") and re.match(r"</[A-Za-z]|<[A-Za-z_][\w:.-]*(?:\s+[\w:.-]+\s*=|/?>)", source[i:]):
            raise SyntaxError(f"XML/E4X syntax is outside the initial class-parser tier at line {line}")
        if char in _IDENT_START:
            i += 1
            while i < len(source) and source[i] in _IDENT_CONT:
                i += 1
            tokens.append(_Token(source[start:i], "name", start, i, line))
            continue
        if char.isdigit():
            i += 1
            while i < len(source) and (source[i].isalnum() or source[i] in "._"):
                i += 1
            tokens.append(_Token(source[start:i], "number", start, i, line))
            continue
        op = next((candidate for candidate in _MULTI if source.startswith(candidate, i)), None)
        if op:
            i += len(op)
            tokens.append(_Token(op, "symbol", start, i, line))
        elif char in "{}[]():;,.=*<>!?+-/%&|":
            i += 1
            tokens.append(_Token(char, "symbol", start, i, line))
        else:
            raise SyntaxError(f"Unsupported character {char!r} at line {line}")
    tokens.append(_Token("", "eof", len(source), len(source), line))
    return tokens


class _Parser:
    def __init__(self, source: str):
        self.source = source
        self.tokens = _lex(source)
        self.pos = 0

    @property
    def token(self) -> _Token:
        return self.tokens[self.pos]

    def take(self) -> _Token:
        token = self.token
        if token.kind != "eof":
            self.pos += 1
        return token

    def match(self, value: str) -> bool:
        if self.token.value == value:
            self.take()
            return True
        return False

    def expect(self, value: str) -> _Token:
        if self.token.value != value:
            self.fail(f"Expected {value!r}")
        return self.take()

    def fail(self, message: str) -> None:
        raise SyntaxError(f"{message} at line {self.token.line}: {self.token.value or 'EOF'}")

    def identifier(self) -> str:
        if self.token.kind != "name":
            self.fail("Expected an identifier")
        return self.take().value

    def qualified_name(self) -> str:
        parts = [self.identifier()]
        while self.match("."):
            if self.token.value == "<":
                self.fail("Generics are outside the initial class-parser tier")
            if self.token.value == "*":
                self.fail("Wildcard imports are not resolvable in this parser tier")
            parts.append(self.identifier())
        return ".".join(parts)

    def type_name(self) -> str:
        if self.match("*"):
            return "*"
        if self.match("<"):
            self.fail("Generics are outside the initial class-parser tier")
        name = self.qualified_name()
        if self.token.value == "<":
            self.fail("Generics are outside the initial class-parser tier")
        return name

    def parse(self) -> Program:
        program = Program()
        while self.token.kind != "eof":
            part = Program()
            if self.match("package"):
                if self.token.value != "{":
                    part.package = self.qualified_name()
                self.expect("{")
                self.parse_toplevel(part, "}")
                if self.token.kind == "eof":
                    self.fail("Unclosed package body")
                self.expect("}")
            else:
                self.parse_toplevel(part, "")
            for declaration in part.classes:
                declaration.package = part.package
                declaration.imports = list(part.imports)
            for declaration in part.namespaces:
                declaration.package = part.package
            if not program.classes and not program.namespaces:
                program.package, program.imports = part.package, list(part.imports)
            program.classes.extend(part.classes)
            program.namespaces.extend(part.namespaces)
        if not program.classes and not program.namespaces:
            raise SyntaxError("Expected a class or interface declaration")
        return program

    def parse_toplevel(self, program: Program, terminator: str) -> None:
        pending: list[MetadataDecl] = []
        seen_imports: set[str] = set()
        seen_classes: set[str] = set()
        while self.token.kind != "eof" and self.token.value != terminator:
            if self.match(";"):
                continue
            if self.token.value == "[":
                pending.extend(self.metadata_list())
                continue
            if self.match("import"):
                imported = self.qualified_name()
                self.expect(";")
                if imported in seen_imports:
                    self.fail(f"Duplicate import {imported!r}")
                seen_imports.add(imported)
                program.imports.append(imported)
                if pending:
                    self.fail("Metadata cannot annotate an import")
                continue
            checkpoint = self.pos
            ns_modifiers = (self.take().value,) if self.token.value in ("public", "internal") and self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].value == "namespace" else ()
            if self.match("namespace"):
                if pending:
                    self.fail("Namespace metadata is not supported")
                name = self.identifier()
                self.expect("=")
                token = self.take()
                if token.kind != "string":
                    self.fail("Namespace identity requires a literal URI string")
                self.expect(";")
                program.namespaces.append(NamespaceDecl(name, self.source[token.start:token.end], self.visibility(ns_modifiers)))
                continue
            self.pos = checkpoint
            decl = self.class_decl(pending)
            pending = []
            if decl.name in seen_classes:
                self.fail(f"Duplicate class/interface {decl.name!r}")
            seen_classes.add(decl.name)
            program.classes.append(decl)
        if pending:
            self.fail("Metadata is not followed by a declaration")

    def metadata_list(self) -> list[MetadataDecl]:
        result = []
        while self.match("["):
            name = self.identifier()
            items: list[tuple[str, str]] = []
            if self.match("("):
                if self.token.value != ")":
                    while True:
                        if self.token.kind == "name" and self.tokens[self.pos + 1].value == "=":
                            key = self.take().value
                            self.expect("=")
                        else:
                            key = ""
                        start = self.token.start
                        end = self.raw_value_end({",", ")"})
                        value = self.source[start:end].strip()
                        if not value:
                            self.fail("Expected a metadata value")
                        if key and any(existing == key for existing, _ in items):
                            self.fail(f"Duplicate metadata key {key!r}")
                        items.append((key, value))
                        if not self.match(","):
                            break
                self.expect(")")
            self.expect("]")
            result.append(MetadataDecl(name, items))
        return result

    def raw_value_end(self, stops: set[str]) -> int:
        start_pos = self.pos
        stack: list[str] = []
        pairs = {"(": ")", "[": "]", "{": "}"}
        while self.token.kind != "eof":
            value = self.token.value
            if not stack and value in stops:
                break
            if value in pairs:
                stack.append(pairs[value])
            elif value in {")", "]", "}"}:
                if not stack or stack.pop() != value:
                    self.fail("Unbalanced expression")
            self.take()
        if stack:
            self.fail("Unbalanced expression")
        if self.pos == start_pos:
            self.fail("Expected a source value")
        return self.tokens[self.pos - 1].end

    def modifiers(self, allowed: set[str]) -> tuple[str, ...]:
        found: list[str] = []
        while self.token.value in _MODIFIERS:
            value = self.take().value
            if value not in allowed:
                self.fail(f"Modifier {value!r} is not valid here")
            if value in found:
                self.fail(f"Duplicate modifier {value!r}")
            found.append(value)
        visibility = [x for x in found if x in {"public", "private", "protected", "internal"}]
        if len(visibility) > 1:
            self.fail("Conflicting visibility modifiers")
        return tuple(found)

    @staticmethod
    def visibility(modifiers: tuple[str, ...]) -> str:
        return next((x for x in modifiers if x in {"public", "private", "protected", "internal"}), "internal")

    def class_decl(self, metadata: list[MetadataDecl]) -> ClassDecl:
        mods = self.modifiers({"public", "internal", "final", "dynamic"})
        if self.match("class"):
            is_interface = False
        elif self.match("interface"):
            is_interface = True
            mods = mods + ("interface",)
        else:
            self.fail("Expected class or interface declaration")
        name = self.identifier()
        super_name = None
        interfaces: list[str] = []
        if self.match("extends"):
            if is_interface:
                interfaces.append(self.qualified_name())
                while self.match(","):
                    interfaces.append(self.qualified_name())
            else:
                super_name = self.qualified_name()
        if self.match("implements"):
            if is_interface:
                self.fail("Interfaces use extends, not implements")
            interfaces.append(self.qualified_name())
            while self.match(","):
                interfaces.append(self.qualified_name())
        if len(interfaces) != len(set(interfaces)):
            self.fail("Duplicate interface in declaration")
        self.expect("{")
        decl = ClassDecl(name, self.visibility(mods), mods, super_name, interfaces, [], metadata)
        self.class_members(decl, is_interface)
        if self.token.kind == "eof":
            self.fail("Unclosed class/interface body")
        self.expect("}")
        return decl

    def class_members(self, decl: ClassDecl, is_interface: bool) -> None:
        pending: list[MetadataDecl] = []
        ordinary: set[str] = set()
        accessors: dict[str, set[str]] = {}
        while self.token.kind != "eof" and self.token.value != "}":
            if self.match(";"):
                continue
            if self.token.value == "[":
                pending.extend(self.metadata_list())
                continue
            mods = self.modifiers(_MODIFIERS)
            if self.token.kind == "name" and self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].value in ("var", "const", "function"):
                if any(modifier in ("public", "private", "protected", "internal") for modifier in mods):
                    self.fail("An explicit namespace cannot be combined with a visibility modifier")
                mods = mods + (self.take().value,)
            if self.token.value == "{":
                if is_interface or mods or pending:
                    self.fail("Class initializer blocks cannot carry modifiers or metadata")
                decl.members.append(InitializerDecl(self.body()))
            elif self.match("var") or self.match("const"):
                constant = self.tokens[self.pos - 1].value == "const"
                field = self.field_decl(mods, constant, pending)
                if field.name in ordinary or field.name in accessors:
                    self.fail(f"Conflicting duplicate member {field.name!r}")
                ordinary.add(field.name)
                decl.members.append(field)
            elif self.match("function"):
                method = self.method_decl(decl.name, mods, pending, is_interface)
                if method.accessor:
                    kinds = accessors.setdefault(method.name, set())
                    if method.accessor in kinds:
                        self.fail(f"Duplicate {method.accessor} accessor {method.name!r}")
                    if method.name in ordinary:
                        self.fail(f"Conflicting duplicate member {method.name!r}")
                    kinds.add(method.accessor)
                else:
                    if method.name in ordinary or method.name in accessors:
                        self.fail(f"Conflicting duplicate member {method.name!r}")
                    ordinary.add(method.name)
                decl.members.append(method)
            else:
                self.fail("Expected var, const, or function member")
            pending = []
        if pending:
            self.fail("Metadata is not followed by a member")

    def field_decl(self, mods: tuple[str, ...], constant: bool, metadata: list[MetadataDecl]) -> FieldDecl:
        name = self.identifier()
        type_name = self.type_name() if self.match(":") else "*"
        value_source = None
        if self.match("="):
            start = self.token.start
            end = self.raw_value_end({";", "}"})
            value_source = self.source[start:end].strip()
            if not value_source:
                self.fail("Expected a field initializer")
        self.expect(";")
        return FieldDecl(name, type_name, self.visibility(mods), mods, constant, value_source, metadata)

    def method_decl(self, class_name: str, mods: tuple[str, ...], metadata: list[MetadataDecl], is_interface: bool) -> MethodDecl:
        accessor = None
        if self.token.value in {"get", "set"} and self.tokens[self.pos + 1].kind == "name":
            accessor = self.take().value
        name = self.identifier()
        if "(" not in {token.value for token in self.tokens[self.pos:self.pos + 1]}:
            self.fail("Expected a method parameter list")
        parameters = self.parameters()
        return_type = self.type_name() if self.match(":") else "*"
        if self.match(";"):
            body = None
        elif self.token.value == "{":
            if is_interface:
                self.fail("Interface methods cannot have bodies")
            body = self.body()
        else:
            self.fail("Expected method body or declaration terminator")
        if name == class_name and accessor:
            self.fail("A constructor cannot be an accessor")
        if name != class_name and accessor is None and name == "constructor":
            self.fail("Constructor must use the class name")
        return MethodDecl(name, self.visibility(mods), mods, accessor, parameters, return_type, body, metadata)

    def parameters(self) -> list[ParameterDecl]:
        self.expect("(")
        result: list[ParameterDecl] = []
        saw_default = False
        saw_rest = False
        if self.token.value != ")":
            while True:
                rest = self.match("...")
                name = self.identifier()
                type_name = self.type_name() if self.match(":") else "*"
                default_source = None
                if self.match("="):
                    if rest:
                        self.fail("Rest parameters cannot have defaults")
                    start = self.token.start
                    end = self.raw_value_end({",", ")"})
                    default_source = self.source[start:end].strip()
                    if not default_source:
                        self.fail("Expected a default parameter value")
                    saw_default = True
                elif saw_default and not rest:
                    self.fail("Required parameter cannot follow a default parameter")
                if saw_rest:
                    self.fail("Rest parameter must be last")
                saw_rest = rest
                result.append(ParameterDecl(name, type_name, default_source, rest))
                if not self.match(","):
                    break
                if saw_rest:
                    self.fail("Rest parameter must be last")
        self.expect(")")
        names = [parameter.name for parameter in result]
        if len(names) != len(set(names)):
            self.fail("Duplicate parameter name")
        return result

    def body(self) -> str:
        opening = self.expect("{")
        depth = 1
        while self.token.kind != "eof":
            token = self.take()
            if token.value == "{":
                depth += 1
            elif token.value == "}":
                depth -= 1
                if depth == 0:
                    return self.source[opening.end:token.start]
        self.fail("Unterminated method body")


def parse_class_program(source: str) -> Program:
    """Parse an ActionScript package/class declaration subset into dataclasses.

    Unsupported XML/E4X syntax and generic type syntax are rejected with an
    explicit diagnostic. Body text is preserved verbatim (excluding braces).
    """
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    return _Parser(source).parse()


__all__ = [
    "ClassDecl", "FieldDecl", "MetadataDecl", "MethodDecl", "ParameterDecl",
    "Program", "parse_class_program",
]
