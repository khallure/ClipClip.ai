param(
    [switch]$ValidateOnly,
    [switch]$DownloadTools,
    [switch]$SkipBuild,
    [string]$ManifestJson,
    [switch]$WithWhisper,
    [switch]$WithFFmpeg,
    [switch]$WithIngestion,
    [switch]$WithReframe
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$WorkspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')

if ($WithWhisper -or $WithFFmpeg -or $WithIngestion -or $WithReframe) {
    throw 'Switch opsional lama tidak digunakan lagi. Seluruh dependency terverifikasi dikelola melalui scripts\toolchain.lock.json dan scripts\requirements-windows-cp312.lock.txt.'
}
if ($ManifestJson -and -not $ValidateOnly) {
    throw 'Parameter -ManifestJson hanya diizinkan bersama -ValidateOnly untuk pengujian read-only.'
}
if ($DownloadTools -and $ValidateOnly) {
    throw 'Parameter -DownloadTools tidak dapat digabungkan dengan -ValidateOnly.'
}

function Assert-WorkspacePath([string]$Candidate, [string]$Boundary = $WorkspaceRoot) {
    if (-not $Candidate -or $Candidate -notmatch '^[A-Za-z]:\\' -or ($Candidate -split '[\\/]') -contains '..') {
        throw "Path tidak valid atau di luar aturan workspace lokal Windows: $Candidate"
    }
    $full = [IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $root = [IO.Path]::GetFullPath($Boundary).TrimEnd('\')
    if (-not $full.Equals($root, [StringComparison]::OrdinalIgnoreCase) -and
        -not $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path berada di luar batas workspace yang diizinkan: $Candidate"
    }
    if ($full.Substring(2).Contains(':')) {
        throw "Path tidak boleh memakai alternate data stream: $Candidate"
    }
    for ($cursor = $full; $cursor; $cursor = [IO.Path]::GetDirectoryName($cursor)) {
        try {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
        } catch [System.Management.Automation.ItemNotFoundException] {
            continue
        }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.LinkType -eq 'HardLink') {
            throw "Path tidak boleh melewati symlink, junction, atau hardlink: $Candidate"
        }
    }
    return $full
}

function Read-JsonMarker([string]$Path, [string]$Label, [int]$MaxBytes = 65536) {
    $checked = Assert-WorkspacePath $Path
    if (-not (Test-Path -LiteralPath $checked -PathType Leaf)) {
        throw "$Label tidak ditemukan di '$checked'."
    }
    $info = Get-Item -LiteralPath $checked -Force
    if ($info.Length -le 0 -or $info.Length -gt $MaxBytes) {
        throw "$Label memiliki ukuran tidak valid ($($info.Length) byte)."
    }
    try {
        return (Get-Content -LiteralPath $checked -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        throw "$Label bukan JSON yang valid: $($_.Exception.Message)"
    }
}

function Assert-Sha256Format([string]$Value, [string]$Label) {
    if (-not $Value -or $Value -ceq 'UNVERIFIED' -or $Value -cnotmatch '^[a-fA-F0-9]{64}$') {
        throw "$Label belum memiliki SHA-256 64-heksadesimal yang terverifikasi (nilai saat ini: '$Value')."
    }
}

function Assert-FileSha256([string]$Path, [string]$ExpectedHash, [string]$Label) {
    $checked = Assert-WorkspacePath $Path
    Assert-Sha256Format $ExpectedHash "Checksum manifest untuk $Label"
    if (-not (Test-Path -LiteralPath $checked -PathType Leaf)) {
        throw "Artefak $Label tidak ditemukan di '$checked'."
    }
    $actual = (Get-FileHash -LiteralPath $checked -Algorithm SHA256).Hash.ToLowerInvariant()
    $expected = $ExpectedHash.ToLowerInvariant()
    if ($actual -cne $expected) {
        throw "Verifikasi SHA-256 gagal untuk $Label ('$checked'). Diharapkan: $expected, aktual: $actual. File ditolak."
    }
    return $actual
}

function Test-WorkspaceAndLockfiles($Manifest) {
    $null = Assert-WorkspacePath $WorkspaceRoot
    $workspaceMarker = Read-JsonMarker (Join-Path $WorkspaceRoot 'podcut-dev-workspace.json') 'Marker workspace Multi-PC' 16384
    if ($workspaceMarker.schema -ne 1 -or $workspaceMarker.kind -cne 'podcut-multipc-dev') {
        throw 'Direktori ini bukan workspace pengembangan Multi-PC yang sah (podcut-dev-workspace.json tidak cocok).'
    }
    if (-not $Manifest -or $Manifest.schema -ne 1 -or $Manifest.kind -cne 'podcut-multipc-toolchain-lock' -or
        $Manifest.platform -cne 'win32' -or $Manifest.arch -cne 'x64') {
        throw 'Format scripts\toolchain.lock.json tidak valid atau bukan untuk win32-x64.'
    }

    foreach ($lockKey in @('python', 'frontend_npm', 'desktop_npm')) {
        $entry = $Manifest.lockfiles.$lockKey
        if (-not $entry -or -not $entry.path -or $entry.status -cne 'VERIFIED') {
            throw "Metadata lockfile '$lockKey' pada scripts\toolchain.lock.json tidak lengkap atau belum VERIFIED."
        }
        $lockPath = Assert-WorkspacePath (Join-Path $WorkspaceRoot ($entry.path -replace '/', '\'))
        $null = Assert-FileSha256 $lockPath ([string]$entry.sha256) "lockfile $lockKey ($($entry.path))"
    }

    $pythonLockPath = Assert-WorkspacePath (Join-Path $WorkspaceRoot ($Manifest.lockfiles.python.path -replace '/', '\'))
    $pythonLines = @(Get-Content -LiteralPath $pythonLockPath | Where-Object { $_ -and $_ -notmatch '^\s*#' })
    if ($pythonLines.Count -ne [int]$Manifest.lockfiles.python.package_count) {
        throw "Jumlah paket pada $($Manifest.lockfiles.python.path) ($($pythonLines.Count)) berbeda dari manifest ($($Manifest.lockfiles.python.package_count))."
    }
    $pyinstallerPin = $Manifest.tools.pyinstaller
    if (-not $pyinstallerPin) {
        throw 'Entri tools.pyinstaller tidak ditemukan pada scripts\toolchain.lock.json.'
    }
    $foundPyInstaller = $false
    foreach ($line in $pythonLines) {
        if ($line -notmatch '^[A-Za-z0-9._-]+==[A-Za-z0-9._-]+(\s+--hash=sha256:[a-f0-9]{64})+$') {
            throw "Baris lockfile Python tidak memenuhi syarat pin versi eksak dan --hash=sha256: $line"
        }
        if ($line.StartsWith("pyinstaller==$($pyinstallerPin.version) ", [StringComparison]::OrdinalIgnoreCase)) {
            if ($line -notmatch ("--hash=sha256:" + [regex]::Escape($pyinstallerPin.sha256.ToLowerInvariant()))) {
                throw 'Hash PyInstaller pada scripts\requirements-windows-cp312.lock.txt tidak cocok dengan scripts\toolchain.lock.json.'
            }
            $foundPyInstaller = $true
        }
    }
    if (-not $foundPyInstaller) {
        throw "Pin pyinstaller==$($pyinstallerPin.version) tidak ditemukan pada scripts\requirements-windows-cp312.lock.txt."
    }

    $desktopLockPath = Assert-WorkspacePath (Join-Path $WorkspaceRoot ($Manifest.lockfiles.desktop_npm.path -replace '/', '\'))
    # Windows PowerShell 5.1 ConvertFrom-Json rejects the empty-string "" root package key in npm lockfileVersion 3.
    $desktopLockRaw = (Get-Content -LiteralPath $desktopLockPath -Raw -Encoding UTF8) -replace '(?m)^(\s*)""\s*:', '$1"__root__":'
    $desktopLock = $desktopLockRaw | ConvertFrom-Json
    $electronPkg = $desktopLock.packages.'node_modules/electron'
    if (-not $electronPkg -or $electronPkg.version -cne $Manifest.tools.electron.version -or
        $electronPkg.integrity -cne $Manifest.tools.electron.npm_package_integrity) {
        throw 'Versi atau integrity paket Electron pada desktop\package-lock.json tidak cocok dengan scripts\toolchain.lock.json.'
    }
}

function Test-ToolchainManifestAndArtifacts($Manifest, [switch]$AllowDownload) {
    $requiredTools = @(
        @{ Key = 'python'; Version = '3.12.16'; NeedsArchive = $true },
        @{ Key = 'node'; Version = '24.22.3'; NeedsArchive = $true },
        @{ Key = 'npm'; Version = '11.19.1'; NeedsArchive = $false },
        @{ Key = 'ffmpeg'; Version = '9.0.2'; NeedsArchive = $true },
        @{ Key = 'electron'; Version = '45.3.4'; NeedsArchive = $true },
        @{ Key = 'pyinstaller'; Version = '6.22.3'; NeedsArchive = $false },
        @{ Key = 'inno_setup'; Version = '7.1.1'; NeedsArchive = $true }
    )

    $unverified = @()
    foreach ($spec in $requiredTools) {
        $key = $spec.Key
        $tool = $Manifest.tools.$key
        if (-not $tool) {
            throw "Komponen toolchain '$key' tidak ditemukan pada scripts\toolchain.lock.json."
        }
        if ($tool.version -cne $spec.Version -or $tool.platform -cne 'win32' -or $tool.arch -cne 'x64') {
            throw "Pin komponen '$key' pada scripts\toolchain.lock.json tidak sesuai kontrak (diharapkan $($spec.Version) win32-x64)."
        }
        if ($tool.status -cne 'VERIFIED' -or -not $tool.sha256 -or $tool.sha256 -ceq 'UNVERIFIED' -or $tool.sha256 -cnotmatch '^[a-fA-F0-9]{64}$') {
            $unverified += "$key $($tool.version) (status=$($tool.status), sha256=$($tool.sha256))"
        }
    }

    if ($unverified.Count -gt 0) {
        throw ("Bootstrap ditolak karena komponen toolchain berikut pada scripts\toolchain.lock.json masih berstatus UNVERIFIED atau belum memiliki SHA-256 resmi: " +
            ($unverified -join '; ') + ". Verifikasi checksum resmi terlebih dahulu.")
    }

    if ($Manifest.tools.npm.sha256.ToLowerInvariant() -cne $Manifest.tools.node.sha256.ToLowerInvariant()) {
        throw 'Checksum npm yang dibundel bersama Node.js harus sama dengan SHA-256 arsip Node.js pada scripts\toolchain.lock.json.'
    }
    if ($Manifest.tools.electron.archive -cne 'electron-v45.3.4-win32-x64.zip') {
        throw 'Nama arsip Electron pada scripts\toolchain.lock.json harus electron-v45.3.4-win32-x64.zip.'
    }

    foreach ($spec in $requiredTools) {
        if (-not $spec.NeedsArchive) { continue }
        $key = $spec.Key
        $tool = $Manifest.tools.$key
        $archivePath = Assert-WorkspacePath (Join-Path $WorkspaceRoot ($tool.download_path -replace '/', '\'))
        if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
            if ($AllowDownload) {
                if (-not $tool.source_url -or $tool.source_url -notmatch '^https://' -or $tool.source_url.EndsWith('/')) {
                    throw "URL unduhan langsung HTTPS untuk '$key' belum lengkap pada scripts\toolchain.lock.json ($($tool.source_url)). Sediakan arsip terverifikasi di '$archivePath'."
                }
                $downloadsDir = Assert-WorkspacePath (Split-Path -Parent $archivePath)
                $null = [IO.Directory]::CreateDirectory($downloadsDir)
                $tempArchive = Assert-WorkspacePath ($archivePath + '.download-' + [Guid]::NewGuid().ToString('N'))
                try {
                    Write-Host "Mengunduh $key $($tool.version) dari $($tool.source_url)..."
                    Invoke-WebRequest -Uri $tool.source_url -OutFile $tempArchive -UseBasicParsing
                    $null = Assert-FileSha256 $tempArchive ([string]$tool.sha256) "unduhan $key ($($tool.archive))"
                    [IO.File]::Move($tempArchive, $archivePath)
                } finally {
                    if (Test-Path -LiteralPath $tempArchive -PathType Leaf) {
                        [IO.File]::Delete((Assert-WorkspacePath $tempArchive))
                    }
                }
            } else {
                throw "Arsip toolchain '$key' tidak ditemukan di '$archivePath'. Tempatkan arsip resmi di folder tersebut atau jalankan setup.ps1 -DownloadTools setelah checksum diverifikasi."
            }
        }
        $null = Assert-FileSha256 $archivePath ([string]$tool.sha256) "arsip $key ($($tool.archive))"
    }
}

function Expand-VerifiedToolArchive($ToolEntry, [string]$Label, [string]$ExpectedRelativeFile) {
    $installDir = Assert-WorkspacePath (Join-Path $WorkspaceRoot ($ToolEntry.install_dir -replace '/', '\'))
    $expectedFile = Assert-WorkspacePath (Join-Path $WorkspaceRoot ($ExpectedRelativeFile -replace '/', '\'))
    $archivePath = Assert-WorkspacePath (Join-Path $WorkspaceRoot ($ToolEntry.download_path -replace '/', '\'))
    $null = Assert-FileSha256 $archivePath ([string]$ToolEntry.sha256) "arsip $Label ($($ToolEntry.archive))"
    if (Test-Path -LiteralPath $expectedFile -PathType Leaf) {
        return
    }
    if (Test-Path -LiteralPath $installDir) {
        throw "Direktori toolchain '$installDir' sudah ada tetapi '$expectedFile' tidak ditemukan. Periksa isi folder tersebut tanpa menghapus data secara otomatis."
    }
    $toolsRoot = Assert-WorkspacePath (Join-Path $WorkspaceRoot '.tools')
    $null = [IO.Directory]::CreateDirectory($toolsRoot)
    $stageDir = Assert-WorkspacePath (Join-Path $toolsRoot ('.staging-' + $Label + '-' + [Guid]::NewGuid().ToString('N')))
    try {
        [IO.Directory]::CreateDirectory($stageDir) | Out-Null
        if ($archivePath.EndsWith('.tar.gz', [StringComparison]::OrdinalIgnoreCase)) {
            $tarExe = Join-Path $env:SystemRoot 'System32\tar.exe'
            if (-not (Test-Path -LiteralPath $tarExe -PathType Leaf)) {
                throw "System32\tar.exe tidak ditemukan untuk mengekstrak arsip '$archivePath'."
            }
            $tarListing = @(& $tarExe -tvf $archivePath)
            if ($LASTEXITCODE -ne 0) {
                throw "Gagal memeriksa daftar isi arsip tar.gz '$archivePath'."
            }
            foreach ($entryLine in $tarListing) {
                if ($entryLine -match '^[lh]' -or $entryLine -match '\s(/|\\|[A-Za-z]:)' -or $entryLine -match '(^|[\\/])\.\.([\\/]|$)') {
                    throw "Arsip tar.gz '$archivePath' mengandung symlink, hardlink, atau path traversal yang tidak aman: $entryLine"
                }
            }
            & $tarExe -xzf $archivePath -C $stageDir
            if ($LASTEXITCODE -ne 0) {
                throw "Gagal mengekstrak arsip tar.gz '$archivePath' dengan tar.exe (exit code $LASTEXITCODE)."
            }
        } elseif ($ToolEntry.archive_format -ceq 'inno-portable-exe') {
            # Pass a single quoted argument string so spaces in workspace paths stay inside $stageDir.
            $innoArgs = '/PORTABLE=1 /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS /DIR="' + $stageDir + '"'
            $innoProc = Start-Process -FilePath $archivePath -ArgumentList $innoArgs -Wait -PassThru
            if ($innoProc.ExitCode -ne 0) {
                throw "Gagal membongkar Inno Setup portabel '$archivePath' (exit code $($innoProc.ExitCode))."
            }
        } else {
            $zipHandle = [IO.Compression.ZipFile]::OpenRead($archivePath)
            try {
                foreach ($zipEntry in $zipHandle.Entries) {
                    if ($zipEntry.FullName -match '^(/|\\|[A-Za-z]:)' -or ($zipEntry.FullName -split '[\\/]') -contains '..' -or $zipEntry.FullName.Contains(':')) {
                        throw "Arsip ZIP '$archivePath' mengandung entri path tidak aman: $($zipEntry.FullName)"
                    }
                }
            } finally {
                $zipHandle.Dispose()
            }
            [IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $stageDir)
        }
        foreach ($item in @(Get-ChildItem -LiteralPath $stageDir -Recurse -Force)) {
            $null = Assert-WorkspacePath $item.FullName $stageDir
        }
        $topEntries = @(Get-ChildItem -LiteralPath $stageDir -Force)
        $sourceRoot = $stageDir
        if ($topEntries.Count -eq 1 -and $topEntries[0].PSIsContainer) {
            $candidateSingle = Assert-WorkspacePath $topEntries[0].FullName $stageDir
            $nugetTools = Join-Path $candidateSingle 'tools'
            if ((Test-Path -LiteralPath (Join-Path $nugetTools 'python.exe') -PathType Leaf)) {
                $sourceRoot = Assert-WorkspacePath $nugetTools $stageDir
            } else {
                $sourceRoot = $candidateSingle
            }
        } elseif (Test-Path -LiteralPath (Join-Path $stageDir 'tools\python.exe') -PathType Leaf) {
            $sourceRoot = Assert-WorkspacePath (Join-Path $stageDir 'tools') $stageDir
        }
        [IO.Directory]::Move($sourceRoot, $installDir)
    } finally {
        if (Test-Path -LiteralPath $stageDir) {
            Remove-Item -LiteralPath (Assert-WorkspacePath $stageDir $toolsRoot) -Recurse -Force
        }
    }
    if (-not (Test-Path -LiteralPath $expectedFile -PathType Leaf)) {
        throw "Ekstraksi arsip $Label selesai tetapi executable '$expectedFile' tidak ditemukan."
    }
}

function Assert-ExtractedToolVersions($Manifest) {
    $pythonExe = Find-Python
    $pyVersion = (& $pythonExe -c "import platform, sys, venv; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.machine()}')").Trim()
    if ($LASTEXITCODE -ne 0 -or $pyVersion -cne "$($Manifest.tools.python.version)|AMD64") {
        throw "Versi Python portabel di '$pythonExe' tidak sesuai (diharapkan $($Manifest.tools.python.version)|AMD64, aktual: $pyVersion)."
    }

    $nodeExe = Find-Node
    $nodeLicense = Assert-DevPath (Join-Path $script:ProjectRoot '.tools\node\LICENSE')
    if (-not (Test-Path -LiteralPath $nodeLicense -PathType Leaf)) {
        throw "File lisensi Node.js tidak ditemukan di '$nodeLicense'."
    }
    $nodeVersion = (& $nodeExe --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -cne "v$($Manifest.tools.node.version)") {
        throw "Versi Node.js portabel di '$nodeExe' tidak sesuai (diharapkan v$($Manifest.tools.node.version), aktual: $nodeVersion)."
    }

    $manager = Find-PackageManager $nodeExe
    $npmArgs = @($manager.Prefix) + @('--version')
    $npmVersion = (& $manager.Executable @npmArgs).Trim()
    if ($LASTEXITCODE -ne 0 -or $npmVersion -cne $Manifest.tools.npm.version) {
        throw "Versi npm portabel tidak sesuai (diharapkan $($Manifest.tools.npm.version), aktual: $npmVersion)."
    }

    foreach ($mediaTool in @('ffmpeg.exe', 'ffprobe.exe')) {
        $mediaExe = Find-DevFFmpeg $mediaTool
        $versionLines = @(& $mediaExe -version)
        $versionHeader = if ($versionLines.Count -gt 0) { [string]$versionLines[0] } else { '' }
        if ($LASTEXITCODE -ne 0 -or $versionHeader -notmatch ('\b' + [regex]::Escape($Manifest.tools.ffmpeg.version) + '\b')) {
            throw "Versi $mediaTool di '$mediaExe' tidak sesuai dengan pin $($Manifest.tools.ffmpeg.version) (aktual: $versionHeader)."
        }
    }

    $isccExe = Assert-DevPath (Join-Path $script:ProjectRoot ($Manifest.tools.inno_setup.executable -replace '/', '\'))
    $null = Assert-FileSha256 $isccExe ([string]$Manifest.tools.inno_setup.executable_sha256) 'Inno Setup ISCC.exe'
}

$manifestPath = Assert-WorkspacePath (Join-Path $WorkspaceRoot 'scripts\toolchain.lock.json')
$manifest = if ($ManifestJson) {
    try { $ManifestJson | ConvertFrom-Json } catch { throw "Parameter -ManifestJson bukan JSON yang valid: $($_.Exception.Message)" }
} else {
    Read-JsonMarker $manifestPath 'Manifest toolchain (scripts\toolchain.lock.json)' 65536
}

Test-WorkspaceAndLockfiles $manifest
Test-ToolchainManifestAndArtifacts $manifest -AllowDownload:$DownloadTools

if ($ValidateOnly) {
    Write-Host 'Validasi read-only workspace, lockfile, dan manifest toolchain lulus.' -ForegroundColor Green
    exit 0
}

Expand-VerifiedToolArchive $manifest.tools.python 'python' $manifest.tools.python.executable
Expand-VerifiedToolArchive $manifest.tools.node 'node' $manifest.tools.node.executable
Expand-VerifiedToolArchive $manifest.tools.ffmpeg 'ffmpeg' $manifest.tools.ffmpeg.executable
Expand-VerifiedToolArchive $manifest.tools.inno_setup 'inno_setup' $manifest.tools.inno_setup.executable

. (Join-Path $PSScriptRoot 'common.ps1')

$env:LOCALAPPDATA = New-DevDirectory (Join-Path $script:ProfileRoot 'cache\localappdata')
$env:APPDATA = New-DevDirectory (Join-Path $script:ProfileRoot 'config\appdata')
$env:USERPROFILE = New-DevDirectory (Join-Path $script:ProfileRoot 'cache\userprofile')

Assert-ExtractedToolVersions $manifest

$pythonExecutable = Find-Python
$nodeExecutable = Find-Node
$manager = Find-PackageManager $nodeExecutable
$venvDirectory = Assert-DevPath (Join-Path $script:ProjectRoot '.venv')
if (-not (Test-Path -LiteralPath (Join-Path $venvDirectory 'Scripts\python.exe') -PathType Leaf)) {
    Write-Host "Membuat virtual environment Python terisolasi di '$venvDirectory'..."
    & $pythonExecutable -m venv $venvDirectory
    if ($LASTEXITCODE -ne 0) {
        throw 'Gagal membuat Python virtual environment (.venv) di workspace ini.'
    }
}
$venvPython = Find-VenvPython
if (-not (Test-Path -LiteralPath (Assert-DevPath $script:BackendEnvFile) -PathType Leaf)) {
    throw 'Konfigurasi .dev\profile\config\backend.env belum tersedia. Setup tidak menyalin kredensial dari profil lain.'
}

$pythonLockFile = Assert-DevPath (Join-Path $script:ProjectRoot ($manifest.lockfiles.python.path -replace '/', '\'))
Write-Host 'Memasang paket Python dari lockfile cp312 dengan verifikasi --require-hashes dan --only-binary :all:...'
& $venvPython -m pip install --require-virtualenv --require-hashes --only-binary :all: --no-deps --no-user --prefix $venvDirectory --progress-bar off --cache-dir $env:PIP_CACHE_DIR -r $pythonLockFile
if ($LASTEXITCODE -ne 0) {
    throw 'Pemasangan dependency Python berbasis hash dari scripts\requirements-windows-cp312.lock.txt gagal.'
}

$pyInstallerActual = (& $venvPython -B -m PyInstaller --version).Trim()
if ($LASTEXITCODE -ne 0 -or $pyInstallerActual -cne $manifest.tools.pyinstaller.version) {
    throw "Versi PyInstaller di .venv tidak sesuai (diharapkan $($manifest.tools.pyinstaller.version), aktual: $pyInstallerActual)."
}

$frontendDir = Assert-DevPath (Join-Path $script:ProjectRoot 'frontend')
Push-Location -LiteralPath $frontendDir
try {
    $frontendLock = Assert-DevPath (Join-Path $frontendDir 'package-lock.json')
    if (-not (Test-Path -LiteralPath $frontendLock -PathType Leaf)) {
        throw 'frontend\package-lock.json wajib tersedia untuk npm ci.'
    }
    Write-Host 'Memasang dependency frontend menggunakan frontend\package-lock.json...'
    Invoke-PackageManager $manager @('ci', '--cache', $env:npm_config_cache, '--prefix', $frontendDir, '--no-audit', '--no-fund')
    if (-not $SkipBuild) {
        Write-Host 'Menjalankan build frontend Next.js...'
        Invoke-PackageManager $manager @('run', 'build')
    }
} finally {
    Pop-Location
}

$desktopDir = Assert-DevPath (Join-Path $script:ProjectRoot 'desktop')
Push-Location -LiteralPath $desktopDir
try {
    $desktopLock = Assert-DevPath (Join-Path $desktopDir 'package-lock.json')
    if (-not (Test-Path -LiteralPath $desktopLock -PathType Leaf)) {
        throw 'desktop\package-lock.json wajib tersedia untuk npm ci.'
    }
    $env:ELECTRON_SKIP_BINARY_DOWNLOAD = '1'
    $env:electron_config_cache = Assert-DevPath (Join-Path $script:ProjectRoot '.tools\downloads')
    Write-Host 'Memasang dependency desktop packaging menggunakan desktop\package-lock.json...'
    Invoke-PackageManager $manager @('ci', '--cache', $env:npm_config_cache, '--prefix', $desktopDir, '--no-audit', '--no-fund')
} finally {
    [Environment]::SetEnvironmentVariable('ELECTRON_SKIP_BINARY_DOWNLOAD', $null, 'Process')
    [Environment]::SetEnvironmentVariable('electron_config_cache', $null, 'Process')
    Pop-Location
}

$electronChecksumsPath = Assert-DevPath (Join-Path $script:ProjectRoot 'desktop\node_modules\electron\checksums.json')
if (-not (Test-Path -LiteralPath $electronChecksumsPath -PathType Leaf)) {
    throw 'desktop\node_modules\electron\checksums.json tidak ditemukan setelah npm ci.'
}
$electronChecksums = Get-Content -LiteralPath $electronChecksumsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$electronArchiveName = $manifest.tools.electron.archive
$electronArchivePath = Assert-DevPath (Join-Path $script:ProjectRoot ($manifest.tools.electron.download_path -replace '/', '\'))
$electronActualHash = Assert-FileSha256 $electronArchivePath ([string]$manifest.tools.electron.sha256) "arsip Electron ($electronArchiveName)"
if ($electronActualHash -ine $electronChecksums.$electronArchiveName) {
    throw "SHA-256 arsip Electron berbeda dari desktop\node_modules\electron\checksums.json."
}

Write-Host 'Bootstrap PODCUT Multi-PC selesai dan terverifikasi. Jalankan scripts\test.ps1 atau scripts\start.ps1.' -ForegroundColor Green
