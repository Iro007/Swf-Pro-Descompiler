# Flash Universal Studio: implementación y pendientes

Entrega siguiente: **0.3.0**, con motores propios **PySWF** y **RustSWF**, Windows primero y GPLv3. Los IDs internos `native`, `python` y `rust` y los lectores de proyectos anteriores permanecen compatibles. JPEXS 26.3.0 funciona como módulo local opcional y referencia comparativa; no es requisito del motor propio. Este seguimiento distingue funciones implementadas, pruebas dirigidas y pendientes.

## 1. Fuente y compilación — cobertura parcial

PySWF reconstruye control de flujo y otros patrones AS1/AS2/AS3 compatibles, conserva diagnósticos/P-code cuando no puede demostrar una representación segura y recompila cuerpos o proyectos propios en su subconjunto. Los proyectos AS3 admiten varias fuentes, imports internos, herencia, interfaces y overrides compatibles, y clases de escenario Sprite/MovieClip. La campaña CLI comprueba casos dirigidos de `for in` AS2, `for each` AS3, closures vivas, herencia y scopes de excepciones en una VM acotada. AS1/AS2 dispone de perfiles propios y JPEXS; Flex, Haxe y HaxeFlixel se integran como compiladores locales configurados.

RustSWF inventaría AVM1/AVM2 y reconstruye un subconjunto. Conserva namespaces públicos calificados y conjuntos de la misma URI; private/internal/protected y conjuntos ambiguos mantienen P-code. Reconstruye `lookupswitch` terminales con brazos separados y coerción signed-int probada. Su escritor conserva la imagen FWS normalizada al cambiar la envoltura FWS/CWS/ZWS; no compila ActionScript.

**Pendiente:** ampliar familias AS y variantes generales de scopes/closures, E4X, inicializadores complejos, excepciones solapadas, joins con efectos y control irreducible. Mantener pruebas de binding e inicialización; no transformar un namespace privado en público para aumentar cobertura. Fuente reconstruida, fuente compilable y comportamiento equivalente son resultados distintos.

## 2. Fuentes, proyectos e historial — implementado con límites

`.pyswfproj` v3 conserva original, copia de trabajo, fuentes, revisiones, toolchains y recuperación; lee v1/v2. Los cambios validados y los cambios únicamente de fuentes comparten undo/redo. Las fuentes aportadas AS/Haxe/HXML/Project.xml se pueden editar y guardar; `source_edit` comprueba revisión del SWF y hash del texto, y `compile_saved` recompila las fuentes guardadas en staging propio hacia un nuevo SWF. `source_revision` y `source_pending` distinguen texto guardado de bytecode actualizado. Los borradores se conservan ante errores y al cambiar de panel.

**Pendiente:** ampliar mapas línea–bytecode e identidades ante transformaciones arbitrarias entre múltiples ABC, mejorar la recuperación de proyectos con dependencias externas ausentes y cubrir más variantes de proyectos. Conservar archivos `.hx` aportados no equivale a recuperarlos desde un SWF.

## 3. Recursos y reproducción — edición estructurada parcial

La suite expone edición de imágenes, DefineSound compatible con MP3/WAV, DefineEditText, FPS, vértices de formas y glifos, y propiedades/métricas de fuentes compatibles. `resource_info` devuelve tipo y estructura; `resource_edit` valida el candidato y conserva historial. Las formas usan píxeles (0,05 px por twip); los glifos mantienen las unidades originales de su fuente. El visor muestra contornos y selección por índice, sin ejecutar ActionScript.

Timeline muestra display list/SVG estático. Preview ejecuta explícitamente un Ruffle local con red y acceso JavaScript de la película desactivados. La inspección estática y la ejecución son acciones separadas.

**Pendiente:** herramientas visuales profesionales de transformación/arrastre, edición completa de controles de curvas, fills/strokes/gradientes, timeline avanzado, máscaras/filtros/blend modes y variantes antiguas o no compatibles de recursos. No equiparar parseo, contorno o una escena de prueba con render fiel de todas las películas.

## 4. Módulos, exportación y distribución — capacidades acotadas

Tauri reúne procesos RustSWF/PySWF y módulos externos. Los workers tienen límites/cancelación y recuperación; Windows Job acota recursos y no constituye un sandbox completo. RABCDAsm exporta/importa ABC estructural; otras herramientas especializadas se habilitan por operación. Los proveedores comerciales son asistidos y requieren una instalación/licencia del usuario; los instaladores no acreditan disponibilidad ni calidad del motor.

El paquete de recursos ZIP se identifica como tal. JPEXS puede producir scripts, XML y archivos FLA/XFL: **abrirlos y verificarlos en Animate sigue pendiente**. No presentar un paquete JSON como FLA ni una exportación como proyecto ya verificado en Animate.

**Pendiente:** campañas legítimas y comparables con Sothink/Trillix/ASV/Sorcerer, firma/actualizaciones/instalador, sandbox más estricto, cargas grandes medidas, Linux/macOS y servicio multiusuario autenticado. No restaurar descargas bloqueadas ni activar/comprar licencias automáticamente.

## 5. Evidencia y campaña sin GUI

La campaña 0.3.0 usa comprobaciones de fuente/CLI, sin nuevas ventanas, navegador, CDP ni Playwright. La interfaz pasó `npm run lint` y `npm run build`; esto valida TypeScript/bundle y no es prueba gráfica de los controles nuevos. La [guía de consola](SUITE_CLI.md) describe cómo repetir operaciones de fuentes, recursos, compilación y proyectos sin GUI. Las suites dirigidas de fuentes guardadas, recursos y fuentes tipográficas están en `tests/test_suite_saved_sources.py`, `tests/test_suite_resource_documents.py` y `tests/test_suite_font_documents.py`.

Resultados finales de fuente/CLI, conservando el alcance de cada suite:

- Python: **788 passed, 1 skipped**, en 91,62 s. La omisión corresponde a un corpus público fijado no disponible; se registró un aviso de deprecación de Starlette.
- RustSWF: **85 pruebas del núcleo y ocho del supervisor aprobadas**; una integración opcional del host permaneció ignorada. Clippy pasó. El [reporte fuente](../../rust_swf/reports/rustswf-source-only-summary.json) también registra tres casos CLI opcionales de namespaces con Flex; corresponde al código probado antes del incremento de versión de empaquetado.
- SDKs: **5/5**, Haxe, Haxe desde fuentes guardadas, Flex, HaxeFlixel y HaxeFlixel desde fuentes guardadas, en [suite-integration/verification.json](../reports/suite-integration/verification.json). Compilación y validación no acreditan comportamiento de runtime.
- Módulos: **8/8**, AS2 PySWF/JPEXS, RABCDAsm y exportaciones de recursos, scripts, XML, FLA y XFL, en [suite-modules/verification.json](../reports/suite-modules/verification.json). FLA/XFL se produjo sin abrir Animate.
- Compilador: **6/6** en una VM independiente acotada, en [native-compiler-cli/verification.json](../reports/native-compiler-cli/verification.json); sin GUI, navegador ni runtime Flash.

El [informe consolidado 0.3.0](../../reports/headless-verification-0.3.0.json) reúne la campaña y las comprobaciones congeladas que se incorporan al finalizar el empaquetado. No sumar suites distintas o ejecuciones parciales como una medida global de compatibilidad.

El validador estricto actual rechaza dos métodos de un SWF AS3 grande del corpus: un rango/target de excepción y una unión de alturas stack/scope. La exportación completa sigue bloqueada y el hash del original permanece intacto. No se concluye que Flash rechace esa película. En [native-functions-cli-review/verification.json](../reports/native-functions-cli-review/verification.json), la edición P-code AS3 se verifica mediante una biblioteca aislada que conserva el ABC real seleccionado completo, sin omitir métodos de ese ABC. El resto de tags se compara dentro de esa biblioteca; no se presenta como preservación de la película original completa. El AS2 de ese informe sí se prueba sobre su SWF original completo.

Los reportes de UI/Ruffle de la entrega anterior se conservan como históricos. Las pruebas congeladas de `reports/suite-integration/frozen-worker.json` acreditan su alcance registrado, no controles nuevos ni compatibilidad general con Animate.

Las campañas comparativas conservan corpus, unidad, versión/hash, fallos, no-mapeados, operación y perfil. Separar parseo, fuente, sintaxis, compilación, reapertura y comportamiento; tiempos con distinta cobertura no son comparables como una victoria. La matriz documental no declara paridad ni superioridad general.
