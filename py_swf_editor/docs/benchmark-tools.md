# Herramientas de comparación y límites

La aplicación continúa en Python nativo. Las herramientas externas son solo
referencias de desarrollo: no se empaquetan ni se adaptan fuentes propietarias.

## Categorías y estado

- Fuente ActionScript: PySWF, RustSWF y JPEXS 26.3.0 tienen adaptadores locales.
  pyflashkit 1.3.0 añade otra referencia local de fuente AS3; su instalación y
  ejecución en Windows se verificaron el 14 de octubre de 2026. La pertenencia
  a esta categoría no demuestra recuperación completa o equivalencia.
- Desensamblado: PySWF, RustSWF, RABCDAsm 1.18, pyflashkit 1.3.0, Flasm 1.62
  y Apache Flex swfdump 4.16.1 tienen
  adaptadores locales. RABCDAsm es un desensamblador AVM2; no produce fuente AS3.
- Flasm 1.62 (desensamblado AVM1) quedó configurado desde el enlace HTTP que
  publica su autor; el certificado HTTPS sigue vencido y no se omitió TLS.
  Flare 0.6 (fuente AVM1) sigue sin instalación configurada.
- Sothink 7.4 y Trillix 5.3.1400: instaladores locales identificados por sus
  metadatos, con firmas válidas de SourceTec y Eltima respectivamente. AS3
  Sorcerer 6.78 tiene instalador sin firma. Están pendientes de instalación,
  revisión de licencia y medición; la firma no demuestra seguridad absoluta.
- [ASV](https://www.buraks.com/asv/) y
  [AS3 Sorcerer](https://www.buraks.com/as3sorcerer/) anuncian licencia gratuita
  para uso privado, personal y no comercial. No son código abierto. No se
  presupone que cubra distribución o automatización; aún no están medidos.
- swfmill: conversión XML; swfextract/SWFTools: extracción; Ruffle: ejecución.
  Son referencias de otras operaciones, no competidores de fuente en esta campaña.
- [Uptodown](https://flash-swf-decompiler.uptodown.com/windows): distribución de
  un producto Eltima, no otro motor independiente de la familia Trillix.
- [Shankejingling](https://www.shankejingling.com/): candidato regional
  Sothink/硕思闪客精灵; equivalencia binaria/versión no verificada. No se cuenta
  como otro motor independiente y no se enviaron archivos a ese dominio.

No se adoptan las valoraciones del texto adjunto como evidencia de calidad,
velocidad o superioridad. La identidad del motor exige versión, procedencia y hashes.

La descarga del instalador ASV fue bloqueada por Defender y no se completó,
según confirmó el usuario. No hay un instalador local que recuperar. Los archivos
`asv202201_other` son complementos, no el programa principal. No se modifica
Defender, restaura cuarentena ni elude el bloqueo. El catálogo conserva esta
ausencia explícita sin inventar resultados. Las pruebas preparadas en
`Competing_tools/benchmark_inputs` usan solo tres SWF del corpus público fijado;
los originales y los instaladores no se modifican.

## Medición local

RABCDAsm produce estructuras ABC, traits y archivos por clase; Python produce
listados de instrucciones por método/acción. El alcance no es equivalente:
no hay ganador. La cobertura externa sin correspondencia por unidad conserva
null (no verificada), no 0% ni 100%. Todos los archivos/fallos permanecen visibles.
El CLI arranca en cada operación caliente; solo se excluye arranque del adaptador.
Se separan tiempo del CLI, parseo, I/O/catalogación, serialización y transporte.
RSS incluye el adaptador y sus hijos. Los límites de proceso no son un sandbox.

La ampliación del 14 de octubre mide siete herramientas distintas en dos SWF
pequeños del corpus independiente: cuatro recuperan fuente y seis desensamblan
en al menos un lenguaje aplicable. JPEXS conserva `not_applicable` para
desensamblado porque el puente actual admite fuente y compilación.
Los reportes separados están en `../reports/benchmark-refresh-20261014/`.
Son comprobaciones de integración durante desarrollo activo; no establecen
una clasificación de velocidad. Fuente, desensamblado, compiladores, conversores,
extractores y runtimes conservan operaciones y evidencias separadas.

`quality.disassembly_available` y los contadores por ámbito solo cuentan
observaciones con identidad canónica comprobada. En esa operación,
`source_available` es null. Flasm y Flex swfdump conservan
`artifact_unit_scope: file`; RABCDAsm conserva `abc`. Sus archivos y tiempos
son observables, pero su cobertura por método sigue en null.

Apache Flex swfdump usa el SDK oficial ya instalado y los JAR `swfdump.jar`,
`asc.jar` y `swfutils.jar`, con hashes, LICENSE y NOTICE retenidos. Se invoca
con `-asm -abc -showbytecode -showoffset -noglyphs`. El adaptador exige un SWFX
completo con instrucciones y rechaza ayuda, metadatos sin instrucciones y
errores de análisis aunque el proceso termine con cero. Es otra herramienta
distinta de `swfdump` de SWFTools, que no está instalado.

El ZIP de Flasm enlazado por el [autor](http://www.nowrap.de/flasm.html) tiene
SHA256 `0dfbf0447782dd95a2bb1beaeddf9aaec0cb341ee1c3d8e31d54fbc5d680d14d`
en esta descarga. Ese hash fue medido localmente sobre HTTP; no es un digest
independiente del editor ni acredita transporte autenticado. La distribución
incluye licencia BSD-3-Clause, conservada junto al ejecutable y sus archivos.

~~~powershell
.venv\Scripts\python.exe tools/setup_benchmark_expansion.py
.venv\Scripts\python.exe tools/refresh_benchmark_tools.py --measure --output ../reports/benchmark-refresh-NUEVA-CORRIDA
~~~

La segunda orden exige un destino nuevo para preservar campañas previas.
Sin `--measure`, actualiza el inventario y conserva las observaciones existentes.

## pyflashkit local e independiente

[pyflashkit 1.3.0](https://pypi.org/project/pyflashkit/1.3.0/) es una referencia
de código abierto con licencia MIT y fuente AS3/AVM2, publicada desde el
[commit e50946b48d91d726aac2beec4e6252e3bd512f33](https://github.com/bitalizer/pyflashkit/tree/e50946b48d91d726aac2beec4e6252e3bd512f33).
El wheel oficial tiene SHA256
`a618f4055b8d9c6c0fa09b800d43840692581e0bc9414cca1ba569a4c0c94b6a`.
PyPI publica digest y procedencia de publicación para ese artefacto; los hashes
del intérprete y archivos instalados se registran además localmente.

Se instala en `.dev-references/extra-tools/pyflashkit-1.3.0/.venv`, con Python
3.14.6 en la verificación local, y sin dependencias de ejecución adicionales.
No se instala dentro del entorno del motor PySWF ni se incorpora al ejecutable
de distribución. El manifiesto conserva `development_only: true`, versión,
licencia, procedencia y hashes. La comprobación de hashes ocurre antes de abrir
el trabajador de benchmark. Si se mueve el proyecto, las rutas de desarrollo
se resuelven desde la copia local; el venv debe regenerarse si su Python base
deja de estar disponible.

El modo predeterminado actual `api_all_abc` recorre todos los bloques ABC con
las APIs públicas de pyflashkit, registra clase y método por separado y verifica
hash y tamaño del bytecode frente al inventario canónico. Cada ámbito conserva
su propio denominador. La campaña de ampliación usa este modo.

El modo histórico `cli_first_abc` invoca el punto de entrada oficial:

~~~text
<python del venv pyflashkit> -I -c "from flashkit.cli import main; main()" decompile <input.swf> --all --outdir <output>
~~~

El alcance de `--all` se verificó en el código instalado y fijado de 1.3.0:
[`DecompilerCache._get_decompiler`](https://github.com/bitalizer/pyflashkit/blob/e50946b48d91d726aac2beec4e6252e3bd512f33/flashkit/decompile/cache.py#L21-L60)
invoca `_extract_first_abc_block`, que devuelve el primer tag DoABC/DoABC2.
El [CLI de fuente](https://github.com/bitalizer/pyflashkit/blob/e50946b48d91d726aac2beec4e6252e3bd512f33/flashkit/cli/decompile.py)
llama `dec.decompile_all(outdir)` sobre ese único ABC; no ofrece selección de
ABC por índice. En este modo, «all» significa las clases del primer ABC
seleccionado. La medición histórica de ese modo conserva el CLI oficial tal como está: no mide un
recorrido de fuente mediante API por todos los ABC del SWF. Esta diferencia
de alcance impide interpretar menos archivos o menor tiempo como prueba de
inferioridad o superioridad del motor de reconstrucción.

La operación de desensamblado recorre secuencialmente todos los bloques ABC y
cuerpos de método con las APIs oficiales `Workspace`, `decode_instructions`
y `resolve_instructions`. Se genera un archivo `.asm` por cuerpo. Se conserva
como operación distinta de la exportación de fuente por clase. Cada muestra
incluye el arranque del intérprete externo, CLI/API, inventario canónico,
I/O temporal y catalogación. «Caliente» excluye solo el arranque del adaptador
JSONL; no significa que pyflashkit mantenga un proceso o una caché persistente.

Un código de salida cero exige además archivos no vacíos del tipo esperado.
La salida se registra con ruta relativa, bytes y SHA256. Sin correspondencia
exacta por unidad, el estado es `output_unmapped` y la cobertura queda en null.
Compilación, conducta y fidelidad permanecen sin verificar. En el smoke local
de `as3_cross_compile.flex.swf`, el CLI exportó un archivo `.as` de 191 bytes;
el inventario canónico contiene 129 cuerpos de método. El adaptador de
desensamblado generó 129 archivos. Esos números describen salidas observadas y
no justifican tratar la exportación de fuente como completa.
Las fuentes inspectables y sus logs quedan en
`../reports/external-source-evidence-20261014/`. El manifiesto documenta el
modo del primer ABC; esas exportaciones se realizaron fuera de las muestras
de rendimiento. Se conservaron los hashes de originales, copias y salidas.

AS2 y las operaciones de compilación quedan como `not_applicable`, sin tiempos
inventados. Los fallos de procesos conservan el código nativo y el origen;
un error positivo no se etiqueta como crash por señal. El rechazo de ZWS o
cualquier error del CLI se conserva como fallo de ese archivo. No se descomprime
el original para ocultar un formato que la herramienta no admite.

RABCDAsm 1.18: commit 68f6fa241cda4f67550978dc024b184397d91156.
Su archivo oficial tiene SHA256
68f2a39b45bafab874bacae7ec7b809e27eb6a14a2ec45c52c4305165d2308bf,
registrado desde la publicación HTTPS; no hay digest independiente del autor.
El archivo BCJ2 se valida y extrae con 7zr portátil oficial 26.04, cuyo SHA256
publicado por GitHub es 256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282.
Avisos GPLv3/excepciones de RABCDAsm permanecen con los binarios de desarrollo;
7-Zip y py7zr no forman parte del ejecutable.

## Decompiler.com

El usuario autorizó únicamente el corpus público. Se enviaron dos SWF del
checkout JPEXS fijado, comprobando hashes: as2_definefunc_regs y
as3_cross_compile.flex. Se descargaron sus salidas con 3 y 64 archivos .as;
permanecen en .dev-references/remote-results/.

Una observación de petición incluye red, cola, recursos, conversión FLA y página
de resultados: no es tiempo puro de fuente ni perfil frío/caliente. Versión,
implementación y memoria remotas son desconocidas; sus logs de heap son
autoinformados, no RSS medido. No se verificaron compilación, unidades o conducta.
La prueba remota aparece separada y nunca se activa automáticamente desde la app.

Se solicitó eliminar solo las copias creadas por esta prueba. Los originales
locales y ZIP descargados se conservan. El estado observable está en
reports/decompiler-com-evidence.json; no se afirma verificar borrado físico
del disco del servicio. No se enviaron archivos personales.

## Repetir

~~~powershell
.venv\Scripts\python.exe -m pip install -e ".[benchmark,reference-tools]"
.venv\Scripts\python.exe tools/setup_extra_benchmark_tools.py
.venv\Scripts\python.exe tools/setup_pyflashkit_benchmark.py
.venv\Scripts\python.exe -m py_swf.cli benchmark-tools
.venv\Scripts\python.exe -m py_swf.cli benchmark third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata --engines native,jpexs --profile smoke --output reports/benchmark-bindings-source
.venv\Scripts\python.exe -m py_swf.cli benchmark third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata --operation disassemble --engines native,rabcdasm --profile smoke --output reports/benchmark-disassembly-expanded
.venv\Scripts\python.exe -m py_swf.cli benchmark ../benchmark_inputs --engines native,rust,jpexs,pyflashkit --profile smoke --output reports/benchmark-source-expanded
.venv\Scripts\python.exe -m py_swf.cli benchmark ../benchmark_inputs --operation disassemble --engines native,rust,rabcdasm,pyflashkit --profile smoke --output reports/benchmark-disassembly-local
~~~

El parámetro --tool-config admite un manifiesto de desarrollo con versión, ruta
y SHA256 obligatorio de ejecutable y dependencias. Lo ausente/no aplicable queda
registrado sin tiempos ficticios. No existe subida remota automática por CLI.

## Espacio compartido PySWF/RustSWF

La carpeta padre reúne PySWF, RustSWF y las herramientas externas. Los IDs `native`, `python` y `rust` conservan compatibilidad en los comandos y reportes. Desde ella:

~~~powershell
.\flash-studio.ps1 -Accion Inventario
.\flash-studio.ps1 -Accion Funciones
.\flash-studio.ps1 -Accion Comparar -Corpus benchmark_inputs -Motores native,rust,jpexs -Perfil full
~~~

El inventario se refresca al ejecutarlo y distingue instaladores, fuentes y
programas disponibles. No inicia instaladores ni descarga herramientas.
`docs/funciones_objetivo.json` (raíz del repositorio) registra el conjunto de funciones buscado y los límites
actuales; una función parcial o pendiente sigue identificada así.

RustSWF usa el CLI portable de `rust_swf/dist/RustSWFStudio/rustswf.exe`, con SHA-256
verificado antes del arranque. Cada petición abre una copia nueva del SWF y
pagina las unidades y el P-code. Las unidades solo se emparejan si coinciden
ubicación, lenguaje, tamaño y hash del bytecode; los casos sin correspondencia
quedan como cobertura no verificada. RustSWF ofrece reconstrucción parcial y no
tiene compilador ActionScript. Las pruebas del adaptador verifican su integración;
no establecen un ganador ni paridad funcional.

Los tiempos de RustSWF incluyen inventario de identidad en PySWF, I/O temporal y
transporte JSONL. El reporte conserva ese alcance, el coste de inventario y el
tiempo interno autoinformado de RustSWF por separado. No se debe interpretar el
tiempo total como una medida aislada del lenguaje o del algoritmo.

Los benchmarks se ejecutan con el código fuente y el entorno del proyecto.
Los ejecutables GUI ya existentes requieren una nueva compilación para recoger
cambios futuros de la aplicación.

La campaña 0.3.0 comprueba las ampliaciones de los motores, fuentes guardadas y
recursos mediante fuente/CLI, sin abrir GUI ni navegador. Las pruebas gráficas
de la entrega anterior conservan su alcance histórico. Consulta
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) para capacidades actuales,
reportes dirigidos y pendientes de lenguaje, Animate y herramientas comerciales.
