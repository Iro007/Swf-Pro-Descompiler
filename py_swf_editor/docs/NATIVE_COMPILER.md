# Compilador nativo: control de flujo y excepciones

Los compiladores de cuerpos y del primer nivel de programas AS3 son Python.
No arrancan Java ni usan JPEXS para compilar, validar, exportar o reabrir un SWF.
El nivel de clases está acotado; no cubre todo ActionScript.

## Programas AS3: primer nivel de clases

En la interfaz, **Nuevo desde código** crea una biblioteca SWF desde un paquete
con clases derivadas de `Object`. Genera declaraciones ABC, campos de valor
literal, visibilidades, constantes estáticas, constructores, métodos,
getters/setters, parámetros opcionales/rest y metadatos de cadenas. La API es
`POST /api/files/from-source`; la fachada Python ofrece `compile_classes`.

```powershell
.venv\Scripts\python.exe -m py_swf.cli compile-classes Counter.as --output Counter.swf --compression CWS
```

El documento **Programa AS3 · fuente original** solo aparece cuando recompilar
la fuente guardada reproduce exactamente el ABC actual. No es una decompilación
completa de clases arbitrarias. Sus cambios admiten cuerpos de métodos y valores
literales de campos, no nombres, firmas, defaults de parámetros o estructura.
La comprobación de hash y ABI precede a la validación y al historial; un fallo
conserva bytes, fuente, undo/redo y borrador. Un metadato de proyecto falsificado
no permite reemplazar ABC desconocido.

La fuente se conserva en `.pyswfproj` v2 y en undo/redo. Exportar únicamente el
SWF no incluye esa fuente: al reabrirlo, vuelve el esquema de lectura y los
documentos por método. Una edición P-code o de cuerpo que invalide la prueba
exacta protege automáticamente el documento del programa.

Herencia, interfaces y overrides internos se enlazan por proveedores y firmas
comprobados; véase NATIVE_LINKER.md. Quedan rechazados inicializadores con
efectos/lecturas de miembros, SDKs externos generales, genéricos, E4X y sintaxis
fuera del compilador de cuerpos. Los tipos no cuentan con un verificador semántico
AS3 completo. Las pruebas actuales comprueban estructura ABC, reapertura y el
flujo de edición; no prueban la inicialización de todas las clases en Flash.
La opción document_class permite generar escenario y SymbolClass para Sprite/MovieClip.

## Sintaxis incorporada

- Arrays `[a,b]` y objetos `{clave:valor}` en AS2/AS3. Se conserva el orden
  de evaluación y la regla de claves duplicadas; AVM1 y AVM2 consumen la pila
  de forma distinta, por lo que sus generadores no comparten ese orden.
- Acceso, asignación, `+=`/`-=` e incremento/decremento con corchetes en AS2.
  AS3 admite este subconjunto solo en contexto de script con namespace público
  vacío probado. Clases, ámbitos privados/open namespaces y llamadas por nombre
  computado siguen protegidos; no se aproximan cambiando su binding.
- `for (inicio; condición; actualización)`: cláusulas vacías y listas de
  inicializadores/actualizaciones. `continue` salta a la actualización, no a la condición.
- `switch (expresión)`: evaluación única del selector, comparación estricta,
  expresiones de caso, caída entre casos y `default` en cualquier posición.
  `break` abandona el switch y `continue` busca el bucle envolvente.
- `try { ... } catch (e) { ... } finally { ... }`: cualquiera de catch/finally
  puede omitirse, pero no ambos. Admite capturas tipadas, varios catches en orden,
  capturas anidadas y shadowing del nombre de la excepción.

El compilador admite `for in` AS2/AS3, `for each` AS3 y funciones anidadas/closures
con capturas locales declaradas. Las activaciones AS3 necesitan contexto ABC y
traits explícitos. Etiquetas, `do while`, capturas de catch y bindings dinámicos
this/super/arguments en closures AS3 siguen rechazados antes de modificar el SWF.
Los límites y la verificación CLI se registran en NATIVE_FUNCTIONS.md.

## Documentos fuente y edición segura

La respuesta nativa conserva `sections` y `body_index` y añade `source_document`
con identidad estable, contexto y hash del SWF de trabajo. La interfaz compila
mediante `compile-document`; una base obsoleta devuelve 409 antes de cambiar
bytes, fuentes, historial, undo o redo. El borrador se conserva en pantalla y
solo se descarta mediante una acción explícita del usuario.

Los proyectos `.pyswfproj` v2 incluyen fuentes guardadas y checksum. Los v1
siguen abriéndose y migran solo en memoria; el archivo antiguo no se modifica
hasta que el usuario guarda. Undo/redo de la sesión restaura también las fuentes
escritas. Las fuentes guardadas se distinguen de la decompilación y pueden
recuperarse con un botón; no sustituyen silenciosamente la salida del motor.

Esto cubre documentos de método/acción y el programa AS3 administrado descrito
arriba. Los esquemas de clases externas son de lectura; no hay compilador AS3
general ni mapa exacto línea–bytecode todavía.
El campo `map_status=not_available` registra ese límite explícitamente.

## Semántica de finally

Las salidas de un try se representan como una terminación pendiente: normal,
excepción, return, break o continue. El bloque finally se emite una vez, fuera
del área protegida por sus propios catches. Después se resuelve la terminación.

- El valor de return se evalúa y se conserva antes de ejecutar finally.
- En AS3, la coerción del tipo de retorno ocurre antes de finally.
- Una salida abrupta desde finally sustituye la terminación pendiente.
- Un catch que lanza otra excepción no vuelve a entrar en sus catches hermanos.
- Un bucle dentro de try puede continuar sin ejecutar finally en cada vuelta;
  un salto que sale de try sí lo ejecuta.
- Los finally anidados se ejecutan de dentro hacia fuera.

AS2 utiliza registros de acción ActionTry (0x8F) con tres longitudes UI16,
catches internos y saltos explícitos para evitar ejecutar catches en el flujo
normal. Los temporales se declaran como locales AVM1 con nombres higiénicos.
El límite de cada stream Try es 65 535 bytes y los saltos AVM1 son S16.
ActionTry requiere SWF 7 o posterior: editar un contenedor más antiguo se rechaza
con diagnóstico, sin actualizar silenciosamente su versión ni modificar el original.

AS3 utiliza tablas ABC con rangos `[from, to)`, tipos de captura y destinos.
Los handlers restauran el scope inicial y añaden el scope de `this`; los nombres
de catch se resuelven en registros locales privados del compilador. Las nuevas
closures generan activaciones y slots declarados; los handlers restauran también
su activación. Las activaciones opacas existentes mantienen su protección.

## Ensamblado y conservación de las excepciones

La fuente compilada AS3 contiene directivas serializables:

```text
.exception L_inicio L_fin L_handler String error
```

El desensamblado exacto usa `#índice` para conservar los multinames originales:

```text
.exception L_10 L_30 L_40 #5 #6
```

Quien use la API de bajo nivel debe recoger la tabla junto con los bytes:

```python
assembly = compile_actionscript(source, target="as3")
exceptions = []
code = assemble_instructions(pool, assembly, exception_table=exceptions)
body.code, body.exceptions = code, exceptions
```

Omitir la salida de la tabla cuando hay directivas genera un error; convertir
el assembly a texto y recuperarlo no pierde esa protección. Al editar P-code,
los offsets de las excepciones se recalculan mediante etiquetas. Si se omiten
las directivas de un método que tenía excepciones, no se permiten cambios de código.

Cada edición de fuente sustituye la tabla del cuerpo completo, calcula sus
límites de pila/scope/locales y vuelve a analizar el ABC serializado. La aplicación
añade el cambio al historial solamente cuando el candidato pasa la validación.

## Verificación y límites

`tests/test_native_control_flow.py` comprueba resultados de bytecode, exportación
atómica, FWS/CWS/ZWS, DoAction/DoInitAction/DoABC 72 y 82, proyectos, undo/redo,
remapeo del P-code y rechazo de entradas incorrectas. El intérprete de referencia
vive en `tests/native_control_vm.py`: ejecuta bytes, no el AST del compilador.

`tools/verify_native_flow.py` (retirado con las interfaces anteriores; ver la etiqueta git `legacy-ui-final`) verificaba API y navegador contra el ejecutable
con Java/JPEXS no disponibles, desde una carpeta temporal. Incluye edición de
recursos, exportación, reapertura, tablas de excepciones y ejecución acotada
del bytecode guardado.

Esto no demuestra compatibilidad con todos los SWF ni con un runtime Flash
completo. La validación actual no reemplaza al verificador de tipos completo
de AVM2.

## Reconstrucción de fuente en Python

`py_swf/decompile/native.py` coordina un IR común para AVM1 y AVM2, elevación de
expresiones con preservación de efectos, regiones de control y reconstrucción de
excepciones. Se reconocen bucles naturales, actualizaciones del for, joins de
if/else, switch por comparaciones y por lookupswitch, ActionTry y tablas ABC.
Los despachadores internos de finally se recuperan como retornos/saltos originales.

La fuente no se recupera de archivos `.as`, caches ni comentarios del SWF. Las
fuentes del corpus sirven únicamente como referencia independiente para pruebas.
El bytecode puede representar varias sintaxis equivalentes: un for recuperado
puede corresponder originalmente a un while equivalente. No se promete el texto
original ni igualdad de bytecode al recompilar.

`tests/test_native_reconstruction.py` compara 62 casos del compilador por resultado,
trazas, excepciones y retornos tras decompilar/recompilar. También ejecuta bytecode
de SWF reales de AS2 y de variantes Flex/AIR, y prueba excepciones provocadas por
una llamada dentro del try.

Para repetir la comprobación de películas reales:

```powershell
.venv\Scripts\python.exe tools\verify_native_decompile.py
```

El informe `reports/native-decompile-real-swf.json` registra 19 casos de cuatro SWF,
sus hashes, fuentes generadas, comparación de comportamiento y reapertura. Los
cinco scripts AS2 se editan en la película completa; los 14 métodos AS3 conservan
las protecciones de namespaces/activaciones y se recompilan como métodos aislados.
Esta distinción se registra en `full_movie_source_edit` y `source_edit_guard`.

La UI habilita edición de una reconstrucción aceptada por el compilador y cuyos
bindings no estén protegidos. Las construcciones desconocidas, cierres, funciones
anidadas, ámbitos dinámicos y grafos no estructurables siguen devolviendo P-code
con diagnóstico. Un receptor de llamada o coerción que no pueda preservarse no se
descarta silenciosamente. La reconstrucción tiene límites de tamaño y trabajo.

Además se corrigió el orden de palabras de 32 bits de los doubles AVM1, verificado
contra bytes conocidos y el corpus real, no solo por auto-round-trip.
