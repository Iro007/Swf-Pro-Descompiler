# Protocolo de motores (v2)

```
React (ui/)  →  Electron (desktop/): el arnés  →  motores (un proceso JSONL cada uno)
                  ├─ rust    RustSWF   rustswf worker                 (protocolo v2 nativo)
                  ├─ native  PySWF     FlashSuiteWorker worker        (la edición vive aquí; adaptador «suite»)
                  ├─ js go asm         JsSWF, GoSWF, AsmSWF           (adaptador «legacy»)
                  └─ jpexs             se sirve a través de PySWF
```

El proceso principal de Electron es el **arnés**: lanza los motores, abre el documento en ellos, enruta cada operación, elige el mejor resultado y avisa de qué motor contestó. Los motores son iguales entre sí; ninguno hace de intermediario de otro. PySWF es el motor que *edita* (historial, recompilar, editar recursos, exportar, proxy de JPEXS) y por eso es la autoridad del documento editable.

Este documento es el contrato. Lo que las máquinas leen vive en dos sitios y una prueba comprueba que coinciden con lo escrito aquí:

- [`engines/protocol.json`](protocol.json): niveles y operaciones, clase de tiempo y si la operación modifica el documento, cadena de recurso, carpetas JPEXS, errores. Lo leen el arnés y, con `include_str!`, `rustswf`.
- `engines/<id>/engine.json`, bloque `worker`: qué operaciones implementa cada motor y cómo se lanza.

## 1. Transporte

- Un JSON por línea, UTF-8, terminada en `\n`. `stdout` es solo protocolo; `stderr` es libre (el arnés conserva los últimos 8 KiB para los diagnósticos).
- Una petición en vuelo por proceso: el arnés las encola. Tamaño máximo de una petición, 8 MiB; de una respuesta, 8 MiB (RustSWF) a 16 MiB (límite del arnés). Lo grande se pagina (`offset`, `limit`) o se escribe a disco (`path`, `dir`).
- Ciclo de vida: el proceso arranca; con `framing: v2` el arnés manda `capabilities` y comprueba `protocol.version == 2` y las operaciones (los workers `legacy` y `suite` no necesitan esa llamada: el adaptador la completa con el manifiesto); después `open {path}`; el documento queda abierto hasta el siguiente `open`; **cerrar `stdin` (EOF) termina el proceso**. El arnés lo lanza, si puede, a través de `rustswf supervise` (sección 8).
- Petición: `{"id": 7, "op": "decompile", "args": {"unit_id": "tag:6/class:1"}}`. `protocol_version` es opcional (2).
- Respuesta correcta: `{"id": 7, "ok": true, "result": {...}}`. Con error: `{"id": 7, "ok": false, "error": {"message": "…", "kind": "not_found"}}`. `elapsed_ms` es opcional. El `id` se devuelve tal cual; una respuesta con otro `id`, que no sea JSON o que no corresponda a una petición pendiente es una violación del protocolo: el arnés retira el proceso.

### Clases de error (`error.kind`)

| Los da el motor | Significado |
|---|---|
| `bad_request` | Argumentos mal formados o faltantes. Otro motor los rechazaría igual: **no hay recurso**. |
| `not_open` | No hay SWF abierto en este motor. |
| `not_found` | La unidad, el tag o el recurso no existen en este documento. |
| `unsupported` | El motor no implementa la operación (o no para este tipo de unidad). |
| `failed` | El motor lo intentó y falló (archivo no legible, descompilación imposible…). |
| `limit` | Tamaño, tiempo o memoria por encima de lo permitido. |
| `internal` | Fallo del propio motor. |

| Los añade el arnés | Significado |
|---|---|
| `timeout` | La operación superó su plazo; el proceso se mató (y todo su árbol). |
| `crashed` | El proceso terminó o se cerró su salida a mitad de la petición. |
| `unavailable` | El motor no está instalado o no pudo arrancar. |
| `cancelled` | El usuario canceló. **Sin recurso.** |
| `stale` | La petición era para una sesión que ya no es la actual. **Sin recurso.** |
| `protocol` | El motor rompió el protocolo. |
| `no_engine` | Ningún motor pudo atender la operación. |

## 2. Formatos de línea (`framing`)

El arnés habla `{id, op, args}` por dentro y un *adaptador* traduce al formato real de cada worker. El campo `worker.framing` del manifiesto elige:

| `framing` | Petición | Respuesta | Motores |
|---|---|---|---|
| `v2` | `{id, op, args}` | `{id, ok, result \| error:{message, kind}}` | RustSWF |
| `legacy` | `{request_id, operation, args}` (ids como texto) | `{request_id, result \| error:"texto"}` | JsSWF, GoSWF, AsmSWF; `rustswf worker` también lo contesta, en su mismo formato |
| `suite` | `{protocol_version:1, id, op, args}` | `{id, ok, protocol_version:1, result \| error:"texto"}` | PySWF (`universal_worker`) |

Los adaptadores `legacy` y `suite` además dan a las respuestas los nombres y formas del protocolo: `unit_id` ↔ `id`, `avm1`/`avm2` → `as2`/`as3`, diagnósticos como `{code, message, offset}`, `abc_method` → `method`, y derivan lo que el worker no tiene (`header` desde `info` en los `legacy`; `disassemble` en PySWF).

## 3. Niveles

El protocolo sigue la lista de funciones de JPEXS partida en cinco niveles. **Un nivel está completo cuando el motor tiene todas sus operaciones**; el registro lo calcula de `worker.ops` y la app lo muestra por motor (`L1 6/6  L2 6/8 …`). Lo que falta es la hoja de ruta de ese motor.

| Nivel | Nombre | Operaciones |
|---|---|---|
| L1 | Lectura | `capabilities`, `open`, `header`, `tag_tree`, `raw`, `resources` |
| L2 | Recursos | `resource_info`, `extract_image`, `extract_sound`, `extract_font`, `extract_text`, `shape_svg`, `resource_preview`, `sprite_frames` |
| L3 | Código | `scripts`, `disassemble`, `decompile` |
| L4 | Edición | `assemble`, `compile`, `replace_resource`, `undo`, `redo`, `save` |
| L5 | Avanzado | `export_fla`, `export_xfl`, `export_svg`, `export_xml`, `deobfuscate`, `search` |

Motores de paridad completa: **RustSWF y PySWF**. JsSWF, GoSWF y AsmSWF se quedan en L1 + L3 (hoy lo que implementan son `capabilities`, `open`, `header`, `scripts`, `disassemble` y `decompile`).

Además cada motor puede declarar `native_ops`: operaciones propias que el arnés reenvía tal cual (`info`, `outline`, `abc_metadata` de RustSWF; `edit`, `compile_project`, `code_documents`, `timeline`, `tool_operation`… de PySWF). No cuentan para los niveles.

### Identificadores

- **Tag**: `tag:<ruta>`, la ruta son los índices separados por `/`: `tag:12` es el tag 12 de la línea de tiempo principal; `tag:7/3` es el cuarto tag dentro del sprite que está en el 7. Un tag se pide con `tag_index` (solo nivel superior), `tag_path` (`[7, 3]`) o `id` (`"tag:7/3"`).
- **Unidad de código**, la misma en todos los motores: `tag:<ruta>/method:<n>` (cuerpo de método AVM2), `tag:<ruta>/class:<n>` (**clase AVM2 completa**), `tag:<ruta>/actions` (DoAction o DoInitAction AVM1), `tag:<ruta>/button:<n>`, `tag:<ruta>/clip:<n>` (manejadores de eventos) y, anidadas, `…/function:<offset en hex>`.
- **Revisión**: el arnés cuenta `revision` (entero, +1 cada vez que cambian los bytes del documento) y da `sha256` de los bytes actuales. PySWF llama `revision` a ese mismo hash y lo exige como `base_revision` en las ediciones.

## 4. Operaciones

Los resultados son JSON. Aquí solo van los campos que el protocolo promete; un motor puede añadir los suyos. `engine` en los argumentos lo consume el arnés (sección 7), no llega al worker.

### L1 · Lectura

#### `capabilities`
Sin documento. `{}` → `{schema_version, engine, display_name, version, protocol:{name:"flash-engine-jsonl", version:2, framings:[…]}, ops:[…], native_ops:[…], languages:["as2","as3"]}`. `ops` debe coincidir con `worker.ops` del manifiesto: el arnés avisa de las diferencias.

#### `open`
`{path}` → `{info:{filename, sha256, signature, version, width, height, frame_rate, frame_count, tag_count}, diagnostics:[{code, message, offset}], script_count}`. Reemplaza el documento del proceso. El arnés siempre pasa una **copia de trabajo** (`working-NNN.swf`), nunca el archivo del usuario, y añade `session_id`, `source_path`, `working_path` y `filename` (el nombre original). Los proyectos `.pyswfproj` los abre PySWF (la interfaz los pide con `load_project`, que el arnés trata como un `open` de proyecto).

#### `header`
`{}` → la cabecera que lee la ventana única; todos los motores responden con los mismos campos y las mismas reglas de recuento (las de PySWF):

```
{ filename,
  signature:"FWS"|"CWS"|"ZWS", compression:"none"|"zlib"|"lzma", version,
  file_length,                 // longitud declarada, sin comprimir
  file_size,                   // bytes del archivo tal cual
  frame_size:{xmin, xmax, ymin, ymax, width, height},   // twips; width y height en píxeles
  frame_rate, frame_count,
  shown_frames,                // ShowFrame de la línea de tiempo principal
  tag_count,                   // tags de la línea de tiempo principal (el contenido de los sprites no cuenta)
  abc_blocks,                  // DoABC y DoABC1
  has_as3,                     // FileAttributes lo dice, o hay código ABC
  has_metadata,                // hay un tag Metadata o FileAttributes lo anuncia
  file_attributes:{use_network, actionscript3, has_metadata, use_gpu, use_direct_blit}|null,
  metadata:string|null,        // texto del tag Metadata, hasta 64 K caracteres
  sha256, revision,            // el hash de los bytes que describe (PySWF lo llama revision)
  script_count?,               // unidades de código
  background_color?:"#rrggbb"|null, script_limits?:{max_recursion_depth, script_timeout_seconds}|null }
```

Una cabecera *derivada* (`derived: true`: la de JsSWF, GoSWF y AsmSWF, que sale de `info`, o la que lee el arnés) deja en `null` lo que no sabe. El arnés pone `filename` (el archivo que abrió la persona, no la copia de trabajo) y `revision` si el motor no la dio.

#### `tag_tree`
`{limit?}` → `{nodes:[Node], node_count, truncated, limit, revision, filename}`. `limit` (20 000 por omisión, 100 000 como máximo) acota los nodos y `truncated` avisa de que se recortó. Árbol como el de JPEXS que alimenta la ventana única, un nodo `header` y las carpetas, **sin las vacías**, en este orden: `header`, `shapes`, `morphshapes`, `sprites`, `texts`, `images`, `movies`, `sounds`, `buttons`, `fonts`, `binaryData`, `frames`, `others`, `scripts`, `sources`. Qué códigos de tag van a cada carpeta, y cuáles son «de línea de tiempo», está en `protocol.json` (`tag_folders`) y es la clasificación de RustSWF (PySWF tiene la suya en `tag_tree.py`); `sources` son las fuentes de proyecto que PySWF guarda y solo PySWF conoce.

```
Node = {
  id,                  // único dentro del árbol («folder:frames», «tag:5», «class:tag:6/class:1», «frame:main:2»)
  label,               // texto que se muestra: «DefineShape (3)», «Cuadro 2», «Main»
  kind,                // header | folder | tag | frame | package | class | script | source
  detail?,             // texto secundario: símbolo, paquete, etiqueta de cuadro
  folder?,             // carpeta JPEXS a la que pertenece
  tag_index?, tag_path?, tag_type?, tag_name?, character_id?, bytes?, error?,   // nodos de tag (tag_path solo dentro de un sprite)
  unit_id?, language?, // nodos script (unidad de código; as2 | as3)
  document_id?, members?:[{unit_id, name, role}],   // nodos class: el documento de la clase y las unidades de sus miembros
  frame?,              // número de cuadro, desde 1
  source_id?, editable?,                            // nodos source
  children?, child_count?
}
```

- Todo tag aparece al menos una vez. Los tags de control (`PlaceObject`, `FrameLabel`…) cuelgan de su cuadro dentro de `frames`; un `DefineSprite` lleva sus propios cuadros; los tags que dependen de un carácter (`DefineFontName`…) cuelgan de él.
- `scripts`: **AS3** como árbol paquete → clase (`kind: package` anidado por segmentos; `kind: class` con `document_id: tag:<ruta>/class:<n>` y `members` con la unidad de cada método y su `role`: `constructor`, `static_init`, `method`, `getter`, `setter`, `function`, `script_init`, `static_*`, `other`; el código que ninguna clase posee aparece como `kind: script`). **AS1/2** agrupado por cuadro, sprite y botón.
- La interfaz decompila cada `members[].unit_id`, no la clase: una unidad `…/class:<n>` solo la usan quienes piden el documento de la clase entera (`decompile`, `disassemble`).
- **Estado de RustSWF.** Su `tag_tree` tiene las mismas carpetas pero otra forma de nodo (`total_tags` en lugar de `node_count`; `count`, `code`, `name`, `length` y `symbol` en lugar de `child_count`, `tag_type`, `tag_name`, `bytes` y `detail`; sus clases llevan `unit_id` en lugar de `members`). Mientras no se porte a la forma de arriba, `tag_tree` (y `resource_info`, cuyo `points` y `glyphs` solo da PySWF) los contesta **primero el propietario de la sesión** (`prefer_authority` en `protocol.json`) y RustSWF solo cuando PySWF no puede o se le pide con `engine: "rust"`.

#### `raw`
`{tag_index | tag_path | id | scope:"file", offset?=0, limit?=4096 (1 a 65536), encoding?:"hex"|"base64"}` → `{scope:"tag"|"file", name, tag_index?, tag_path?, type?, offset, limit, total, length, encoding, hex | base64, revision}`. Una página de bytes de un tag (`tag_path` solo si está dentro de un sprite; `type` es su código) o, con `scope: "file"`, del archivo tal cual. `total` son los bytes de todo el contenido y `length` los de la página. Un `offset` mayor que `total`, un `limit` fuera de rango o una codificación desconocida son `bad_request`: no se recortan.

#### `resources`
`{query?, codes?:[…], other?:bool, offset?, limit?≤500}` → `{total, offset, items:[{id, tag_index?, tag_path, code, name, length, character_id, symbol, category, formats:[…], diagnostics}]}`. `formats` son los que `extract_*` acepta para ese recurso (`raw` siempre).

### L2 · Recursos

#### `resource_info`
`{tag_index | tag_path | id}` → `{id, tag_index?, tag_path, kind:"shape"|"morph"|"sprite"|"text"|"image"|"video"|"sound"|"button"|"font"|"binary"|"raw", parsed:{…}, …}`. `parsed` es la descripción propia del tipo (límites, tamaño de imagen, códec de sonido, glifos…). La de PySWF es la que edita la interfaz: `kind` es `image`, `sound`, `text`, `shape`, `font` o `raw` y añade `points` (vértices de una forma), `glyphs` (puntos de cada glifo), `coordinate_units` y `revision`. Por eso la contesta primero PySWF (`prefer_authority`).

#### `extract_image`
`{tag_index | tag_path | id, format?:"png"|"jpeg"|"raw", path? | dir?, inline?}` → `{id, format, mime, extension, bytes, diagnostics, path?, data_base64?}`. Con `path` escribe ahí (nunca sobrescribe), con `dir` escribe `<dir>/<id>-<formato>.<ext>`; sin ninguno (o con `inline: true`) devuelve `data_base64` si pesa 4 MiB o menos. Todas las `extract_*` y `shape_svg` se comportan igual y rechazan (`bad_request`) un tag de otro tipo.

#### `extract_sound`
Como `extract_image`; `format`: `mp3`, `wav` o `raw`.

#### `extract_font`
Como `extract_image`; `format`: `svg` (glifos), `cff` (DefineFont4) o `raw`.

#### `extract_text`
Como `extract_image`; `format`: `txt`, `svg` o `raw`.

#### `shape_svg`
`{tag_index | tag_path | id, path? | dir?, inline?}` → igual que `extract_image` con `format: "svg"`. Formas y *morph shapes*.

#### `resource_preview`
`{tag_index | tag_path | id, frame?, ratio?}` → `{kind:"image"|"shape"|"morph"|"sprite"|"text"|"font", mime, encoding:"base64"|"text", data, width?, height?, frame?, frame_count?, ratio?, glyphs?, tag_index, name, revision}`. Vista previa estática de un carácter: una imagen PNG o JPEG en base64, o un SVG como texto; `frame` elige el cuadro de un sprite y `ratio` (0 a 65535) el punto de una forma morf. No ejecuta ActionScript. Hoy solo PySWF la implementa.

#### `sprite_frames`
`{id?, frame?}` → `{frame, frame_count, display_list:[{depth, …}], svg, actionscript_executed:false}`. Dibuja un cuadro de la línea de tiempo principal (o de un sprite, si el motor lo soporta). No ejecuta ActionScript.

### L3 · Código

#### `scripts`
`{query?, offset?, limit?≤500}` → `{total, offset, items:[{id, name, language:"as2"|"as3", engine_language, kind, tag_path, byte_len, sha256, diagnostics}]}`. Son **unidades pequeñas** (métodos, acciones); las clases completas salen de `tag_tree`. `language` siempre es `as2` o `as3`; `engine_language` guarda lo que dijo el motor (`avm1`, `avm2`).

#### `disassemble`
`{unit_id, offset?, limit?}` → `{unit_id, state:"decoded"|"partial"|…, pcode, pcode_total, offset, diagnostics}`. `pcode` es una página de hasta 65536 caracteres. Con una unidad `…/class:<n>` el P-code es el de todos los métodos de la clase, cada uno con su cabecera `; ---- nombre (unidad) ----` (`scope: "class"`).

#### `decompile`
`{unit_id, engine?}` → `{unit_id, engine, state:"structured"|"partial"|"pcode_only", source:string|null, pcode, pcode_total, diagnostics, scope?, …}`. Si el motor no puede reconstruir, `source` es `null` y `state` es `pcode_only`: nunca texto inventado. Con una clase, `source` es el documento de la clase.
- `engine`: un id (`rust`, `native`, `js`, `go`, `asm`, `jpexs`; `python`/`pyswf` = `native`) fuerza ese motor primero; `auto` (o nada) lanza **todos los motores de código a la vez** y se queda con el mejor (sección 7).
- Para clases RustSWF necesita probar los ancestros externos: el arnés le pasa `hierarchy_swc` y `hierarchy_sha256` si encuentra el `playerglobal.swc` fijado en `protocol.json` (`contracts`). Sin él, las clases que extienden clases de Flash devuelven P-code.
- Con `auto` el resultado lleva `selection` (sección 7).

### L4 · Edición

Solo PySWF las implementa. Devuelven el **estado** del documento: `{revision (sha256), working_path, filename, dirty, history:{undo, redo}, script_count, validation, …}`. Un cambio que PySWF confirma pasa a ser la siguiente revisión del arnés.

#### `assemble`
`{unit_id, pcode, base_revision?}` → estado. Sustituye el cuerpo de la unidad por el P-code editado.

#### `compile`
`{unit_id, source, engine?, base_revision?, source_document?}` → estado (recompila una unidad; `engine: "jpexs"` la recompila con JPEXS). `{entry, profile, output, main_class?, debug?}` compila un proyecto y `{output, profile?}` recompila las fuentes guardadas; ambos devuelven además `{path, engine, log, validation}`.

#### `replace_resource`
`{tag_index, operation, …, base_revision?}` → estado. `operation`: `replace-image`, `replace-sound`, `text`, `shape-point`, `font-glyph`, `font-properties`, `header` (velocidad de cuadros).

#### `undo`
`{}` → estado. Error `failed` si no hay nada que deshacer.

#### `redo`
`{}` → estado.

#### `save`
`{path, format?:"swf"|"project"}` → estado + `{path}`. No crea revisión.

`base_revision`: PySWF rechaza una edición hecha sobre una revisión que ya no es la actual («El documento cambió»). Si se omite en `assemble`, `compile` o `replace_resource`, el arnés pone el `sha256` actual.

### L5 · Avanzado

#### `export_fla`
`{path}` → `{path, engine, log, diagnostics}`. PySWF lo hace con JPEXS (`animate_compatibility: "exported_not_opened"`).

#### `export_xfl`
`{path}` → como `export_fla`, carpeta XFL.

#### `export_svg`
`{path, …}` → exportación SVG de formas y cuadros. **Ningún motor la implementa todavía.**

#### `export_xml`
`{path}` → `{path, engine, log}`: el SWF como XML (JPEXS `swf2xml`).

#### `deobfuscate`
`{options?}` → estado. Recuperar flujo y símbolos de SWF ofuscados. **Pendiente.**

#### `search`
`{query, scope?:"scripts"|"strings"|"all", regex?, limit?}` → `{total, items:[{unit_id, tag_path, line, text}]}`. **Pendiente.**

## 5. El manifiesto

Cada `engines/<id>/engine.json` declara el motor (ver el [README](README.md)); si es un worker añade un bloque `worker`:

```json
"worker": {
  "protocol": "jsonl",
  "framing": "v2",
  "command": ["bin/rustswf.exe", "worker"],
  "dev_command": [["target/x86_64-pc-windows-gnu/release/rustswf.exe", "worker"],
                  ["target/x86_64-pc-windows-gnu/debug/rustswf.exe", "worker"]],
  "cwd": "engines/pyswf",
  "env": {"FLASH_SUITE_WORKSPACE": "{workspace}", "FLASH_SUITE_STATE_DIR": "{state_dir}"},
  "memory_mib": 1024,
  "ops": ["capabilities", "open", "header", "…"],
  "derived_ops": ["disassemble"],
  "native_ops": {"state": {}, "edit": {"mutates": true}, "compile_project": {"timeout": "compile", "mutates": true}},
  "class_units": false,
  "authority": true,
  "supervisor": true
}
```

- `command`: el lanzamiento de la aplicación instalada; `dev_command`: el del repositorio. Cada uno es un comando o una lista de comandos candidatos. Gana el primero cuyos archivos existen. Las rutas relativas se buscan, en este orden, en `resources` (aplicación empaquetada), en la raíz del repositorio y en las carpetas de `FLASH_ENGINE_ROOTS`; todas las de un comando tienen que estar en la **misma** raíz. `{node}` es el Node que ejecuta el arnés (Electron con `ELECTRON_RUN_AS_NODE=1` dentro de la app), de modo que JsSWF no necesita Node instalado. Un nombre sin ruta se busca en `PATH`.
- `cwd` (relativo a esa raíz, si existe), `env` (`{workspace}`, `{root}` y `{state_dir}` se sustituyen) y `memory_mib` (tope de memoria del árbol de procesos; PySWF 2048).
- `ops`: las operaciones del protocolo que el motor implementa. `derived_ops`: las que no tiene el worker pero el adaptador resuelve con otras llamadas suyas. `native_ops`: propias, con su clase de tiempo (`default` 30 s, `compile` 120 s, `benchmark` 600 s) y si modifican el documento (`mutates`); `document: false` si no necesitan documento abierto.
- `class_units: false`: no sabe decompilar clases completas (no entra en la votación de una clase). `authority: true`: guarda el documento editable. `supervisor: true`: su ejecutable puede envolver a los demás (`supervise`).
- `via`: el motor no tiene proceso propio, lo sirve otro con unos argumentos fijos. JPEXS: `{"via": "native", "args": {"engine": "jpexs"}, "ops": ["decompile", "compile"]}`.

### Cobertura actual

| Motor | `framing` | L1 | L2 | L3 | L4 | L5 | Falta |
|---|---|---|---|---|---|---|---|
| RustSWF `rust` | v2 | 6/6 | 6/8 | 3/3 | 0/6 | 0/6 | `resource_preview`, `sprite_frames`; todo L4 y L5; la forma de su `tag_tree` |
| PySWF `native` | suite | 6/6 | 3/8 | 3/3 | 6/6 | 3/6 | `extract_*`, `shape_svg`; `export_svg`, `deobfuscate`, `search` |
| JsSWF `js`, GoSWF `go`, AsmSWF `asm` | legacy | 3/6 | 0/8 | 3/3 | 0/6 | 0/6 | solo L1 + L3: `tag_tree`, `raw`, `resources` |
| JPEXS `jpexs` | vía `native` | 0/6 | 0/8 | 1/3 | 1/6 | 0/6 | solo `decompile` y `compile` |

## 6. Cómo añadir un motor

1. Carpeta `engines/<id>/` con su `engine.json` (identidad, `order` = prioridad para desempatar) y el bloque `worker`.
2. Un ejecutable que hable JSONL por `stdin`/`stdout`: `capabilities`, `open` y las operaciones de `worker.ops`. Si ya habla otro formato, elija `framing` (`legacy`, `suite`) o escriba un adaptador en `desktop/harness/adapters/`.
3. Listo: el arnés lo descubre, lo muestra con sus niveles, lo incluye en la votación de `decompile` si implementa `decompile`, y lo usa de reserva en la cadena para lo que implemente.

## 7. Lo que hace el arnés

**Sesión.** `open` copia el archivo del usuario a una carpeta temporal (`working-000.swf`). Los motores leen siempre esa copia. Cada cambio que PySWF confirma se copia como la siguiente (`working-001.swf`…), se incrementa `revision` y los demás motores vuelven a abrir el documento la próxima vez que se les pide algo. Se conservan las últimas cuatro. Una edición espera a las lecturas en curso y las lecturas posteriores esperan a la edición.

**Enrutado y reserva (`served_by`).** Cada operación va al motor que se pide con `engine`; si ese motor no la implementa, no está disponible o falla, sigue la cadena **RustSWF → PySWF → JPEXS a través de PySWF**, solo por los motores que la implementan; después, los lectores que el propio arnés trae para `header` y `raw` (`served_by: "harness"`: lee el archivo, solo FWS y CWS); y por último el resto de motores (JsSWF, GoSWF, AsmSWF). `strict: true` desactiva la reserva. No se reintenta tras `bad_request`, `cancelled` ni `stale`. Una operación nativa va a sus propietarios por orden de manifiesto.

**El propietario de la sesión primero.** `protocol.json` lista en `prefer_authority` las operaciones cuya respuesta forma parte de la sesión de edición (`tag_tree`: incluye las fuentes de proyecto; `resource_info`: da los puntos y glifos que se editan). Para ellas la cadena empieza por PySWF y sigue con RustSWF; el resto de operaciones mantiene el orden RustSWF → PySWF → JPEXS. Pedir `engine` explícito sigue mandando.

**La respuesta de `open`** lleva siempre el estado que lee la interfaz de edición (`revision`, `dirty`, `history:{undo, redo}`, `script_count`, `signature`, `frame_count`, `validation`). Si abre PySWF es el suyo, tal cual; si abre otro motor, el arnés lo completa con lo que sabe (`dirty: false`, historial vacío, `revision` = el `sha256` de la copia) y PySWF toma el documento cuando la primera operación lo necesita. Las respuestas de PySWF llaman al documento `working-NNN.swf` (es lo que abrió); el arnés pone en `filename` y `source_path` el archivo que abrió la persona, y en `header` y `tag_tree` pone además `revision` si falta.

La respuesta de cada llamada (`window.flash.call`) es un sobre:

```json
{ "ok": true, "op": "decompile", "session_id": "…", "revision": 2, "sha256": "…",
  "served_by": "native",
  "attempts": [{"engine": "rust", "ok": false, "kind": "failed", "error": "…", "ms": 12},
               {"engine": "native", "ok": true, "ms": 40}],
  "changed": false, "elapsed_ms": 55, "result": { … } }
```

`served_by` es el motor que respondió; `via` aparece solo si lo ejecutó el proceso de otro (`jpexs` → `native`). Si ninguno puede, `{"ok": false, "error": {"message", "kind", "attempts"}}`: el mensaje es el del primer fallo real y `attempts` los lista todos.

**`auto`.** `decompile` sin motor (o `auto`) pregunta a la vez a todos los motores de código disponibles (para una clase, solo a los que saben de clases) con un plazo de 20 s (`FLASH_AUTO_TIMEOUT_SECONDS`) y puntúa cada fuente con la misma regla (`desktop/harness/scoring-rules.json`): 100 menos 30 por cada marca de error dura (`§§`, «retained as P-code», «pcode_only»…), 5 por cada marca blanda y 40 si los corchetes no cuadran; hasta 10 apariciones por marca. La puntuación detecta lo que el motor admite que falló; **no** prueba que recompile ni que se comporte igual. Gana la mayor; el empate lo decide el menor `order` del manifiesto. JPEXS solo se consulta si nadie sacó 100 (`include_jpexs: "always" | "never"` lo cambia). El resultado trae

```json
"selection": { "strategy": "auto", "engine": "native", "name": "PySWF", "score": {"value": 100, …},
  "candidates": [{"engine": "rust", "name": "RustSWF", "ok": true, "score": null, "state": "pcode_only", "ms": 143}, …] }
```

`engines: ["rust", "js"]` limita la votación a esos motores.

**Plazos y memoria** (los de `src-tauri/src/suite.rs`): 30 s por operación, 120 s para `assemble`, `compile`, `export_*`, `deobfuscate` y las nativas de compilación, 600 s para benchmarks; `FLASH_SUITE_TIMEOUT_SECONDS`, `FLASH_SUITE_COMPILE_TIMEOUT_SECONDS` y `FLASH_SUITE_BENCHMARK_TIMEOUT_SECONDS` los cambian (1 a 600). Al vencer, el arnés mata el proceso y todo su árbol (`taskkill /T /F`) y el siguiente uso lo reinicia y le vuelve a abrir el documento. PySWF conserva su proyecto de recuperación y su historial en `FLASH_SUITE_STATE_DIR`: tras reiniciarlo el arnés lo adopta si contiene los bytes actuales. Memoria: `rustswf supervise --memory-mib N -- <worker>` mete al worker y a sus hijos en un Job Object de Windows con ese tope (1024 MiB; 2048 PySWF; `FLASH_SUITE_MEMORY_MIB`) y `KILL_ON_JOB_CLOSE`: si el arnés muere, no queda ningún proceso. Node no puede crear Job Objects; sin `rustswf.exe` los workers corren sin tope de memoria pero con los mismos plazos y la misma muerte del árbol.

**Cancelar.** `window.flash.cancel()` mata los procesos, rechaza lo pendiente con `cancelled`, y el documento sigue: la próxima operación reinicia los motores sobre la misma revisión.

## 8. Lo que ve la interfaz: `window.flash`

El *preload* de Electron expone solo esto; cada llamada se valida otra vez en el proceso principal.

| Función | Devuelve |
|---|---|
| `call(op, args?)` | el sobre de la sección 7 (nunca rechaza: `ok: false` lleva el error) |
| `cancel()` | `{ok: true}` |
| `engines()` | los motores con disponibilidad, niveles (`levels`, `levels_line`), `ops`, `native_ops` y estado de ejecución |
| `openDialog(kind)` | ruta o `null`. `kind`: `swf`, `project`, `source`, `image`, `audio`, `tool`, `folder`, `output`, `project_output` |
| `saveDialog({name, extensions?})` | ruta o `null` |
| `assetUrl(path)` | URL `flash-asset://` para un archivo que los motores extrajeron |
| `startup()` | `{path, smoke, packaged, version, electron, node, platform, ui, problems}`: el archivo de `--open`, si es una verificación (`--smoke-report`), y qué interfaz se muestra (`built`, `dev` o `shell`) |
| `onMenu(callback)` | `({id, path?}) => …` y una función para darse de baja. Ids: `file:open`, `file:open-project`, `file:save-project`, `file:save-swf`, `file:export-all`, `file:close`, `tools:engines`, `tools:validate`, `tools:undo`, `tools:redo`, `tools:cancel`, `tools:preview`, `tools:benchmarks`, `help:protocol`, `help:about`; `file:open-path` (con `path`) llega al abrir un archivo con la app ya abierta |
| `onEngineEvent(callback)` | `{event:"engine-start"\|"engine-exit", engine, …}` |
| `onFileDrop(callback)` | `(path) => …` con la ruta del primer archivo que se suelta en la ventana (nunca el `File`; si no está en disco, no llama) y una función para darse de baja |
| `reportVerification(evidence)` | `{ok: true}`. Una verificación (`--smoke-report`) espera lo que la interfaz vio: un objeto JSON de hasta 256 KiB; con `success: true` la verificación pasa |

`ui/src/api.ts` es el único módulo de la interfaz que habla con su anfitrión. Con Electron se reescribe así, función por función (la versión escrita es `desktop/ui-host/api.ts`, que exporta lo mismo que la de Tauri; `npm run try-ui` compila la interfaz con ella, sin tocar `ui/`, y la abre en Electron):

| `api.ts` | `window.flash` |
|---|---|
| `native` | `typeof window.flash !== "undefined"` |
| `suiteRpc` | `call(op, args)`: si `ok` es falso lanza `Error(error.message)`; si no, devuelve `result` |
| `suitePick` | `openDialog(kind)`: los mismos nueve `kind` |
| `suiteCancel` | `cancel()` |
| `startup` | `startup()` |
| `reportVerification` | `reportVerification(evidence)` |
| `onFileDrop` | `onFileDrop(callback)` |
