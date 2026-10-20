# Descompilador híbrido Python (PySWF + JPEXS).
# Uso: .\flashdec-py.ps1 juego.swf [-o salida] [--engine best|native|jpexs] [--help]
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot 'py_swf_editor\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}
$env:PYTHONPATH = Join-Path $PSScriptRoot 'flashdec_py'
& $python -m flashdec @args
exit $LASTEXITCODE
