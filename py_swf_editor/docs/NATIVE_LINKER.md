# Programas AS3 nativos enlazados y ejecución verificada

El compilador Python admite varias fuentes/paquetes con imports explícitos que
resuelvan a proveedores incluidos, herencia entre clases propias, interfaces y
overrides con firmas comprobadas. Ordena las declaraciones por dependencias,
rechaza ciclos y clases finales como base, conserva namespaces privados,
protegidos y de interfaces, y emite llamadas `super` y construcción de tipos
con proveedor probado. Los locales de tipos propios conservan su coerción y
las llamadas mediante interfaces usan el namespace de esa interfaz.

```python
compiled = compile_class_program(main_source, sources={"model/Counter.as": counter_source})
data = class_program_swf(compiled, document_class="app.Main", width=640, height=480)
# O la fachada común:
data = NativePythonEngine().compile_classes(
    main_source, sources={"model/Counter.as": counter_source},
    document_class="app.Main", width=640, height=480, frame_rate=30,
)
```

Una document class pública que herede `Sprite`/`MovieClip` y pueda construirse
sin argumentos genera `FileAttributes`, `DoABC`, `SymbolClass` con ID 0,
escenario y frame. Sin document class se mantiene la biblioteca SWF anterior.
El nombre corto se resuelve solo si identifica una clase sin ambigüedad.
Los QNames, la cadena de clases Flash y las aridades usadas por este nivel se
contrastaron con `playerglobal32_0.swc`; no se adivinan proveedores externos.

Los inicializadores pueden ser literales o aritmética numérica pura con
`+`, `-`, `*`. Siguen fuera del nivel nativo: SDKs externos generales,
imports con comodines, E4X, genéricos, bindings dinámicos de this/super/arguments en closures,
inicializadores calculados con llamadas/lecturas de miembros y verificación
completa de tipos AS3. Las bases Flash admitidas directamente son Object,
EventDispatcher, Sprite y MovieClip; otras familias requieren enlace SDK.

Las enumeraciones y funciones anidadas con capturas locales se documentan en
[NATIVE_FUNCTIONS.md](NATIVE_FUNCTIONS.md). Su comprobación actual utiliza
únicamente CLI y una VM de bytecode acotada, sin abrir navegador ni ventanas.

`tools/verify_ruffle.py` compila variantes propias de Haxe/Flex, conserva los
SWF de integración originales y ejecuta cinco escenarios en Chromium con el
WASM oficial Ruffle 0.7.1 servido solo en loopback. Registra fuentes, hashes,
compiladores, traces, color/píxeles esperados y PNG. La carga de referencia
con `--load-mode data` usa `Uint8Array`, igual que Preview. Networking y
acceso JavaScript de la película quedan desactivados.

La campaña `reports/ruffle-authored-runtime/runtime-report.json` pasó Haxe,
Flex, HaxeFlixel, Sprite nativo y un programa nativo con varios paquetes,
herencia, interface, constructor `super(3)`, `new`, override y dispatch a
través de interface. Este último produjo el valor 4 esperado y dibujó la
escena azul. Son pruebas de esos escenarios, no paridad global con Flash,
JPEXS ni equivalencia de todos los cuerpos decompilados.

El proyecto `.pyswfproj` v3 conserva original, fuentes escritas, historial,
snapshots, `suite_metadata` y `toolchains`; sigue leyendo v1/v2 sin reescribir
el archivo de origen. Las declaraciones/firmas de un programa administrado
siguen protegidas al editar la fuente guardada de un SWF existente.
