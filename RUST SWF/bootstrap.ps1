$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$bootstrap=Join-Path $PSScriptRoot 'tools\bootstrap'
New-Item -ItemType Directory -Force $bootstrap | Out-Null
function Download-Verified($url,$destination,$sha){if(-not(Test-Path -LiteralPath $destination)){Invoke-WebRequest -Uri $url -OutFile $destination};if((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha){throw "SHA-256 no coincide: $destination"}}
$cargoBinary=Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
if(-not(Test-Path $cargoBinary)){
  $initializer=Join-Path $bootstrap 'rustup-init.exe'
  Download-Verified 'https://static.rust-lang.org/rustup/archive/1.29.1/x86_64-pc-windows-msvc/rustup-init.exe' $initializer '6f4bef66261261fcb43131be8720bab817d403a09edec7455c371974b90bdb7e'
  & $initializer -y --default-toolchain 1.99.0-x86_64-pc-windows-gnu --profile minimal --no-modify-path
  if($LASTEXITCODE -ne 0){throw 'No se pudo preparar Rust'}
}
$gccBinary=Join-Path $bootstrap 'mingw\mingw64\bin\gcc.exe'
if(-not(Test-Path $gccBinary)){
  $archive=Join-Path $bootstrap 'mingw.7z'
  Download-Verified 'https://github.com/niXman/mingw-builds-binaries/releases/download/16.2.0-rt_v14-rev1/x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z' $archive '26788998d615856a915e1eeacb0854fce87bc608fa9a0c16f96bcbfdcf3415c0'
  $extractor=Join-Path $bootstrap '7zr.exe'
  if(-not(Test-Path $extractor)){Invoke-WebRequest -Uri 'https://www.7-zip.org/a/7zr.exe' -OutFile $extractor}
  & $extractor x $archive "-o$(Join-Path $bootstrap 'mingw')" -y
  if($LASTEXITCODE -ne 0){throw 'No se pudo extraer MinGW'}
}
. .\tools\env.ps1
rustup toolchain install 1.99.0-x86_64-pc-windows-gnu --profile minimal --component rustfmt --component clippy
if($LASTEXITCODE -ne 0){throw 'No se pudo preparar el toolchain GNU'}
node --version
cargo --version
gcc --version | Select-Object -First 1
Write-Output 'Listo. Ejecuta .\build.ps1 para compilar y verificar.'
