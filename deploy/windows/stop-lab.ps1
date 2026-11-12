# Stop through the same installer, state directory, ref, and port as startup.
[CmdletBinding()]
param([string]$Ref = $env:CERES_REF)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Installer = Join-Path $Root 'install.ps1'
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "找不到安装入口：$Installer"
}
& $Installer -Command down -Ref $Ref
