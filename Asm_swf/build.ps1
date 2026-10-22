$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $Root
$MinGWBin = Join-Path $RepoRoot "tools\bootstrap\mingw\mingw64\bin"
$GccExe = Join-Path $MinGWBin "gcc.exe"

if (-not (Test-Path $GccExe)) {
    throw "MinGW gcc.exe not found at $GccExe. Run bootstrap.ps1 first."
}

$env:PATH = "$MinGWBin;$env:PATH"
$DistDir = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
$OutExe = Join-Path $DistDir "asmswf.exe"

& $GccExe -O3 -Wall -Wextra -Wno-unused-parameter -s `
    (Join-Path $Root "src\main.c") `
    (Join-Path $Root "src\swf_fast.S") `
    -o $OutExe

Write-Host "Built $OutExe"
$Hash = (Get-FileHash -Algorithm SHA256 $OutExe).Hash.ToLowerInvariant()
Write-Host "SHA256: $Hash"
