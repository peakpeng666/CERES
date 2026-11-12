# Double-click launcher: use the same installer and saved selection as PowerShell.
[CmdletBinding()]
param([string]$Ref = $env:CERES_REF)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Installer = Join-Path $Root 'install.ps1'
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "找不到安装入口：$Installer"
}
# Call a file directly. Paths with spaces, Unicode, or quotes remain data.
& $Installer -Command up -Ref $Ref
