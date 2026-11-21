# Negative launcher checks. Run only when the Installer DEV app is stopped.
# This file never discovers cleanup targets from a PID record or by port/name.
. (Join-Path $PSScriptRoot 'common.ps1')
$powerShellCache = New-DevDirectory (Join-Path $script:ProfileRoot 'cache\powershell')
$env:PSModuleAnalysisCachePath = Assert-DevPath (Join-Path $powerShellCache 'ModuleAnalysisCache')
$env:POWERSHELL_TELEMETRY_OPTOUT = '1'

$script:VerificationId = [Guid]::NewGuid().ToString()
$script:VerificationRoot = New-DevDirectory (Join-Path $script:ProfileRoot 'runtime\phase2a')
$script:FixtureRoot = New-DevDirectory (Join-Path $script:VerificationRoot $script:VerificationId)
$script:ReportPath = Assert-DevPath (Join-Path $script:VerificationRoot 'launcher-negative-report.json')
$script:LogPath = Assert-DevPath (Join-Path $script:ProfileRoot ('logs\phase2a-launcher-' + $script:VerificationId + '.log'))
$script:Fixtures = New-Object System.Collections.ArrayList
$script:Results = New-Object System.Collections.ArrayList
$script:OriginalStateBytes = $null
$script:ExpectedStateHash = $null
$script:StateManaged = $false
$script:Sequence = 0
$script:CleanupErrors = @()
$script:PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$script:NodeExe = Find-Node
$script:StartedAt = [DateTime]::UtcNow.ToString('o')

function Write-VerificationLog([string]$Message) {
    $null = Assert-DevPath $script:LogPath
    [IO.File]::AppendAllText($script:LogPath, ([DateTime]::UtcNow.ToString('o') + ' ' + $Message + [Environment]::NewLine))
}
function Assert-Verification([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Get-StateHash {
    $null = Assert-DevPath $script:PidFile
    if (-not (Test-Path -LiteralPath $script:PidFile)) { return '<absent>' }
    return (Get-FileHash -LiteralPath $script:PidFile -Algorithm SHA256).Hash
}
function Assert-StateUnchanged {
    Assert-Verification ((Get-StateHash) -eq $script:ExpectedStateHash) 'DEV process state changed outside this verification. It was left untouched.'
}
function Restore-TestState {
    if (-not $script:StateManaged) { return }
    Assert-StateUnchanged
    $null = Assert-DevPath $script:PidFile
    if ($null -ne $script:OriginalStateBytes) { [IO.File]::WriteAllBytes($script:PidFile, $script:OriginalStateBytes) }
    elseif (Test-Path -LiteralPath $script:PidFile) { Remove-Item -LiteralPath $script:PidFile }
    $script:ExpectedStateHash = Get-StateHash
}
function Set-TestState($State) {
    Assert-StateUnchanged
    Write-DevState $State
    $script:ExpectedStateHash = Get-StateHash
}
function New-TestState {
    return @{ schema = 2; profile_id = $script:ProfileId; protocol = $script:Protocol; project_root = $script:ProjectRoot; profile_root = $script:ProfileRoot; run_id = [Guid]::NewGuid().ToString(); run_token = ('a' * 64); processes = @() }
}
function New-ForgedRecord($Details, $State) {
    # Deliberately plausible metadata; these fixtures are never treated as cleanup authority.
    return @{ role = 'frontend'; profile_id = $script:ProfileId; project_root = $script:ProjectRoot; run_id = $State.run_id; id = [int]$Details.ProcessId; started_at = ([DateTime]$Details.CreationDate).ToUniversalTime().ToString('o'); executable = $Details.ExecutablePath; command_line = $Details.CommandLine }
}
function Register-Fixture($Process, [string]$Executable, [string[]]$Arguments, [string]$Label) {
    $details = Get-ProcessDetails ([int]$Process.Id)
    if (-not $details) { throw ('Fixture exited before its identity was captured: ' + $Label) }
    Assert-Verification ($details.ExecutablePath -eq $Executable) ('Unexpected fixture executable: ' + $Label)
    Assert-Verification (Test-ExpectedCommandLine $details.CommandLine (@($Executable) + $Arguments)) ('Unexpected fixture arguments: ' + $Label)
    Assert-Verification ([int]$details.ParentProcessId -eq [int]$PID) ('Fixture was not created by this verification shell: ' + $Label)
    Assert-Verification ([Math]::Abs((([DateTime]$details.CreationDate).ToUniversalTime() - $Process.StartTime.ToUniversalTime()).TotalMilliseconds) -lt 10) ('Fixture creation time does not match its Start-Process result: ' + $Label)
    $fixture = @{ label = $Label; id = [int]$details.ProcessId; started_at = ([DateTime]$details.CreationDate).ToUniversalTime().ToString('o'); executable = $details.ExecutablePath; command_line = $details.CommandLine; parent_id = [int]$details.ParentProcessId; process = $Process }
    [void]$script:Fixtures.Add($fixture)
    return $fixture
}
function Test-FixtureAlive($Fixture) {
    $details = Get-ProcessDetails ([int]$Fixture.id)
    if (-not $details) { return $false }
    return (([DateTime]$details.CreationDate).ToUniversalTime().ToString('o') -eq $Fixture.started_at -and $details.ExecutablePath -eq $Fixture.executable -and $details.CommandLine -ceq $Fixture.command_line -and [int]$details.ParentProcessId -eq $Fixture.parent_id)
}
function Stop-ExplicitFixture($Fixture) {
    $details = Get-ProcessDetails ([int]$Fixture.id)
    if (-not $details) { return }
    Assert-Verification ((Get-ProtectedProcessIds) -notcontains [int]$Fixture.id) 'Fixture cleanup refused a protected process.'
    Assert-Verification (Test-FixtureAlive $Fixture) ('Fixture ownership changed; no stop was attempted: ' + $Fixture.label)
    # Only this exact Start-Process result, revalidated immediately above.
    Stop-Process -Id ([int]$Fixture.id) -ErrorAction Stop
    Write-VerificationLog ('Cleaned explicit fixture ' + $Fixture.label + '. No process-tree or port-based stop was used.')
}
function Start-CheckProcess([string]$Executable, [string[]]$Arguments, [string]$Label) {
    $script:Sequence++
    $stdout = Assert-DevPath (Join-Path $script:FixtureRoot ('process-' + $script:Sequence + '.stdout.log'))
    $stderr = Assert-DevPath (Join-Path $script:FixtureRoot ('process-' + $script:Sequence + '.stderr.log'))
    $process = Start-Process -FilePath $Executable -ArgumentList (ConvertTo-ProcessArguments $Arguments) -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $fixture = Register-Fixture $process $Executable $Arguments $Label
    $fixture.stdout = $stdout; $fixture.stderr = $stderr
    return $fixture
}
function Invoke-CheckChild([string]$File, [string[]]$Arguments, [string]$Label, [int]$TimeoutSeconds = 25) {
    $null = Assert-DevPath $File
    $fixture = Start-CheckProcess $script:PowerShellExe (@('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $File) + $Arguments) $Label
    try {
        Assert-Verification ($fixture.process.WaitForExit($TimeoutSeconds * 1000)) ('Child exceeded its bounded timeout: ' + $Label)
        $fixture.process.Refresh()
        $exitCode = $fixture.process.ExitCode
        $stdout = [IO.File]::ReadAllText($fixture.stdout)
        $stderr = [IO.File]::ReadAllText($fixture.stderr)
        Write-VerificationLog ('Child ' + $Label + ' exit=' + $exitCode + [Environment]::NewLine + $stdout + $stderr)
        return @{ exit_code = $exitCode; stdout = $stdout; stderr = $stderr }
    } finally { Stop-ExplicitFixture $fixture }
}
function Invoke-NegativeCase([string]$Name, [scriptblock]$Body) {
    Write-VerificationLog ('BEGIN ' + $Name)
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        & $Body
        [void]$script:Results.Add(@{ name = $Name; status = 'passed'; elapsed_ms = $watch.ElapsedMilliseconds })
        Write-VerificationLog ('PASS ' + $Name)
    } catch {
        [void]$script:Results.Add(@{ name = $Name; status = 'failed'; elapsed_ms = $watch.ElapsedMilliseconds; error = $_.Exception.Message })
        Write-VerificationLog ('FAIL ' + $Name + ': ' + $_.Exception.Message)
        throw
    } finally { $watch.Stop() }
}

$sentinelScript = Assert-DevPath (Join-Path $script:FixtureRoot 'sentinel.cjs')
$environmentScript = Assert-DevPath (Join-Path $script:FixtureRoot 'environment-check.ps1')
$fatalError = $null
try {
    Write-VerificationLog 'Checking stopped DEV prerequisites; ports outside 3300/8300 will not be inspected or requested.'
    $null = Find-VenvPython
    $null = Get-ExpectedService 'frontend' ([Guid]::NewGuid().ToString())
    Assert-Verification (Test-Path -LiteralPath (Assert-DevPath (Join-Path $script:ProjectRoot 'frontend\.next\BUILD_ID'))) 'Build the DEV frontend before verifying launcher refusal paths.'
    Assert-Verification (Test-Path -LiteralPath (Assert-DevPath $script:BackendEnvFile)) 'External DEV backend configuration is missing.'
    Assert-Verification (Test-Path -LiteralPath $script:PowerShellExe) 'Windows PowerShell is unavailable.'
    foreach ($port in @(3300, 8300)) { Assert-Verification (-not @(Get-DevPortListeners $port).Count) ('DEV port ' + $port + ' must be free before this verification; no owner was stopped.') }
    $initialState = Read-DevState
    Assert-Verification (-not $initialState -or @($initialState.processes).Count -eq 0) 'DEV process state must be absent or contain zero records. Stop DEV normally first.'
    if (Test-Path -LiteralPath $script:PidFile) {
        $script:OriginalStateBytes = [IO.File]::ReadAllBytes($script:PidFile)
        $backup = Assert-DevPath (Join-Path $script:FixtureRoot 'processes.original.json')
        [IO.File]::WriteAllBytes($backup, $script:OriginalStateBytes)
    }
    $script:ExpectedStateHash = Get-StateHash
    $script:StateManaged = $true

    @'
const fs = require('node:fs');
const http = require('node:http');
const port = Number(process.argv[2]);
const ready = process.argv[3];
const evidence = process.argv[4];
if (![0, 3300, 8300].includes(port)) throw new Error('Unexpected fixture port');
if (port === 0) { fs.writeFileSync(ready, 'ready'); setInterval(() => {}, 1000); }
else {
  const server = http.createServer((request, response) => {
    fs.appendFileSync(evidence, request.url + '\n');
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ profile_id: 'foreign-fixture', run_id: 'wrong-run', port }));
  });
  server.listen(port, '127.0.0.1', () => fs.writeFileSync(ready, 'ready'));
}
'@ | Set-Content -LiteralPath $sentinelScript -Encoding UTF8

    @'
param([string]$CommonPath, [string]$Mode, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
$foreign = 'C:\PODCUTAI-Phase2A-Forbidden'
function Write-ChildResult($Value) {
    $null = Assert-DevPath $OutputPath
    $Value | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}
if ($Mode -eq 'sanitize') {
    $env:PIP_TARGET = $foreign + '\pip'; $env:PIP_PREFIX = $foreign; $env:PIP_USER = 'true'; $env:PIP_CONFIG_FILE = $foreign + '\pip.ini'
    $env:NPM_CONFIG_PREFIX = $foreign; $env:NPM_CONFIG_CACHE = $foreign; $env:NPM_CONFIG_GLOBAL = 'true'; $env:NPM_CONFIG_USERCONFIG = $foreign + '\npmrc'
    $env:PYTHONPATH = $foreign; $env:PYTHONHOME = $foreign; $env:PYTHONSTARTUP = $foreign + '\startup.py'
    $env:NODE_OPTIONS = '--require=' + $foreign + '\injection.cjs'; $env:NODE_EXTRA_CA_CERTS = $foreign + '\cert.pem'
    $env:OPENAI_API_KEY = 'phase2a-dummy'; $env:CLOUD_TOKEN = 'phase2a-dummy'; $env:APP_CREDENTIALS = 'phase2a-dummy'; $env:ALL_PROXY = 'http://127.0.0.1:9'
    . $CommonPath
    $checks = @{
        pip_target_removed = -not $env:PIP_TARGET; pip_prefix_removed = -not $env:PIP_PREFIX; pip_user_removed = -not $env:PIP_USER
        pip_config_disabled = ($env:PIP_CONFIG_FILE -ceq 'nul'); pip_cache_scoped = ($env:PIP_CACHE_DIR -eq (Join-Path $script:ProfileRoot 'cache\pip'))
        npm_prefix_scoped = ($env:npm_config_prefix -eq (Join-Path $script:ProfileRoot 'cache\npm-prefix')); npm_cache_scoped = ($env:npm_config_cache -eq (Join-Path $script:ProfileRoot 'cache\npm'))
        npm_global_disabled = ($env:npm_config_global -eq 'false'); npm_user_config_scoped = ($env:npm_config_userconfig -eq (Join-Path $script:ProfileRoot 'config\npmrc'))
        python_path_removed = -not $env:PYTHONPATH; python_home_removed = -not $env:PYTHONHOME; python_startup_removed = -not $env:PYTHONSTARTUP
        node_options_removed = -not $env:NODE_OPTIONS; node_ca_removed = -not $env:NODE_EXTRA_CA_CERTS
        secrets_removed = (-not $env:OPENAI_API_KEY -and -not $env:CLOUD_TOKEN -and -not $env:APP_CREDENTIALS); proxy_removed = -not $env:ALL_PROXY
        temp_scoped = ($env:TEMP -eq (Join-Path $script:ProfileRoot 'temp') -and $env:TMP -eq $env:TEMP)
        model_caches_scoped = ($env:HF_HOME -eq (Join-Path $script:ProfileRoot 'cache\huggingface') -and $env:TORCH_HOME -eq (Join-Path $script:ProfileRoot 'cache\torch') -and $env:XDG_CACHE_HOME -eq (Join-Path $script:ProfileRoot 'cache\xdg'))
        profile_pinned = ($env:DATA_DIR -eq (Join-Path $script:ProfileRoot 'data') -and $env:MODELS_DIR -eq (Join-Path $script:ProfileRoot 'models'))
        safety_pinned = ($env:AI_PROVIDER -eq 'heuristic' -and $env:YOUTUBE_INGEST_ENABLED -eq 'false' -and $env:PYTHONDONTWRITEBYTECODE -eq '1')
        ports_pinned = ($env:PODCUT_FRONTEND_PORT -eq '3300' -and $env:PODCUT_BACKEND_PORT -eq '8300' -and $env:PODCUT_API_ORIGIN -eq 'http://127.0.0.1:8300')
    }
    Write-ChildResult @{ mode = $Mode; checks = $checks }
    if (@($checks.Values | Where-Object { -not $_ }).Count) { throw 'A child environment boundary check failed.' }
} elseif ($Mode -in @('reject-source-data', 'reject-foreign-data')) {
    $candidate = if ($Mode -eq 'reject-source-data') { 'D:\PODCUT-Labs\Installer-Dev\backend\data' } else { $foreign + '\data' }
    $env:DATA_DIR = $candidate; $env:PIP_TARGET = 'phase2a-before-initialization-marker'
    $rejected = $false; $message = ''
    try { . $CommonPath } catch { $rejected = $true; $message = $_.Exception.Message }
    if (-not $rejected -or $env:DATA_DIR -ne $candidate -or $env:PIP_TARGET -ne 'phase2a-before-initialization-marker') { throw 'Invalid writable path did not fail before environment initialization.' }
    if ($message -notmatch 'outside the two approved DEV roots|must be inside the external DEV profile') { throw ('Unexpected rejection: ' + $message) }
    Write-ChildResult @{ mode = $Mode; rejected_before_initialization = $true }
} else { throw 'Unknown verification child mode.' }
'@ | Set-Content -LiteralPath $environmentScript -Encoding UTF8

    foreach ($mode in @('sanitize', 'reject-source-data', 'reject-foreign-data')) {
        $caseMode = $mode
        Invoke-NegativeCase ('environment-' + $caseMode) {
            $output = Assert-DevPath (Join-Path $script:FixtureRoot ($caseMode + '.json'))
            $result = Invoke-CheckChild $environmentScript @('-CommonPath', (Join-Path $PSScriptRoot 'common.ps1'), '-Mode', $caseMode, '-OutputPath', $output) $caseMode
            Assert-Verification ($result.exit_code -eq 0) ('Environment child failed: ' + $caseMode)
            Assert-Verification (Test-Path -LiteralPath $output) 'Child did not create its scoped evidence report.'
        }
    }

    foreach ($port in @(3300, 8300)) {
        $casePort = $port
        $sentinel = $null
        try {
            $ready = Assert-DevPath (Join-Path $script:FixtureRoot ('ready-' + $casePort + '.txt'))
            $evidence = Assert-DevPath (Join-Path $script:FixtureRoot ('requests-' + $casePort + '.txt'))
            $sentinel = Start-CheckProcess $script:NodeExe @($sentinelScript, [string]$casePort, $ready, $evidence) ('foreign-listener-' + $casePort)
            for ($attempt = 0; $attempt -lt 40 -and -not (Test-Path -LiteralPath $ready); $attempt++) { Assert-Verification (Test-FixtureAlive $sentinel) 'Listener fixture exited.'; Start-Sleep -Milliseconds 100 }
            Assert-Verification (Test-Path -LiteralPath $ready) 'Listener fixture was not ready within four seconds.'
            $listeners = @(Get-DevPortListeners $casePort)
            Assert-Verification ($listeners.Count -gt 0 -and @($listeners | Where-Object { [int]$_.OwningProcess -ne $sentinel.id }).Count -eq 0) 'The fixture does not exclusively own its DEV port.'
            Invoke-NegativeCase ('foreign-port-' + $casePort + '-startup-refusal') {
                Assert-StateUnchanged
                $result = Invoke-CheckChild (Join-Path $PSScriptRoot 'start.ps1') @('-NoBrowser') ('blocked-start-' + $casePort)
                Assert-Verification ($result.exit_code -ne 0) 'Startup accepted a foreign listener.'
                Assert-Verification (($result.stdout + $result.stderr) -match ('DEV port ' + $casePort + ' is occupied')) 'Startup failed for an unrelated reason instead of its foreign-port guard.'
                Assert-Verification (Test-FixtureAlive $sentinel) 'Startup stopped or changed the foreign sentinel.'
                Assert-StateUnchanged
            }
            if ($casePort -eq 8300) {
                Invoke-NegativeCase 'wrong-identity-single-handshake-attempt' {
                    $testState = New-TestState
                    $returnedReady = $false; $bounded = $false
                    # Unit scope limits Wait-DevIdentity to one actual HTTP request.
                    & {
                        function Start-Sleep { throw 'Phase2AIdentityAttemptBound' }
                        try { Wait-DevIdentity $testState; $returnedReady = $true } catch { if ($_.Exception.Message -ne 'Phase2AIdentityAttemptBound') { throw }; $bounded = $true }
                        Assert-Verification (-not $returnedReady -and $bounded) 'A foreign identity was accepted or the one-attempt bound failed.'
                    }
                    Assert-Verification (Test-FixtureAlive $sentinel) 'The identity check changed the foreign sentinel.'
                    $requests = @([IO.File]::ReadAllLines($evidence))
                    Assert-Verification ($requests.Count -eq 1 -and $requests[0] -eq '/api/runtime-identity') 'Expected exactly one identity request to the verified fixture on 8300.'
                }
            }
        } finally { if ($sentinel) { Stop-ExplicitFixture $sentinel } }
    }

    $ready = Assert-DevPath (Join-Path $script:FixtureRoot 'ready-unrelated.txt')
    $evidence = Assert-DevPath (Join-Path $script:FixtureRoot 'requests-unrelated.txt')
    $unrelated = Start-CheckProcess $script:NodeExe @($sentinelScript, '0', $ready, $evidence) 'unrelated-node-with-no-listener'
    for ($attempt = 0; $attempt -lt 40 -and -not (Test-Path -LiteralPath $ready); $attempt++) { Assert-Verification (Test-FixtureAlive $unrelated) 'Unrelated Node fixture exited.'; Start-Sleep -Milliseconds 100 }
    Assert-Verification (Test-Path -LiteralPath $ready) 'Unrelated Node fixture was not ready.'
    foreach ($forgery in @('current-shell', 'unrelated-node', 'creation-time-mismatch')) {
        $caseForgery = $forgery
        Invoke-NegativeCase ('forged-state-' + $caseForgery) {
            $testState = New-TestState
            $details = if ($caseForgery -eq 'current-shell') { Get-ProcessDetails ([int]$PID) } else { Get-ProcessDetails ([int]$unrelated.id) }
            $record = New-ForgedRecord $details $testState
            if ($caseForgery -eq 'creation-time-mismatch') {
                # Getter-only unit scope: isolate the timestamp check without starting Next
                # or allowing this fixture to become a production cleanup target.
                & {
                    function Get-ExpectedService {
                        param([string]$Role, [string]$RunId)
                        return @{ Executable = $script:NodeExe; Arguments = @($sentinelScript, '0', $ready, $evidence) }
                    }
                    $baseline = New-ForgedRecord $details $testState
                    Assert-Verification ($null -ne (Get-OwnedProcess $baseline $testState)) 'Timestamp unit fixture did not pass the otherwise matching ownership getter.'
                    $changed = $baseline.Clone()
                    $changed.started_at = ([DateTime]$details.CreationDate).ToUniversalTime().AddMinutes(-1).ToString('o')
                    Assert-Verification ($null -eq (Get-OwnedProcess $changed $testState)) 'Ownership getter accepted a changed creation timestamp.'
                }
                $record.started_at = ([DateTime]$details.CreationDate).ToUniversalTime().AddMinutes(-1).ToString('o')
            }
            $testState.processes = @($record)
            $refused = $false
            try { Assert-AllLiveRecordsOwned $testState } catch { $refused = $true }
            Assert-Verification $refused 'Ownership validation accepted a forged live record.'
            Set-TestState $testState
            try {
                $result = Invoke-CheckChild (Join-Path $PSScriptRoot 'stop.ps1') @() ('forged-stop-' + $caseForgery)
                Assert-Verification ($result.exit_code -ne 0) 'Stop accepted a forged process record.'
                Assert-Verification (($result.stdout + $result.stderr) -match 'not a verified DEV service') 'Stop failed for an unrelated reason instead of rejecting ownership.'
                Assert-Verification (Test-FixtureAlive $unrelated) 'Stop changed the unrelated Node sentinel.'
                Assert-Verification ($null -ne (Get-ProcessDetails ([int]$PID))) 'The verification shell was stopped.'
                Assert-StateUnchanged
            } finally { Restore-TestState }
        }
    }
    Invoke-NegativeCase 'final-empty-state-and-ports' {
        Assert-StateUnchanged
        foreach ($port in @(3300, 8300)) { Assert-Verification (-not @(Get-DevPortListeners $port).Count) ('A DEV listener remains on port ' + $port + '; no unrecorded process will be stopped.') }
    }
} catch {
    $fatalError = $_.Exception.Message
    Write-VerificationLog ('ABORT ' + $fatalError)
} finally {
    foreach ($fixture in @($script:Fixtures | Sort-Object { $_.id } -Descending)) {
        try { Stop-ExplicitFixture $fixture } catch { $script:CleanupErrors += $_.Exception.Message; Write-VerificationLog ('CLEANUP REFUSED ' + $_.Exception.Message) }
    }
    try { Restore-TestState } catch { $script:CleanupErrors += $_.Exception.Message; Write-VerificationLog ('STATE RESTORE REFUSED ' + $_.Exception.Message) }
    $passed = -not $fatalError -and $script:CleanupErrors.Count -eq 0 -and @($script:Results | Where-Object { $_.status -eq 'failed' }).Count -eq 0
    $report = @{ verification = 'phase2a-launcher-negative'; verification_id = $script:VerificationId; profile_id = $script:ProfileId; project_root = $script:ProjectRoot; profile_root = $script:ProfileRoot; started_at = $script:StartedAt; finished_at = [DateTime]::UtcNow.ToString('o'); passed = $passed; results = @($script:Results); fatal_error = $fatalError; cleanup_errors = $script:CleanupErrors; log_path = $script:LogPath; fixture_path = $script:FixtureRoot; inspected_ports = @(3300, 8300) }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Assert-DevPath $script:ReportPath) -Encoding UTF8
    Write-Host ('Launcher verification report: ' + $script:ReportPath)
    Write-Host ('Full log: ' + $script:LogPath)
}
if (-not $passed) { throw 'Phase 2A launcher verification failed. Read the scoped report and log; no unverified cleanup was attempted.' }
