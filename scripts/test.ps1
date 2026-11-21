param([switch]$HistoricalSuite)
. (Join-Path $PSScriptRoot 'common.ps1')
$pythonExecutable = Find-VenvPython
$manager = Find-PackageManager (Find-Node)
Push-Location (Assert-DevPath $script:ProjectRoot)
try {
    $testTarget = if ($HistoricalSuite) { 'backend/tests' } else { 'backend/tests/test_runtime_isolation.py' }
    if ($HistoricalSuite) { Write-Warning 'Historical tests include old cloud-provider and port expectations. See docs/PHASE2A-TESTING.md before interpreting failures.' }
    & $pythonExecutable -m pytest $testTarget -q -p no:cacheprovider --basetemp (Assert-DevPath (Join-Path $script:ProfileRoot 'temp\pytest'))
    if ($LASTEXITCODE -ne 0) { throw 'DEV backend tests failed.' }
} finally { Pop-Location }
Push-Location (Assert-DevPath (Join-Path $script:ProjectRoot 'frontend'))
try { Invoke-PackageManager $manager @('run', 'build') } finally { Pop-Location }
Write-Host 'Installer DEV backend tests and frontend build passed.' -ForegroundColor Green
