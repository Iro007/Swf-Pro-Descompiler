# Decompilador: entregas y evidencia

## Política de cierre

El programa distribuible continúa con Python nativo, sin Java/JPEXS. La referencia
Java y sus dependencias están únicamente en `.dev-references/` para desarrollo.
No se modifican el Java global, la copia antigua de JPEXS ni la configuración del agente.

Paridad y superioridad no se deducen de que exista texto fuente, de una compilación
exitosa o de un tiempo menor. La superioridad requiere paridad sobre el corpus
fijado y tres fallos independientes de JPEXS resueltos con evidencia de comportamiento.

## Entrega actual

- Hito 0: herramientas de comparación integradas y mediciones iniciales realizadas.
  Referencia JPEXS 26.3.0 y Temurin 21 portátiles, con archivos descargados verificados
  por SHA256; copia nueva de fuentes fijada al commit aprobado.
- Enumeración de cuerpos AVM2, incluidos constructores/inicializadores y métodos
  internos; acciones AVM1 de frames, inicialización, sprites, botones y clip events.
  Los contenedores que no pueden interpretarse de forma segura se contabilizan con diagnóstico.
- Bridge JSONL mantenido separado del adaptador antiguo: correlación de solicitudes,
  decompilación en memoria, compilación AS2/AS3 de referencia y reapertura del candidato.
- CLI `pyswf benchmark`: perfiles smoke/full, mediciones frías/calientes, muestras
  de RSS del árbol de procesos, límite de memoria por Windows Job y timeout por operación.
- Muestra de recompilación separada de la medición: como máximo ocho documentos
  elegibles por archivo/motor. Python usa cuerpos de métodos; JPEXS usa script packs.
  Sus denominadores NO son equivalentes y no se usan para declarar un ganador.
- Primer avance del Hito 1: postdominadores y manejo de bloques acíclicos compartidos
  entre ramas mutuamente excluyentes. Se mantienen las protecciones de loops y excepciones.
  La muestra histórica conserva 423 casos: 235 fuentes antes, 240 después.
- Página local de benchmarks inspirada en paneles de análisis independientes:
  barras por métrica, cobertura frente a latencia, filtros, JSON resumido y
  metodología. Se abre desde el icono de gráficos del editor o `/#benchmarks`.
  Volver al editor conserva la sesión. La página solo lee resultados; no ejecuta
  Java ni decompila desde HTTP. El ejecutable incluye una muestra de resultados.
  Cada informe conserva su propio corpus/perfil; no mezcla campañas en un ranking.
  El informe `benchmark-postdom-full` queda marcado como exploratorio porque
  coincidió con un build; no se utiliza como comparación de rendimiento.

El Hito 0 **no está cerrado para todo el corpus**: las mediciones iniciales cubren
una selección y no verifican todo el comportamiento en un runtime Flash.
Los campos `behavior=not_run`, `compilation=not_run`, errores y timeouts no cuentan
como éxito. La selección validation/development agrupa variantes por proyecto de
fuente; aún no demuestra deduplicación semántica entre proyectos diferentes.

Actualización: hay una auditoría de generación sobre los 96 SWF del corpus
fijado, sin retirar entradas fallidas. Tras corregir el pool float de ABC 47.16,
se relacionan 10.917 unidades por identidad/hash. Las unidades largas exceden
un presupuesto explícito de 50.000 instrucciones y conservan P-code en vez
de agotar el worker. Dos entradas cifradas/ofuscadas quedan fallidas y visibles.
La auditoría no mide velocidad ni recompilación exhaustiva ni comportamiento.
Los informes anteriores siguen conservados. Esto no cierra Hito 0.

La vista predeterminada de Benchmarks ahora reúne todas las herramientas en
una sola página. Cada columna/operación mantiene su corpus, perfil y revisión;
se puede cambiar de campaña dentro de la misma página. Los informes individuales
quedan como evidencia histórica opcional, no como comparación principal.

Competing_tools contiene instaladores identificados, no herramientas medidas.
Sothink 7.4 tiene firma válida de SourceTec; Trillix 5.3.1400 tiene firma válida
de Eltima; AS3 Sorcerer 6.78 no está firmado. ASV setup fue bloqueado por Defender
(Program:AndroidOS/Multiverze): la descarga no se completó y no hay instalador
local, según confirmó el usuario. Los complementos no sustituyen al programa.
No se restauró cuarentena, no se cambió Defender y no se ejecutaron instaladores.
Se prepararon tres SWF públicos con manifiesto para pruebas locales.
La automatización de interfaz Windows no está disponible en esta sesión;
instalación/licencia requiere intervención del usuario antes de medir esos programas.

## Pendiente del plan

Prioridad actual por petición del usuario: desarrollo del motor y del editor
Python. Las comparaciones se retoman al cierre de un hito estable, en una sola
página local; no se ejecutaron mediciones nuevas durante esta entrega.
Hay documentos fuente de método/acción con edición por hash, borradores
protegidos, fuentes guardadas y proyecto v2 compatible con v1. El compilador
admite arrays/objetos y accesos públicos acotados. Se añadió el primer nivel del
compilador de programas AS3: clases Object con campos literales, constructores,
accesores, firmas y metadatos. Está disponible desde UI/API/CLI; permite cambios
de cuerpos/valores en programas propios verificados y conserva fuentes en v2.
Los esquemas de clases externas siguen de lectura y los mapas línea–bytecode
siguen sin implementarse. Hito 2 continúa parcial; véase NATIVE_COMPILER.md.

Avance de privados: identidades de namespace acotadas al ABC, miembros de un
único propietario probado y recompilación por índices QName originales.
Ambigüedades, receptores no probados y llamadas privadas con receptor nulo
quedan en P-code. La muestra histórica conserva hashes y 423 casos:
240 a 254 fuentes estructuradas en aquella medición. La revisión posterior
conservadora de tipos dejó 253 estructuradas/170 P-code: `TestCollidingTry.run`
queda protegido porque `tests::e` es PackageInternal y no hay una declaración
demostrada en el SWF. No se reduce el denominador de 423 ni se oculta ese caso.
Cuatro casos se recompilaron
y reabrieron; diez mantienen protecciones adicionales. Esto no cierra Hito 1
ni demuestra todo el comportamiento en un runtime Flash.

El exportador antiguo «FLA» se corrigió: produce un paquete ZIP de recursos con
`manifest.json` y P-code, no FLA/XFL. La ruta nueva es
`/api/files/{sid}/export/resource-bundle`; `/export/fla` queda como alias deprecado
con aviso y nombre `.resources.zip`. Los fallos de extracción se incluyen como
diagnósticos. La exportación real compatible con Animate sigue pendiente.

El catálogo de benchmarks separa fuente, desensamblado, XML, extracción y runtime.
Véase [benchmark-tools.md](benchmark-tools.md) para resultados y limitaciones.

1. Ampliar corpus completo y conjuntos públicos independientes de validación;
   añadir cargas sintéticas significativas de hasta 100 MB y pruebas Ruffle observadas.
2. Resolver identidades de namespaces/multinames, ámbitos privados, capturas y
   flujos complejos; no quitar las protecciones del editor sin compilación segura.
3. Compilador Python de clases/scripts completos, constructores, interfaces,
   firmas, namespaces, closures, enumeraciones y XML/E4X.
4. Desofuscación por IR/AST y modos faithful/readable/pcode con mapa de identidades.
5. SourceDocument, mapas fuente/bytecode, compile-document con control de hash,
   proyectos versión 2 y edición documental en la interfaz.
6. Evaluación final de paridad y casos independientes de superioridad; sin promesa
   de conseguir esos casos antes de investigarlos y verificarlos.

## Repetir las mediciones

```powershell
.venv\Scripts\python.exe -m pip install -e ".[benchmark]"
.venv\Scripts\python.exe tools/bootstrap_references.py --source
.venv\Scripts\python.exe tools/build_reference_bridge.py
.venv\Scripts\python.exe -m py_swf.cli benchmark third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata --profile smoke --engines native,jpexs --output reports/benchmark-current-smoke
.venv\Scripts\python.exe -m py_swf.cli benchmark third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata/as3_new/bin/as3_new.flex.swf --profile full --output reports/benchmark-postdom-full-quiet
.venv\Scripts\python.exe tools/publish_benchmark_snapshot.py reports/benchmark-current-smoke/benchmark.json
```

El perfil full en un archivo aplica las repeticiones exhaustivas a ese archivo,
no a todo el corpus. Para evaluar todos los archivos se pasa el directorio.
No ejecutar builds ni otros análisis pesados en paralelo con una medición que
se quiera utilizar para comparar rendimiento. El informe declara la máquina,
versiones, opciones, hashes y límites del muestreo de memoria.

## Referencias y avisos

`reference-lock.json` contiene revisiones y procedencia. JPEXS principal es GPLv3;
ffdec_lib es LGPLv3 y minimal-json MIT. El JDK y SDKs empaquetados con FFDec son
herramientas de desarrollo y conservan sus avisos originales; no se distribuyen
con PySWF Studio. REA se usa como referencia de evidencia, y Phoenix/Ruffle como
referencias de diseño/verificación. El postdominador añadido es una implementación
original de las ecuaciones estándar; no incorpora angr como dependencia.

Todo traslado futuro de código tendrá sus avisos y procedencia por componente.
