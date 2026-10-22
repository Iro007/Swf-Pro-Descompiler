$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BootstrapDir = Join-Path $Root "tools\bootstrap"
$GoRoot = Join-Path $BootstrapDir "go"
$GoExe = Join-Path $GoRoot "bin\go.exe"

if (-not (Test-Path $GoExe)) {
    New-Item -ItemType Directory -Force -Path $BootstrapDir | Out-Null
    $ZipPath = Join-Path $BootstrapDir "go1.23.6.windows-amd64.zip"
    if (-not (Test-Path $ZipPath)) {
        Write-Host "Downloading portable Go 1.23.6 (windows-amd64)..."
        Invoke-WebRequest -Uri "https://go.dev/dl/go1.23.6.windows-amd64.zip" -OutFile $ZipPath -UseBasicParsing
    }
    Write-Host "Extracting portable Go toolchain..."
    & tar.exe -xf $ZipPath -C $BootstrapDir
}

$env:GOROOT = $GoRoot
$env:PATH = "$GoRoot\bin;$env:PATH"
& $GoExe version
