# Código, documentos y compilación compartida

Esta etapa mantiene PySWF y RustSWF como motores de reconstrucción distintos.
La suite recompila documentos admitidos mediante el compilador nativo de PySWF.
`source_engine` y `compiler_engine` identifican ambos roles; recompilar fuente
Rust no añade un compilador propio a RustSWF.

## Consultar documentos

`code_documents` acepta `tag_path` y paginación `offset`/`limit` (máximo 200),
o `document_id` como `tag:3/class:0` o `tag:3/script:0`. `engine` acepta `native`
y `rust`; `auto` utiliza PySWF. `include_source` añade texto a una página; la
consulta de un documento individual devuelve su texto.

Cada documento declara `scope`, `completeness`, los IDs de cuerpos originales,
diagnósticos y `compiler_eligibility`. Un documento completo puede ser de lectura:
la edición requiere demostrar que la reconstrucción nativa reproduce el ABC
original. Los métodos de bootstrap siguen disponibles como P-code y enlazan a
las declaraciones de clase/script; no cuentan como cuerpos de método recuperados.

```powershell
.\pyswf.ps1 code-documents --input ejemplo.swf --args-json '{"tag_path":[3]}'
.\pyswf.ps1 code-documents --input ejemplo.swf --document tag:3/class:0 --engine rust
```

## Recompilar un documento

`compile_code_document` exige `document_id`, `source`, `base_revision`,
`base_source_sha256` (el `original_source_sha256` de la prueba nativa actual),
`tag_sha256` y una salida nueva o `commit:true`. La revisión del SWF, el tag,
la fuente admitida, la ABI y todos los cuerpos serializados se validan antes
de guardar. Sin `commit`, el proyecto, su historial y el original permanecen
intactos. El CLI calcula el locator nativo actual y exporta por defecto:

```powershell
.\pyswf.ps1 compile-document --input ejemplo.swf --document tag:3/class:0 --source A.as --engine rust --output candidato.swf
```

`--engine rust` registra el origen solicitado del texto; no concede elegibilidad
ni demuestra fidelidad. La fuente candidata se compila en Python. Para proyectos
nuevos sigue disponible `compile --profile native-as3` y sus perfiles existentes.

## Evidencia y comparación

El catálogo conserva compatibilidad de reportes v1 y migra su almacenamiento a
v2 con `canonical_units` y `unit_observations`. Las identidades se derivan de los
bytes y conservan offsets del SWF descomprimido, ABC, cuerpos y contexto. Las
observaciones históricas sin inventario independiente no se reinterpretan como
nueva prueba canónica. Las declaraciones, métodos, funciones, acciones y scripts
tienen denominadores separados.

`benchmark_catalog_units` permite filtrar por validación, campaña, unidad,
alcance y motor. `benchmark_catalog_evidence` permite consultar evidencias por
ID, campaña o tipo. Ambos aceptan `offset`, `limit` (0–200) e `include_metadata`;
la respuesta máxima permanece en 4 MiB. Los endpoints HTTP equivalentes son
`/api/benchmarks/catalog/units` y `/api/benchmarks/catalog/evidence`.

El corpus optimizado contiene 12 familias y 72 variantes, con fuentes y hashes,
división de desarrollo/validación/reserva por familia y flags reales de los
compiladores. pyflashkit `api_all_abc` recorre todos los bloques ABC; el modo
histórico `cli_first_abc` queda separado. Ni texto presente, ni compilación,
ni trazas acotadas equivalentes implican paridad general con JPEXS o Flash.

Las pruebas de runtime usan el harness upstream de Ruffle sin renderer, audio,
vídeo ni red, con hasta 120 ticks, 15 segundos de ejecución, 30 de proceso y
límite Windows Job de 2 GiB. Los estados de referencia no soportada, timeout,
fallo de compilación y diferencia de comportamiento quedan explícitos.
