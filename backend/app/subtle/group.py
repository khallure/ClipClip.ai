"""Transcript cleanup, word grouping into pages, beat snapping, and SRT/VTT export."""
from __future__ import annotations

import bisect
from itertools import combinations
import math
import re

from .cuts import clamp_pages, split_pages
from .emphasis import (
    MODES,
    SENTENCE_END,
    WEAK_END,
    apply_manual_overrides,
    bare,
    finalise,
    lexicon,
    rule_picks,
    split_words,
)
from .hinglish import romanize_text
from .lyrics import align_lyrics, read_lyrics_text
from .spoken_numbers import normalise_numbers

CLAUSE_END = re.compile(r"[,;:–—]['\")\]]*$")
FILLERS = {'um', 'uh', 'umm', 'uhh', 'erm', 'hmm', 'mm'}
ES_FILLERS = {'eh', 'ehh', 'em', 'emm', 'ehm', 'mmm'}
ID_FILLERS = {'eh', 'emm', 'anu', 'ee', 'hmm'}


def extract_interval_words(
    segments: list[dict],
    start_sec: float,
    end_sec: float,
    *,
    corrections: dict[int, str] | None = None,
    hinglish: bool = False,
) -> list[dict]:
    """Extract and rebase ClipClip.ai transcript word timestamps to 0..duration_ms."""
    out: list[dict] = []
    global_id = 0
    edited = corrections or {}
    for seg in segments or []:
        raw_words = seg.get('words') or []
        for w in raw_words:
            a = float(w.get('start', 0.0))
            b = float(w.get('end', a + 0.05))
            raw_text = str(w.get('word', w.get('text', '')))
            text = edited.get(global_id, edited.get(str(global_id), raw_text)).strip()
            global_id += 1
            if not text:
                continue
            if hinglish:
                text = romanize_text(text)
            if b > start_sec and a < end_sec:
                s_ms = int(round(max(0.0, a - start_sec) * 1000.0))
                e_ms = int(round(max(s_ms + 40.0, min(end_sec - start_sec, b - start_sec) * 1000.0)))
                out.append({'text': text, 'startMs': s_ms, 'endMs': e_ms, 'prob': None})
    return out


def clean_words(
    words: list[dict],
    replacements: dict[str, str] | None = None,
    strip_fillers: bool = False,
    min_ms: int = 80,
    close_gap_ms: int = 30,
    numbers: str = 'auto',
    language: str | None = None,
) -> list[dict]:
    out: list[dict] = []
    lang = (language or '').lower()
    if lang.startswith('es'):
        fillers = FILLERS | ES_FILLERS
    elif lang.startswith('id'):
        fillers = FILLERS | ID_FILLERS
    else:
        fillers = FILLERS

    clean_repl = {bare(k): str(v).strip() for k, v in (replacements or {}).items() if bare(k) and str(v).strip()}
    for w in words:
        text = (w.get('text') or '').strip()
        if not text:
            continue
        if strip_fillers and bare(text) in fillers:
            continue
        if clean_repl:
            key = bare(text)
            if key in clean_repl:
                lead = re.match(r'^\W*', text).group(0)
                trail = re.search(r'\W*$', text).group(0)
                text = lead + clean_repl[key] + trail
        item = {
            'text': text,
            'startMs': int(round(w['startMs'])),
            'endMs': int(round(w['endMs'])),
            'prob': w.get('prob'),
        }
        if w.get('lyricLine') is not None:
            item['lyricLine'] = w['lyricLine']
        out.append(item)

    out.sort(key=lambda w: w['startMs'])
    joined: list[dict] = []
    for w in out:
        if joined and (re.match(r'^[,.]\d', w['text']) or re.match(r'^[^\w$₹€£#@(\[\"\'“‘]+$', w['text'], flags=re.UNICODE)):
            joined[-1]['text'] += w['text']
            joined[-1]['endMs'] = max(joined[-1]['endMs'], w['endMs'])
        else:
            joined.append(w)
    out = joined
    if numbers != 'off':
        out = normalise_numbers(out, language, numbers)

    for i, w in enumerate(out):
        if i and w['startMs'] < out[i - 1]['startMs']:
            w['startMs'] = out[i - 1]['startMs']
        if w['endMs'] <= w['startMs']:
            w['endMs'] = w['startMs'] + min_ms
    for i, w in enumerate(out):
        nxt = out[i + 1]['startMs'] if i + 1 < len(out) else None
        if w['endMs'] - w['startMs'] < min_ms:
            w['endMs'] = w['startMs'] + min_ms
        if nxt is not None:
            if w['endMs'] > nxt:
                w['endMs'] = max(w['startMs'] + 40, nxt)
                if w['endMs'] > nxt:
                    out[i + 1]['startMs'] = w['endMs']
                    out[i + 1]['endMs'] = max(out[i + 1]['endMs'], out[i + 1]['startMs'] + 40)
            elif nxt - w['endMs'] < close_gap_ms:
                w['endMs'] = nxt
    return out


def split_lines(words: list[dict], max_chars: int, max_lines: int, weak_end=WEAK_END) -> list[int]:
    texts = [w['text'] for w in words]
    n = len(words)
    if max_lines < 2 or n < 2 or len(' '.join(texts)) <= max_chars:
        return [0] * n

    def score(cuts: tuple[int, ...]) -> int:
        bounds = [0, *cuts, n]
        lens = [len(' '.join(texts[a:b])) for a, b in zip(bounds, bounds[1:])]
        sc = (max(lens) - min(lens)) + sum(1000 for v in lens if v > max_chars)
        for k in cuts:
            if CLAUSE_END.search(texts[k - 1]) or SENTENCE_END.search(texts[k - 1]):
                sc -= 3
            if bare(texts[k - 1]) in weak_end:
                sc += 4
        return sc

    best: tuple[int, ...] = ()
    for lines in range(2, min(max_lines, n) + 1):
        cand = min(combinations(range(1, n), lines - 1), key=score)
        best = cand
        if score(cand) < 500:
            break
    out, line = [], 0
    for k in range(n):
        if line < len(best) and k == best[line]:
            line += 1
        out.append(line)
    return out


def snap_to_beats(pages: list[dict], beats: dict, mode: str, hold: bool, frame: float) -> list[dict]:
    grid = sorted(beats.get('downbeats' if mode == 'bar' else 'times') or [])
    every = sorted(beats.get('times') or [])
    if len(grid) < 2 or len(every) < 2:
        return pages
    beat = (every[-1] - every[0]) / (len(every) - 1)
    tol = 0.3 * beat

    def at_or_before(t: int, g: list[int]):
        k = bisect.bisect_right(g, t + tol) - 1
        return g[k] if k >= 0 else None

    def next_beat(t: float):
        k = bisect.bisect_left(every, t)
        return every[k] if k < len(every) else None

    prev_start = None
    for p in pages:
        first = p['words'][0]['startMs']
        b = at_or_before(first, grid)
        if b is None or first - b > (2.2 * beat if mode == 'bar' else 1.2 * beat):
            b = at_or_before(first, every)
        if b is None or first - b > 1.2 * beat:
            b = first
        if prev_start is not None and b <= prev_start:
            b = first
        p['startMs'] = int(round(b / frame) * frame + 1e-6) if abs(round(b / frame) * frame - b) < 1 else int(b // frame * frame + 1e-6)
        prev_start = p['startMs']
    for i, p in enumerate(pages):
        nxt = pages[i + 1]['startMs'] if i + 1 < len(pages) else None
        last = p['words'][-1]['endMs']
        if nxt is not None and (hold or nxt - last < 2 * beat):
            end = nxt if nxt - last < 8 * beat else (next_beat(last + beat) or last + 2 * beat)
        else:
            end = next_beat(last + 0.5 * beat) or last + beat
            if nxt is not None:
                end = min(end, nxt)
        if end != nxt:
            end = int(end // frame * frame + 1e-6)
        p['endMs'] = max(int(end), p['startMs'] + max(1, int(frame)))
    return pages


def group_pages(
    words: list[dict],
    page_cfg: dict,
    fps: float,
    wide: bool,
    beats: dict | None = None,
    cuts: list[int] | None = None,
    language: str | None = None,
) -> list[dict]:
    weak_end = lexicon(language).weak_end
    max_words = max(1, int(page_cfg.get('maxWords', 3)))
    max_lines = max(1, int(page_cfg.get('maxLines', 1)))
    per_line = int(page_cfg.get('maxCharsPerLineWide' if wide else 'maxCharsPerLine', 30 if wide else 18))
    max_chars = per_line if page_cfg.get('unit') == 'line' else per_line * max_lines
    gap_break = int(page_cfg.get('gapBreakMs', 350))
    frame = 1000.0 / max(1.0, fps)
    min_ms = int(page_cfg.get('minFrames', 10) * 1000 / 30)
    max_ms = int(page_cfg.get('maxFrames', 90) * 1000 / 30)

    def text_len(ws: list[dict]) -> int:
        return len(' '.join(x['text'] for x in ws))

    phrases: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            prev = cur[-1]
            if SENTENCE_END.search(prev['text']) or CLAUSE_END.search(prev['text']) or w['startMs'] - prev['endMs'] > gap_break:
                phrases.append(cur)
                cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)

    merged: list[list[dict]] = []
    for ph in phrases:
        if (merged and len(merged[-1]) == 1 and not SENTENCE_END.search(merged[-1][-1]['text'])
                and ph[0]['startMs'] - merged[-1][-1]['endMs'] <= gap_break
                and len(ph) + 1 <= max_words and text_len(merged[-1] + ph) <= max_chars):
            merged[-1] = merged[-1] + ph
        else:
            merged.append(ph)
    phrases = merged

    def fits(c: list[dict]) -> bool:
        return len(c) == 1 or (len(c) <= max_words and text_len(c) <= max_chars and c[-1]['endMs'] - c[0]['startMs'] <= max_ms)

    pages: list[list[dict]] = []
    for ph in phrases:
        n = max(1, math.ceil(len(ph) / max_words))
        base, extra = divmod(len(ph), n)
        chunks, at = [], 0
        for k in range(n):
            sz = base + (1 if k < extra else 0)
            chunks.append(ph[at:at + sz])
            at += sz
        if not all(fits(c) for c in chunks):
            chunks, cur = [], []
            for w in ph:
                if cur and not fits(cur + [w]):
                    chunks.append(cur)
                    cur = []
                cur.append(w)
            if cur:
                chunks.append(cur)
            if len(chunks) > 1 and len(chunks[-1]) == 1 and len(chunks[-2]) > 2 and fits([chunks[-2][-1]] + chunks[-1]):
                chunks[-1].insert(0, chunks[-2].pop())
        for k in range(len(chunks) - 1):
            c, nx = chunks[k], chunks[k + 1]
            if len(c) > 1 and bare(c[-1]['text']) in weak_end and bare(c[-2]['text']) not in weak_end and fits([c[-1]] + nx):
                nx.insert(0, c.pop())
        pages.extend(c for c in chunks if c)

    if cuts and page_cfg.get('unit') != 'line':
        pages = split_pages(pages, cuts, min_ms, max_words, max_chars)

    line_mode = page_cfg.get('unit') == 'line'
    page_lines = None
    if line_mode:
        if any('lyricLine' in w for w in words):
            lines, cur = [], []
            for w in words:
                if cur and w.get('lyricLine') != cur[-1].get('lyricLine'):
                    lines.append(cur)
                    cur = []
                cur.append(w)
            if cur:
                lines.append(cur)
            split, piece_count = [], []
            for ln in lines:
                n = max(1, math.ceil(max(len(ln) / max_words, text_len(ln) / per_line)))
                base, extra = divmod(len(ln), n)
                at = 0
                for k in range(n):
                    sz = base + (1 if k < extra else 0)
                    if sz:
                        split.append(ln[at:at + sz])
                        piece_count.append(n - k)
                    at += sz
            lines = split
        else:
            lines = pages
            piece_count = [1] * len(lines)
        per_page = max(1, int(page_cfg.get('linesPerPage', 1)))
        block_gap = int(page_cfg.get('blockGapMs', 2500))
        pages, page_lines = [], []
        for ln, rest in zip(lines, piece_count):
            room = pages and len(page_lines[-1]) + min(rest, per_page) <= per_page
            if room and ln[0]['startMs'] - pages[-1][-1]['endMs'] <= block_gap:
                page_lines[-1].append(len(pages[-1]))
                pages[-1] = pages[-1] + ln
            else:
                pages.append(list(ln))
                page_lines.append([0])

    out = []
    for i, p in enumerate(pages):
        start = p[0]['startMs']
        nxt = pages[i + 1][0]['startMs'] if i + 1 < len(pages) else None
        end = p[-1]['endMs'] + 400
        if nxt is not None:
            end = min(end, nxt)
        if end - start < min_ms:
            end = min(start + min_ms, nxt) if nxt is not None else start + min_ms
        end = max(end, p[-1]['endMs'] if nxt is None else min(p[-1]['endMs'], nxt))
        if page_lines is not None:
            starts = page_lines[i]
            lines = [sum(1 for st in starts if st <= k) - 1 for k in range(len(p))]
        else:
            lines = split_lines(p, per_line, max_lines, weak_end)
        out.append({
            'startMs': int(round(start / frame) * frame),
            'endMs': int(round(end / frame) * frame),
            'words': [
                {'text': w['text'], 'startMs': w['startMs'], 'endMs': w['endMs'], 'line': ln, 'emphasis': False}
                for w, ln in zip(p, lines)
            ],
        })
    if beats and page_cfg.get('snap') in ('beat', 'bar'):
        snap_to_beats(out, beats, page_cfg['snap'], bool(page_cfg.get('hold')), frame)
    if cuts:
        clamp_pages(out, cuts, fps)
    return out


def apply_page_edits(pages: list[dict], page_edits: dict[str, str] | None) -> None:
    if not page_edits:
        return
    for raw_idx, new_text in page_edits.items():
        if not str(raw_idx).isdigit():
            continue
        pi = int(raw_idx)
        if not (0 <= pi < len(pages)):
            continue
        toks = str(new_text or '').strip().split()
        if not toks:
            continue
        old = pages[pi]['words']
        t0, t1 = old[0]['startMs'], old[-1]['endMs']
        n = len(toks)

        def bare_clean(s: str) -> str:
            return re.sub(r"[^\w']+", '', s, flags=re.UNICODE).lower()

        new_words = []
        first_line_count = sum(1 for w in old if w.get('line', 0) == 0)
        has_two_lines = any(w.get('line', 0) > 0 for w in old)
        for i, tok in enumerate(toks):
            if n == len(old):
                s, e, ln = old[i]['startMs'], old[i]['endMs'], old[i].get('line', 0)
                emph, reason = old[i].get('emphasis', False), old[i].get('reason')
            else:
                s = int(round(t0 + ((t1 - t0) * i) / n))
                e = int(round(t0 + ((t1 - t0) * (i + 1)) / n))
                ln = 0 if (not has_two_lines or i < max(1, round(n * first_line_count / max(1, len(old))))) else 1
                m = next((w for w in old if w.get('emphasis') and bare_clean(w['text']) == bare_clean(tok)), None)
                emph, reason = (True, m.get('reason')) if m else (False, None)
            item = {'text': tok, 'startMs': s, 'endMs': max(s + 40, e), 'line': ln, 'emphasis': bool(emph)}
            if reason and emph:
                item['reason'] = reason
            new_words.append(item)
        pages[pi]['words'] = new_words


def build_captions_document(
    words: list[dict],
    *,
    width: int,
    height: int,
    fps: float,
    duration_ms: int,
    style: dict,
    language: str | None = None,
    emphasis: str = 'auto',
    emphasis_words: list[str] | None = None,
    numbers: str = 'auto',
    clean_fillers: bool = False,
    replacements: dict[str, str] | None = None,
    max_words: int | None = None,
    lyrics_text: str = '',
    beats: dict | None = None,
    cuts: list[int] | None = None,
    manual_picks: list[dict] | None = None,
    manual_drops: list[dict] | None = None,
    clear_auto_emphasis: bool = False,
    page_edits: dict[str, str] | None = None,
) -> tuple[dict, dict | None]:
    lyrics_stats = None
    work_words = list(words)
    if lyrics_text and lyrics_text.strip() and work_words:
        lyr = read_lyrics_text(lyrics_text)
        if lyr:
            work_words, lyrics_stats = align_lyrics(work_words, lyr)

    cleaned = clean_words(
        work_words,
        replacements=replacements,
        strip_fillers=clean_fillers,
        numbers=numbers,
        language=language,
    )
    page_cfg = dict(style.get('page') or {})
    if max_words and max_words >= 1:
        page_cfg['maxWords'] = int(max_words)
    wide = width > height
    pages = group_pages(cleaned, page_cfg, fps, wide, beats=beats, cuts=cuts, language=language) if cleaned else []
    apply_page_edits(pages, page_edits)

    mode = emphasis if emphasis in MODES else 'auto'
    forced = split_words(emphasis_words or [])
    if pages:
        rule_picks(pages, mode, forced, language)
        apply_manual_overrides(
            pages,
            clear_auto=clear_auto_emphasis,
            picks=manual_picks,
            drops=manual_drops,
        )

    out: dict = {
        'version': 1,
        'video': {'width': int(width), 'height': int(height), 'fps': float(fps), 'durationMs': int(duration_ms)},
        'style': style.get('name', 'glide'),
        'language': language,
        'pages': pages,
    }
    if beats and beats.get('times'):
        out['beats'] = {'bpm': beats.get('bpm'), 'times': beats['times'], 'downbeats': beats.get('downbeats', [])}
    if cuts:
        out['cuts'] = cuts
    finalise(out, style)
    if cuts:
        clamp_pages(pages, cuts, fps)
    return out, lyrics_stats


def to_srt(pages: list[dict]) -> str:
    def ts(ms: int) -> str:
        ms = max(0, int(round(ms)))
        h, r = divmod(ms, 3600000)
        m, r = divmod(r, 60000)
        s, rem = divmod(r, 1000)
        return f'{h:02d}:{m:02d}:{s:02d},{rem:03d}'

    rows = []
    for i, p in enumerate(pages, 1):
        lines: dict[int, list[str]] = {}
        for w in p['words']:
            lines.setdefault(w.get('line', 0), []).append(w['text'])
        text = '\n'.join(' '.join(v) for _, v in sorted(lines.items()))
        rows.append(f'{i}\n{ts(p["startMs"])} --> {ts(p["endMs"])}\n{text}\n')
    return '\n'.join(rows)


def to_vtt(pages: list[dict]) -> str:
    def ts(ms: int) -> str:
        ms = max(0, int(round(ms)))
        h, r = divmod(ms, 3600000)
        m, r = divmod(r, 60000)
        s, rem = divmod(r, 1000)
        return f'{h:02d}:{m:02d}:{s:02d}.{rem:03d}'

    rows = []
    for i, p in enumerate(pages, 1):
        lines: dict[int, list[str]] = {}
        for w in p['words']:
            lines.setdefault(w.get('line', 0), []).append(w['text'])
        text = '\n'.join(' '.join(v) for _, v in sorted(lines.items()))
        rows.append(f'{i}\n{ts(p["startMs"])} --> {ts(p["endMs"])}\n{text}\n')
    return 'WEBVTT\n\n' + '\n'.join(rows)
