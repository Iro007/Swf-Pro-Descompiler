# Flash Universal Studio

Suite local de descompilación, edición y compilación SWF/Flash para Windows. Entrega siguiente 0.3.0. Una ventana Tauri/React reúne los motores RustSWF y PySWF y módulos opcionales de JPEXS, Flex, Haxe/HaxeFlixel y herramientas especializadas. Los nombres visibles cambian; los IDs del protocolo y proyectos existentes mantienen compatibilidad.

## Abrir la suite

Ejecuta dist/FlashUniversalStudio/FlashUniversalStudio.exe (la carpeta única que genera build.ps1). El ZIP portable es dist/FlashUniversalStudio-portable.zip: extrae toda la carpeta y conserva FlashSuiteWorker.exe, rustswf.exe, WebView2Loader.dll y licenses junto al programa.

Los motores nativos incluyen su runtime; no necesitan Python, Rust, Node ni Java instalados en el equipo. Windows 10/11 x64 y WebView2 Runtime son los requisitos. Los motores externos conservan sus propios requisitos y se configuran en **Herramientas → Motores…**. Desde la carpeta compartida, flash-studio.ps1 abre la suite por defecto.

## Ventana única

La aplicación es una sola ventana con el aspecto de JPEXS: menú, barra de herramientas, **árbol del SWF** a la izquierda y un **panel de contenido** a la derecha que cambia según el elemento elegido. No hay un explorador aparte ni una mesa de trabajo con estado propio: todo trabaja sobre la misma copia de trabajo de la suite Python (RustSWF sigue disponible como motor).

- **Árbol:** Cabecera, Formas, Formas morf., Sprites (con sus cuadros y etiquetas), Textos, Imágenes, Películas, Sonidos, Botones, Fuentes, Datos binarios, Cuadros, Otros y Scripts. Las carpetas vacías no aparecen. En Scripts, las clases AS3 se agrupan por paquete; AS1/AS2 se agrupa por cuadros, sprites, botones e inicializadores. Si el proyecto conserva fuentes aportadas aparece también *Fuentes del proyecto*. Incluye filtro rápido (Ctrl+F; también encuentra clases por el nombre de un método), expandir/contraer todo y el teclado de un árbol (flechas, Inicio/Fin, Intro). El ancho del árbol se recuerda.
- **Scripts:** fuente descompilada a la izquierda y P-code (solo lectura) a la derecha, con resaltado de sintaxis. Una clase se muestra completa y sus miembros (constructor, inicializador estático, métodos…) se eligen debajo, como los traits de JPEXS. El selector de motor ofrece *Automático* y los motores configurados; *Comparar motores* enseña la salida de cada uno. El texto es editable cuando el motor lo admite: **Aplicar y validar** valida el candidato y lo incorpora al historial; los borradores se conservan al cambiar de elemento o de motor. Con JPEXS se edita el documento completo de la clase.
- **Imágenes, formas, sprites, textos y fuentes:** vista previa estática (zoom, tamaño real, fondo claro/oscuro/transparente), propiedades y edición de recursos (sustituir imagen o audio, texto, FPS, vértices de formas y glifos, propiedades de fuentes) y bytes. Para sprites, cuadros y la película completa se ofrece Ruffle bajo petición.
- **Hex:** páginas de 1 a 64 KiB de cualquier tag, o del archivo completo desde la cabecera.
- **Cabecera:** firma y compresión, versión, longitud, escenario, velocidad (editable), cuadros, tags, ActionScript 3 y metadatos.
- **Menús:** *Archivo* (abrir SWF o proyecto, guardar proyecto, guardar SWF como…, exportar, cerrar), *Edición* (deshacer, rehacer, validar), *Ver* (tema del sistema, claro u oscuro), *Herramientas* (compilar, motores, benchmarks, ensamblado ABC) y *Ayuda*. Guardar nunca sobrescribe: el original y los archivos existentes no son destinos.
- **Compilar:** AS1/AS2 nativo o JPEXS; AS3 nativo o Flex; Haxe y HaxeFlixel; recompilación de las fuentes guardadas del proyecto. Compilación y reproducción son acciones separadas.
- **Exportar:** paquete ZIP de recursos, scripts, SWF XML y FLA/XFL mediante JPEXS.
- **Motores:** capacidades, disponibilidad y rutas locales; añade el estado de los motores que solo mide el banco de pruebas. Los proveedores comerciales usan instalaciones del usuario y flujo asistido.
- **Benchmarks:** campañas de `medir-codigo.ps1`. La tabla de descompiladores añade **% cerca de JPEXS** (parecido medio del texto con el de JPEXS) y **% de unidades de JPEXS** (cuántas unidades de JPEXS emite también el motor); «—» indica que no hay medida. No miden corrección: el ganador se decide solo por comportamiento.

Las ediciones de bytecode y recursos validan y reabren un candidato antes de modificar el historial. Guardar fuentes aportadas modifica el texto del proyecto y deja `source_pending` hasta recompilar; comprueba la revisión del SWF y el hash del texto. Ambos tipos de cambio tienen undo/redo. El original y archivos de salida existentes no son destinos implícitos. Una revisión obsoleta se rechaza sin perder el borrador.

## Compiladores y módulos

RustSWF conserva la identidad URI de namespaces públicos (`Namespace`/`PackageNamespace`) mediante declaraciones y selectores AS3 calificados. Acepta conjuntos de namespaces solamente si sus entradas públicas equivalen a la misma URI. Los nombres runtime requieren una clave primitiva probada y una constante de namespace pública. Private, PackageInternal, protected, atributos XML y conjuntos ambiguos permanecen P-code: no se convierten en propiedades públicas. La fuente calificada incluye sus declaraciones de namespace y sigue siendo un fragmento de método, no una clase completa.

La reconstrucción añade `lookupswitch` cuando sus brazos separados retornan o lanzan una excepción; conserva coerción signed-int, default y casos con destino compartido. Fallthrough/joins complejos y handlers try/catch/finally todavía requieren P-code.

El escritor RustSWF reescribe la envoltura FWS/CWS/ZWS y vuelve a leer el resultado. La imagen FWS normalizada debe ser idéntica byte por byte: tags desconocidos/anidados, bytecode y metadata ABC se conservan en crudo, incluido el encoding original de metadata. No cambia la versión SWF para hacer posible una compresión incompatible y no es un compilador ActionScript.

    rustswf capabilities
    rustswf abc_metadata archivo.swf tag:4/method:0
    rustswf roundtrip archivo.swf salida_nueva.swf zws

`roundtrip` requiere un destino nuevo y respeta el límite de entrada de 100 MiB al reabrir. `original` conserva exactamente los bytes de entrada; FWS/CWS/ZWS cambia sólo compresión/cabecera. `abc_metadata` expone claves/valores e índices junto al hash del encoding original. `capabilities` también está disponible mediante JSONL antes de abrir un archivo.

PySWF utiliza Python para recompilar cuerpos y proyectos AS3 en el subconjunto verificado: clases/paquetes en varias fuentes, imports internos, herencia, interfaces y overrides compatibles, y clase de escenario Sprite/MovieClip. La campaña CLI añade casos dirigidos de closures vivas, recorridos `for in`/`for each`, herencia y scopes de excepciones en una VM acotada. Continúan límites en variantes generales de scopes/closures, E4X, inicializadores complejos y proveedores externos. RustSWF utiliza Rust para inspeccionar FWS/CWS/ZWS, inventariar AVM1/AVM2, reconstruir un subconjunto y extraer recursos; la edición se delega a PySWF. Los identificadores internos permanecen compatibles con los proyectos anteriores.

Perfil Haxe: 4.3.7. Perfil HaxeFlixel: Flixel 6.2.0, OpenFL 9.5.2 y Lime 8.3.2 en Haxelib aislado. Project.xml, fuentes y assets se compilan en staging con lime build flash. Haxe recompila fuentes originales; decompilar SWF muestra AS3/P-code y recursos, sin recuperar automáticamente el .hx original.

El módulo JPEXS usa una clase de aplicación separada del bridge de benchmarks. Los SDKs/ejecutables se registran por SHA-256. Para preparar módulos desde el proyecto Python hermano:

    .venv\Scripts\python.exe tools\setup_suite_tools.py --haxe --flixel --flex --jpexs-bridge

Versiones, procedencia y hashes quedan en ../vendor/toolpacks/lock.json. Los packs no se incluyen automáticamente en el portable nativo. Flasm, Flare, MTASC, swfmill, SWFTools y proveedores comerciales requieren un ejecutable compatible configurado. RABCDAsm importa/exporta ABC estructural y permite ensamblado validado.

## Compilar y comprobar

Primero, desde py_swf_editor:

    .venv\Scripts\python.exe -m PyInstaller packaging\suite_worker.spec --noconfirm

Después, desde rust_swf:

    .\build.ps1

JSONL v1 une motores. Tauri gobierna procesos y cancelación mediante Windows Job. Límites: 30 s/1 GiB normalmente; compilación 120 s/2 GiB; benchmark 600 s/2 GiB. FLASH_SUITE_TIMEOUT_SECONDS, FLASH_SUITE_COMPILE_TIMEOUT_SECONDS, FLASH_SUITE_BENCHMARK_TIMEOUT_SECONDS y FLASH_SUITE_MEMORY_MIB ajustan presupuestos acotados. El Job limita recursos; no es un sandbox de seguridad completo.

CLI compartida: ../engines/pyswf/tools/flash_suite_cli.py y entrada de fuente `flash-suite`. La consola empaquetada usa `FlashSuiteWorker.exe cli OPERACIÓN [opciones]`, con alias `PySWF.exe` y wrapper `pyswf.ps1`. Ofrece operaciones de fuentes, recursos, historial, proyectos y RPC sin abrir la GUI, además de inspección, compilación, validación, exportación y campañas. Usa identidades del inventario del archivo actual.

Las ediciones por consola requieren `--project-output` o `--state-dir` para conservar la sesión. `source-files` obtiene las identidades de las fuentes; `source-edit` guarda texto y `compile-saved` recompila lo guardado. Los destinos de compilación/exportación deben ser nuevos. `--help` muestra los argumentos; la [guía de consola](../engines/pyswf/docs/SUITE_CLI.md) incluye ejemplos de compilación, recursos, proyectos e historial.

La campaña 0.3.0 pasó 788 pruebas Python con una omisión en 91,62 s; se omitió un corpus público fijado que no estaba disponible y se registró un aviso de deprecación de Starlette. RustSWF pasó 85 pruebas del núcleo y ocho del supervisor; una integración opcional del host permaneció ignorada. Los SDKs pasaron cinco casos de compilación/recompilación Haxe, Flex y HaxeFlixel; los módulos pasaron ocho casos AS2, RABCDAsm y exportación. El compilador pasó seis casos en una VM independiente acotada. TypeScript y el bundle también pasaron. Los resultados se conservan por suite y no se suman como una medida global de compatibilidad.

El [informe consolidado de entrega](../reports/headless-verification-0.3.0.json) reúne estos resultados y las comprobaciones congeladas que se incorporan al finalizar el empaquetado. Los reportes fuente incluyen `reports/rustswf-source-only-summary.json`, `../engines/pyswf/reports/native-compiler-cli/verification.json`, `../engines/pyswf/reports/suite-integration/verification.json` y `../engines/pyswf/reports/suite-modules/verification.json`. Los reportes de UI/Ruffle de 0.2.0 se conservan como históricos; esta campaña no abrió GUI ni navegador y no usa capturas antiguas como prueba de los controles nuevos. Las exportaciones FLA/XFL no se abrieron en Animate.

El validador estricto actual rechaza dos métodos de un SWF AS3 grande del corpus y mantiene bloqueada su exportación completa. Esto no demuestra que Flash rechace el original. La [prueba P-code dirigida](../engines/pyswf/reports/native-functions-cli-review/verification.json) conserva el hash original y verifica edición/exportación/reapertura en una biblioteca aislada que contiene el ABC real seleccionado completo, sin omitir métodos de ese ABC. Su resultado queda limitado a esa biblioteca y a fixtures confiables, sin runtime Flash.

No se declara paridad ni superioridad general sobre JPEXS. PySWF, RustSWF y Ruffle tienen compatibilidad acotada. Permanecen pendientes familias AS y recursos no compatibles, edición visual profesional, FLA/XFL abierto y verificado con Animate, y mediciones legítimas de herramientas comerciales. La validación comprueba contenedor, ABC, referencias y stack/scope soportados; no todo el verificador de tipos AVM2.

## Licencia

GPL-3.0-or-later. Se conservan procedencia y avisos de PySWF Studio, Ruffle, Tauri, WebView2 y dependencias. JPEXS/Java, SDKs, bibliotecas Flash y herramientas propietarias permanecen módulos locales separados. El corpus SWF no se distribuye.

