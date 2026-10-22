param(
    [string]$Commit = "792d2c2fc886ddac030c601d286cf179b11b59c4",
    [string]$Repository = "https://github.com/jindrapetrik/jpexs-decompiler.git",
    [string]$Path = "$PSScriptRoot\..\third_party\jpexs-decompiler"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedPath = [IO.Path]::GetFullPath($Path)
if (-not $resolvedPath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "El destino debe estar dentro del workspace: $resolvedPath"
}
if (Test-Path -LiteralPath $resolvedPath) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedPath ".git"))) {
        throw "El destino existe pero no es un submódulo Git. Presérvalo/muévelo manualmente antes de continuar."
    }
    git -C $resolvedPath fetch --depth 1 origin $Commit
    git -C $resolvedPath checkout --detach $Commit
} else {
    git submodule add -f $Repository $resolvedPath
    git -C $resolvedPath checkout --detach $Commit
}
if ($LASTEXITCODE -ne 0) { throw "No se pudo fijar JPEXS al commit $Commit" }
$env:PY_SWF_JPEXS_COMMIT = $Commit
python (Join-Path $PSScriptRoot "verify_jpexs.py")
