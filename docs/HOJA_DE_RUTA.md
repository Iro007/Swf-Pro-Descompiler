# Hoja de ruta: Flash Universal Studio, una suite con varios motores y benchmarks útiles

## Contexto

La idea original era **un programa que use varios descompiladores, compiladores y desensambladores a la vez** (PySWF, RustSWF, JPEXS, Flex, Haxe, RABCDAsm…) y que **los compare con benchmarks que sirvan**. Buena parte ya existe, pero creció por capas:

- 4 registros de motores que se solapan
- 3 puentes Java para JPEXS
- 5 interfaces
- 2 procesos que leen el mismo SWF
- rutas fijas de esta máquina
- un "Automático" que no elige el mejor resultado
- benchmarks que casi siempre dan N/V y no comparan compiladores, desensambladores ni exportadores

Este plan explica cómo funciona hoy (Parte 1) y propone una hoja de ruta por fases (Parte 2) en la que la app sigue funcionando en cada paso.

**Decisiones del usuario:**
- Las cuatro prioridades: arquitectura de motores, benchmarks útiles, calidad de los motores propios, app e instalación.
- Solo **Windows**.
- Herramientas comerciales (Sothink, Trillix, AS3 Sorcerer, ASV) **opcionales, con la copia con licencia del usuario**; nunca se incluyen ni se descargan.
- **Borrar las interfaces viejas** (Qt, web FastAPI/React, pywebview) después de crear la etiqueta git `legacy-ui-final`.
- **Mejorar los dos descompiladores propios por igual**; "auto" elige el mejor por script.

---

## Parte 1: cómo funciona hoy

```
                 Flash Universal Studio (Tauri + React: RUST SWF/desktop + RUST SWF/web)
                 ┌─────────────────────────────┬──────────────────────────────────────────┐
  Explorador ───▶│ rpc  → rustswf.exe worker    │ suite_rpc → FlashSuiteWorker.exe worker   │◀── Mesa de trabajo
  (App.tsx)      │ (crates swf-core/avm/        │ (= py_swf/universal.py: UniversalSuite)   │   (SuiteWorkbench.tsx)
                 │  decompile; solo lectura)    │   ├─ PySWF (Python, en proceso)           │
                 └─────────────────────────────┤   ├─ RustSWF (2.º rustswf.exe, persistente) │
                                               │   ├─ JPEXS (UniversalBridge.java, JVM)    │
                                               │   ├─ Flex mxmlc, MTASC, Haxe, HaxeFlixel  │
                                               │   ├─ RABCDAsm, Flasm (desensambladores)   │
                                               │   ├─ Ruffle (vista previa, en la web)     │
                                               │   └─ Sothink/Trillix/ASV/AS3 Sorcerer     │
                                               │      (solo "asistido": abrir + importar)  │
                                               └──────────────────────────────────────────┘
```

**Procesos.**
- **`rustswf.exe`** (`RUST SWF/crates/swf-cli`) alimenta el *Explorador*: árbol de recursos y scripts, código, P-code, hexadecimal y exportación. Solo lee. Tiene un límite fijo de 30 s y una lista de operaciones permitidas.
- **`FlashSuiteWorker.exe`** es Python congelado y es el **orquestador real**. `UniversalSuite` (`py_swf_editor/py_swf/universal.py`, 1447 líneas) despacha unas 37 operaciones con una cadena de `if`:
  - abrir, descompilar, comparar, editar, deshacer/rehacer, compilar proyecto, editar recursos
  - exportar (FLA/XFL vía JPEXS), timeline, vista previa
  - herramientas, benchmarks, snapshots

**Registro de herramientas.** `ToolRegistry` en `py_swf/suite_tools.py`:
- `DEFINITIONS` es una tabla fija con las operaciones de cada herramienta.
- `discover()` busca en rutas fijas y luego en PATH.
- El SHA-256 de cada ejecutable queda fijado en `%LOCALAPPDATA%\FlashUniversalStudio\tools.json`.
- La línea de comandos de cada herramienta está escrita dentro de `compile_project` y `tool_operation`.

**Automático y comparar.** "Automático" prueba python → rust → jpexs y se queda con el **primero que devuelva texto**, sin medir la calidad. "Comparar motores" muestra los tres lado a lado.

**Compilar.** Rust no compila nada; todo pasa por Python:
- AS1/AS2: nativo, JPEXS o MTASC.
- AS3: nativo (subconjunto, hasta 64 archivos) o Flex `mxmlc`.
- Haxe y HaxeFlixel: en una copia aislada.
- Editar un método o acción: `editing.py` / `unit_editing.py`.

**Proyectos.** `.pyswfproj` v3: un ZIP con el original, la copia de trabajo, las fuentes y los snapshots. Deshacer/rehacer guarda hasta 16 pasos o 256 MiB.

**Híbridos `flashdec-py` / `flashdec-rs`.** Motor propio + JPEXS, quedándose con el mejor archivo de cada script. La app no los usa.

**Benchmarks** (11 módulos `benchmark*.py` y 77 scripts en `py_swf_editor/tools`):
- **Descompilación/desensamblado** (`pyswf benchmark`): cobertura por unidad canónica, tiempos fríos y calientes, RAM.
- **Semántica** (`medir-codigo.ps1`): descompilar → recompilar (nativo, Flex o Rascal) → ejecutar original y copia en Ruffle → comparar trazas. **Es la medida que de verdad sirve.**
- Todo va a un catálogo SQLite (~400 MB; la app rechaza más de 512 MiB) que muestra la pestaña Benchmarks.

### Problemas principales

| Área | Problema | Dónde |
|---|---|---|
| Motores | 4 registros con "auto" distinto en cada uno | `engines.py` y `decompile/backends.py` (JPEXS primero), `suite_tools.DEFINITIONS` (nativo primero), `benchmark_engines.ENGINES` |
| JPEXS | 3 puentes Java casi iguales; flashdec además lanza una JVM por cada SWF | `tools/jpexs_application_bridge/UniversalBridge.java`, `DevelopmentReferenceBridge`, `JPEXSJsonlBridge` + `tools/jpexs_bridge.py` |
| Orquestador | Clase gigante con cadena de `if`; herramientas escritas dentro del código; busca uno a uno en RustSWF (`_rust_unit`) | `universal.py` |
| Interfaces | Tauri (la buena) + Qt `gui/` + pywebview `desktop.py` + FastAPI `server/` con `py_swf_editor/web/` (con su propia sesión y deshacer) | `py_swf_editor/` |
| Procesos | El mismo SWF se analiza en dos procesos; dos supervisores y dos protocolos duplicados | `desktop/src/main.rs`, `suite.rs` |
| Rutas | Cadenas de carpetas con fecha (`FlashUniversalStudio-*-20261014/17`), `.dev-references`, `toolpacks`, `RUST SWF` con espacio (36 archivos), oráculo Ruffle con nombre-hash en `%LOCALAPPDATA%` | launchers, `suite_tools.py`, `benchmark_engines.py`, `run_semantic_benchmarks.py` |
| Benchmarks | Tiempo y memoria casi siempre N/V. No se miden compiladores, ida y vuelta de desensambladores ni exportadores de recursos. "smoke" apunta a archivos inexistentes. El catálogo crece por encima del límite | `benchmarks.py` (`SMOKE_FILES`), `benchmarkModel.ts`, `benchmark_catalog.py` |
| Calidad propia | RustSWF rechaza closures anónimos, try/finally, switch y bucles complejos (23 códigos `UNSUPPORTED_*`). El compilador AS3 nativo solo admite un subconjunto | `swf-decompile/src/reconstruct.rs`, `py_swf/compiler/` |
| Repo | CI solo Ubuntu, sin `cargo test` ni Windows. Casi sin tests de web y escritorio. Bug probable: snapshots idénticos se fusionan al reabrir | `.github/workflows/ci.yml`, `project.py:from_archive` |

---

## Parte 2: hoja de ruta

### Decisiones de diseño
1. **Python es dueño del registro y la orquestación** (`UniversalSuite`). Rust y la UI piden la lista de motores con la operación `engines`.
2. **Una carpeta `shared/`** guarda los contratos comunes: `toolchain.schema.json`, `scoring-rules.json` con sus vectores de prueba y `protocol-ops.json`. Python los lee; Rust los incluye con `include_str!`; un test "dorado" corre en pytest y en `cargo test`.
3. **Un solo puente JPEXS** (`UniversalBridge`, ampliado) y un solo JVM persistente por sesión.
4. **Los dos descompiladores propios siguen evolucionando.** "auto" elige por script con una puntuación común.
5. **Una sola interfaz** (Flash Universal Studio) y una sola CLI (`flashsuite`). La API web de benchmarks queda como un servidor mínimo de solo lectura.
6. **Una sola carpeta de entrega** `dist/FlashUniversalStudio/`, y en la versión empaquetada ninguna ruta relativa al workspace.

### Fase 0: red de seguridad y limpieza (S–M)
- **Fusionar el PR #3.** Después, CI con un job `windows-latest` que ejecute `cargo test --workspace`, ambas suites pytest y `npm run lint && npm run build` de `RUST SWF/web`. Se quita el job de `py_swf_editor/web`.
- **`SMOKE_FILES`** (`py_swf/benchmarks.py`): pasa a ids que existen (los 3 SWF de `benchmark_inputs/` + 3 familias parity). Un test falla si falta alguno, y el "smoke" no admite SWF inválidos.
- **Bug de snapshots** en `project.from_archive`: mapear por nombre, no por hash. Test con dos snapshots idénticos.
- **Interfaces viejas.** Crear la etiqueta `legacy-ui-final` y borrar `gui/`, `main.py`, `desktop.py`, `py_swf_editor/web/`, `Dockerfile` y `docker-compose.yml`. Quitar PySide6 y pywebview de `requirements.txt`. `server/` se elimina en la Fase 1, cuando exista su reemplazo.
- **Una ruta de entrega.**
  - `build.ps1` escribe solo `dist/FlashUniversalStudio/`, y `tar.exe -a` reemplaza `Compress-Archive`.
  - `flash-studio.ps1`, `pyswf.ps1` y `benchmark_engines.default_rust_executable` usan esa ruta, más un fallback de desarrollo.
  - Las carpetas con fecha se archivan a mano.
- **Renombrar `RUST SWF` → `rust_swf`** en un commit aislado: 36 archivos, y desaparece la junction de windres. `bootstrap.ps1` crea una junction `RUST SWF` opcional para accesos viejos.
- *Hecho cuando* la CI está en verde en Windows, `pyswf benchmark --profile smoke` corre sobre ids fijos y la app abre desde la ruta única.

### Fase 1: un solo registro de herramientas (M)
- **Nuevo `py_swf_editor/py_swf/toolchain/`.**
  - `manifests/<id>.json`, validados con `shared/toolchain.schema.json`, describen cada herramienta:
    - `category`: decompiler, compiler, disassembler, assembler, resource-exporter, runtime, converter, assisted o hybrid
    - capacidades por operación y lenguaje, y cómo invocarla (en proceso, worker persistente o CLI)
    - dónde buscarla, versión y SHA-256 fijados, licencia
    - `delivery`: `bundled`, `download` o `user_install` (las comerciales, que solo se localizan, p. ej. en Program Files o en el registro de desinstalación)
    - prioridad y color
  - `registry.py` absorbe `ToolRegistry` de `suite_tools.py` (`fingerprint`, `configure`, `environment`, `java_command`) y `workspace_tools.discover_workspace_tools`. Orden de búsqueda: configuración del usuario → `%LOCALAPPDATA%\FlashUniversalStudio\toolpacks` → recursos del instalador → PATH → workspace (solo con `FLASH_SUITE_DEV=1`).
  - `adapters/`: una clase por herramienta (`capabilities`, `decompile`, `compile`, `disassemble`, `assemble`, `export_resources`, `run`). Reciben las líneas de comando que hoy están en `universal.compile_project` y `tool_operation`, `benchmark_engines.external_command` y `benchmark_external_worker.perform`.
    - El adaptador PySWF envuelve `decompile.backends.NativeBackend`.
    - El adaptador RustSWF envuelve `benchmark_rust_worker.RustWorker`.
    - Se añaden Rascal y pyflashkit.
- **Migración** en commits pequeños:
  1. `DEFINITIONS` y `benchmark_engines.ENGINES` pasan a ser vistas generadas desde los manifiestos.
  2. Las llamadas pasan a los adaptadores.
  3. Se borran `engines.py`, `JPEXSBackend`, `tools/jpexs_bridge.py`, `workspace_tools.py`, `server/` y `plugins.py`.
  4. Un `py_swf/bench/serve.py` mínimo (stdlib, solo lectura) sirve `/api/benchmarks/*` para `analysis.html`. Se quitan fastapi y uvicorn.
- **UI.** Las listas de motores escritas a mano desaparecen de `displayNames.ts`, `benchmarkModel.ts`, `BenchmarkCatalog.tsx`, `BenchmarkResults.tsx` y `SuiteBenchmarks.tsx`. Nombre, color, categoría y capacidades vienen de `engines`.
- *Hecho cuando* no queda ninguna ruta ni comando de herramienta fuera de `toolchain/`, el selector de motores viene del registro y pasan los ~621 tests Python.

### Fase 2: un puente JPEXS, workers persistentes y un "auto" que elige el mejor (M)
- **Puente JPEXS.** Unir en `UniversalBridge.java` las operaciones de los otros dos puentes (P-code, exportación, inventario de `ParityBridgeInventory`), compilarlo solo desde el instalador de toolchain con el hash en `lock.json`, y borrar los otros dos. flashdec deja de lanzar `ffdec-cli` por cada SWF.
- **Java persistente para Flex.** `mxmlc` y `swfdump` corren en un runner Java persistente, para que los tiempos sean comparables.
- **`decompile_all` en el worker `rustswf`** (`crates/swf-cli/src/main.rs`): todas las unidades con id, sha, fuente y códigos de diagnóstico. Sustituye la búsqueda unidad por unidad de `_rust_unit`.
- **Puntuación común.** `shared/scoring-rules.json` + `scoring-vectors.json` sustituyen las tablas copiadas a mano en `flashdec_py/flashdec/scoring.py` y `crates/flashdec/src/scoring.rs`.
- **`toolchain/select.py`**, el "auto" nuevo:
  1. Ejecuta los motores nativos en lote (con caché por sha del SWF, motor y versión).
  2. Llama a JPEXS solo si algún documento no queda limpio.
  3. Puntúa cada documento con las marcas de error, un análisis sintáctico (`compiler/class_parser.py`) y, si se puede, el resultado de recompilar (`class_documents.compile_reconstructed_document`).
  4. Elige un motor por documento de clase o script, sin mezclar motores dentro de una clase. Los empates se resuelven con la prioridad del manifiesto.
  - Devuelve `selection{engine, score, candidates}` y la UI muestra «Elegido: RustSWF (100) · PySWF 70 · JPEXS 40». `universal.decompile` y `code_documents` lo usan cuando el motor es `auto`.
- **flashdec** queda congelado como participante del benchmark. Cuando "auto" iguale o supere su cobertura (hoy 687/968 en `as3_new.flex.swf`), los dos CLIs se borran y `flashdec.ps1` pasa a ser un alias de `flashsuite decompile --engine auto --out DIR`.
- *Hecho cuando* hay una sola JVM por sesión, "auto" llega a ≥ 687/968 y la puntuación se cambia en un solo archivo.

### Fase 3: benchmarks útiles por categoría (L)
- **Un solo runner `py_swf/bench/`**: `python -m py_swf.bench run --category decompiler|compiler|disassembler|resource|runtime --profile smoke|dev|full`. Une los 11 `benchmark*.py` y el núcleo de `tools/run_semantic_benchmarks.py`, y reutiliza:
  - `benchmarks.run_engine` (muestras frío/caliente, RSS con `benchmark_process`)
  - `canonical_inventory.build_inventory`
  - el sellado y la caché de `run_semantic_benchmarks`
  - `corpus.read_immutable_manifest`
- **Un corpus: `benchmark_inputs/corpus.json` (v3).**
  - Cada entrada lleva id, sha256, etiquetas, split, fuentes de referencia, escenarios y recursos de referencia.
    - Lenguaje: as1, as2, as3.
    - Productor: Flash IDE, Flex, MTASC, Haxe, HaxeFlixel, Rascal.
    - Rasgos: ofuscado, dañado, grande, lzma.
  - Referencia las familias parity selladas por sha, sin modificarlas.
  - Conjuntos nuevos:
    - `damaged/`: mutaciones con semilla.
    - `obfuscated/`: las fuentes parity con identificadores renombrados por un script propio, así que no hay problemas de licencia.
    - Una app Flex grande.
- **Métricas que sirven.** Cada valor es *medido*, *no aplica*, *no disponible* o *falló*, y siempre lleva un motivo.

| Categoría | Qué se compara | Métricas |
|---|---|---|
| Descompiladores | PySWF, RustSWF, JPEXS, pyflashkit, Flare, auto, comerciales opcionales | cobertura por unidad canónica sin marcas; documentos que **recompilan**; **traza igual en Ruffle**; ida y vuelta de bytecode por método |
| Compiladores | nativo AS2/AS3, Flex, MTASC, Rascal, Haxe, JPEXS-AS2 | compila las fuentes del corpus; SWF válido; **traza igual a la build de referencia**; tamaño; tiempo en caliente |
| Desensambladores/ensambladores | PySWF, RustSWF, RABCDAsm, Flasm, Flex swfdump, pyflashkit | **ida y vuelta** desensamblar→ensamblar idéntico o ABC equivalente; cobertura |
| Exportadores de recursos | PySWF, RustSWF, JPEXS, swfextract | imagen (RMSE de píxeles), audio (decodificar y hash del PCM), fuentes (número de glifos) frente a la referencia |
| Runtime | Ruffle | solo como oráculo, nunca se clasifica |

- **Oráculo Ruffle.** Manifiesto `ruffle-oracle`, compilado por `tools/build_ruffle_oracle.ps1` desde un commit fijado, en lugar del binario con nombre-hash de `%LOCALAPPDATA%`.
- **Almacenamiento.**
  - Un SQLite por campaña: `reports/bench/<campaña>.sqlite`.
  - Un `summary.sqlite` con agregados por motor × categoría × entrada, menos de 50 MB.
  - `bench prune --keep N`. Con esto se acaba el problema de los 512 MiB.
- **UI** (`BenchmarkAnalysis.tsx`):
  - Una tabla de posiciones por categoría.
  - Cada N/V muestra su motivo.
  - Solo se declara ganador con suficientes entradas medidas por todos y una diferencia mayor que el ruido.
  - Los tiempos en caliente (worker persistente) y en frío se muestran por separado.
- *Hecho cuando* un "smoke" llena las cinco pestañas sin N/V sin explicación.

### Fase 4: calidad de los motores propios, en bucle continuo (L)
- **Los dos motores siguen mejorando**, así que hace falta no duplicar el esfuerzo:
  - Fixtures y corpus compartidos.
  - La misma puntuación.
  - `bench report --failures` saca un histograma de fallos **por motor** desde el catálogo.
- **Orden de trabajo:**
  1. Primero, lo que **fallan los dos** y solo JPEXS resuelve: más cobertura de "auto".
  2. Después, el diff «un motor resuelve y el otro no»: tareas de portado en ambas direcciones, con la implementación que ya funciona como referencia.
- **Huecos de RustSWF** (`reconstruct.rs`, `cfg.rs`, `document.rs`), por orden:
  1. CONTROL_FLOW / LOOP_STACK / SWITCH_FLOW
  2. CLOSURE / ACTIVATION (funciones anónimas y manejadores de eventos)
  3. EXCEPTION_FLOW (try/finally anidados)
  4. THIS_ASSIGNMENT / constructores / STATIC_INITIALIZER
  5. SCRIPT/SOURCE_DOCUMENT / NAMESPACE / TYPE (documentos que recompilan)
- **Huecos de PySWF** (`py_swf/decompile/`): los mismos códigos, normalizados a la taxonomía común de diagnósticos de `shared/`.
- **Compilador nativo** (`compiler/class_compiler.py`, `as_compiler.py`): AS3 con interfaces, get/set, namespaces, inicialización estática, try/catch y closures; clases AS2. Se mide en la tabla de compiladores contra Flex, MTASC y Rascal.
- **Puerta de calidad.** Cada mejora añade un microfixture y una línea base en `benchmarks/baselines/*.json`. `bench gate` corre en CI y falla si baja la cobertura o se pierde una traza igual.
- *Hecho cuando* cada versión sube la cobertura limpia y la de documentos que recompilan de ambos motores, sin regresiones en la puerta.

### Fase 5: un supervisor, un protocolo, lista de operaciones permitidas (M)
- **`shared/protocol-ops.json`** asigna a cada operación su clase de tiempo y de memoria y si modifica algo.
  - El `dispatch` de `universal.py` pasa de cadena de `if` a tabla y rechaza operaciones desconocidas.
  - `Limits::for_operation` (`desktop/src/suite.rs`) lee el mismo archivo.
  - Un test comprueba que ambos lados coinciden.
- **Supervisor genérico.** `WorkerSupervisor` se extrae de `suite.rs` y gestiona las dos instancias. Se borra el `Host` de `main.rs` y su límite fijo de 30 s.
- **Protocolo.** `rustswf worker` adopta `{protocol_version,id,op,args}` y acepta el formato viejo durante una versión.
- **Rutas.** `workspace_beside` y el `CARGO_MANIFEST_DIR` incrustado quedan solo para builds de depuración.
- **Una sola versión** (Cargo workspace). `tools/bump_version.ps1` sincroniza `pyproject.toml` y `package.json`, y un test lo vigila. El identificador `com.rustswf.studio` pasa a `com.flashuniversalstudio.app` antes del primer instalador público.
- **Una sola CLI.** `py_swf/suite_cli.py` se convierte en `flashsuite` (engines, decompile, compile, bench, toolchain). Se borra `tools/flash_suite_cli.py`; `py_swf/cli.py` queda como CLI de desarrollo.
- *Hecho cuando* una operación desconocida se rechaza en ambos lados y la build de release no contiene rutas del workspace.

### Fase 6: instalador, asistente de primer uso y releases (M)
- **NSIS** (ya existe `build.ps1 -Installer`) incluye la app, `FlashSuiteWorker` en modo *onedir* (arranca antes y da menos falsos positivos de antivirus), `rustswf.exe`, Ruffle web y las licencias.
- **Asistente de primer uso.** Descarga desde las entradas `download` de los manifiestos: JPEXS, un JDK reducido con jlink, Flex, Haxe, RABCDAsm, Flasm y Rascal.
  - Pide consentimiento y muestra el tamaño antes de cada una.
  - Verifica el sha256.
  - Instala en `%LOCALAPPDATA%\FlashUniversalStudio\toolpacks`.
  - Las comerciales solo tienen "Localizar instalación".
- **Instalación unificada.** Los scripts `bootstrap_references.py`, `setup_suite_tools.py`, `setup_extra_benchmark_tools.py`, `setup_benchmark_expansion.py`, `setup_flare.py` y `setup_flashdec_benchmark.py` se unen en `python -m py_swf.toolchain install <id>`, que el asistente llama con progreso.
- **`.github/workflows/release.yml`.** Con cada etiqueta `v*`: compila, ejecuta un smoke y publica el instalador con su sha256 en GitHub Releases.
- *Hecho cuando*, en una VM limpia de Windows 10, se instala, el asistente instala JPEXS, y funcionan descompilar en "auto", compilar con Flex y un benchmark smoke.

### Orden y esfuerzo

| Fase | Tamaño | Depende de |
|---|---|---|
| 0 Red de seguridad y limpieza | S–M | fusionar el PR #3 |
| 1 Registro único | M | 0 |
| 2 JPEXS único + auto con elección | M | 1 |
| 3 Benchmarks por categoría | L | 1 (y 2 para tiempos comparables) |
| 4 Calidad de motores | L, continuo | 3 (la puerta de calidad) |
| 5 Supervisor y protocolo | M | 1; en paralelo con 3 |
| 6 Instalador | M | 1 y 5 |

### Primera sesión de implementación (hecha el 2026-10-20)
1. ✅ CI en `windows-latest` con `cargo test` (sin el crate de escritorio, que necesita `build.ps1`). Los tests que dependen de JPEXS local se omiten si no está instalado. **Pendiente:** fusionar el PR #3.
2. ✅ `SMOKE_FILES` (lista fija del repositorio con su test de existencia; el modo smoke descarta contenedores ilegibles) y el bug de `project.from_archive` (snapshots idénticos), con test de regresión.
3. ✅ Etiqueta `legacy-ui-final`. Borrados `gui/`, `main.py`, `desktop.py`, `py_swf_editor/web/`, Docker, `packaging/pyswf.spec`, `build_windows.ps1` y las herramientas de verificación de esas interfaces.
4. ✅ Una sola carpeta de entrega `dist/FlashUniversalStudio` (más la compilación de desarrollo en `target/`) en `build.ps1`, los launchers y la búsqueda de `rustswf.exe`. `tar.exe` sustituye a `Compress-Archive`.
5. ✅ `shared/scoring-rules.json` + `scoring-vectors.json`, leídos por `flashdec_py` y embebidos por `flashdec-rs`, con los mismos casos de prueba en ambos.

**Siguiente:** renombrar `RUST SWF` → `rust_swf` (tras fusionar el PR) y empezar la Fase 1 (registro único).

---

## Verificación (en cada fase)
- **Tests**:
  - `py_swf_editor`: `.venv\Scripts\python.exe -m pytest tests` (~621 tests)
  - `flashdec_py`: `pytest tests`
  - `RUST SWF`: `. .\tools\env.ps1; cargo test --workspace`
  - `RUST SWF/web`: `npm run lint && npm run build`
  - Todo esto también en la CI de Windows.
- **App de extremo a extremo**: `build.ps1`, después `flash-studio.ps1`. Abrir `benchmark_inputs/as3_new.flex.swf`, descompilar en "auto", editar y recompilar con Flex, exportar recursos, vista previa en Ruffle.
- **Benchmarks**: `python -m py_swf.bench run --profile smoke` en las cinco categorías, revisar la pestaña Benchmarks (sin N/V sin motivo) y `bench gate` contra las líneas base.
- **Instalador** (Fase 6): instalar en una VM limpia y repetir la prueba de extremo a extremo.
