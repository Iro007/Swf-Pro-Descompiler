# Consola PySWF y RustSWF

La consola usa las mismas transacciones, compiladores y validación de la suite. No inicia una interfaz ni un servidor web. Desde el workspace:

```powershell
.\pyswf.ps1 engines
.\pyswf.ps1 inspect --input 'juego.swf'
.\pyswf.ps1 scripts --input 'juego.swf'
.\pyswf.ps1 decompile --input 'juego.swf' --unit 'tag:0/abc/method:1' --engine python
```

El ID de unidad se copia del inventario `scripts`; el ejemplo no presupone que exista en cualquier SWF. Los nombres visibles son PySWF y RustSWF, mientras `python`, `rust` y `native` siguen siendo IDs compatibles.

Desde el portable, `FlashSuiteWorker.exe cli` y su alias `PySWF.exe cli` ofrecen la misma consola sin Python instalado. El wrapper `pyswf.ps1` del portable añade automáticamente `cli`. En un checkout también funciona `.venv\Scripts\python.exe tools\flash_suite_cli.py` o `python -m py_swf.suite_cli` usando el entorno virtual.

## Compilar, guardar fuentes y recompilar

```powershell
.\pyswf.ps1 compile --input 'Counter.as' --profile native-as3 --output 'primero.swf' --project-output 'primero.flashproj'
.\pyswf.ps1 source-files --input 'primero.flashproj'
.\pyswf.ps1 source-edit --input 'primero.flashproj' --document 'project:entry' --source 'Counter_editado.as' --project-output 'borrador.flashproj'
.\pyswf.ps1 compile-saved --input 'borrador.flashproj' --output 'recompilado.swf' --project-output 'recompilado.flashproj'
```

Guardar texto no cambia aún el SWF. `source_pending` indica que hay fuentes pendientes y la recompilación usa el perfil guardado salvo `--profile`. La salida debe ser nueva. La suite conserva el original, el árbol de fuentes externo y el borrador cuando falla la compilación. `compile-saved` genera un programa desde las fuentes archivadas; recursos externos de proyectos HaxeFlixel deben estar disponibles en una entrada de proyecto completa al usar `compile`, porque el archivo de fuentes no los incorpora automáticamente.

Perfiles: `native-as2`, `native-as3`, `auto-as2`, `auto-as3`, `as2-jpexs`, `as3-flex`, `as2-mtasc`, `haxe`, `haxeflixel`. Los SDKs opcionales requieren rutas configuradas en `engines`.

## Editar recursos y conservar la sesión

```powershell
.\pyswf.ps1 resources --input 'juego.swf'
.\pyswf.ps1 resource-info --input 'juego.swf' --tag-index 12
.\pyswf.ps1 resource-edit --input 'juego.swf' --tag-index 12 --args-file 'cambio.json' --state-dir '.\sesion'
.\pyswf.ps1 undo --state-dir '.\sesion'
.\pyswf.ps1 redo --state-dir '.\sesion'
.\pyswf.ps1 save-project --state-dir '.\sesion' --output 'editado.flashproj'
.\pyswf.ps1 rpc --state-dir '.\sesion' --rpc-operation export_swf --args-file 'salida.json'
```

`cambio.json` puede contener `{"operation":"text","text":"Texto nuevo"}` para un DefineEditText. También se admiten `replace-image`, `replace-sound`, `shape-point`, `font-glyph` y `font-properties` según el tipo de tag. `resource-info` devuelve las coordenadas, índices y metadatos que necesita cada operación. Los puntos de formas están en píxeles; los glifos usan las unidades propias de su fuente.

`--project-output` o `--state-dir` es obligatorio para las ediciones de consola. Reabrir `--input` en cada llamada vuelve a cargar ese archivo; omítelo al continuar una sesión persistente. Use `--args-file` para rutas y texto complejos en PowerShell. `rpc` permite las operaciones JSONL restantes con un objeto de argumentos. Los errores salen por stderr con código 1 y no avanzan la revisión.

## CLI RustSWF

```powershell
.\rustswf.exe capabilities
.\rustswf.exe abc_metadata 'juego.swf' 'ID_DEL_INVENTARIO'
.\rustswf.exe roundtrip 'juego.swf' 'copia.swf' cws
```

`roundtrip` conserva exactamente la imagen FWS normalizada y permite `original`, `fws`, `cws`, `zws`; cambia la envoltura y rechaza un destino existente. No equivale a reconstruir fuente AS3 completa. `capabilities` y los diagnósticos detallan el subconjunto soportado.
