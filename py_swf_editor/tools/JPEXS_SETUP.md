# Configurar el motor JPEXS opcional

El backend Python funciona sin Java. Para activar el motor JPEXS, instala o
compila FFDec por separado y configura una de estas variables:

```powershell
$env:PY_SWF_FFDEC = "C:\tools\ffdec\ffdec.bat"
# Alternativa:
$env:PY_SWF_FFDEC_JAR = "C:\tools\ffdec\ffdec.jar"
$env:PY_SWF_JPEXS_TIMEOUT = "120"
python -m server
```

El servidor llama a `tools/jpexs_bridge.py`. Este recibe una petición JSON por
línea, escribe el SWF en un directorio temporal, ejecuta FFDec en modo CLI y
devuelve las fuentes `.as` encontradas como JSON. Los temporales se eliminan
al terminar cada petición.

## Bridge Java persistente (recomendado para la distribución)

El repositorio incluye el código de un adaptador que usa directamente la API
`ffdec_lib` de JPEXS. Compílalo contra el JAR de la revisión fijada:

```powershell
.\tools\build_jpexs_bridge.ps1 -JpexsLibJar C:\tools\ffdec_lib.jar
$env:PY_SWF_FFDEC_LIB_JAR = "C:\tools\ffdec_lib.jar"
$env:PY_SWF_JPEXS_JAVA_BRIDGE = "C:\ruta\al\proyecto\tools\jpexs-java-bridge.jar"
```

Con esas variables, el bridge Python mantiene una JVM persistente y reenvía
JSONL al adaptador Java. Si no están configuradas, conserva el modo CLI como
fallback compatible.

Para comprobar el estado:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/files/engines/status
```

La UI ofrece tres opciones al abrir `Source`: `Auto` (JPEXS y fallback a
Python), `Python` o `JPEXS` (error explícito si no está configurado).

El JAR, Java y los binarios de FFDec no se incluyen en este repositorio.
Consulta la licencia GPLv3 de JPEXS antes de redistribuirlos junto con una
versión empaquetada.

El checkout reproducible recomendado usa el commit
`792d2c2fc886ddac030c601d286cf179b11b59c4`:

```powershell
.\tools\setup_jpexs.ps1
```

El script no sobrescribe un directorio existente que no sea un submódulo Git;
eso evita perder una copia local de fuentes o pruebas.
