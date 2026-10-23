# Corpus y comparación por identidad

`tools/build_parity_corpus.py --prepare-only` prepara doce proyectos locales
CC0-1.0. Las familias están fijadas antes de medir: seis de desarrollo, tres de
validación y tres de holdout. Las variantes debug/release y FWS/CWS/ZWS de una
familia conservan su split. No se sustituyen compiladores externos faltantes
por fixtures generadas por PySWF. Un compilador faltante o una compilación
fallida queda en `build-state.json`; el manifiesto solo se sella cuando las 72
variantes están presentes. `manifest.sha256` protege el manifiesto y cada caso
conserva hashes de fuente, SWF, dependencia y salida del compilador.

Este corpus pequeño permite estudiar los patrones declarados. No demuestra
representatividad de todos los programas Flash ni paridad global.

## Inventario canónico

`py_swf.canonical_inventory.build_inventory(bytes)` recorre el layout binario
sin usar `ABCFile.parse`, el descompilador nativo ni sus nombres de métodos como
ancla. Los offsets están referidos al SWF descomprimido con encabezado de ocho
bytes; las cabeceras originales de tags se conservan. FWS/CWS/ZWS comparten
ubicación y hash de bytecode, pero mantienen hashes e identidades de archivo
propios.

El identificador completo es `input_sha256:document_id`. `document_id` sigue
siendo el alias histórico `tag:<ruta>/method:<índice>` o el alias de acción,
función, clase o script. El inventario incluye hash del ABC, body_index,
method_index, bytecode_offset, bytecode_sha256, contexto y parent_unit_id.
Los contenedores dañados conservan una unidad de diagnóstico contada.

Los scopes `method`, `action`, `function`, `class` y `script` tienen denominadores
independientes. Para una clase, el hash agregado concatena `u32le(len(code))`
y el cuerpo de cada método propio en orden creciente de method_index: iinit,
cinit y traits de método/getter/setter/function. Para scripts usa init y traits
directos; no incluye los miembros de clases declaradas. La verificación con
Rust contrasta ubicación, idioma, tamaño, hash y contexto de funciones AVM1.
Un desacuerdo bloquea las métricas de calidad y queda expuesto.

## Adaptadores y pares

El modo nuevo de pyflashkit 1.3.0 es `api_all_abc`. Utiliza el API público
`Workspace.abc_blocks`, `decompile_class(abc, class_index=...)` y
`decompile_method(abc, method_idx=...)`. Cada ABC se exporta a una carpeta que
incluye tag, ordinal y hash. Dos clases de igual nombre en ABC distintos no se
sobrescriben. Se conservan filas fallidas y artefactos de las exportaciones
correctas. Si se solicita `include_source`, la fuente vuelve en la respuesta;
`evidence_root` en la configuración permite conservar los archivos originales.

El modo histórico `cli_first_abc` permanece disponible mediante
`adapter_mode` explícito y debe figurar al comparar informes antiguos. No se
mezclan sus medidas con las del API nuevo. Flasm, Flare y RABCDAsm siguen
contando salida a nivel de archivo cuando no existe correspondencia exacta;
el número de archivos no se utiliza como cobertura de métodos.

`match_units` genera todos los pares de motores de fuente solicitados (seis
para native/rust/jpexs/pyflashkit), incluso cuando native no está solicitado.
La columna histórica primaria native/JPEXS permanece como alias compatible.
Solo entran a la intersección verificada las identidades únicas con bytecode
exacto. Clases, scripts y métodos nunca comparten denominador. La cobertura
de aplicación y dependencia se etiqueta por propiedad de clases y se informa
separadamente; las familias deben pesar igual al resumir el corpus.

Un initializer que instala clases o namespaces conserva su P-code y una
referencia `represented_by_document` al script. No entra en la cobertura de
fuente de métodos aunque exista una vista legible de declaraciones completas.
Las clases y scripts que sí se reconstruyen se contabilizan en sus scopes.
El API de pyflashkit solicita `include_signature=False` para los métodos;
sus clases completas salen por `decompile_class`. Las métricas compartidas
rechazan payloads de clases/scripts etiquetados como métodos, incluido el
estado histórico `implicit`, cuando la metadata declara ese otro scope.

En los informes persistidos, `engine.units` contiene las observaciones completas
y `unit_observations_ref: "units"` declara el alias. El inventario completo aparece
una vez por caso; los motores conservan su hash. Esto evita duplicar listas de
miles de unidades al importar catálogos por familia. `run_benchmark(...,
family_ids=[...])` permite emitir una familia sin cambiar su split ni incluir
fuentes retiradas. El universo completo se conserva en el manifiesto sellado.

## Ejecución controlada

`tools/run_parity_campaign.py` requiere un archivo freeze JSON con
`agents_quiescent: true`, `builds_and_tests_finished: true` e
`implementation_sha256` vigente. Ejecuta tres sesiones consecutivas del perfil
full: tres calentamientos, treinta corridas hot y cinco cold por caso/motor.
Exige CPU de fondo <=10 % y RAM disponible >=2 GiB, registra tres muestras de
preflight, monitorea durante la corrida y vuelve a verificar al terminar.
Builds/tests observados, cambios del código o condiciones insuficientes se
registran como bloqueos o contaminación. Nunca se inventan medidas ausentes
ni se anuncia un ganador por salida no comparable.

La compilación del original, la generación de fuente, su sintaxis, recompilación,
validez del SWF guardado, fidelidad y ejecución son dimensiones distintas.
El arnés de comportamiento y la recompilación completa tienen informes propios;
generar texto o reabrir un SWF no establece equivalencia semántica.
