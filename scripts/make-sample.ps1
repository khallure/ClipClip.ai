# Generate original test media locally with a Windows system voice and FFmpeg.
# The result is QA material, never an example of real podcast analysis quality.
. (Join-Path $PSScriptRoot 'common.ps1')
$sampleDirectory = New-DevDirectory (Join-Path $script:ProfileRoot 'temp\sample')
$voicePath = Assert-DevPath (Join-Path $sampleDirectory 'owned-voice.wav')
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice.Rate = -1
$voice.SetOutputToWaveFile($voicePath)
$text = @'
I remember losing my first freelance project because I promised a deadline before asking what the customer actually needed. We tried adding more late nights, but the extra hours only created revisions. Nobody had agreed on the deliverable. The lesson was to write one acceptance example before estimating the project. That way both people can identify a finished result. Now a small paid trial replaces vague promises. The result is fewer surprises for the designer and for the customer.

Here is another surprising lesson. People often believe that working faster always means working better. Actually, I learned that taking a short break before checking my work helps me notice mistakes. Because I return with fresh eyes, I can see problems that were invisible a few minutes earlier. The takeaway is simple. Finish a draft, take a walk, and review it once before you send it. A small pause can save a whole afternoon of revisions.

When I first started public speaking, I was afraid of forgetting every word. I practiced alone in my room but never practiced with an audience. Then a friend suggested telling just one short story at dinner. I was surprised that the fear became smaller after I started. The lesson is not to wait until you feel brave. Start with a small audience that supports you, practice one clear idea, and increase the challenge a little each week.
'@
try { $voice.Speak($text) } finally { $voice.Dispose() }
$ffmpegExecutable = Find-DevFFmpeg
$samplePath = Assert-DevPath (Join-Path $sampleDirectory 'owned-podcast-sample.mp4')
& $ffmpegExecutable -hide_banner -loglevel error -nostdin -y -f lavfi -i 'testsrc2=size=640x360:rate=24' -i $voicePath -map '0:v:0' -map '1:a:0' -shortest -c:v libx264 -preset ultrafast -crf 24 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart $samplePath
if ($LASTEXITCODE -ne 0) { throw 'Synthetic sample encode failed.' }
Write-Host "Generated owned QA sample: $samplePath"
