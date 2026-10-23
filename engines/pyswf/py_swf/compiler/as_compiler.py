"""Strict, method-body ActionScript compiler implemented entirely in Python.

Supported syntax is deliberately explicit: expressions, variables, assignments,
if/else, while/for, enumerations, switch, exceptions and bounded nested functions.
AVM2 closures require an explicit ABC context. Unsupported constructs fail
before bytecode replaces the working tag. This is not a whole-class compiler.
"""

from dataclasses import dataclass
import re

from ..script_strings import parse_string, quote_string


@dataclass
class Token:
    kind: str
    value: str
    line: int


_PATTERN = re.compile(
    r'(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)|'
    r'(?P<string>"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')|'
    r'(?P<number>0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|'
    r'(?P<name>[a-zA-Z_$][a-zA-Z0-9_$]*)|'
    r'(?P<op>===|!==|>>>|<<|>>|::|==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|[+*/%<>=!;:,.(){}\[\]\-&|^~])|'
    r'(?P<space>\s+)|(?P<invalid>.)'
)


def tokenize(source):
    tokens, line = [], 1
    for match in _PATTERN.finditer(source):
        kind, value = match.lastgroup, match.group()
        if kind == "invalid":
            raise SyntaxError(f"Unsupported character {value!r} at line {line}")
        if kind not in ("space", "comment"):
            tokens.append(Token(kind, value, line))
        line += value.count("\n")
    tokens.append(Token("eof", "", line))
    return tokens


@dataclass
class Literal:
    kind: str
    value: object


@dataclass
class Name:
    name: str


@dataclass
class Member:
    obj: object
    name: str


@dataclass
class ComputedMember:
    obj: object
    key: object


@dataclass
class ArrayLiteral:
    items: list


@dataclass
class ObjectLiteral:
    items: list


@dataclass
class Call:
    callee: object
    args: list


@dataclass
class Binary:
    op: str
    left: object
    right: object


@dataclass
class Unary:
    op: str
    value: object


@dataclass
class Update:
    target: object
    op: str
    postfix: bool


@dataclass
class Var:
    name: str
    value: object
    type_name: str | None = None


@dataclass
class Assign:
    target: object
    value: object


@dataclass
class CompoundAssign:
    target: object
    op: str
    value: object


@dataclass
class Expr:
    value: object


@dataclass
class If:
    condition: object
    then: list
    otherwise: list


@dataclass
class While:
    condition: object
    body: list


@dataclass
class For:
    initial: list
    condition: object
    update: list
    body: list


@dataclass
class ForEnumeration:
    target: object
    collection: object
    body: list
    values: bool = False


@dataclass
class FunctionParameter:
    name: str
    type_name: str = "*"


@dataclass
class FunctionExpression:
    parameters: list
    body: list
    return_type: str = "*"


@dataclass
class FunctionDeclaration:
    name: str
    function: FunctionExpression


@dataclass
class Switch:
    value: object
    cases: list  # (expression or None for default, statements), in source order


@dataclass
class Catch:
    name: str
    type_name: str
    body: list


@dataclass
class Try:
    body: list
    catches: list
    finalizer: list


class CompiledActionScript(str):
    """Assembly with serializable .exception directives and symbolic regions."""
    def __new__(cls, text, exception_regions=(), activation_traits=()):
        result = super().__new__(cls, text)
        result.exception_regions = tuple(exception_regions)
        result.activation_traits = tuple(activation_traits)
        return result


@dataclass
class Exit:
    kind: str
    value: object = None


_PRECEDENCE = {"||": 1, "&&": 2, "|": 3, "^": 4, "&": 5,
               "==": 6, "!=": 6, "===": 6, "!==": 6,
               "<": 7, "<=": 7, ">": 7, ">=": 7, "<<": 8, ">>": 8, ">>>": 8,
               "+": 9, "-": 9, "*": 10, "/": 10, "%": 10}
_UNSUPPORTED = {"function", "class", "package", "public", "private", "protected",
                "static", "const", "do", "new", "with", "delete", "namespace", "import"}
_RESERVED = {"true", "false", "null", "undefined", "this", "if", "else", "while",
             "return", "throw", "break", "continue", "var", "for", "switch", "case",
             "default", "try", "catch", "finally", "in", "each"}


class Parser:
    def __init__(self, tokens, target="as3", bindings=None):
        self.tokens, self.pos, self.target = tokens, 0, target.lower()
        self.bindings, self.runtime_multiname_index = bindings, None

    def runtime_name_index(self):
        if self.runtime_multiname_index is not None:
            return self.runtime_multiname_index
        pool = getattr(self.bindings, "pool", None)
        if pool is None:
            self.fail("Runtime bracket access needs an ABC constant pool; use P-code")
        public_ns = next((index for index, item in enumerate(pool.namespaces)
                          if item and item.get("kind") == 0x16 and item.get("name") == 0), None)
        if getattr(self.bindings, "owner", None) is not None:
            self.fail("Runtime bracket access is limited to script-level public scope; class namespace binding requires exact P-code")
        # owner=None also represents ambiguous class/script ownership. Since
        # MethodBindings intentionally exposes no owner list, reject any pool
        # that records private or named namespaces instead of guessing that
        # the current method is truly script-scoped. This is conservative for
        # unrelated classes in the same ABC as well.
        if any(item and (item.get("kind") != 0x16 or item.get("name") != 0)
               for item in pool.namespaces[1:]):
            self.fail("Runtime bracket access cannot preserve private/open namespace lookup; use exact P-code")
        if public_ns is not None and any(item and item != [public_ns]
                                         for item in pool.ns_sets[1:]):
            self.fail("Runtime bracket access cannot preserve open namespace sets; use exact P-code")
        if public_ns is None:
            pool.namespaces.append({"kind": 0x16, "name": 0})
            public_ns = len(pool.namespaces) - 1
        public_set = next((index for index, item in enumerate(pool.ns_sets)
                           if item == [public_ns]), None)
        if public_set is None:
            pool.ns_sets.append([public_ns])
            public_set = len(pool.ns_sets) - 1
        for index, item in enumerate(pool.multinames):
            if item == {"kind": 0x1B, "ns_set": public_set}:
                self.runtime_multiname_index = index
                return index
        pool.multinames.append({"kind": 0x1B, "ns_set": public_set})
        self.runtime_multiname_index = len(pool.multinames) - 1
        return self.runtime_multiname_index

    def peek(self):
        return self.tokens[self.pos]

    def match(self, value):
        if self.peek().value == value:
            self.pos += 1
            return True
        return False

    def expect(self, value):
        if not self.match(value):
            self.fail(f"Expected {value!r}")

    def fail(self, message):
        token = self.peek()
        raise SyntaxError(f"{message} at line {token.line}: {token.value or 'EOF'}")

    def name(self):
        token = self.peek()
        if token.kind != "name" or token.value in _UNSUPPORTED | _RESERVED:
            self.fail("Expected an identifier")
        self.pos += 1
        return token.value

    def parse_program(self):
        if self.peek().value == "{":
            result = self.block()
            if self.peek().kind != "eof":
                self.fail("Unexpected code after method body")
            return result
        result = []
        while self.peek().kind != "eof":
            statement = self.statement()
            if statement is not None:
                result.append(statement)
        return result

    def block(self):
        if not self.match("{"):
            statement = self.statement()
            return [statement] if statement else []
        result = []
        while self.peek().value != "}":
            if self.peek().kind == "eof":
                self.fail("Unclosed block")
            statement = self.statement()
            if statement:
                result.append(statement)
        self.expect("}")
        return result

    def finish(self, line):
        if self.match(";") or self.peek().value == "}" or self.peek().kind == "eof":
            return
        if self.peek().line <= line:
            self.fail("Expected a semicolon or a supported operator")

    def statement(self):
        token, line = self.peek(), self.peek().line
        if self.match(";"):
            return None
        if self.match("function"):
            name = self.name()
            return FunctionDeclaration(name, self.function_expression())
        if token.value in _UNSUPPORTED:
            self.fail("Unsupported syntax; edit a method body or use P-code")
        if self.match("var"):
            node = self.variable()
            self.finish(line)
            return node
        if self.match("if"):
            self.expect("(")
            condition = self.expression()
            self.expect(")")
            then = self.block()
            otherwise = self.block() if self.match("else") else []
            return If(condition, then, otherwise)
        if self.match("while"):
            self.expect("(")
            condition = self.expression()
            self.expect(")")
            return While(condition, self.block())
        if self.match("for"):
            values = self.match("each")
            if values and self.target in ("as1", "as2", "avm1"):
                self.fail("for each belongs to AS3; AS1/AS2 supports for in")
            self.expect("(")
            initial = []
            if self.match("var"):
                variable_start = self.pos
                initial.append(self.variable())
                if self.match("in"):
                    if any(token.value == "=" for token in self.tokens[variable_start:self.pos-1]):
                        self.fail("Enumeration declarations cannot have an initializer")
                    collection = self.expression()
                    self.expect(")")
                    return ForEnumeration(initial[0], collection, self.block(), values)
                while self.match(","):
                    initial.append(self.variable())
            elif self.peek().value != ";":
                initial = self.clauses()
                if self.match("in"):
                    if len(initial) != 1 or not isinstance(initial[0], Expr) or not isinstance(initial[0].value, Name):
                        self.fail("Enumeration target must be one variable")
                    collection = self.expression()
                    self.expect(")")
                    return ForEnumeration(initial[0].value, collection, self.block(), values)
            if values:
                self.fail("for each requires an in clause")
            self.expect(";")
            condition = Literal("true", None) if self.peek().value == ";" else self.expression()
            self.expect(";")
            update = [] if self.peek().value == ")" else self.clauses()
            self.expect(")")
            return For(initial, condition, update, self.block())
        if self.match("switch"):
            self.expect("(")
            value = self.expression()
            self.expect(")")
            self.expect("{")
            cases, default_seen = [], False
            while not self.match("}"):
                if self.match("case"):
                    case = self.expression()
                elif self.match("default"):
                    if default_seen:
                        self.fail("Duplicate switch default")
                    default_seen, case = True, None
                else:
                    self.fail("Expected case or default")
                self.expect(":")
                body = []
                while self.peek().value not in ("case", "default", "}"):
                    if self.peek().kind == "eof":
                        self.fail("Unclosed switch")
                    child = self.statement()
                    if child:
                        body.append(child)
                cases.append((case, body))
            return Switch(value, cases)
        if self.match("try"):
            body = self.required_block()
            catches, finalizer = [], []
            while self.match("catch"):
                self.expect("(")
                name = self.name()
                type_name = self.type_name() if self.match(":") else "*"
                self.expect(")")
                if catches and catches[-1].type_name == "*":
                    self.fail("A catch-all must be the last catch")
                catches.append(Catch(name, type_name, self.required_block()))
            has_finally = self.match("finally")
            if has_finally:
                finalizer = self.required_block()
            if not catches and not has_finally:
                self.fail("try requires catch or finally")
            return Try(body, catches, finalizer)
        if token.value in ("return", "throw", "break", "continue"):
            self.pos += 1
            value = None
            if token.value in ("return", "throw") and self.peek().value not in (";", "}") and self.peek().kind != "eof" and self.peek().line == line:
                value = self.expression()
            if token.value == "throw" and value is None:
                self.fail("throw requires a value on the same line")
            self.finish(line)
            return Exit(token.value, value)
        node = self.simple_statement()
        self.finish(line)
        return node

    def required_block(self):
        if self.peek().value != "{":
            self.fail("Expected a braced block")
        return self.block()

    def function_expression(self):
        self.expect("(")
        parameters = []
        if self.peek().value != ")":
            while True:
                name = self.name()
                if name in ("arguments", "super"):
                    self.fail("Implicit function bindings cannot be parameters")
                type_name = self.type_name() if self.match(":") else "*"
                parameters.append(FunctionParameter(name, type_name))
                if not self.match(","):
                    break
        self.expect(")")
        if len({parameter.name for parameter in parameters}) != len(parameters):
            self.fail("Duplicate function parameter")
        return_type = self.type_name() if self.match(":") else "*"
        return FunctionExpression(parameters, self.required_block(), return_type)

    def type_name(self):
        if self.match("*"):
            return "*"
        name = self.name()
        while self.match("."):
            name += "." + self.name()
        return name

    def variable(self):
        name, type_name, value = self.name(), None, Literal("undefined", None)
        if self.match(":"):
            type_name = self.type_name()
        if self.match("="):
            value = self.expression()
        return Var(name, value, type_name)

    def clauses(self):
        nodes = [self.simple_statement()]
        while self.match(","):
            nodes.append(self.simple_statement())
        return nodes

    def simple_statement(self):
        expression = self.expression()
        if self.peek().value in ("=", "+=", "-="):
            op = self.peek().value
            self.pos += 1
            if not isinstance(expression, (Name, Member, ComputedMember)):
                self.fail("Assignment requires a variable or member")
            value = self.expression()
            if op != "=":
                if isinstance(expression, Name):
                    return Assign(expression, Binary(op[0], expression, value))
                if isinstance(expression, ComputedMember) and (self.target in ("as1", "as2", "avm1") or self.runtime_multiname_index is not None):
                    return CompoundAssign(expression, op[0], value)
                self.fail("Compound member assignments require AVM1 computed access; use P-code")
            return Assign(expression, value)
        return Expr(expression)

    def expression(self, minimum=1):
        left = self.unary()
        while _PRECEDENCE.get(self.peek().value, 0) >= minimum:
            op = self.peek().value
            precedence = _PRECEDENCE[op]
            self.pos += 1
            left = Binary(op, left, self.expression(precedence + 1))
        return left

    def array_literal(self):
        items = []
        if not self.match("]"):
            while True:
                if self.peek().value == ",":
                    self.fail("Sparse arrays are not supported")
                items.append(self.expression())
                if self.match("]"):
                    break
                self.expect(",")
                if self.match("]"):
                    break
        return ArrayLiteral(items)

    def object_literal(self):
        items = []
        if not self.match("}"):
            while True:
                token = self.peek()
                if token.kind == "name" and token.value not in _UNSUPPORTED | _RESERVED:
                    key = token.value
                    self.pos += 1
                elif token.kind == "string":
                    key = parse_string(token.value)
                    self.pos += 1
                elif token.kind == "number":
                    key = token.value
                    self.pos += 1
                else:
                    self.fail("Object literal keys must be identifiers, strings, or numbers")
                self.expect(":")
                items.append((key, self.expression()))
                if self.match("}"):
                    break
                self.expect(",")
                if self.match("}"):
                    break
        return ObjectLiteral(items)

    def unary(self):
        token = self.peek()
        if token.value in ("!", "-", "+", "~"):
            self.pos += 1
            return Unary(token.value, self.unary())
        if token.value in ("++", "--"):
            self.pos += 1
            target = self.unary()
            if not isinstance(target, (Name, Member, ComputedMember)):
                self.fail("Update requires a variable or member")
            return Update(target, token.value, False)
        if self.match("function"):
            if self.peek().kind == "name":
                self.fail("Named function expressions require a separate self binding; use a function declaration")
            value = self.function_expression()
        elif self.match("("):
            value = self.expression()
            self.expect(")")
        elif self.match("["):
            value = self.array_literal()
        elif self.match("{"):
            value = self.object_literal()
        elif token.kind == "number":
            self.pos += 1
            number = int(token.value, 16) if token.value.lower().startswith("0x") else float(token.value) if any(c in token.value for c in ".eE") else int(token.value)
            value = Literal("number", number)
        elif token.kind == "string":
            self.pos += 1
            value = Literal("string", parse_string(token.value))
        elif token.kind == "name":
            public_qualifier = token.value == "public" and self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].value == "::"
            if not public_qualifier and token.value in _UNSUPPORTED | (_RESERVED - {"true", "false", "null", "undefined", "this"}):
                self.fail("Unsupported expression; use P-code")
            self.pos += 1
            value = Literal(token.value, None) if token.value in ("true", "false", "null", "undefined") else Name(token.value)
        else:
            self.fail("Expected an expression")
        while True:
            if self.match("."):
                member = self.peek().value
                if member == "public" and self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].value == "::":
                    self.pos += 1
                else:
                    member = self.name()
                if self.match("::"):
                    member += "::" + self.name()
                value = Member(value, member)
            elif self.match("::"):
                if not isinstance(value, Name):
                    self.fail("Namespace qualification requires a declared namespace identifier")
                value = Name(value.name + "::" + self.name())
            elif self.match("["):
                key = self.expression()
                self.expect("]")
                if self.target in ("as3", "avm2"):
                    self.runtime_name_index()
                value = ComputedMember(value, key)
            elif self.match("("):
                if isinstance(value, ComputedMember):
                    self.fail("Calls through computed member names are not supported; use P-code")
                args = []
                if self.peek().value != ")":
                    args.append(self.expression())
                    while self.match(","):
                        args.append(self.expression())
                self.expect(")")
                value = Call(value, args)
            elif self.peek().value in ("++", "--"):
                op = self.peek().value
                self.pos += 1
                if not isinstance(value, (Name, Member, ComputedMember)):
                    self.fail("Update requires a variable or member")
                value = Update(value, op, True)
            else:
                return value


class Emitter:
    def __init__(self):
        self.lines, self.counter, self.controls = [], 0, []
        self.tries, self.aliases, self.exception_regions = [], {}, []
        self.used_names = set()

    def emit(self, value):
        self.lines.append(value)

    def label(self, value):
        self.lines.append(value + ":")
        # AVM2 backward branch destinations require the dedicated label opcode
        # during player verification. It has no execution-side effect. Keep
        # adjacent joins and empty bodies attached to real instructions too.
        self.emit("ActionPush undefined" if self.avm1 else "label")
        if self.avm1:
            self.emit("ActionPop")

    def new_label(self):
        self.counter += 1
        return f"L_native_{self.counter}"

    def compile(self, statements):
        def names(node):
            if isinstance(node, (list, tuple)):
                for child in node:
                    names(child)
            elif hasattr(node, "__dataclass_fields__"):
                for value in vars(node).values():
                    names(value)
            elif isinstance(node, str):
                self.used_names.add(node)
        names(statements)
        self.prepare(statements)
        for statement in statements:
            self.statement(statement)
        self.finish_method()
        directives = [f".exception {start} {stop} {target} {typ} {name or '#0'}"
                      for start, stop, target, typ, name in self.exception_regions]
        traits = getattr(getattr(self, "closure_context", None), "activation_traits", ())
        directives.extend(f".activation #{trait.name} {trait.slot_id} #{trait.type_name}" for trait in traits)
        return CompiledActionScript("\n".join(self.lines + directives), self.exception_regions, traits)

    def temporary(self):
        while True:
            name = "$pyswf_temp_" + self.new_label()
            if name not in self.used_names and name not in getattr(self, "registers", {}):
                break
        self.used_names.add(name)
        if not self.avm1:
            self.registers[name] = self.next_register
            self.next_register += 1
        return name

    def save_temp(self, name):
        if self.avm1:
            self.emit(f"ActionPush {quote_string(name)}")
            self.emit("ActionStackSwap")
            self.emit("ActionDefineLocal")
        else:
            self.store_stack(name)

    def children(self, nodes):
        for node in nodes:
            self.statement(node)

    def prepare(self, statements):
        pass

    def function_declaration(self, stmt):
        raise ValueError("Nested AS3 functions require an ABC compilation context")

    def function_expression(self, expr):
        raise ValueError("Nested AS3 functions require an ABC compilation context")

    def finish_method(self):
        raise NotImplementedError

    def jump_false(self, label):
        raise NotImplementedError

    def statement(self, stmt):
        if isinstance(stmt, Var):
            self.declare(stmt)
        elif isinstance(stmt, FunctionDeclaration):
            self.function_declaration(stmt)
        elif isinstance(stmt, Assign):
            self.assign(stmt.target, stmt.value)
        elif isinstance(stmt, CompoundAssign):
            self.compound_assign(stmt)
        elif isinstance(stmt, Expr):
            self.expression(stmt.value)
            self.emit(self.pop_instruction)
        elif isinstance(stmt, If):
            other, end = self.new_label(), self.new_label()
            self.expression(stmt.condition)
            self.jump_false(other)
            for child in stmt.then:
                self.statement(child)
            self.emit(f"{self.jump_instruction} {end}")
            self.label(other)
            for child in stmt.otherwise:
                self.statement(child)
            self.label(end)
        elif isinstance(stmt, While):
            header, end = self.new_label(), self.new_label()
            self.label(header)
            self.expression(stmt.condition)
            self.jump_false(end)
            self.controls.append((header, end, len(self.tries)))
            for child in stmt.body:
                self.statement(child)
            self.controls.pop()
            self.emit(f"{self.jump_instruction} {header}")
            self.label(end)
        elif isinstance(stmt, For):
            self.children(stmt.initial)
            header, update, end = self.new_label(), self.new_label(), self.new_label()
            self.label(header)
            self.expression(stmt.condition)
            self.jump_false(end)
            self.controls.append((update, end, len(self.tries)))
            self.children(stmt.body)
            self.controls.pop()
            self.label(update)
            self.children(stmt.update)
            self.emit(f"{self.jump_instruction} {header}")
            self.label(end)
        elif isinstance(stmt, ForEnumeration):
            self.enumeration(stmt)
        elif isinstance(stmt, Switch):
            selector = self.temporary()
            self.expression(stmt.value)
            self.save_temp(selector)
            end = self.new_label()
            labels = [self.new_label() for _ in stmt.cases]
            default = end
            for (case, _), label in zip(stmt.cases, labels):
                if case is None:
                    default = label
                else:
                    self.load(selector)
                    self.expression(case)
                    self.binary("===")
                    self.emit(f"{self.true_instruction} {label}")
            self.emit(f"{self.jump_instruction} {default}")
            self.controls.append((None, end, len(self.tries)))
            for (_, body), label in zip(stmt.cases, labels):
                self.label(label)
                self.children(body)
            self.controls.pop()
            self.label(end)
        elif isinstance(stmt, Try):
            self.try_statement(stmt)
        elif isinstance(stmt, Exit):
            if stmt.kind in ("break", "continue"):
                control = next((c for c in reversed(self.controls) if stmt.kind == "break" or c[0]), None)
                if control is None:
                    raise ValueError(f"{stmt.kind} outside a loop or switch")
                self.complete(stmt.kind, None, control[1 if stmt.kind == "break" else 0], control[2])
            elif stmt.kind == "throw":
                self.expression(stmt.value)
                self.emit(self.throw_instruction)
            else:
                if self.tries:
                    value = self.capture_return(stmt.value)
                    self.complete("return", value)
                else:
                    self.return_value(stmt.value)
        else:
            raise ValueError(f"Unsupported statement: {type(stmt).__name__}")

    def capture_return(self, value):
        if not self.avm1 and value is not None and self.return_type == "void":
            raise ValueError("A void method cannot return a value")
        if not self.avm1 and value is None and (self.return_type == "void" or self.constructor):
            return None
        name = self.temporary()
        self.expression(value or Literal("undefined", None))
        if not self.avm1:
            self.coerce_return()
        self.save_temp(name)
        return Name(name)

    def complete(self, kind, value=None, destination=None, depth=0):
        if len(self.tries) > depth:
            context = self.tries[-1]
            route = len(context["routes"]) + 2
            context["routes"].append((route, kind, value, destination, depth))
            self.assign(Name(context["route"]), Literal("number", route))
            self.emit(f"{self.jump_instruction} {context['exit']}")
        elif kind in ("break", "continue"):
            self.emit(f"{self.jump_instruction} {destination}")
        elif kind == "throw":
            self.expression(value)
            self.emit(self.throw_instruction)
        else:
            self.return_value(value)

    def begin_try(self):
        route, error = self.temporary(), self.temporary()
        self.literal(Literal("number", 0))
        self.save_temp(route)
        context = {"route": route, "error": error, "routes": [], "exit": None}
        self.tries.append(context)
        return context

    def pending_throw(self, context, error):
        self.load(error)
        self.save_temp(context["error"])
        self.assign(Name(context["route"]), Literal("number", 1))

    def end_try(self, stmt, context):
        self.tries.pop()
        self.children(stmt.finalizer)
        end, thrown = self.new_label(), self.new_label()
        routes = [(1, "throw", Name(context["error"]), None, 0)] + context["routes"]
        labels = [thrown] + [self.new_label() for _ in context["routes"]]
        for (route, *_), label in zip(routes, labels):
            self.load(context["route"])
            self.literal(Literal("number", route))
            self.binary("===")
            self.emit(f"{self.true_instruction} {label}")
        self.emit(f"{self.jump_instruction} {end}")
        for (_, kind, value, destination, depth), label in zip(routes, labels):
            self.label(label)
            # Throw is a real exception, not a return-like completion: outer
            # catches must see it before their finally blocks are executed.
            if kind == "throw":
                self.expression(value)
                self.emit(self.throw_instruction)
            else:
                self.complete(kind, value, destination, depth)
        self.label(end)

    def declare(self, stmt):
        self.assign(Name(stmt.name), stmt.value)

    def compound_assign(self, stmt):
        if not self.avm1:
            raise ValueError("Computed compound assignment is not supported by this emitter")
        target = stmt.target
        obj_temp, key_temp = self.temporary(), self.temporary()
        self.expression(target.obj)
        self.save_temp(obj_temp)
        self.expression(target.key)
        self.save_temp(key_temp)
        self.load(obj_temp)
        self.load(key_temp)
        self.emit("ActionGetMember")
        self.expression(stmt.value)
        self.emit("ActionAdd2" if stmt.op == "+" else "ActionSubtract")
        value_temp = self.temporary()
        self.save_temp(value_temp)
        self.load(obj_temp)
        self.load(key_temp)
        self.load(value_temp)
        self.emit("ActionSetMember")

    def short_circuit(self, expr):
        end = self.new_label()
        self.expression(expr.left)
        self.emit(self.dup_instruction)
        if expr.op == "&&":
            self.jump_false(end)
        else:
            self.emit(f"{self.true_instruction} {end}")
        self.emit(self.pop_instruction)
        self.expression(expr.right)
        self.label(end)

    def expression(self, expr):
        if isinstance(expr, Literal):
            self.literal(expr)
        elif isinstance(expr, Name):
            self.load(expr.name)
        elif isinstance(expr, Member):
            self.member(expr)
        elif isinstance(expr, ComputedMember):
            if not self.avm1:
                self.expression(expr.obj)
                self.expression(expr.key)
                self.emit(f"getproperty #{self.runtime_multiname_index}")
            else:
                self.computed_member(expr)
        elif isinstance(expr, Call):
            self.call(expr)
        elif isinstance(expr, FunctionExpression):
            self.function_expression(expr)
        elif isinstance(expr, Update):
            if not self.avm1 and not isinstance(expr.target, Name):
                self.update(expr)
                return
            if not self.avm1 and self.local_types.get(self.aliases.get(expr.target.name, expr.target.name)) in ("String", "Boolean", "Object"):
                raise ValueError("Numeric updates of non-numeric typed locals require P-code")
            if self.avm1:
                self.update(expr)
                return
            self.load(expr.target.name)
            self.emit("ActionToNumber" if self.avm1 else "convert_d")
            if expr.postfix:
                self.emit(self.dup_instruction)
            self.literal(Literal("number", 1))
            self.emit(self.add_instruction if expr.op == "++" else self.subtract_instruction)
            if not self.avm1:
                self.coerce_local(expr.target.name)
            if not expr.postfix:
                self.emit(self.dup_instruction)
            self.store_stack(expr.target.name)
        elif isinstance(expr, ArrayLiteral):
            staged = []
            for item in expr.items:
                temp = self.temporary()
                self.expression(item)
                self.save_temp(temp)
                staged.append(temp)
            # AVM1 InitArray's first pop becomes index 0; AVM2 newarray
            # restores source order while constructing the Array. Both must
            # evaluate items first in source order, but only AVM1 reverses the
            # staged values before placing them on the operand stack.
            values = reversed(staged) if self.avm1 else staged
            for temp in values:
                self.load(temp)
            if self.avm1:
                self.emit(f"ActionPush {len(expr.items)}")
                self.emit("ActionInitArray")
            else:
                self.emit(f"newarray {len(expr.items)}")
        elif isinstance(expr, ObjectLiteral):
            staged = []
            for key, item in expr.items:
                temp = self.temporary()
                self.expression(item)
                self.save_temp(temp)
                staged.append((key, temp))
            # AVM1 InitObject consumes topmost pairs directly, so it needs
            # reversed pairs. AVM2 newobject reassembles consumed pairs in
            # source order, so its stack input stays in source order.
            pairs = reversed(staged) if self.avm1 else staged
            for key, temp in pairs:
                if self.avm1:
                    self.literal(Literal("string", key))
                else:
                    self.emit(f"pushstring {quote_string(key)}")
                self.load(temp)
            if self.avm1:
                self.emit(f"ActionPush {len(expr.items)}")
                self.emit("ActionInitObject")
            else:
                self.emit(f"newobject {len(expr.items)}")
        elif isinstance(expr, Binary):
            if expr.op in ("&&", "||"):
                self.short_circuit(expr)
            else:
                self.expression(expr.left)
                self.expression(expr.right)
                self.binary(expr.op)
        elif isinstance(expr, Unary):
            if expr.op == "-" and self.avm1:
                self.literal(Literal("number", 0))
                self.expression(expr.value)
                self.emit("ActionSubtract")
            else:
                self.expression(expr.value)
                if expr.op == "!":
                    self.emit("ActionNot" if self.avm1 else "not")
                elif expr.op == "-":
                    self.emit("negate")
                elif expr.op == "~":
                    if self.avm1:
                        self.literal(Literal("number", -1))
                        self.emit("ActionBitXor")
                    else:
                        self.emit("bitnot")
                elif not self.avm1:
                    self.emit("convert_d")
        else:
            raise ValueError(f"Unsupported expression: {type(expr).__name__}")


class AVM1Emitter(Emitter):
    avm1 = True
    pop_instruction, dup_instruction = "ActionPop", "ActionPushDuplicate"
    jump_instruction, true_instruction = "ActionJump", "ActionIf"
    add_instruction, subtract_instruction, throw_instruction = "ActionAdd2", "ActionSubtract", "ActionThrow"

    def __init__(self, *, function_scope=False):
        super().__init__()
        self.function_scope = function_scope
        self.hoisted = set()

    def prepare(self, statements):
        for statement in statements:
            if isinstance(statement, FunctionDeclaration):
                self.emit_function(statement.function, statement.name)
                self.hoisted.add(id(statement))

    def function_declaration(self, stmt):
        if id(stmt) not in self.hoisted:
            raise ValueError("Function declarations inside control-flow blocks need semantic linking; use an anonymous function expression")

    def function_expression(self, expr):
        self.emit_function(expr, "")

    def emit_function(self, expr, name):
        if expr.return_type != "*" or any(parameter.type_name != "*" for parameter in expr.parameters):
            raise ValueError("The AVM1 function tier currently accepts untyped signatures only")
        if self.aliases:
            raise ValueError("Functions created in catch scope require distinct captured catch bindings")
        if name in ("trace", "stop", "play") or any(parameter.name in ("trace", "stop", "play") for parameter in expr.parameters):
            raise ValueError("Shadowing native AVM1 intrinsics requires an explicit callable binding")
        end = self.new_label()
        child = AVM1Emitter(function_scope=True)
        child.counter = self.counter
        child.used_names.update(parameter.name for parameter in expr.parameters)
        body = child.compile(expr.body)
        self.counter = child.counter
        parameters = ", ".join(quote_string(parameter.name) for parameter in expr.parameters)
        self.emit(f"ActionDefineFunction {quote_string(name)} [{parameters}] {end}")
        self.lines.extend(body.splitlines())
        self.label(end)

    def declare(self, stmt):
        if stmt.name in ("trace", "stop", "play") and isinstance(stmt.value, FunctionExpression):
            raise ValueError("Shadowing native AVM1 intrinsics requires an explicit callable binding")
        if not self.function_scope:
            return super().declare(stmt)
        self.literal(Literal("string", stmt.name))
        self.expression(stmt.value)
        self.emit("ActionDefineLocal")

    def enumeration(self, stmt):
        if stmt.values:
            raise ValueError("AS1/AS2 does not have for each syntax")
        target = Name(stmt.target.name) if isinstance(stmt.target, Var) else stmt.target
        if isinstance(stmt.target, Var):
            self.declare(stmt.target)
        collection, keys, count, key, index = [self.temporary() for _ in range(5)]
        self.expression(stmt.collection);self.save_temp(collection)
        self.emit("ActionPush 0");self.emit("ActionInitArray");self.save_temp(keys)
        self.literal(Literal("number", 0));self.save_temp(count)
        self.load(collection);self.emit("ActionEnumerate2")
        drain, ready = self.new_label(), self.new_label()
        self.label(drain);self.save_temp(key)
        self.load(key);self.literal(Literal("null", None));self.binary("===")
        self.emit(f"ActionIf {ready}")
        self.load(keys);self.load(count);self.load(key);self.emit("ActionSetMember")
        self.load(count);self.literal(Literal("number", 1));self.binary("+");self.store_stack(count)
        self.emit(f"ActionJump {drain}");self.label(ready)
        # Enumerate2 snapshots all keys on the operand stack. Drain before
        # entering user code so abrupt completions cannot leak that stack.
        self.literal(Literal("number", 0));self.save_temp(index)
        header, end = self.new_label(), self.new_label()
        self.label(header);self.load(index);self.load(count);self.binary("<");self.jump_false(end)
        self.load(keys);self.load(index);self.emit("ActionGetMember");self.save_temp(key)
        self.load(index);self.literal(Literal("number", 1));self.binary("+");self.store_stack(index)
        self.assign(target, Name(key))
        self.controls.append((header, end, len(self.tries)))
        self.children(stmt.body);self.controls.pop()
        self.emit(f"ActionJump {header}");self.label(end)

    def finish_method(self):
        if self.function_scope:
            self.emit("ActionPush undefined")
            self.emit("ActionReturn")
        else:
            self.emit("end")

    def try_statement(self, stmt):
        context = self.begin_try()
        caught = self.temporary()
        catch_start, catch_end = self.new_label(), self.new_label()
        # ActionTry's header contains sizes of the three *inline* streams,
        # not a payload containing the bodies. Finally is lowered outside
        # the container so abrupt completions cannot run it twice.
        self.emit(f"ActionTry 1 {quote_string(caught)} {catch_start} {catch_end} {catch_end}")
        context["exit"] = catch_end
        self.children(stmt.body)
        # AVM1 containers are inline: explicit normal/abrupt jumps skip catch
        # streams. Do not rely on a renderer/interpreter to skip them for us.
        self.emit(f"ActionJump {catch_end}")
        self.label(catch_start)
        for catch in stmt.catches:
            next_catch = self.new_label()
            if catch.type_name != "*":
                self.load(caught)
                parts = catch.type_name.split(".")
                type_expr = Name(parts[0])
                for part in parts[1:]:
                    type_expr = Member(type_expr, part)
                self.expression(type_expr)
                self.emit("ActionInstanceOf")
                self.jump_false(next_catch)
            # Protect the catch body too: an exception thrown from it must
            # bypass sibling catches, execute finally and reach an outer try.
            secondary = self.temporary()
            failed, finished = self.new_label(), self.new_label()
            self.emit(f"ActionTry 1 {quote_string(secondary)} {failed} {finished} {finished}")
            context["exit"] = finished
            old = self.aliases.copy()
            self.aliases[catch.name] = caught
            self.children(catch.body)
            self.aliases = old
            self.emit(f"ActionJump {finished}")
            self.label(failed)
            self.pending_throw(context, secondary)
            self.label(finished)
            self.emit(f"ActionJump {catch_end}")
            self.label(next_catch)
        self.pending_throw(context, caught)
        self.label(catch_end)
        self.end_try(stmt, context)

    def jump_false(self, label):
        self.emit("ActionNot")
        self.emit(f"ActionIf {label}")

    def literal(self, expr):
        value = quote_string(expr.value) if expr.kind == "string" else expr.value if expr.kind == "number" else expr.kind
        if expr.kind == "number" and isinstance(value, int) and not -(2 ** 31) <= value < 2 ** 31:
            value = str(float(value))
        if expr.kind == "string" and "\0" in expr.value:
            raise ValueError("AVM1 null bytes in strings require P-code construction")
        self.emit(f"ActionPush {value}")

    def load(self, name):
        name = self.aliases.get(name, name)
        self.emit(f"ActionPush {quote_string(name)}")
        self.emit("ActionGetVariable")

    def store_stack(self, name):
        name = self.aliases.get(name, name)
        self.emit(f"ActionPush {quote_string(name)}")
        self.emit("ActionStackSwap")
        self.emit("ActionSetVariable")

    def assign(self, target, value):
        if isinstance(target, Name):
            target = Name(self.aliases.get(target.name, target.name))
            if target.name == "this":
                raise ValueError("Cannot assign to this")
            self.emit(f"ActionPush {quote_string(target.name)}")
            self.expression(value)
            self.emit("ActionSetVariable")
        elif isinstance(target, Member):
            self.expression(target.obj)
            self.literal(Literal("string", target.name))
            self.expression(value)
            self.emit("ActionSetMember")
        elif isinstance(target, ComputedMember):
            self.expression(target.obj)
            self.expression(target.key)
            self.expression(value)
            self.emit("ActionSetMember")
        else:
            raise ValueError("Invalid assignment target")

    def member(self, expr):
        self.expression(expr.obj)
        self.literal(Literal("string", expr.name))
        self.emit("ActionGetMember")

    def computed_member(self, expr):
        self.expression(expr.obj)
        self.expression(expr.key)
        self.emit("ActionGetMember")

    def update(self, expr):
        target = expr.target
        if isinstance(target, Name):
            if target.name == "this":
                raise ValueError("Cannot update this")
            self.load(target.name)
            self.emit("ActionToNumber")
            if expr.postfix:
                self.emit(self.dup_instruction)
            self.literal(Literal("number", 1))
            self.emit(self.add_instruction if expr.op == "++" else self.subtract_instruction)
            if not expr.postfix:
                self.emit(self.dup_instruction)
            self.store_stack(target.name)
            return

        obj_temp, key_temp = self.temporary(), self.temporary()
        self.expression(target.obj)
        self.save_temp(obj_temp)
        if isinstance(target, ComputedMember):
            self.expression(target.key)
        else:
            self.literal(Literal("string", target.name))
        self.save_temp(key_temp)
        self.load(obj_temp)
        self.load(key_temp)
        self.emit("ActionGetMember")
        self.emit("ActionToNumber")
        old_value = self.temporary() if expr.postfix else None
        if old_value:
            self.emit(self.dup_instruction)
            self.save_temp(old_value)
        self.literal(Literal("number", 1))
        self.emit(self.add_instruction if expr.op == "++" else self.subtract_instruction)
        new_value = self.temporary()
        self.save_temp(new_value)
        self.load(obj_temp)
        self.load(key_temp)
        self.load(new_value)
        self.emit("ActionSetMember")
        self.load(old_value or new_value)

    def call(self, expr):
        if isinstance(expr.callee, FunctionExpression):
            function = self.temporary()
            self.expression(expr.callee);self.save_temp(function)
            return self.call(Call(Name(function), expr.args))
        if isinstance(expr.callee, Name) and expr.callee.name in ("stop", "play", "trace"):
            name = expr.callee.name
            expected = 1 if name == "trace" else 0
            if len(expr.args) != expected:
                raise ValueError(f"{name} expects {expected} arguments in native AVM1")
            for arg in expr.args:
                self.expression(arg)
            self.emit({"stop": "ActionStop", "play": "ActionPlay", "trace": "ActionTrace"}[name])
            self.emit("ActionPush undefined")
            return
        arguments = []
        for arg in expr.args:
            staged = self.temporary()
            self.expression(arg);self.save_temp(staged)
            arguments.append(staged)
        for staged in reversed(arguments):
            self.load(staged)
        self.emit(f"ActionPush {len(expr.args)}")
        if isinstance(expr.callee, Name):
            self.literal(Literal("string", expr.callee.name))
            self.emit("ActionCallFunction")
        elif isinstance(expr.callee, Member):
            self.expression(expr.callee.obj)
            self.literal(Literal("string", expr.callee.name))
            self.emit("ActionCallMethod")
        else:
            raise ValueError("Indirect calls are not supported; use P-code")

    def binary(self, op):
        operations = {"+": "ActionAdd2", "-": "ActionSubtract", "*": "ActionMultiply", "/": "ActionDivide",
                      "%": "ActionModulo", "==": "ActionEquals2", "!=": "ActionEquals2", "===": "ActionStrictEquals",
                      "!==": "ActionStrictEquals", "<": "ActionLess2", ">": "ActionGreater", "<=": "ActionGreater", ">=": "ActionLess2"}
        operations.update({"&":"ActionBitAnd", "|":"ActionBitOr", "^":"ActionBitXor", "<<":"ActionBitLShift", ">>":"ActionBitRShift", ">>>":"ActionBitURShift"})
        if op not in operations:
            raise ValueError(f"Unsupported operator {op}")
        self.emit(operations[op])
        if op in ("!=", "!==", "<=", ">="):
            self.emit("ActionNot")

    def return_value(self, value):
        self.expression(value or Literal("undefined", None))
        self.emit("ActionReturn")


class AVM2Emitter(Emitter):
    avm1 = False
    pop_instruction, dup_instruction = "pop", "dup"
    jump_instruction, true_instruction = "jump", "iftrue"
    add_instruction, subtract_instruction, throw_instruction = "add", "subtract", "throw"

    def __init__(self, registers=None, next_register=1, return_type="*", constructor=False, register_types=None, bindings=None, runtime_multiname_index=None):
        super().__init__()
        self.registers = dict(registers or {})
        self.next_register = next_register
        self.return_type = return_type
        self.constructor = constructor
        self.super_called = False
        self.local_types = dict(register_types or {})
        self.bindings = bindings
        self.runtime_multiname_index = runtime_multiname_index
        self.closure_context = None

    def enable_closures(self, abc, method_index, init_scope_depth=0, **kwargs):
        from .closure_compile import ClosureCompilation
        self.closure_context = ClosureCompilation(self, abc, method_index, init_scope_depth, **kwargs)

    def captured(self, name):
        return self.closure_context is not None and name in self.closure_context.references

    def function_expression(self, expression):
        if self.closure_context is None:
            raise ValueError("Nested AS3 functions require an ABC compilation context")
        self.closure_context.function(expression)

    def function_declaration(self, statement):
        if self.closure_context is None:
            raise ValueError("Nested AS3 functions require an ABC compilation context")
        self.closure_context.declaration(statement)

    def property_operand(self, text, receiver=None):
        return self.bindings.operand(text, receiver=receiver) if self.bindings else text

    def qualified_source_name(self, expr):
        parts = []
        while isinstance(expr, Member):
            parts.append(expr.name)
            expr = expr.obj
        if not isinstance(expr, Name) or expr.name in self.registers or self.captured(expr.name) or expr.name in getattr(self.bindings, "members", {}):
            return None
        text = ".".join([expr.name, *reversed(parts)])
        resolver = getattr(self.bindings, "qualified_operand", None)
        return text if resolver and resolver(text) is not None else None

    def prepare(self, statements):
        def reserve(nodes):
            for node in nodes:
                if isinstance(node, Var):
                    if node.type_name not in (None, "*", "int", "uint", "Number", "String", "Boolean", "Object"):
                        resolver = getattr(self.bindings, "resolve_local_type", None)
                        if resolver is None and self.closure_context is not None:
                            resolver = lambda name, local_name: f"#{self.closure_context.type_index(name)}"
                        if resolver is None:
                            raise ValueError(f"Local type {node.type_name} is not supported yet; use P-code")
                        self.local_types[node.name] = resolver(node.type_name, node.name)
                    else:
                        self.local_types[node.name] = node.type_name
                    if node.name not in self.registers:
                        self.registers[node.name] = self.next_register
                        self.next_register += 1
                elif isinstance(node, FunctionDeclaration):
                    if self.closure_context is None:
                        raise ValueError("Nested AS3 functions require an ABC compilation context")
                    self.local_types[node.name] = f"#{self.closure_context.type_index('Function')}"
                    if node.name not in self.registers:
                        self.registers[node.name] = self.next_register
                        self.next_register += 1
                elif isinstance(node, If):
                    reserve(node.then)
                    reserve(node.otherwise)
                elif isinstance(node, While):
                    reserve(node.body)
                elif isinstance(node, For):
                    reserve(node.initial)
                    reserve(node.body)
                elif isinstance(node, ForEnumeration):
                    if isinstance(node.target, Var):
                        reserve([node.target])
                    reserve(node.body)
                elif isinstance(node, Switch):
                    for _, body in node.cases:
                        reserve(body)
                elif isinstance(node, Try):
                    reserve(node.body)
                    for catch in node.catches:
                        reserve(catch.body)
                    reserve(node.finalizer)
        reserve(statements)
        self.emit("getlocal_0")
        self.emit("pushscope")
        if self.closure_context is not None:
            self.closure_context.prepare(statements)

    def handler_entry(self, local):
        self.save_temp(local)
        # AVM2 restores the captured (initial) scope on exception delivery.
        # Our only dynamic scope is this; catch bindings live in hygienic
        # registers because activation/closure compilation is not supported.
        self.emit("getlocal_0")
        self.emit("pushscope")
        if self.closure_context is not None:
            self.closure_context.restore_scope()

    def enumeration(self, stmt):
        target = Name(stmt.target.name) if isinstance(stmt.target, Var) else stmt.target
        if isinstance(stmt.target, Var):
            self.declare(stmt.target)
        collection, index = self.temporary(), self.temporary()
        self.expression(stmt.collection);self.save_temp(collection)
        self.literal(Literal("number", 0));self.save_temp(index)
        header, end = self.new_label(), self.new_label()
        self.label(header)
        self.emit(f"hasnext2 {self.registers[collection]} {self.registers[index]}")
        self.jump_false(end)
        self.load(collection);self.load(index);self.emit("nextvalue" if stmt.values else "nextname")
        value = self.temporary();self.save_temp(value)
        self.assign(target, Name(value))
        self.controls.append((header, end, len(self.tries)))
        self.children(stmt.body);self.controls.pop()
        self.emit(f"jump {header}");self.label(end)

    def try_statement(self, stmt):
        context = self.begin_try()
        start, stop, finish, failed = [self.new_label() for _ in range(4)]
        self.label(start)
        context["exit"] = stop
        self.children(stmt.body)
        self.label(stop)
        self.emit(f"jump {finish}")
        regions = []
        for catch in stmt.catches:
            handler, catch_stop = self.new_label(), self.new_label()
            caught = self.temporary()
            self.label(handler)
            self.handler_entry(caught)
            context["exit"] = catch_stop
            old = self.aliases.copy()
            self.aliases[catch.name] = caught
            self.children(catch.body)
            self.aliases = old
            self.label(catch_stop)
            self.emit(f"jump {finish}")
            namespace, _, local = catch.type_name.rpartition(".")
            type_name = namespace + "::" + local if namespace else catch.type_name
            regions.append((start, stop, handler, type_name, catch.name))
            regions.append((handler, catch_stop, failed, "*", ""))
        if not any(c.type_name == "*" for c in stmt.catches):
            regions.append((start, stop, failed, "*", ""))
        self.label(failed)
        self.handler_entry(context["error"])
        self.assign(Name(context["route"]), Literal("number", 1))
        self.label(finish)
        # Inner regions are appended first, so nested handlers win before
        # an enclosing catch-all. Typed sibling catches keep source order.
        self.exception_regions.extend(regions)
        self.end_try(stmt, context)

    def finish_method(self):
        if self.constructor and not self.super_called:
            raise ValueError("An AS3 instance constructor must preserve its super(...) call; use P-code")
        if self.return_type == "void" or self.constructor:
            self.emit("returnvoid")
        else:
            self.emit("pushundefined")
            self.coerce_return()
            self.emit("returnvalue")

    def coerce_return(self):
        conversions = {"int": "coerce_i", "uint": "coerce_u", "Number": "coerce_d", "String": "coerce_s", "Boolean": "coerce_b"}
        if self.return_type in conversions:
            self.emit(conversions[self.return_type])
        elif self.return_type not in ("*", "void"):
            self.emit(f"coerce {self.return_type}")

    def jump_false(self, label):
        self.emit(f"iffalse {label}")

    def literal(self, expr):
        if expr.kind == "number":
            value = expr.value
            if isinstance(value, int) and -128 <= value <= 127:
                self.emit(f"pushbyte {value}")
            elif isinstance(value, int) and -(2 ** 31) <= value < 2 ** 31:
                self.emit(f"pushint {value}")
            else:
                self.emit(f"pushdouble {float(value)}")
        elif expr.kind == "string":
            self.emit(f"pushstring {quote_string(expr.value)}")
        else:
            self.emit({"true": "pushtrue", "false": "pushfalse", "null": "pushnull", "undefined": "pushundefined"}[expr.kind])

    def load(self, name):
        name = self.aliases.get(name, name)
        if self.captured(name):
            self.closure_context.load(name)
        elif name == "this":
            self.emit("getlocal_0")
        elif name in self.registers:
            self.emit(f"getlocal {self.registers[name]}")
        else:
            operand = self.property_operand(name)
            self.emit(f"findpropstrict {operand}")
            self.emit(f"getproperty {operand}")

    def store_stack(self, name):
        name = self.aliases.get(name, name)
        if self.captured(name):
            self.closure_context.store(name)
        elif name in self.registers:
            self.coerce_local(name)
            self.emit(f"setlocal {self.registers[name]}")
        elif name == "this":
            raise ValueError("Cannot assign to this")
        else:
            operand = self.property_operand(name)
            self.emit(f"findproperty {operand}")
            self.emit("swap")
            self.emit(f"setproperty {operand}")

    def coerce_local(self, name):
        name = self.aliases.get(name, name)
        conversion = {"int": "coerce_i", "uint": "coerce_u", "Number": "coerce_d", "String": "coerce_s", "Boolean": "coerce_b", "Object": "coerce_o"}.get(self.local_types.get(name))
        if conversion:
            self.emit(conversion)
        elif isinstance(self.local_types.get(name), str) and self.local_types[name].startswith("#"):
            self.emit(f"coerce {self.local_types[name]}")

    def assign(self, target, value):
        if isinstance(target, Name):
            target = Name(self.aliases.get(target.name, target.name))
            if target.name == "this":
                raise ValueError("Cannot assign to this")
            if target.name in self.registers or self.captured(target.name):
                self.expression(value)
                self.store_stack(target.name)
            else:
                operand = self.property_operand(target.name)
                self.emit(f"findproperty {operand}")
                self.expression(value)
                self.emit(f"setproperty {operand}")
        elif isinstance(target, Member):
            if isinstance(target.obj, Name) and target.obj.name == "super":
                if self.bindings is None or not hasattr(self.bindings, "super_operand"):
                    raise ValueError("super has no verified class hierarchy")
                self.emit("getlocal_0")
                self.expression(value)
                self.emit(f"setsuper {self.bindings.super_operand(target.name)}")
                return
            operand = self.property_operand(target.name, target.obj.name if isinstance(target.obj, Name) else "unproven")
            self.expression(target.obj)
            self.expression(value)
            self.emit(f"setproperty {operand}")
        elif isinstance(target, ComputedMember):
            self.expression(target.obj)
            self.expression(target.key)
            self.expression(value)
            self.emit(f"setproperty #{self.runtime_multiname_index}")
        else:
            raise ValueError("Invalid assignment target")

    def member(self, expr):
        qualified = self.qualified_source_name(expr)
        if qualified:
            self.load(qualified)
            return
        if isinstance(expr.obj, Name) and expr.obj.name == "super":
            if self.bindings is None or not hasattr(self.bindings, "super_operand"):
                raise ValueError("super has no verified class hierarchy")
            self.emit("getlocal_0")
            self.emit(f"getsuper {self.bindings.super_operand(expr.name)}")
            return
        operand = self.property_operand(expr.name, expr.obj.name if isinstance(expr.obj, Name) else "unproven")
        self.expression(expr.obj)
        self.emit(f"getproperty {operand}")

    def compound_assign(self, stmt):
        target = stmt.target
        obj_temp, key_temp = self.temporary(), self.temporary()
        self.expression(target.obj)
        self.save_temp(obj_temp)
        self.expression(target.key)
        self.save_temp(key_temp)
        self.load(obj_temp)
        self.load(key_temp)
        self.emit(f"getproperty #{self.runtime_multiname_index}")
        self.expression(stmt.value)
        self.binary(stmt.op)
        value_temp = self.temporary()
        self.save_temp(value_temp)
        self.load(obj_temp)
        self.load(key_temp)
        self.load(value_temp)
        self.emit(f"setproperty #{self.runtime_multiname_index}")

    def update(self, expr):
        target = expr.target
        if isinstance(target, Name):
            raise AssertionError("Local updates are emitted by the shared AVM2 path")
        if not isinstance(target, ComputedMember):
            raise ValueError("Only local or computed-member updates are supported for AVM2")
        obj_temp, key_temp = self.temporary(), self.temporary()
        self.expression(target.obj)
        self.save_temp(obj_temp)
        self.expression(target.key)
        self.save_temp(key_temp)
        self.load(obj_temp)
        self.load(key_temp)
        self.emit(f"getproperty #{self.runtime_multiname_index}")
        self.emit("convert_d")
        old_value = self.temporary() if expr.postfix else None
        if old_value:
            self.emit(self.dup_instruction)
            self.save_temp(old_value)
        self.literal(Literal("number", 1))
        self.emit("add" if expr.op == "++" else "subtract")
        new_value = self.temporary()
        self.save_temp(new_value)
        self.load(obj_temp)
        self.load(key_temp)
        self.load(new_value)
        self.emit(f"setproperty #{self.runtime_multiname_index}")
        self.load(old_value or new_value)

    def call(self, expr):
        count = len(expr.args)
        if isinstance(expr.callee, Name) and expr.callee.name == "super":
            if not self.constructor:
                raise ValueError("super(...) is only valid in an instance constructor")
            self.emit("getlocal_0")
            for arg in expr.args:
                self.expression(arg)
            self.emit(f"constructsuper {count}")
            self.super_called = True
            self.emit("pushundefined")
            return
        if isinstance(expr.callee, Name) and expr.callee.name not in self.registers and not self.captured(expr.callee.name):
            name = expr.callee.name
            operand = self.property_operand(name)
            self.emit(f"findpropstrict {operand}")
            for arg in expr.args:
                self.expression(arg)
            self.emit(f"callproperty {operand} {count}")
        elif isinstance(expr.callee, Member):
            if isinstance(expr.callee.obj, Name) and expr.callee.obj.name == "super":
                if self.bindings is None or not hasattr(self.bindings, "super_operand"):
                    raise ValueError("super has no verified class hierarchy")
                self.emit("getlocal_0")
                for arg in expr.args:
                    self.expression(arg)
                self.emit(f"callsuper {self.bindings.super_operand(expr.callee.name)} {count}")
                return
            receiver = expr.callee.obj.name if isinstance(expr.callee.obj, Name) else self.qualified_source_name(expr.callee.obj) or "unproven"
            operand = self.property_operand(expr.callee.name, receiver)
            self.expression(expr.callee.obj)
            for arg in expr.args:
                self.expression(arg)
            self.emit(f"callproperty {operand} {count}")
        else:
            self.expression(expr.callee)
            self.emit("pushnull")
            for arg in expr.args:
                self.expression(arg)
            self.emit(f"call {count}")

    def binary(self, op):
        operations = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide", "%": "modulo",
                      "==": "equals", "!=": "equals", "===": "strictequals", "!==": "strictequals",
                      "<": "lessthan", "<=": "lessequals", ">": "greaterthan", ">=": "greaterequals",
                      "&": "bitand", "|": "bitor", "^": "bitxor", "<<": "lshift", ">>": "rshift", ">>>": "urshift"}
        if op not in operations:
            raise ValueError(f"Unsupported operator {op}")
        self.emit(operations[op])
        if op in ("!=", "!=="):
            self.emit("not")

    def return_value(self, value):
        if value is not None and self.return_type == "void":
            raise ValueError("A void method cannot return a value")
        if value is None and (self.return_type == "void" or self.constructor):
            self.emit("returnvoid")
        else:
            self.expression(value or Literal("undefined", None))
            self.coerce_return()
            self.emit("returnvalue")


def compile_actionscript(source: str, target: str = "as3", *, registers=None,
                         next_register=1, return_type="*", constructor=False, register_types=None, bindings=None,
                         abc=None, method_index=None, init_scope_depth=0) -> str:
    parser = Parser(tokenize(source), target, bindings)
    ast = parser.parse_program()
    if target.lower() in ("as1", "as2", "avm1"):
        emitter = AVM1Emitter()
    elif target.lower() in ("as3", "avm2"):
        emitter = AVM2Emitter(registers, next_register, return_type, constructor, register_types, bindings,
                              parser.runtime_multiname_index)
        if abc is not None:
            emitter.enable_closures(abc, method_index, init_scope_depth)
    else:
        raise ValueError(f"Unsupported ActionScript target: {target}")
    return emitter.compile(ast)


def compile_as3_method(source, **context):
    return compile_actionscript(source, target="as3", **context)


def compile_avm1_script(source):
    return compile_actionscript(source, target="as1")
