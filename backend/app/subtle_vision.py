"""Windows-native ONNX person segmentation, face/hand subject sampling, and Behind-the-Speaker mattes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading

import cv2
import numpy as np

from . import config, media, reframe_detection, subtle_models
from .process_runtime import external_popen
from .runtime_profile import checked_path

BANDS = 24
CACHE_VERSION = 1
EMA_MOTION_THRESHOLD = 0.012
CHECK_MOTION_THRESHOLD = 0.20

_session_lock = threading.Lock()
_sessions: dict[str, tuple[object, str]] = {}


def capabilities() -> dict:
    has_ort = importlib.util.find_spec('onnxruntime') is not None
    has_cv2 = importlib.util.find_spec('cv2') is not None
    bundled_ok = False
    modnet_ok = False
    try:
        subtle_models.verify_bundled_selfie()
        bundled_ok = True
    except Exception:
        pass
    try:
        subtle_models.verify_modnet()
        modnet_ok = True
    except Exception:
        pass
    face_ok = reframe_detection.capabilities().get('auto_face', False)
    return {
        'available': bool(has_ort and has_cv2 and bundled_ok),
        'bundled_selfie': bundled_ok,
        'modnet_ready': modnet_ok,
        'face_detector': face_ok,
    }


def _get_session(model_kind: str) -> tuple[object, str]:
    import onnxruntime as ort

    with _session_lock:
        if model_kind == 'modnet':
            path = subtle_models.verify_modnet()
        else:
            path = subtle_models.verify_bundled_selfie()
        key = str(path)
        if key not in _sessions:
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 2
            opts.intra_op_num_threads = max(2, min(4, os.cpu_count() or 4))
            sess = ort.InferenceSession(key, sess_options=opts, providers=['CPUExecutionProvider'])
            _sessions[key] = (sess, model_kind)
        return _sessions[key]


def _resolve_model_kind(quality: str) -> str:
    if quality == 'accurate':
        try:
            subtle_models.verify_modnet()
            return 'modnet'
        except Exception:
            return 'selfie'
    return 'selfie'


def infer_person_mask(bgr: np.ndarray, *, quality: str = 'balanced') -> np.ndarray:
    """Return float32 mask in [0, 1] matching bgr (H, W) shape."""
    kind = _resolve_model_kind(quality)
    sess, active_kind = _get_session(kind)
    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    inp_name = sess.get_inputs()[0].name

    if active_kind == 'modnet':
        # Scale shortest edge to 512, aligned to multiples of 32
        scale = 512.0 / max(1, min(h, w))
        rw = max(32, int(round(w * scale / 32.0)) * 32)
        rh = max(32, int(round(h * scale / 32.0)) * 32)
        resized = cv2.resize(rgb, (rw, rh), interpolation=cv2.INTER_AREA).astype(np.float32)
        tensor = ((resized / 255.0) - 0.5) / 0.5
        nchw = np.transpose(tensor, (2, 0, 1))[None, ...]
        alpha = sess.run(None, {inp_name: nchw})[0][0, 0]
    else:
        resized = cv2.resize(rgb, (256, 256), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        nchw = np.transpose(resized, (2, 0, 1))[None, ...]
        alpha = sess.run(None, {inp_name: nchw})[0][0, 0]

    alpha = np.clip(alpha.astype(np.float32), 0.0, 1.0)
    if alpha.shape != (h, w):
        alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_LINEAR)
    return alpha


def clean_matte(alpha: np.ndarray, *, min_blob_frac: float = 0.008, max_hole_frac: float = 0.015) -> np.ndarray:
    """Coarse connected-component cleanup matching Subtle vision-matte hole fill & speck removal."""
    h, w = alpha.shape[:2]
    scale = 4
    ch, cw = max(8, (h + scale - 1) // scale), max(8, (w + scale - 1) // scale)
    small = cv2.resize(alpha, (cw, ch), interpolation=cv2.INTER_AREA)
    fg = (small >= 0.5).astype(np.uint8)
    n_cells = ch * cw
    min_cells = max(1, int(min_blob_frac * n_cells))
    max_holes = max(1, int(max_hole_frac * n_cells))

    out = alpha.copy()
    num_fg, labels_fg, stats_fg, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    remove_mask = np.zeros((ch, cw), dtype=np.uint8)
    for idx in range(1, num_fg):
        if int(stats_fg[idx, cv2.CC_STAT_AREA]) < min_cells:
            remove_mask[labels_fg == idx] = 1
    if remove_mask.any():
        dilated = cv2.dilate(remove_mask, np.ones((3, 3), dtype=np.uint8), iterations=1)
        zero_full = cv2.resize(dilated, (w, h), interpolation=cv2.INTER_NEAREST) > 0
        out[zero_full] = 0.0

    bg = 1 - fg
    num_bg, labels_bg, stats_bg, _ = cv2.connectedComponentsWithStats(bg, connectivity=4)
    fill_mask = np.zeros((ch, cw), dtype=np.uint8)
    for idx in range(1, num_bg):
        x, y, bw, bh, area = stats_bg[idx]
        touches_border = x == 0 or y == 0 or (x + bw) >= cw or (y + bh) >= ch
        if not touches_border and int(area) <= max_holes:
            fill_mask[labels_bg == idx] = 1
    if fill_mask.any():
        one_full = cv2.resize(fill_mask, (w, h), interpolation=cv2.INTER_NEAREST) > 0
        out[one_full] = 1.0
    return out


def _detect_hands_heuristic(mask: np.ndarray, faces: list[list[float]]) -> list[list[float]]:
    """Estimate raised gesture/hand boxes outside detected face core without Apple Vision."""
    h, w = mask.shape[:2]
    fg = (mask >= 0.5).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    if num <= 2:
        return []
    # Find largest person body blob
    areas = [(int(stats[i, cv2.CC_STAT_AREA]), i) for i in range(1, num)]
    areas.sort(reverse=True)
    main_idx = areas[0][1]
    boxes: list[list[float]] = []
    min_hand_area = 0.004 * (h * w)
    max_hand_area = 0.08 * (h * w)
    for area, idx in areas[1:]:
        if idx == main_idx or not (min_hand_area <= area <= max_hand_area):
            continue
        bx, by, bw, bh = stats[idx, cv2.CC_STAT_LEFT], stats[idx, cv2.CC_STAT_TOP], stats[idx, cv2.CC_STAT_WIDTH], stats[idx, cv2.CC_STAT_HEIGHT]
        rx, ry, rw, rh = bx / w, by / h, bw / w, bh / h
        overlaps_face = any(
            max(0.0, min(rx + rw, fb[0] + fb[2]) - max(rx, fb[0])) * max(0.0, min(ry + rh, fb[1] + fb[3]) - max(ry, fb[1])) > 0.25 * rw * rh
            for fb in faces
        )
        if not overlaps_face:
            boxes.append([round(rx, 4), round(ry, 4), round(rw, 4), round(rh, 4)])
    return boxes[:4]


def _read_frames_via_ffmpeg(
    video: Path,
    times_ms: list[int],
    *,
    offset_sec: float = 0.0,
    crop_box: tuple[float, float, float, float] | None = None,
) -> dict[int, np.ndarray]:
    """Decode upright BGR frames via VideoCapture or FFmpeg fallback."""
    out: dict[int, np.ndarray] = {}
    cap = None
    try:
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            cap.release()
            cap = None
    except Exception:
        cap = None

    for t_ms in times_ms:
        t_sec = max(0.0, offset_sec + t_ms / 1000.0)
        frame = None
        if cap is not None:
            cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
            ok, fr = cap.read()
            if ok and fr is not None:
                if crop_box is not None:
                    cw, ch, cx, cy = [int(v) for v in crop_box]
                    fh, fw = fr.shape[:2]
                    cx0 = max(0, min(fw - 2, cx))
                    cy0 = max(0, min(fh - 2, cy))
                    cx1 = max(cx0 + 2, min(fw, cx0 + cw))
                    cy1 = max(cy0 + 2, min(fh, cy0 + ch))
                    fr = fr[cy0:cy1, cx0:cx1]
                frame = fr
        if frame is None:
            from .subtle import legibility
            rgb = legibility.grab_rgb(video, t_sec, 640, 360, crop_box=crop_box)
            if rgb is not None:
                frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if frame is not None:
            out[int(t_ms)] = frame

    if cap is not None:
        cap.release()
    return out


def _video_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:16]


def sample_subject(
    video: Path,
    times_ms: list[int],
    *,
    bands: int = BANDS,
    offset_sec: float = 0.0,
    crop_box: tuple[float, float, float, float] | None = None,
    cache_dir: Path | None = None,
    cancelled=None,
) -> dict[int, dict]:
    """Return {ms: {'t': s, 'faces': [[x,y,w,h]], 'hands': [[x,y,w,h]], 'person': [24 floats]}}."""
    if not times_ms:
        return {}
    digest = _video_digest(video)
    crop_tag = f'{crop_box}' if crop_box else 'full'
    cache_key = hashlib.sha256(f'{digest}|{offset_sec:.3f}|{crop_tag}|{bands}'.encode()).hexdigest()[:16]
    cdir = cache_dir or (config.CACHE_DIR / 'subtle' / 'subject' / cache_key)
    checked_path(cdir, config.RUNTIME.root, 'Subtle subject cache')
    cdir.mkdir(parents=True, exist_ok=True)
    cache_file = checked_path(cdir / f'subject.v{CACHE_VERSION}.json', config.RUNTIME.root, 'Subtle subject cache file')
    have: dict[int, dict] = {}
    if cache_file.is_file():
        try:
            have = {int(k): v for k, v in json.loads(cache_file.read_text(encoding='utf-8')).items()}
        except (ValueError, OSError):
            have = {}

    need = sorted({int(t) for t in times_ms} - set(have))
    if need:
        face_detector = None
        if reframe_detection.capabilities().get('auto_face'):
            try:
                face_detector = reframe_detection._face_detector(cv2, np)
            except Exception:
                face_detector = None
        frames = _read_frames_via_ffmpeg(video, need, offset_sec=offset_sec, crop_box=crop_box)
        for t_ms in need:
            if cancelled and cancelled():
                raise media.MediaCancelled('Analisis subjek dibatalkan.')
            bgr = frames.get(t_ms)
            if bgr is None:
                continue
            mask = clean_matte(infer_person_mask(bgr, quality='fast'))
            faces: list[list[float]] = []
            if face_detector is not None:
                small_bgr = cv2.resize(bgr, (320, max(180, int(round(320 * bgr.shape[0] / max(1, bgr.shape[1]))))))
                detected = reframe_detection._detect_faces(face_detector, small_bgr)
                faces = [[round(v, 4) for v in f['bbox']] for f in detected]
            hands = _detect_hands_heuristic(mask, faces)
            mh, mw = mask.shape[:2]
            band_vals: list[float] = []
            for b in range(bands):
                y0 = int(b * mh / bands)
                y1 = max(y0 + 1, int((b + 1) * mh / bands))
                band_vals.append(round(float((mask[y0:y1, :] >= 0.5).mean()), 4))
            have[t_ms] = {'t': round(t_ms / 1000.0, 3), 'faces': faces, 'hands': hands, 'person': band_vals}
        cache_file.write_text(json.dumps({str(k): v for k, v in have.items()}), encoding='utf-8')
    return {int(t): have[int(t)] for t in times_ms if int(t) in have}


def check_subject(
    video: Path,
    *,
    band_height: float = 0.19,
    samples: int = 12,
    start: float = 0.0,
    end: float | None = None,
    crop_box: tuple[float, float, float, float] | None = None,
    cancelled=None,
) -> dict:
    """Quick readiness check: returns {'recommend': 'behind'|'front', 'bigY': float, 'bandCoverage': float, 'reason': str}."""
    from .subtle import shots as subtle_shots

    info = media.probe(video)
    dur_ms = int(round(((end - start) if (end is not None and end > start) else info['duration']) * 1000.0))
    if dur_ms < 700:
        return {'recommend': 'front', 'bigY': 0.26, 'bandCoverage': 0.0, 'reason': 'Durasi klip terlalu singkat.'}
    times_ms = [int(dur_ms * (k + 0.5) / max(1, samples)) for k in range(samples)]
    got = sample_subject(video, times_ms, offset_sec=start, crop_box=crop_box, cancelled=cancelled)
    frames = [got[t] for t in times_ms if t in got]
    if not frames:
        return {'recommend': 'front', 'bigY': 0.26, 'bandCoverage': 0.0, 'reason': 'Frame video tidak terbaca.'}
    res = subtle_shots.analyse_shot((0, dur_ms), frames, band_height)
    recommend = 'behind' if res.get('behind') else 'front'
    return {
        'recommend': recommend,
        'bigY': round(float(res.get('y', 26.0)) / 100.0, 3),
        'bandCoverage': float(res.get('cover', 0.0)),
        'reason': res.get('reason') or ('Subjek terpisah rapi; ruang kepala mendukung teks belakang.' if recommend == 'behind' else 'Close-up terdeteksi; dialihkan ke depan.'),
    }


def generate_matte(
    video: Path,
    out_matte: Path,
    *,
    quality: str = 'balanced',
    start: float = 0.0,
    end: float | None = None,
    cancelled=None,
) -> dict:
    """Write grayscale person matte MP4 (white=person, black=background) with temporal EMA."""
    info = media.probe(video)
    vw, vh = int(info['width']), int(info['height'])
    vw -= vw % 2
    vh -= vh % 2
    fps = max(1.0, float(info.get('fps') or 30.0))
    ffmpeg = media.tool('ffmpeg')
    active_model = _resolve_model_kind(quality)

    dec_cmd = [ffmpeg, '-hide_banner', '-v', 'error', '-nostdin']
    if start > 0:
        dec_cmd += ['-ss', f'{start:.6f}']
    if end is not None and end > start:
        dec_cmd += ['-t', f'{end - start:.6f}']
    dec_cmd += [*media.input_args(video), '-vf', f'scale={vw}:{vh}', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']

    enc_cmd = [
        ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-y',
        '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', f'{vw}x{vh}', '-r', f'{fps:.6f}', '-i', '-',
        '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '16', '-pix_fmt', 'yuv420p', str(out_matte),
    ]

    dec = external_popen(
        dec_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=config.child_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    enc = external_popen(
        enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=config.child_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    frame_bytes = vw * vh * 3
    prev_raw: np.ndarray | None = None
    prev_ema: np.ndarray | None = None
    frames_count = 0
    try:
        assert dec.stdout is not None and enc.stdin is not None
        while True:
            if cancelled and cancelled():
                raise media.MediaCancelled('Pembuatan matte pembicara dibatalkan.')
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            bgr = np.frombuffer(buf, dtype=np.uint8).reshape(vh, vw, 3)
            raw_mask = infer_person_mask(bgr, quality=quality)
            cleaned = clean_matte(raw_mask)
            if prev_raw is not None and prev_ema is not None:
                motion = float(np.abs(raw_mask - prev_raw).mean())
                if motion <= EMA_MOTION_THRESHOLD:
                    smoothed = 0.55 * prev_ema + 0.45 * cleaned
                else:
                    smoothed = cleaned
            else:
                smoothed = cleaned
            prev_raw = raw_mask
            prev_ema = smoothed
            gray_u8 = np.clip(np.round(smoothed * 255.0), 0, 255).astype(np.uint8)
            enc.stdin.write(gray_u8.tobytes())
            frames_count += 1
        enc.stdin.close()
        dec.wait(timeout=30)
        enc_err = enc.stderr.read().decode('utf-8', 'replace') if enc.stderr else ''
        enc.wait(timeout=60)
        if enc.returncode != 0:
            raise media.MediaError('FFmpeg gagal menulis video matte pembicara: ' + enc_err[-300:])
    finally:
        for p in (dec, enc):
            if p.poll() is None:
                p.kill()
                p.wait()
    return {'out': str(out_matte), 'frames': frames_count, 'model': active_model, 'quality': quality}


def generate_packed_preview(
    video: Path,
    out_packed: Path,
    *,
    start: float,
    end: float,
    width: int,
    height: int,
    crop_box: tuple[float, float, float, float] | None = None,
    quality: str = 'fast',
    cancelled=None,
) -> Path:
    """Create side-by-side packed MP4 (left=picture, right=matte) for WebGL live Studio preview."""
    # Cap preview resolution cleanly so matte preview renders quickly in Studio
    scale = min(1.0, 480.0 / max(1, width))
    pw = max(64, int(round(width * scale / 2.0)) * 2)
    ph = max(64, int(round(height * scale / 2.0)) * 2)
    ffmpeg = media.tool('ffmpeg')
    info = media.probe(video)
    fps = min(30.0, max(12.0, float(info.get('fps') or 24.0)))
    vf_parts = []
    if crop_box is not None:
        cw, ch, cx, cy = [int(v) for v in crop_box]
        vf_parts.append(f'crop={cw}:{ch}:{cx}:{cy}')
    vf_parts.append(f'fps={fps:g},scale={pw}:{ph}')

    dec_cmd = [
        ffmpeg, '-hide_banner', '-v', 'error', '-nostdin',
        '-ss', f'{max(0.0, start):.6f}', '-t', f'{max(0.1, end - start):.6f}',
        *media.input_args(video), '-vf', ','.join(vf_parts),
        '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-',
    ]
    enc_cmd = [
        ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-y',
        '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{pw * 2}x{ph}', '-r', f'{fps:.6f}', '-i', '-',
        '-ss', f'{max(0.0, start):.6f}', '-t', f'{max(0.1, end - start):.6f}', *media.input_args(video),
        '-map', '0:v:0', '-map', '1:a:0?', '-shortest',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(out_packed),
    ]
    dec = external_popen(
        dec_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=config.child_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    enc = external_popen(
        enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=config.child_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    frame_bytes = pw * ph * 3
    prev_raw = None
    prev_ema = None
    try:
        assert dec.stdout is not None and enc.stdin is not None
        while True:
            if cancelled and cancelled():
                raise media.MediaCancelled('Pembuatan preview matte dibatalkan.')
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            bgr = np.frombuffer(buf, dtype=np.uint8).reshape(ph, pw, 3)
            raw_mask = infer_person_mask(bgr, quality=quality)
            cleaned = clean_matte(raw_mask)
            if prev_raw is not None and prev_ema is not None and float(np.abs(raw_mask - prev_raw).mean()) <= EMA_MOTION_THRESHOLD:
                smoothed = 0.55 * prev_ema + 0.45 * cleaned
            else:
                smoothed = cleaned
            prev_raw = raw_mask
            prev_ema = smoothed
            gray_u8 = np.clip(np.round(smoothed * 255.0), 0, 255).astype(np.uint8)
            matte_bgr = np.repeat(gray_u8[:, :, None], 3, axis=2)
            packed = np.concatenate([bgr, matte_bgr], axis=1)
            enc.stdin.write(packed.tobytes())
        enc.stdin.close()
        dec.wait(timeout=30)
        enc.wait(timeout=60)
        if enc.returncode != 0:
            raise media.MediaError('FFmpeg gagal membuat preview matte.')
    finally:
        for p in (dec, enc):
            if p.poll() is None:
                p.kill()
                p.wait()
    return out_packed
