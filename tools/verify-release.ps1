param([string]$AppFolder=(Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\FlashUniversalStudio'))
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$corpus=Join-Path (Split-Path $projectRoot -Parent) 'py_swf\third_party\references\jpexs-26.3.0\libsrc\ffdec_lib\testdata'
$app=Join-Path $AppFolder 'FlashUniversalStudio.exe'
foreach($required in @($app,(Join-Path $AppFolder 'rustswf.exe'),(Join-Path $AppFolder 'WebView2Loader.dll'))){if(-not(Test-Path -LiteralPath $required)){throw "Falta componente portable: $required"}}
$cases=@(@{Name='as2';File='as2\as2.swf'},@{Name='as3';File='as3_new\bin\as3_new.flex.swf'},@{Name='zws';File='amf3\AmfTest.swf'},@{Name='resources';File='graphics\graphics.swf'})
$summary=@()
foreach($case in $cases){
  $source=Join-Path $corpus $case.File
  $before=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
  $report=Join-Path $projectRoot ("reports\release-verify-"+$case.Name+'-'+[guid]::NewGuid().ToString('N')+'.json')
  $process=Start-Process -FilePath $app -ArgumentList '--open',('"'+$source+'"'),'--smoke-report',('"'+$report+'"') -WorkingDirectory $env:TEMP -WindowStyle Hidden -PassThru
  try{
    $deadline=[DateTime]::UtcNow.AddSeconds(60)
    $result=$null
    while([DateTime]::UtcNow -lt $deadline){
      if(Test-Path -LiteralPath $report){try{$result=Get-Content -LiteralPath $report -Raw|ConvertFrom-Json}catch{$result=$null};if($result.verification){break}}
      $process.Refresh();if($process.HasExited){throw "El programa terminó antes de verificar $($case.Name)"}
      Start-Sleep -Milliseconds 200
    }
    if(-not $result.verification){throw "Timeout de verificación: $($case.Name)"}
    $after=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    if($before -ne $after){throw "El original fue modificado: $source"}
    if(-not $result.verification.success){throw "Falló $($case.Name): $(Get-Content -LiteralPath $report -Raw)"}
    $summary+=@{case=$case.Name;report=$report;original_sha256=$before;original_unchanged=$true;success=$true}
    Write-Output "PASS packaged $($case.Name): $report"
  }finally{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue}}
}
$summary|ConvertTo-Json -Depth 5|Set-Content -LiteralPath (Join-Path $projectRoot 'reports\release-verification-summary.json') -Encoding utf8
