# PRO FLASH DESCOMPILER

Un **arnés de descompiladores de Flash**: una sola aplicación que usa varios motores (PySWF, RustSWF, JsSWF, GoSWF, AsmSWF, JPEXS, Flex, Haxe, RABCDAsm, Flasm, swfmill, swfextract…) sobre el mismo SWF, los compara y se queda con el mejor resultado.

## Estructura

```
src-tauri/   la aplicación de escritorio (Tauri, Rust)
ui/          la interfaz (React)
crates/      RustSWF: leer/escribir SWF, desensamblar AVM1/AVM2, descompilar
py_swf/      PySWF: orquestador de herramientas, compilador, editor de recursos, benchmarks
engines/     motores propios en JS (jsswf), Go (goswf) y ensamblador x86_64 (asmswf)
benchmark_inputs/           corpus de prueba (SWF, fuentes originales y escenarios)
shared/      contratos que leen Python y Rust por igual (reglas de puntuación)
tools/       scripts de compilación de la app
docs/        documentación y hoja de ruta
```

Las herramientas de terceros (JPEXS, JDK, Flex, Haxe, instaladores comerciales…), los entornos virtuales, las compilaciones y los informes **no se versionan**: se regeneran o se descargan con los scripts de `py_swf/tools/`.

## Uso (Windows, PowerShell)

```powershell
.\flash-studio.ps1            # abre la aplicación
.\pyswf.ps1 engines           # CLI: lista los motores disponibles
.\pyswf.ps1 inspect --input juego.swf
.\medir-codigo.ps1            # benchmark: recompila y misma traza en Ruffle (-Completo para todo)
```

## Preparar el entorno

1. **Python**: crea `py_swf/.venv` e instala con `pip install -e ".[dev,build,benchmark]"` (desde `py_swf/`).
2. **Rust**: `.\bootstrap.ps1` instala el toolchain GNU y MinGW verificados; `.\build.ps1` compila la aplicación en `dist/FlashUniversalStudio/`.
3. **Herramientas externas** (opcional): `py_swf/tools/bootstrap_references.py` (JDK + JPEXS), `setup_suite_tools.py` (Flex, Haxe, Ruffle), `setup_extra_benchmark_tools.py` (RABCDAsm), `setup_benchmark_expansion.py` (Flasm, Flex swfdump), `setup_swf_utilities.py` (swfmill, swfextract).

## Más

- [Hoja de ruta y cómo funciona](docs/HOJA_DE_RUTA.md)
- [La aplicación](docs/APP.md) · [benchmarks](docs/BENCHMARKS_CODIGO.md)

## Licencia

GPL-3.0-or-later ([LICENSE](LICENSE)). Las herramientas de terceros que se instalan aparte conservan sus propias licencias.
