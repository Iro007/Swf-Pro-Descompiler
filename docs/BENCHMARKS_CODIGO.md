# Pruebas de fuente, recompilación y comportamiento

Desde PowerShell, en la carpeta de la suite:

```powershell
.\medir-codigo.ps1
```

La orden compara PySWF, RustSWF, JPEXS, pyflashkit y Flare como generadores de fuente. Registra además RABCDAsm, Flasm y Flex SWFDump como referencias de desensamblado. Que una herramienta genere bytecode legible no demuestra que genere un proyecto ActionScript recompilable.

Por defecto se ejecuta una variante release FWS de cada familia de desarrollo. Para añadir validación:

```powershell
.\medir-codigo.ps1 -Particiones development,validation
```

Para repetir realmente los trabajos, conservando los intentos anteriores:

```powershell
.\medir-codigo.ps1 -Forzar
```

`-Familias` limita la selección por identificadores del manifiesto; `-Motores` recibe una lista de motores; `-TodasLasVariantes` añade las variantes debug y de compresión. Las variantes de una familia no constituyen proyectos independientes. `-Ticks` admite 1 a 120 y conserva ese límite en la identidad de la prueba.

## Qué se mide

1. **Fuente emitida:** texto obtenido de cada motor, con su hash. No acredita corrección.
2. **Proyecto o acciones completas que recompilan:** únicamente las fuentes generadas por el motor. No se rellenan miembros pendientes con las fuentes originales. Cada ruta de compilación conserva su resultado por separado: PySWF compartido, Flex para AS3 y Rascal para AS2.
3. **Escenario ejecutado:** original y recompilado se ejecutan en el mismo harness headless de Ruffle, con entradas y trazas declaradas. Coincidir en esos escenarios acotados no demuestra equivalencia para cualquier entrada.

La vista muestra **superadas / programadas** y separa ejecutadas, fallidas, sin soporte y pendientes. Por ejemplo, `0/6 · 0 ejecutadas · 6 sin soporte` conserva el tamaño del corpus sin inventar seis fallos de ejecución. Una referencia original que no funcione en Ruffle queda señalada y no demuestra una divergencia del recompilado.

## Repeticiones y evidencia

Los resultados quedan en `reports/semantic-benchmarks/latest`. Cada trabajo guarda fuentes, comandos, candidatos SWF, logs, escenarios, trazas y un recibo de hashes. La caché depende de los bytes de entrada, adaptadores, motores, compiladores, runtime y escenario. Una evidencia modificada no se reutiliza. `runs/` conserva los índices de cada ejecución; `cache/` conserva los intentos individuales. Cambios de dependencias durante una campaña invalidan su contexto.

`benchmark.json` se importa automáticamente al catálogo local de la suite sin borrar los reportes históricos. `semantic-index.json` contiene el detalle de la campaña. `-SinImportar` permite generar evidencia sin actualizar el catálogo. No se instala una tarea programada: se ejecuta la orden cuando se desee medir.

Esta campaña mide calidad. Las duraciones de compiladores y runtime son diagnósticos operativos; no constituyen una clasificación de velocidad entre descompiladores. La comparación de rendimiento exige una campaña aparte, alcance y calidad equivalentes y las condiciones de CPU/RAM acordadas.

## Flare

Flare 0.6 genera fuente AS1/AS2; no implementa AS3. La integración conserva la salida íntegra y solo admite automáticamente la forma de timeline que el adaptador puede reconstruir completa. Su licencia limita el uso y redistribución a fines no comerciales; consulte el `LICENSE` original incluido con su descarga. No se han modificado sus binarios.
