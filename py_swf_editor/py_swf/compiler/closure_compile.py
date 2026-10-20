"""Lexical AVM2 closures with explicit activation cells and captured scope.

The assembler receives real body traits; existing opaque activations are never
reconstructed or made editable by this module.
"""
from copy import copy
from dataclasses import dataclass,fields,is_dataclass

from ..avm2 import MethodInfo,MethodBodyInfo,TraitsInfo,assemble_instructions,search_or_add_multiname,search_or_add_string
from ..bytecode_validation import analyze_method
from .as_compiler import FunctionDeclaration,FunctionExpression,Name,Var


def scope_nodes(node):
    if isinstance(node,(list,tuple)):
        for child in node:yield from scope_nodes(child)
    elif is_dataclass(node):
        yield node
        if isinstance(node,(FunctionExpression,FunctionDeclaration)):
            return
        for field in fields(node):yield from scope_nodes(getattr(node,field.name))


def declared_names(body):
    return {node.name for node in scope_nodes(body) if isinstance(node,(Var,FunctionDeclaration))}


def free_names(expression):
    bound=declared_names(expression.body)|{parameter.name for parameter in expression.parameters}
    names=set()
    for node in scope_nodes(expression.body):
        if isinstance(node,Name):names.add(node.name)
        elif isinstance(node,FunctionExpression):names.update(free_names(node))
        elif isinstance(node,FunctionDeclaration):names.update(free_names(node.function))
    return names-bound


@dataclass(frozen=True)
class Capture:
    scope_index: int|None
    slot: int
    type_name: str


class ClosureCompilation:
    def __init__(self,emitter,abc,method_index,scope_depth,*,inherited=None,budget=None,depth=0):
        if not 0<=method_index<len(abc.methods):raise ValueError('Closure context requires a valid ABC method')
        self.emitter,self.abc,self.method_index,self.scope_depth=emitter,abc,method_index,scope_depth
        self.initial_registers=dict(emitter.registers)
        self.inherited=dict(inherited or {})
        self.references={}
        self.activation_traits=[]
        self.activation_register=None
        self.hoisted=set()
        self.budget=budget if budget is not None else {'methods':0}
        self.depth=depth

    def type_index(self,name):
        if name in (None,'*'):return 0
        if name.startswith('#'):
            index=int(name[1:])
            if not 0<=index<len(self.abc.constant_pool.multinames):raise ValueError('Invalid captured type reference')
            return index
        builder=getattr(self.emitter.bindings,'builder',None)
        if builder is not None:return builder.type_name(name)
        if name not in ('void','int','uint','Number','String','Boolean','Object','Function','Array','Error'):
            raise ValueError('Closure type needs a verified class provider: '+name)
        return search_or_add_multiname(self.abc.constant_pool,name)

    def prepare(self,statements):
        e=self.emitter
        owned=declared_names(statements)|set(self.initial_registers)
        self.references={name:entry for name,entry in self.inherited.items() if name not in owned}
        functions=[]
        for node in scope_nodes(statements):
            if isinstance(node,FunctionExpression):functions.append(node)
            elif isinstance(node,FunctionDeclaration):functions.append(node.function)
        captures=set().union(*(free_names(function) for function in functions)) & owned if functions else set()
        # Parameter aliases (e.g. score/local1) share the same physical cell.
        for name in tuple(captures):
            if name in self.initial_registers:
                captures.update(alias for alias,index in self.initial_registers.items() if index==self.initial_registers[name])
        pool=self.abc.constant_pool
        groups={}
        for name in sorted(captures):
            identity=('register',self.initial_registers[name]) if name in self.initial_registers else ('name',name)
            groups.setdefault(identity,[]).append(name)
        if groups:
            uri=search_or_add_string(pool,f'$pyswf_activation_{self.method_index}')
            pool.namespaces.append({'kind':5,'name':uri});namespace=len(pool.namespaces)-1
            for slot,names in enumerate(groups.values(),1):
                name=names[0]
                type_name=next((e.local_types.get(alias) for alias in names if e.local_types.get(alias)), '*') or '*'
                pool.multinames.append({'kind':7,'ns':namespace,'name':search_or_add_string(pool,name)})
                trait=TraitsInfo();trait.name=len(pool.multinames)-1;trait.kind_flags=0;trait.slot_id=slot;trait.type_name=self.type_index(type_name)
                self.activation_traits.append(trait)
                for alias in names:self.references[alias]=Capture(None,slot,type_name)
            self.abc.methods[self.method_index].flags|=2
            register_name=e.temporary();self.activation_register=e.registers[register_name]
            e.emit('newactivation');e.emit('dup');e.emit(f'setlocal {self.activation_register}');e.emit('pushscope')
            copied=set()
            for name,index in self.initial_registers.items():
                reference=self.references.get(name)
                if reference and reference.scope_index is None and reference.slot not in copied:
                    e.emit(f'getlocal {index}');self.store(name);copied.add(reference.slot)
        for name,reference in self.references.items():
            e.local_types[name]=reference.type_name
        for statement in statements:
            if isinstance(statement,FunctionDeclaration):
                self.function(statement.function)
                e.store_stack(statement.name)
                self.hoisted.add(id(statement))

    def receiver(self,reference):
        self.emitter.emit(f'getlocal {self.activation_register}' if reference.scope_index is None else f'getouterscope {reference.scope_index}')

    def load(self,name):
        reference=self.references[name];self.receiver(reference);self.emitter.emit(f'getslot {reference.slot}')

    def store(self,name):
        reference=self.references[name]
        self.emitter.coerce_local(name);self.receiver(reference);self.emitter.emit('swap');self.emitter.emit(f'setslot {reference.slot}')

    def restore_scope(self):
        if self.activation_register is not None:
            self.emitter.emit(f'getlocal {self.activation_register}');self.emitter.emit('pushscope')

    def declaration(self,statement):
        if id(statement) not in self.hoisted:
            raise ValueError('Function declarations inside control-flow blocks need semantic linking; use an anonymous expression')

    def function(self,expression):
        e=self.emitter
        if self.depth>=32 or self.budget['methods']>=256:raise ValueError('Nested function compilation budget exceeded')
        if e.aliases:raise ValueError('Functions created in catch scope require distinct captured catch bindings')
        if any(isinstance(node,Name) and node.name in ('this','super','arguments') for node in scope_nodes(expression.body)):
            raise ValueError('Closure this/super/arguments bindings require an explicit function receiver tier')
        self.budget['methods']+=1
        scope=self.scope_depth+1+(self.activation_register is not None)
        references={name:Capture(self.scope_depth+1 if entry.scope_index is None else entry.scope_index,entry.slot,entry.type_name) for name,entry in self.references.items()}
        info=MethodInfo();info.name=search_or_add_string(self.abc.constant_pool,'$closure')
        info.param_types=[self.type_index(parameter.type_name) for parameter in expression.parameters]
        if any(parameter.type_name=='void' for parameter in expression.parameters):raise ValueError('Function parameters cannot have type void')
        info.param_names=[search_or_add_string(self.abc.constant_pool,parameter.name) for parameter in expression.parameters]
        info.flags=0x80 if info.param_names else 0;info.return_type=self.type_index(expression.return_type)
        method=len(self.abc.methods);self.abc.methods.append(info)
        bindings=copy(e.bindings)
        if hasattr(bindings,'local_types'):
            bindings.local_types={parameter.name:self.type_index(parameter.type_name) for parameter in expression.parameters if parameter.type_name not in ('*','int','uint','Number','String','Boolean','Object')}
        registers={parameter.name:index for index,parameter in enumerate(expression.parameters,1)}
        types={parameter.name:parameter.type_name if parameter.type_name in ('*','int','uint','Number','String','Boolean','Object') else f'#{self.type_index(parameter.type_name)}' for parameter in expression.parameters}
        ret=expression.return_type if expression.return_type in ('*','void','int','uint','Number','String','Boolean') else f'#{info.return_type}'
        child=type(e)(registers,len(registers)+1,ret,False,types,bindings,e.runtime_multiname_index)
        child.enable_closures(self.abc,method,scope,inherited=references,budget=self.budget,depth=self.depth+1)
        assembly=child.compile(expression.body)
        body=MethodBodyInfo();body.method=method;body.local_count=len(registers)+1;body.init_scope_depth=scope
        body.code=assemble_instructions(self.abc.constant_pool,assembly,exception_table=body.exceptions,activation_traits=body.traits)
        if len(body.code)>512000:raise ValueError('Nested function body exceeds the compilation budget')
        limits=analyze_method(self.abc,body)
        body.max_stack,body.local_count,body.max_scope_depth=limits.max_stack,limits.local_count,limits.max_scope_depth
        self.abc.method_bodies.append(body)
        e.emit(f'newfunction {method}')
