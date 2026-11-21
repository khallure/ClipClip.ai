$ErrorActionPreference = 'Stop'
$script:ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$script:ProfileRoot = Join-Path $script:ProjectRoot '.dev\profile'
$script:ProfileId = 'installer-dev'
$script:Protocol = 'podcut-installer-dev-v1'

function Assert-DevPath([string]$Path) {
    if ($Path -notmatch '^[A-Za-z]:\\' -or ($Path -split '[\\/]') -contains '..') {
        throw 'DEV paths must be absolute local Windows paths without parent traversal.'
    }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not $full.Equals($script:ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -and
        -not $full.StartsWith($script:ProjectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'A path was outside this DEV checkout.'
    }
    if ($full.Substring(2).Contains(':')) { throw 'DEV paths cannot use an alternate data stream.' }
    for ($cursor = $full; $cursor; $cursor = [IO.Path]::GetDirectoryName($cursor)) {
        try { $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] { continue }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.LinkType -eq 'HardLink') {
            throw 'DEV paths cannot traverse a link, junction or hardlinked file.'
        }
    }
    return $full
}

function New-DevDirectory([string]$Path) {
    $checked = Assert-DevPath $Path
    $null = [IO.Directory]::CreateDirectory($checked)
    return (Assert-DevPath $checked)
}

function Invoke-WithDevWorkspaceLock([scriptblock]$Action) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($script:ProjectRoot.ToUpperInvariant()))
    } finally { $hasher.Dispose() }
    $lockName = 'Local\PODCUT-MultiPC-Dev-' + ([BitConverter]::ToString($digest)).Replace('-', '')
    $lock = [Threading.Mutex]::new($false, $lockName)
    $held = $false
    try {
        try { $held = $lock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held = $true }
        if (-not $held) { throw 'Another launcher operation owns this DEV checkout. Try again after it finishes.' }
        & $Action
    } finally {
        if ($held) { $lock.ReleaseMutex() }
        $lock.Dispose()
    }
}

function Read-DevMarker([string]$Path, [string]$Label) {
    $path = Assert-DevPath $Path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -gt 16384) {
        throw ('' + $Label + ' is missing or invalid.')
    }
    return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Write-DevSeed([string]$Path, [string]$Content) {
    $file = Assert-DevPath $Path
    if (Test-Path -LiteralPath $file -PathType Leaf) { return }
    $temporary = Assert-DevPath ($file + '.seed-' + [Guid]::NewGuid().ToString('N'))
    $data = [Text.Encoding]::UTF8.GetBytes($Content)
    try {
        $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($data, 0, $data.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        [IO.File]::Move($temporary, $file)
    } finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            [IO.File]::Delete((Assert-DevPath $temporary))
        }
    }
}

function Initialize-DevProfile {
    $null = Assert-DevPath $script:ProjectRoot
    $workspace = Read-DevMarker (Join-Path $script:ProjectRoot 'podcut-dev-workspace.json') 'Multi-PC workspace marker'
    if ($workspace.schema -ne 1 -or $workspace.kind -cne 'podcut-multipc-dev') {
        throw 'This launcher requires the Multi-PC source checkout.'
    }
    $null = Assert-DevPath $script:ProfileRoot
    if ($env:PODCUT_PACKAGED_ROOT -or $env:PODCUT_PROFILE_INSTANCE) {
        throw 'A desktop profile environment cannot launch this source checkout.'
    }
    # Reject inherited application paths before creating any directory or masking them.
    foreach ($entry in @(Get-ChildItem Env:)) {
        if ($entry.Name -match '^(DATA_DIR|MODELS_DIR|CACHE_DIR|LOG_DIR|TEMP_DIR|RUNTIME_DIR|FFMPEG_PATH|FFPROBE_PATH|NODE_PATH|PODCUT_.*_DIR|PODCUT_.*_ROOT|PODCUT_.*ENV_FILE)$' -and $entry.Value) {
            $null = Assert-DevPath $entry.Value
            if ($entry.Name -in @('FFMPEG_PATH', 'FFPROBE_PATH', 'NODE_PATH') -and -not ([IO.Path]::GetFullPath($entry.Value)).StartsWith((Join-Path $script:ProjectRoot '.tools\'), [StringComparison]::OrdinalIgnoreCase)) { throw ('Inherited ' + $entry.Name + ' is not a DEV portable tool.') }
            if ($entry.Name -eq 'PODCUT_PROJECT_ROOT' -and $entry.Value -ne $script:ProjectRoot) { throw 'Inherited DEV project identity is invalid.' }
            if ($entry.Name -eq 'PODCUT_PROFILE_ROOT' -and $entry.Value -ne $script:ProfileRoot) { throw 'Inherited DEV profile identity is invalid.' }
            if ($entry.Name -notin @('FFMPEG_PATH', 'FFPROBE_PATH', 'NODE_PATH', 'PODCUT_PROJECT_ROOT')) {
                $inheritedPath = [IO.Path]::GetFullPath($entry.Value).TrimEnd('\')
                if (-not $inheritedPath.Equals($script:ProfileRoot, [StringComparison]::OrdinalIgnoreCase) -and -not $inheritedPath.StartsWith($script:ProfileRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw ('Inherited writable ' + $entry.Name + ' must be inside the external DEV profile.') }
            }
        }
    }
    # Discard configuration injection before invoking pip, npm, Python or Node.
    foreach ($entry in @(Get-ChildItem Env:)) {
        if ($entry.Name -match '^(PIP_|npm_config_|PYTHON|NODE_|PYTEST_|AWS_|AZURE_|GOOGLE_|OPENAI_|GEMINI_|ANTHROPIC_)' -or $entry.Name -match '(^|_)(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?|PROXY)$' -or $entry.Name -in @('PODCUT_RUN_ID', 'VIRTUAL_ENV', 'COVERAGE_FILE')) { [Environment]::SetEnvironmentVariable($entry.Name, $null, 'Process') }
    }
    $ownerFile = Assert-DevPath (Join-Path $script:ProfileRoot 'config\workspace-owner.json')
    if (-not (Test-Path -LiteralPath $ownerFile)) {
        # Allow an empty config folder left by an interrupted first initialization.
        $config = Assert-DevPath (Join-Path $script:ProfileRoot 'config')
        $existing = @(Get-ChildItem -LiteralPath $script:ProfileRoot -Force -ErrorAction SilentlyContinue)
        if ($existing.Count -and ($existing.Count -ne 1 -or $existing[0].FullName -ne $config -or
            -not $existing[0].PSIsContainer -or @(Get-ChildItem -LiteralPath $config -Force).Count)) {
            throw 'The DEV profile contains data without an ownership marker. It was left intact.'
        }
        $null = New-DevDirectory $config
        $owner = @{ schema = 1; workspace_id = [Guid]::NewGuid().ToString()
            project_root = $script:ProjectRoot; profile_root = $script:ProfileRoot }
        Write-DevSeed $ownerFile ($owner | ConvertTo-Json -Compress)
    }
    $owner = Read-DevMarker $ownerFile 'DEV profile ownership marker'
    if ($owner.schema -ne 1 -or $owner.workspace_id -cnotmatch '^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$' -or
        $owner.project_root -ne $script:ProjectRoot -or $owner.profile_root -ne $script:ProfileRoot) {
        throw 'The DEV profile belongs to a different checkout or is invalid. It was left intact.'
    }
    $script:WorkspaceId = $owner.workspace_id
    $script:RuntimeDirectory = Assert-DevPath (Join-Path $script:ProfileRoot 'runtime')
    $script:PidFile = Assert-DevPath (Join-Path $script:RuntimeDirectory 'processes.json')
    $script:BackendEnvFile = Assert-DevPath (Join-Path $script:ProfileRoot 'config\backend.env')
    foreach ($folder in @('data', 'models', 'cache', 'logs', 'temp', 'runtime', 'config', 'cache\pip', 'cache\npm', 'cache\npm-prefix', 'cache\huggingface', 'cache\torch', 'cache\xdg')) { $null = New-DevDirectory (Join-Path $script:ProfileRoot $folder) }
    Write-DevSeed $script:BackendEnvFile (@(
        '# Local Multi-PC development settings. The source runtime uses heuristic analysis.'
        'AI_PROVIDER=heuristic'
        'WHISPER_MODEL=small'
        'WHISPER_DEVICE=cpu'
        'WHISPER_COMPUTE_TYPE=int8'
        'YOUTUBE_INGEST_ENABLED=false'
    ) -join [Environment]::NewLine)
    foreach ($fileName in @('npmrc', 'npm-globalrc')) {
        $configPath = Assert-DevPath (Join-Path $script:ProfileRoot ('config\' + $fileName))
        if (Test-Path -LiteralPath $configPath) {
            if ([IO.File]::ReadAllText($configPath).Trim()) { throw 'DEV npm configuration must be empty; existing settings were preserved.' }
        } else { Write-DevSeed $configPath '' }
    }
    $values = @{
        PODCUT_PROFILE = $script:ProfileId; PODCUT_PROFILE_ID = $script:ProfileId
        PODCUT_PROJECT_ROOT = $script:ProjectRoot; PODCUT_PROFILE_ROOT = $script:ProfileRoot
        PODCUT_CONFIG_DIR = (Join-Path $script:ProfileRoot 'config'); PODCUT_ENV_FILE = $script:BackendEnvFile; PODCUT_BACKEND_ENV_FILE = $script:BackendEnvFile
        DATA_DIR = (Join-Path $script:ProfileRoot 'data'); MODELS_DIR = (Join-Path $script:ProfileRoot 'models'); CACHE_DIR = (Join-Path $script:ProfileRoot 'cache'); LOG_DIR = (Join-Path $script:ProfileRoot 'logs'); TEMP_DIR = (Join-Path $script:ProfileRoot 'temp'); RUNTIME_DIR = $script:RuntimeDirectory
        PODCUT_MODELS_DIR = (Join-Path $script:ProfileRoot 'models'); PODCUT_CACHE_DIR = (Join-Path $script:ProfileRoot 'cache'); PODCUT_LOG_DIR = (Join-Path $script:ProfileRoot 'logs'); PODCUT_TEMP_DIR = (Join-Path $script:ProfileRoot 'temp'); PODCUT_RUNTIME_DIR = $script:RuntimeDirectory
        PIP_CACHE_DIR = (Join-Path $script:ProfileRoot 'cache\pip'); PIP_CONFIG_FILE = 'nul'; PIP_REQUIRE_VIRTUALENV = 'true'; PIP_DISABLE_PIP_VERSION_CHECK = '1'
        npm_config_cache = (Join-Path $script:ProfileRoot 'cache\npm'); npm_config_prefix = (Join-Path $script:ProfileRoot 'cache\npm-prefix'); npm_config_userconfig = (Join-Path $script:ProfileRoot 'config\npmrc'); npm_config_globalconfig = (Join-Path $script:ProfileRoot 'config\npm-globalrc'); npm_config_global = 'false'; npm_config_location = 'project'
        TEMP = (Join-Path $script:ProfileRoot 'temp'); TMP = (Join-Path $script:ProfileRoot 'temp'); HF_HOME = (Join-Path $script:ProfileRoot 'cache\huggingface'); TORCH_HOME = (Join-Path $script:ProfileRoot 'cache\torch'); XDG_CACHE_HOME = (Join-Path $script:ProfileRoot 'cache\xdg')
        PYTHONDONTWRITEBYTECODE = '1'; PYTHONNOUSERSITE = '1'; PYTHONUTF8 = '1'; PYTHONIOENCODING = 'utf-8'; PYTHONPATH = ''; PYTHONHOME = ''; NODE_OPTIONS = ''; NODE_PATH = ''; VIRTUAL_ENV = ''
        AI_PROVIDER = 'heuristic'; GEMINI_API_KEY = ''; OPENAI_API_KEY = ''; ANTHROPIC_API_KEY = ''; GOOGLE_API_KEY = ''; YOUTUBE_INGEST_ENABLED = 'false'
        PODCUT_FRONTEND_PORT = '3300'; PODCUT_BACKEND_PORT = '8300'; UVICORN_HOST = '127.0.0.1'; UVICORN_PORT = '8300'; HOSTNAME = '127.0.0.1'; PORT = '3300'; PODCUT_API_ORIGIN = 'http://127.0.0.1:8300'; NEXT_TELEMETRY_DISABLED = '1'
    }
    foreach ($key in $values.Keys) { [Environment]::SetEnvironmentVariable($key, [string]$values[$key], 'Process') }
    # Process-only PATH; all application executable selection uses explicit DEV paths.
    $env:Path = @((Join-Path $script:ProjectRoot '.venv\Scripts'), (Join-Path $script:ProjectRoot '.tools\node'), (Join-Path $script:ProjectRoot '.tools\python'), (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';'
}
# Entry points source this before resolving or starting any application tool.
Invoke-WithDevWorkspaceLock { Initialize-DevProfile }

function Find-Python {
    $candidate = Assert-DevPath (Join-Path $script:ProjectRoot '.tools\python\python.exe')
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'Portable DEV Python is missing from .tools\python\python.exe.' }
    return $candidate
}
function Find-VenvPython {
    $candidate = Assert-DevPath (Join-Path $script:ProjectRoot '.venv\Scripts\python.exe')
    $configuration = Assert-DevPath (Join-Path $script:ProjectRoot '.venv\pyvenv.cfg')
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf) -or -not (Test-Path -LiteralPath $configuration -PathType Leaf)) { throw 'The isolated DEV .venv is missing. Run DEV setup first.' }
    $homeLine = Get-Content -LiteralPath $configuration | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
    $homePath = if ($homeLine) { ($homeLine -split '=', 2)[1].Trim() } else { '' }
    $allowedHome = Join-Path $script:ProjectRoot '.tools\python'
    if (-not $homePath -or -not ([IO.Path]::GetFullPath($homePath).TrimEnd('\')).Equals($allowedHome, [StringComparison]::OrdinalIgnoreCase)) { throw 'DEV .venv must use portable Python inside DEV .tools\python.' }
    $null = Assert-DevPath $homePath
    return $candidate
}
function Find-Node {
    $candidate = Assert-DevPath (Join-Path $script:ProjectRoot '.tools\node\node.exe')
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'Portable DEV Node is missing from .tools\node\node.exe.' }
    return $candidate
}
function Find-PackageManager([string]$NodePath) {
    $expectedNode = Find-Node
    if (-not $NodePath.Equals($expectedNode, [StringComparison]::OrdinalIgnoreCase)) { throw 'Only DEV portable Node can run npm.' }
    $npmCli = Assert-DevPath (Join-Path $script:ProjectRoot '.tools\node\node_modules\npm\bin\npm-cli.js')
    if (-not (Test-Path -LiteralPath $npmCli -PathType Leaf)) { throw 'The npm CLI bundled with DEV Node is missing.' }
    return @{ Executable = $expectedNode; Prefix = @($npmCli); Kind = 'npm' }
}
function Invoke-PackageManager($Manager, [string[]]$Arguments) {
    $verified = Find-PackageManager (Find-Node)
    if ($Manager.Executable -ne $verified.Executable -or (@($Manager.Prefix) -join '') -ne (@($verified.Prefix) -join '')) { throw 'Unexpected DEV package manager.' }
    $commandArgs = @($verified.Prefix) + $Arguments
    & $verified.Executable @commandArgs
    if ($LASTEXITCODE -ne 0) { throw ('DEV npm failed with exit code ' + $LASTEXITCODE + '.') }
}
function ConvertTo-ProcessArguments([string[]]$Arguments) {
    return (($Arguments | ForEach-Object {
        if ($_ -match '["\r\n]') { throw 'Unsafe process argument.' }
        if ($_ -match '\s' -or $_ -eq '') { '"' + $_ + '"' } else { $_ }
    }) -join ' ')
}
function Get-ExpectedService([string]$Role, [string]$RunId) {
    if ($RunId -notmatch '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$') { throw 'Invalid DEV run ID.' }
    if ($Role -eq 'backend') { return @{ Executable = (Find-VenvPython); Arguments = @('-X', ('podcut_installer_dev_run=' + $RunId), '-m', 'uvicorn', 'app.main:app', '--app-dir', (Join-Path $script:ProjectRoot 'backend'), '--host', '127.0.0.1', '--port', '8300'); Directory = (Assert-DevPath (Join-Path $script:ProjectRoot 'backend')); Port = 8300 } }
    if ($Role -eq 'frontend') {
        $nextCli = Assert-DevPath (Join-Path $script:ProjectRoot 'frontend\node_modules\next\dist\bin\next')
        if (-not (Test-Path -LiteralPath $nextCli -PathType Leaf)) { throw 'DEV frontend dependencies are missing.' }
        return @{ Executable = (Find-Node); Arguments = @(('--title=PODCUTAI-InstallerDev:' + $RunId), $nextCli, 'start', '--hostname', '127.0.0.1', '--port', '3300'); Directory = (Assert-DevPath (Join-Path $script:ProjectRoot 'frontend')); Port = 3300 }
    }
    throw 'Unknown DEV service role.'
}
function Assert-DevState($State) {
    if (-not $State -or $State.schema -ne 2 -or $State.workspace_id -ne $script:WorkspaceId -or $State.profile_id -ne $script:ProfileId -or $State.protocol -ne $script:Protocol -or $State.project_root -ne $script:ProjectRoot -or $State.profile_root -ne $script:ProfileRoot) { throw 'Process records do not identify this DEV profile. No processes were changed.' }
    if ([string]$State.run_id -notmatch '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$' -or [string]$State.run_token -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid DEV run identity. No processes were changed.' }
    $roles = @()
    foreach ($record in @($State.processes)) {
        if ($record.role -notin @('backend', 'frontend') -or $roles -contains $record.role -or $record.profile_id -ne $script:ProfileId -or $record.run_id -ne $State.run_id -or $record.project_root -ne $script:ProjectRoot -or [int]$record.id -le 0) { throw 'Invalid DEV process record. No processes were changed.' }
        $roles += $record.role
    }
    if (@($State.processes).Count -gt 2) { throw 'Unexpected DEV process records.' }
}
function Read-DevState {
    $null = Assert-DevPath $script:PidFile
    if (-not (Test-Path -LiteralPath $script:PidFile)) { return $null }
    $state = Get-Content -LiteralPath $script:PidFile -Raw | ConvertFrom-Json
    Assert-DevState $state
    return $state
}
function Write-DevState($State) {
    Assert-DevState $State
    $null = Assert-DevPath $script:PidFile
    $State | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $script:PidFile -Encoding UTF8
}
function Get-ProcessDetails([int]$ProcessId) { return (Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $ProcessId) -ErrorAction Stop) }
function Get-ProtectedProcessIds {
    $ids = @([int]$PID); $cursor = [int]$PID
    for ($i = 0; $i -lt 32; $i++) {
        $details = Get-ProcessDetails $cursor
        if (-not $details -or [int]$details.ParentProcessId -le 0 -or $ids -contains [int]$details.ParentProcessId) { break }
        $cursor = [int]$details.ParentProcessId; $ids += $cursor
    }
    return $ids
}
function Test-ExpectedCommandLine([string]$CommandLine, [string[]]$Expected) {
    if (-not $CommandLine) { return $false }
    $tokens = @(); $end = 0
    foreach ($match in [regex]::Matches($CommandLine, '"[^"\r\n]*"|[^\s"\r\n]+')) {
        if ($CommandLine.Substring($end, $match.Index - $end).Trim()) { return $false }
        $tokens += $match.Value.Trim('"'); $end = $match.Index + $match.Length
    }
    if ($CommandLine.Substring($end).Trim() -or $tokens.Count -ne $Expected.Count) { return $false }
    for ($i = 0; $i -lt $tokens.Count; $i++) { if (-not $tokens[$i].Equals($Expected[$i], [StringComparison]::OrdinalIgnoreCase)) { return $false } }
    return $true
}
function Get-OwnedProcess($Record, $State) {
    Assert-DevState $State
    if (-not $Record -or (Get-ProtectedProcessIds) -contains [int]$Record.id) { return $null }
    $details = Get-ProcessDetails ([int]$Record.id)
    if (-not $details) { return $null }
    $expected = Get-ExpectedService $Record.role $State.run_id
    $actualStart = ([DateTime]$details.CreationDate).ToUniversalTime().ToString('o')
    if ($Record.started_at -is [DateTime]) { $recordStart = $Record.started_at.ToUniversalTime().ToString('o') } else { $recordStart = [DateTime]::Parse([string]$Record.started_at).ToUniversalTime().ToString('o') }
    if ($actualStart -ne $recordStart -or -not $details.ExecutablePath -or -not $details.ExecutablePath.Equals($expected.Executable, [StringComparison]::OrdinalIgnoreCase)) { return $null }
    $null = Assert-DevPath $details.ExecutablePath
    if ($Record.executable -ne $expected.Executable -or $Record.command_line -cne $details.CommandLine -or -not (Test-ExpectedCommandLine $details.CommandLine (@($expected.Executable) + @($expected.Arguments)))) { return $null }
    return $details
}
function Assert-AllLiveRecordsOwned($State) {
    Assert-DevState $State
    foreach ($record in @($State.processes)) { if ((Get-ProcessDetails ([int]$record.id)) -and -not (Get-OwnedProcess $record $State)) { throw 'A recorded PID is not a verified DEV service. No processes were changed.' } }
}
function New-OwnedProcessRecord([string]$Role, $Process, $State) {
    $details = Get-ProcessDetails ([int]$Process.Id)
    if (-not $details) { throw ('DEV ' + $Role + ' exited before it could be recorded.') }
    $record = @{ role = $Role; profile_id = $script:ProfileId; project_root = $script:ProjectRoot; run_id = $State.run_id; id = [int]$details.ProcessId; started_at = ([DateTime]$details.CreationDate).ToUniversalTime().ToString('o'); executable = $details.ExecutablePath; command_line = $details.CommandLine }
    if (-not (Get-OwnedProcess $record $State)) { throw 'The launched DEV process did not match its expected executable and arguments.' }
    return $record
}
function Get-OwnedProcessTree($Record, $State) {
    $root = Get-OwnedProcess $Record $State
    if (-not $root) { return @() }
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $tree = @{}; $tree[[int]$root.ProcessId] = @{ details = $root; depth = 0 }
    $protectedIds = @(Get-ProtectedProcessIds); $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($item in $snapshot) {
            $id = [int]$item.ProcessId; $parent = [int]$item.ParentProcessId
            if ($tree.ContainsKey($id) -or -not $tree.ContainsKey($parent) -or $protectedIds -contains $id) { continue }
            if (([DateTime]$item.CreationDate) -lt ([DateTime]$tree[$parent].details.CreationDate) -or -not $item.ExecutablePath -or -not $item.CommandLine) { continue }
            try { $null = Assert-DevPath $item.ExecutablePath } catch { continue }
            $allowedExe = $item.ExecutablePath.StartsWith((Join-Path $script:ProjectRoot '.tools\'), [StringComparison]::OrdinalIgnoreCase) -or $item.ExecutablePath.StartsWith((Join-Path $script:ProjectRoot '.venv\'), [StringComparison]::OrdinalIgnoreCase) -or $item.ExecutablePath.StartsWith((Join-Path $script:ProjectRoot '.venv-asd'), [StringComparison]::OrdinalIgnoreCase)
            if (-not $allowedExe) { continue }
            $tree[$id] = @{ details = $item; depth = $tree[$parent].depth + 1 }; $changed = $true
        }
    }
    return @($tree.Values | Sort-Object -Property depth -Descending)
}
function Stop-OwnedProcessTree($Record, $State) {
    $tree = @(Get-OwnedProcessTree $Record $State); $stopped = 0
    foreach ($entry in $tree) {
        $before = $entry.details; $now = Get-ProcessDetails ([int]$before.ProcessId)
        if (-not $now) { continue }
        if ($now.CreationDate -ne $before.CreationDate -or $now.ParentProcessId -ne $before.ParentProcessId -or $now.ExecutablePath -ne $before.ExecutablePath -or $now.CommandLine -cne $before.CommandLine) { throw 'DEV process provenance changed during stop. Remaining processes were left alone.' }
        if ($entry.depth -eq 0 -and -not (Get-OwnedProcess $Record $State)) { throw 'DEV root ownership changed during stop.' }
        Stop-Process -Id ([int]$now.ProcessId) -ErrorAction Stop; $stopped++
    }
    return $stopped
}
function Get-DevPortListeners([int]$Port) {
    if ($Port -notin @(3300, 8300)) { throw 'Only DEV ports 3300 and 8300 may be inspected.' }
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}
function Assert-DevPortAvailableOrOwned([int]$Port, $Record, $State) {
    $listeners = @(Get-DevPortListeners $Port)
    if (-not $listeners.Count) { return }
    if (-not $Record -or -not $State) { throw ('DEV port ' + $Port + ' is occupied by another process. Nothing was stopped.') }
    $ownedIds = @(Get-OwnedProcessTree $Record $State | ForEach-Object { [int]$_.details.ProcessId })
    foreach ($listener in $listeners) { if ($ownedIds -notcontains [int]$listener.OwningProcess) { throw ('DEV port ' + $Port + ' is not owned by this recorded DEV run. Nothing was stopped.') } }
}
function Test-DevIdentity($Identity, $State) {
    return ($Identity -and $Identity.profile_id -eq $script:ProfileId -and $Identity.project_root -eq $script:ProjectRoot -and $Identity.profile_root -eq $script:ProfileRoot -and $Identity.run_id -eq $State.run_id -and $Identity.port -eq 8300 -and $Identity.frontend_port -eq 3300 -and $Identity.backend_host -eq '127.0.0.1' -and $Identity.protocol -eq $script:Protocol -and $Identity.build -eq 'phase2a')
}
function Wait-DevIdentity($State, [switch]$ThroughFrontend) {
    $port = if ($ThroughFrontend) { 3300 } else { 8300 }
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        foreach ($record in @($State.processes)) { if (-not (Get-OwnedProcess $record $State)) { throw ('DEV ' + $record.role + ' exited or changed ownership during startup.') } }
        try {
            $response = Invoke-WebRequest -Uri ('http://127.0.0.1:' + $port + '/api/runtime-identity') -Headers @{ 'x-podcut-run-token' = $State.run_token } -UseBasicParsing -TimeoutSec 2
            $identity = $response.Content | ConvertFrom-Json
            if ((Test-DevIdentity $identity $State) -and ((-not $ThroughFrontend) -or ($response.Headers['x-podcut-proxy-profile'] -eq $script:ProfileId -and $response.Headers['x-podcut-proxy-run-id'] -eq $State.run_id))) {
                $role = if ($ThroughFrontend) { 'frontend' } else { 'backend' }
                $record = @($State.processes | Where-Object { $_.role -eq $role }) | Select-Object -First 1
                if (-not @(Get-DevPortListeners $port).Count) { throw 'The verified service has no DEV listener.' }
                Assert-DevPortAvailableOrOwned $port $record $State
                if ($ThroughFrontend) {
                    $backendRecord = @($State.processes | Where-Object { $_.role -eq 'backend' }) | Select-Object -First 1
                    if (-not @(Get-DevPortListeners 8300).Count) { throw 'The verified backend has no DEV listener.' }
                    Assert-DevPortAvailableOrOwned 8300 $backendRecord $State
                }
                return
            }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw ('DEV identity handshake failed on port ' + $port + '. See external profile logs.')
}
function Find-DevFFmpeg([string]$Name = 'ffmpeg.exe') {
    if ($Name -notin @('ffmpeg.exe', 'ffprobe.exe')) { throw 'Unexpected media tool.' }
    $tools = Assert-DevPath (Join-Path $script:ProjectRoot '.tools\ffmpeg')
    if (-not (Test-Path -LiteralPath $tools)) { throw 'Portable FFmpeg is missing from DEV .tools\ffmpeg.' }
    foreach ($item in @(Get-ChildItem -LiteralPath $tools -Recurse -Filter $Name -File)) { return (Assert-DevPath $item.FullName) }
    throw ('DEV portable ' + $Name + ' is missing.')
}
