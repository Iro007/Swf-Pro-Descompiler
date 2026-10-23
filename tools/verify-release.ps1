# Drives the packaged Electron application (desktop\dist\win-unpacked, made by build.ps1) over real SWF files:
# for each one it starts the program with --open and --smoke-report, the interface opens the file, shows a script
# and an image or shape, and the program writes what it saw and exits. The original must be unchanged afterwards.
# For the same check on the sources instead of the package: cd desktop; npm run smoke   (or npm run try-ui -- --smoke)
param(
    [string]$AppFolder=(Join-Path (Split-Path $PSScriptRoot -Parent) 'desktop\dist\win-unpacked'),
    [int]$TimeoutSeconds=120
)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$corpus=Join-Path $projectRoot 'vendor\jpexs-source\libsrc\ffdec_lib\testdata'
$app=Join-Path $AppFolder 'Flash Universal Studio.exe'
foreach($required in @($app,(Join-Path $AppFolder 'resources\ui\index.html'),(Join-Path $AppFolder 'resources\bin\rustswf.exe'),(Join-Path $AppFolder 'resources\bin\FlashSuiteWorker.exe'))){
    if(-not(Test-Path -LiteralPath $required)){throw "Falta componente del paquete: $required"}
}
$cases=@(@{Name='as2';File='as2\as2.swf'},@{Name='as3';File='as3_new\bin\as3_new.flex.swf'},@{Name='zws';File='amf3\AmfTest.swf'},@{Name='resources';File='graphics\graphics.swf'})
foreach($absent in @($cases | Where-Object {-not(Test-Path -LiteralPath (Join-Path $corpus $_.File) -PathType Leaf)})){Write-Warning "Sin SWF de verificación, se omite: $($absent.Name)"}
$cases=@($cases | Where-Object {Test-Path -LiteralPath (Join-Path $corpus $_.File) -PathType Leaf})
if($cases.Count -eq 0){throw "No hay SWF de verificación en $corpus"}
New-Item -ItemType Directory -Force (Join-Path $projectRoot 'reports') | Out-Null
$summary=@()
foreach($case in $cases){
    $source=Join-Path $corpus $case.File
    $before=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $report=Join-Path $projectRoot ("reports\release-verify-"+$case.Name+'-'+[guid]::NewGuid().ToString('N')+'.json')
    $process=Start-Process -FilePath $app -ArgumentList '--open',('"'+$source+'"'),'--smoke-report',('"'+$report+'"') -WorkingDirectory $env:TEMP -WindowStyle Hidden -PassThru
    try{
        if(-not $process.WaitForExit($TimeoutSeconds*1000)){throw "Timeout de verificación: $($case.Name)"}
        if(-not(Test-Path -LiteralPath $report)){throw "El programa terminó sin escribir el informe de $($case.Name) (código $($process.ExitCode))"}
        $result=Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
        $after=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        if($before -ne $after){throw "El original fue modificado: $source"}
        if($process.ExitCode -ne 0 -or -not $result.ok -or -not $result.checks.ui.success){throw "Falló $($case.Name): $(Get-Content -LiteralPath $report -Raw)"}
        $summary+=@{case=$case.Name;report=$report;original_sha256=$before;original_unchanged=$true;success=$true}
        Write-Output "PASS packaged $($case.Name): $report"
    }finally{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue}}
}
$summary|ConvertTo-Json -Depth 5|Set-Content -LiteralPath (Join-Path $projectRoot 'reports\release-verification-summary.json') -Encoding utf8
