# PRO FLASH DESCOMPILER

Un **arnés de descompiladores de Flash**: una sola aplicación que usa varios motores (PySWF, RustSWF, JsSWF, GoSWF, AsmSWF, JPEXS, Flex, Haxe, RABCDAsm, Flasm, swfmill, swfextract…) sobre el mismo SWF, los compara y se queda con el mejor resultado.

## Estructura

```
desktop/     el anfitrión Electron: el arnés que lanza los motores como procesos JSONL
ui/          la interfaz (React)
engines/     un motor por carpeta, cada uno con su engine.json; PROTOCOL.md es el contrato común
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

## Arquitectura

```
interfaz React  →  Electron (desktop/): el arnés  →  motores (un proceso JSONL cada uno)
```

El proceso principal de Electron lanza cada motor (RustSWF, PySWF, JsSWF, GoSWF, AsmSWF) como un proceso que habla JSONL, abre el documento en ellos, enruta cada operación (con reserva `rust → pyswf → jpexs` y la indicación de qué motor contestó) y, en «Automático», pregunta a todos a la vez y se queda con la mejor fuente. PySWF es solo un motor más, el que edita. Ver [engines/PROTOCOL.md](engines/PROTOCOL.md).

```powershell
cd desktop
npm ci
npm start            # abre la ventana (muestra la consola del arnés mientras la interfaz no esté compilada)
npm test             # pruebas del arnés; las que usan los motores reales se omiten si no están compilados
npm run e2e          # abre un SWF del corpus y descompila una clase en «automático»
npm run try-ui       # compila la interfaz React (ui/dist) y la abre en Electron (--smoke: verificación; --smoke-report <archivo>)
npm run smoke        # abre la ventana con un SWF y escribe reports/electron-smoke.json (una interfaz compilada informa de lo que vio)
```

## Uso (Windows, PowerShell)

```powershell
.\flash-studio.ps1            # abre la aplicación
.\pyswf.ps1 engines           # CLI: lista los motores disponibles
.\pyswf.ps1 inspect --input juego.swf
.\medir-codigo.ps1            # benchmark: recompila y misma traza en Ruffle (-Completo para todo)
```

## Preparar el entorno

1. **Python**: crea `engines/pyswf/.venv` e instala con `pip install -e ".[dev,build,benchmark]"` (desde `engines/pyswf/`).
2. **Rust**: `.\bootstrap.ps1` instala el toolchain GNU y MinGW verificados; `.\build.ps1` compila los motores y la interfaz y empaqueta la aplicación Electron (portable e instalador) en `desktop/dist/`; `-BuildWorker` compila también el worker de PySWF.
3. **Herramientas externas** (opcional): `engines/pyswf/tools/bootstrap_references.py` (JDK + JPEXS), `setup_suite_tools.py` (Flex, Haxe, Ruffle), `setup_extra_benchmark_tools.py` (RABCDAsm), `setup_benchmark_expansion.py` (Flasm, Flex swfdump), `setup_swf_utilities.py` (swfmill, swfextract).

## Más

- [Hoja de ruta y cómo funciona](docs/HOJA_DE_RUTA.md)
- [La aplicación](docs/APP.md) · [benchmarks](docs/BENCHMARKS_CODIGO.md)

## Licencia

GPL-3.0-or-later ([LICENSE](LICENSE)). Las herramientas de terceros que se instalan aparte conservan sus propias licencias.
