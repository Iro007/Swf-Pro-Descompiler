param(
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedWorkerSha256,
    [string]$WorkerPath,
    [string]$ExpectedVersion='0.3.0',
    [ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedMainSha256='c09fd90464790dfb48d1f740dc02bc443c986c2de44705883fce01ab1bcbf663',
    [ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedRustCliSha256='a3e9b62c5ec9eab6858117a2b28ee3a385923d035b39ea7efd3ad047c778328b'
)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$destination=Join-Path $projectRoot 'dist\FlashUniversalStudio'
if(-not $WorkerPath){$WorkerPath=Join-Path (Split-Path $projectRoot -Parent) 'py_swf_editor\dist\FlashSuiteWorker.exe'}
$WorkerPath=(Resolve-Path -LiteralPath $WorkerPath).Path
$ExpectedWorkerSha256=$ExpectedWorkerSha256.ToLowerInvariant()
function Assert-Hash([string]$Path,[string]$Expected){
    if(-not(Test-Path -LiteralPath $Path -PathType Leaf) -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected.ToLowerInvariant()){throw "El artefacto no corresponde al SHA esperado: $Path"}
}
function Copy-Verified([string]$Source,[string]$Target,[string]$Expected){
    Assert-Hash $Source $Expected
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    Assert-Hash $Target $Expected
}
# Refuse a stale main/CLI or an incomplete previous package before replacing workers.
Assert-Hash (Join-Path $destination 'FlashUniversalStudio.exe') $ExpectedMainSha256
Assert-Hash (Join-Path $destination 'rustswf.exe') $ExpectedRustCliSha256
Assert-Hash $WorkerPath $ExpectedWorkerSha256
$mainVersion=[Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $destination 'FlashUniversalStudio.exe')).ProductVersion
$manifest=Get-Content -LiteralPath (Join-Path $destination 'release-manifest.json') -Raw | ConvertFrom-Json
if($mainVersion -ne $ExpectedVersion -or $manifest.version -ne $ExpectedVersion){throw 'La versión de main/manifiesto no corresponde a este repack'}
& (Join-Path $PSScriptRoot 'verify-release-headless.ps1') -AppFolder $destination -ZipPath (Join-Path $projectRoot 'dist\FlashUniversalStudio-portable.zip') | Out-Null
Copy-Verified $WorkerPath (Join-Path $destination 'FlashSuiteWorker.exe') $ExpectedWorkerSha256
Copy-Verified $WorkerPath (Join-Path $destination 'PySWF.exe') $ExpectedWorkerSha256
Copy-Verified $WorkerPath (Join-Path $projectRoot 'desktop\binaries\FlashSuiteWorker.exe') $ExpectedWorkerSha256
$workerManifestPath=Join-Path $destination 'licenses\flash-suite-python\worker-manifest.json'
$workerManifest=Get-Content -LiteralPath $workerManifestPath -Raw | ConvertFrom-Json
$workerManifest.sha256=$ExpectedWorkerSha256
$workerManifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $workerManifestPath -Encoding utf8
$packageNames=@('FlashUniversalStudio.exe','rustswf.exe','FlashSuiteWorker.exe','PySWF.exe','pyswf.ps1','WebView2Loader.dll','LICENSE','THIRD_PARTY_NOTICES.md','README.md','RELEASE-0.3.0.md','SUITE_CLI.md','rustswf-capabilities.json')
$packagePaths=@($packageNames | ForEach-Object {Join-Path $destination $_})
$licensePath=Join-Path $destination 'licenses'
$hashFiles=@($packagePaths | ForEach-Object {Get-Item -LiteralPath $_}) + @(Get-ChildItem -LiteralPath $licensePath -File -Recurse)
$manifest.artifacts=@($hashFiles | Sort-Object FullName | ForEach-Object {
    @{path=[IO.Path]::GetRelativePath($destination,$_.FullName).Replace('\','/');bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$manifest.verification_scope='Repack worker assets; unchanged main/Rust CLI verified by pinned hashes; no GUI launched'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $destination 'release-manifest.json') -Encoding utf8
$portableZip=Join-Path $projectRoot 'dist\FlashUniversalStudio-portable.zip'
if(Test-Path -LiteralPath $portableZip){Remove-Item -LiteralPath $portableZip -Force}
& (Join-Path $env:SystemRoot "System32\tar.exe") -a -c -f $portableZip -C $destination @($packageNames+@('release-manifest.json','licenses'))
if($LASTEXITCODE -ne 0){throw "tar.exe falló: $LASTEXITCODE"}
$zipSha256=(Get-FileHash -LiteralPath $portableZip -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-Hash (Join-Path $destination 'FlashUniversalStudio.exe') $ExpectedMainSha256
Assert-Hash (Join-Path $destination 'rustswf.exe') $ExpectedRustCliSha256
@{version=$ExpectedVersion;worker_sha256=$ExpectedWorkerSha256;rust_cli_sha256=$ExpectedRustCliSha256;main_sha256=$ExpectedMainSha256;portable_sha256=$zipSha256;verification_scope='Worker assets repack; main and Rust CLI unchanged; no GUI launch'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'reports\release-artifact-hashes.json') -Encoding utf8
& (Join-Path $PSScriptRoot 'verify-release-headless.ps1') -AppFolder $destination -ZipPath $portableZip -RunCliChecks
