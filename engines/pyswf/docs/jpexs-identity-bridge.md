# Identidad y scopes del puente JPEXS 26.3.0

Los puentes de desarrollo y de aplicación llaman las APIs públicas del JPEXS
fijado. Comparten `ParityBridgeInventory.java`, que contiene únicamente lectura
estructural de identidades y la asociación con la procedencia del writer.
No contiene algoritmos de descompilación ni sustitutos de la salida JPEXS.

## Funciones AVM1 anidadas

El recorrido de los bytes originales de cada `ASMSource` identifica
DefineFunction/DefineFunction2, With y Try. Conserva body offset, hash, parámetros,
registros, register_count, flags y constant pool. Su base es
`ByteArrayRange.getPos()`, referida al SWF descomprimido original, incluyendo
encabezados de tags largos y timelines de sprites.

`OffsetWriter` conserva el `GraphSourceItem` activo cuando JPEXS entra en
`FunctionActionItem.startFunction`. Anota su `fileOffset` en el highlight de la
función. Solo un highlight con el offset exacto de la acción que define esa
función puede asociarse a su cuerpo. Los nombres y el orden de líneas no son
claves de asociación. Las funciones sin un span único siguen contadas con su
identidad binaria, `generation: failed` y `source_mapping: unverified`.

El texto conserva el contexto del decompilador de la acción padre. Una función
aislada sigue siendo de lectura: editarla requiere recompilar la acción padre
y verificar el contexto capturado. `pool_runtime_verified` distingue el linaje
lineal del pool de los flujos cuya captura no queda demostrada por ese recorrido.
La presencia de fuente no verifica compilación ni comportamiento.

## Clases, scripts y métodos AVM2

Las clases y scripts se emiten como observaciones propias, con IDs
`tag:<ruta>/class:<índice>` y `tag:<ruta>/script:<índice>`. El hash del ABC se
obtiene de los bytes originales del tag, no de una reserealización.
Los hashes agregados usan los cuerpos propios ordenados por method_index,
enmarcando cada uno con su longitud u32 little endian. Para clases incluye
iinit, cinit y traits de método/getter/setter/function; para scripts incluye
init y traits directos, manteniendo los métodos de clases fuera de ese agregado.

Una clase solo se marca completa cuando están representados todos los cuerpos
propios y no hay fallos de conversión. Los cuerpos abstractos sin method_body
permanecen representados por sus declaraciones. Los scripts requieren su
representación del initializer, traits directos y clases declaradas completas.
Los documentos parciales y ausentes conservan una observación con diagnóstico.

Los initializers o spans vacíos representados por declaraciones usan
`generation: represented_by_document`. No entran en la cobertura de fuente de
métodos. Cuando se solicita fuente, incluyen el P-code producido por la API
pública de JPEXS. Los documentos originales `/script:N/traits:[...]` se conservan
para el editor y para recompilación. Los IDs canónicos de clase/script aceptan
recompilación cuando resuelven un único pack; un script repartido entre varios
packs exige seleccionar el documento de traits exacto.

Los offsets de highlights se aplican al texto con CRLF normalizado a LF. El
documento completo conserva su texto original. Esto mantiene la correspondencia
UTF-16 del writer sin modificar lo que recibe el compilador.

## Compilación y verificación

`tools/build_reference_bridge.py` normaliza los paths relocatables antes de
ejecutar javac y registra todos los `.class` del helper compartido. El build
de aplicación conserva su fuente autoritativa en
`tools/jpexs_application_bridge/UniversalBridge.java`, la copia generada en
`vendor/toolpacks/jpexs-bridge`, y su lock independiente. La versión y commit de JPEXS
se mantienen fijados.

`tests/test_jpexs_parity_bridge.py` ejecuta ambos puentes reales sobre las 17
funciones de las fixtures conservadas, Function2 con preload/register context,
constant pools Unicode, sprites anidados FWS/CWS/ZWS, nombres duplicados,
clases/scripts con hashes exactos, dos ABC con clases del mismo nombre y
recompilación por el ID canónico de clase. Estos checks no son un perfil de
rendimiento ni una afirmación de equivalencia semántica.
