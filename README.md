# PRO FLASH DESCOMPILER

Un **arnés de descompiladores de Flash**: una sola aplicación que usa varios motores (PySWF, RustSWF, JsSWF, GoSWF, AsmSWF, JPEXS, Flex, Haxe, RABCDAsm, Flasm, swfmill, swfextract…) sobre el mismo SWF, los compara y se queda con el mejor resultado.

## Estructura

```
src-tauri/   la aplicación de escritorio (Tauri, Rust)
ui/          la interfaz (React)
engines/     un motor por carpeta, cada uno con su engine.json
  pyswf/       PySWF: orquestador de herramientas, compilador, editor de recursos, benchmarks (Python)
  rustswf/     RustSWF: crates swf-core, swf-avm, swf-decompile y swf-cli
  jsswf/ goswf/ asmswf/   motores propios en JS, Go y ensamblador x86_64
  ...          el resto son manifiestos de herramientas de terceros
corpus/      corpus de prueba (SWF, fuentes originales y escenarios)
tools/       scripts de compilación de la app
docs/        documentación y hoja de ruta
vendor/      NO se versiona: lo descargado o instalado (references/, toolpacks/, installers/, jpexs-source/, third_party/)
```

Las herramientas de terceros (JPEXS, JDK, Flex, Haxe, instaladores comerciales…), los entornos virtuales, las compilaciones y los informes **no se versionan**: viven en `vendor/` o se regeneran con los scripts de `engines/pyswf/tools/`. Todas las rutas de Python salen de `engines/pyswf/py_swf/paths.py` (`FLASH_SUITE_WORKSPACE`, `FLASH_ENGINES_DIR` y `FLASH_VENDOR_DIR` las sobrescriben).

## Uso (Windows, PowerShell)

```powershell
.\flash-studio.ps1            # abre la aplicación
.\pyswf.ps1 engines           # CLI: lista los motores disponibles
.\pyswf.ps1 inspect --input juego.swf
.\medir-codigo.ps1            # benchmark: recompila y misma traza en Ruffle (-Completo para todo)
```

## Preparar el entorno

1. **Python**: crea `engines/pyswf/.venv` e instala con `pip install -e ".[dev,build,benchmark]"` (desde `engines/pyswf/`).
2. **Rust**: `.\bootstrap.ps1` instala el toolchain GNU y MinGW verificados; `.\build.ps1` compila la aplicación en `dist/FlashUniversalStudio/`.
3. **Herramientas externas** (opcional): `engines/pyswf/tools/bootstrap_references.py` (JDK + JPEXS), `setup_suite_tools.py` (Flex, Haxe, Ruffle), `setup_extra_benchmark_tools.py` (RABCDAsm), `setup_benchmark_expansion.py` (Flasm, Flex swfdump), `setup_swf_utilities.py` (swfmill, swfextract).

## Más

- [Hoja de ruta y cómo funciona](docs/HOJA_DE_RUTA.md)
- [La aplicación](docs/APP.md) · [benchmarks](docs/BENCHMARKS_CODIGO.md)

## Licencia

GPL-3.0-or-later ([LICENSE](LICENSE)). Las herramientas de terceros que se instalan aparte conservan sus propias licencias.
