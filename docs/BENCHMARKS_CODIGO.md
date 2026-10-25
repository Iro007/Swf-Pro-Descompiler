# Benchmarks

Una pregunta por categoría, una tabla por categoría; nunca se mezclan cosas que no son comparables. Todas usan el mismo oráculo: Ruffle imprime la traza esperada del corpus.

| Categoría | Pregunta | Motores |
|---|---|---|
| Descompiladores | ¿lo que emite recompila (Flex/Rascal) y se comporta igual? | PySWF, RustSWF, JsSWF, GoSWF, AsmSWF, JPEXS, pyflashkit, Flare |
| Robustez | ¿qué hace con un SWF dañado (truncado o corrupto)? | los mismos descompiladores |
| Fidelidad por unidad | ¿el fuente de cada clase, método o acción, compilado solo y empalmado en el SWF original, se comporta igual? | los mismos descompiladores |
| Compiladores | ¿compilan el proyecto del corpus y se comporta como se espera? | Flex, Rascal, Haxe, compilador de PySWF |
| Desensambladores | ¿desensamblar y reensamblar conserva el comportamiento? | RABCDAsm (AS3), Flasm (AS2); los que solo listan lo dicen |
| Conversores XML | ¿SWF a XML a SWF conserva el comportamiento? | swfmill |
| Extractores | ¿todos los recursos se extraen y decodifican? | swfextract |

```powershell
.\medir-codigo.ps1            # rápido: un caso por familia de desarrollo y los 51 archivos dañados (~16 min)
.\medir-codigo.ps1 -Completo  # todos los casos de desarrollo y validación, todas las unidades de cada caso
.\medir-codigo.ps1 -Motores native rust   # solo estos descompiladores (JPEXS se mide siempre: es la referencia de cercanía y compite como los demás)
.\medir-codigo.ps1 -Solo robustness unit_fidelity   # solo estas categorías
.\medir-codigo.ps1 -Unidades 40            # unidades por caso en la fidelidad por unidad (0 = todas)
```

Los mismos filtros en `engines/pyswf/tools/bench.py`: `--smoke`, `--full`, `--engines`, `--only decompilers robustness unit_fidelity compilers disassemblers converters extractors`, `--unit-cap N`, `--robustness-timeout S`, `--workers N` (procesos a la vez en robustez y fidelidad por unidad, 4 por defecto) y `--keep-evidence`.

- Corpus fijo: `corpus/parity-20261014` (sellado con hashes; con fuente original y traza esperada) y `corpus/damaged` (derivado de él, con su manifiesto sellado).
- Salida: `reports/bench/<ejecución>/results.json` (cientos de KB con robustez) e `index.json`. La ventana Benchmarks de la app (Herramientas, Benchmarks) lo lee y lo dibuja como una clasificación: tarjetas destacadas, el Índice Flash, una gráfica de barras ordenadas por métrica, el diagrama de cercanía frente a velocidad, la robustez (desenlaces apilados, nota y desglose por tipo de daño), la fidelidad por unidad (barras y clases frente a métodos) y, plegada, la tabla completa con los motivos.
- Cada celda sin número lleva su motivo (no instalado, no lee ese lenguaje, fuente parcial…). No se declara ganador si la ventaja es de un caso o menos, ni entre motores medidos sobre casos distintos: esos motores se muestran aparte, sin número de puesto.
- Pendiente: haxeflixel (necesita lime), ida y vuelta de PySWF desde P-code, corpus más grande con más recursos.

## Cercanía a JPEXS (solo descompiladores)

Además de las cuentas de comportamiento, cada descompilador lleva dos números frente a JPEXS, que actúa de referencia: **cerca JPEXS** es la similitud media del texto generado con el de JPEXS, unidad por unidad (se ignoran comentarios, espacios, comillas y `1` frente a `1.0`), y **cubre JPEXS** es la fracción de las unidades de JPEXS que el motor también emitió. Solo se comparan unidades cuyo `document_id`, ámbito y hash de bytecode coinciden con el inventario canónico del SWF; las que no verifican se excluyen y se cuentan. Es una medida de parecido, no de corrección: JPEXS también se equivoca, y un texto distinto puede comportarse igual. **El ganador de la tabla sigue decidiéndose solo por comportamiento** (recompila y misma traza en Ruffle); el Índice Flash, de abajo, ordena la clasificación pero no lo reemplaza. Si JPEXS no está instalado o falla en un caso, esas celdas quedan en blanco con su motivo (`jpexs_reason` en `results.json`); la línea de JPEXS vale 1,0 por definición y se marca `reference: true`. En las gráficas de cercanía y de cobertura JPEXS es la barra de referencia; en todo lo demás compite como cualquier motor.

## Índice Flash (solo descompiladores)

Una nota de 0 a 100 por motor, la que ordena la clasificación de la ventana Benchmarks. JPEXS compite en ella como los demás:

**Índice Flash = (fuente + recompila + misma traza + unidades con fuente [+ fidelidad por unidad] [+ robustez]) / N**, cada término en porcentaje y N el número de términos que entran (4, 5 o 6).

- **Fuente**: `source / cases`, los casos en que el motor emite fuente completo de todas las unidades del SWF.
- **Recompila**: `recompiles / cases`, los casos en que ese fuente compila con un compilador independiente (Flex para AS3, Rascal para AS2).
- **Misma traza**: `same_trace / cases`, los casos en que, además, Ruffle imprime la misma traza que el original.
- **Unidades con fuente**: `unit_coverage`, la media por caso de unidades con fuente verificado entre unidades del SWF. Las unidades son las clases, scripts y acciones del inventario ABC y AVM1 del propio SWF (`canonical_inventory`), no las de otro descompilador. Una unidad cuenta si el motor la emitió con su identidad y el hash de su bytecode, aunque el proyecto no llegara a recompilar; los métodos y las funciones anidadas viajan dentro de su clase o acción y no se cuentan aparte. Cada caso pesa lo mismo: un SWF grande no ahoga a los pequeños.
- **Fidelidad por unidad** (opcional): `misma / (evaluadas − no se ejecutan)` de la sección de abajo, de 0 a 1. Un motor medido pero sin ninguna unidad juzgada (nada de lo que emitió se pudo compilar solo y ejecutar) puntúa 0 en este término.
- **Robustez** (opcional): la nota de 0 a 100 de la sección de abajo, dividida entre 100.

**Qué términos entran en una corrida.** Los cuatro primeros entran siempre. La fidelidad por unidad y la robustez entran solo si la corrida las midió para **todos** los motores que se ordenan (`comparable: true`); si a uno le falta cualquiera de las dos, esa no entra para ninguno y los motores siguen siendo comparables entre sí. La corrida lo dice: `tables.decompilers.index_components` lista los términos (`["source", "recompiles", "same_trace", "units", "unit_fidelity", "robustness"]` cuando entran todos) y la ventana escribe «Esta corrida incluye: …» y «No incluye: …» sobre el gráfico. Con `--only decompilers` el índice es el de cuatro términos de siempre; las corridas de los esquemas 1 y 2 se leen con sus cuatro términos (o ninguno si no contaron las unidades). Los términos se calculan sobre los casos en que el motor se midió (`cases`): fuente, recompila y misma traza sobre los casos del corpus de paridad; robustez sobre los archivos dañados; fidelidad por unidad sobre las unidades de esos mismos casos. Ninguno usa la salida de otro descompilador, así que se miden igual en todos los motores. Por eso la cercanía a JPEXS y la cobertura de JPEXS, que valen 100 % para JPEXS por definición, no entran en el índice y tienen sus propias gráficas.

Reglas, todas en `py_swf/bench_results.py` (la misma función la usan `tools/bench.py` al escribir y el lector al abrir corridas antiguas):

- JPEXS corre una sola vez por caso: ese trabajo es su propia fila (fuente, recompila, misma traza, unidades y tiempo) y, a la vez, la referencia con la que se mide la cercanía de los demás. Compite por el índice, por el `winner` y por las tarjetas destacadas como cualquier motor; solo en «Cercanía a JPEXS» y «Cobertura de JPEXS» es la barra de referencia (100 % por definición, rayada y sin número de puesto) y no puede ser «el más cercano a JPEXS».
- Un motor sin casos medidos, o una corrida anterior al recuento de unidades (sin `unit_coverage`), no tiene índice: `flash_index` es `null` y la ventana dice «Sin datos en esta corrida» en lugar de calcularlo con menos términos.
- Solo se ordenan juntos los motores medidos sobre exactamente los mismos casos (`comparable: true`: el mayor grupo de motores con el mismo conjunto de casos medidos, de dos o más). Un motor que no lee un lenguaje (pyflashkit solo AS3, Flare solo AS2) o que no está instalado queda aparte con `comparable: false`; su porcentaje sobre otros casos no se compara con el de los demás, y tampoco decide qué términos entran en el índice.
- Es una nota compuesta: fuente, recompila y misma traza están anidadas (cada una incluye a la siguiente), así que el comportamiento pesa más que la cobertura de unidades. El primero del índice puede no ser el `winner` por comportamiento; las dos cosas se muestran.
- Las demás categorías no tienen índice: su métrica principal es el porcentaje de casos correctos (`ok / cases`), ordenado con la misma regla de casos comparables. Robustez y fidelidad por unidad tienen su propia nota (abajo) y su propio ganador, con la misma regla de ruido.

## Robustez ante archivos dañados (solo descompiladores)

¿Qué hace cada motor con un SWF dañado? Solo hay dos tipos de daño: truncar y corromper.

### El corpus dañado

`corpus/damaged/` lo escribe `engines/pyswf/tools/make_damaged_corpus.py`: 51 archivos de menos de 7 KB, cada uno derivado de un SWF del corpus de paridad por un solo tipo de daño.

| Tipo | Qué hace | Casos |
|---|---|---|
| `truncate_header` | corta dentro de la cabecera (firma, longitud, rectángulo, frecuencia o número de cuadros) | 6 |
| `truncate_tag` | corta en mitad de un tag que no es código (antes o después del código) | 6 |
| `truncate_code` | corta en mitad del cuerpo de un método o de una acción | 7 |
| `bad_length` | longitud de archivo errónea en la cabecera: mayor, menor, cero o `0xFFFFFFFF` | 6 |
| `bad_compression` | flujo zlib roto en un CWS: bytes alterados, cabecera o suma de comprobación malas | 6 |
| `bad_tag_length` | el campo de longitud de un tag (de código o no) miente: más largo o más corto | 6 |
| `flip_code` | bytes alterados al azar en el tag de código (DoABC/DoAction), en el código o en cualquier byte | 8 |
| `flip_noncode` | bytes alterados al azar en un tag que no es código | 6 |

`manifest.json` (con su `manifest.sha256`, como los corpus sellados) da por caso: el archivo de origen y su sha256, el tipo, las opciones del plan, los parámetros ya resueltos (corte, desplazamientos alterados…), la semilla, el tamaño y el sha256 del resultado y los **rangos de bytes del original que alcanza el daño** (`damage.ranges`, en el SWF descomprimido con su cabecera de 8 bytes: la base del inventario canónico). Es determinista: la semilla global es `20261024`, la de cada caso sale de ella y del id con SHA-256, y el azar es SHA-256 en modo contador (no `random`), así que la misma versión del plan da los mismos bytes en cualquier máquina y versión de Python. Nunca recomprime (los CWS solo se dañan byte a byte). `python tools/make_damaged_corpus.py --check` regenera en memoria y falla si `corpus/damaged/` difiere. Un archivo, su origen o el manifiesto que cambien los rechaza `read_damaged_manifest`.

### Cómo se mide

Cada pareja (motor, archivo dañado) corre en **un proceso aparte** con plazo (30 s, `--robustness-timeout`) y un trabajo de Windows que limita a 2 GiB la memoria comprometida y mata todo el árbol al cerrarse; hasta 4 a la vez. Una caída o un cuelgue es un resultado más: nunca para el banco. El proceso (`bench_robustness.py --worker`) llama a cada motor por su entrada habitual: PySWF en el propio proceso, RustSWF/JsSWF/GoSWF/AsmSWF por su worker JSONL, JPEXS por su puente Java, pyflashkit y Flare por su línea de comandos. Abre el archivo, lista las unidades y pide el fuente de cada clase, script, acción y método; no juzga nada. El padre lo contrasta con el **original**, no con el archivo dañado, así que un fallo del propio PySWF al leer el daño no puede hundir a otro motor.

### Los cinco desenlaces

| Desenlace | Cuándo | Nota |
|---|---|---|
| `recovered` | dio fuente verificado de al menos una parte que seguía intacta **y** avisó del daño | 100 |
| `partial` | dio salida, pero sin avisar o sin nada de lo intacto | 60 |
| `clean_error` | no dio salida, pero dio un mensaje claro | 40 |
| `crash` | el proceso murió o acabó con un código de fallo de Windows, no dejó resultado, o el error que da es una traza de pila o una excepción sin controlar | 0 |
| `hang` | superó el plazo | 0 |

Las preguntas se hacen en este orden: ¿superó el plazo? (hang) → ¿cayó el proceso, no dejó resultado, o el error es una excepción sin controlar? (crash) → ¿dio salida? Si no: ¿tiene un mensaje? (clean_error) → ¿hay salida de lo intacto y un aviso? (recovered) → si no, partial.

- **Salida** es fuente no vacío de algún documento (clase, script, acción) o método. Un fallback a P-code no es salida.
- **De lo intacto, verificado**: el documento o método emitido es una unidad intacta del original, con su identidad y el hash de su bytecode (o, para los documentos de PySWF, los métodos que contiene; para las clases de RustSWF, todos sus métodos listados con los hashes del original; un script de RustSWF se compone de sus clases, como hace la suite; el texto único de Flare es la acción raíz si el SWF tiene exactamente una).
- **Aviso del daño**: un mensaje que el motor **no** dio ya para el archivo sano (se compara con una pasada del mismo motor sobre el SWF de origen sin dañar), o un error tras haber dado salida. Los diagnósticos de rutina (un método que no sabe reconstruir) nunca cuentan como aviso.
- **Excepción sin controlar** en un motor de Python: cualquier tipo salvo `ValueError` y `EOFError` (los errores que PySWF lanza a propósito; los de decodificación de Unicode tampoco). En el resto de motores: un mensaje con traza de pila o firma de fallo interno (Python, Java, Rust, Go, Node: `Traceback`, `NullPointerException`, `panicked at`, `goroutine`, `Cannot read properties of undefined`…) o un código de salida de fallo (`0xC0000005`…). Un mensaje de error de validación («Invalid SWF file», «Tag 389 exceeds its container»), aunque salga con código 1, es un error limpio.
- **Sin salida ni mensaje**: si el motor termina bien pero no emite nada y no dice nada, cuenta como `clean_error` con el motivo «sin salida ni mensaje» (es la más cercana de las cinco definiciones; el motivo queda en cada fila).
- Cuando no queda nada que recuperar (el corte cae en la cabecera, o el código ya no está), lo mejor que se puede hacer es un error claro: 40. Es un techo igual para todos los motores, que no cambia el orden pero sí la escala absoluta; por eso el desglose por tipo de daño se muestra aparte y cada archivo guarda `intact_units`.

### Unidades intactas y unidades recuperadas

Las unidades intactas salen del inventario por bytes del original (`canonical_inventory`) y de los rangos del manifiesto, sin ningún descompilador (`py_swf/robustness.intact_units`). Una unidad está **intacta** si ningún byte dañado toca ni sus propios bytes ni lo que necesita para leerse:

- AS3: los registros de cuerpo que posee (cabecera, código, tabla de excepciones y rasgos del cuerpo; una clase posee los de sus métodos, un script los de sus funciones y los de las clases que declara) y, compartido por todas las unidades del tag DoABC, la cabecera del tag y todo lo anterior al primer cuerpo (constantes, tablas de métodos, clases y scripts), más las cabeceras de los sprites que lo contienen;
- AS2: sus bytes de código y el resto de su tag (cabecera, id del sprite, datos del botón o del clip).

Daño después del código (un `SymbolClass` cortado) deja intacto todo el código; un byte en el cuerpo de un método se lleva ese método y su clase; un byte en las constantes se lleva todo el tag. Los daños de metadatos (la longitud de la cabecera) no tocan ninguna unidad. En este corpus 26 de los 51 archivos dejan alguna unidad intacta (clase, script o acción); los otros 25 no tienen nada que recuperar (el corte cae antes del código, o el daño toca las constantes de su tag).

**Unidades recuperadas** (`unit_recovery` por motor, solo informativa; no entra en la nota): las clases, scripts y acciones intactos para los que el motor emitió fuente verificado, entre las unidades intactas, media por archivo con algo intacto. Las sumas (`recovered_units` de `intact_units`) van en cada motor.

### La nota

**Robustez = media de los desenlaces** (recuperó 100, parcial 60, error claro 40, caída 0, cuelgue 0) sobre los archivos en que el motor se midió, de 0 a 100 (`py_swf/bench_results.robustness_score`). Un motor que no lee el lenguaje de un archivo (pyflashkit solo AS3, Flare solo AS2) no se mide en ese archivo, y solo se ordena con motores medidos sobre los mismos archivos. Gana el primero si su ventaja es de más de un archivo (la ventaja en nota por el número de archivos entre 100 es mayor que 1). Cada motor lleva además el desglose por tipo de daño (`by_kind`), la mediana de segundos por archivo (el trabajo del motor en abrir y recorrer el archivo, sin arrancar el proceso de medida) y el **pico de memoria** de todo el árbol de procesos (`peak_memory_mib`, memoria comprometida máxima del trabajo de Windows; incluye el worker de Python de la medida, unos 40 MiB, y el JVM de JPEXS). El tiempo y la memoria se miden con hasta 4 procesos a la vez; no puntúan.

## Fidelidad por unidad (solo descompiladores)

La tabla de descompiladores juzga el SWF entero: el proyecto que emite el motor tiene que recompilar y Ruffle imprimir la misma traza. Aquí cada unidad con fuente verificado se juzga **sola**:

1. se compila **solo** su fuente con la ruta de edición de una unidad de PySWF (`py_swf/unit_editing.edit_unit` en modo fuente, la API que hay detrás de la operación `edit`): el compilador es el mismo, e independiente del motor, para todos;
2. los bytes de esa unidad se empalman en el SWF original y todo lo demás queda como estaba (una prueba comprueba que ninguna otra unidad cambia de bytes);
3. Ruffle ejecuta el resultado (en lotes, en procesos aparte, con plazo) y se compara su traza con la del original.

Se reutiliza la salida que el banco ya produce: el trabajo de descompilación de cada motor guarda también el fuente por método (`source.methods`) de las unidades que se van a juzgar, sin una segunda pasada salvo RustSWF, que sirve por método con su worker aparte de las clases.

### Unidades

| Tipo | Qué se empalma | Quién la tiene |
|---|---|---|
| `method` | el cuerpo de un método de AS3, solo | motores que emiten fuente por método |
| `class` | a la vez todos los métodos editables de una clase de AS3 para los que el motor emitió fuente por método (la clase cuenta si el motor emitió el documento de la clase con fuente verificado) | PySWF, RustSWF, JPEXS, pyflashkit |
| `action` | el código entero de una acción de AS2 (fotograma, `init`, botón o clip); las funciones anidadas viajan dentro | todos los que leen AS2 |

La ruta de edición espera el cuerpo del método, no su declaración: el fuente de un motor que viene como `public function add(param1:int) : int { … }` se lee como declaración y se empalma su cuerpo; sus parámetros declarados pasan a ser alias de los registros que la ruta enlaza (`var param1:int = local1;`), y un comentario inicial (GoSWF y AsmSWF abren con una cabecera) se ignora. Es toda la adaptación: no se repara ningún cuerpo ni se traducen los nombres de registros de un motor (`loc1`, `_arg_1`), así que un motor que usa nombres que ni su fuente ni el compilador conocen se queda en `different` o `no_compile`. Cuentan solo las unidades con **identidad y bytecode verificados** contra el inventario del SWF (`generation == available`; un P-code no es fuente).

### Desenlaces

| Desenlace | Cuándo |
|---|---|
| `same` | Ruffle imprime la traza del original |
| `different` | Ruffle imprime otra traza |
| `no_compile` | la ruta de edición no tomó el fuente (error de sintaxis, un constructor sin `super(...)`, una forma que el compilador no admite…) |
| `not_run` | la traza del caso nunca ejecuta la unidad |

**Unidades que no se ejecutan.** Una unidad que nunca corre no puede mostrar ninguna diferencia, y contarla como `same` inflaría la nota. Antes de juzgar un caso se reconstruye cada unidad **original** con una traza marcadora al principio (en P-code, la misma ruta de edición en modo exacto) y se ejecuta con la traza esperada: si la marca sale, la traza ya no coincide y la unidad corre; si la traza coincide, la unidad no se ejecuta. Es independiente del motor y se calcula una vez por caso. Una unidad que compila y no se ejecuta es `not_run` y queda **fuera del denominador**; una que no compila sigue dentro, se ejecute o no. Una clase se ejecuta si se ejecuta alguno de los métodos que se empalman; si no se pudo sondear una unidad, se juzga normalmente.

**Fuera de alcance.** La ruta de edición rechaza por motivos que no dependen del motor algunos métodos: cierres con activación, espacios de nombres privados o explícitos, multinombres de ejecución, tipos ocultos por un miembro. Esas unidades no se juzgan para ningún motor ni cuentan contra ninguno: se cuentan aparte (`out_of_scope` de la tabla). La aplicación protege además los constructores y los inicializadores (no puede probar que la inicialización de la clase sobreviva); el banco, que decide por la traza, pide a la misma ruta que los admita (`allow_initializers`, falso por omisión), de modo que un constructor sí se juzga, y debe conservar su `super(...)`.

### La nota

**Fidelidad = misma / (unidades evaluadas − no se ejecutan)**, es decir, `same / (same + different + no_compile)`, sobre todas las unidades de todos los casos (`unit_fidelity` en `bench_results.py`); `null` si ninguna unidad se pudo juzgar. Cada línea lleva los recuentos (`same`, `different`, `no_compile`, `not_run`, `evaluated`, `judged`) y el desglose por tipo de unidad (`by_kind`); en el índice, un motor medido sin ninguna unidad juzgada puntúa 0. Se evalúan como mucho `--unit-cap` unidades por caso (20 en el modo rápido, todas en el completo): se eligen del inventario del propio SWF, ordenadas y repartidas de modo uniforme, **las mismas para todos los motores**. Los motores se comparan solo sobre los casos en que ambos se midieron; la unidad no emitida por un motor no es una unidad evaluada de ese motor (eso lo mide «Unidades con fuente»). Gana el primero si su ventaja es de más de una unidad.

Un motor que emite menos unidades pero acierta tiene fidelidad alta: la nota mide cuánto de lo emitido es fiel, no cuánto emite; la cobertura está en «Unidades con fuente» y las dos entran en el índice. Los constructores y los inicializadores que el motor escribe sin `super(...)` son un `no_compile` honesto de la ruta compartida, no una decisión del banco; los campos que un motor saca del constructor a la declaración del campo se pierden al compilar el constructor solo.

## Velocidad

Cada fila de `results.json` lleva `seconds` (tiempo del paso propio del motor en ese caso, `null` si no terminó) y cada motor `median_seconds`, la mediana de segundos por caso, con `timed_cases` casos con tiempo.

- Descompiladores: desde que se le entrega el SWF al motor hasta que devuelve el fuente de todas las unidades. Incluye arrancar su proceso (JPEXS arranca una JVM; PySWF corre dentro del proceso de la medición y cuenta su análisis del SWF). No incluye recompilar ni la ejecución en Ruffle.
- Robustez: desde que se le entrega el archivo dañado hasta que el motor ha recorrido sus unidades (la construcción de la sesión de PySWF y la carga de módulos de Python quedan fuera). Un cuelgue cuenta el plazo entero.
- Compiladores, desensambladores, conversores y extractores: el tiempo de la herramienta (compilar, desensamblar y reensamblar, SWF a XML a SWF, extraer), nunca el de la comprobación en Ruffle.
- Menos es mejor. Un motor que falla pronto también sale rápido: la velocidad se lee junto al Índice Flash (el diagrama de cercanía frente a velocidad los pone juntos) y no es un criterio de ganador.

## Formato de results.json

`schema_version` 2 añade `seconds`, `measured` (`false` si el motor no se ejecutó: no instalado o no lee ese lenguaje), `units_total` y `units_with_source` a las filas de descompiladores, `seconds` a las demás, y a cada motor de cada tabla `median_seconds`, `timed_cases`, `comparable` y, en descompiladores, `units_total` y `units_with_source` (sumas), `unit_coverage` (media por caso) y `flash_index`; cada tabla lleva `corpus_cases`. `cases` pasa a contar solo los casos medidos del motor.

`schema_version` 3 añade:

- `tables.robustness` y `rows.robustness`: por motor, `outcomes` (cuántos archivos acabaron en cada desenlace), `robustness` (la nota), `by_kind` (`cases`, `outcomes` y `score` por tipo de daño), `recoverable_cases`, `intact_units`, `recovered_units`, `unit_recovery`, `peak_memory_mib`, `median_seconds`, `comparable`; la tabla lleva `kinds` (el nombre de cada tipo de daño). Cada fila (motor, archivo dañado) trae `kind`, `outcome`, `reason`, `seconds`, `peak_memory_mib`, `intact_units`, `recovered_units`, `intact_methods`, `recovered_methods` y `reported`.
- `tables.unit_fidelity` y `rows.unit_fidelity`: por motor `same`, `different`, `no_compile`, `not_run`, `evaluated`, `judged`, `fidelity` y `by_kind` (los mismos recuentos y `fidelity` para `class`, `method` y `action`); la tabla lleva `out_of_scope` (`units` y `reasons`) y `unit_cap`. Cada fila (motor, caso) trae `units`, `by_kind` y hasta 12 `details` (unidad, tipo, desenlace y motivo) de las que no acabaron en `same`.
- en `tables.decompilers`: `index_components` (los términos del índice de la corrida) y, por motor, `unit_fidelity`, `unit_fidelity_judged`, `unit_fidelity_cases`, `robustness` y `robustness_cases`; `flash_index` se calcula con esos términos.
- en el nivel superior, `options` (`unit_cap`, `robustness_timeout`, `workers`).

Las corridas de los esquemas 1 y 2 se leen igual (`read_bench` rellena esos campos con `null` donde la corrida no midió nada, y sin `index_components` el índice es el de cuatro términos) y la ventana dice «Sin datos en esta corrida» en los gráficos y en las secciones de robustez y fidelidad por unidad, que no existen en esas corridas.
