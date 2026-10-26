"""Small, side-effect-aware IR shared by the two native decompilers."""
from dataclasses import dataclass, field, replace
import math

from ..script_strings import quote_string
from ..symbols import source_identifier


class ReconstructionError(ValueError):
    """A graph cannot be represented without changing observable behavior."""


@dataclass(frozen=True)
class Value:
    kind: str
    text: str
    args: tuple = ()
    literal: object = None
    origin: int = -1
    binding: object = None

    @property
    def reads(self):
        return {self.text} if self.kind in ("name", "dynamic_name") else set().union(*(v.reads for v in self.args))

    @property
    def effectful(self):
        return self.kind in ("call", "member", "construct", "function", "container", "dynamic_name", "qualified", "array", "object", "enumeration_string", "primitive_conversion", "descendant") or any(v.effectful for v in self.args)


def name(text, pc=-1):
    return Value("name", text, origin=pc)


def literal(value, pc=-1):
    if isinstance(value, str):
        text = quote_string(value)
    elif value is True or value is False:
        text = str(value).lower()
    elif value is None:
        text = "null"
    elif isinstance(value, float) and not math.isfinite(value):
        text = "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    else:
        text = str(value)
    return Value("literal", text, literal=value, origin=pc)


def binary(op, left, right, pc=-1):
    return Value("binary", op, (left, right), origin=min(v for v in (pc, left.origin, right.origin) if v >= 0) if any(v >= 0 for v in (pc, left.origin, right.origin)) else -1)


def negate(value):
    if value.kind == "unary" and value.text == "!":
        return value.args[0]
    if value.kind == "literal" and isinstance(value.literal, bool):
        return literal(not value.literal, value.origin)
    # Do not invert relational comparisons: !(x < y) is NOT x >= y for NaN.
    if value.kind == "binary" and value.text in ("==", "!=", "===", "!=="):
        return replace(value, text={"==": "!=", "!=": "==", "===": "!==", "!==": "==="}[value.text])
    return Value("unary", "!", (value,), origin=value.origin)


def render(value):
    if value.kind in ("name", "literal", "raw", "function", "container", "dynamic_name", "qualified"):
        return value.text
    if value.kind == "binary":
        return f"({render(value.args[0])} {value.text} {render(value.args[1])})"
    if value.kind == "unary":
        return f"{value.text}({render(value.args[0])})"
    if value.kind == "cast":
        return f"{value.text}({render(value.args[0])})"
    if value.kind == "primitive_conversion":
        operand = render(value.args[0])
        if value.text == "Number":
            return f"+({operand})"
        if value.text == "Boolean":
            return f"!(!({operand}))"
        return f"({operand} {'|' if value.text == 'int' else '>>>'} 0)"
    if value.kind == "member":
        obj, key = value.args
        receiver = "(" + render(obj) + ")" if obj.kind in ("object", "function") else render(obj)
        if value.binding is not None and key.kind == "literal" and isinstance(key.literal, str) and "::" in key.literal and all(source_identifier(part) for part in key.literal.split("::")):
            return receiver + "." + key.literal
        identifier = key.kind == "literal" and isinstance(key.literal,str) and (key.literal.isidentifier() or value.binding is not None and source_identifier(key.literal))
        return f"{receiver}.{key.literal}" if identifier else f"{receiver}[{render(key)}]"
    if value.kind == "descendant":
        obj, key = value.args
        receiver = "(" + render(obj) + ")" if obj.kind in ("object", "function") else render(obj)
        identifier = key.kind == "literal" and isinstance(key.literal, str) and (key.literal.isidentifier() or key.literal == "*")
        return f"{receiver}..{key.literal}" if identifier else f"{receiver}..[{render(key)}]"
    if value.kind == "array":
        return "[" + ", ".join(render(v) for v in value.args) + "]"
    if value.kind == "object":
        return "{" + ", ".join(render(value.args[i]) + ": " + render(value.args[i + 1]) for i in range(0, len(value.args), 2)) + "}"
    if value.kind in ("call", "construct"):
        prefix = "new " if value.kind == "construct" else ""
        callee = render(value.args[0])
        if value.args[0].kind in ("function", "object"):
            callee = "(" + callee + ")"
        return prefix + callee + "(" + ", ".join(render(v) for v in value.args[1:]) + ")"
    raise ReconstructionError(f"Cannot render internal value {value.kind}")


@dataclass
class Statement:
    kind: str
    value: Value | None = None
    target: Value | None = None
    pc: int = -1
    declared: bool = False
    type_name: str | None = None

    def source(self):
        if self.kind == "assign":
            if not self.declared and self.target.kind == self.value.kind == "name" and self.target.text == self.value.text:
                return ""
            if not self.declared and self.target.kind == "name" and self.value.kind == "binary" and self.value.text in ("+", "-") and self.value.args[0].kind == "name" and self.value.args[0].text == self.target.text and self.value.args[1].kind == "literal" and self.value.args[1].literal == 1:
                return render(self.target) + ("++" if self.value.text == "+" else "--") + ";"
            annotation = ":" + self.type_name if self.type_name else ""
            declaration = "var " if self.declared else ""
            return f"{declaration}{render(self.target)}{annotation if self.declared else ''} = {render(self.value)};"
        if self.kind == "expr":
            text = render(self.value)
            return ("(" + text + ")" if self.value.kind in ("function", "object") else text) + ";"
        if self.kind == "container":
            return render(self.value)
        if self.kind in ("return", "throw"):
            return self.kind + (" " + render(self.value) if self.value else "") + ";"
        if self.kind in ("break", "continue"):
            return self.kind + ";"
        raise ReconstructionError(f"Cannot render statement {self.kind}")


@dataclass
class Instruction:
    pc: int
    end: int
    op: str
    args: tuple = ()


@dataclass
class Block:
    pc: int
    instructions: list
    statements: list = field(default_factory=list)
    term: str = "jump"
    condition: Value | None = None
    successors: list = field(default_factory=list)
    stack_out: tuple = ()
    switch: object = None


@dataclass
class Switch:
    selector: Value
    cases: list
    default: int


@dataclass
class TryRegion:
    start: int
    stop: int
    catches: list  # (name, type, handler start, handler stop)
    after: int
    final_start: int | None = None
    final_stop: int | None = None
    exclude: set = field(default_factory=set)
    routes: dict = field(default_factory=dict)
    route_variable: str | None = None
    normal_stop: int | None = None


def blocks_for(instructions, leaders=()):
    if not instructions:
        return {}
    locations = {i.pc for i in instructions}
    starts = {instructions[0].pc, *leaders}
    for ins in instructions:
        if ins.op in ("jump", "branch"):
            starts.update((ins.args[-1], ins.end))
        elif ins.op == "lookup":
            starts.update((ins.args[0], *ins.args[1], ins.end))
        elif ins.op == "structured_switch":
            starts.update((ins.args[0].default, *(pc for _, pc in ins.args[0].cases), ins.end))
        elif ins.op in ("return", "throw", "end", "try", "structured_exit"):
            starts.add(ins.end)
    starts &= locations
    blocks, current = {}, None
    for ins in instructions:
        if ins.pc in starts:
            current = blocks[ins.pc] = Block(ins.pc, [])
        current.instructions.append(ins)
    for block in blocks.values():
        ins = block.instructions[-1]
        if ins.op == "jump":
            block.successors = [ins.args[0]]
        elif ins.op == "branch":
            block.successors = [ins.args[-1], ins.end]
        elif ins.op == "lookup":
            block.successors = list(dict.fromkeys([ins.args[0], *ins.args[1]]))
        elif ins.op not in ("return", "throw", "end", "structured_exit"):
            block.successors = [ins.end] if ins.end in blocks else []
    return blocks
