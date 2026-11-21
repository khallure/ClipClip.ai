param([switch]$NoBrowser)
. (Join-Path $PSScriptRoot 'common.ps1')
$null = Find-VenvPython
$null = Find-Node
$buildId = Assert-DevPath (Join-Path $script:ProjectRoot 'frontend\.next\BUILD_ID')
if (-not (Test-Path -LiteralPath $buildId -PathType Leaf)) { throw 'The DEV frontend build is missing. Run the scoped DEV setup first.' }
if (-not (Test-Path -LiteralPath (Assert-DevPath $script:BackendEnvFile))) { throw 'The external DEV backend configuration is missing.' }
$state = Read-DevState
if ($state) { Assert-AllLiveRecordsOwned $state }
$backendRecord = if ($state) { @($state.processes | Where-Object { $_.role -eq 'backend' }) | Select-Object -First 1 } else { $null }
$frontendRecord = if ($state) { @($state.processes | Where-Object { $_.role -eq 'frontend' }) | Select-Object -First 1 } else { $null }
Assert-DevPortAvailableOrOwned 8300 $backendRecord $state
Assert-DevPortAvailableOrOwned 3300 $frontendRecord $state
if (-not $state -or -not @($state.processes | Where-Object { Get-OwnedProcess $_ $state }).Count) {
    $random = New-Object byte[] 32; $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($random) } finally { $rng.Dispose() }
    $state = @{ schema = 2; workspace_id = $script:WorkspaceId; profile_id = $script:ProfileId; protocol = $script:Protocol; project_root = $script:ProjectRoot; profile_root = $script:ProfileRoot; run_id = [Guid]::NewGuid().ToString(); run_token = ([BitConverter]::ToString($random)).Replace('-', '').ToLowerInvariant(); processes = @() }
}
Assert-DevState $state
$env:PODCUT_RUN_ID = $state.run_id
$env:PODCUT_RUN_TOKEN = $state.run_token
$createdRecords = @()
try {
    foreach ($role in @('backend', 'frontend')) {
        $service = Get-ExpectedService $role $state.run_id
        $record = @($state.processes | Where-Object { $_.role -eq $role }) | Select-Object -First 1
        if (-not $record -or -not (Get-OwnedProcess $record $state)) {
            Assert-DevPortAvailableOrOwned $service.Port $null $null
            $stdout = Assert-DevPath (Join-Path $script:ProfileRoot ('logs\' + $role + '.stdout.log'))
            $stderr = Assert-DevPath (Join-Path $script:ProfileRoot ('logs\' + $role + '.stderr.log'))
            $process = Start-Process -FilePath $service.Executable -ArgumentList (ConvertTo-ProcessArguments $service.Arguments) -WorkingDirectory $service.Directory -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
            $record = New-OwnedProcessRecord $role $process $state
            $createdRecords += $record
            $state.processes = @($state.processes | Where-Object { $_.role -ne $role }) + @($record)
            Write-DevState $state
        }
        Assert-DevPortAvailableOrOwned $service.Port $record $state
        if ($role -eq 'backend') { Wait-DevIdentity $state }
    }
    Wait-DevIdentity $state -ThroughFrontend
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:3300' -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -ne 200) { throw 'The verified DEV frontend did not return its home page.' }
    Write-Host 'PODCUT Installer DEV is ready: http://127.0.0.1:3300' -ForegroundColor Green
    Write-Host ('Profile: ' + $script:ProfileRoot)
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:3300' }
} catch {
    foreach ($record in $createdRecords) { try { $null = Stop-OwnedProcessTree $record $state } catch { Write-Warning 'A DEV process could not be safely stopped; its record was retained.' } }
    throw
}
