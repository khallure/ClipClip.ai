. (Join-Path $PSScriptRoot 'common.ps1')
$state = Read-DevState
if (-not $state) { Write-Host 'No Installer DEV process records were found.'; exit 0 }
# Validate every live record before stopping any process, including forged IDs.
Assert-AllLiveRecordsOwned $state
foreach ($record in @($state.processes)) {
    $count = Stop-OwnedProcessTree $record $state
    Write-Host ('Installer DEV ' + $record.role + ': stopped ' + $count + ' verified process(es).')
}
$null = Assert-DevPath $script:PidFile
Remove-Item -LiteralPath $script:PidFile
