# Catálogo de benchmarks de Flash Universal Studio

El catálogo reúne campañas guardadas en SQLite y conserva las mediciones originales. La vista de escritorio está en **Vista → Benchmarks → Catálogo unificado**. El HTML exportado funciona sin servidor ni herramientas externas.

Cada fila conserva campaña, entorno, herramienta/build, operación, fase y SHA-256 del SWF. No se calculan medianas agrupando campañas Linux/cloud y Windows/local. La presencia de fuente, la aceptación por un importador, la compilación independiente y el comportamiento son evidencias diferentes.

## Actualizar las campañas guardadas

Desde el workspace:

```powershell
.\flash-studio.ps1 -Accion Catalogo
```

Para importar también el paquete exacto de la nube recuperado en esta integración:

```powershell
.\flash-studio.ps1 -Accion Catalogo -PaqueteNube reports\cloud-benchmark-20261013\flash-benchmark-minimal.zip -HashPaqueteNube fae411b8fddc06e2f9f7c9ffe285693eefa778f6f49300b117a92d271909940f
```

Esta acción importa registros; no arranca motores ni ejecuta comandos contenidos en los paquetes. Repetir una importación no duplica campañas ni muestras. Las repeticiones reales dentro de una campaña se conservan.

Las campañas nuevas ejecutadas desde la suite se registran automáticamente después de guardar su informe. Usa **Actualizar catálogo** para leerlas en la vista. Si falla el registro, la respuesta incluye `catalog_warning` y conserva el JSON del benchmark para importarlo después.

La salida está en `reports/benchmark-catalog/`: `benchmark.sqlite`, `benchmark.html`, `catalog-summary.json` e `integration.json`. Los JSON originales y los paquetes de transferencia permanecen en sus carpetas.

## Campaña de Codex en la nube

El paquete recuperado contiene 336 filas principales y 2 sondas de heap, y el manifiesto de 22 SWF. Sus motores son JPEXS 26.3.0, pyflashkit 1.3.0, Flasm 1.64 y RABCDAsm 1.19-dev. **pyflashkit no es PySWF**. El lote `batch_primary_16` carece de SHA individual: se conserva como grupo sin identificar, con sus tres ejecuciones, y no aumenta el número de SWF físicos.

Los procesos son nuevos y los tiempos incluyen su arranque; la fase se conserva como `cold`. El heap probe queda aparte. RSS es muestreada y no equivale al límite de heap ni a `rusage_maxrss`. Los timeouts censurados quedan fuera de la mediana de ejecuciones completadas. El código de salida cero y los marcadores de error de fuente se registran por separado.

`verification.json` documenta los hashes de los paquetes, de los archivos de evidencia y de los 22 SWF. Los datos mínimos no incluyen las salidas y logs completos: el ZIP original de 7.460.342 bytes, con SHA-256 `50ebd4d886344881b8606c8c6b702330eacbcbff6d1f002dc4f63603cd8f611c`, permanece en la [Página privada de transferencia](https://chatgpt.com/space/page_451b96fa21c481919fad97e41cf78347), junto con las licencias y referencias originales. La disponibilidad de esos archivos no implica que hayan sido descargados o revalidados localmente.

## Repetir casos comunes en Windows

Desde `py_swf_editor`, usando el entorno existente:

```powershell
.\.venv\Scripts\python.exe tools\verify_cloud_transfer.py
.\.venv\Scripts\python.exe tools\run_matched_cloud_benchmark.py --profile smoke --output ..\reports\benchmark-cloud-matched-windows-20261013
```

Se preparan seis microcasos comunes AVM1/AVM2; el perfil `smoke` selecciona los primeros cinco según el orden del harness y el manifiesto registra la selección exacta. Se utiliza el Rust CLI optimizado indicado por `--rust-cli`, PySWF y el bridge local de JPEXS. El destino debe ser nuevo. `full` incluye los seis y aumenta las repeticiones; debe ejecutarse cuando la carga de fondo permita una medición útil. Los resultados Windows constituyen otra campaña y no se convierten en aceleraciones frente al entorno cloud.

## Lectura desde la aplicación y API

El worker acepta RPC `benchmark_catalog`, con filtros `campaign`, `operation`, `engine`, `environment` y `limit` (máximo 5.000 filas). La consulta abre SQLite sólo para lectura, no inicia motores y no modifica el proyecto abierto. `FLASH_BENCHMARK_CATALOG` permite elegir otra base.

El servidor existente ofrece `/api/benchmarks/catalog/summary` y `/api/benchmarks/catalog/dashboard`. El portable incluye una copia de la base medida dentro del worker, que permite consultar los resultados aunque se ejecute fuera del checkout.
