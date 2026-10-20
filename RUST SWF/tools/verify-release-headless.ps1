param(
    [string]$AppFolder=(Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\RustSWFStudio'),
    [string]$ZipPath=(Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\FlashUniversalStudio-portable.zip'),
    [switch]$RunCliChecks
)
$ErrorActionPreference='Stop'
$AppFolder=(Resolve-Path -LiteralPath $AppFolder).Path
$ZipPath=(Resolve-Path -LiteralPath $ZipPath).Path
$manifestPath=Join-Path $AppFolder 'release-manifest.json'
$manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if($manifest.schema_version -ne 1 -or -not $manifest.version){throw 'Manifiesto de release inválido'}
$known=@{}
foreach($artifact in $manifest.artifacts){
    $file=[IO.Path]::GetFullPath((Join-Path $AppFolder $artifact.path))
    $relative=[IO.Path]::GetRelativePath($AppFolder,$file)
    if([IO.Path]::IsPathRooted($relative) -or $relative -eq '..' -or $relative.StartsWith('..'+[IO.Path]::DirectorySeparatorChar)){throw "Ruta fuera del portable: $($artifact.path)"}
    if(-not(Test-Path -LiteralPath $file -PathType Leaf)){throw "Falta artefacto: $($artifact.path)"}
    if((Get-Item -LiteralPath $file).Length -ne $artifact.bytes -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $artifact.sha256){throw "Hash/tamaño incorrecto: $($artifact.path)"}
    if($known.ContainsKey($artifact.path)){throw "Artefacto duplicado: $($artifact.path)"}
    $known[$artifact.path]=$artifact
}
foreach($name in @('FlashUniversalStudio.exe','RustSWFStudio.exe','rustswf.exe','FlashSuiteWorker.exe','PySWF.exe','pyswf.ps1','WebView2Loader.dll','rustswf-capabilities.json')){
    if(-not $known.ContainsKey($name)){throw "Falta contrato portable: $name"}
}
if($known['FlashUniversalStudio.exe'].sha256 -ne $known['RustSWFStudio.exe'].sha256 -or $known['FlashSuiteWorker.exe'].sha256 -ne $known['PySWF.exe'].sha256){throw 'Los alias no coinciden con los ejecutables canónicos'}
$mainVersion=[Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $AppFolder 'FlashUniversalStudio.exe')).ProductVersion
if($mainVersion -ne $manifest.version){throw 'La versión del recurso PE de escritorio no corresponde al manifiesto'}
$capabilities=Get-Content -LiteralPath (Join-Path $AppFolder 'rustswf-capabilities.json') -Raw | ConvertFrom-Json
if($capabilities.version -ne $manifest.version -or $capabilities.display_name -ne 'RustSWF'){throw 'Capacidades/versiones RustSWF no corresponden a la entrega'}
$workerManifest=Get-Content -LiteralPath (Join-Path $AppFolder 'licenses\flash-suite-python\worker-manifest.json') -Raw | ConvertFrom-Json
if($workerManifest.version -ne $manifest.version -or $workerManifest.sha256 -ne $known['FlashSuiteWorker.exe'].sha256){throw 'Manifiesto Python no corresponde al worker entregado'}
Add-Type -AssemblyName System.IO.Compression
$zip=[IO.Compression.ZipFile]::OpenRead($ZipPath)
try{
    $seen=@{}
    foreach($entry in $zip.Entries){
        if(-not $entry.Name){continue}
        $name=$entry.FullName.Replace('\','/')
        if($seen.ContainsKey($name)){throw "Entrada ZIP duplicada: $name"}
        $seen[$name]=$true
        $expected=if($name -eq 'release-manifest.json'){@{bytes=(Get-Item -LiteralPath $manifestPath).Length;sha256=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()}}elseif($known.ContainsKey($name)){$known[$name]}else{throw "Entrada ZIP no registrada: $name"}
        $stream=$entry.Open()
        $hasher=[Security.Cryptography.SHA256]::Create()
        try{$sha=[Convert]::ToHexString($hasher.ComputeHash($stream)).ToLowerInvariant()}finally{$hasher.Dispose();$stream.Dispose()}
        if($entry.Length -ne $expected.bytes -or $sha -ne $expected.sha256){throw "ZIP diferente del portable: $name"}
    }
    if($seen.Count -ne $known.Count+1){throw 'El ZIP no contiene todos los artefactos del manifiesto'}
}finally{$zip.Dispose()}
function Invoke-ConsoleJson([string]$Executable,[string[]]$Arguments){
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=$Executable
    $start.WorkingDirectory=$env:TEMP
    $start.UseShellExecute=$false
    $start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true
    $start.RedirectStandardError=$true
    foreach($argument in $Arguments){$start.ArgumentList.Add($argument)}
    $process=[Diagnostics.Process]::Start($start)
    try{
        $output=$process.StandardOutput.ReadToEndAsync()
        $errors=$process.StandardError.ReadToEndAsync()
        if(-not $process.WaitForExit(30000)){$process.Kill($true);throw 'Timeout de CLI en verificación de release'}
        $stdout=$output.GetAwaiter().GetResult()
        $stderr=$errors.GetAwaiter().GetResult()
        if($process.ExitCode -ne 0){throw "La CLI terminó con error: $stderr"}
        return ($stdout | ConvertFrom-Json)
    }finally{$process.Dispose()}
}
$cliChecks='not requested'
if($RunCliChecks){
    $liveCapabilities=Invoke-ConsoleJson (Join-Path $AppFolder 'rustswf.exe') @('capabilities')
    if($liveCapabilities.version -ne $manifest.version -or $liveCapabilities.display_name -ne 'RustSWF'){throw 'La CLI RustSWF ejecutada no corresponde al manifiesto'}
    $engines=Invoke-ConsoleJson (Join-Path $AppFolder 'PySWF.exe') @('cli','engines')
    if(-not $engines -or $engines.ok -eq $false){throw 'La nueva consola PySWF no devolvió su catálogo'}
    $cliChecks='RustSWF capabilities and PySWF cli engines passed from TEMP; no GUI'
}
$report=@{version=$manifest.version;main_product_version=$mainVersion;artifact_count=$known.Count;all_hashes_verified=$true;zip_exact_manifest=$true;aliases_verified=$true;cli_checks=$cliChecks;gui_used=$false;zip_sha256=(Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()}
$reportPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'reports\release-headless-verification.json'
$report | ConvertTo-Json | Set-Content -LiteralPath $reportPath -Encoding utf8
$report | ConvertTo-Json
