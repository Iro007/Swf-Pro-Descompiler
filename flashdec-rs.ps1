# Descompilador híbrido Rust (RustSWF + JPEXS). Compila la primera vez.
# Uso: .\flashdec-rs.ps1 juego.swf [-o salida] [--engine best|native|jpexs] [--help]
$ErrorActionPreference = 'Stop'
$project = Join-Path $PSScriptRoot 'RUST SWF'
$exe = Join-Path $project 'target\x86_64-pc-windows-gnu\release\flashdec-rs.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    $env:PATH = (Join-Path $project 'tools\bootstrap\mingw\mingw64\bin') + ';' + (Join-Path $env:USERPROFILE '.cargo\bin') + ';' + $env:PATH
    Push-Location -LiteralPath $project
    try {
        cargo build --release -p flashdec
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar flashdec-rs' }
    } finally { Pop-Location }
}
$env:FLASHDEC_WORKSPACE = $PSScriptRoot
& $exe @args
exit $LASTEXITCODE
