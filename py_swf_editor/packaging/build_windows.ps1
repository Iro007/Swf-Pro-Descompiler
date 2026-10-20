param([switch]$SkipDependencies)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & python -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno Python" }
}
if (-not $SkipDependencies) {
    & $pythonExe -m pip install -e "$projectRoot[desktop,build]"
    if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias de build" }
}
Push-Location (Join-Path $projectRoot "web")
try {
    & npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci falló" }
    & npm run lint
    if ($LASTEXITCODE -ne 0) { throw "TypeScript falló" }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "Vite falló" }
} finally { Pop-Location }
Push-Location $projectRoot
try {
    & $pythonExe -m PyInstaller packaging\pyswf.spec --clean --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló" }
} finally { Pop-Location }
Write-Output "Distribución nativa creada: $projectRoot\dist\PySWFStudio.exe"
Write-Output "Incluye Python y la UI. No requiere Java/JPEXS."
