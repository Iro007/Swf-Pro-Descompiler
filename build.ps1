param(
    [switch]$Installer,
    [switch]$SkipTests,
    [switch]$SkipDependencyInstall,
    [switch]$Offline,
    [string]$SuiteWorkerPath,
    [ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedWorkerSha256
)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
. .\tools\env.ps1
function Check-Exit {if($LASTEXITCODE -ne 0){throw "La operación anterior falló: $LASTEXITCODE"}}
function Copy-Verified([string]$Source,[string]$Destination,[string]$ExpectedSha256){
    if((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedSha256){throw "El artefacto fuente cambió: $Source"}
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    if((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedSha256){throw "La copia no coincide con su SHA-256: $Destination"}
}
$appConfig=Get-Content -LiteralPath '.\src-tauri\tauri.conf.json' -Raw | ConvertFrom-Json
$appVersion=$appConfig.version
$webVersion=(Get-Content -LiteralPath '.\ui\package.json' -Raw | ConvertFrom-Json).version
if($appVersion -ne $webVersion){throw "Las versiones de Tauri y web no coinciden"}
$cargoMode=@('--locked')
if($Offline){$cargoMode+='--offline'}
$pythonProject=Join-Path $PSScriptRoot 'engines\pyswf'
if(-not $SuiteWorkerPath){$SuiteWorkerPath=Join-Path $pythonProject 'dist\FlashSuiteWorker.exe'}
if(-not(Test-Path -LiteralPath $SuiteWorkerPath -PathType Leaf)){throw "Falta el worker universal: $SuiteWorkerPath. Compile engines/pyswf/packaging/suite_worker.spec antes de distribuir la suite."}
$SuiteWorkerPath=(Resolve-Path -LiteralPath $SuiteWorkerPath).Path
$workerSha256=(Get-FileHash -LiteralPath $SuiteWorkerPath -Algorithm SHA256).Hash.ToLowerInvariant()
if($ExpectedWorkerSha256 -and $workerSha256 -ne $ExpectedWorkerSha256.ToLowerInvariant()){throw "El worker no coincide con el SHA-256 de esta entrega"}
New-Item -ItemType Directory -Force '.\src-tauri\binaries' | Out-Null
# suite_worker.spec produces a onefile executable with its own Python runtime.
Copy-Verified $SuiteWorkerPath '.\src-tauri\binaries\FlashSuiteWorker.exe' $workerSha256
node tools\make-icon.mjs
Check-Exit
if(-not $SkipDependencyInstall){npm.cmd ci --prefix ui;Check-Exit}
npm.cmd run lint --prefix ui
Check-Exit
npm.cmd run build --prefix ui
Check-Exit
if(-not $SkipTests){cargo test @cargoMode -p swf-core -p swf-avm -p swf-decompile -p swf-cli -p rust-swf-studio;Check-Exit}
cargo build @cargoMode --release -p swf-cli
Check-Exit
$rustCliPath=Join-Path $PSScriptRoot 'target\x86_64-pc-windows-gnu\release\rustswf.exe'
$capabilities=& $rustCliPath capabilities | ConvertFrom-Json
Check-Exit
if($capabilities.display_name -ne 'RustSWF' -or $capabilities.version -ne $appVersion){throw "La CLI RustSWF compilada no corresponde a la versión $appVersion"}
$rustCliSha256=(Get-FileHash -LiteralPath $rustCliPath -Algorithm SHA256).Hash.ToLowerInvariant()
New-Item -ItemType Directory -Force '.\src-tauri\binaries' | Out-Null
Copy-Verified $rustCliPath '.\src-tauri\binaries\rustswf.exe' $rustCliSha256
New-Item -ItemType Directory -Force reports | Out-Null
# UTF-8 without BOM in both Windows PowerShell 5.1 and PowerShell 7; '>' writes UTF-16 in 5.1.
$cargoMetadata=cargo metadata @cargoMode --filter-platform x86_64-pc-windows-gnu --format-version 1
Check-Exit
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'reports\cargo-metadata.json'),($cargoMetadata -join "`n"),(New-Object Text.UTF8Encoding $false))
$metadata=Get-Content reports\cargo-metadata.json -Raw | ConvertFrom-Json
$webviewPackage=$metadata.packages | Where-Object name -EQ 'webview2-com-sys' | Select-Object -First 1
$loaderPath=Join-Path (Split-Path $webviewPackage.manifest_path -Parent) 'x64\WebView2Loader.dll'
Copy-Item -LiteralPath $loaderPath -Destination '.\src-tauri\binaries\WebView2Loader.dll' -Force
node tools\collect-licenses.mjs
Check-Exit
$pythonNotices=Join-Path $PSScriptRoot 'dist\FlashUniversalStudio\licenses\flash-suite-python'
New-Item -ItemType Directory -Force $pythonNotices | Out-Null
foreach($name in @('LICENSE','requirements.txt','THIRD_PARTY_NOTICES.md')){
    $notice=Join-Path $pythonProject $name
    if(Test-Path -LiteralPath $notice -PathType Leaf){Copy-Item -LiteralPath $notice -Destination $pythonNotices -Force}
}
$pythonRuntime=Join-Path $pythonProject '.venv\Scripts\python.exe'
$pythonLicenseCollector=Join-Path $pythonProject 'tools\collect_suite_licenses.py'
$pythonImportToc=Join-Path $pythonProject 'build\suite_worker\PYZ-00.toc'
if((Test-Path -LiteralPath $pythonRuntime -PathType Leaf) -and (Test-Path -LiteralPath $pythonLicenseCollector -PathType Leaf) -and (Test-Path -LiteralPath $pythonImportToc -PathType Leaf)){
    & $pythonRuntime $pythonLicenseCollector
    Check-Exit
}
@{application='Flash Universal Studio';version=$appVersion;engine='PySWF';protocol_version=1;worker='FlashSuiteWorker.exe';cli_alias='PySWF.exe';cli_prefix='cli';sha256=$workerSha256;packaging='onefile'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $pythonNotices 'worker-manifest.json') -Encoding utf8
$ruffleAssets=Join-Path $PSScriptRoot 'ui\public\ruffle'
if(Test-Path -LiteralPath $ruffleAssets -PathType Container){
    $ruffleNotices=Join-Path $PSScriptRoot 'dist\FlashUniversalStudio\licenses\ruffle-web'
    New-Item -ItemType Directory -Force $ruffleNotices | Out-Null
    foreach($name in @('LICENSE_APACHE','LICENSE_MIT','README.md','package.json','PROVENANCE.json')){
        $notice=Join-Path $ruffleAssets $name
        if(Test-Path -LiteralPath $notice -PathType Leaf){Copy-Item -LiteralPath $notice -Destination $ruffleNotices -Force}
    }
}
if($Installer){Push-Location src-tauri;try{& ..\ui\node_modules\.bin\tauri.cmd build --target x86_64-pc-windows-gnu;Check-Exit}finally{Pop-Location}}else{cargo build @cargoMode --release -p rust-swf-studio;Check-Exit}
$destination=Join-Path $PSScriptRoot 'dist\FlashUniversalStudio'
New-Item -ItemType Directory -Force $destination | Out-Null
$mainPath=Join-Path $PSScriptRoot 'target\x86_64-pc-windows-gnu\release\rust-swf-studio.exe'
$mainSha256=(Get-FileHash -LiteralPath $mainPath -Algorithm SHA256).Hash.ToLowerInvariant()
Copy-Verified $mainPath (Join-Path $destination 'FlashUniversalStudio.exe') $mainSha256
Copy-Verified $rustCliPath (Join-Path $destination 'rustswf.exe') $rustCliSha256
Copy-Item -LiteralPath '.\target\x86_64-pc-windows-gnu\release\WebView2Loader.dll' -Destination $destination -Force
Copy-Verified $SuiteWorkerPath (Join-Path $destination 'FlashSuiteWorker.exe') $workerSha256
Copy-Verified $SuiteWorkerPath (Join-Path $destination 'PySWF.exe') $workerSha256
Copy-Item -LiteralPath '.\LICENSE','.\THIRD_PARTY_NOTICES.md','.\README.md','.\docs\RELEASE-0.3.0.md' -Destination $destination -Force
Copy-Item -LiteralPath (Join-Path $pythonProject 'docs\SUITE_CLI.md') -Destination $destination -Force
@'
& (Join-Path $PSScriptRoot 'PySWF.exe') cli @args
exit $LASTEXITCODE
'@ | Set-Content -LiteralPath (Join-Path $destination 'pyswf.ps1') -Encoding utf8
$capabilities | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $destination 'rustswf-capabilities.json') -Encoding utf8
# Explicit inputs prevent an old sidecar or unrelated file in dist from entering the ZIP.
$packageNames=@('FlashUniversalStudio.exe','rustswf.exe','FlashSuiteWorker.exe','PySWF.exe','pyswf.ps1','WebView2Loader.dll','LICENSE','THIRD_PARTY_NOTICES.md','README.md','RELEASE-0.3.0.md','SUITE_CLI.md','rustswf-capabilities.json')
$packagePaths=@($packageNames | ForEach-Object {Join-Path $destination $_})
$licensePath=Join-Path $destination 'licenses'
$licenseFiles=@(Get-ChildItem -LiteralPath $licensePath -File -Recurse)
$hashFiles=@($packagePaths | ForEach-Object {Get-Item -LiteralPath $_}) + $licenseFiles
$artifactManifest=@($hashFiles | Sort-Object FullName | ForEach-Object {
    # Substring instead of [IO.Path]::GetRelativePath, which Windows PowerShell 5.1 lacks.
    @{path=$_.FullName.Substring((Resolve-Path -LiteralPath $destination).Path.TrimEnd('\').Length+1).Replace('\','/');bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
})
@{schema_version=1;application='Flash Universal Studio';version=$appVersion;engines=@('RustSWF','PySWF');protocol_version=1;verification_scope='Build, CLI capabilities and artifact hashes; no GUI launched';artifacts=$artifactManifest} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $destination 'release-manifest.json') -Encoding utf8
# Windows' own tar.exe (bsdtar, Windows 10+) writes ZIP from the -a suffix; the
# explicit System32 path avoids Git's GNU tar on PATH. Compress-Archive
# fails on these files under Windows PowerShell 5.1.
$zipPath=Join-Path $PSScriptRoot 'dist\FlashUniversalStudio-portable.zip'
if(Test-Path -LiteralPath $zipPath){Remove-Item -LiteralPath $zipPath -Force}
& (Join-Path $env:SystemRoot "System32\tar.exe") -a -c -f $zipPath -C $destination @($packageNames+@('release-manifest.json','licenses'))
Check-Exit
$zipSha256=(Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
@{version=$appVersion;worker_sha256=$workerSha256;rust_cli_sha256=$rustCliSha256;main_sha256=$mainSha256;portable_sha256=$zipSha256;verification_scope='No GUI launch'} | ConvertTo-Json | Set-Content -LiteralPath '.\reports\release-artifact-hashes.json' -Encoding utf8
Write-Output "Portable: $destination\FlashUniversalStudio.exe"

