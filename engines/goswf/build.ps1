$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $Root "bootstrap.ps1")

$GoRoot = Join-Path $Root "tools\bootstrap\go"
$GoExe = Join-Path $GoRoot "bin\go.exe"
$env:GOROOT = $GoRoot
$env:GOPATH = Join-Path $Root "tools\bootstrap\gopath"
$env:GOCACHE = Join-Path $Root "tools\bootstrap\gocache"
$env:PATH = "$GoRoot\bin;$env:PATH"

$DistDir = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
$OutExe = Join-Path $DistDir "goswf.exe"

Push-Location $Root
try {
    & $GoExe build -trimpath -ldflags="-s -w" -o $OutExe ./cmd/goswf
    Write-Host "Built $OutExe"
    $Hash = (Get-FileHash -Algorithm SHA256 $OutExe).Hash.ToLowerInvariant()
    Write-Host "SHA256: $Hash"
} finally {
    Pop-Location
}
