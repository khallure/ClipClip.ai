"""Per-shot subject analysis for behind-the-speaker and split caption styles."""
from __future__ import annotations

from pathlib import Path

BANDS = 24
TARGET = 0.30
MIN_COVER = 0.06
MAX_COVER = 0.45
ROW_MAX = 0.55
MIN_SHOT_MS = 700
SAMPLES = 7
SUBJECT_EVERY_MS = 400


def shots_from_cuts(cuts: list[int], duration_ms: int) -> list[tuple[int, int]]:
    edges = [0] + [int(c) for c in sorted(cuts) if 0 < int(c) < duration_ms] + [int(duration_ms)]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def sample_times_for(shot: tuple[int, int], fps: float) -> list[int]:
    a, b = shot
    pad = min(200, (b - a) // 4)
    lo, hi = a + pad, max(a + pad, b - pad - int(1000 / max(1.0, fps)))
    n = max(2, min(SAMPLES + (b - a) // 5000, 12)) if b - a >= 1500 else 2 if b - a >= 700 else 1
    if n == 1:
        return [int((a + b) / 2)]
    return sorted({int(lo + (hi - lo) * k / (n - 1)) for k in range(n)})


def window_cover(bands: list[float], c: float, h: float) -> float:
    n = len(bands)
    top, bot = max(0.0, c - h / 2) * n, min(1.0, c + h / 2) * n
    tot = wsum = 0.0
    for i in range(int(top), min(n, int(bot) + 1)):
        w = min(i + 1, bot) - max(i, top)
        if w > 0:
            tot += bands[i] * w
            wsum += w
    return tot / wsum if wsum else 0.0


def window_max(bands: list[float], c: float, h: float) -> float:
    n = len(bands)
    top, bot = max(0.0, c - h / 2) * n, min(1.0, c + h / 2) * n
    return max((bands[i] for i in range(int(top), min(n, int(bot) + 1)) if min(i + 1, bot) - max(i, top) > 0.25), default=0.0)


def best_window(bands: list[float], h: float, lo: float = 0.0, hi: float = 0.7, target: float = TARGET) -> tuple[float, float]:
    best = None
    y = max(lo, h / 2 + 0.02)
    while y <= min(hi, 0.98 - h / 2) + 1e-9:
        cov = window_cover(bands, y, h)
        cost = abs(cov - target) + 0.02 * y + (1.0 if window_max(bands, y, h) > ROW_MAX else 0.0)
        if best is None or cost < best[0] - 1e-9:
            best = (cost, y, cov)
        y += 0.005
    return (best[1], best[2]) if best else (0.3, 1.0)


def analyse_shot(shot: tuple[int, int], frames: list[dict], band_h: float, target: float = TARGET) -> dict:
    a, b = shot
    seen = [f for f in frames if f.get('person') and max(f['person']) > 0.04]
    out: dict = {'startMs': a, 'endMs': b, 'samples': len(frames), 'person': round(len(seen) / len(frames), 2) if frames else 0.0}
    if not seen:
        out.update(behind=False, reason='tidak ada pembicara terdeteksi')
        return out
    n = len(seen[0]['person'])
    mean = [sum(f['person'][i] for f in seen) / len(seen) for i in range(n)]
    out['bands'] = [round(v, 2) for v in mean]
    faces = [max(f['faces'], key=lambda r: r[2] * r[3]) for f in seen if f.get('faces')]
    if faces:
        out['face'] = [round(sorted(r[k] for r in faces)[len(faces) // 2], 3) for k in range(4)]
    y, cov = best_window(mean, band_h, target=target)
    out['y'], out['cover'] = round(y * 100, 1), round(cov, 2)
    if b - a < MIN_SHOT_MS:
        out.update(behind=False, reason='shot terlalu singkat')
    elif len(seen) / max(1, len(frames)) < 0.6:
        out.update(behind=False, reason='pembicara hanya muncul < 60% sampel shot')
    elif not (MIN_COVER <= cov <= MAX_COVER) or window_max(mean, y, band_h) > ROW_MAX:
        out.update(
            behind=False,
            reason='tidak ada subjek menutupi teks' if cov < MIN_COVER
            else f'close-up ({int(window_max(mean, y, band_h) * 100)}% baris terisi subjek); dialihkan ke depan',
        )
    else:
        out['behind'] = True
    return out


def subject_entry(t: int, f: dict) -> dict:
    e: dict = {'ms': t, 'bands': [round(v, 2) for v in (f.get('person') or [])]}
    if f.get('faces'):
        e['face'] = [round(v, 3) for v in max(f['faces'], key=lambda r: r[2] * r[3])]
    return e


def shot_at(shots: list[dict], ms: int):
    for s in shots or []:
        if s['startMs'] <= ms < s['endMs']:
            return s
    return None


def mark_bigs(cap: dict) -> dict:
    shots = cap.get('shots')
    if not shots:
        return cap
    for b in cap.get('big') or []:
        s = shot_at(shots, b['startMs'] + 40)
        if b.get('close') and (s is None or s.get('behind')):
            b.pop('front', None)
            b.pop('close', None)
        if s is not None and not s.get('behind') and not b.get('front'):
            b['front'] = True
            b['close'] = True
    return cap


def band_height(style: dict) -> float:
    b = style.get('behind') or {}
    if b.get('bandHeight'):
        return float(b['bandHeight'])
    return (float(b.get('bigSize', 14)) + float(b.get('bigMaxSize', 24))) / 200.0


def apply(video: Path, cap: dict, style: dict, *, sampler=None, offset_sec: float = 0.0, force_behind: bool = False, cancelled=None) -> tuple[list[str], list[dict]]:
    fps = float(cap['video'].get('fps') or 30)
    dur = cap['video']['durationMs']
    if dur <= 0:
        return [], []
    if sampler is None:
        from .. import subtle_vision
        sampler = lambda times: subtle_vision.sample_subject(video, times, bands=BANDS, offset_sec=offset_sec, cancelled=cancelled)  # noqa: E731
    spans = shots_from_cuts(cap.get('cuts') or [], dur)
    per = [sample_times_for(s, fps) for s in spans]
    tl = list(range(0, max(1, int(dur - 1000 / max(1.0, fps))), SUBJECT_EVERY_MS))
    got = sampler(sorted({t for ts in per for t in ts} | set(tl)))
    h = band_height(style)
    tgt = float((style.get('behind') or {}).get('target', TARGET))
    shots = [analyse_shot(s, [got[t] for t in ts if t in got], h, tgt) for s, ts in zip(spans, per)]
    if force_behind:
        for s in shots:
            s['behind'] = True
            if 'y' not in s:
                s['y'] = float((style.get('behind') or {}).get('bigY', 26))
    cap['shots'] = shots
    cap['subject'] = [subject_entry(t, got[t]) for t in tl if t in got]
    mark_bigs(cap)
    ok = sum(1 for s in shots if s.get('behind'))
    notes = [f'behind-the-speaker: {ok} dari {len(shots)} shot menyediakan ruang di belakang subjek']
    return notes, shots
