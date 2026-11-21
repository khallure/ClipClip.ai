[CmdletBinding()]
param(
    [switch]$ValidateOnly,
    [switch]$DryRun,
    [switch]$JsonSummary,
    [string]$InputPath = '',
    [string]$WritePreviewPath = ''
)

$ErrorActionPreference = 'Stop'

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$markerPath = Join-Path $projectRoot 'podcut-dev-workspace.json'
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
    throw 'Workspace marker podcut-dev-workspace.json is missing.'
}

$marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($marker.schema -ne 1 -or $marker.kind -cne 'podcut-multipc-dev') {
    throw 'Workspace marker does not match podcut-multipc-dev schema 1.'
}

$pythonExe = Join-Path $projectRoot '.tools\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw 'Portable Python 3.12 executable (.tools\python\python.exe) is missing.'
}

$cliPath = Join-Path $projectRoot 'scripts\live_handoff\cli.py'
if (-not (Test-Path -LiteralPath $cliPath -PathType Leaf)) {
    throw 'Offline live handoff CLI (scripts\live_handoff\cli.py) is missing.'
}

if ($ValidateOnly -and $DryRun) {
    throw 'Specify either -ValidateOnly or -DryRun, not both.'
}

$pyArgs = @('-B', '-I', $cliPath, '--workspace-root', $projectRoot)
if ($ValidateOnly) {
    $pyArgs += '--validate-only'
} else {
    $pyArgs += '--dry-run'
}
if ($JsonSummary) {
    $pyArgs += '--json-summary'
}
if ($InputPath) {
    $pyArgs += @('--input', $InputPath)
}
if ($WritePreviewPath) {
    $pyArgs += @('--write-preview', $WritePreviewPath)
}

& $pythonExe @pyArgs
exit $LASTEXITCODE
