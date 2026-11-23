"""Hard cuts: detect scene cuts via isolated FFmpeg and prevent pages from straddling them."""
from __future__ import annotations

import bisect
from pathlib import Path
import re

from .. import media

THRESHOLD = 0.30
FLASH_MS = 220
MIN_SHOT_MS = 350


def detect(
    video: Path,
    *,
    threshold: float = THRESHOLD,
    fps: float | None = None,
    start: float | None = None,
    end: float | None = None,
    cancelled=None,
) -> list[int]:
    ffmpeg = media.tool('ffmpeg')
    cmd = [ffmpeg, '-hide_banner', '-v', 'error', '-nostdin']
    if start is not None and start > 0:
        cmd += ['-ss', f'{start:.6f}']
    if end is not None and end > (start or 0):
        cmd += ['-t', f'{end - (start or 0):.6f}']
    cmd += [
        *media.input_args(video),
        '-an',
        '-vf', f"scale=320:-2,select='gt(scene,{min(threshold, 0.2)})',metadata=print:file=-",
        '-f', 'null', '-',
    ]
    out = media.run(cmd, cancelled=cancelled, timeout=300, capture=True)
    cand: list[tuple[float, float]] = []
    t: float | None = None
    for line in out.splitlines():
        m = re.search(r'pts_time:([\d.]+)', line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r'scene_score=([\d.]+)', line)
        if m and t is not None:
            cand.append((t * 1000.0, float(m.group(1))))
            t = None
    return pick(cand, threshold, fps)


def pick(cand: list[tuple[float, float]], threshold: float = THRESHOLD, fps: float | None = None) -> list[int]:
    cand = sorted(c for c in cand if c[1] >= threshold)
    drop: set[int] = set()
    for i in range(1, len(cand)):
        if cand[i][0] - cand[i - 1][0] < FLASH_MS:
            drop.update((i - 1, i))
    cand = [c for i, c in enumerate(cand) if i not in drop]
    kept: list[tuple[float, float]] = []
    for ms, sc in cand:
        if kept and ms - kept[-1][0] < MIN_SHOT_MS:
            if sc > kept[-1][1]:
                kept[-1] = (ms, sc)
            continue
        kept.append((ms, sc))
    out: list[int] = []
    for ms, _ in kept:
        if ms < MIN_SHOT_MS:
            continue
        out.append(int(round(round(ms * fps / 1000.0) * 1000.0 / fps)) if fps else int(round(ms)))
    return out


def shot_bounds(cuts: list[int], ms: float) -> tuple[int, int | None]:
    k = bisect.bisect_right(cuts, ms)
    return (cuts[k - 1] if k else 0), (cuts[k] if k < len(cuts) else None)


def _mid(w: dict) -> float:
    return (w['startMs'] + w['endMs']) / 2.0


def _same_origin(pages: list[list[dict]], a: list[dict], b: list[dict]) -> bool:
    for p in pages:
        if any(w is a[-1] for w in p):
            return any(w is b[0] for w in p)
    return False


def split_pages(pages: list[list[dict]], cuts: list[int], min_ms: int = 330, max_words: int = 4, max_chars: int = 40) -> list[list[dict]]:
    if not cuts:
        return pages
    shot = lambda w: bisect.bisect_right(cuts, _mid(w))  # noqa: E731
    frags: list[tuple[list[dict], int]] = []
    for p in pages:
        if not p:
            continue
        run = [p[0]]
        for w in p[1:]:
            if shot(w) != shot(run[-1]):
                frags.append((run, shot(run[0])))
                run = []
            run.append(w)
        frags.append((run, shot(run[0])))

    def tiny(ws: list[dict]) -> bool:
        return len(ws) == 1 and (ws[0]['endMs'] - ws[0]['startMs'] < min_ms or len(ws[0]['text']) <= 3)

    def fits(a: list[dict], b: list[dict]) -> bool:
        return len(a) + len(b) <= max_words and len(' '.join(x['text'] for x in a + b)) <= max_chars

    i = 0
    while i < len(frags):
        ws, sh = frags[i]
        is_split_piece = (
            (i > 0 and frags[i - 1][1] != sh and _same_origin(pages, frags[i - 1][0], ws))
            or (i + 1 < len(frags) and frags[i + 1][1] != sh and _same_origin(pages, ws, frags[i + 1][0]))
        )
        if is_split_piece and tiny(ws):
            if i > 0 and frags[i - 1][1] == sh and fits(frags[i - 1][0], ws):
                frags[i - 1] = (frags[i - 1][0] + ws, sh)
                del frags[i]
                continue
            if i + 1 < len(frags) and frags[i + 1][1] == sh and fits(ws, frags[i + 1][0]):
                frags[i + 1] = (ws + frags[i + 1][0], sh)
                del frags[i]
                continue
        i += 1
    return [f[0] for f in frags]


def clamp_pages(pages: list[dict], cuts: list[int], fps: float) -> list[dict]:
    if not cuts:
        return pages
    frame = 1000.0 / fps
    snap = lambda x: int(round(round(x / frame) * frame))  # noqa: E731
    for p in pages:
        if not p['words']:
            continue
        lo, hi = shot_bounds(cuts, _mid(p['words'][0]))
        if p['startMs'] < lo:
            p['startMs'] = snap(lo)
        if hi is not None and p['endMs'] > hi:
            p['endMs'] = snap(hi)
        if p['endMs'] <= p['startMs']:
            p['endMs'] = snap(p['startMs'] + frame)
    return pages
