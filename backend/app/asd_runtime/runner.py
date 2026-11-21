"""Isolated Light-ASD inference over existing M1 face tracks.

Run with the optional ASD interpreter, never from the V1 environment:
    python -m app.asd_runtime.runner manifest.json --output result.json

The manifest uses source-global seconds and normalized XYWH face boxes. This
module neither detects faces nor transcribes audio. Its scores are uncalibrated
model outputs; speaker selection/abstention belongs to the calling policy.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

from .. import config

import cv2
import numpy as np
import python_speech_features
from scipy.io import wavfile
from scipy.ndimage import median_filter
import torch
from torch import nn

from .model import ASD_Model

VERSION = "light-asd-m2-1"
UPSTREAM_COMMIT = "ed38c232de5efe0261dbd68627c0ade7cdfe14eb"
KNOWN_WEIGHTS = {
    "efc375833887eefa9d209dc92810e18519b04c3c73ea35a549f2a7f40b7d94d5": "finetuning_TalkSet",
    "d44bc3ea7baa8e0946fa3921311714a630ed8b90a1928fab0dbe30d918909317": "pretrain_AVA_CVPR",
}
FPS = 25


def progress(stage: str, **values: Any) -> None:
    print(json.dumps({"event": "progress", "stage": stage, **values}), file=sys.stderr, flush=True)


class _Head(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.FC = nn.Linear(128, 2)


class LightASD(nn.Module):
    """Only inference state; names intentionally match the official checkpoint."""
    def __init__(self) -> None:
        super().__init__()
        self.model = ASD_Model()
        self.lossAV = _Head()
        self.lossV = _Head()


def _command(arguments: list[str], timeout: float = 600) -> None:
    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"FFmpeg failed ({result.returncode}): {result.stderr[-2400:]}")


def _tracks(manifest: dict[str, Any]) -> dict[int, list[tuple[float, list[float], float]]]:
    tracks: dict[int, list[tuple[float, list[float], float]]] = {}
    for sample in manifest.get("face_samples", []):
        for face in sample.get("faces", []):
            tracks.setdefault(int(face["track_id"]), []).append((float(sample["time"]), face["bbox"], float(face.get("confidence", 0))))
    for track in manifest.get("tracks", []):
        for sample in track.get("samples", []):
            tracks.setdefault(int(track["track_id"]), []).append((float(sample["time"]), sample["bbox"], float(sample.get("confidence", 0))))
    return {key: sorted({row[0]: row for row in rows}.values()) for key, rows in tracks.items()}


def dense_track(rows: list[tuple[float, list[float], float]], times: np.ndarray, *, max_gap: float = .5, edge_gap: float = .2, min_confidence: float = .75) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate only short, observed track gaps. Missing detections stay invalid."""
    rows = [r for r in rows if r[2] >= min_confidence and len(r[1]) == 4 and all(math.isfinite(float(v)) for v in r[1]) and r[1][2] > 0 and r[1][3] > 0]
    boxes = np.zeros((len(times), 4), dtype=np.float32)
    valid = np.zeros(len(times), dtype=bool)
    if not rows:
        return boxes, valid
    observed = [row[0] for row in rows]
    for i, at in enumerate(times):
        k = bisect.bisect_left(observed, at)
        if k < len(rows) and abs(observed[k] - at) < 1e-5:
            boxes[i], valid[i] = rows[k][1], True
        elif 0 < k < len(rows) and observed[k] - observed[k - 1] <= max_gap + 1e-6:
            mix = (at - observed[k - 1]) / (observed[k] - observed[k - 1])
            boxes[i] = (1 - mix) * np.asarray(rows[k - 1][1]) + mix * np.asarray(rows[k][1])
            valid[i] = True
        elif k == 0 and observed[0] - at <= edge_gap:
            boxes[i], valid[i] = rows[0][1], True
        elif k == len(rows) and at - observed[-1] <= edge_gap:
            boxes[i], valid[i] = rows[-1][1], True
    return boxes, valid


def _crop(gray: np.ndarray, composition: np.ndarray) -> np.ndarray:
    """Official padded crop -> 224 -> center 112, with exact XY geometry."""
    cx, cy, half = composition
    pad = max(1, int(math.ceil(half * 1.8)))
    frame = np.pad(gray, ((pad, pad), (pad, pad)), mode="constant", constant_values=110)
    cx, cy = cx + pad, cy + pad
    face = frame[int(cy - half):int(cy + half * 1.8), int(cx - half * 1.4):int(cx + half * 1.4)]
    if not face.size:
        return np.full((112, 112), 110, dtype=np.uint8)
    return cv2.resize(face, (224, 224), interpolation=cv2.INTER_LINEAR)[56:168, 56:168]


def _prepare_uncached(manifest: dict[str, Any], temporary: Path) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
    start, end = float(manifest["start"]), float(manifest["end"])
    if not 0 <= start < end or end - start > 600:
        raise ValueError("ASD requires a selected shot/clip between 0 and 600 seconds")
    source, ffmpeg = str(Path(manifest["source"]).resolve()), str(Path(manifest["ffmpeg"]).resolve())
    if not Path(source).is_file() or not Path(ffmpeg).is_file():
        raise FileNotFoundError("Source video or FFmpeg is missing")
    video, audio = temporary / "video.mp4", temporary / "audio.wav"
    # Paths with emoji remain FFmpeg arguments; OpenCV only opens an ASCII temp path.
    width = min(1920, int(manifest.get("source_width", 1920)))
    width -= width % 2
    progress("prepare_media", start=start, end=end)
    _command([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", source, "-t", str(end - start), "-map", "0:v:0", "-an", "-vf", f"fps={FPS},scale={width}:-2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-threads", "4", str(video)])
    _command([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", source, "-t", str(end - start), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)])
    sr, wave = wavfile.read(audio)
    features = python_speech_features.mfcc(wave, sr, numcep=13, winlen=.025, winstep=.010).astype(np.float32)
    capture = cv2.VideoCapture(str(video))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_width, frame_height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if count < 2 or not frame_width or not frame_height:
        raise RuntimeError("Prepared video has no readable frames")
    count = min(count, len(features) // 4)
    times = start + np.arange(count, dtype=np.float64) / FPS
    tracks = _tracks(manifest)
    if not tracks:
        raise ValueError("No existing M1 face tracks were supplied")
    faces, masks, compositions = {}, {}, {}
    for tid, rows in tracks.items():
        boxes, valid = dense_track(rows, times, max_gap=min(.5, float(manifest.get("max_track_gap", .5))), min_confidence=float(manifest.get("min_face_confidence", .75)))
        x, y, w, h = boxes.T * np.array([[frame_width], [frame_height], [frame_width], [frame_height]])
        centers = np.column_stack((x + w / 2, y + h / 2, np.maximum(w, h) / 2))
        # Official 13-frame median filtering, applied only within valid runs.
        edges = np.flatnonzero(np.diff(np.r_[False, valid, False]))
        for a, b in zip(edges[::2], edges[1::2]):
            centers[a:b] = median_filter(centers[a:b], size=(min(13, ((b-a-1)//2)*2+1), 1), mode="nearest")
        compositions[tid] = centers
        faces[tid], masks[tid] = np.full((count, 112, 112), 110, dtype=np.uint8), valid
    for i in range(count):
        ok, frame = capture.read()
        if not ok:
            for mask in masks.values():
                mask[i:] = False
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for tid in faces:
            if masks[tid][i]:
                faces[tid][i] = _crop(gray, compositions[tid][i])
    capture.release()
    audio_frames = np.pad(wave.astype(np.float64), (0, max(0, count * 640 - len(wave))))[:count * 640].reshape(count, 640) / 32768
    rms_dbfs = 20 * np.log10(np.maximum(np.sqrt(np.mean(audio_frames ** 2, axis=1)), 1e-8))
    return faces, masks, features[:count * 4], times, {"prepared_width": frame_width, "prepared_height": frame_height, "audio_sample_rate": sr, "audio_samples": len(wave), "frame_count": count, "audio_rms_dbfs": np.round(rms_dbfs, 3).tolist(), "audio_activity_is_speech_detector": False}


def prepare(manifest: dict[str, Any], temporary: Path) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
    """Cache immutable prepared input, independently from checkpoint or device."""
    cache_path = None
    if manifest.get("cache_dir"):
        source = Path(manifest["source"]).resolve()
        stat = source.stat()
        fingerprint = {key: manifest.get(key) for key in ("start", "end", "source_width", "face_samples", "tracks", "max_track_gap", "min_face_confidence")}
        fingerprint.update(version=VERSION, source=str(source), source_size=stat.st_size, source_mtime_ns=stat.st_mtime_ns)
        digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        cache_path = Path(manifest["cache_dir"]).resolve() / f"prepared-{digest}.npz"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path and cache_path.is_file():
        progress("prepared_cache_hit")
        with np.load(cache_path, allow_pickle=False) as stored:
            track_ids = stored["track_ids"].tolist()
            faces = {tid: stored[f"face_{tid}"].copy() for tid in track_ids}
            masks = {tid: stored[f"mask_{tid}"].copy() for tid in track_ids}
            audio, times = stored["audio"].copy(), stored["times"].copy()
            media = json.loads(str(stored["media"].item()))
        media["prepared_features_cache_hit"] = True
    else:
        faces, masks, audio, times, media = _prepare_uncached(manifest, temporary)
        if cache_path:
            payload = {"track_ids": np.asarray(list(faces)), "audio": audio, "times": times, "media": np.asarray(json.dumps(media))}
            payload.update({f"face_{tid}": value for tid, value in faces.items()})
            payload.update({f"mask_{tid}": value for tid, value in masks.items()})
            staging = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
            try:
                with staging.open("wb") as handle:
                    np.savez_compressed(handle, **payload)
                os.replace(staging, cache_path)
            finally:
                staging.unlink(missing_ok=True)
        media["prepared_features_cache_hit"] = False
    # Controlled ablations are applied after cached original inputs are loaded.
    if manifest.get("diagnostic_silent_audio"):
        audio = python_speech_features.mfcc(np.zeros(media["audio_samples"], dtype=np.int16), 16000, numcep=13, winlen=.025, winstep=.010).astype(np.float32)[:len(times) * 4]
        media["audio_rms_dbfs"] = [-160.0] * len(times)
    shift = int(round(float(manifest.get("diagnostic_audio_shift", 0)) * 100))
    if shift:
        audio = np.roll(audio, shift, axis=0)
    return faces, masks, audio, times, media


def infer(manifest: dict[str, Any]) -> dict[str, Any]:
    config.validate_model_manifest(manifest)
    began = time.perf_counter()
    torch.set_num_threads(max(1, min(16, int(manifest.get("cpu_threads", 4)))))
    torch.set_num_interop_threads(1)
    weights = Path(manifest["weights"]).resolve()
    sha = hashlib.sha256(weights.read_bytes()).hexdigest()
    if sha not in KNOWN_WEIGHTS:
        raise ValueError("Checkpoint checksum is not an audited official Light-ASD weight")
    requested = manifest.get("device", "cpu")
    if requested not in {"cpu", "cuda"}:
        raise ValueError("device must be cpu or cuda")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable in the isolated ASD environment")
    device = torch.device(requested)
    network = LightASD()
    network.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True), strict=True)
    network.to(device).eval()
    loaded = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="podcut_asd_") as work:
        faces, masks, audio, times, prepared = prepare(manifest, Path(work))
        preprocessing_end = time.perf_counter()
        output = []
        durations = (1, 2, 3, 4, 5, 6)
        with torch.inference_mode():
            for track_index, (tid, frames) in enumerate(sorted(faces.items())):
                progress("model_inference", track_id=tid, completed_tracks=track_index, total_tracks=len(faces))
                total = np.zeros((len(times), 2), dtype=np.float64)
                for duration in durations:
                    n = duration * FPS
                    for offset in range(0, len(times), n):
                        finish = min(len(times), offset + n)
                        input_a = torch.from_numpy(audio[offset * 4:finish * 4]).unsqueeze(0).to(device)
                        input_v = torch.from_numpy(frames[offset:finish].astype(np.float32)).unsqueeze(0).to(device)
                        av, _ = network.model(input_a, input_v)
                        logits = network.lossAV.FC(av).detach().cpu().numpy()
                        if logits.shape != (finish - offset, 2):
                            raise RuntimeError(f"Audio/video feature mismatch: {logits.shape} vs {finish-offset}")
                        total[offset:finish] += logits
                logits = total / len(durations)
                stable = logits - logits.max(axis=1, keepdims=True)
                probabilities = np.exp(stable) / np.exp(stable).sum(axis=1, keepdims=True)
                valid = masks[tid]
                output.append({
                    "track_id": tid,
                    "valid_fraction": round(float(valid.mean()), 6),
                    "frames": [{"time": round(float(at), 6), "valid": bool(valid[i]), "speaking_score": round(float(probabilities[i, 1]), 7) if valid[i] else None, "logits": [round(float(v), 7) for v in logits[i]] if valid[i] else None, "raw_positive_logit": round(float(logits[i, 1]), 7) if valid[i] else None} for i, at in enumerate(times)],
                })
        if device.type == "cuda":
            torch.cuda.synchronize()
        finished = time.perf_counter()
    return {
        "version": VERSION, "model": "Light-ASD", "checkpoint": KNOWN_WEIGHTS[sha], "checkpoint_sha256": sha, "upstream_commit": UPSTREAM_COMMIT,
        "score_kind": "softmax_of_mean_multiscale_logits", "score_is_calibrated": False,
        "score_description": "Uncalibrated two-class audio-visual model output, not probability that the selected speaker is correct.",
        "start": float(manifest["start"]), "end": float(manifest["end"]), "processed_end": round(float(times[-1] + 1/FPS), 6), "fps": FPS,
        "track_interpolation_max_gap_seconds": min(.5, float(manifest.get("max_track_gap", .5))), "durations_seconds": list(durations), "tracks": output,
        "runtime": {"device": str(device), "torch_version": torch.__version__, "cuda_available": torch.cuda.is_available(), "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None, "cpu_threads": torch.get_num_threads(), "parameters": sum(p.numel() for p in network.parameters()), "load_seconds": round(loaded - began, 4), "preprocessing_seconds": round(preprocessing_end - loaded, 4), "inference_seconds": round(finished - preprocessing_end, 4), "total_seconds": round(finished - began, 4), "inference_realtime_factor": round((finished-preprocessing_end)/(float(manifest["end"])-float(manifest["start"])), 4)},
        "media": prepared,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        config.RUNTIME.validate_writable(args.manifest, 'ASD request')
        if args.output:
            config.RUNTIME.validate_writable(args.output, 'ASD output')
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        result = infer(manifest)
        serialized = json.dumps(result, ensure_ascii=True, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
            print(json.dumps({"event": "complete", "output": str(args.output), "runtime": result["runtime"]}), flush=True)
        else:
            print(serialized, flush=True)
        return 0
    except Exception as error:
        print(json.dumps({"event": "error", "type": type(error).__name__, "message": str(error)}), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
