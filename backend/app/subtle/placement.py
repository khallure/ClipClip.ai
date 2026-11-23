"""Subject-aware caption placement: avoid faces and hands inside style's flex range."""
from __future__ import annotations

import bisect
from pathlib import Path

BANDS = 24
FIXED_TYPES = {'chalkline', 'downbeat', 'billboard', 'singalong', 'keyword'}
FACE_MARGIN = (0.025, 0.035)
HAND_MARGIN = (0.03, 0.04)
TOLERANCE = 0.01


def flex_range(style: dict) -> tuple[float, float]:
    pos = style.get('position') or {}
    y = float(pos.get('y', 50))
    if (style.get('anim') or {}).get('type') in FIXED_TYPES or style.get('group') == 'music':
        return y, y
    flex = pos.get('flex', None)
    if flex is None:
        lo, hi = max(10.0, y - 12.0), min(max(84.0, y), y + 12.0)
    elif not flex:
        return y, y
    else:
        lo, hi = float(flex[0]), float(flex[1])
    return min(lo, y), max(hi, y)


def caption_size(style: dict, page: dict, frame_w: int, frame_h: int) -> tuple[float, float]:
    pos = style.get('position') or {}
    wide = frame_w > frame_h
    scale = float(style.get('wideScale', 1.35)) if wide else 1.0
    size = float(style.get('size', 4)) / 100.0 * frame_h * scale
    lh = float(style.get('lineHeight', 1.15))
    ws = page['words']
    lines: dict[int, list[str]] = {}
    for w in ws:
        lines.setdefault(min(int(w.get('line', 0)), 3), []).append(w['text'])
    n_lines = max(1, len(lines))
    anim = style.get('anim') or {}
    cont = style.get('container') or {}
    char_w = 0.68 if style.get('case') == 'upper' else 0.58
    if anim.get('type') == 'stack':
        n_lines, lh = len(ws), anim.get('pitch', 1.08) * 0.72
        longest = max((len(w['text']) for w in ws), default=4)
        width = min(float(anim.get('pileWidth', 50)) / 100.0, longest * size * 0.62 / frame_w + 0.1)
    else:
        longest = max((len(' '.join(t)) for t in lines.values()), default=4)
        width = longest * size * char_w / frame_w + 2 * float(cont.get('padX', 0)) * size / frame_w
        if anim.get('type') == 'sticker':
            lh = float(cont.get('height', 1.5)) * 1.05
    width = min(width, 1.0 - 2 * float(pos.get('marginX', 6)) / 100.0)
    height = (n_lines * size * lh + 2 * float(cont.get('padY', 0.3 if cont else 0)) * size) / frame_h
    return width, height


def overlap_share(box: tuple[float, float, float, float], other: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(box[0] + box[2], other[0] + other[2]) - max(box[0], other[0]))
    iy = max(0.0, min(box[1] + box[3], other[1] + other[3]) - max(box[1], other[1]))
    return ix * iy / max(1e-9, box[2] * box[3])


def grow(b: tuple[float, float, float, float] | list[float], m: tuple[float, float]) -> tuple[float, float, float, float]:
    return (b[0] - m[0], b[1] - m[1], b[2] + 2 * m[0], b[3] + 2 * m[1])


def conflict(box: tuple[float, float, float, float], frames: list[dict]) -> float:
    if not frames:
        return 0.0
    total = 0.0
    for f in frames:
        c = 0.0
        for fb in f.get('faces', []):
            c += 3.0 * overlap_share(box, grow(fb, FACE_MARGIN))
        for hb in f.get('hands', []):
            c += 2.0 * overlap_share(box, grow(hb, HAND_MARGIN))
        total += c
    return total / len(frames)


def busy(box: tuple[float, float, float, float], frames: list[dict]) -> float:
    vals = []
    for f in frames:
        per = f.get('person') or []
        if not per:
            continue
        a = max(0, int(box[1] * len(per)))
        b = min(len(per), max(a + 1, int((box[1] + box[3]) * len(per) + 0.999)))
        seg = per[a:b]
        if seg:
            vals.append(sum(seg) / len(seg))
    return sum(vals) / len(vals) if vals else 0.0


def choose(frames: list[dict], size: tuple[float, float], cx: float, lo: float, hi: float, y_pref: float, prev_y: float | None = None, step: float = 1.0) -> float:
    w, h = size
    if lo >= hi:
        return float(y_pref)

    def box_at(y: float) -> tuple[float, float, float, float]:
        return (cx - w / 2, y / 100.0 - h / 2, w, h)

    if prev_y is not None and lo - 1e-6 <= prev_y <= hi + 1e-6 and conflict(box_at(prev_y), frames) <= TOLERANCE:
        return prev_y
    ref = prev_y if prev_y is not None else y_pref
    best, best_cost = float(y_pref), None
    y = lo
    while y <= hi + 1e-6:
        b = box_at(y)
        if (b[1] >= 0.03 and b[1] + b[3] <= 0.96) or abs(y - y_pref) < 1e-6:
            cost = 100.0 * conflict(b, frames) + 0.25 * busy(b, frames) + 0.01 * abs(y - ref)
            if best_cost is None or cost < best_cost - 1e-9:
                best, best_cost = y, cost
        y += step
    return round(best * 2) / 2.0


def place_pages(pages: list[dict], samples: list[list[dict]], style: dict, frame_w: int, frame_h: int, cuts=()) -> list[float]:
    lo, hi = flex_range(style)
    y_pref = float((style.get('position') or {}).get('y', 50))
    cx = float((style.get('position') or {}).get('x', 50)) / 100.0
    out, prev_y, prev_shot = [], None, None
    cuts_list = list(cuts)
    for p, frames in zip(pages, samples):
        shot = bisect.bisect_right(cuts_list, (p['words'][0]['startMs'] + p['words'][0]['endMs']) / 2.0)
        new_shot = prev_shot is not None and shot != prev_shot
        size = caption_size(style, p, frame_w, frame_h)
        y = choose(frames, size, cx, lo, hi, y_pref, None if (new_shot or prev_y is None) else prev_y)
        out.append(y)
        prev_y, prev_shot = y, shot
    return out


def sample_times(pages: list[dict], cuts: list[int], duration_ms: int, fps: float) -> tuple[list[list[int]], list[int]]:
    frame = 1000.0 / max(1.0, fps)
    per_page = []
    for p in pages:
        s, e = p['startMs'], max(p['endMs'] - frame, p['startMs'])
        per_page.append([int(min(max(0, duration_ms - frame), s + (e - s) * k)) for k in (0.15, 0.5, 0.85)])
    extra = [int(min(max(0, duration_ms - frame), c + 2 * frame)) for c in cuts]
    return per_page, extra


def apply(video: Path, cap: dict, style: dict, cuts=(), *, sampler=None, offset_sec: float = 0.0, cancelled=None) -> tuple[list[str], dict]:
    pages = cap['pages']
    if not pages:
        return [], {'moved': 0, 'pages': 0}
    vw, vh = cap['video']['width'], cap['video']['height']
    fps = float(cap['video'].get('fps') or 30)
    lo, hi = flex_range(style)
    if lo >= hi:
        return ['placement: style ini mempertahankan posisi tetap'], {'moved': 0, 'pages': len(pages)}
    if sampler is None:
        from .. import subtle_vision
        sampler = lambda times: subtle_vision.sample_subject(video, times, bands=BANDS, offset_sec=offset_sec, cancelled=cancelled)  # noqa: E731
    per_page, extra = sample_times(pages, list(cuts or ()), cap['video']['durationMs'], fps)
    got = sampler(sorted({t for ts in per_page for t in ts} | set(extra)))
    samples = [[got[t] for t in ts if t in got] for ts in per_page]
    ys = place_pages(pages, samples, style, vw, vh, cuts)
    for p, y in zip(pages, ys):
        p['y'] = y
    pref = float((style.get('position') or {}).get('y', 50))
    moved = sum(1 for y in ys if abs(y - pref) > 0.01)
    changes = sum(1 for a, b in zip(ys, ys[1:]) if a != b)
    seen = sum(1 for s in samples for f in s if f.get('faces') or f.get('hands'))
    return [], {'pages': len(pages), 'moved': moved, 'changes': changes, 'range': [lo, hi], 'samplesWithSubject': seen}
