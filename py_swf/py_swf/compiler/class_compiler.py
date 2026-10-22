"""Bounded native AS3 class-program compiler; unsupported semantics fail closed.

The first tier emits Object-derived classes, constant-valued fields, metadata,
constructors, methods and accessors. It is not the complete AS3 language.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
import hashlib
import json
import math

from ..avm2 import (ABCFile, ClassInfo, InstanceInfo, MetadataInfo, MethodBodyInfo,
                   MethodInfo, ScriptInfo, TraitsInfo, assemble_instructions, search_or_add_string)
from ..bytecode_validation import analyze_method, decode_avm2
from ..decompile.hierarchy_contract import VERIFIED_ERROR_NAMES, external_error_constructors, type_key
from ..swf_parser import SWFFile, SWFTag
from .as_compiler import (AVM2Emitter, Binary, Call, Expr, Literal, Member, Name, Parser, Unary,
                         tokenize)
from .class_parser import FieldDecl, InitializerDecl, MethodDecl, Program, parse_class_program

# QName/hierarchy and constructor arity checked against playerglobal32_0.swc.
# Other Flash APIs require an explicit SDK linker; names are never guessed.
DISPLAY_BASES = {
    "flash.events.EventDispatcher": "Object",
    "flash.display.DisplayObject": "flash.events.EventDispatcher",
    "flash.display.InteractiveObject": "flash.display.DisplayObject",
    "flash.display.DisplayObjectContainer": "flash.display.InteractiveObject",
    "flash.display.Sprite": "flash.display.DisplayObjectContainer",
    "flash.display.MovieClip": "flash.display.Sprite",
}

BUILTINS=frozenset("Object Array String Number Boolean int uint Error TypeError RangeError ReferenceError SyntaxError Math JSON Function Namespace QName XML XMLList Class Date RegExp void".split())
CALLABLE_GLOBALS=frozenset("trace Object Array String Number Boolean int uint Error TypeError RangeError ReferenceError SyntaxError isNaN isFinite parseInt parseFloat Math JSON".split())


@dataclass
class Construct:
    type_name: str
    arguments: list


def _validate_error_constructor_bindings(statements, parameters, bindings):
    """A type spelling cannot bypass an AS3 lexical/member constructor binding."""
    from .as_compiler import FunctionDeclaration, FunctionExpression, Try
    from .closure_compile import declared_names

    def scope(body, inherited, parameters=()):
        names = inherited | declared_names(body) | {parameter.name for parameter in parameters}
        visit(body, names)

    def visit(node, names):
        if isinstance(node, Construct) and node.type_name in VERIFIED_ERROR_NAMES and node.type_name in names:
            raise ValueError("Error constructor type is shadowed by a lexical or member binding: " + node.type_name)
        if isinstance(node, FunctionExpression):
            scope(node.body, names, node.parameters)
        elif isinstance(node, FunctionDeclaration):
            scope(node.function.body, names, node.function.parameters)
        elif isinstance(node, Try):
            visit(node.body, names)
            for catch in node.catches:
                visit(catch.body, names | {catch.name})
            visit(node.finalizer, names)
        elif is_dataclass(node):
            for descriptor in fields(node):
                visit(getattr(node, descriptor.name), names)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child, names)

    scope(statements, set(bindings.members), parameters)


class ClassBodyParser(Parser):
    """Construction is enabled only with the class program's verified providers."""
    def unary(self):
        if not self.match("new"):
            return super().unary()
        parts = [self.name()]
        while self.match("."):
            parts.append(self.name())
        self.expect("(")
        arguments = []
        if self.peek().value != ")":
            arguments.append(self.expression())
            while self.match(","):
                arguments.append(self.expression())
        self.expect(")")
        value = Construct(".".join(parts), arguments)
        while self.match("."):
            value = Member(value, self.name())
            if self.match("("):
                args = []
                if self.peek().value != ")":
                    args.append(self.expression())
                    while self.match(","):
                        args.append(self.expression())
                self.expect(")")
                value = Call(value, args)
        return value

    def statement(self):
        if self.peek().value == "new":
            line = self.peek().line
            expression = self.expression()
            self.finish(line)
            return Expr(expression)
        return super().statement()


@dataclass
class CompiledClassProgram:
    abc: ABCFile
    source: str
    class_names: list[str]
    abi_sha256: str
    diagnostics: list[str]
    sources: dict[str, str] = field(default_factory=dict)

    def abc_bytes(self):
        return self.abc.serialize()


def _namespace(pool,kind,uri,*,unique=False):
    item={"kind":kind,"name":search_or_add_string(pool,uri)}
    if not unique:
        for index,current in enumerate(pool.namespaces[1:],1):
            if current==item:
                return index
    pool.namespaces.append(item)
    return len(pool.namespaces)-1


def _qname(pool,namespace,local):
    item={"kind":7,"ns":namespace,"name":search_or_add_string(pool,local)}
    for index,current in enumerate(pool.multinames[1:],1):
        if current==item:
            return index
    pool.multinames.append(item)
    return len(pool.multinames)-1


def _constant_expression(source):
    parser=Parser(tokenize(source),"as3")
    value=parser.expression()
    if parser.peek().kind!="eof":
        raise ValueError("Field/default initializer must be a single constant")
    def numeric(node):
        if isinstance(node, Literal) and node.kind == "number":
            return float(node.value)
        if isinstance(node, Unary) and node.op in ("+", "-"):
            number = numeric(node.value)
            return number if node.op == "+" else -number
        if isinstance(node, Binary) and node.op in ("+", "-", "*"):
            left, right = numeric(node.left), numeric(node.right)
            return {"+": lambda: left + right, "-": lambda: left - right, "*": lambda: left * right}[node.op]()
        raise ValueError("Initializer arithmetic must use numeric literals only")
    if isinstance(value, Binary):
        return "number", numeric(value)
    if isinstance(value,Unary) and value.op in ("+","-"):
        inner=value.value if hasattr(value,"value") else value.operand
        if not isinstance(inner,Literal) or inner.kind!="number":
            raise ValueError("Only numeric unary constants are supported")
        return "number",inner.value if value.op=="+" else -inner.value
    if isinstance(value,Name) and value.name in ("NaN","Infinity"):
        return "number",float("nan") if value.name=="NaN" else float("inf")
    if not isinstance(value,Literal):
        raise ValueError("Computed field/default initializers are not supported yet")
    return value.kind,value.value


def _pool_value(values,value):
    for index,current in enumerate(values[1:],1):
        if current==value:
            return index
    values.append(value)
    return len(values)-1


def _constant(pool,source,type_name="*"):
    kind,value=_constant_expression(source)
    if kind in ("true","false"):
        value=kind=="true"
    if type_name in ("int","uint","Number"):
        if kind not in ("number","true","false"):
            raise ValueError("Numeric defaults require numeric/boolean constants")
        if type_name=="Number":
            return _pool_value(pool.doubles,float(value)),6
        number=int(value) if math.isfinite(float(value)) else 0
        number&=0xffffffff
        if type_name=="uint":
            return _pool_value(pool.uintegers,number),4
        return _pool_value(pool.integers,number-2**32 if number>=2**31 else number),3
    if type_name=="String" and kind not in ("string","null","undefined"):
        raise ValueError("Implicit non-string field/default coercions need explicit source")
    if type_name=="Boolean" and kind not in ("true","false"):
        raise ValueError("Boolean defaults require boolean literals in this tier")
    if type_name not in ("*","Object","String","Boolean") and kind not in ("null","undefined"):
        raise ValueError("Reference-type defaults must be null/undefined")
    if kind=="string":
        return _pool_value(pool.strings,value),1
    if kind=="number":
        if isinstance(value,int) and -2**31<=value<2**31:
            return _pool_value(pool.integers,value),3
        return _pool_value(pool.doubles,float(value)),6
    if kind in ("true","false","null","undefined"):
        code={"true":11,"false":10,"null":12,"undefined":0}[kind]
        return code,code
    raise ValueError("Unsupported constant value")


class _ClassBindings:
    def __init__(self,builder,class_index,static=False):
        self.builder=builder
        self.pool=builder.abc.constant_pool
        self.owner=("class" if static else "instance",class_index)
        self.class_index=class_index
        self.static=static
        self.members=builder.member_names[class_index]
        self.local_types = {}

    def resolve_local_type(self, name, local_name=None):
        index = self.builder.type_name(name)
        if local_name is not None:
            self.local_types[local_name] = index
        return f"#{index}"

    def qualified_operand(self, name):
        if "." not in name:
            return None
        try:
            return f"#{self.builder.type_name(name)}"
        except ValueError:
            return None

    def super_operand(self, text):
        if self.static:
            raise ValueError("super is not available in static methods")
        entries = self.builder.inherited_members[self.class_index].get(text, [])
        if len({entry[0] for entry in entries}) != 1 or not entries or entries[0][1]:
            raise ValueError("Unresolved or ambiguous super member: " + text)
        return f"#{entries[0][0]}"

    def operand(self,text,*,receiver=None):
        if "::" in text:
            namespace, local = text.rsplit("::", 1)
            namespace_index = self.builder.public_ns if namespace == "public" else self.builder.namespace_name(namespace)
            return f"#{_qname(self.pool, namespace_index, local)}"
        if receiver not in (None, "this", "super"):
            index = self.local_types.get(receiver)
            class_receiver = False
            if index is None:
                try:
                    index = self.builder.type_name(receiver)
                    class_receiver = True
                except ValueError:
                    index = None
            provider = next((i for i, item in enumerate(self.builder.abc.instances) if item.name == index), None)
            if provider is not None:
                entries = self.builder.member_names[provider].get(text, [])
                permitted = [entry for entry in entries if entry[1] == class_receiver and (provider == self.class_index or entry[2] == "public" or entry[2] == "internal" and self.builder.program.classes[provider].package == self.builder.program.classes[self.class_index].package)]
                if len({entry[0] for entry in permitted}) != 1:
                    raise ValueError("Member has no accessible verified receiver binding: " + text)
                return f"#{permitted[0][0]}"
        entries=self.members.get(text,[])
        if text in self.builder.hidden_private[self.class_index] and not entries:
            raise ValueError("Inherited private member is inaccessible: " + text)
        if entries:
            names={entry[0] for entry in entries}
            if len(names)!=1:
                raise ValueError("Ambiguous class member namespace: "+text)
            index,static,visibility=entries[0]
            class_name=self.builder.program.classes[self.class_index].name
            if receiver=="this" and static:
                raise ValueError("Static members require the class receiver, not this")
            if receiver==class_name and not static:
                raise ValueError("An instance member cannot use the class receiver")
            if receiver not in (None,"this",class_name) and visibility!="public":
                raise ValueError("Non-public member needs a proven this receiver")
            if self.static and not static and receiver in (None,"this"):
                raise ValueError("Static methods cannot access an instance member implicitly")
            return f"#{index}"
        if receiver is None:
            try:
                return f"#{self.builder.type_name(text)}"
            except ValueError:
                if text not in CALLABLE_GLOBALS:
                    raise ValueError("Unresolved name in class body: "+text) from None
        return f"#{_qname(self.pool,self.builder.public_ns,text)}"


class _ClassEmitter(AVM2Emitter):
    """Class-aware static receivers without guessing a scope-chain lookup."""
    def _static_member(self,text):
        entries=self.bindings.members.get(text,[]) if self.bindings else []
        return entries[0] if entries and entries[0][1] and not self.bindings.static else None

    def _class_object(self):
        index=self.bindings.builder.abc.instances[self.bindings.class_index].name
        self.emit(f"getlex #{index}")

    def _cinit_self_reference(self, text):
        builder = self.bindings.builder
        owner = self.bindings.class_index
        context = self.closure_context
        if not self.bindings.static or context is None or context.method_index != builder.abc.classes[owner].cinit:
            return False
        root = text.split(".", 1)[0]
        if root in self.registers or root in self.aliases or self.captured(root) or root in self.bindings.members or root in builder.hidden_private[owner]:
            return False
        try:
            return builder.type_name(text) == builder.abc.instances[owner].name
        except ValueError:
            return False

    def expression(self, expr):
        if isinstance(expr, Construct):
            builder = self.bindings.builder
            qname = builder.type_name(expr.type_name)
            provider = next((i for i, instance in enumerate(builder.abc.instances) if instance.name == qname), None)
            if provider is None:
                constructible = ("flash.events.EventDispatcher", "flash.display.Sprite", "flash.display.MovieClip")
                error_contract = external_error_constructors().get(type_key(builder.abc.constant_pool, qname)) if expr.type_name in VERIFIED_ERROR_NAMES else None
                if error_contract is not None:
                    minimum, maximum = error_contract
                elif expr.type_name == "Object" or qname in [builder.type_names[name] for name in constructible if name in builder.type_names]:
                    minimum, maximum = 0, 1 if qname == builder.type_names.get("flash.events.EventDispatcher") else 0
                else:
                    raise ValueError("Construction needs a verified class provider: " + expr.type_name)
            else:
                if builder.abc.instances[provider].flags & 4:
                    raise ValueError("Cannot construct an interface")
                constructor = builder.abc.methods[builder.abc.instances[provider].iinit]
                minimum = len(constructor.param_types) - len(constructor.options)
                maximum = None if constructor.flags & 4 else len(constructor.param_types)
            count = len(expr.arguments)
            if count < minimum or maximum is not None and count > maximum:
                raise ValueError("Constructor argument count does not match its declaration")
            self.emit(f"findpropstrict #{qname}")
            for argument in expr.arguments:
                self.expression(argument)
            self.emit(f"constructprop #{qname} {count}")
        else:
            super().expression(expr)

    def load(self,text):
        if text=="this" and self.bindings.static:
            raise ValueError("this is not available in a static method")
        if self._cinit_self_reference(text):
            # newclass executes cinit before the script installs its global
            # class slot. The exact cinit receiver already is that class.
            self.emit("getlocal_0")
        elif text not in self.registers and not self.captured(text) and self._static_member(text):
            self._class_object()
            self.emit(f"getproperty {self.property_operand(text)}")
        else:
            super().load(text)

    def assign(self,target,value):
        self._writable(target)
        if isinstance(target, Member) and isinstance(target.obj, Name) and target.obj.name == "super":
            self.emit("getlocal_0")
            self.expression(value)
            self.emit(f"setsuper {self.bindings.super_operand(target.name)}")
            return
        if isinstance(target,Name) and target.name not in self.registers and not self.captured(target.name) and self._static_member(target.name):
            self._class_object()
            self.expression(value)
            self.emit(f"setproperty {self.property_operand(target.name)}")
        else:
            super().assign(target,value)

    def _writable(self,target):
        if isinstance(target,Name):
            name=self.aliases.get(target.name,target.name)
            if name in self.registers or self.captured(name):
                return
        elif isinstance(target,Member):
            name=target.name
        else:
            return
        if name in self.bindings.builder.constant_members[self.bindings.class_index]:
            raise ValueError("Cannot assign to constant member: "+name)

    def store_stack(self,name):
        self._writable(Name(name))
        if name not in self.registers and not self.captured(name) and self._static_member(name):
            self._class_object()
            self.emit("swap")
            self.emit(f"setproperty {self.property_operand(name)}")
        else:
            super().store_stack(name)

    def call(self,expr):
        if isinstance(expr.callee, Member) and isinstance(expr.callee.obj, Name) and expr.callee.obj.name == "super":
            self.emit("getlocal_0")
            for argument in expr.args:
                self.expression(argument)
            self.emit(f"callsuper {self.bindings.super_operand(expr.callee.name)} {len(expr.args)}")
            return
        if isinstance(expr.callee,Name) and expr.callee.name not in self.registers and not self.captured(expr.callee.name) and self._static_member(expr.callee.name):
            self._class_object()
            for argument in expr.args:
                self.expression(argument)
            self.emit(f"callproperty {self.property_operand(expr.callee.name)} {len(expr.args)}")
        else:
            super().call(expr)

    def member(self, expr):
        if isinstance(expr.obj, Name) and expr.obj.name == "super":
            self.emit("getlocal_0")
            self.emit(f"getsuper {self.bindings.super_operand(expr.name)}")
        else:
            super().member(expr)


class _Builder:
    def __init__(self,program):
        self.program=program
        self.abc=ABCFile()
        self.public_ns=_namespace(self.abc.constant_pool,0x16,"")
        self.type_names={name:_qname(self.abc.constant_pool,self.public_ns,name) for name in sorted(BUILTINS)}
        self.type_names["*"]=0
        self.member_names=[]
        self.constant_members=[]
        self.methods=[]
        self.class_names=[]
        self.active_class = None
        self.inherited_members = []
        self.hidden_private = []
        self.parent_indices = []
        self.method_declarations = []
        self.ancestor_names = []
        self.namespace_exports = {}
        for declaration in program.namespaces:
            kind, uri = _constant_expression(declaration.value_source)
            if kind != "string":
                raise ValueError("Namespace identity requires a literal URI string")
            full = ".".join(filter(None, (declaration.package, declaration.name)))
            if full in self.namespace_exports:
                raise ValueError("Duplicate namespace provider: " + full)
            self.namespace_exports[full] = (_namespace(self.abc.constant_pool, 8, uri), declaration)
        needed = set()
        for declaration in program.classes:
            for name in declaration.imports + ([declaration.super_name] if declaration.super_name else []):
                if name in DISPLAY_BASES:
                    while name:
                        needed.add(name)
                        name = DISPLAY_BASES.get(name)
        for name in DISPLAY_BASES:
            if name not in needed:
                continue
            package, _, local = name.rpartition(".")
            self.type_names[name] = _qname(self.abc.constant_pool, _namespace(self.abc.constant_pool, 0x16, package), local)

    def type_name(self,name):
        if self.active_class is not None and name not in BUILTINS and name != "*":
            declaration = self.program.classes[self.active_class]
            local = ".".join(filter(None, (declaration.package, name)))
            imports = [item for item in declaration.imports if item.rsplit(".", 1)[-1] == name]
            candidates = ([local] if local in self.type_names else []) + imports
            if len(set(candidates)) > 1:
                raise ValueError("Ambiguous imported/local type: " + name)
            if candidates:
                name = candidates[0]
        if name in self.type_names:
            return self.type_names[name]
        raise ValueError("Type is not declared or supported in this class program: "+name)

    def namespace_name(self, name):
        candidates = [name] if name in self.namespace_exports else []
        if self.active_class is not None:
            declaration = self.program.classes[self.active_class]
            local = ".".join(filter(None, (declaration.package, name)))
            if local in self.namespace_exports:
                candidates.append(local)
            candidates.extend(item for item in declaration.imports if item.rsplit(".", 1)[-1] == name and item in self.namespace_exports)
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) != 1:
            raise ValueError("Namespace has no unique verified provider: " + name)
        return self.namespace_exports[candidates[0]][0]

    def metadata(self,items):
        result=[]
        pool=self.abc.constant_pool
        for item in items:
            metadata=MetadataInfo()
            metadata.name=search_or_add_string(pool,item.name)
            for key,raw in item.items:
                kind,value=_constant_expression(raw)
                if kind!="string":
                    raise ValueError("Metadata values must be strings in this tier")
                metadata.items.append((search_or_add_string(pool,key),search_or_add_string(pool,value)))
            self.abc.metadata.append(metadata)
            result.append(len(self.abc.metadata)-1)
        return result

    def same_signature(self, left, right):
        a, b = self.abc.methods[left], self.abc.methods[right]
        return (a.param_types, a.return_type, a.flags & 4, a.options) == (b.param_types, b.return_type, b.flags & 4, b.options)

    def check_interfaces(self, class_index):
        visited = set()
        def check(index):
            if index in visited:
                return
            visited.add(index)
            interface = self.abc.instances[index]
            for name, signature in self.method_declarations[index].items():
                implementation = self.method_declarations[class_index].get(name)
                if implementation is None or implementation[1].visibility != "public" or not self.same_signature(signature[0], implementation[0]):
                    raise ValueError("Interface method is missing or has a different public signature: " + name[0])
            for qname in interface.interfaces:
                check(next(i for i, inst in enumerate(self.abc.instances) if inst.name == qname))
        for qname in self.abc.instances[class_index].interfaces:
            check(next(i for i, inst in enumerate(self.abc.instances) if inst.name == qname))

    def method_info(self,member,constructor=False):
        info=MethodInfo()
        info.name=search_or_add_string(self.abc.constant_pool,member.name)
        info.return_type=0 if constructor else self.type_name(member.return_type)
        fixed=[p for p in member.parameters if not p.rest]
        if any(p.type_name=="void" for p in member.parameters):
            raise ValueError("Parameters cannot have type void")
        if any(p.rest and p.type_name not in ("*","Array") for p in member.parameters):
            raise ValueError("Rest parameters must be untyped or Array")
        info.param_types=[self.type_name(p.type_name) for p in fixed]
        info.param_names=[search_or_add_string(self.abc.constant_pool,p.name) for p in fixed]
        info.flags=0x80 if fixed else 0
        defaults=[p for p in fixed if p.default_source is not None]
        if defaults:
            info.flags|=8
            for parameter in defaults:
                value,kind=_constant(self.abc.constant_pool,parameter.default_source,parameter.type_name)
                info.options.append({"val":value,"kind":kind})
        if any(p.rest for p in member.parameters):
            info.flags|=4
        self.abc.methods.append(info)
        return len(self.abc.methods)-1

    def body(self,index,source,bindings,parameters=(),constructor=False,return_type="void",scope_depth=2):
        parser=ClassBodyParser(tokenize(source),"as3",bindings)
        statements=parser.parse_program()
        _validate_error_constructor_bindings(statements, parameters, bindings)
        if constructor:
            def super_calls(node):
                if isinstance(node, Call) and isinstance(node.callee, Name) and node.callee.name == "super":
                    yield node
                if is_dataclass(node):
                    for descriptor in fields(node):
                        yield from super_calls(getattr(node, descriptor.name))
                elif isinstance(node, (list, tuple)):
                    for child in node:
                        yield from super_calls(child)
            calls=list(super_calls(statements))
            first=statements[0] if statements else None
            first_super=isinstance(first,Expr) and isinstance(first.value,Call) and isinstance(first.value.callee,Name) and first.value.callee.name=="super"
            if calls and (len(calls)!=1 or not first_super):
                raise ValueError("super(...) must be the first constructor statement, exactly once")
            if not calls:
                statements=Parser(tokenize("super();"),"as3",bindings).parse_program()+statements
            first=statements[0]
            parent = self.parent_indices[bindings.class_index]
            if parent is None:
                minimum, maximum = 0, 0
            else:
                base_ctor = self.abc.methods[self.abc.instances[parent].iinit]
                minimum = len(base_ctor.param_types) - len(base_ctor.options)
                maximum = None if base_ctor.flags & 4 else len(base_ctor.param_types)
            count = len(first.value.args)
            if count < minimum or maximum is not None and count > maximum:
                raise ValueError("super(...) argument count does not match the verified base constructor")
        registers={p.name:i for i,p in enumerate(parameters,1)}
        bindings.local_types = {p.name:self.type_name(p.type_name) for p in parameters if p.type_name not in ("*", "int", "uint", "Number", "String", "Boolean", "Object")}
        types={p.name:p.type_name if p.type_name in ("*", "int", "uint", "Number", "String", "Boolean", "Object") else f"#{self.type_name(p.type_name)}" for p in parameters}
        # All reference coercions are emitted with their exact QName index.
        ret=return_type if return_type in ("*","void","int","uint","Number","String","Boolean") else f"#{self.type_name(return_type)}"
        emitter=_ClassEmitter(registers,len(registers)+1,"void" if constructor else ret,constructor,types,bindings,parser.runtime_multiname_index)
        emitter.enable_closures(self.abc,index,scope_depth)
        assembly=emitter.compile(statements)
        body=MethodBodyInfo()
        body.method=index
        body.local_count=len(registers)+1
        body.init_scope_depth=scope_depth
        exceptions=[]
        body.code=assemble_instructions(self.abc.constant_pool,assembly,exception_table=exceptions,activation_traits=body.traits)
        body.exceptions=exceptions
        if len(body.code)>512000 or len(decode_avm2(body.code))>50000:
            raise ValueError("Class method exceeds the bounded compilation budget")
        limits=analyze_method(self.abc,body)
        body.max_stack,body.local_count,body.max_scope_depth=limits.max_stack,limits.local_count,limits.max_scope_depth
        self.abc.method_bodies.append(body)

    def build(self):
        pool=self.abc.constant_pool
        if not (self.program.classes or self.program.namespaces) or len(self.program.classes)>128:
            raise ValueError("Class program must contain 1–128 classes")
        for declaration in self.program.classes:
            if declaration.name in BUILTINS:
                raise ValueError("Builtin-shadowing class declarations need explicit semantic linking")
            if "final" in declaration.modifiers and "dynamic" in declaration.modifiers:
                raise ValueError("A class cannot be both final and dynamic")
            ns=_namespace(pool,0x16 if declaration.visibility=="public" else 0x17,declaration.package)
            name=_qname(pool,ns,declaration.name)
            full=".".join(filter(None,(declaration.package,declaration.name)))
            self.type_names[full]=name
            self.class_names.append(full)
            instance=InstanceInfo()
            instance.name,instance.super_name=name,self.type_name("Object")
            instance.flags=(0 if "dynamic" in declaration.modifiers else 1)|(2 if "final" in declaration.modifiers else 0)
            self.abc.instances.append(instance)
            self.abc.classes.append(ClassInfo())
            self.member_names.append({})
            self.constant_members.append(set())
            self.inherited_members.append({})
            self.hidden_private.append(set())
            self.parent_indices.append(None)
            self.method_declarations.append({})
            self.ancestor_names.append([])
        for ci,declaration in enumerate(self.program.classes):
            self.active_class = ci
            is_interface = "interface" in declaration.modifiers
            if is_interface and any(modifier in ("final", "dynamic") for modifier in declaration.modifiers):
                raise ValueError("Interfaces cannot be final or dynamic")
            for imported in declaration.imports:
                if imported not in self.type_names and imported not in self.namespace_exports:
                    raise ValueError("Import has no verified provider in this program: "+imported)
            instance,cls=self.abc.instances[ci],self.abc.classes[ci]
            instance.super_name = 0 if is_interface else self.type_name(declaration.super_name or "Object")
            if is_interface:
                instance.flags = 5
            base = next((index for index, item in enumerate(self.abc.instances) if item.name == instance.super_name), None)
            if base is not None:
                if base >= ci or self.abc.instances[base].flags & 4:
                    raise ValueError("Invalid or cyclic superclass dependency")
                if self.abc.instances[base].flags & 2:
                    raise ValueError("Cannot extend a final class")
                self.parent_indices[ci] = base
                inherited = {name: [entry for entry in entries if not entry[1] and entry[2] != "private" and (entry[2] != "internal" or self.program.classes[base].package == declaration.package)] for name, entries in self.member_names[base].items()}
                inherited = {name: entries for name, entries in inherited.items() if entries}
                self.inherited_members[ci] = inherited
                self.member_names[ci].update(inherited)
                self.hidden_private[ci] = self.hidden_private[base] | {name for name, entries in self.member_names[base].items() if any(entry[2] == "private" for entry in entries)}
                self.constant_members[ci].update(name for name in self.constant_members[base] if name in inherited)
                self.method_declarations[ci].update({key: value for key, value in self.method_declarations[base].items() if not key[2] and value[1].visibility != "private" and key[0] in inherited})
                self.ancestor_names[ci] = self.ancestor_names[base] + [self.class_names[base]]
            elif not is_interface:
                full_base = next((name for name, index in self.type_names.items() if index == instance.super_name), None)
                if full_base not in ("Object", "flash.events.EventDispatcher", "flash.display.Sprite", "flash.display.MovieClip"):
                    raise ValueError("Superclass has no verified hierarchy provider")
                chain = []
                while full_base:
                    chain.append(full_base)
                    full_base = DISPLAY_BASES.get(full_base)
                self.ancestor_names[ci] = list(reversed(chain))
            instance.interfaces = [self.type_name(name) for name in declaration.interfaces]
            for interface in instance.interfaces:
                provider = next((index for index, item in enumerate(self.abc.instances) if item.name == interface), None)
                if provider is None or provider >= ci or not self.abc.instances[provider].flags & 4:
                    raise ValueError("Implemented/extended interface has no verified declaration")
            private=_namespace(pool,5,self.class_names[ci]+"$private",unique=True)
            internal=_namespace(pool,0x17,declaration.package)
            protected=_namespace(pool,0x18,self.class_names[ci])
            interface_ns = _namespace(pool, 8, (declaration.package + ":" if declaration.package else "") + declaration.name) if is_interface else None
            namespaces={"public":self.public_ns,"private":private,"internal":internal,"protected":protected}
            field_slots={False:0,True:0}
            constructor=None
            initializers=[]
            accessors={}
            for member in declaration.members:
                if isinstance(member, InitializerDecl):
                    if is_interface:
                        raise ValueError("Interfaces cannot contain initializer code")
                    initializers.append(member.body_source)
                    continue
                if isinstance(member,MethodDecl) and member.name==declaration.name and member.accessor is None:
                    if constructor is not None or any(m!="public" for m in member.modifiers) or member.return_type!="*":
                        raise ValueError("Invalid or duplicate constructor")
                    constructor=member
                    continue
                if is_interface and (not isinstance(member, MethodDecl) or member.body_source is not None or any(modifier != "public" for modifier in member.modifiers)):
                    raise ValueError("Interfaces require public instance method declarations without bodies")
                static="static" in member.modifiers
                traits=cls.traits if static else instance.traits
                trait=TraitsInfo()
                explicit = [modifier for modifier in member.modifiers if modifier not in ("public", "private", "protected", "internal", "static", "override", "final", "dynamic")]
                if len(explicit) > 1:
                    raise ValueError("Member has multiple explicit namespaces")
                member_namespace = self.namespace_name(explicit[0]) if explicit else namespaces[member.visibility]
                trait.name=_qname(pool,interface_ns if is_interface else member_namespace,member.name)
                trait.metadata=self.metadata(member.metadata)
                if isinstance(member,FieldDecl):
                    if member.name in self.inherited_members[ci]:
                        raise ValueError("Inherited fields cannot be redeclared: " + member.name)
                    if any(m in ("final","dynamic") for m in member.modifiers):
                        raise ValueError("Field modifiers are not valid")
                    field_slots[static]+=1
                    trait.kind_flags=6 if member.constant else 0
                    trait.slot_id=field_slots[static] if static or self.parent_indices[ci] is None and declaration.super_name in (None, "Object") else 0
                    if member.type_name=="void":
                        raise ValueError("A field cannot have type void")
                    trait.type_name=self.type_name(member.type_name)
                    if member.value_source is not None:
                        trait.vindex,trait.vkind=_constant(pool,member.value_source,member.type_name)
                    if member.constant and trait.vindex==0:
                        raise ValueError("Constants require an explicit representable initializer")
                    if member.constant:
                        self.constant_members[ci].add(member.name)
                else:
                    if "dynamic" in member.modifiers:
                        raise ValueError("Methods cannot be dynamic")
                    if member.body_source is None and not is_interface:
                        raise ValueError("Class methods need explicit bodies; no empty replacement is generated")
                    if member.accessor=="get" and (member.parameters or member.return_type=="void"):
                        raise ValueError("Getter signature is invalid")
                    if member.accessor=="set" and (len(member.parameters)!=1 or member.return_type!="void"):
                        raise ValueError("Setter signature is invalid")
                    if member.accessor=="set" and (member.parameters[0].rest or member.parameters[0].default_source is not None):
                        raise ValueError("Setters require one fixed parameter without a default")
                    if member.accessor:
                        pair=accessors.setdefault(member.name,{})
                        peer=pair.get("set" if member.accessor=="get" else "get")
                        if peer:
                            getter=member if member.accessor=="get" else peer
                            setter=peer if member.accessor=="get" else member
                            if getter.visibility!=setter.visibility or ("static" in getter.modifiers)!=("static" in setter.modifiers) or getter.return_type!=setter.parameters[0].type_name:
                                raise ValueError("Getter/setter namespace, receiver and type must match")
                        pair[member.accessor]=member
                    trait.kind_flags={None:1,"get":2,"set":3}[member.accessor]
                    if "final" in member.modifiers:
                        trait.kind_flags|=0x10
                    trait.method=self.method_info(member)
                    key = (member.name, member.accessor, static)
                    inherited_method = self.method_declarations[ci].get(key)
                    if not is_interface and inherited_method is not None and member.visibility != "private":
                        if "override" not in member.modifiers:
                            raise ValueError("Inherited method requires override: " + member.name)
                        previous_info, previous_member = inherited_method
                        if "final" in previous_member.modifiers or previous_member.visibility != member.visibility or not self.same_signature(previous_info, trait.method):
                            raise ValueError("Override signature/visibility does not match the base declaration: " + member.name)
                        trait.kind_flags |= 0x20
                    elif "override" in member.modifiers:
                        raise ValueError("override has no inherited method: " + member.name)
                    self.method_declarations[ci][key] = (trait.method, member)
                    if not is_interface:
                        self.methods.append((ci,member,trait.method,static))
                if trait.metadata:
                    trait.kind_flags|=0x40
                traits.append(trait)
                if member.name in self.inherited_members[ci]:
                    self.member_names[ci][member.name] = []
                self.member_names[ci].setdefault(member.name,[]).append((trait.name,static,"public" if is_interface else member.visibility))
                if member.visibility=="protected":
                    instance.flags|=8
                    instance.protectedNs=protected
            ctor=constructor or MethodDecl(name=declaration.name,visibility="public",body_source="super();")
            if ctor.body_source is None:
                raise ValueError("Constructor body is missing")
            instance.iinit=self.method_info(ctor,True)
            if not is_interface:
                self.methods.append((ci,ctor,instance.iinit,False))
            init=MethodDecl(name="$cinit",return_type="void",body_source="\n".join(initializers))
            cls.cinit=self.method_info(init)
            self.methods.append((ci,init,cls.cinit,True))
        for ci,member,index,static in self.methods:
            self.active_class = ci
            constructor=index==self.abc.instances[ci].iinit
            # AVM2 binds instance methods/iinit with the class object appended
            # to the declaration scope; static methods/cinit use class_scope.
            self.body(index,member.body_source or "",_ClassBindings(self,ci,static),member.parameters,constructor,member.return_type,scope_depth=1+len(self.ancestor_names[ci])+(0 if static else 1))
        for ci,declaration in enumerate(self.program.classes):
            if "interface" not in declaration.modifiers:
                self.check_interfaces(ci)
        script=ScriptInfo()
        script.init=self.method_info(MethodDecl(name="$script",return_type="void"))
        lines=["getlocal_0","pushscope"]
        for ns_index, declaration in self.namespace_exports.values():
            trait = TraitsInfo()
            trait.kind_flags, trait.name = 6, _qname(pool, _namespace(pool, 0x16 if declaration.visibility == "public" else 0x17, declaration.package), declaration.name)
            trait.slot_id, trait.type_name = len(script.traits) + 1, self.type_names["Namespace"]
            trait.vkind, trait.vindex = 8, ns_index
            script.traits.append(trait)
            lines.extend(("getscopeobject 0", f"pushnamespace {ns_index}", f"initproperty #{trait.name}"))
        for ci,declaration in enumerate(self.program.classes):
            trait=TraitsInfo()
            trait.kind_flags,trait.name,trait.slot_id,trait.class_idx=4,self.abc.instances[ci].name,len(script.traits)+1,ci
            trait.metadata=self.metadata(declaration.metadata)
            if trait.metadata:
                trait.kind_flags|=0x40
            script.traits.append(trait)
            lines.append("getscopeobject 0")
            if self.ancestor_names[ci] == ["Object"]:
                lines.extend((f"getlex #{self.type_names['Object']}", "dup", "pushscope"))
            else:
                for ancestor in self.ancestor_names[ci]:
                    lines.extend((f"getlex #{self.type_names[ancestor]}", "pushscope"))
                lines.append(f"getlex #{self.abc.instances[ci].super_name}" if self.abc.instances[ci].super_name else "pushnull")
            lines.append(f"newclass {ci}")
            lines.extend("popscope" for _ in self.ancestor_names[ci])
            lines.append(f"initproperty #{trait.name}")
        lines.append("returnvoid")
        self.abc.scripts.append(script)
        body=MethodBodyInfo()
        body.method,body.local_count=script.init,1
        body.code=assemble_instructions(pool,"\n".join(lines))
        limits=analyze_method(self.abc,body)
        body.max_stack,body.max_scope_depth=limits.max_stack,limits.max_scope_depth
        self.abc.method_bodies.append(body)
        # Format-boundary validation is required before bytes leave this module.
        parsed=ABCFile()
        parsed.parse(self.abc.serialize())
        for body in parsed.method_bodies:
            analyze_method(parsed,body)
        return self.abc


def class_abi(program):
    """Public and private declarations/order; body/default edits are separate."""
    value=asdict(program)
    for declaration, cls in zip(program.classes, value["classes"]):
        cls["members"] = [member for original, member in zip(declaration.members, cls["members"]) if not isinstance(original, InitializerDecl)]
        for member in cls["members"]:
            member.pop("body_source",None)
            member.pop("value_source",None)
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _linked_program(source, sources):
    primary = parse_class_program(source)
    declarations = list(primary.classes)
    namespace_declarations = list(primary.namespaces)
    for filename, text in sorted((sources or {}).items()):
        if not isinstance(filename, str) or not isinstance(text, str):
            raise ValueError("Additional sources must map filenames to source strings")
        part = parse_class_program(text)
        declarations.extend(part.classes)
        namespace_declarations.extend(part.namespaces)
    by_name = {}
    for declaration in declarations:
        full = ".".join(filter(None, (declaration.package, declaration.name)))
        if full in by_name:
            raise ValueError("Duplicate class provider: " + full)
        by_name[full] = declaration
    def provider(name, declaration):
        if name in by_name:
            return name
        local = ".".join(filter(None, (declaration.package, name)))
        imported = [item for item in declaration.imports if item.rsplit(".", 1)[-1] == name and item in by_name]
        candidates = ([local] if local in by_name else []) + imported
        if len(set(candidates)) > 1:
            raise ValueError("Ambiguous dependency: " + name)
        return candidates[0] if candidates else None
    ordered, visiting, done = [], set(), set()
    def visit(name):
        if name in visiting:
            raise ValueError("Cyclic class/interface dependency: " + name)
        if name in done:
            return
        visiting.add(name)
        declaration = by_name[name]
        for dependency in ([declaration.super_name] if declaration.super_name else []) + declaration.interfaces:
            target = provider(dependency, declaration)
            if target:
                visit(target)
        visiting.remove(name)
        done.add(name)
        ordered.append(declaration)
    for name in by_name:
        visit(name)
    unique_namespaces = {}
    for declaration in namespace_declarations:
        full = ".".join(filter(None, (declaration.package, declaration.name)))
        previous = unique_namespaces.get(full)
        if previous is not None and previous != declaration:
            raise ValueError("Ambiguous namespace provider: " + full)
        unique_namespaces[full] = declaration
    return Program(primary.package, primary.imports, ordered, list(unique_namespaces.values()))


def compile_class_program(source, *, sources=None):
    source.encode("utf-8")
    if len(source)>1_000_000:
        raise ValueError("Class source exceeds the limit")
    if sources is not None and not isinstance(sources, dict):
        raise ValueError("sources must map filenames to ActionScript source text")
    if sum(len(text) for text in (sources or {}).values() if isinstance(text, str)) + len(source) > 1_000_000:
        raise ValueError("Class sources exceed the limit")
    program=_linked_program(source, sources)
    builder=_Builder(program)
    abc=builder.build()
    return CompiledClassProgram(abc,source,builder.class_names,class_abi(program),[
        "Resolved native classes/interfaces and a bounded display hierarchy; no complete AS3 type verifier or runtime parity claim"
    ], dict(sources or {}))


def class_program_swf(compiled,signature="CWS", *, document_class=None, width=550, height=400, frame_rate=24):
    if signature not in ("FWS","CWS","ZWS"):
        raise ValueError("Unsupported SWF compression")
    movie=SWFFile()
    movie.signature,movie.version=signature,15
    if not (1 <= width <= 32767 and 1 <= height <= 32767 and 0 < frame_rate < 256):
        raise ValueError("Invalid document stage dimensions/frame rate")
    movie.rect = {"xmin": 0, "xmax": int(width * 20), "ymin": 0, "ymax": int(height * 20)}
    movie.frame_rate = frame_rate
    movie.tags=[SWFTag(69,b"\x08\0\0\0"),SWFTag(82,b"\0\0\0\0native-classes\0"+compiled.abc_bytes())]
    if document_class is not None:
        if document_class not in compiled.class_names and "." not in document_class:
            candidates = [name for name in compiled.class_names if name.rsplit(".", 1)[-1] == document_class]
            if len(candidates) == 1:
                document_class = candidates[0]
        if document_class not in compiled.class_names:
            raise ValueError("Document class must identify a compiled class")
        from ..avm2 import resolve_multiname
        index = compiled.class_names.index(document_class)
        document_instance = compiled.abc.instances[index]
        definition = compiled.abc.constant_pool.multinames[document_instance.name]
        if compiled.abc.constant_pool.namespaces[definition["ns"]]["kind"] != 0x16:
            raise ValueError("Document class must be public")
        constructor = compiled.abc.methods[document_instance.iinit]
        if len(constructor.param_types) > len(constructor.options):
            raise ValueError("Document class constructor must accept zero arguments")
        seen = set()
        while index is not None:
            if index in seen:
                raise ValueError("Cyclic document-class hierarchy")
            seen.add(index)
            instance = compiled.abc.instances[index]
            if instance.flags & 4:
                raise ValueError("An interface cannot be a document class")
            base = resolve_multiname(compiled.abc.constant_pool, instance.super_name).replace("::", ".")
            if base in ("flash.display.Sprite", "flash.display.MovieClip"):
                break
            index = next((i for i, item in enumerate(compiled.class_names) if item == base), None)
        else:
            raise ValueError("Document class must derive from Sprite or MovieClip")
        movie.tags.append(SWFTag(76, b"\x01\0\0\0" + document_class.encode("utf-8") + b"\0"))
    movie.tags.append(SWFTag(1,b""))
    return movie.save_bytes()
