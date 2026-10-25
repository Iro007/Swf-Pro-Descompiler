# Benchmark: ¿lo que genera cada descompilador recompila y se comporta igual en Ruffle? ¿Y ante archivos dañados?
# Resultado: reports/bench/<ejecución>/results.json (lo muestra la pestaña Benchmarks).
#   .\medir-codigo.ps1                          rápido: un caso por familia de desarrollo
#   .\medir-codigo.ps1 -Completo                todos los casos de desarrollo y validación
#   .\medir-codigo.ps1 -Motores native rust     solo estos descompiladores (JPEXS se mide siempre)
#   .\medir-codigo.ps1 -Solo robustness         solo estas categorías (decompilers, robustness, unit_fidelity, compilers, ...)
#   .\medir-codigo.ps1 -Unidades 40             unidades por caso en la fidelidad por unidad (0 = todas)
[CmdletBinding()]
param(
    [switch]$Completo,
    [string[]]$Motores,
    [string[]]$Solo,
    [int]$Unidades = -1
)
$ErrorActionPreference = 'Stop'
$proyecto = Join-Path $PSScriptRoot 'engines\pyswf'
$python = Join-Path $proyecto '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Falta engines/pyswf/.venv. Prepare el entorno virtual del proyecto.' }
$argumentos = @((Join-Path $proyecto 'tools/bench.py'), $(if ($Completo) { '--full' } else { '--smoke' }))
if ($Motores) { $argumentos += '--engines'; $argumentos += $Motores }
if ($Solo) { $argumentos += '--only'; $argumentos += $Solo }
if ($Unidades -ge 0) { $argumentos += '--unit-cap'; $argumentos += "$Unidades" }
Push-Location -LiteralPath $proyecto
try { & $python @argumentos; exit $LASTEXITCODE } finally { Pop-Location }
