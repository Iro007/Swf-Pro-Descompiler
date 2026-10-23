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
- `root`: dónde está el código si no es esta carpeta (`../../py_swf` para PySWF, `../../crates` para RustSWF).

Los motores propios hablan el protocolo JSONL de `rustswf worker`. Para añadir uno: crea la carpeta con su `engine.json` y, si es un ejecutable, regístralo con su hash en `py_swf/.dev-references/extra-tools.json`.
