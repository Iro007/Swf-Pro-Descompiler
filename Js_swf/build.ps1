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
$OutExe = Join-Path $DistDir "jsswf.exe"

$SrcFiles = Get-ChildItem -Path (Join-Path $Root "src\*.mjs") | Sort-Object Name
$Combined = ($SrcFiles | ForEach-Object { (Get-FileHash -Algorithm SHA256 $_.FullName).Hash }) -join ":"
$HeaderPath = Join-Path $Root "src\bundle_hash.h"
Set-Content -Path $HeaderPath -Value "#define JSSWF_BUNDLE_HASH `"$Combined`"" -Encoding UTF8

& $GccExe -O3 -Wall -Wextra -s `
    (Join-Path $Root "src\launcher.c") `
    -o $OutExe

Write-Host "Built $OutExe"
$Hash = (Get-FileHash -Algorithm SHA256 $OutExe).Hash.ToLowerInvariant()
Write-Host "SHA256: $Hash"
