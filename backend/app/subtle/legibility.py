"""Measure WCAG contrast and texture busyness per page, plus auto-palette from footage."""
from __future__ import annotations

import colorsys
import json
import math
from pathlib import Path
import re
import subprocess

import numpy as np

from .. import config, media
from ..process_runtime import external_popen

CLEAN_TARGET = 4.5
BUSY_EXTRA = 6.0
SHIELD = 0.65


def parse_color(c) -> tuple[float, float, float] | None:
    if not isinstance(c, str):
        return None
    c = c.strip()
    m = re.fullmatch(r'#([0-9a-fA-F]{3,8})', c)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = ''.join(ch * 2 for ch in h)
        if len(h) in (6, 8):
            return tuple(float(int(h[i:i + 2], 16)) for i in (0, 2, 4))
        return None
    m = re.fullmatch(r'rgba?\(([^)]*)\)', c)
    if m:
        p = [x.strip() for x in m.group(1).split(',')]
        try:
            return (float(p[0]), float(p[1]), float(p[2]))
        except (ValueError, IndexError):
            return None
    return None


def to_hex(rgb) -> str:
    return '#%02X%02X%02X' % tuple(int(round(min(255, max(0, v)))) for v in rgb)


def rel_lum(rgb) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def contrast_ratio(l1: float, l2: float) -> float:
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: tuple[float, float, float], b: tuple[float, float, float], k: float) -> tuple[float, float, float]:
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def target_contrast(busy: float) -> float:
    return CLEAN_TARGET + BUSY_EXTRA * min(1.0, max(0.0, busy))


def boost_for(contrast: float, busy: float) -> float:
    tgt = target_contrast(busy)
    deficit = (tgt - contrast) / (tgt - 1.2)
    return float(min(1.0, max(0.0, deficit)) ** 0.85)


def resolve_auto(obj, palette: dict | None):
    if isinstance(obj, dict):
        if isinstance(obj.get('auto'), str):
            return (palette or {}).get(obj['auto']) or obj.get('fallback')
        return {k: resolve_auto(v, palette) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_auto(v, palette) for v in obj]
    return obj


def has_auto(obj) -> bool:
    if isinstance(obj, dict):
        return isinstance(obj.get('auto'), str) or any(has_auto(v) for k, v in obj.items() if k != 'variants')
    if isinstance(obj, list):
        return any(has_auto(v) for v in obj)
    return False


def style_mode(style: dict) -> str:
    lg = style.get('legibility') or {}
    if lg.get('mode') in ('halo', 'scrim', 'stroke', 'plate', 'none'):
        return lg['mode']
    return 'none' if style.get('container') else 'halo'


def style_inks(style: dict):
    lg = style.get('legibility') or {}
    a = style.get('anim') or {}
    em = style.get('emphasis') or {}
    main = parse_color(lg.get('ink')) or parse_color(style.get('color'))
    if main is None and a.get('gradTop') and a.get('gradBottom'):
        main = mix(parse_color(a['gradTop']) or (255.0, 255.0, 255.0), parse_color(a['gradBottom']) or (128.0, 128.0, 128.0), 0.5)
    main = main or (255.0, 255.0, 255.0)
    accent = parse_color(em.get('color')) or parse_color(style.get('activeColor')) or parse_color(em.get('gradTop'))
    if lg.get('accent') is False:
        accent = None
    dim = float(a['dim']) if isinstance(a.get('dim'), (int, float)) else 1.0
    return main, accent, dim


def grab_rgb(video: Path, t: float, w: int, h: int, *, crop_box: tuple[float, float, float, float] | None = None) -> np.ndarray | None:
    ffmpeg = media.tool('ffmpeg')
    vf_parts = []
    if crop_box is not None:
        cw, ch, cx, cy = crop_box
        vf_parts.append(f'crop={cw}:{ch}:{cx}:{cy}')
    vf_parts.append(f'scale={w}:{h}')
    cmd = [
        ffmpeg, '-hide_banner', '-v', 'error', '-nostdin',
        '-ss', f'{max(0.0, t):.3f}', *media.input_args(video),
        '-frames:v', '1', '-vf', ','.join(vf_parts),
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-',
    ]
    proc = external_popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=config.child_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    raw, _ = proc.communicate(timeout=45)
    if proc.returncode != 0 or len(raw) < w * h * 3:
        return None
    return np.frombuffer(raw[: w * h * 3], dtype=np.uint8).reshape(h, w, 3)


def region_of(page: dict, style: dict, W: int, H: int) -> tuple[float, float, float, float]:
    wide = W > H
    pos = style.get('position') or {}
    a = style.get('anim') or {}
    em = style.get('emphasis') or {}
    size = float(style.get('size', 4)) * (float(style.get('wideScale', 1.35)) if wide else 1.0)
    fs = size / 100.0 * H
    lines = 1 + max([int(w.get('line', 0) or 0) for w in page['words']] + [0])
    lines = min(lines, 4)
    has_em = any(w.get('emphasis') for w in page['words'])
    big = max(float(a.get('scriptScale', 1)), float(a.get('keyScale', 1)), float(em.get('scale', 1)) if has_em else 1.0, 1.0)
    big = min(big, 3.0) if has_em else 1.0
    lh = fs * float(style.get('lineHeight', 1.2))
    height = lh * lines * (1 + 0.5 * (big - 1)) + 0.4 * fs
    chars = sum(len(w['text']) + 1 for w in page['words'])
    mono = 'mono' in json.dumps(style.get('font', {})).lower() or 'courier' in json.dumps(style.get('font', {})).lower()
    adv = (0.6 if mono else 0.56) * fs
    avail = W * (1 - 2 * float(pos.get('marginX', 8)) / 100.0)
    width = min(avail, max(chars / lines, 3) * adv * (1 + 0.35 * (big - 1)) + 0.6 * fs)
    cx = W * float(pos.get('x', 50)) / 100.0
    cy = H * float(page.get('y') if page.get('y') is not None else pos.get('y', 60)) / 100.0
    x0, x1 = max(0.0, cx - width / 2), min(float(W), cx + width / 2)
    y0, y1 = max(0.0, cy - height / 2), min(float(H), cy + height / 2)
    return x0 / W, y0 / H, x1 / W, y1 / H


def busyness(lum: np.ndarray) -> float:
    if lum.shape[0] < 3 or lum.shape[1] < 3:
        return 0.0
    gx = np.abs(np.diff(lum, axis=1))[:-1, :]
    gy = np.abs(np.diff(lum, axis=0))[:, :-1]
    grad = float(np.hypot(gx, gy).mean())
    spread = float(lum.std())
    return float(min(1.0, 0.6 * min(1.0, grad / 0.05) + 0.4 * min(1.0, spread / 0.25)))


def _default_sampler(video: Path, W: int, H: int, offset_sec: float = 0.0, crop_box=None):
    cache: dict[tuple[float, int, int], np.ndarray | None] = {}

    def get(t: float, sw: int, sh: int) -> np.ndarray | None:
        key = (round(t, 2), sw, sh)
        if key not in cache:
            cache[key] = grab_rgb(video, offset_sec + t, sw, sh, crop_box=crop_box)
        return cache[key]

    return get


def measure_page(sampler, page: dict, style: dict, W: int, H: int, inks) -> dict | None:
    main, accent, dim = inks
    x0, y0, x1, y1 = region_of(page, style, W, H)
    sw = 360
    sh = max(2, int(round(sw * H / W / 2) * 2))
    ps, pe = page['startMs'] / 1000.0, page['endMs'] / 1000.0
    pix_list, busy_list = [], []
    for k in (0.2, 0.5, 0.8):
        fr = sampler(ps + k * (pe - ps), sw, sh)
        if fr is None:
            continue
        r = fr[int(y0 * sh):max(int(y0 * sh) + 2, int(y1 * sh)), int(x0 * sw):max(int(x0 * sw) + 2, int(x1 * sw))]
        busy_list.append(busyness(rel_lum(r)))
        pix_list.append(r.reshape(-1, 3))
    if not pix_list:
        return None
    pix = np.concatenate(pix_list).astype(np.float64)
    if style_mode(style) == 'plate':
        fill = (style.get('container') or {}).get('fill', '')
        m = re.search(r'rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?', str(fill))
        if m:
            al = float(m.group(4)) if m.group(4) else 1.0
            pix = pix * (1 - al) + np.array([float(m.group(i)) for i in (1, 2, 3)]) * al
        b_scale = 0.35
    else:
        b_scale = 1.0
    lum = rel_lum(pix)
    mean_rgb = pix.mean(axis=0)
    has_em = any(w.get('emphasis') for w in page['words'])
    c_all = []
    for ink, op in [(main, dim)] + ([(accent, 1.0)] if (accent is not None and has_em) else []):
        l_ink = float(rel_lum(np.array(ink)))
        light_ink = l_ink > 0.3
        bg_l = float(np.percentile(lum, 75 if light_ink else 25))
        if op < 1.0:
            l_ink = float(rel_lum(np.array(mix(tuple(mean_rgb), ink, op))))
        c_all.append(contrast_ratio(l_ink, bg_l))
    contrast = min(c_all)
    b = float(np.mean(busy_list)) * b_scale
    return {'contrast': round(contrast, 2), 'busy': round(b, 3), 'bg': to_hex(mean_rgb), 'boost': round(boost_for(contrast, b), 3)}


def kmeans(pix: np.ndarray, k: int = 6, iters: int = 14, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = len(pix)
    k = max(1, min(k, n))
    c = [pix[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min(((pix[:, None, :] - np.array(c)[None]) ** 2).sum(-1), axis=1)
        tot = d2.sum()
        c.append(pix[rng.choice(n, p=d2 / tot)] if tot > 0 else pix[rng.integers(n)])
    c_arr = np.array(c, dtype=np.float64)
    for _ in range(iters):
        lab = np.argmin(((pix[:, None, :] - c_arr[None]) ** 2).sum(-1), axis=1)
        for j in range(k):
            m = lab == j
            if m.any():
                c_arr[j] = pix[m].mean(axis=0)
    lab = np.argmin(((pix[:, None, :] - c_arr[None]) ** 2).sum(-1), axis=1)
    w = np.bincount(lab, minlength=k) / n
    return c_arr, w


def hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


def pick_palette(centres: np.ndarray, weights: np.ndarray, bg_rgb: np.ndarray, bg_lum: float, shield: float = SHIELD) -> dict:
    hsv = np.array([colorsys.rgb_to_hsv(*(np.clip(c, 0, 255) / 255.0)) for c in centres])
    sat_w = weights * hsv[:, 1] * (hsv[:, 2] > 0.2)
    if sat_w.sum() < 0.02:
        dom, grey = 0.13, True
    else:
        ang = hsv[:, 0] * 2 * math.pi
        dom = (math.atan2((sat_w * np.sin(ang)).sum(), (sat_w * np.cos(ang)).sum()) / (2 * math.pi)) % 1.0
        grey = False
    bg_eff = bg_lum * (1.0 - shield)
    bg_hsv = colorsys.rgb_to_hsv(*(np.clip(bg_rgb, 0, 255) / 255.0))
    best, best_s = (dom, 0.4), -1.0
    best2, best2_s = ((dom + 0.5) % 1.0, 0.4), -1.0
    for dh, harm in ((0.0, 1.0), (0.06, 0.88), (-0.06, 0.88), (0.42, 0.7), (-0.42, 0.7), (0.5, 0.62)):
        for s in (0.2, 0.4, 0.62, 0.82):
            h = (dom + dh) % 1.0
            rgb = np.array(colorsys.hsv_to_rgb(h, s, 1.0)) * 255.0
            c = contrast_ratio(float(rel_lum(rgb)), bg_eff)
            vib = 1.0 - abs(s - 0.5) / 0.5
            apart = min(1.0, hue_dist(h, bg_hsv[0]) / 0.1 + (1.0 if bg_hsv[1] < 0.15 else 0.0))
            score = 0.4 * min(1.0, c / 7.0) + 0.35 * harm + 0.15 * vib + 0.10 * apart + (0.05 * (s < 0.3) if grey else 0.0)
            if score > best_s:
                best, best_s = (h, s), score
            if abs(dh) >= 0.4:
                s2 = 0.35 * min(1.0, c / 7.0) + 0.35 * harm + 0.3 * vib
                if s2 > best2_s:
                    best2, best2_s = (h, s), s2
    h, s = best
    rgb = np.array(colorsys.hsv_to_rgb(h, s, 1.0)) * 255.0
    for _ in range(12):
        if contrast_ratio(float(rel_lum(rgb)), bg_eff) >= CLEAN_TARGET:
            break
        s = max(0.0, s - 0.07)
        rgb = np.array(colorsys.hsv_to_rgb(h, s, 1.0)) * 255.0
    h2, s2_ = best2
    rgb2 = np.array(colorsys.hsv_to_rgb(h2, s2_, 1.0)) * 255.0
    for _ in range(12):
        if contrast_ratio(float(rel_lum(rgb2)), bg_eff) >= CLEAN_TARGET:
            break
        s2_ = max(0.0, s2_ - 0.07)
        rgb2 = np.array(colorsys.hsv_to_rgb(h2, s2_, 1.0)) * 255.0
    ink = np.array(mix((255.0, 255.0, 255.0), tuple(rgb), 0.07))
    shade = np.array(colorsys.hsv_to_rgb(dom, 0.55 if not grey else 0.0, 0.09)) * 255.0
    return {
        'accent': to_hex(rgb),
        'accent2': to_hex(rgb2),
        'ink': to_hex(ink),
        'shade': to_hex(shade),
        'bg': to_hex(bg_rgb),
        'contrast': round(contrast_ratio(float(rel_lum(rgb)), bg_eff), 2),
    }


def sample_palette(sampler, cap: dict, n: int = 8) -> np.ndarray | None:
    W, H = cap['video']['width'], cap['video']['height']
    dur = cap['video']['durationMs'] / 1000.0
    sw = 64
    sh = max(2, int(round(sw * H / W)))
    pix = []
    for i in range(n):
        fr = sampler(dur * (i + 0.5) / n, sw, sh)
        if fr is not None:
            pix.append(fr.reshape(-1, 3).astype(np.float64))
    return np.concatenate(pix) if pix else None


def sample_frame_mean(sampler, cap: dict, zone) -> np.ndarray:
    W, H = cap['video']['width'], cap['video']['height']
    sw = 160
    sh = max(2, int(round(sw * H / W)))
    dur = cap['video']['durationMs'] / 1000.0
    acc = []
    for k in (0.2, 0.5, 0.8):
        fr = sampler(dur * k, sw, sh)
        if fr is not None:
            acc.append(fr[int(zone[1] * sh):max(int(zone[1] * sh) + 2, int(zone[3] * sh)),
                          int(zone[0] * sw):max(int(zone[0] * sw) + 2, int(zone[2] * sw))].reshape(-1, 3))
    return np.concatenate(acc).mean(axis=0) if acc else np.array([0.0, 0.0, 0.0])


def analyse(
    cap: dict,
    style: dict,
    video: Path | None,
    *,
    palette_mode: str = 'auto',
    legibility_mode: str = 'auto',
    sampler=None,
    offset_sec: float = 0.0,
    crop_box=None,
) -> dict:
    W, H = cap['video']['width'], cap['video']['height']
    mode = style_mode(style)
    cap.pop('legibility', None)
    cap.pop('legibilitySummary', None)
    cap.pop('palette', None)
    pages = cap['pages']
    if sampler is None and video is not None:
        sampler = _default_sampler(video, W, H, offset_sec=offset_sec, crop_box=crop_box)
    if sampler is None:
        return cap

    rows = []
    if legibility_mode != 'off' and mode != 'none' and pages:
        inks = style_inks(resolve_auto(style, None))
        for i, p in enumerate(pages):
            r = measure_page(sampler, p, style, W, H, inks)
            rows.append({'page': i, **(r or {'contrast': 21.0, 'busy': 0.0, 'bg': '#000000', 'boost': 0.0})})
    uses_palette = palette_mode != 'off' and has_auto(style)
    if uses_palette:
        pix = sample_palette(sampler, cap)
        if pix is not None:
            centres, weights = kmeans(pix)
            if rows:
                bgs = np.array([parse_color(r['bg']) for r in rows])
                lums = rel_lum(bgs)
                bg_lum = float(np.percentile(lums, 75))
                bg_rgb = bgs[int(np.argmin(abs(lums - bg_lum)))]
            else:
                zone = region_of({'words': [{'text': 'x' * 18, 'line': 0}]}, style, W, H)
                bg_rgb = sample_frame_mean(sampler, cap, zone)
                bg_lum = float(rel_lum(bg_rgb))
            cap['palette'] = pick_palette(centres, weights, bg_rgb, bg_lum)
            if rows:
                inks = style_inks(resolve_auto(style, cap['palette']))
                for i, p in enumerate(pages):
                    r = measure_page(sampler, p, style, W, H, inks)
                    if r:
                        rows[i] = {'page': i, **r}
    if rows:
        cap['legibility'] = rows
        cap['legibilitySummary'] = {
            'mode': mode,
            'pages': len(rows),
            'boosted': sum(1 for r in rows if r['boost'] >= 0.15),
            'meanBoost': round(float(np.mean([r['boost'] for r in rows])), 3),
            'maxBoost': round(max(r['boost'] for r in rows), 3),
            'minContrast': round(min(r['contrast'] for r in rows), 2),
            'meanBusy': round(float(np.mean([r['busy'] for r in rows])), 3),
        }
    elif legibility_mode != 'off':
        cap['legibilitySummary'] = {'mode': mode, 'pages': 0, 'boosted': 0, 'meanBoost': 0.0, 'maxBoost': 0.0}
    return cap
