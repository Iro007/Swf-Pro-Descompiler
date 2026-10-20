# Medir el código reconstruido

Desde la raíz del workspace:

```powershell
.\medir-codigo.ps1
```

La orden descubre y verifica las herramientas locales, conserva los SWF de
entrada, reconstruye fuente, compila cada proyecto completo admitido, ejecuta
original y candidato con el mismo escenario acotado y actualiza el catálogo.
La selección predeterminada son las seis familias de desarrollo, con una
variante release FWS por familia y los ocho adaptadores locales.

Para incluir las tres familias de validación y consultar la evidencia de esta
campaña:

```powershell
.\medir-codigo.ps1 -Particiones development,validation -Salida reports/semantic-benchmarks-20261014/campaign
```

Al repetir exactamente la orden, los trabajos cuyo contexto y evidencia
conservan sus hashes se recuperan de la caché. `-Forzar` crea otros intentos y
conserva los anteriores, incluidos los errores. `-Familias` y `-Motores` permiten
una selección menor; `-TodasLasVariantes` incluye las variantes de compresión y
depuración. Las variantes siguen perteneciendo a su familia original.

El equivalente Python, con más opciones de límites y binarios explícitos, es:

```powershell
.\py_swf_editor\.venv\Scripts\python.exe py_swf_editor/tools/run_semantic_benchmarks.py --splits development validation --output reports/semantic-benchmarks-20261014/campaign
```

`semantic-index.json` contiene los trabajos, estados y resúmenes separados por
compilador. `benchmark.json` es la versión compacta importable por el catálogo;
enlaza las fuentes y registros completos de cada trabajo mediante su carpeta de
evidencia. `pins/*.json` identifica las dependencias ejecutadas. Cada intento de
`cache/<key>/<attempt>/` conserva `input.swf`, fuentes, candidatos, comandos,
registros de compilación y runtime, `result.json` y un `receipt.json` con los
hashes de todos esos archivos. `runs/*.json` conserva las campañas terminadas.

La caché depende de los bytes de entrada, manifiesto, código de implementación,
adaptadores, configuración y artefactos de herramientas, SDK y compiladores,
runtime y escenario. Una modificación, un archivo perdido o un hash distinto
impiden reutilizar ese intento. Los errores también son resultados reutilizables
para el mismo contexto; instalar o cambiar una dependencia genera otra clave.

La medición distingue tres hechos:

- **Fuente disponible:** el emisor produjo texto y se conserva su procedencia.
- **Proyecto recompilado:** el compilador produjo un SWF validado a partir del
  conjunto completo de clases o de todas las acciones contenedoras generadas.
- **Escenario coincidente:** el original pasó su traza esperada y el candidato
  del mismo compilador pasó exactamente esa traza bajo los mismos límites.

La fuente escrita por los autores del corpus no se utiliza para completar ni
reparar lo que emitió un descompilador. AS3 requiere todas las clases originales,
la raíz `SymbolClass 0` y un bootstrap compuesto únicamente por instalaciones
de clases reconocidas. El clasificador de este nuevo programa admite hasta un
ámbito inicial ambiente, conserva ese dato como fidelidad léxica **sin verificar**
y mantiene los controles de instrucciones, pila y clases. Las llamadas,
ramificaciones, escrituras adicionales, declaraciones globales no enlazadas y
contextos más amplios impiden la admisión. Esta clasificación pertenece a la
campaña; la admisión de edición exacta de ABC permanece separada.

AS2 compila todas las acciones contenedoras reconstruidas con el compilador
compartido. El adaptador independiente Rascal admite un único DoAction raíz en
el primer frame de un SWF de un frame, sin sprites ni eventos. Flare elimina
únicamente su wrapper de película/frame después de demostrar esa topología y
conserva tanto el `.flr` original como su interior exacto.

PySWF, RustSWF, JPEXS, pyflashkit y Flare participan como emisores de fuente donde
su lenguaje y adaptador lo permiten. RABCDAsm, Flasm y Flex swfdump conservan su
papel de desensamblado. Una instrucción listada por esas herramientas no cuenta
como un proyecto fuente recompilado.

Los proyectos y escenarios programados mantienen su denominador aunque no
produzcan fuente. `not_configured`, `not_supported`, `not_applicable`, una
referencia de runtime no elegible y una prueba pendiente conservan estados
distintos de un fallo de compilación o de una discrepancia de comportamiento.
Un porcentaje de éxitos demostrados se interpreta junto a ese desglose.

Cada trabajo tiene un límite de 240 segundos y 2 GiB. El runtime tiene un máximo
de 15 segundos de ejecución, 30 de pared y entre 1 y 120 ticks; la campaña usa un
tick por defecto. El harness es headless, sin renderizado, audio, vídeo ni red.
Sus archivos temporales se ejecutan desde una ruta corta de Windows y luego se
conservan byte por byte en la evidencia. Los resultados demuestran únicamente
las trazas declaradas, sin una afirmación de equivalencia general de Flash.

Esta campaña serializa trabajos de calidad. Sus duraciones operativas no forman
una carrera de velocidad entre herramientas. Los splits históricos se conservan
como procedencia; la exposición previa del corpus impide presentar el holdout
como una evaluación ciega.
