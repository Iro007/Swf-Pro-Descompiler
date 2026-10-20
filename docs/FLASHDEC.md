# flashdec: descompiladores híbridos (Python y Rust) con JPEXS

Dos descompiladores de línea de órdenes que convierten un SWF entero en una carpeta de proyecto. Cada uno ejecuta **dos motores sobre el mismo SWF** y, **archivo por archivo**, conserva la mejor reconstrucción:

| Herramienta | Motor propio | Motor externo | Código |
|---|---|---|---|
| `flashdec-py` | PySWF (`py_swf_editor/py_swf`) | JPEXS 26.3 (`ffdec-cli.jar`) | `flashdec_py/flashdec/` |
| `flashdec-rs` | RustSWF (crates `swf-core`, `swf-avm`, `swf-decompile`) | JPEXS 26.3 | `RUST SWF/crates/flashdec/` |

JPEXS se ejecuta en paralelo con el motor propio, en un proceso aparte y con límite de tiempo. Si JPEXS no está disponible, la herramienta sigue funcionando solo con el motor propio.

## Uso

```powershell
.\flashdec-py.ps1 juego.swf -o juego_src
.\flashdec-rs.ps1 juego.swf -o juego_src
.\flashdec-rs.ps1 carpeta_de_swf -o salida -j 4      # carpeta recursiva, 4 en paralelo
```

| Opción | Efecto |
|---|---|
| `-e best` (defecto) / `native` / `jpexs` | ambos motores y el mejor archivo / solo el propio / solo JPEXS |
| `--prefer jpexs` (defecto) / `native` | motor que gana cuando ambos obtienen la misma puntuación |
| `--keep-engines` | guarda además la salida cruda de cada motor en `engines/` |
| `--deobfuscate` | activa la desofuscación automática de JPEXS |
| `--no-resources`, `--no-pcode` | solo scripts / sin listados P-code |
| `--timeout 900`, `--java-memory 2g` | límites de JPEXS por SWF |
| `--java`, `--jpexs-jar` | rutas explícitas (también `FLASHDEC_JAVA`, `FLASHDEC_JPEXS_JAR`) |
| `--json`, `-q` | resumen en JSON / sin progreso |

Entradas: `.swf` (FWS, CWS, ZWS), `.swc` (se extrae `library.swf`) y carpetas. JPEXS se busca en `py_swf_editor/.dev-references` (JDK 21 y ffdec incluidos), en `JAVA_HOME`/`PATH` y en `Program Files\FFDec`.

## Salida

```
salida/
  scripts/               fuente elegida, con la estructura de JPEXS (pkg/Clase.as, frame_N/DoAction.as, __Packages/...)
  pcode/                 P-code exacto del motor propio para cada script
  resources/             imágenes, formas SVG, sonidos, fuentes, textos, binaryData, symbolClass
  extra/native_only/     archivos que solo produjo el motor propio cuando JPEXS ya cubrió el SWF
  engines/native|jpexs/  (con --keep-engines) salida cruda de cada motor
  report.json            puntuaciones por archivo y motor, tiempos, errores
  REPORT.md              el mismo informe en tabla
```

Los recursos se copian de JPEXS cuando terminó sin error (TTF, MP3, PNG/JPEG, SVG); si no, los exporta el motor propio.

## Cómo se elige el mejor archivo

Las rutas de ambos motores siguen la convención de exportación de JPEXS, así que el mismo script se compara directamente. Los manejadores de botones y `onClipEvent` se emparejan por carpeta contenedora y orden de aparición.

Las reglas están en [`shared/scoring-rules.json`](../shared/scoring-rules.json): `scoring.py` las lee y `scoring.rs` las incrusta al compilar. Los dos comprueban los mismos casos de [`shared/scoring-vectors.json`](../shared/scoring-vectors.json). Cada texto empieza con 100 puntos:

- −30 por marca de fallo: `§§` (pila sin resolver de JPEXS), `Decompilation error`, `retained as P-code`, `Native reconstruction incomplete`, `// no decompilable`, `//unresolved jump`… (máximo 10 por marca)
- −5 por marca dudosa (`Code may be obfuscated`, opcodes desconocidos)
- −40 si las llaves, paréntesis y corchetes no cuadran (sin contar cadenas ni comentarios)

Gana la mayor puntuación; en empate decide `--prefer`. La puntuación detecta fallos declarados; no demuestra que el código recompile ni que se comporte igual. Para eso siguen estando `medir-codigo.ps1` y los benchmarks semánticos de la suite.

## En el panel de benchmarks (Flash Analysis)

Ambas herramientas están registradas como motores `flashdec_py` y `flashdec_rs` del arnés de benchmarks (`py_swf/benchmark_engines.py`, adaptador en `py_swf/benchmark_external_worker.py`). Cada script de `report.json` declara qué unidad canónica cubre (`tag:N/class:I` o `tag:N/actions`, …) junto con la evidencia de su propio análisis: el hash del bytecode de la acción, o el hash del ABC más el nombre de la clase. El adaptador solo acepta la evidencia que coincide con el inventario canónico. Después expande la clase a sus métodos y la acción a sus funciones anidadas. Si un archivo tiene cualquier marca de error, todas sus unidades quedan como no disponibles.

```powershell
cd py_swf_editor
.\.venv\Scripts\python.exe tools\setup_flashdec_benchmark.py     # registrar o actualizar hashes tras cambiar las herramientas
.\.venv\Scripts\python.exe -m py_swf.cli benchmark ..\benchmark_inputs --engines native,rust,jpexs,pyflashkit,flashdec_py,flashdec_rs --tool-config .dev-references\extra-tools.json --output ..\reports\mi-campaña
```

## Pruebas

```powershell
cd flashdec_py; ..\py_swf_editor\.venv\Scripts\python.exe -m pytest tests
cd "RUST SWF"; . .\tools\env.ps1; cargo test --release -p flashdec
```
