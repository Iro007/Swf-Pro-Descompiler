# Consola de la suite: .\pyswf.ps1 engines | .\pyswf.ps1 inspect --input juego.swf
# Todas las opciones se entregan como argumentos individuales al worker.
$ErrorActionPreference = 'Stop'
$suiteWorkspace = $PSScriptRoot
$worker = Join-Path $suiteWorkspace 'dist\FlashUniversalStudio\FlashSuiteWorker.exe'
if (Test-Path -LiteralPath $worker -PathType Leaf) {
    $envBefore = $env:FLASH_SUITE_WORKSPACE
    try {
        $env:FLASH_SUITE_WORKSPACE = $suiteWorkspace
        & $worker cli @args
        exit $LASTEXITCODE
    } finally {
        $env:FLASH_SUITE_WORKSPACE = $envBefore
    }
}
$python = Join-Path $suiteWorkspace 'py_swf\.venv\Scripts\python.exe'
$entry = Join-Path $suiteWorkspace 'py_swf\tools\flash_suite_cli.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Falta PySWF empaquetado y el entorno .venv del proyecto.'
}
& $python $entry @args
exit $LASTEXITCODE
