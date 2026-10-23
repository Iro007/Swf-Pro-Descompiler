param(
    [switch]$SkipTests,
    [switch]$SkipDependencyInstall,
    [switch]$Offline,
    [switch]$SkipPackage,
    [switch]$BuildWorker,
    [string]$SuiteWorkerPath,
    [ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedWorkerSha256
)
# Flash Universal Studio (Windows): engines -> interface (ui\dist) -> Electron packages (desktop\dist).
#   RustSWF          cargo (GNU target, see tools\env.ps1)
#   PySWF worker     PyInstaller: -BuildWorker builds it, otherwise engines\pyswf\dist\FlashSuiteWorker.exe must exist
#   GoSWF, AsmSWF    engines\goswf\build.ps1, engines\asmswf\build.ps1; an engine that is not built stays out of the package
#   interface        Vite -> ui\dist
#   Electron         electron-builder in desktop\ -> portable and NSIS installer (desktop\dist)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
. .\tools\env.ps1
function Check-Exit {if($LASTEXITCODE -ne 0){throw "La operación anterior falló: $LASTEXITCODE"}}
function Copy-Verified([string]$Source,[string]$Destination,[string]$ExpectedSha256){
    if((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedSha256){throw "El artefacto fuente cambió: $Source"}
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    if((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedSha256){throw "La copia no coincide con su SHA-256: $Destination"}
}
$appVersion=(Get-Content -LiteralPath '.\desktop\package.json' -Raw | ConvertFrom-Json).version
$webVersion=(Get-Content -LiteralPath '.\ui\package.json' -Raw | ConvertFrom-Json).version
if($appVersion -ne $webVersion){throw "Las versiones de desktop y de la interfaz no coinciden"}
$cargoMode=@('--locked')
if($Offline){$cargoMode+='--offline'}
$pythonProject=Join-Path $PSScriptRoot 'engines\pyswf'
$pythonRuntime=Join-Path $pythonProject '.venv\Scripts\python.exe'
# suite_worker.spec produces a onefile executable with its own Python runtime; electron-builder packs it from here.
$packagedWorker=Join-Path $pythonProject 'dist\FlashSuiteWorker.exe'
if($BuildWorker){
    if(-not(Test-Path -LiteralPath $pythonRuntime -PathType Leaf)){throw 'Falta engines/pyswf/.venv: cree el entorno e instale ".[dev,build]" antes de usar -BuildWorker.'}
    Push-Location $pythonProject
    try{& $pythonRuntime -m PyInstaller --noconfirm --clean packaging\suite_worker.spec;Check-Exit}finally{Pop-Location}
}
if(-not $SuiteWorkerPath){$SuiteWorkerPath=$packagedWorker}
if(-not(Test-Path -LiteralPath $SuiteWorkerPath -PathType Leaf)){throw "Falta el worker universal: $SuiteWorkerPath. Use -BuildWorker o compile engines/pyswf/packaging/suite_worker.spec antes de distribuir la suite."}
$SuiteWorkerPath=(Resolve-Path -LiteralPath $SuiteWorkerPath).Path
$workerSha256=(Get-FileHash -LiteralPath $SuiteWorkerPath -Algorithm SHA256).Hash.ToLowerInvariant()
if($ExpectedWorkerSha256 -and $workerSha256 -ne $ExpectedWorkerSha256.ToLowerInvariant()){throw "El worker no coincide con el SHA-256 de esta entrega"}
# A worker taken from another folder is put where electron-builder looks for it, hash-checked.
$packagedWorkerFull=if(Test-Path -LiteralPath $packagedWorker -PathType Leaf){(Resolve-Path -LiteralPath $packagedWorker).Path}else{$null}
if($SuiteWorkerPath -ne $packagedWorkerFull){
    New-Item -ItemType Directory -Force (Split-Path $packagedWorker) | Out-Null
    Copy-Verified $SuiteWorkerPath $packagedWorker $workerSha256
}
foreach($optional in @('engines\goswf\dist\goswf.exe','engines\asmswf\dist\asmswf.exe')){
    if(-not(Test-Path -LiteralPath $optional -PathType Leaf)){Write-Warning "Sin compilar, queda fuera del paquete: $optional (ejecute el build.ps1 de ese motor)"}
}
if(-not $SkipDependencyInstall){npm.cmd ci --prefix ui;Check-Exit}
npm.cmd run lint --prefix ui
Check-Exit
npm.cmd run build --prefix ui
Check-Exit
# Electron host (desktop/): the harness that runs every engine as a JSONL worker.
if(-not $SkipDependencyInstall){npm.cmd ci --prefix desktop;Check-Exit}
if(-not $SkipTests){npm.cmd test --prefix desktop;Check-Exit}
if(-not $SkipTests){cargo test @cargoMode --workspace;Check-Exit}
cargo build @cargoMode --release -p swf-cli
Check-Exit
$rustCliPath=Join-Path $PSScriptRoot 'target\x86_64-pc-windows-gnu\release\rustswf.exe'
$capabilities=& $rustCliPath capabilities | ConvertFrom-Json
Check-Exit
if($capabilities.display_name -ne 'RustSWF' -or $capabilities.version -ne $appVersion){throw "La CLI RustSWF compilada no corresponde a la versión $appVersion"}
$rustCliSha256=(Get-FileHash -LiteralPath $rustCliPath -Algorithm SHA256).Hash.ToLowerInvariant()
New-Item -ItemType Directory -Force reports | Out-Null
# UTF-8 without BOM in both Windows PowerShell 5.1 and PowerShell 7; '>' writes UTF-16 in 5.1.
$cargoMetadata=cargo metadata @cargoMode --filter-platform x86_64-pc-windows-gnu --format-version 1
Check-Exit
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'reports\cargo-metadata.json'),($cargoMetadata -join "`n"),(New-Object Text.UTF8Encoding $false))
# Licence texts of every dependency: dist\licenses (rebuilt from scratch), which electron-builder packs as resources\licenses.
$licenseRoot=Join-Path $PSScriptRoot 'dist\licenses'
if(Test-Path -LiteralPath $licenseRoot){Remove-Item -LiteralPath $licenseRoot -Recurse -Force}
node tools\collect-licenses.mjs
Check-Exit
$pythonNotices=Join-Path $PSScriptRoot 'dist\licenses\flash-suite-python'
New-Item -ItemType Directory -Force $pythonNotices | Out-Null
foreach($name in @('LICENSE','requirements.txt','THIRD_PARTY_NOTICES.md')){
    $notice=Join-Path $pythonProject $name
    if(Test-Path -LiteralPath $notice -PathType Leaf){Copy-Item -LiteralPath $notice -Destination $pythonNotices -Force}
}
$pythonLicenseCollector=Join-Path $pythonProject 'tools\collect_suite_licenses.py'
$pythonImportToc=Join-Path $pythonProject 'build\suite_worker\PYZ-00.toc'
if((Test-Path -LiteralPath $pythonRuntime -PathType Leaf) -and (Test-Path -LiteralPath $pythonLicenseCollector -PathType Leaf) -and (Test-Path -LiteralPath $pythonImportToc -PathType Leaf)){
    & $pythonRuntime $pythonLicenseCollector
    Check-Exit
}
@{application='Flash Universal Studio';version=$appVersion;engine='PySWF';protocol_version=1;worker='FlashSuiteWorker.exe';sha256=$workerSha256;packaging='onefile'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $pythonNotices 'worker-manifest.json') -Encoding utf8
$ruffleAssets=Join-Path $PSScriptRoot 'ui\public\ruffle'
if(Test-Path -LiteralPath $ruffleAssets -PathType Container){
    $ruffleNotices=Join-Path $PSScriptRoot 'dist\licenses\ruffle-web'
    New-Item -ItemType Directory -Force $ruffleNotices | Out-Null
    foreach($name in @('LICENSE_APACHE','LICENSE_MIT','README.md','package.json','PROVENANCE.json')){
        $notice=Join-Path $ruffleAssets $name
        if(Test-Path -LiteralPath $notice -PathType Leaf){Copy-Item -LiteralPath $notice -Destination $ruffleNotices -Force}
    }
}
# Electron packages (portable and NSIS installer). electron-builder copies ui\dist, rustswf.exe, FlashSuiteWorker.exe,
# goswf.exe and asmswf.exe (when built), the engine manifests and the licences into the app's resources.
# The application icon is generated by the package's own predist script.
if(-not $SkipPackage){
    npm.cmd run dist --prefix desktop
    Check-Exit
    $desktopArtifacts=@(Get-ChildItem -LiteralPath 'desktop\dist' -Filter 'FlashUniversalStudio-*' -File | ForEach-Object {
        @{path=$_.Name;bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    })
    if($desktopArtifacts.Count -eq 0){throw 'electron-builder no produjo ningún paquete en desktop\dist'}
    @{version=$appVersion;worker_sha256=$workerSha256;rust_cli_sha256=$rustCliSha256;verification_scope='Build, CLI capabilities and artifact hashes; no GUI launched (see tools\verify-release.ps1)';artifacts=$desktopArtifacts} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '.\reports\desktop-artifact-hashes.json' -Encoding utf8
    foreach($artifact in $desktopArtifacts){Write-Output "Electron: desktop\dist\$($artifact.path)"}
}
