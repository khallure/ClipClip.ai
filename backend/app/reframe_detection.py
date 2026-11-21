"""Optional, local shot/face analysis for a bounded clip; never speaker inference.

The source and all V1 transcript/analysis data remain untouched. A disposable
H.264 proxy keeps OpenCV independent of source AV1 support and Unicode filenames.
All returned times are original source seconds; face confidence is YuNet's
detection score, and speaker_confidence is deliberately always None in M1.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import math
import tempfile
from pathlib import Path

from . import config, media

MODEL_NAME = 'face_detection_yunet_2023mar.onnx'
MODEL_PATH = config.MODELS_DIR / 'reframe' / MODEL_NAME
MODEL_SHA256 = '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'
CV_VERSION = '4.14.0.94'
SCENE_VERSION = '0.7.3'
FACE_SAMPLE_FPS = 3.0
FACE_THRESHOLD = .75
MAX_FACES = 12


def capabilities() -> dict:
    """Readiness only: no model inference, imports of cv2, or network downloads."""
    result = {'available': False, 'auto_face': False, 'device': 'cpu', 'model': 'YuNet 2023mar'}
    try:
        if (not importlib.util.find_spec('cv2') or not importlib.util.find_spec('scenedetect')
                or importlib.metadata.version('opencv-python') != CV_VERSION
                or importlib.metadata.version('scenedetect') != SCENE_VERSION):
            return {**result, 'reason': 'Paket analisis framing lokal belum siap. Jalankan setup reframing opsional.'}
        if not config.RUNTIME.validate_writable(MODEL_PATH, 'Face model').is_file() or hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
            return {**result, 'reason': 'Model wajah lokal belum tersedia atau gagal pemeriksaan integritas.'}
    except (ImportError, ValueError, OSError, importlib.metadata.PackageNotFoundError):
        return {**result, 'reason': 'Paket analisis framing lokal belum siap. Jalankan setup reframing opsional.'}
    return {**result, 'available': True, 'auto_face': True,
            'reason': 'Deteksi shot dan wajah lokal siap. Pembicara aktif belum didukung pada Milestone 1.'}


def _cancel(cancelled):
    if cancelled and cancelled():
        raise media.MediaCancelled('Analisis framing dibatalkan. Sumber dan hasil V1 tetap tersimpan.')


def _progress(progress, fraction, message):
    if progress:
        progress(fraction, message)


def _imports():
    try:
        import cv2
        import numpy as np
        from scenedetect import FrameTimecode
        from scenedetect.detectors import ContentDetector
        return cv2, np, FrameTimecode, ContentDetector
    except (ImportError, OSError):
        raise media.MediaError('Paket reframing lokal tidak dapat dimuat. Periksa setup OpenCV/PySceneDetect.') from None


def _face_detector(cv2, np):
    try:
        # OpenCV's filename loader fails for Windows paths containing emoji.
        # Its public buffer overload avoids copying weights to a second path.
        weights = np.frombuffer(MODEL_PATH.read_bytes(), dtype=np.uint8)
        return cv2.FaceDetectorYN.create('onnx', weights, np.empty(0, dtype=np.uint8),
                                        (320, 320), FACE_THRESHOLD, .3, 1000,
                                        cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
    except (OSError, cv2.error):
        raise media.MediaError('Model wajah lokal tidak dapat dimuat oleh OpenCV. Periksa setup reframing.') from None


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap = max(0, min(ax+aw, bx+bw)-max(ax, bx)) * max(0, min(ay+ah, by+bh)-max(ay, by))
    union = aw*ah + bw*bh-overlap
    return overlap/union if union > 0 else 0.0


def _detect_faces(detector, frame):
    height, width = frame.shape[:2]
    detector.setInputSize((width, height))
    _, raw = detector.detect(frame)
    if raw is None:
        return []
    faces = []
    for row in raw:
        if len(row) < 15 or not all(math.isfinite(float(v)) for v in (*row[:4], row[-1])):
            continue
        x, y = max(0., min(1., float(row[0])/width)), max(0., min(1., float(row[1])/height))
        right = max(x, min(1., float(row[0]+row[2])/width))
        bottom = max(y, min(1., float(row[1]+row[3])/height))
        box = [x, y, right-x, bottom-y]
        score = float(row[-1])
        if min(box[2:]) < .008 or not FACE_THRESHOLD <= score <= 1:
            continue
        rx, ry = round(x, 7), round(y, 7)
        bounded = [rx, ry, min(round(box[2], 7), 1-rx), min(round(box[3], 7), 1-ry)]
        faces.append({'bbox': bounded, 'confidence': round(score, 7)})
    return sorted(faces, key=lambda f: f['confidence'], reverse=True)[:MAX_FACES]


def _track_sample(faces, tracks, next_id, time):
    """Short IoU track identities within ONE shot, not cross-shot face identity."""
    tracks = {ident: track for ident, track in tracks.items() if time-track['time'] <= 1.5}
    used, result = set(), []
    for face in sorted(faces, key=lambda f: f['bbox'][0]):
        matches = [(ident, _iou(face['bbox'], track['bbox'])) for ident, track in tracks.items() if ident not in used]
        ident, overlap = max(matches, key=lambda pair: pair[1], default=(None, 0))
        if overlap < .12:
            ident, next_id = next_id, next_id+1
        used.add(ident)
        tracks[ident] = {'bbox': face['bbox'], 'time': time}
        result.append({'track_id': ident, **face})
    return result, tracks, next_id


def _pose_for_face(box, width, height):
    x, y, w, h = box
    # Full-height landscape viewport preserves headroom/upper body at zoom 1.
    # On a tall input, place its top half a face-height above the forehead.
    viewport_h = min(float(height), width*16/9)
    target_y = y-h*.55+viewport_h/(2*height)
    return min(1., max(0., x+w/2)), min(1., max(0., target_y))


def _compose_shot(shot_id, start, end, samples, width, height):
    counts = [len(sample['faces']) for sample in samples]
    present = [sample for sample in samples if len(sample['faces']) == 1]
    base = {'id': shot_id, 'start': round(start, 6), 'end': round(end, 6), 'focus': 'auto',
            'speaker_confidence': None, 'face_samples': samples}
    ambiguous = any(count > 1 for count in counts)
    if ambiguous or not present:
        reason = ('Lebih dari satu wajah terdeteksi. Seluruh gambar dipertahankan dengan Focus Both; pilih subjek bila perlu. Pembicara aktif belum dinilai.'
                  if ambiguous else 'Wajah belum terdeteksi dengan cukup yakin. Framing tengah dipertahankan; periksa shot ini.')
        return {**base, 'focus':'both' if ambiguous else 'auto', 'decision': 'ambiguous' if ambiguous else 'no_face', 'review_required': True,
                'reason': reason, 'keyframes': [
                    {'time': round(start, 6), 'x': .5, 'y': .5, 'zoom': 1., 'manual': False},
                    {'time': round(end, 6), 'x': .5, 'y': .5, 'zoom': 1., 'manual': False}]}
    identities = {sample['faces'][0]['track_id'] for sample in present}
    low_coverage = len(present)/max(1, len(samples)) < .6
    low_scores = sum(sample['faces'][0]['confidence'] for sample in present)/len(present) < .85
    review = low_coverage or low_scores or len(identities) > 1
    reason = 'Deteksi wajah terputus, berubah, atau kurang yakin. Periksa posisi subjek.' if review else None
    # Initialize from the first reliable sample rather than panning from center.
    px, py = _pose_for_face(present[0]['faces'][0]['bbox'], width, height)
    keys = [{'time': round(start, 6), 'x': round(px, 7), 'y': round(py, 7), 'zoom': 1., 'manual': False}]
    previous_time = start
    last_id = present[0]['faces'][0]['track_id']
    for sample in samples:
        if sample['time'] <= start+.000001 or sample['time'] >= end-.000001:
            continue
        if len(sample['faces']) == 1:
            face = sample['faces'][0]
            # A newly appearing track after occlusion is not proof of continuity.
            # Hold the composition when identity becomes uncertain; manual wins.
            if face['track_id'] == last_id:
                tx, ty = _pose_for_face(face['bbox'], width, height)
                dt = max(.001, sample['time']-previous_time)
                alpha = 1-math.exp(-dt/.55)
                if abs(tx-px) > .006:
                    px += alpha*(tx-px)
                if abs(ty-py) > .006:
                    py += alpha*(ty-py)
        keys.append({'time': sample['time'], 'x': round(px, 7), 'y': round(py, 7), 'zoom': 1., 'manual': False})
        previous_time = sample['time']
    keys.append({'time': round(end, 6), 'x': round(px, 7), 'y': round(py, 7), 'zoom': 1., 'manual': False})
    return {**base, 'decision': 'single_face', 'review_required': review, 'reason': reason, 'keyframes': keys}


def analyze_reframe(source: Path, source_info: dict, start: float, end: float, *, cancelled=None, progress=None) -> dict:
    """Detect hard cuts/faces only in a selected <=600-second source interval."""
    ready = capabilities()
    if not ready['available']:
        raise media.MediaError(ready['reason'])
    if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (start, end))
            or start < 0 or not 0 < end-start <= 600 or end > source_info['duration']+.001):
        raise media.MediaError('Pilih rentang framing di dalam sumber, maksimal 10 menit.')
    source = Path(source)
    if not source.is_file():
        raise media.MediaError('Sumber lokal tidak ditemukan untuk analisis framing.')
    # Cancellation must not load optional model libraries in the minimal DEV runtime.
    _cancel(cancelled)
    cv2, np, FrameTimecode, ContentDetector = _imports()
    fps = float(source_info.get('fps') or 30)
    if not math.isfinite(fps) or not 0 < fps <= 240:
        raise media.MediaError('FPS sumber tidak didukung untuk analisis framing.')
    width, height, duration = int(source_info['width']), int(source_info['height']), end-start
    _cancel(cancelled)
    _progress(progress, .01, 'Menyiapkan proxy kecil untuk deteksi shot dan wajah lokal.')
    with tempfile.TemporaryDirectory(prefix='podcut-reframe-') as temporary:
        proxy = Path(temporary)/'analysis.mp4'
        # Analysis-only CFR proxy gives frame-index times a defined clock for VFR
        # inputs too. It never changes the source or the exported clip duration.
        media.run([media.tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                   '-ss', f'{start:.6f}', *media.input_args(source), '-t', f'{duration:.6f}', '-an',
                   '-vf', f'scale=trunc(min(640\\,iw)/2)*2:-2,fps={fps:.9f}',
                   '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p',
                   '-threads', '2', str(proxy)], cancelled=cancelled, timeout=1800)
        _cancel(cancelled)
        capture = cv2.VideoCapture(str(proxy))
        try:
            if not capture.isOpened():
                raise media.MediaError('Proxy analisis framing tidak dapat dibaca.')
            proxy_fps = float(capture.get(cv2.CAP_PROP_FPS)) or fps
            total = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
            detector = ContentDetector(threshold=22.0, min_scene_len=max(2, round(proxy_fps*.12)))
            cuts, index = [], 0
            while True:
                _cancel(cancelled)
                ok, frame = capture.read()
                if not ok:
                    break
                cuts.extend(cut.seconds for cut in detector.process_frame(FrameTimecode(index, proxy_fps), frame))
                index += 1
                if index % max(1, round(proxy_fps)) == 0:
                    _progress(progress, .08+.3*min(1, index/total), f'Memeriksa pergantian shot: {min(duration,index/proxy_fps):.1f}/{duration:.1f} detik.')
            cuts.extend(cut.seconds for cut in detector.post_process(FrameTimecode(index, proxy_fps)))
            if not index:
                raise media.MediaError('Tidak ada frame video untuk dianalisis.')
            boundaries = [0.] + sorted({round(t, 6) for t in cuts if 0.000001 < t < duration-.000001}) + [duration]
            face_detector = _face_detector(cv2, np)
            shots = []
            # Re-read each proxy frame, but perform DNN inference at about 3 FPS.
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            sample_step = max(1, round(proxy_fps/FACE_SAMPLE_FPS))
            shot_index, samples, tracks, next_id, frame_index, first_frame = 0, [], {}, 1, 0, True
            while True:
                _cancel(cancelled)
                ok, frame = capture.read()
                if not ok:
                    break
                rel = frame_index/proxy_fps
                while shot_index+1 < len(boundaries)-1 and rel >= boundaries[shot_index+1]-.000001:
                    shots.append(_compose_shot(f'shot-{shot_index+1:04}', start+boundaries[shot_index],
                                               start+boundaries[shot_index+1], samples, width, height))
                    shot_index += 1
                    samples, tracks, next_id, first_frame = [], {}, 1, True
                if first_frame or frame_index % sample_step == 0:
                    time = round(start+max(boundaries[shot_index], rel), 6)
                    if time < round(start+boundaries[shot_index+1], 6):
                        faces, tracks, next_id = _track_sample(_detect_faces(face_detector, frame), tracks, next_id, time)
                        if not samples or time > samples[-1]['time']:
                            samples.append({'time': time, 'faces': faces})
                    first_frame = False
                    _progress(progress, .4+.58*min(1, frame_index/total), f'Mendeteksi wajah lokal: shot {shot_index+1}/{len(boundaries)-1}.')
                frame_index += 1
            shots.append(_compose_shot(f'shot-{shot_index+1:04}', start+boundaries[shot_index], end, samples, width, height))
        except cv2.error:
            raise media.MediaError('OpenCV tidak dapat menyelesaikan deteksi. Gunakan framing manual atau periksa sumber.') from None
        finally:
            capture.release()
    _cancel(cancelled)
    _progress(progress, 1., f'Analisis framing selesai: {len(shots)} shot. Periksa shot yang ditandai.')
    return {'shots': shots, 'analysis': {'detector': 'YuNet 2023mar', 'model_sha256': MODEL_SHA256,
            'scene_detector': f'PySceneDetect {SCENE_VERSION} ContentDetector', 'scene_threshold': 22.,
            'min_scene_seconds': .12, 'face_sample_fps': round(proxy_fps/sample_step, 4),
            'device': 'cpu', 'proxy_width_limit': 640, 'source_fps': fps,
            'active_speaker_available': False, 'warnings': [
                'Milestone 1 mendeteksi wajah, bukan pembicara aktif. Master shot atau deteksi yang meragukan memerlukan review.',
                'Wajah kecil, profil, mikrofon dan perpindahan sangat cepat dapat membuat deteksi terlewat.']}}
