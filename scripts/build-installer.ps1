param(
    [string]$BuildId = '',
    [string]$AppVersion = '1.0.0',
    [string]$ReleaseNotes = 'Rilis desktop ClipClip.ai.',
    [string]$DownloadUrl = ''
)

$ErrorActionPreference = 'Stop'
$Project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')

if ($AppVersion -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw 'AppVersion harus berupa semver sederhana x.y.z (contoh: 1.0.0).'
}

function Resolve-InstallerPath([string]$Path, [string]$Boundary = $Project) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full -ine $Boundary -and -not $full.StartsWith($Boundary + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path installer di luar area kerja yang disetujui: $Path"
    }
    $cursor = $full
    while ($cursor) {
        $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Path installer tidak boleh melewati symlink atau junction: $Path"
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $full
}

$workspace = Get-Content -LiteralPath (Resolve-InstallerPath (Join-Path $Project 'podcut-dev-workspace.json')) -Raw -Encoding UTF8 | ConvertFrom-Json
if ($workspace.schema -ne 1 -or $workspace.kind -cne 'podcut-multipc-dev') {
    throw 'Build installer memerlukan workspace Multi-PC.'
}

$lock = Get-Content -LiteralPath (Resolve-InstallerPath (Join-Path $Project 'scripts\toolchain.lock.json')) -Raw -Encoding UTF8 | ConvertFrom-Json
$innoLock = $lock.tools.inno_setup
if (-not $innoLock -or $innoLock.status -cne 'VERIFIED') {
    throw 'Manifest tools.inno_setup belum terverifikasi di scripts\toolchain.lock.json.'
}

$iscc = Resolve-InstallerPath (Join-Path $Project $innoLock.executable)
if (-not (Test-Path -LiteralPath $iscc -PathType Leaf)) {
    throw "Portable Inno Setup compiler tidak ditemukan di '$iscc'. Siapkan .tools\inno terlebih dahulu."
}
$actualIsccHash = (Get-FileHash -LiteralPath $iscc -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualIsccHash -cne $innoLock.executable_sha256.ToLowerInvariant()) {
    throw "SHA-256 ISCC.exe tidak cocok dengan toolchain.lock.json. Diharapkan: $($innoLock.executable_sha256), aktual: $actualIsccHash"
}

if (-not $BuildId) {
    $BuildId = [Guid]::NewGuid().ToString()
    Write-Host "Memulai build paket desktop ClipClip.ai (BuildId: $BuildId)..."
    & (Resolve-InstallerPath (Join-Path $Project 'scripts\build-quality-upgrade.ps1')) -BuildId $BuildId
    if ($LASTEXITCODE -ne 0) { throw 'Build paket desktop gagal.' }
} else {
    if ($BuildId -cnotmatch '^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$') {
        throw 'BuildId harus berupa UUID kanonis.'
    }
}

$runDir = Resolve-InstallerPath (Join-Path $Project ('quality-upgrade\runs\' + $BuildId))
$appDistDir = Resolve-InstallerPath (Join-Path $runDir 'dist\ClipClip.ai-win32-x64')
$appExe = Resolve-InstallerPath (Join-Path $appDistDir 'ClipClip.ai.exe')
$pkgManifestPath = Resolve-InstallerPath (Join-Path $runDir 'desktop-package-manifest.json')
$issScript = Resolve-InstallerPath (Join-Path $Project 'desktop\installer\ClipClip.iss')
$iconIco = Resolve-InstallerPath (Join-Path $Project 'desktop\assets\icon.ico')

foreach ($req in @($appExe, $pkgManifestPath, $issScript, $iconIco)) {
    if (-not (Test-Path -LiteralPath $req -PathType Leaf)) {
        throw "Artefak wajib installer tidak ditemukan: $req"
    }
}

$installerDir = Resolve-InstallerPath (Join-Path $runDir 'installer')
$installerExe = Resolve-InstallerPath (Join-Path $installerDir ("ClipClip.ai-Setup-$AppVersion-x64.exe"))
if (Test-Path -LiteralPath $installerExe) {
    throw "Installer sudah ada di '$installerExe'. Gunakan BuildId baru agar artefak lama tetap utuh."
}
$null = [IO.Directory]::CreateDirectory($installerDir)

$logPath = Resolve-InstallerPath (Join-Path $runDir 'installer-compile.log')
Write-Host "Mengompilasi ClipClip.ai-Setup-$AppVersion-x64.exe menggunakan portable Inno Setup $($innoLock.version)..."
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $iscc `
        "/DAppVersion=$AppVersion" `
        "/DAppDistDir=$appDistDir" `
        "/DOutputDirPath=$installerDir" `
        $issScript *> $logPath
    $isccExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prev
}

if ($isccExit -ne 0) {
    Get-Content -LiteralPath $logPath -Tail 25
    throw "Kompilasi Inno Setup gagal (exit code $isccExit)."
}

if (-not (Test-Path -LiteralPath $installerExe -PathType Leaf)) {
    throw "Output installer tidak ditemukan setelah kompilasi: $installerExe"
}

$installerInfo = Get-Item -LiteralPath $installerExe
$installerSha = (Get-FileHash -LiteralPath $installerExe -Algorithm SHA256).Hash.ToLowerInvariant()
$pkgManifest = Get-Content -LiteralPath $pkgManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

$manifestObj = [ordered]@{
    schema = 1
    product = 'ClipClip.ai'
    version = $AppVersion
    build_id = $BuildId
    platform = 'win32'
    arch = 'x64'
    scope = 'per-user'
    default_install_dir = '%LOCALAPPDATA%\Programs\ClipClip.ai'
    user_data_dir = '%LOCALAPPDATA%\ClipClip.ai'
    signed = $false
    updater = $true
    compiler = ('Inno Setup ' + $innoLock.version)
    installer_file = $installerInfo.Name
    installer_path = $installerExe
    installer_bytes = [long]$installerInfo.Length
    installer_sha256 = $installerSha
    source_package_files = @($pkgManifest.files).Count
    source_package_bytes = [long]$pkgManifest.total_bytes
    created_at = [DateTime]::UtcNow.ToString('o')
    notice = 'Unsigned private Windows installer build. Bundled FFmpeg/PyAV codec libraries require license review before public redistribution.'
}

$manifestPath = Resolve-InstallerPath (Join-Path $installerDir 'installer-manifest.json')
$manifestObj | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$resolvedDownloadUrl = if ($DownloadUrl) { $DownloadUrl.Trim() } else { "https://github.com/khallure/ClipClip.ai/releases/download/v$AppVersion/$($installerInfo.Name)" }
$latestObj = [ordered]@{
    schema = 1
    product = 'ClipClip.ai'
    platform = 'win32'
    arch = 'x64'
    version = $AppVersion
    published_at = $manifestObj.created_at
    notes = $ReleaseNotes.Trim()
    installer = [ordered]@{
        filename = $installerInfo.Name
        bytes = [long]$installerInfo.Length
        sha256 = $installerSha
        url = $resolvedDownloadUrl
    }
}
$latestPath = Resolve-InstallerPath (Join-Path $installerDir 'latest.json')
$latestObj | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $latestPath -Encoding UTF8

Write-Host ('Installer siap: ' + $installerExe) -ForegroundColor Green
Write-Host ('Manifest update: ' + $latestPath) -ForegroundColor Green
Write-Host ('Ukuran: ' + [Math]::Round($installerInfo.Length / 1MB, 2) + ' MB | SHA-256: ' + $installerSha)
