# Benchmarks

Una pregunta por categoría, una tabla por categoría; nunca se mezclan cosas que no son comparables. Todas usan el mismo oráculo: Ruffle imprime la traza esperada del corpus.

| Categoría | Pregunta | Motores |
|---|---|---|
| Descompiladores | ¿lo que emite recompila (Flex/Rascal) y se comporta igual? | PySWF, RustSWF, JsSWF, GoSWF, AsmSWF, JPEXS, pyflashkit, Flare |
| Compiladores | ¿compilan el proyecto del corpus y se comporta como se espera? | Flex, Rascal, Haxe, compilador de PySWF |
| Desensambladores | ¿desensamblar y reensamblar conserva el comportamiento? | RABCDAsm (AS3), Flasm (AS2); los que solo listan lo dicen |
| Conversores XML | ¿SWF a XML a SWF conserva el comportamiento? | swfmill |
| Extractores | ¿todos los recursos se extraen y decodifican? | swfextract |

```powershell
.\medir-codigo.ps1            # rápido: un caso por familia de desarrollo (~6 min)
.\medir-codigo.ps1 -Completo  # todos los casos de desarrollo y validación
.\medir-codigo.ps1 -Motores native rust   # solo estos descompiladores
```

- Corpus fijo: `corpus/parity-20261014` (sellado con hashes; con fuente original y traza esperada).
- Salida: `reports/bench/<ejecución>/results.json` (decenas de KB) e `index.json`. La pestaña Benchmarks de la app lo lee.
- Cada celda sin número lleva su motivo (no instalado, no lee ese lenguaje, fuente parcial…). No se declara ganador si la ventaja es de un caso o menos, ni entre motores medidos sobre casos distintos.
- Pendiente: haxeflixel (necesita lime), ida y vuelta de PySWF desde P-code, corpus más grande con más recursos.

## Cercanía a JPEXS (solo descompiladores)

Además de las cuentas de comportamiento, cada descompilador lleva dos números frente a JPEXS, que actúa de referencia: **cerca JPEXS** es la similitud media del texto generado con el de JPEXS, unidad por unidad (se ignoran comentarios, espacios, comillas y `1` frente a `1.0`), y **cubre JPEXS** es la fracción de las unidades de JPEXS que el motor también emitió. Solo se comparan unidades cuyo `document_id`, ámbito y hash de bytecode coinciden con el inventario canónico del SWF; las que no verifican se excluyen y se cuentan. Es una medida de parecido, no de corrección: JPEXS también se equivoca, y un texto distinto puede comportarse igual. **El ganador sigue decidiéndose solo por comportamiento** (recompila y misma traza en Ruffle). Si JPEXS no está instalado o falla en un caso, esas celdas quedan en blanco con su motivo (`jpexs_reason` en `results.json`); la línea de JPEXS vale 1,0 por definición y se marca `reference: true`.
