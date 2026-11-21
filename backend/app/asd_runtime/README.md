# Local Light-ASD runtime

This optional Python process consumes existing Milestone 1 face tracks. It does
not detect faces, transcribe, select highlights, contact any model API, or modify
V1 dependencies. Use an isolated Python 3.12 environment with PyTorch 2.7.1 and
the pinned preprocessing requirements alongside this file.

The model, encoder and classifier are adapted from the MIT licensed official
[Light-ASD repository](https://github.com/Junhua-Liao/Light-ASD) at commit
`ed38c232de5efe0261dbd68627c0ade7cdfe14eb`. Only relative imports and provenance
comments differ in the vendored architecture. `LICENSE.Light-ASD` retains the
upstream copyright and permission notice. The checkpoint distributed inside the
same upstream repository is stored in `backend/assets/active_speaker`; its
`PROVENANCE.json` records download URLs and original file hashes. No separate
weight-specific license was found. Training data rights are not represented by
the MIT code license.

From `backend`, run:

```powershell
..\.venv-asd\Scripts\python.exe -m app.asd_runtime.runner manifest.json --output result.json
..\.venv-asd\Scripts\python.exe -m unittest app.asd_runtime.test_runtime
```

Manifest fields:

```json
{
  "source": "D:/media/podcast.mp4",
  "ffmpeg": "D:/tools/ffmpeg.exe",
  "weights": "D:/podcut-ai/backend/assets/active_speaker/light-asd-talkset.model",
  "start": 2401.84,
  "end": 2411.92,
  "source_width": 3840,
  "device": "cpu",
  "cpu_threads": 4,
  "cache_dir": "D:/podcut-ai/backend/data/reframing-cache/prepared",
  "face_samples": [
    {"time": 2401.84, "faces": [
      {"track_id": 1, "bbox": [0.1, 0.2, 0.1, 0.2], "confidence": 0.95}
    ]}
  ]
}
```

Supply one shot at a time, using source-global times and normalized XYWH boxes.
Track identity must not be reused across camera cuts in one request. Missing
boxes interpolate only between observations at most 0.5 seconds apart. Track
edges extend by at most 0.2 seconds; low-confidence or missing observations stay
invalid. No face detector is run. The selected interval is limited to 600 seconds.

FFmpeg reads the original source and creates a temporary proxy at up to 1920
pixels wide and 25 fps, plus aligned mono 16 kHz PCM audio. OpenCV only opens an
ASCII temporary path to avoid Windows Unicode path failures. This proxy is for
analysis only; final rendering must use the original source.

Preprocessing follows the official inference: padded face crop with crop scale
0.4, 224-pixel resize, grayscale center 112 pixels, 13-coefficient MFCCs with
25 ms windows and 10 ms steps. The checkpoint visual normalization and audio
encoder remain unchanged. Inference uses unique chunk durations 1–6 seconds;
their frame logits are averaged, then the two-class softmax is computed. Raw
positive logits are also returned. Tiny trailing MFCC/video differences are
trimmed and reported in `processed_end`; original clip duration is not changed
by this analysis.

`tracks[].frames[]` contains `time`, `valid`, `speaking_score`, `logits`, and
`raw_positive_logit`. Invalid observations return null scores. These are actual
model outputs and **are not calibrated probabilities of speaker correctness**.
The caller must handle silence, overlapping speakers, low margins, missing
tracks, review state, hysteresis and manual priority. Measured audio RMS values
are supplied in `media.audio_rms_dbfs`; energy is not speech detection.

Prepared NPZ cache keys include the source path, size, modification time, shot
range, tracks, threshold settings and preprocessing version. They exclude the
checkpoint/device so a model comparison or GPU retry reuses decoded frames and
MFCCs. Stored arrays never use Python pickle. Runtime output reports cache hits
and timings. The application is responsible for cache retention and cleanup.

Two optional diagnostic-only manifest flags are `diagnostic_silent_audio: true`
and `diagnostic_audio_shift: 2.0`. They modify audio features after loading the
cached original inputs. They must not be used for normal analysis.

The integrated M2 supervisor additionally uses official SyncNet-v2 embeddings
to require a local audio/face synchronization distance margin, with explicit
offset tolerance. `syncnet_runner.py` consumes the same M1 tracks, with official
224-pixel BGR five-frame crops and MFCC audio. It returns real distance margins
and offsets in two-second rolling contexts; those margins are not probabilities.
Its MIT code notice is `LICENSE.SyncNet`. Oxford's weights are separate:
[research purposes under Creative Commons Attribution](https://www.robots.ox.ac.uk/~vgg/software/lipsync/).
They are downloaded by the optional setup into ignored `.tools/active-speaker`,
verified by fixed SHA-256, and are not bundled under the code's MIT license.
Normal inference disables ablations. The supervisor uses chunks of up to 22.4
seconds and retains raw model outputs, so crop edits do not rerun either model.

Initial real-master experiments showed the TalkSet checkpoint strongly favored
the manually identified left speaker. However, it also remained strongly positive
on that face with silent or time-shifted audio. This is a genuine joint model
whose outputs are visually dominated on that example; these experiments do not
establish reliable lip synchronization discrimination. Keep this feature
experimental and use conservative review/fallback. User-podcast accuracy must be
measured against independent manual annotations, not upstream benchmark figures.
