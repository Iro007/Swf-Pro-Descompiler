# Benchmarks

Una pregunta por categoría, una tabla por categoría; nunca se mezclan cosas que no son comparables. Todas usan el mismo oráculo: Ruffle imprime la traza esperada del corpus.

| Categoría | Pregunta | Motores |
|---|---|---|
| Descompiladores | ¿lo que emite recompila (Flex/Rascal) y se comporta igual? | PySWF, RustSWF, JsSWF, GoSWF, AsmSWF, JPEXS, pyflashkit, Flare |
| Compiladores | ¿compilan el proyecto del corpus y se comporta como se espera? | Flex, Rascal, Haxe, compilador de PySWF |
| Desensambladores | ¿desensamblar y reensamblar conserva el comportamiento? | RABCDAsm (AS3), Flasm (AS2); los que solo listan lo dicen |
| Conversores XML | ¿SWF a XML a SWF conserva el comportamiento? | swfmill |
| Extractores | ¿todos los recursos se extraen y decodifican? | swfextract |

```powershell
.\medir-codigo.ps1            # rápido: un caso por familia de desarrollo (~6 min)
.\medir-codigo.ps1 -Completo  # todos los casos de desarrollo y validación
.\medir-codigo.ps1 -Motores native rust   # solo estos descompiladores (JPEXS se mide siempre: es la referencia de cercanía y compite como los demás)
```

- Corpus fijo: `corpus/parity-20261014` (sellado con hashes; con fuente original y traza esperada).
- Salida: `reports/bench/<ejecución>/results.json` (decenas de KB) e `index.json`. La ventana Benchmarks de la app (Herramientas, Benchmarks) lo lee y lo dibuja como una clasificación: tarjetas destacadas, el Índice Flash, una gráfica de barras ordenadas por métrica, el diagrama de cercanía frente a velocidad y, plegada, la tabla completa con los motivos.
- Cada celda sin número lleva su motivo (no instalado, no lee ese lenguaje, fuente parcial…). No se declara ganador si la ventaja es de un caso o menos, ni entre motores medidos sobre casos distintos: esos motores se muestran aparte, sin número de puesto.
- Pendiente: haxeflixel (necesita lime), ida y vuelta de PySWF desde P-code, corpus más grande con más recursos.

## Cercanía a JPEXS (solo descompiladores)

Además de las cuentas de comportamiento, cada descompilador lleva dos números frente a JPEXS, que actúa de referencia: **cerca JPEXS** es la similitud media del texto generado con el de JPEXS, unidad por unidad (se ignoran comentarios, espacios, comillas y `1` frente a `1.0`), y **cubre JPEXS** es la fracción de las unidades de JPEXS que el motor también emitió. Solo se comparan unidades cuyo `document_id`, ámbito y hash de bytecode coinciden con el inventario canónico del SWF; las que no verifican se excluyen y se cuentan. Es una medida de parecido, no de corrección: JPEXS también se equivoca, y un texto distinto puede comportarse igual. **El ganador de la tabla sigue decidiéndose solo por comportamiento** (recompila y misma traza en Ruffle); el Índice Flash, de abajo, ordena la clasificación pero no lo reemplaza. Si JPEXS no está instalado o falla en un caso, esas celdas quedan en blanco con su motivo (`jpexs_reason` en `results.json`); la línea de JPEXS vale 1,0 por definición y se marca `reference: true`. En las gráficas de cercanía y de cobertura JPEXS es la barra de referencia; en todo lo demás compite como cualquier motor.

## Índice Flash (solo descompiladores)

Una nota de 0 a 100 por motor, la que ordena la clasificación de la ventana Benchmarks. JPEXS compite en ella como los demás:

**Índice Flash = (fuente + recompila + misma traza + unidades con fuente) / 4**, cada término en porcentaje.

- **Fuente**: `source / cases`, los casos en que el motor emite fuente completo de todas las unidades del SWF.
- **Recompila**: `recompiles / cases`, los casos en que ese fuente compila con un compilador independiente (Flex para AS3, Rascal para AS2).
- **Misma traza**: `same_trace / cases`, los casos en que, además, Ruffle imprime la misma traza que el original.
- **Unidades con fuente**: `unit_coverage`, la media por caso de unidades con fuente verificado entre unidades del SWF. Las unidades son las clases, scripts y acciones del inventario ABC y AVM1 del propio SWF (`canonical_inventory`), no las de otro descompilador. Una unidad cuenta si el motor la emitió con su identidad y el hash de su bytecode, aunque el proyecto no llegara a recompilar; los métodos y las funciones anidadas viajan dentro de su clase o acción y no se cuentan aparte. Cada caso pesa lo mismo: un SWF grande no ahoga a los pequeños.

Los tres primeros se calculan sobre los casos en que el motor se midió (`cases`). Ninguno de los cuatro usa la salida de otro descompilador, así que se miden igual en todos los motores. Por eso la cercanía a JPEXS y la cobertura de JPEXS, que valen 100 % para JPEXS por definición, no entran en el índice y tienen sus propias gráficas.

Reglas, todas en `py_swf/bench_results.py` (la misma función la usan `tools/bench.py` al escribir y el lector al abrir corridas antiguas):

- JPEXS corre una sola vez por caso: ese trabajo es su propia fila (fuente, recompila, misma traza, unidades y tiempo) y, a la vez, la referencia con la que se mide la cercanía de los demás. Compite por el índice, por el `winner` y por las tarjetas destacadas como cualquier motor; solo en «Cercanía a JPEXS» y «Cobertura de JPEXS» es la barra de referencia (100 % por definición, rayada y sin número de puesto) y no puede ser «el más cercano a JPEXS».
- Un motor sin casos medidos, o una corrida anterior al recuento de unidades (sin `unit_coverage`), no tiene índice: `flash_index` es `null` y la ventana dice «Sin datos en esta corrida» en lugar de calcularlo con tres cuartos.
- Solo se ordenan juntos los motores medidos sobre exactamente los mismos casos (`comparable: true`: el mayor grupo de motores con el mismo conjunto de casos medidos, de dos o más). Un motor que no lee un lenguaje (pyflashkit solo AS3, Flare solo AS2) o que no está instalado queda aparte con `comparable: false`; su porcentaje sobre otros casos no se compara con el de los demás.
- Es una nota compuesta: fuente, recompila y misma traza están anidadas (cada una incluye a la siguiente), así que el comportamiento pesa más que la cobertura de unidades. El primero del índice puede no ser el `winner` por comportamiento; las dos cosas se muestran.
- Las demás categorías no tienen índice: su métrica principal es el porcentaje de casos correctos (`ok / cases`), ordenado con la misma regla de casos comparables.

## Velocidad

Cada fila de `results.json` lleva `seconds` (tiempo del paso propio del motor en ese caso, `null` si no terminó) y cada motor `median_seconds`, la mediana de segundos por caso, con `timed_cases` casos con tiempo.

- Descompiladores: desde que se le entrega el SWF al motor hasta que devuelve el fuente de todas las unidades. Incluye arrancar su proceso (JPEXS arranca una JVM; PySWF corre dentro del proceso de la medición y cuenta su análisis del SWF). No incluye recompilar ni la ejecución en Ruffle.
- Compiladores, desensambladores, conversores y extractores: el tiempo de la herramienta (compilar, desensamblar y reensamblar, SWF a XML a SWF, extraer), nunca el de la comprobación en Ruffle.
- Menos es mejor. Un motor que falla pronto también sale rápido: la velocidad se lee junto al Índice Flash (el diagrama de cercanía frente a velocidad los pone juntos) y no es un criterio de ganador.

## Formato de results.json

`schema_version` 2 añade `seconds`, `measured` (`false` si el motor no se ejecutó: no instalado o no lee ese lenguaje), `units_total` y `units_with_source` a las filas de descompiladores, `seconds` a las demás, y a cada motor de cada tabla `median_seconds`, `timed_cases`, `comparable` y, en descompiladores, `units_total` y `units_with_source` (sumas), `unit_coverage` (media por caso) y `flash_index`; cada tabla lleva `corpus_cases`. `cases` pasa a contar solo los casos medidos del motor. Las corridas con `schema_version` 1 se leen igual (`read_bench` rellena esos campos con `null` donde la corrida no midió nada) y la ventana dice «Sin datos en esta corrida» en los gráficos que no se pueden dibujar.
