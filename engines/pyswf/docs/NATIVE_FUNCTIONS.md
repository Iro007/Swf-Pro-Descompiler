# PySWF: enumeraciones y funciones con capturas

El motor Python compila `for (var key in collection)` en AS2/AS3 y
`for each (var value in collection)` en AS3. El destino puede ser una variable
nueva o existente, sin inicializador. La colección se evalúa una vez;
`break`, `continue`, retornos y finally conservan las salidas del bucle.
AS3 obtiene valores con `nextvalue`; AS2 conserva el snapshot de claves de
ActionEnumerate2 y drena su pila antes de entrar en código del usuario.
No se promete un orden universal de enumeración de propiedades Flash.

AS2 admite funciones anidadas de firmas sin tipos, declaraciones al nivel
principal de cada función y expresiones anónimas. Emite ActionDefineFunction,
conserva referencias al scope padre y declara locales propios sin sobrescribir
los del padre. Las declaraciones se inicializan antes de las otras sentencias
del mismo cuerpo. ActionDefineFunction requiere SWF 5; ActionEnumerate2, SWF 6.

AS3 genera métodos ABC secundarios, `newfunction`, NEED_ACTIVATION, traits de
slots y accesos a la celda capturada mediante `getouterscope/getslot/setslot`.
Las factories tienen activaciones independientes; hijos y nietos conservan
las celdas compartidas de sus scopes. Parámetros/locales que sombrean una
captura se resuelven en su propio scope. Los handlers de excepción restauran
this y la activación que pertenecen al cuerpo actual.

La API de cuerpos conserva su retorno de texto y acepta contexto opcional:

```python
assembly = compile_actionscript(source, "as3", abc=abc,
                               method_index=body.method,
                               init_scope_depth=body.init_scope_depth)
body.code = assemble_instructions(abc.constant_pool, assembly,
                                 exception_table=body.exceptions,
                                 activation_traits=body.traits)
```

Las directivas `.activation #nombre slot #tipo` son serializables. El
ensamblador rechaza su uso si no recibe la salida `activation_traits`, incluso
si el assembly se convierte a str. La compilación de clases y el editor nativo
ya transportan esos traits. El contexto ABC debe ser una copia/candidato del
llamador; puede añadir métodos, constantes y flags al compilar.

Las activaciones/traits opacos de un SWF existente siguen protegidos al
reemplazar su cuerpo desde fuente. Su edición usa P-code exacto o un programa
completo cuya fuente guardada reproduzca el ABC. Esta capacidad no habilita
bindings privados ambiguos ni elimina las protecciones anteriores.

Siguen rechazados: expresiones de función con nombre propio, declaraciones
dentro de ramas/bucles, capturas desde catch, optional/rest en firmas anidadas,
this/super/arguments dinámicos en closures AS3, firmas tipadas de funciones AS2
y sombreado de intrínsecos AVM1. AS3 limita a 32 niveles y 256 métodos anidados
por contexto. SDKs completos, E4X, genéricos, `with`, etiquetas y `do while`
requieren otro nivel de implementación o un módulo externo.

Pruebas reproducibles, sin GUI ni navegador:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_native_enumeration.py tests/test_native_closure_compiler.py tests/test_native_control_flow.py -q
.venv\Scripts\python.exe tools/verify_compiler_cli.py
```

La VM independiente ejecuta bytes ensamblados y ABC reabierto, no el AST del
compilador. Comprueba trazas, valores, orden de efectos, recursión, activaciones
compartidas/separadas, shadowing, serialización y rechazos. Es un intérprete
acotado para fixtures propios, no un runtime Flash completo. No se ejecutó
Ruffle ni navegador en esta campaña; los informes Ruffle anteriores permanecen
como evidencia histórica de otros escenarios.

La comprobación `tools/verify_native_functions.py` identifica el método AS3
por nombre y su literal, evitando índices de tag ligados a una versión del
corpus. `ActionCall` AVM1 (`0x9E`, desde SWF 4) admite exactamente un payload
vacío; el número de frame se toma de la pila. Un payload no vacío, truncado
o un opcode desconocido siguen siendo errores de validación.

El validador estricto actual rechaza dos métodos ajenos a la closure elegida
en `as3_new.flex.swf`: `TestFinallyZeroJump.run` por un rango de excepción
vacío y `TestTryReturn2.run` por alturas de pila distintas en un join. Esto
describe una limitación o un rechazo del validador local; no demuestra que
Flash rechace ese SWF. La prueba conserva el hash y los errores del original,
comprueba que la exportación completa queda bloqueada y que el original no
cambia. Después verifica P-code, guardado y reapertura en una biblioteca SWF
que contiene el ABC real completo de `TestInlineFunctions`, sin recompilarlo
ni sustituir sus métodos por código sintético. El informe marca
`source_snapshot_isolated` y especifica si `other_tags_preserved` se refiere
al SWF original completo o a esa biblioteca aislada. El caso AS2 usa el SWF
original completo.
