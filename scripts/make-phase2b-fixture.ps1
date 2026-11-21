# Independent public-domain NASA still + Windows synthesized speech. QA only.
. (Join-Path $PSScriptRoot 'common.ps1')
$fixtureRoot = New-DevDirectory (Join-Path $script:ProfileRoot 'temp\phase2b-fixtures')
$image = Assert-DevPath (Join-Path $fixtureRoot 'astronaut.png')
if (-not (Test-Path -LiteralPath $image)) { throw 'The independently downloaded NASA astronaut fixture is missing.' }
$voiceFile = Assert-DevPath (Join-Path $fixtureRoot 'speech.wav')
$text = @'
I used to lose an entire afternoon fixing work that nobody had actually requested. The surprising part was that working faster only created more revisions. So I changed one thing. Before starting a project, I wrote down one clear example of a finished result. The customer checked that example before I estimated the deadline. This small question saved more time than any expensive tool.

Here is another mistake I learned to avoid. I once believed that longer working hours always produced better results. But after a short break I could see errors that had been invisible all morning. The useful habit was simple. Finish a draft, take a short walk, and check the result once before sending it. A small pause can prevent an entire afternoon of revisions.

The lesson is to begin with a clear question and review one small result. That makes progress easier to measure, and it makes the next decision easier to explain.
'@
[IO.File]::WriteAllText((Assert-DevPath (Join-Path $fixtureRoot 'speech-reference.txt')), $text)
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $english = @($voice.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'en-*' -and $_.Enabled }) | Select-Object -First 1
    if (-not $english) { throw 'An English Windows speech voice is unavailable; no cloud substitute is used.' }
    $voice.SelectVoice($english.VoiceInfo.Name)
    $voice.Rate = 0
    $voice.SetOutputToWaveFile($voiceFile)
    $voice.Speak($text)
} finally { $voice.Dispose() }
$ffmpeg = Find-DevFFmpeg
$shots = @()
foreach ($shot in @(@{Name='left';X=0}, @{Name='right';X=768})) {
    $target = Assert-DevPath (Join-Path $fixtureRoot ('shot-' + $shot.Name + '.mp4'))
    & $ffmpeg -v error -nostdin -y -loop 1 -framerate 25 -i $image -t 6 -vf ('scale=512:512,pad=1280:720:' + $shot.X + ':104:color=0x181818,setsar=1') -an -c:v libx264 -preset ultrafast -crf 20 -threads 2 -pix_fmt yuv420p $target
    if ($LASTEXITCODE -ne 0) { throw 'Single-face fixture encode failed.' }
    $shots += $target
}
$master = Assert-DevPath (Join-Path $fixtureRoot 'shot-both.mp4')
& $ffmpeg -v error -nostdin -y -loop 1 -framerate 25 -i $image -t 6 -filter_complex '[0:v]scale=512:512,split=2[a][b];[a]pad=1280:720:0:104:color=0x181818[base];[base][b]overlay=768:104,setsar=1[out]' -map '[out]' -an -c:v libx264 -preset ultrafast -crf 20 -threads 2 -pix_fmt yuv420p $master
if ($LASTEXITCODE -ne 0) { throw 'Two-face fixture encode failed.' }
$shots += $master
$concat = Assert-DevPath (Join-Path $fixtureRoot 'shots.txt')
[IO.File]::WriteAllLines($concat, @($shots | ForEach-Object { "file '" + $_.Replace('\','/') + "'" }))
$video = Assert-DevPath (Join-Path $fixtureRoot 'owned-face-speech.mp4')
& $ffmpeg -v error -nostdin -y -stream_loop -1 -f concat -safe 0 -i $concat -i $voiceFile -map '0:v:0' -map '1:a:0' -shortest -c:v copy -c:a aac -ar 48000 -b:a 128k -movflags +faststart $video
if ($LASTEXITCODE -ne 0) { throw 'Speech/face source mux failed.' }
[ordered]@{
    fixture = $video; face_image = $image; image_sha256 = (Get-FileHash -LiteralPath $image).Hash
    image_source = 'https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png'
    license_reference = 'https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.astronaut'
    notice = 'Public-domain NASA portrait duplicated into synthetic shots; Windows synthesized English speech. Not a real podcast or active-speaker benchmark.'
    source_sha256 = (Get-FileHash -LiteralPath $video).Hash
    hard_cuts_seconds = @(6,12,18,24,30,36,42,48,54,60)
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Assert-DevPath (Join-Path $script:ProfileRoot 'runtime\phase2b\fixture-provenance.json')) -Encoding UTF8
Write-Host ('Independent face/speech QA fixture: ' + $video)
