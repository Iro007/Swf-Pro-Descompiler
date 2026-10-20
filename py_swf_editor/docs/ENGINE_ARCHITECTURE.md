# Arquitectura de motores

PySWF Studio usa una frontera de bytes y diccionarios entre la aplicación y
los motores. `py_swf.engines.DecompilerEngine` define las operaciones comunes:

- `inspect_swf`, `list_tags`, `decompile`, `disassemble` y `assemble`;
- `export_resource`, `replace_resource`, `save_swf`, `validate` y `export_fla`.

`NativePythonEngine` implementa las operaciones que ya son nativas. Su
decompilación AS3 sigue siendo parcial: para control de flujo complejo devuelve
diagnósticos o P-code en lugar de inventar código fuente.

Ahora es el motor predeterminado de la API, la CLI y el workspace. La distribución
Windows nativa contiene Python y el frontend; no incluye JRE/JPEXS. La edición
se implementa en `py_swf.editing`, valida los candidatos antes de registrarlos en
historial y comprueba que sobreviven a la serialización/reapertura. La validación
AS3 recalcula límites de pila, registros y scope; no es un verificador completo
de tipos ni un runtime Flash.

`JPEXSJavaEngine` delega decompilación al bridge JSONL persistente. El contrato
incluye `request_id`, temporales por operación, timeout y errores estructurados.
La sustitución progresiva consiste en implementar una operación en Python,
añadir pruebas diferenciales y cambiar su capacidad de `delegated` a `native`.

El adaptador JPEXS actual solo expone decompilación opcional. Las demás operaciones
se anuncian como no soportadas en ese adaptador para evitar etiquetar operaciones
Python como si se hubieran ejecutado en Java. La matriz efectiva está en
`docs/capabilities.json` y `/api/files/engines/status`.

## Selección

| Modo | Comportamiento |
| --- | --- |
| `auto` | JPEXS si está configurado; Python como fallback |
| `native` | Fuerza Python y falla de forma explícita si no soporta una operación |
| `jpexs` | Fuerza el bridge Java/JPEXS |
| `compare` | Ejecuta ambos motores y devuelve sus salidas y métricas |

El diagnóstico de la instalación es solo lectura:

```powershell
python tools/verify_jpexs.py
```

La variable opcional `PY_SWF_JPEXS_COMMIT` permite bloquear la aplicación a un
commit concreto del checkout local. Los binarios, la JRE y el JAR no se
incluyen automáticamente en Git.
