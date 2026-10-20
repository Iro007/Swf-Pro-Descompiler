# RustSWF + PySWF 0.3.0

Esta entrega conserva la suite Flash Universal Studio y sus nombres de archivo
compatibles. Los motores se identifican como **RustSWF** y **PySWF**. La validación
de esta fase se ejecutó mediante código, consola y archivos, sin abrir la GUI,
un navegador ni un reproductor Flash.

## Cambios RustSWF

- Reconstrucción de namespaces públicos `Namespace` y `PackageNamespace` con URI
  conservada, declaraciones AS3 y propiedades calificadas. Los namespace sets
  requieren identidades públicas equivalentes. Las claves runtime deben ser
  primitivas probadas y los namespaces runtime, constantes públicos probados.
- `lookupswitch` con índice signed-int probado y brazos separados que terminan
  en `return` o `throw`, incluidos default y destinos compartidos.
- `capabilities` enumera operaciones, límites y restricciones; también funciona
  como solicitud JSONL antes de abrir una película.
- `abc_metadata` expone índices, claves/valores y SHA del encoding original.
- `roundtrip` reescribe la envoltura Original/FWS/CWS/ZWS, reabre el candidato y
  exige identidad byte a byte de la imagen FWS normalizada. Los tags desconocidos,
  anidados y metadata ABC se conservan. El original y destinos existentes se
  protegen. Este escritor no compila ActionScript.
- `ScriptUnit.event_flags` permite comparar correctamente eventos AVM1 de botón
  y clip. Las funciones AVM1 anidadas conservan su propia identidad de inventario.
- El supervisor aplica 120 segundos/2 GiB a `compile_saved` y `source_edit`, como
  a compilación de proyecto; el picker de audio admite MP3 y WAV.
- Los recursos Tauri nombran los dos workers explícitamente y mantienen los
  binarios dentro de `binaries/`; un build de escritorio no sobrescribe la CLI
  recién compilada con una copia histórica.

## Cobertura comprobada

Pasaron 85 pruebas nativas dirigidas, Clippy sin warnings y ocho pruebas del
supervisor de escritorio por consola (timeout, protocolo, cola, cancelación,
reinicio e historial). Una prueba de integración fuente está marcada ignored y
no se ejecutó en esa campaña. Una prueba opcional Apache Flex CLI recompiló tres
fixtures y verificó sus bindings/encodings públicos; Java se ejecutó headless.
La lectura/escritura volvió a verificar diez entradas de metadata ABC reales,
con sus índices, claves/valores y hashes originales intactos.

En los 48 archivos del corpus con scripts y SHA iguales a los históricos,
10.915 unidades pasaron de 2.689 a 3.184 reconstrucciones estructuradas. Los
diagnósticos `UNSUPPORTED_NAMESPACE` bajaron de 5.148 a 3.366. Seis archivos
legibles no tienen scripts; faltan 40 rutas históricas y dos entradas cifradas
se rechazan. La comparación usa la intersección verificable, no completa rutas
ausentes con resultados vacíos.

Private, PackageInternal, protected, XML/E4X, namespaces ambiguos, handlers
try/catch/finally y switch con fallthrough/joins complejos conservan P-code. La
fuente reconstruida RustSWF sigue siendo un fragmento de método. Los resultados
anteriores no acreditan paridad general con JPEXS ni equivalencia de runtime.

Los informes fuente son `reports/rustswf-source-only-summary.json`,
`reports/rustswf-desktop-source-only-tests.txt`,
`reports/rustswf-namespace-coverage-intersection.json`,
`reports/rustswf-cli-readback.json` y
`reports/rustswf-metadata-roundtrip-readback.json`. Corresponden al código probado
antes de incrementar la versión de empaquetado; el manifiesto de entrega registra
los hashes del compilado final.

## Ejecutables y consola

La suite mantiene `FlashUniversalStudio.exe` y `RustSWFStudio.exe`. `rustswf.exe`
es la CLI del motor RustSWF. `FlashSuiteWorker.exe` sigue siendo el sidecar JSONL
canónico; `PySWF.exe` es una copia idéntica que expone la consola con el prefijo
`cli`. El wrapper `pyswf.ps1` añade ese prefijo.

```powershell
.\rustswf.exe capabilities
.\rustswf.exe inspect archivo.swf
.\rustswf.exe abc_metadata archivo.swf tag:4/method:0
.\rustswf.exe roundtrip archivo.swf salida_nueva.swf cws
.\PySWF.exe cli engines
.\pyswf.ps1 inspect --input archivo.swf
.\pyswf.ps1 compile --input Main.as --profile auto-as3 --output nuevo.swf
```

Los nombres `RustSWF.exe` y `rustswf.exe` coinciden en Windows; por ello no se
reutiliza ese nombre para un alias de la GUI. `PySWF.exe` requiere `cli` para la
consola y conserva los modos `worker`/`request` del sidecar.

## Empaquetar y comprobar sin GUI

Primero se construye el worker onefile mediante
`py_swf_editor/packaging/suite_worker.spec`. Después, desde este proyecto:

```powershell
.\build.ps1 -Offline -SkipTests -SkipDependencyInstall -ExpectedWorkerSha256 <SHA256_FINAL>
.\tools\verify-release-headless.ps1 -RunCliChecks
```

`SkipTests` se utiliza cuando las pruebas dirigidas de la misma fuente ya
pasaron. `SkipDependencyInstall` requiere dependencias web preparadas; el build
sigue ejecutando TypeScript y Vite. No se lanza la aplicación de escritorio.
El comprobador histórico `verify-release.ps1` utiliza GUI y no forma parte de
esta verificación.

El portable incluye ambas CLI, el runtime Python incorporado, WebView2Loader,
licencias y `release-manifest.json`. El build compara SHA antes/después de copiar
el worker y la CLI, comprueba la versión reportada por RustSWF, y arma el ZIP con
una lista explícita de artefactos. Conserva los ZIP
`FlashUniversalStudio-portable.zip` y `RustSWFStudio-portable.zip` con bytes
idénticos. `reports/release-artifact-hashes.json` contiene sus hashes.
Los SDKs/toolpacks y el corpus no se incorporan automáticamente.
