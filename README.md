# PRO FLASH DESCOMPILER

Una suite para descompilar, editar y comparar archivos SWF/Flash (AS1, AS2 y AS3). La componen dos motores propios, uno en Python y otro en Rust, una aplicación de escritorio y dos descompiladores híbridos que combinan cada motor con JPEXS. También incluye un arnés de benchmarks reproducibles.

## Componentes

| Carpeta | Contenido |
|---|---|
| [`py_swf_editor/`](py_swf_editor) | **PySWF**: parser, descompilador AVM1/AVM2, compilador del subconjunto admitido, editor de recursos, API web, CLI y arnés de benchmarks |
| [`RUST SWF/`](<RUST SWF>) | **RustSWF**: crates `swf-core`, `swf-avm`, `swf-decompile` y `swf-cli`, más **Flash Universal Studio**, la aplicación Tauri/React que reúne todos los motores |
| [`flashdec_py/`](flashdec_py) | **flashdec-py**: descompilador híbrido PySWF + JPEXS que conserva el mejor archivo de cada script |
| [`RUST SWF/crates/flashdec/`](<RUST SWF/crates/flashdec>) | **flashdec-rs**: el mismo descompilador híbrido, con RustSWF + JPEXS |
| [`benchmark_inputs/`](benchmark_inputs) | Corpus de prueba: proyectos AS2/AS3/Haxe con sus SWF compilados y casos de Ruffle y JPEXS |
| [`shared/`](shared) | Contratos que Python y Rust leen por igual, por ahora [las reglas de puntuación](shared/scoring-rules.json) de los scripts descompilados con sus [casos de prueba](shared/scoring-vectors.json) |
| [`docs/`](docs) | [Hoja de ruta y cómo funciona la suite](docs/HOJA_DE_RUTA.md), [flashdec](docs/FLASHDEC.md), [benchmarks de código](docs/BENCHMARKS_CODIGO.md) y la [matriz de funciones](docs/funciones_objetivo.json) |

## Uso rápido (Windows, PowerShell)

```powershell
.\flash-studio.ps1                          # abre Flash Universal Studio
.\flashdec-py.ps1 juego.swf -o juego_src    # descompilador híbrido en Python
.\flashdec-rs.ps1 juego.swf -o juego_src    # descompilador híbrido en Rust (se compila la primera vez)
.\pyswf.ps1 inspect --input juego.swf       # CLI de la suite
.\medir-codigo.ps1                          # benchmarks de fuente, recompilación y comportamiento
.\flash-studio.ps1 -Accion Comparar -Corpus benchmark_inputs -Perfil full
```

## Preparar el entorno

1. **Python**: crea `py_swf_editor/.venv` e instala el proyecto con `pip install -e ".[desktop,build]"`.
2. **Rust**: ejecuta `RUST SWF/bootstrap.ps1`, que instala el toolchain GNU y MinGW verificados. Después, `RUST SWF/build.ps1` compila la suite completa.
3. **JPEXS** (opcional, necesario para flashdec y las comparaciones): `py_swf_editor/tools/bootstrap_references.py` descarga y verifica el JDK y JPEXS 26.3 en `py_swf_editor/.dev-references/`.
4. **Compiladores y referencias opcionales**:
   - `setup_suite_tools.py` instala Flex, Haxe y Ruffle en `toolpacks/`.
   - `setup_extra_benchmark_tools.py` instala RABCDAsm.
   - `setup_benchmark_expansion.py` instala Flasm y Flex swfdump.
   - `setup_flare.py` registra Flare.
   - `setup_flashdec_benchmark.py` registra flashdec-py y flashdec-rs.

   Todos están en `py_swf_editor/tools/`.

Las herramientas de terceros (JPEXS, JDK, SDK, instaladores de AS3 Sorcerer y ASV, Flare, swfdec), los entornos virtuales, las compilaciones y los informes de benchmarks **no se versionan**. Se regeneran con los scripts anteriores o se colocan en las rutas que aparecen en [`.gitignore`](.gitignore), que son las que el código busca.

## Benchmarks

Las campañas guardan sus resultados en `reports/` y el catálogo SQLite que muestra la pestaña **Benchmarks** de Flash Universal Studio. También se pueden consultar en la página web `RUST SWF/web/analysis.html`. Las métricas separan tres cosas: fuente emitida, proyectos recompilados y escenarios que coinciden con el original en Ruffle. Emitir texto no demuestra que el código compile ni que se comporte igual. Los detalles están en [docs/BENCHMARKS_CODIGO.md](docs/BENCHMARKS_CODIGO.md) y [py_swf_editor/docs/benchmark-tools.md](py_swf_editor/docs/benchmark-tools.md).

## Licencia

GPL-3.0-or-later (ver [LICENSE](LICENSE)). Las herramientas de terceros que se instalan aparte conservan sus propias licencias.
