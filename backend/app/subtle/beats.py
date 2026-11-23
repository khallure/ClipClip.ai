"""Local tempo, beat, and downbeat detection (FFmpeg + NumPy only)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np

from .. import config, media
from ..process_runtime import external_popen
from ..runtime_profile import checked_path

SR = 22050
N_FFT = 1024
HOP = 256
CACHE_VERSION = 1


def _decode(path: Path, *, start: float | None = None, end: float | None = None, cancelled=None) -> np.ndarray:
    ffmpeg = media.tool('ffmpeg')
    cmd = [ffmpeg, '-hide_banner', '-v', 'error', '-nostdin']
    if start is not None and start > 0:
        cmd += ['-ss', f'{start:.6f}']
    if end is not None and end > (start or 0):
        cmd += ['-t', f'{end - (start or 0):.6f}']
    cmd += [*media.input_args(path), '-vn', '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-']
    proc = external_popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=config.child_environment(),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    raw, err = proc.communicate(timeout=240)
    if cancelled and cancelled():
        raise media.MediaCancelled('Analisis beat dibatalkan.')
    if proc.returncode != 0:
        raise media.MediaError('FFmpeg gagal mendekode audio untuk analisis beat: ' + err.decode('utf-8', 'replace')[-300:])
    # Align length to float32 boundary
    n_bytes = len(raw) - (len(raw) % 4)
    return np.frombuffer(raw[:n_bytes], dtype=np.float32)


def onset_curves(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = 1 + max(0, (len(y) - N_FFT) // HOP)
    if n < 8:
        return np.zeros(0), np.zeros(0)
    win = np.hanning(N_FFT).astype(np.float32)
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    mag = np.abs(np.fft.rfft(y[idx] * win, axis=1))
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    edges = np.geomspace(40, 10000, 37)
    bands = np.stack([mag[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) for lo, hi in zip(edges[:-1], edges[1:])], axis=1)
    logb = np.log1p(20.0 * bands)
    flux = np.maximum(0.0, np.diff(logb, axis=0, prepend=logb[:1]))
    full = flux.sum(axis=1)
    low = flux[:, edges[:-1] < 160].sum(axis=1)

    def norm(x: np.ndarray) -> np.ndarray:
        x = x - np.convolve(x, np.ones(87) / 87, mode='same')
        x = np.maximum(0.0, x)
        s = x.std()
        return x / s if s > 0 else x

    return norm(full), norm(low)


def tempo_of(onset: np.ndarray, lo: float = 55.0, hi: float = 210.0, centre: float = 120.0) -> tuple[float, float]:
    fps = SR / HOP
    n = len(onset)
    ac = np.correlate(onset, onset, mode='full')[n - 1:]
    if ac[0] <= 0:
        return fps * 60.0 / centre, 0.0
    ac = ac / ac[0]
    lag_lo, lag_hi = int(fps * 60.0 / hi), min(int(fps * 60.0 / lo), n - 1)
    if lag_hi <= lag_lo:
        return fps * 60.0 / centre, 0.0
    lags = np.arange(lag_lo, lag_hi + 1)
    bpm = 60.0 * fps / lags
    weight = np.exp(-0.5 * (np.log2(bpm / centre) / 0.9) ** 2)
    score = (ac[lags] + 0.5 * ac[np.minimum(2 * lags, n - 1)]) * weight
    best = int(lags[int(np.argmax(score))])
    a = ac[best - 1] if best > 0 else ac[best]
    b = ac[best]
    c = ac[best + 1] if best + 1 < n else ac[best]
    off = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    return best + float(np.clip(off, -0.5, 0.5)), float(max(0.0, ac[best]))


def track(onset: np.ndarray, period: float, tightness: float = 100.0) -> np.ndarray:
    n = len(onset)
    score = onset.copy()
    back = -np.ones(n, dtype=int)
    lo, hi = max(1, int(round(period * 0.5))), max(2, int(round(period * 2.0)))
    gaps = np.arange(lo, hi + 1)
    penalty = -tightness * np.log(gaps / max(1e-6, period)) ** 2
    for t in range(lo, n):
        prev = t - gaps
        ok = prev >= 0
        if not ok.any():
            continue
        cand = score[prev[ok]] + penalty[ok]
        k = int(np.argmax(cand))
        score[t] = onset[t] + cand[k]
        back[t] = prev[ok][k]
    t = int(np.argmax(score[max(0, n - int(max(1, period))):])) + max(0, n - int(max(1, period)))
    beats = []
    while t >= 0:
        beats.append(t)
        t = int(back[t])
    return np.array(beats[::-1])


def analyse_audio_bytes(y: np.ndarray) -> dict:
    full, low = onset_curves(y)
    if len(full) < 64:
        return {'bpm': None, 'times': [], 'downbeats': [], 'clarity': 0.0}
    period, clarity = tempo_of(full)
    beats = track(full, period)
    env = np.convolve(np.abs(y), np.ones(HOP * 8) / (HOP * 8), mode='same')[::HOP][:len(full)]
    loud = env > 0.02 * env.max() if len(env) else np.zeros(0, dtype=bool)
    if loud.any():
        first, last = int(np.argmax(loud)), len(loud) - int(np.argmax(loud[::-1]))
        beats = beats[(beats >= first - period) & (beats <= last)]
    if len(beats) < 4:
        return {'bpm': None, 'times': [], 'downbeats': [], 'clarity': round(clarity, 3)}
    spacing = float(np.median(np.diff(beats)))
    bpm = 60.0 * SR / HOP / spacing
    w = 3
    strength = np.array([low[max(0, b - w):b + w + 1].max() for b in beats])
    phase = int(np.argmax([strength[p::4].mean() for p in range(4)]))
    ms = lambda b: int(round((b * HOP + N_FFT / 2) * 1000.0 / SR))  # noqa: E731
    times = [ms(int(b)) for b in beats]
    return {
        'bpm': round(bpm, 2),
        'times': times,
        'downbeats': times[phase::4],
        'clarity': round(clarity, 3),
        'beatsPerBar': 4,
    }


def detect_beats(
    audio_path: Path,
    *,
    start: float | None = None,
    end: float | None = None,
    cache_root: Path | None = None,
    cancelled=None,
) -> dict:
    h = hashlib.sha256()
    with audio_path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    digest = h.hexdigest()
    range_key = f'{round(start or 0.0, 3)}-{round(end, 3) if end is not None else "full"}'
    cache_dir = cache_root or (config.CACHE_DIR / 'subtle' / 'beats')
    checked_path(cache_dir, config.RUNTIME.root, 'Subtle beats cache')
    cache_file = checked_path(cache_dir / f'{digest[:16]}-{hashlib.sha256(range_key.encode()).hexdigest()[:10]}-v{CACHE_VERSION}.json',
                              config.RUNTIME.root, 'Subtle beat cache file')
    if cache_file.is_file():
        return json.loads(cache_file.read_text(encoding='utf-8'))
    y = _decode(audio_path, start=start, end=end, cancelled=cancelled)
    res = analyse_audio_bytes(y)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(res), encoding='utf-8')
    return res
