# Benchmark: ¿lo que genera cada descompilador recompila y se comporta igual en Ruffle?
# Resultado: reports/bench/<ejecución>/results.json (lo muestra la pestaña Benchmarks).
[CmdletBinding()]
param(
    [switch]$Completo,
    [string[]]$Motores
)
$ErrorActionPreference = 'Stop'
$proyecto = Join-Path $PSScriptRoot 'engines\pyswf'
$python = Join-Path $proyecto '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Falta engines/pyswf/.venv. Prepare el entorno virtual del proyecto.' }
$argumentos = @((Join-Path $proyecto 'tools/bench.py'), $(if ($Completo) { '--full' } else { '--smoke' }))
if ($Motores) { $argumentos += '--engines'; $argumentos += $Motores }
Push-Location -LiteralPath $proyecto
try { & $python @argumentos; exit $LASTEXITCODE } finally { Pop-Location }
