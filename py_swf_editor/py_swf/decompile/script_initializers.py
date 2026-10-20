"""Prove declaration-only script bootstraps before emitting class source.

This deliberately recognizes a stack program, not merely the presence of
newclass. Extra calls, writes, branches, mismatched bases, duplicate installs,
and unresolved captured scopes retain the original P-code.
"""
from ..abc_symbols import AbcSymbolIndex
from ..symbols import name_identity
from .ir import ReconstructionError
from .lift import avm2_instructions


def script_declaration_owner(abc, body):
    """Locate a script unit needing a declaration document, without text sniffing."""
    owners = [index for index, script in enumerate(abc.scripts) if script.init == body.method]
    if len(owners) != 1:
        return None
    try:
        instructions = avm2_instructions(body.code)
    except ValueError:
        return None
    return owners[0] if any(ins.op in ("newclass", "pushnamespace") for ins in instructions) else None


def declaration_order(abc, body):
    owners = [script for script in abc.scripts if script.init == body.method]
    if len(owners) != 1 or body.exceptions or body.traits or body.init_scope_depth:
        raise ReconstructionError("Script has no unique declaration-only initialization context")
    traits = owners[0].traits
    namespace_traits = [trait for trait in traits if trait.kind_flags & 15 in (0, 6) and trait.vkind == 8]
    if any(trait.kind_flags & 15 != 4 and trait not in namespace_traits for trait in traits):
        raise ReconstructionError("Script contains package declarations beyond classes")
    expected = {trait.class_idx: trait for trait in traits if trait.kind_flags & 15 == 4}
    if len(expected) + len(namespace_traits) != len(traits):
        raise ReconstructionError("Duplicate class installation traits")
    stack, scopes, installed, installed_namespaces = [], [], [], set()
    index = AbcSymbolIndex([abc])
    def pop():
        if not stack:
            raise ReconstructionError("Class bootstrap expression stack underflow")
        return stack.pop()
    def identity(operand):
        return name_identity(abc.constant_pool, operand)
    instructions = avm2_instructions(body.code)
    for position, ins in enumerate(instructions):
        op, args = ins.op, ins.args
        if op in ("debug", "debugline", "debugfile", "nop", "label"):
            continue
        if op in ("getlocal_0",) or op == "getlocal" and args == (0,):
            stack.append(("global",))
        elif op == "getscopeobject":
            if not 0 <= args[0] < len(scopes):
                raise ReconstructionError("Class bootstrap scope index is unresolved")
            stack.append(scopes[args[0]])
        elif op in ("findpropstrict", "findproperty"):
            if identity(args[0]) not in [identity(t.name) for t in traits]:
                raise ReconstructionError("Class bootstrap lookup is not a declared class")
            stack.append(("global",))
        elif op == "getlex":
            stack.append(("type", identity(args[0])))
        elif op == "pushnull":
            stack.append(("null",))
        elif op == "pushnamespace":
            namespace = abc.constant_pool.namespaces[args[0]]
            if namespace["kind"] != 8:
                raise ReconstructionError("Bootstrap namespace is not an explicit URI identity")
            stack.append(("namespace", namespace["kind"], abc.constant_pool.strings[namespace["name"]]))
        elif op == "dup":
            if not stack:
                raise ReconstructionError("Class bootstrap dup stack underflow")
            stack.append(stack[-1])
        elif op == "pushscope":
            value = pop()
            if value[0] not in ("global", "type"):
                raise ReconstructionError("Class bootstrap scope is not a declared hierarchy")
            scopes.append(value)
        elif op == "popscope":
            if len(scopes) < 2:
                raise ReconstructionError("Class bootstrap scope stack underflow")
            scopes.pop()
        elif op == "newclass":
            ci = args[0]
            if ci not in expected or not 0 <= ci < min(len(abc.instances), len(abc.classes)):
                raise ReconstructionError("newclass has no matching script trait")
            instance = abc.instances[ci]
            base = pop()
            required = ("type", identity(instance.super_name)) if instance.super_name else ("null",)
            if base != required:
                raise ReconstructionError("newclass superclass value differs from its declaration")
            if not scopes or scopes[0] != ("global",):
                raise ReconstructionError("Class bootstrap lost the script global scope")
            from .hierarchy_contract import captured_chain
            expected_chain = captured_chain(abc, index, ci)
            actual_chain = []
            for scope in scopes[1:]:
                if scope[0] != "type" or len(scope[1].namespaces) != 1:
                    raise ReconstructionError("Class capture has an unproven scope identity")
                namespace = scope[1].namespaces[0]
                if namespace.kind not in (8, 0x16, 0x17):
                    raise ReconstructionError("Class capture has a private/protected type identity")
                actual_chain.append((0x16 if namespace.kind in (8, 0x16) else namespace.kind, namespace.uri, scope[1].local))
            if tuple(actual_chain) != expected_chain:
                raise ReconstructionError("Class captured scope chain differs in identity, order or multiplicity")
            stack.append(("class", ci))
        elif op == "initproperty":
            value, receiver = pop(), pop()
            if value[0] == "namespace" and receiver == ("global",):
                matches = [trait for trait in namespace_traits if identity(trait.name) == identity(args[0])]
                if len(matches) != 1 or args[0] in installed_namespaces:
                    raise ReconstructionError("Namespace installation identity is mismatched or duplicated")
                namespace = abc.constant_pool.namespaces[matches[0].vindex]
                if value != ("namespace", namespace["kind"], abc.constant_pool.strings[namespace["name"]]):
                    raise ReconstructionError("Namespace installation URI identity differs from its trait")
                installed_namespaces.add(args[0])
                continue
            if value[0] != "class" or receiver != ("global",):
                raise ReconstructionError("Class installation has an unproven receiver/value")
            ci = value[1]
            if ci in installed or identity(args[0]) != identity(expected[ci].name) or identity(args[0]) != identity(abc.instances[ci].name):
                raise ReconstructionError("Class installation identity is mismatched or duplicated")
            installed.append(ci)
        elif op == "return" and args == (False,):
            if position != len(instructions) - 1 or stack or scopes != [("global",)] or set(installed) != set(expected) or len(installed_namespaces) != len(namespace_traits):
                raise ReconstructionError("Class bootstrap has extra stack/scopes or uninstalled classes")
            return tuple(installed)
        else:
            raise ReconstructionError(f"Script contains executable operation {op} beyond class declarations")
    raise ReconstructionError("Class bootstrap has no terminal returnvoid")
