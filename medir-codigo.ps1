# Una orden: fuente generada -> compiladores -> Ruffle acotado -> catálogo.
# Se reutilizan únicamente trabajos con las mismas entradas y dependencias.
[CmdletBinding()]
param(
    [string]$Corpus = 'benchmark_inputs/parity-20261014/optimized',
    [string]$Salida = 'reports/semantic-benchmarks/latest',
    [string[]]$Motores = @('native','rust','jpexs','pyflashkit','flare','rabcdasm','flasm','flex_swfdump'),
    [ValidateSet('development','validation','holdout')]
    [string[]]$Particiones = @('development'),
    [string[]]$Familias,
    [ValidateRange(1,120)][int]$Ticks = 1,
    [switch]$TodasLasVariantes,
    [switch]$Forzar,
    [switch]$SinImportar
)
$ErrorActionPreference = 'Stop'
$workspaceSemantic = $PSScriptRoot
$projectSemantic = Join-Path $workspaceSemantic 'py_swf_editor'
$pythonSemantic = Join-Path $projectSemantic '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonSemantic -PathType Leaf)) {
    throw 'Falta py_swf_editor/.venv. Prepare el entorno virtual del proyecto.'
}
if (-not [IO.Path]::IsPathRooted($Corpus)) { $Corpus = Join-Path $workspaceSemantic $Corpus }
if (-not [IO.Path]::IsPathRooted($Salida)) { $Salida = Join-Path $workspaceSemantic $Salida }
if (-not (Test-Path -LiteralPath (Join-Path $Corpus 'manifest.json') -PathType Leaf)) {
    throw ('Falta el manifiesto del corpus: ' + $Corpus)
}
$argumentsSemantic = @((Join-Path $projectSemantic 'tools/run_semantic_benchmarks.py'),
    '--corpus', $Corpus, '--output', $Salida, '--ticks', "$Ticks",
    '--tool-config', (Join-Path $projectSemantic '.dev-references/extra-tools.json'), '--engines')
$argumentsSemantic += $Motores
$argumentsSemantic += '--splits'
$argumentsSemantic += $Particiones
if ($Familias) { $argumentsSemantic += '--families'; $argumentsSemantic += $Familias }
if ($TodasLasVariantes) { $argumentsSemantic += '--all-variants' }
if ($Forzar) { $argumentsSemantic += '--force' }
Push-Location -LiteralPath $projectSemantic
try {
    & $pythonSemantic @argumentsSemantic
    if ($LASTEXITCODE -ne 0) { throw ('La evaluación terminó con código ' + $LASTEXITCODE + '. Revise sus evidencias en ' + $Salida) }
    if (-not $SinImportar) {
        $reportSemantic = Join-Path $Salida 'benchmark.json'
        if (-not (Test-Path -LiteralPath $reportSemantic -PathType Leaf)) {
            throw ('Falta el informe importable: ' + $reportSemantic)
        }
        & $pythonSemantic (Join-Path $projectSemantic 'tools/import_semantic_report.py') --report $reportSemantic --database (Join-Path $workspaceSemantic 'reports/benchmark-catalog/benchmark.sqlite')
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo actualizar el catálogo de la suite.' }
    }
} finally { Pop-Location }
