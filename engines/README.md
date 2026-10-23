# Motores

Cada carpeta es un motor y su `engine.json` es el único sitio donde se declara. La app, la CLI y los benchmarks leen esos archivos.

```json
{
  "id": "toy", "order": 99, "name": "Toy", "category": "decompiler",
  "operations": ["source", "disassemble"], "languages": ["as2", "as3"],
  "website": "", "color": "#336699", "license": "GPL-3.0",
  "delivery": "repo", "root": "."
}
```

- `category`: `decompiler`, `disassembler`, `commercial_decompiler`, `restricted_decompiler`, `remote_service`, `resource_extractor`, `xml_converter`, `runtime`.
- `operations`: `source`, `disassemble`, `extract`, `xml`, `execute`.
- `delivery`: `repo` (el código está aquí o en `root`), `download` (se instala aparte, con hash), `user_install` (licencia del usuario; solo se localiza), `remote` (servicio web, no se envían archivos sin permiso).
- `root`: dónde está el código si no es esta carpeta (`.` para los motores propios: `pyswf/` es el paquete Python y `rustswf/` contiene los crates `swf-*`).

## Workers: motores que la app ejecuta

Un motor con proceso propio añade a su `engine.json` un bloque `worker` (cómo se lanza, qué operaciones implementa, con qué formato de línea habla). La app (`desktop/`, el arnés de Electron) los lanza, les abre el documento, enruta cada operación, elige el mejor resultado en `decompile` y dice qué motor contestó. El contrato completo —niveles L1 a L5, formas JSON de cada operación, errores, cadena de reserva `rust → native → jpexs`— está en [PROTOCOL.md](PROTOCOL.md); lo que leen las máquinas, en [protocol.json](protocol.json).

```json
"worker": {
  "protocol": "jsonl", "framing": "v2",
  "command": ["bin/rustswf.exe", "worker"],
  "dev_command": ["target/x86_64-pc-windows-gnu/release/rustswf.exe", "worker"],
  "ops": ["capabilities", "open", "header", "tag_tree", "raw", "resources", "scripts", "disassemble", "decompile"],
  "native_ops": {"info": {}, "outline": {}}
}
```

- `ops` son las operaciones del protocolo que el motor implementa. El registro calcula de ahí los niveles: un nivel está completo cuando el motor tiene todas sus operaciones (`L1 6/6  L2 6/8  L3 3/3  L4 0/6  L5 0/6`). Lo que falta es su hoja de ruta.
- `framing`: `v2` (nativo), `legacy` (`request_id`/`operation`, JsSWF, GoSWF, AsmSWF) o `suite` (PySWF). Un adaptador del arnés traduce los dos últimos.
- Los motores de paridad completa son RustSWF y PySWF; JsSWF, GoSWF y AsmSWF se quedan en L1 + L3. JPEXS no tiene proceso propio: `"worker": {"via": "native", "args": {"engine": "jpexs"}, "ops": ["decompile", "compile"]}`.
- Para añadir uno: crea la carpeta con su `engine.json`, un ejecutable que hable JSONL y, si es un ejecutable propio, regístralo con su hash en `vendor/references/extra-tools.json` (`vendor/` no se versiona).
