"""Visual QA verification and OpenCV contact-sheet builder (zero Pillow dependency)."""
from __future__ import annotations

from pathlib import Path
import subprocess

import cv2
import numpy as np

from .. import config, media
from ..process_runtime import external_popen


def grab(path: Path, t: float, pix: str, w: int, h: int) -> np.ndarray | None:
    ch = {'rgb24': 3, 'rgba': 4, 'gray': 1}[pix]
    ffmpeg = media.tool('ffmpeg')
    cmd = [
        ffmpeg, '-hide_banner', '-v', 'error', '-nostdin',
        '-ss', f'{max(0.0, t):.3f}', *media.input_args(path),
        '-frames:v', '1', '-vf', f'scale={w}:{h}',
        '-f', 'rawvideo', '-pix_fmt', pix, '-',
    ]
    proc = external_popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=config.child_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    raw, _ = proc.communicate(timeout=45)
    if proc.returncode != 0 or len(raw) < w * h * ch:
        return None
    a = np.frombuffer(raw[: w * h * ch], dtype=np.uint8).reshape(h, w, ch)
    return a[:, :, 0] if ch == 1 else a


def lum(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def safe_box(w: int, h: int) -> tuple[float, float, float, float]:
    if h > w:
        return 0.05, 0.12, 0.95, 0.80
    return 0.04, 0.05, 0.96, 0.94


def big_visibility(b: dict, behind: Path, matte: Path, W: int, H: int, *, grab_fn=grab) -> float | None:
    t = (b['startMs'] + b['endMs']) / 2000.0
    ov = grab_fn(behind, t, 'rgba', W, H)
    m = grab_fn(matte, t, 'gray', W, H)
    if ov is None or m is None:
        return None
    al = ov[:, :, 3].astype(np.float32) / 255.0
    tot = float(al.sum())
    if tot < 50:
        return None
    return float((al * (1.0 - m.astype(np.float32) / 255.0)).sum() / tot)


def prune_hidden_bigs(cap: dict, behind: Path, matte: Path, min_visible: float = 0.55, *, grab_fn=grab) -> tuple[dict, list[dict]]:
    W, H = cap['video']['width'], cap['video']['height']
    kept, dropped = [], []
    for b in cap.get('big', []):
        vis = None if b.get('front') else big_visibility(b, behind, matte, W, H, grab_fn=grab_fn)
        if vis is None or vis >= min_visible:
            kept.append(b)
        else:
            dropped.append({'text': b['text'], 't': round((b['startMs'] + b['endMs']) / 2000.0, 2), 'visible': round(vis, 2)})
    updated = dict(cap)
    updated['big'] = kept
    return updated, dropped


def verify_export(
    *,
    video_path: Path,
    source_path: Path,
    cap: dict,
    style: dict,
    overlay_path: Path,
    preview_out: Path,
    behind_path: Path | None = None,
    matte_path: Path | None = None,
    min_visible: float = 0.55,
    grab_fn=grab,
) -> dict:
    W, H = cap['video']['width'], cap['video']['height']
    pages = cap.get('pages') or []
    warnings: list[str] = []
    frames: list[dict] = []
    L, T, R, B = safe_box(W, H)
    protected = bool(
        (style.get('stroke') or {}).get('width', 0) >= 0.3
        or style.get('box')
        or style.get('container')
        or (style.get('recipe') or {}).get('plate')
        or ((style.get('recipe') or {}).get('stroke') or {}).get('width', 0) >= 0.04
    )
    helped = bool(cap.get('legibility')) and ((style.get('legibility') or {}).get('mode', 'halo') != 'none')
    protected = protected or helped

    picks = sorted({0, len(pages) // 4, len(pages) // 2, (3 * len(pages)) // 4, len(pages) - 1}) if pages else []
    fps = float(cap['video'].get('fps') or 30)
    dur_sec = cap['video']['durationMs'] / 1000.0
    for pi in picks:
        p = pages[pi]
        last = p['words'][-1]
        ps, pe = p['startMs'] / 1000.0, p['endMs'] / 1000.0
        t = last['startMs'] / 1000.0 + 0.3 * (min(last['endMs'], p['endMs']) - last['startMs']) / 1000.0
        t = max(min(t, pe - 0.25), min(ps + 0.2, (ps + pe) / 2))
        t = min(max(0.0, t), max(0.0, dur_sec - 2.5 / max(1.0, fps)))
        ov = grab_fn(overlay_path, t, 'rgba', W, H)
        src = grab_fn(source_path, t, 'rgb24', W, H)
        text = ' '.join(w['text'] for w in p['words'])
        rec: dict = {'t': round(t, 2), 'page': pi, 'text': text}
        if ov is None or src is None:
            rec['error'] = 'gagal membaca frame'
            warnings.append(f"{rec['t']}s: gagal membaca frame verifikasi")
            frames.append(rec)
            continue
        alpha = ov[:, :, 3].astype(np.float32) / 255.0
        ys, xs = np.where(alpha > 0.5)
        if len(xs) < 20:
            rec['drawn'] = False
            warnings.append(f'{rec["t"]}s: tidak ada teks tergambar untuk "{text}"')
            frames.append(rec)
            continue
        if float((alpha > 0.5).mean()) > 0.85:
            ll = lum(ov[:, :, :3][alpha > 0.95]) if (alpha > 0.95).any() else lum(ov[:, :, :3])
            field = float(np.median(ll))
            lo, hi = float(np.percentile(ll, 0.3)), float(np.percentile(ll, 99.7))
            ink = hi if hi - field >= field - lo else lo
            ratio = (max(ink, field) + 0.05) / (min(ink, field) + 0.05)
            rec.update(drawn=True, fullFrame=True, contrast=round(ratio, 2))
            if ratio < 3.0:
                warnings.append(f'{rec["t"]}s: kontras rendah ({ratio:.1f}:1) antara teks dan warna latar')
            frames.append(rec)
            continue
        x0, x1, y0, y1 = float(xs.min()) / W, float(xs.max() + 1) / W, float(ys.min()) / H, float(ys.max() + 1) / H
        rec.update(drawn=True, box=[round(v, 3) for v in (x0, y0, x1, y1)])
        inside = x0 >= L - 0.005 and x1 <= R + 0.005 and y0 >= T - 0.005 and y1 <= B + 0.005
        rec['insideSafeArea'] = bool(inside)
        if not inside:
            warnings.append(f'{rec["t"]}s: posisi teks {rec["box"]} keluar area aman [{L}, {T}, {R}, {B}]')
        elif H > W and x1 > 0.87 and y1 > 0.45:
            rec['note'] = 'sisi kanan mendekati area tombol UI vertikal (TikTok/Reels)'
        solid = alpha > 0.95
        pad = max(4, int(0.004 * H))
        bx0, bx1 = max(0, int(xs.min()) - pad), min(W, int(xs.max()) + pad)
        by0, by1 = max(0, int(ys.min()) - pad), min(H, int(ys.max()) + pad)
        bg = lum(src[by0:by1, bx0:bx1])
        tl = lum(ov[:, :, :3][solid]) if solid.any() else np.array([1.0], dtype=np.float32)
        text_l = float(np.percentile(tl, 75))
        bg_l = float(bg.mean())
        ratio = (max(text_l, bg_l) + 0.05) / (min(text_l, bg_l) + 0.05)
        rec.update(contrast=round(ratio, 2), protected=protected)
        if ratio < 3.0 and not protected:
            warnings.append(f'{rec["t"]}s: kontras rendah ({ratio:.1f}:1); coba style berkontainer (glass/chip/block)')
        frames.append(rec)

    behind = None
    if behind_path and matte_path and cap.get('big'):
        hidden, vis_all = [], []
        bigs = [b for b in cap['big'] if not b.get('front')]
        step = max(1, len(bigs) // 14)
        for b in bigs[::step]:
            t = (b['startMs'] + b['endMs']) / 2000.0
            vis = big_visibility(b, behind_path, matte_path, W, H, grab_fn=grab_fn)
            if vis is None:
                continue
            vis_all.append(vis)
            if vis < min_visible:
                hidden.append({'text': b['text'], 't': round(t, 2), 'visible': round(vis, 2)})
        behind = {
            'checked': len(vis_all),
            'minVisible': round(min(vis_all), 2) if vis_all else None,
            'meanVisible': round(float(np.mean(vis_all)), 2) if vis_all else None,
            'hidden': hidden,
        }
        for hgt in hidden:
            warnings.append(f'{hgt["t"]}s: kata besar "{hgt["text"]}" hanya terlihat {int(hgt["visible"] * 100)}% di belakang subjek')

    # Contact sheet built cleanly via OpenCV + NumPy (no Pillow needed)
    times = [f['t'] for f in frames]
    if behind and cap.get('big'):
        bigs = cap['big']
        for k in (0, len(bigs) // 2, len(bigs) - 1):
            times.append(round((bigs[k]['startMs'] + bigs[k]['endMs']) / 2000.0, 2))
    times = sorted(set(times))[:8]
    if times:
        th = 480 if H >= W else 640
        tw = max(64, int(round(W * th / max(1, H))))
        sheet_w = tw * len(times) + 8 * max(0, len(times) - 1)
        sheet_h = th + 32
        sheet = np.full((sheet_h, sheet_w, 3), (18, 16, 16), dtype=np.uint8)
        for i, t in enumerate(times):
            fr = grab_fn(video_path, t, 'rgb24', tw, th)
            if fr is None:
                continue
            bgr = cv2.cvtColor(fr, cv2.COLOR_RGB2BGR).copy()
            cv2.rectangle(
                bgr,
                (int(round(L * tw)), int(round(T * th))),
                (int(round(R * tw)), int(round(B * th))),
                (235, 220, 120),  # cyan in BGR
                1,
            )
            x = i * (tw + 8)
            sheet[0:th, x:x + tw] = bgr
            cv2.putText(sheet, f'{t:.2f}s', (x + 6, th + 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (215, 215, 215), 1, cv2.LINE_AA)
        ok, enc = cv2.imencode('.jpg', sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if ok:
            preview_out.write_bytes(enc.tobytes())

    return {
        'ok': not warnings,
        'warnings': warnings,
        'frames': frames,
        'behind': behind,
        'safeArea': [L, T, R, B],
        'has_preview': preview_out.is_file(),
    }
