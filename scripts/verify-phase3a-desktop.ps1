param([Parameter(Mandatory=$true)][string]$Executable)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$exe=Assert-DevPath $Executable
if ([IO.Path]::GetFileName($exe) -ne 'PODCUTAI-InstallerDev.exe' -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) {throw 'Missing packaged desktop.'}
foreach ($port in 3300,8300) {if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {throw 'DEV port occupied. No process was stopped.'}}
$id=[guid]::NewGuid().ToString()
$profilePath=Assert-DevPath (Join-Path $script:ProfileRoot "profiles\$id")
if(Test-Path -LiteralPath $profilePath){throw 'Profile must be new.'}
$evidence=New-DevDirectory (Join-Path $script:ProfileRoot "runtime\phase3a\desktop-$id")
$start=New-Object Diagnostics.ProcessStartInfo
$start.UseShellExecute=$false
$start.FileName=$exe
$start.WorkingDirectory=Split-Path $exe
$start.Arguments="--isolated-profile $id"
$start.EnvironmentVariables.Clear()
foreach($pair in @{SystemRoot=$env:SystemRoot;WINDIR=$env:SystemRoot;PATH=(Join-Path $env:SystemRoot 'System32');OS='Windows_NT';TEMP=$env:TEMP;TMP=$env:TMP;USERPROFILE=$script:ProfileRoot;LOCALAPPDATA=(Join-Path $script:ProfileRoot 'cache\appdata');APPDATA=(Join-Path $script:ProfileRoot 'config\appdata')}.GetEnumerator()) {$start.EnvironmentVariables[$pair.Key]=$pair.Value}
$process=[Diagnostics.Process]::Start($start)
$result=[ordered]@{instance=$id;profile=$profilePath;desktop_pid=$process.Id;ready=$false;visible_window=$false;verified_download=$false;normal_shutdown=$false}
$progressSamples=[Collections.Generic.List[long]]::new()
try {
  $deadline=[DateTime]::UtcNow.AddSeconds(215)
  $sinceReady=$null
  $manifest=Get-Content -Raw -LiteralPath (Join-Path $script:ProjectRoot 'backend\assets\models\whisper-small-manifest.json') | ConvertFrom-Json
  while ([DateTime]::UtcNow -lt $deadline -and -not $process.HasExited) {
    $process.Refresh()
    $result.visible_window=$result.visible_window -or ($process.MainWindowTitle -eq 'PODCUT AI — Installer Development')
    $record=Get-ChildItem -LiteralPath (Join-Path $profilePath 'runtime\desktop') -Filter '*.json' -ErrorAction SilentlyContinue | Select-Object -First 1
    if($record) {
      $state=Get-Content -Raw -LiteralPath $record.FullName | ConvertFrom-Json
      if($state.status -eq 'ready' -and $state.desktop_pid -eq $process.Id){$result.ready=$true;if(-not $sinceReady){$sinceReady=[DateTime]::UtcNow}}
    }
    $modelDir=Join-Path $profilePath 'models\whisper\small'
    $sizes=@(Get-ChildItem -LiteralPath $modelDir -File -ErrorAction SilentlyContinue) + @(Get-ChildItem -LiteralPath (Join-Path $profilePath 'temp') -Filter 'model-setup-*' -Directory -ErrorAction SilentlyContinue | ForEach-Object {Get-ChildItem -LiteralPath $_.FullName -File})
    $length=[long](($sizes|Measure-Object Length -Sum).Sum)
    if (-not $progressSamples.Count -or $progressSamples[$progressSamples.Count-1] -ne $length){$progressSamples.Add($length)}
    if(Test-Path -LiteralPath (Join-Path $modelDir 'LICENSE.OpenAI-Whisper.txt')) {
      $verified=@($manifest.files|Where-Object { (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $modelDir $_.name)).Hash -eq $_.sha256 })
      $result.verified_download=($verified.Count -eq $manifest.files.Count)
      if($result.verified_download -and $result.visible_window){Start-Sleep -Seconds 5;break}
    }
    if(Test-Path -LiteralPath (Join-Path $evidence 'finish-observation')){break}
    Start-Sleep -Milliseconds 700
  }
} finally {
  if(-not $process.HasExited){$null=$process.CloseMainWindow();if(-not $process.WaitForExit(20000)){$process.Kill();$process.WaitForExit()}}
  $result.exit_code=$process.ExitCode
  if($record){$end=Get-Content -LiteralPath $record.FullName -Raw|ConvertFrom-Json;$result.normal_shutdown=($end.status -eq 'stopped' -and $end.cleanup_confirmed -and $end.supervisor_exited -and $process.ExitCode -eq 0)}
  $result.observed_model_bytes=@($progressSamples)
  $result|ConvertTo-Json -Depth 5|Set-Content -LiteralPath (Join-Path $evidence 'report.json') -Encoding utf8
  $result|ConvertTo-Json -Depth 5
}
