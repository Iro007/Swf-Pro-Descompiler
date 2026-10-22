# RustSWF: reconstrucción conservadora de ActionScript

`decompile(&swf_avm::ScriptUnit)` devuelve `structured` con una fuente completa
del método soportado, o `pcode_only` con `source: null`, P-code y diagnósticos.
El campo `structured` indica reconstrucción del subconjunto, no que se haya
recompilado el método ni que exista un proyecto ActionScript recompilable.

El pipeline valida límites, destinos de salto, bloques y postdominadores;
levanta operaciones a valores temporales evaluados en orden; combina las pilas
de ramas mediante asignaciones explícitas; y emite un AST de condiciones,
funciones AS2 básicas y bucles reducibles con condición en la cabecera. Un `for`
reducible de esa forma se conserva como `while` con inicialización y actualización.
Los patrones de cortocircuito conservan su evaluación condicional mediante ramas
y asignaciones, sin ejecutar el operando derecho cuando no corresponde.

Se reconstruye `lookupswitch` sólo con índice signed-int probado por un literal,
una operación intrínseca entera o una conversión explícita previa, y brazos
separados que retornan/lanzan. Las conversiones ocurren en su posición original;
la instrucción switch no añade coerciones a objetos sin prueba de tipo. Se
mantienen default y varios números de case que comparten exactamente un destino.

Las lecturas locales, getters, llamadas y operaciones se materializan en el punto
del bytecode. Duplicar un valor de pila reutiliza ese valor y no repite el efecto.
Los argumentos AVM1 se extraen en orden fuente; los AVM2 se invierten al extraerse.
Las búsquedas léxicas se aceptan solo si coinciden con la propiedad pública y no
se desplazan después de una operación observable en los argumentos.

Namespace y PackageNamespace públicos conservan su URI mediante declaraciones
AS3 generadas y selectores `alias::nombre`. Se colapsan namespace sets únicamente
si todas sus entradas públicas tienen la misma identidad URI. MultinameL/RTQName(L)
requiere claves primitivas conocidas (incluidos resultados signed-int intrínsecos)
y namespaces públicos constantes; namespaces que escapan de esa operación se
rechazan. Un parámetro que sombrea un nombre léxico no se convierte en ese binding.
PackageInternal, private, protected y explicit no se convierten en namespaces
públicos, aunque compartan el texto de URI. La identidad private es propia del
pool/contexto y requiere un modelo de clase antes de poder emitir una fuente fiel.

Las conversiones numéricas/booleanas e indefinidos usan operadores (`+`, `!!`,
`| 0`, `>>> 0`, `void 0`) y NaN usa `0 / 0`; no dependen de bindings llamados
Number, Boolean, int, uint, undefined o NaN. Una conversión String ante un
binding String posiblemente sombreado conserva el método entero como P-code.
No se sustituye ToString por concatenación porque cambia el hint de conversión.

Se conserva todo el método como P-code ante opcodes o nombres no soportados,
espacios de nombres no probados/ambiguos, `callproplex`, pila/scope inconsistentes,
handlers, switch con fallthrough/joins no soportados, activation, with, E4X, closures complejos, Function2 con
registros/preloads, constantes no representables, bucles con varias entradas o
salidas, o código inalcanzable no trivial. ConstantPool AS2 condicionado requiere
un decoder que siga cada ruta y por ahora produce P-code.

Límites: 50.000 instrucciones, 2.000 bloques, 128 niveles de regiones, 32 niveles
de funciones y 30 segundos compartidos entre funciones anidadas. El supervisor
externo sigue siendo necesario para acotar la descompilación y la lectura total.

Los tests usan bytecode crudo y Node como oráculo independiente para el subconjunto
ECMAScript compartido: orden de argumentos, lecturas antes de stores, calls/getters
duplicados, short circuit, condiciones de bucles, comparación con NaN y creación
de funciones en el momento correcto. No sustituyen un runtime Flash completo.

La enumeración AVM2 admite el kernel canónico `hasnext2` con `nextname` (`for in`)
o `nextvalue` (`for each`) y emite la construcción nativa de AS3. Exige índice
inicial cero, registros de objeto/índice privados, una sola lectura de enumeración,
ninguna exposición/mutación de esos registros en el cuerpo y ambos `kill` al salir.
El cuerpo conserva sus conversiones explícitas y debe mantener pila y scope.
Una conversión no soportada o una prueba fallida conserva el P-code completo.
Los fixtures Node de `for each` bajan únicamente la cabecera a un `for in` con
lectura inmediata del valor en objetos ordinarios; Flex CLI comprueba por separado
que la fuente AS3 recompilada contiene `hasnext2` y `nextvalue` nativos.

La prueba opcional Apache Flex CLI recompila tres fixtures de selectores y vuelve
a parsear el SWF: verifica URI pública, nombre y clave numérica conservados.
Se ejecuta con `--ignored` y las variables RUSTSWF_FLEX_JAVA, RUSTSWF_MXMLC_JAR y
RUSTSWF_PLAYERGLOBAL_HOME; usa Java headless sin ventanas. Es evidencia del
binding/encoding emitido, no una prueba general de equivalencia de Flash.
