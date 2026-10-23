# Ejecute desde cualquier carpeta. Todas las rutas parten de este archivo.
# Inventario: .\flash-studio.ps1
# Aplicación: .\flash-studio.ps1 (Suite; 'Python' y 'Rust' son alias de la misma ventana)
# Comparar: .\flash-studio.ps1 -Accion Comparar -Corpus corpus -Perfil full
# Source y disassemble son operaciones distintas; vea docs/funciones_objetivo.json.
[CmdletBinding()]
param(
    [ValidateSet('Suite', 'Inventario', 'Funciones', 'Python', 'Rust', 'Comparar')]
    [string]$Accion = 'Suite',
    [string]$Salida,
    [switch]$Completo
)
$ErrorActionPreference = 'Stop'
$workspaceFlash = $PSScriptRoot
$pythonProjectFlash = Join-Path $workspaceFlash 'engines\pyswf'
$pythonFlash = Join-Path $pythonProjectFlash '.venv/Scripts/python.exe'
# 'Python' y 'Rust' abren la misma aplicación: PySWF y RustSWF forman parte de la suite.
if ($Accion -eq 'Suite' -or $Accion -eq 'Rust' -or $Accion -eq 'Python') {
    # La aplicación Electron: el paquete que genera build.ps1 (desktop/dist/win-unpacked) o, si no existe,
    # Electron sobre las fuentes (desktop/node_modules, tras `npm ci --prefix desktop` y compilar ui/dist).
    $suiteFlash = Join-Path $workspaceFlash 'desktop/dist/win-unpacked/Flash Universal Studio.exe'
    $suiteArguments = @()
    if (-not (Test-Path -LiteralPath $suiteFlash -PathType Leaf)) {
        $suiteFlash = Join-Path $workspaceFlash 'desktop/node_modules/electron/dist/electron.exe'
        $suiteArguments = @('"' + (Join-Path $workspaceFlash 'desktop') + '"')
        if (-not (Test-Path -LiteralPath (Join-Path $workspaceFlash 'ui/dist/index.html') -PathType Leaf)) {
            Write-Warning 'Falta ui/dist: la ventana mostrará solo la consola del arnés (npm run build --prefix ui).'
        }
    }
    if (-not (Test-Path -LiteralPath $suiteFlash -PathType Leaf)) { throw 'Falta la aplicación de la suite. Ejecute build.ps1 (o npm ci --prefix desktop).' }
    $suiteWorkspaceBeforeFlash = $env:FLASH_SUITE_WORKSPACE
    try {
        $env:FLASH_SUITE_WORKSPACE = $workspaceFlash
        $start = @{ FilePath = $suiteFlash; WorkingDirectory = (Split-Path $suiteFlash) }
        if ($suiteArguments.Count -gt 0) { $start.ArgumentList = $suiteArguments }
        Start-Process @start
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
    throw 'Falta el entorno Python: cree engines/pyswf/.venv e instale las dependencias del proyecto.'
}
if ($Salida -and -not [System.IO.Path]::IsPathRooted($Salida)) {
    $Salida = Join-Path $workspaceFlash $Salida
}
Push-Location -LiteralPath $pythonProjectFlash
try {
    if ($Accion -eq 'Inventario') {
        if (-not $Salida) { $Salida = Join-Path $workspaceFlash 'reports/herramientas-locales.json' }
        & $pythonFlash -m py_swf.cli workspace-tools --root $workspaceFlash --output $Salida
    } else {
        # Comparar: el benchmark de recompilación y traza (ver medir-codigo.ps1).
        & (Join-Path $workspaceFlash 'medir-codigo.ps1') -Completo:$Completo
    }
    if ($LASTEXITCODE -ne 0) { throw ('El CLI termino con codigo ' + $LASTEXITCODE) }
} finally {
    Pop-Location
}
