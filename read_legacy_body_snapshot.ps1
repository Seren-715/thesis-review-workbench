<# Formal entry for the read-only legacy body snapshot helper.
Creates only a new evidence JSON; never alters original/derived Word files. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Original,
      [Parameter(Mandatory=$true)][string]$Derived,
      [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
$helper=Join-Path $PSScriptRoot 'diagnose_legacy_body_text.ps1'
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {throw 'Read-only body snapshot implementation is missing.'}
& $helper -SourcePath $Original -DerivedPath $Derived -ResultPath $Output
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {exit $LASTEXITCODE}
