param([string]$BuildId = ([Guid]::NewGuid().ToString()))

$ErrorActionPreference = 'Stop'
$Project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($BuildId -cnotmatch '^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$') { throw 'BuildId harus berupa UUID kanonis.' }

function Resolve-BuildPath([string]$Path, [string]$Boundary = $Project) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -ine $Boundary -and !$full.StartsWith($Boundary + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Path build di luar area kerja yang disetujui.' }
    $cursor = $full
    while ($cursor) {
        $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Path build tidak boleh melewati link atau junction.' }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $full
}

function Invoke-BuildStep([string]$Tool, [string[]]$Arguments, [string]$Log) {
    Write-Host "Menjalankan $Log"
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Tool @Arguments *> (Join-Path $Run $Log)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($exitCode -ne 0) {
        Get-Content -LiteralPath (Join-Path $Run $Log) -Tail 18
        throw "Build gagal di $Log."
    }
}

$workspace = Get-Content -LiteralPath (Resolve-BuildPath (Join-Path $Project 'podcut-dev-workspace.json')) -Raw | ConvertFrom-Json
if ($workspace.schema -ne 1 -or $workspace.kind -cne 'podcut-multipc-dev') { throw 'Build memerlukan workspace Multi-PC.' }
$pins = Get-Content -LiteralPath (Resolve-BuildPath (Join-Path $Project 'scripts\toolchain.lock.json')) -Raw | ConvertFrom-Json
$electronName = 'electron-v45.3.4-win32-x64.zip'
if ($pins.tools.electron.archive -cne $electronName) { throw 'Manifest Electron tidak sesuai.' }
$electron = Resolve-BuildPath (Join-Path $Project ('.tools\downloads\' + $electronName))
$checksums = Get-Content -LiteralPath (Resolve-BuildPath (Join-Path $Project 'desktop\node_modules\electron\checksums.json')) -Raw | ConvertFrom-Json
$electronHash = (Get-FileHash -LiteralPath $electron -Algorithm SHA256).Hash
if ($electronHash -ine $checksums.$electronName -or $electronHash -ine $pins.tools.electron.sha256) {
    throw 'SHA-256 arsip Electron berbeda dari manifest dan dependency terpasang.'
}

$Run = Resolve-BuildPath (Join-Path $Project ('quality-upgrade\runs\' + $BuildId))
if (Test-Path -LiteralPath $Run) { throw 'Gunakan UUID build baru agar output terdahulu tetap utuh.' }
[IO.Directory]::CreateDirectory($Run) | Out-Null

foreach ($name in @('temp','cache','cache\pyinstaller','work','work\electron-zip','work\frontend','work\appdata','work\localappdata','work\userprofile')) {
    [IO.Directory]::CreateDirectory((Join-Path $Run $name)) | Out-Null
}
[IO.File]::Copy($electron, (Join-Path $Run ('work\electron-zip\' + $electronName)), $false)
$node = Resolve-BuildPath (Join-Path $Project '.tools\node\node.exe')
$python = Resolve-BuildPath (Join-Path $Project '.venv\Scripts\python.exe')

# Use a process-scoped environment without inherited service, credential or build overrides.
foreach ($entry in @(Get-ChildItem Env:)) {
    if ($entry.Name -match '^(PODCUT_|PYTHON|PYINSTALLER_|NODE_|npm_config_|ELECTRON_|NEXT_|PIP_|OPENAI_|GEMINI_|GOOGLE_|AWS_|AZURE_|ANTHROPIC_|HF_|HUGGINGFACE_|TORCH_)|(^|_)(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?|PROXY)$') {
        [Environment]::SetEnvironmentVariable($entry.Name, $null, 'Process')
    }
}
$env:TEMP = Join-Path $Run 'temp'
$env:TMP = $env:TEMP
$env:LOCALAPPDATA = Join-Path $Run 'work\localappdata'
$env:APPDATA = Join-Path $Run 'work\appdata'
$env:USERPROFILE = Join-Path $Run 'work\userprofile'
$env:XDG_CACHE_HOME = Join-Path $Run 'cache'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Run 'cache\pyinstaller'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUTF8 = '1'
$env:NEXT_TELEMETRY_DISABLED = '1'
$env:PODCUT_QUALITY_BUILD_ID = $BuildId
$env:PATH = @((Join-Path $Project '.tools\node'), (Join-Path $Project '.tools\python'), (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';'

$copyFrontend = @'
const fs = require('node:fs');
const path = require('node:path');
const [project, run] = process.argv.slice(2);
const { checkedPath } = require(path.join(project, 'desktop/lab-boundaries.cjs'));
const frontend = checkedPath(path.join(project, 'frontend'), project);
const target = checkedPath(path.join(run, 'work/frontend'), run);
const sources = ['app', 'components', 'lib', 'public', 'node_modules', 'desktop-cache.cjs',
  'next-env.d.ts', 'next.config.ts', 'package.json', 'package-lock.json', 'proxy.ts', 'tsconfig.json'];
function verifyCopySource(file) {
  const info = fs.lstatSync(file);
  if (info.isSymbolicLink() || !fs.realpathSync.native(file).toLowerCase().startsWith(frontend.toLowerCase() + path.sep)) {
    throw new Error('Frontend contains a linked dependency or source file.');
  }
  if (info.isDirectory()) {
    for (const name of fs.readdirSync(file)) verifyCopySource(path.join(file, name));
  }
}
for (const item of sources) {
  const file = checkedPath(path.join(frontend, item), frontend);
  verifyCopySource(file);
  fs.cpSync(file, path.join(target, item), { recursive: true, force: false, errorOnExist: true });
}
'@
$copyScript = Resolve-BuildPath (Join-Path $Run 'work\stage-frontend.cjs')
[IO.File]::WriteAllText($copyScript,$copyFrontend)
Invoke-BuildStep $node @($copyScript,$Project,$Run) 'frontend-stage.log'

Push-Location -LiteralPath (Join-Path $Run 'work\frontend')
try {
    Invoke-BuildStep $node @('node_modules\next\dist\bin\next','build','--webpack') 'next-build.log'
} finally { Pop-Location }

Push-Location -LiteralPath (Join-Path $Run 'work')
try {
    Invoke-BuildStep $python @('-B','-m','PyInstaller','--noconfirm','--log-level','WARN',
        '--workpath',(Join-Path $Run 'work\pyinstaller'), '--distpath',(Join-Path $Run 'work\backend-dist'),
        (Join-Path $Project 'desktop\backend\PODCUTBackend.spec')) 'pyinstaller.log'
    Invoke-BuildStep $node @((Join-Path $Project 'desktop\build-desktop.mjs'),'--quality-upgrade',$BuildId) 'packaging.log'
} finally { Pop-Location }
Write-Host ('Build tersedia di ' + (Join-Path $Run 'dist\ClipClip.ai-win32-x64'))
