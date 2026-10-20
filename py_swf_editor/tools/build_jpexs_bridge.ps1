param(
    [string]$JpexsLibJar = $env:PY_SWF_FFDEC_LIB_JAR,
    [string]$OutputJar = "$PSScriptRoot\jpexs-java-bridge.jar"
)

$ErrorActionPreference = "Stop"
if (-not $JpexsLibJar) {
    throw "Indica -JpexsLibJar o PY_SWF_FFDEC_LIB_JAR apuntando al JAR de ffdec_lib."
}
if (-not (Test-Path -LiteralPath $JpexsLibJar -PathType Leaf)) {
    throw "No existe el JAR de ffdec_lib: $JpexsLibJar"
}

$source = Join-Path $PSScriptRoot "jpexs_java_bridge\JPEXSJsonlBridge.java"
$build = Join-Path $PSScriptRoot ".jpexs-java-build"
New-Item -ItemType Directory -Force -Path $build | Out-Null
& javac -encoding UTF-8 -cp $JpexsLibJar -d $build $source
if ($LASTEXITCODE -ne 0) { throw "javac no pudo compilar el bridge." }
& jar cfe $OutputJar JPEXSJsonlBridge -C $build .
if ($LASTEXITCODE -ne 0) { throw "jar no pudo empaquetar el bridge." }
Write-Output "Bridge creado: $OutputJar"
