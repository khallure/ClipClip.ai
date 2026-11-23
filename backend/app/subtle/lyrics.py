"""Align a user-provided lyrics text onto word timings from transcription (ported MIT)."""
from __future__ import annotations

import difflib
import re


def norm(text: str) -> str:
    t = re.sub(r"[^\w']+", '', text.lower(), flags=re.UNICODE).replace("'", '')
    return re.sub(r'in$', 'ing', t) if t.endswith('in') and len(t) > 4 else t


def read_lyrics_text(raw_text: str) -> list[tuple[int, str]]:
    out, n = [], 0
    for raw in (raw_text or '').splitlines():
        line = re.sub(r'\[[^\]]*\]|\((?:x\s*\d+|\d+\s*x|repeat[^)]*)\)', '', raw, flags=re.I).strip()
        if not line:
            continue
        for w in line.split():
            if norm(w):
                out.append((n, w))
        n += 1
    return out


def align_lyrics(heard: list[dict], lyrics: list[tuple[int, str]], keep_min: int = 4) -> tuple[list[dict], dict]:
    a = [norm(w['text']) for w in heard]
    b = [norm(w) for _, w in lyrics]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out: list[dict] = []
    stats = {'lyricWords': len(b), 'matched': 0, 'placedBetween': 0, 'notHeard': 0, 'keptAsHeard': 0, 'droppedHeard': 0}
    extra_line = 100000

    def put(line: int, text: str, start: float, end: float) -> None:
        out.append({
            'text': text,
            'startMs': int(round(start)),
            'endMs': int(round(max(end, start + 60))),
            'prob': None,
            'lyricLine': line,
        })

    def keep_heard(run: list[dict]) -> None:
        nonlocal extra_line
        if len(run) < keep_min:
            stats['droppedHeard'] += len(run)
            return
        line = extra_line
        for k, h in enumerate(run):
            if k and (h['startMs'] - run[k - 1]['endMs'] > 450 or re.search(r'[.!?]$', run[k - 1]['text'])):
                line += 1
            put(line, h['text'], h['startMs'], h['endMs'])
        extra_line = line + 1
        stats['keptAsHeard'] += len(run)

    def place_in_gap(j1: int, j2: int, t0: float, t1: float) -> None:
        n = j2 - j1
        if n and t1 - t0 >= 160 * n:
            step = (t1 - t0) / n
            for k, j in enumerate(range(j1, j2)):
                put(lyrics[j][0], lyrics[j][1], t0 + k * step, t0 + (k + 0.9) * step)
            stats['placedBetween'] += n
        else:
            stats['notHeard'] += n

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                h = heard[i1 + k]
                line, text = lyrics[j1 + k]
                put(line, text, h['startMs'], h['endMs'])
            stats['matched'] += i2 - i1
        elif tag == 'replace':
            nh, nl = i2 - i1, j2 - j1
            first_line = lyrics[j1][0]
            head = next((j for j in range(j1, j2) if lyrics[j][0] != first_line), j2) - j1
            if nl > nh and head <= nh and nh > 0:
                t0, t1 = heard[i1]['startMs'], heard[i2 - 1]['endMs']
                weights = [max(2, len(norm(lyrics[j][1]))) for j in range(j1, j1 + head)]
                total, at = float(sum(weights)), float(t0)
                for j, wgt in zip(range(j1, j1 + head), weights):
                    span = (t1 - t0) * wgt / total
                    put(lyrics[j][0], lyrics[j][1], at, at + span * 0.92)
                    at += span
                stats['placedBetween'] += head
                place_in_gap(j1 + head, j2, t1, heard[i2]['startMs'] if i2 < len(heard) else t1)
            else:
                n = min(nh, nl)
                for k in range(n):
                    h = heard[i1 + k]
                    put(lyrics[j1 + k][0], lyrics[j1 + k][1], h['startMs'], h['endMs'])
                stats['placedBetween'] += n
                if nh > nl:
                    keep_heard(heard[i1 + n:i2])
                elif nl > nh:
                    t0 = heard[i2 - 1]['endMs'] if i2 > i1 else 0
                    place_in_gap(j1 + n, j2, t0, heard[i2]['startMs'] if i2 < len(heard) else t0)
        elif tag == 'insert':
            t0 = out[-1]['endMs'] if out else 0
            t1 = heard[i1]['startMs'] if i1 < len(heard) else t0
            place_in_gap(j1, j2, t0, t1)
        else:
            keep_heard(heard[i1:i2])
    out.sort(key=lambda w: w['startMs'])
    for x, y in zip(out, out[1:]):
        if x['endMs'] > y['startMs']:
            x['endMs'] = max(x['startMs'] + 40, y['startMs'])
    stats['coverage'] = round(stats['matched'] / max(1, len(b)), 2)
    return out, stats
