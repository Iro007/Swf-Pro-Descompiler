# py_swf_editor — SWF Decompiler & Editor (Python)

En la suite compartida **Flash Universal Studio**, este motor se muestra como **PySWF** y el motor Rust como **RustSWF**. Los nombres visibles mantienen los IDs internos y los proyectos existentes. La entrega siguiente 0.3.0 incorpora edición de fuentes aportadas AS/Haxe/Project.xml con hashes y recompilación desde el proyecto guardado, además de edición estructurada de texto, audio, formas y fuentes compatibles. Consulta [seguimiento y pendientes](docs/IMPLEMENTATION_PLAN.md) y el [README de la suite](../README.md). Los apartados siguientes describen también la aplicación Python anterior y su evidencia histórica.

El compilador actual de la suite admite clases AS3 compatibles en varias fuentes, imports internos, herencia, interfaces, overrides y clase de escenario Sprite/MovieClip. El [reporte CLI](reports/native-compiler-cli/verification.json) comprueba seis casos de recorridos, closures, herencia y scopes de excepciones en una VM independiente acotada, sin GUI, navegador ni runtime Flash. Estos avances no convierten al compilador en una implementación general de ActionScript. La [guía de consola](docs/SUITE_CLI.md) documenta los flujos actuales sin interfaz; el [informe consolidado 0.3.0](../reports/headless-verification-0.3.0.json) reúne la verificación y el empaquetado final.

## Aplicación Python anterior y evidencia histórica

Las interfaces anteriores de PySWF (web React + FastAPI, ventana Qt `gui/`, ventana pywebview
`desktop.py` y el ejecutable `PySWFStudio.exe`) se retiraron en favor de **Flash Universal Studio**.
Su código queda en la etiqueta git `legacy-ui-final`. `server/` sigue disponible como API local
hasta que la sustituya el registro único de herramientas.

El motor predeterminado propio es **PySWF**. Abrir, editar y exportar no requiere
Java ni JPEXS.

El ciclo nativo `abrir → editar → exportar → volver a abrir` se verifica por API y navegador
con AS2/AS3 en FWS/CWS/ZWS. El código editado se construye en una copia y solo se añade al
historial tras validar y reabrir el candidato. Las ediciones AS3 recalculan `max_stack`,
`local_count` y `max_scope_depth`.

El compilador de **cuerpos de método**, no de clases completas, soporta variables y tipos
primitivos, asignaciones, llamadas como valores, `if/else`, `while`, `for`, `switch`,
`try/catch/finally`, `break/continue`, `return/throw`, operadores y cortocircuito `&&/||`.
`for` admite inicializadores/actualizaciones separados por comas y cláusulas vacías;
`switch` evalúa el selector una vez, compara estrictamente y conserva el fall-through.
`finally` se ejecuta también en salidas abruptas y conserva el valor de retorno previo.
El P-code AS3 usa `#índice` con comentarios
legibles para preservar exactamente las referencias a constantes y namespaces.

La reconstrucción de fuente y el compilador siguen siendo parciales: `for in`, `for each`,
closures, funciones anidadas y compilación general de clases quedan pendientes. Inicializadores,
constructores y bindings no predeterminados están protegidos en edición de fuente.
Los bloques AS2 generan ActionTry y los AS3 tablas de excepciones reales, incluyendo
capturas tipadas y bloques anidados. El P-code AS3 exporta directivas `.exception`
con etiquetas: el ensamblador recalcula sus offsets al editar. Si una edición P-code
omite esas directivas, solo se permite conservar los bytes de un método con excepciones.
El decompilador nativo reconstruye `for`, `switch` y `try/catch/finally` en los
patrones soportados de AS2/AS3, incluidos los emitidos por el compilador Python y
las variantes Flex/AIR verificadas del corpus. Conserva las caídas entre casos,
los saltos de control y los retornos a través de finally. Los joins se emiten una vez.
Un flujo ambiguo o un opcode no soportado devuelve diagnóstico y P-code: no se
presenta como fuente editable. La validación actual comprueba estructura y flujo de
pila/scope, no todos los tipos AVM2 ni el comportamiento de un runtime Flash completo.

El decompilador nativo también reconstruye funciones AS2 (`DefineFunction/2`),
closures AS3 con slots capturados, bloques `with`, literales de arrays/objetos,
enumeraciones AS3 `for in`/`for each` comunes y bucles con condición al final.
Estos últimos se expresan como `while (true)` con salida explícita cuando esa
forma conserva mejor el orden de evaluación. Las uniones de expresiones puras
se resuelven con temporales; no se adivinan uniones con efectos laterales.
**Reconstruir fuente no implica poder recompilarla**: closures, `with` y
enumeraciones siguen de lectura cuando el compilador nativo no admite su sintaxis.
Se mantienen las protecciones de namespaces privados y ámbitos ambiguos.

### Crear y editar programas AS3 propios

**Nuevo desde código** compila un primer subconjunto de paquetes/clases AS3 en
Python puro: clases `Object`, campos literales, visibilidades, constantes
estáticas, constructores, métodos, accesores, parámetros opcionales/rest y
metadatos. La fuente original se conserva en el proyecto y en undo/redo.
Puedes cambiar cuerpos y valores de campos; nombres, firmas y jerarquías
siguen protegidos. Reabrir únicamente el SWF no recupera la fuente original.
No es todavía un compilador AS3 general ni un reproductor de juegos.

```powershell
.venv\Scripts\python.exe -m py_swf.cli compile-classes Counter.as --output Counter.swf
```

Detalles y límites en [NATIVE_COMPILER.md](docs/NATIVE_COMPILER.md); seguimiento
del plan completo en [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).
La exportación de paquetes JSON/recursos no se anuncia como FLA/XFL: la ruta
`export/resource-bundle` genera `.resources.zip` y registra fallos de extracción.

Las pruebas de closures comparan bytecode real con ejecución independiente de
un subconjunto compatible con JavaScript (solo fixtures locales de confianza).
Esto es una verificación acotada, no un reproductor Flash ni un sandbox para
archivos subidos. El informe de cobertura registra cada diagnóstico pendiente,
sin confundir parseo, reconstrucción y equivalencia semántica:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_native_functions.py -q
.venv\Scripts\python.exe tools/native_source_coverage.py third_party/jpexs-decompiler/libsrc/ffdec_lib/testdata/as2/as2.swf third_party/jpexs-decompiler/libsrc/ffdec_lib/testdata/as3_new/bin/as3_new.flex.swf third_party/jpexs-decompiler/libsrc/ffdec_lib/testdata/as3_cross_compile/bin/as3_cross_compile.flex.swf
```

## Características

### Benchmarks y avance del decompilador

La CLI de desarrollo incorpora `pyswf benchmark <corpus> --profile smoke|full
--engines native,jpexs --output <directorio>`. Produce JSON y un informe HTML local
con tiempos fríos/calientes, RSS muestreado, cobertura y muestras de recompilación.
No confunde fuente disponible con equivalencia de comportamiento.

Consulta [hitos y limitaciones](docs/decompiler-milestones.md) para preparar las
referencias portátiles verificadas y repetir las mediciones. Java/JPEXS siguen
siendo exclusivamente herramientas de referencia: no se requieren en el programa.

- Parseo del contenedor SWF: FWS (sin comprimir), CWS (zlib) y ZWS (LZMA), con
  conservación de tags y reapertura. Recomprimir puede cambiar los bytes del
  contenedor sin cambiar su contenido; el original se conserva aparte.
- Shapes (DefineShape 1–4) → SVG.
- Imágenes: exportar/reemplazar DefineBitsLossless/2, DefineBitsJPEG2/3 (Pillow, sin Qt).
- Bytecode: desensamblador/ensamblador AVM1 (AS1/2, incl. DefineFunction/2 y With) y
  AVM2/ABC (AS3) con tabla de opcodes completa validada contra la especificación.
- Edición: modifica P-code o bytes en hexadecimal desde el navegador y guarda un SWF válido.
- Workspace PySWF Studio: explorador buscable, inspector lateral, visores con zoom y
  comparación Python/JPEXS en dos columnas; los errores de cada motor se muestran por separado.
- Proyectos: crear snapshots, comparar los tags con el trabajo actual, restaurar y
  deshacer/rehacer con actualización de los visores. El original se conserva en `.pyswfproj`.

## Workspace

Abre un SWF o proyecto desde **Abrir archivo** o arrástralo al área central. El explorador
permite filtrar por nombre, símbolo e ID; **Contenido AS / texto** consulta strings y
scripts del archivo. Desde el inspector puedes exportar un SWF y desde **Historial**
crear, comparar y restaurar snapshots. Las diferencias de snapshots se comparan por índice
de tag; una inserción puede desplazar los índices siguientes.

En ActionScript, **P-code** edita el bytecode del método seleccionado. **Editar fuente**
se habilita solo para secciones nativas con un método identificable; el esquema de clases
y la salida JPEXS permanecen de lectura. El compilador Python sigue teniendo cobertura
parcial y devuelve diagnóstico cuando no admite una construcción.

La edición nativa ahora identifica cada método/acción y comprueba que el SWF
no cambió desde que se abrió el borrador. Una edición obsoleta se rechaza sin
perder el texto. **Recuperar fuente guardada** restaura el texto escrito por el
usuario, separado de la decompilación. Los proyectos se guardan en formato v2
con fuentes y checksums; se mantiene lectura de v1 sin reescribirlo al abrir.
Arrays, objetos y el subconjunto seguro de corchetes ya pueden recompilarse.
Si la reconstrucción es incompleta pero el método es compilable, **Reemplazar cuerpo AS**
abre un editor vacío con advertencia: escribe el cuerpo completo, sin confiar en el
pseudo-código decompilado. Constructores, inicializadores y closures siguen protegidos.

Las pruebas nuevas (`tests/test_native_control_flow.py`) comprueban el bytecode emitido
con un intérprete acotado independiente del AST, además del guardado atómico,
la reapertura, el historial y los proyectos. No sustituyen las pruebas en un reproductor Flash.
Para repetirlas, sin Java/JPEXS:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Atajos: `Ctrl O` abre archivos, `Ctrl S` exporta el SWF, `Ctrl Shift S` guarda el proyecto
y `Ctrl Z` / `Ctrl Y` deshacen y rehacen. Dentro del editor de código, `Ctrl S` aplica
el P-code o fuente actual. El editor hexadecimal pagina 1024 bytes por vista para mantener
accesibles los payloads grandes.

## Uso

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  |  source .venv/bin/activate en Linux/macOS
pip install -r requirements.txt

# Interfaz gráfica: Flash Universal Studio, desde la raíz del repositorio
..lash-studio.ps1

# API local (headless) para desarrollo/automatización
python -m server --host 127.0.0.1 --port 8765 --no-browser
```

La CLI para automatización incluye inspección, validación, comparación,
reemplazo no destructivo y exportación por lotes:

```powershell
pyswf inspect movie.swf
pyswf validate movie.swf
pyswf decompile movie.swf --engine auto
pyswf compare movie.swf --output diff.json
pyswf export movie.swf out\images --type images
pyswf replace movie.swf --tag 3 --resource replacement.png --output edited.swf
pyswf edit-script movie.swf --tag 20 --body 1 --source patch.as --output edited.swf
pyswf batch .\input .\output
```

Para medir el avance sobre un corpus real:

```powershell
python tools/corpus_report.py third_party\jpexs-decompiler\libsrc\ffdec_lib\testdata `
  --output reports\corpus.json --compare
```

El informe registra validez, tags, scripts, tiempo, pico de memoria y, cuando
JPEXS está configurado, la proporción de líneas coincidentes entre motores.

## Tests

```bash
pip install pytest httpx
python -m pytest tests -q
```

Si clonas [JPEXS decompiler](https://github.com/jindrapetrik/jpexs-decompiler) en
`third_party/jpexs-decompiler`, la suite además hace round-trip sobre sus ~90 SWFs de
prueba reales (se omite automáticamente si no está).

## Estructura

- `py_swf/` — librería core (parser, shapes, imágenes, AVM1, AVM2) sin dependencias de UI.
- `server/` — API FastAPI (`/api/files/...`, `/api/benchmarks/...`).
- `tools/` — scripts de desarrollo, benchmarks y configuración de herramientas.

La interfaz gráfica es Flash Universal Studio (`../rust_swf`).

## Página de benchmarks

Está en la pestaña **Benchmarks** de Flash Universal Studio y en la página
`ui/analysis.html` (servida con la API local). No necesitas abrir un SWF.
La página muestra informes ya medidos; nunca arranca Java desde la interfaz.
Al volver al editor se conserva el archivo y su historial.

Incluye filtros por motor/archivo, barras de cobertura/latencia/memoria,
dispersión fuente–tiempo, datos por archivo, metodología, advertencias de carga
de fondo y descarga JSON resumida. También se genera un `benchmark.html`
autónomo en cada carpeta de resultados. No hay un índice de calidad ni ganador
global: fuente disponible, compilación y comportamiento son métricas distintas.

Los perfiles, los comandos para repetir mediciones y el alcance pendiente están
en [docs/decompiler-milestones.md](docs/decompiler-milestones.md).

## Referencia JPEXS y compatibilidad opcional

La distribución usa PySWF y no incluye Java/JPEXS. La referencia de
benchmarks usa JPEXS fijado a 26.3.0 solo en desarrollo, con un JDK portátil
aislado; no requiere configurar el Java global. El adaptador opcional anterior
se conserva por compatibilidad y no es el adaptador de medición.

El proyecto usa GPLv3. Puedes activar un motor JPEXS local para obtener la
decompilación completa mientras el backend PySWF sigue creciendo.
JPEXS no es necesario para ejecutar la aplicación: si no está configurado,
`engine=auto` usa automáticamente el decompilador Python.

Configura una instalación/compilación local de FFDec con una de estas variables:

```powershell
$env:PY_SWF_FFDEC = "C:\ruta\a\ffdec.bat"
# o, si tienes un JAR compilado:
$env:PY_SWF_FFDEC_JAR = "C:\ruta\a\ffdec.jar"
python -m server
```

La comunicación se realiza mediante JSON Lines en
`tools/jpexs_bridge.py`. El endpoint `/api/files/engines/status` muestra si el
puente está disponible. La UI permite elegir `Auto`, `Python` o `JPEXS` en la
pestaña `Source`.

## Modo hospedado

El servidor mantiene los SWF en memoria y elimina las sesiones por LRU. Para
un despliegue remoto se puede activar autenticación y límites sin cambiar el
código:

```powershell
$env:PY_SWF_API_TOKEN = "un-secreto-largo"
$env:PY_SWF_ALLOWED_ORIGINS = "https://studio.example.com"
$env:PY_SWF_MAX_UPLOAD_BYTES = "104857600"
python -m server --host 0.0.0.0 --port 8000 --no-browser
```

Las peticiones API autenticadas deben enviar `X-PySWF-Token`. El aislamiento
de Java/JPEXS (usuario sin privilegios, sin red, CPU/memoria y timeout) debe
aplicarse en el contenedor o worker del despliegue; el proceso local no se
presenta como un sandbox de seguridad.

El código fuente de JPEXS se mantiene en `third_party/jpexs-decompiler` bajo
su propia licencia GPLv3. Consulta [LICENSE](LICENSE) y
`third_party/jpexs-decompiler/license.txt` para los avisos legales.
