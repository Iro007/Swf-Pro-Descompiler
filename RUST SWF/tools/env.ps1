$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:PATH = "$projectRoot\tools\bootstrap\mingw\mingw64\bin;$env:USERPROFILE\.cargo\bin;$env:PATH"
# windres' internal preprocessor cannot quote -I paths containing spaces.
# A local junction gives build artifacts a stable path without spaces.
$sha=[System.Security.Cryptography.SHA256]::Create()
try{$rootDigest=(-join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot)) | ForEach-Object {$_.ToString('X2')})).Substring(0,12)}finally{$sha.Dispose()}
$aliasParent=Join-Path $env:LOCALAPPDATA 'RustSWFStudioBuild'
$targetAlias=Join-Path $aliasParent $rootDigest
$targetPath=Join-Path $projectRoot 'target'
New-Item -ItemType Directory -Force -Path $targetPath,$aliasParent | Out-Null
if(Test-Path -LiteralPath $targetAlias){
  $aliasItem=Get-Item -LiteralPath $targetAlias -Force
  if($aliasItem.LinkType -ne 'Junction' -or $aliasItem.Target -ne $targetPath){throw "Alias de compilación inesperado: $targetAlias"}
}else{New-Item -ItemType Junction -Path $targetAlias -Target $targetPath | Out-Null}
$env:CARGO_TARGET_DIR=$targetAlias

