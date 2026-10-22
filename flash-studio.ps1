# Ejecute desde cualquier carpeta. Todas las rutas parten de este archivo.
# Inventario: .\flash-studio.ps1
# Aplicación: .\flash-studio.ps1 (Suite; 'Python' y 'Rust' son alias de la misma ventana)
# Comparar: .\flash-studio.ps1 -Accion Comparar -Corpus benchmark_inputs -Perfil full
# Source y disassemble son operaciones distintas; vea docs/funciones_objetivo.json.
[CmdletBinding()]
param(
    [ValidateSet('Suite', 'Inventario', 'Funciones', 'Python', 'Rust', 'Comparar', 'Catalogo')]
    [string]$Accion = 'Suite',
    [string]$Corpus,
    [ValidateSet('source', 'disassemble')]
    [string]$Operacion = 'source',
    [ValidateSet('smoke', 'full')]
    [string]$Perfil = 'smoke',
    [string]$Motores = 'native,rust,jpexs',
    [string]$Salida,
    [string]$PaqueteNube,
    [ValidatePattern('^[a-fA-F0-9]{64}$')][string]$HashPaqueteNube
)
$ErrorActionPreference = 'Stop'
$workspaceFlash = $PSScriptRoot
$pythonProjectFlash = Join-Path $workspaceFlash 'py_swf_editor'
$pythonFlash = Join-Path $pythonProjectFlash '.venv/Scripts/python.exe'
# 'Python' y 'Rust' abren la misma aplicación: PySWF y RustSWF forman parte de la suite.
if ($Accion -eq 'Suite' -or $Accion -eq 'Rust' -or $Accion -eq 'Python') {
    # Entrega única de rust_swf/build.ps1; si no existe, la compilación de desarrollo.
    $suiteFlash = Join-Path $workspaceFlash 'rust_swf/dist/FlashUniversalStudio/FlashUniversalStudio.exe'
    if (-not (Test-Path -LiteralPath $suiteFlash -PathType Leaf)) {
        $suiteFlash = Join-Path $workspaceFlash 'rust_swf/target/x86_64-pc-windows-gnu/release/rust-swf-studio.exe'
    }
    if (-not (Test-Path -LiteralPath $suiteFlash -PathType Leaf)) { throw 'Falta el ejecutable de la suite. Ejecute rust_swf/build.ps1.' }
    $suiteWorkspaceBeforeFlash = $env:FLASH_SUITE_WORKSPACE
    try {
        $env:FLASH_SUITE_WORKSPACE = $workspaceFlash
        Start-Process -FilePath $suiteFlash -WorkingDirectory (Split-Path $suiteFlash)
    } finally {
        $env:FLASH_SUITE_WORKSPACE = $suiteWorkspaceBeforeFlash
    }
    return
}
if ($Accion -eq 'Funciones') {
    Get-Content -LiteralPath (Join-Path $workspaceFlash 'docs/funciones_objetivo.json') -Raw
    return
}
if (-not (Test-Path -LiteralPath $pythonFlash -PathType Leaf)) {
    throw 'Falta el entorno Python: cree py_swf_editor/.venv e instale las dependencias del proyecto.'
}
if ($Salida -and -not [System.IO.Path]::IsPathRooted($Salida)) {
    $Salida = Join-Path $workspaceFlash $Salida
}
Push-Location -LiteralPath $pythonProjectFlash
try {
    if ($Accion -eq 'Inventario') {
        if (-not $Salida) { $Salida = Join-Path $workspaceFlash 'reports/herramientas-locales.json' }
        & $pythonFlash -m py_swf.cli workspace-tools --root $workspaceFlash --output $Salida
    } elseif ($Accion -eq 'Catalogo') {
        $catalogArgsFlash = @((Join-Path $pythonProjectFlash 'tools/merge_benchmark_catalog.py'), '--workspace', $workspaceFlash)
        if ($Salida) { $catalogArgsFlash += @('--output', $Salida) }
        if ($PaqueteNube) {
            if (-not [System.IO.Path]::IsPathRooted($PaqueteNube)) { $PaqueteNube = Join-Path $workspaceFlash $PaqueteNube }
            $catalogArgsFlash += @('--cloud', $PaqueteNube)
        }
        if ($HashPaqueteNube) { $catalogArgsFlash += @('--cloud-sha256', $HashPaqueteNube) }
        & $pythonFlash @catalogArgsFlash
    } else {
        if (-not $Corpus) { throw 'Comparar requiere -Corpus con un SWF o una carpeta de SWF.' }
        # Las rutas relativas del corpus parten de la carpeta compartida.
        if (-not [System.IO.Path]::IsPathRooted($Corpus)) { $Corpus = Join-Path $workspaceFlash $Corpus }
        if (-not (Test-Path -LiteralPath $Corpus)) { throw ('No existe el corpus: ' + $Corpus) }
        if (-not $Salida) {
            $Salida = Join-Path $workspaceFlash ('reports/comparacion-' + $Operacion + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        }
        & $pythonFlash -m py_swf.cli benchmark $Corpus --engines $Motores --operation $Operacion --profile $Perfil --output $Salida --tool-config (Join-Path $pythonProjectFlash '.dev-references/extra-tools.json')
    }
    if ($LASTEXITCODE -ne 0) { throw ('El CLI termino con codigo ' + $LASTEXITCODE) }
} finally {
    Pop-Location
}
