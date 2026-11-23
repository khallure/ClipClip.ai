"""Tests for Dual-Tier Apache-2.0 Windows ONNX segmentation and Subtle vision pipeline."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time

from fastapi.testclient import TestClient
import numpy as np
import pytest

from app import config, media, subtle_models, subtle_vision
from app.main import create_app


class _FakeResp:
    def __init__(self, payload: bytes, chunk: int = 32768):
        self._buf = payload
        self._pos = 0
        self._chunk = chunk

    def read(self, n: int = -1) -> bytes:
        size = self._chunk if n < 0 else min(self._chunk, n)
        out = self._buf[self._pos:self._pos + size]
        self._pos += len(out)
        return out

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_bundled_selfie_segmentation_manifest_and_inference():
    path = subtle_models.verify_bundled_selfie()
    assert path.is_file()
    caps = subtle_vision.capabilities()
    assert caps['available'] is True and caps['bundled_selfie'] is True

    # Synthetic 320x320 BGR frame (head+shoulders circle in center)
    img = np.full((320, 320, 3), 24, dtype=np.uint8)
    import cv2
    cv2.circle(img, (160, 100), 42, (200, 175, 160), -1)
    cv2.ellipse(img, (160, 240), (75, 95), 0, 0, 360, (60, 110, 180), -1)
    mask = subtle_vision.infer_person_mask(img, quality='fast')
    assert mask.shape == (320, 320)
    assert mask.dtype == np.float32
    assert 0.0 <= float(mask.min()) <= float(mask.max()) <= 1.0
    cleaned = subtle_vision.clean_matte(mask)
    assert cleaned.shape == (320, 320)


def test_modnet_tier2_downloader_checksum_and_cancellation(tmp_path, monkeypatch):
    manifest = subtle_models.load_manifest()
    mod_meta = manifest['modnet']
    env_modnet = os.environ.get('PODCUT_TEST_MODNET_PATH', '').strip()
    if env_modnet and Path(env_modnet).is_file():
        # Optional smoke verification against a local MODNet ONNX file supplied by env
        models_dir = tmp_path / 'models_real'
        target = subtle_models.modnet_target_dir(models_dir)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(env_modnet, target / mod_meta['filename'])
        verified = subtle_models.verify_modnet(models_dir)
        assert verified.is_file()

    # Valid checksum passes and marks Tier-2 MODNet ready
    good_bytes = subtle_models.verify_bundled_selfie().read_bytes()
    good_manifest = {
        **manifest,
        'modnet': {
            **mod_meta,
            'bytes': len(good_bytes),
            'sha256': hashlib.sha256(good_bytes).hexdigest(),
        },
    }
    monkeypatch.setattr(subtle_models, 'load_manifest', lambda: good_manifest)
    good_mgr = subtle_models.SegmentationModelManager(
        models_dir=tmp_path / 'm_ok',
        urlopen=lambda url, timeout: _FakeResp(good_bytes, chunk=256),
    )
    good_mgr.start_download()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        st = good_mgr.status()
        if st['modnet_state'] in {'ready', 'failed'}:
            break
        time.sleep(0.05)
    assert good_mgr.status()['modnet_state'] == 'ready'
    assert (subtle_models.modnet_target_dir(tmp_path / 'm_ok') / mod_meta['filename']).is_file()
    monkeypatch.undo()

    # Checksum mismatch deletes file and fails closed
    bad_bytes = b'BAD' * 1000
    mgr = subtle_models.SegmentationModelManager(
        models_dir=tmp_path / 'm1',
        urlopen=lambda url, timeout: _FakeResp(bad_bytes, chunk=500),
    )
    assert mgr.status()['modnet_state'] == 'missing'
    mgr.start_download()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        st = mgr.status()
        if st['modnet_state'] in {'failed', 'ready'}:
            break
        time.sleep(0.05)
    assert mgr.status()['modnet_state'] == 'failed'
    assert not (subtle_models.modnet_target_dir(tmp_path / 'm1') / mod_meta['filename']).exists()


def test_video_subject_sampling_matte_and_packed_preview(tmp_path):
    ffmpeg = config.binary('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg required for video vision integration test')
    src = tmp_path / 'person_synth.mp4'
    subprocess.run([
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=#182030:s=320x480:r=15:d=2',
        '-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=16000:d=2',
        '-vf', 'drawbox=x=100:y=110:w=120:h=130:color=#E5B89B@1:t=fill,drawbox=x=70:y=240:w=180:h=240:color=#2255AA@1:t=fill',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', str(src),
    ], check=True, capture_output=True, timeout=30)

    got = subtle_vision.sample_subject(src, [300, 900, 1500], cache_dir=tmp_path / 'sub_cache')
    assert len(got) == 3
    for ms, row in got.items():
        assert len(row['person']) == 24
        assert isinstance(row['faces'], list)

    chk = subtle_vision.check_subject(src, samples=3)
    assert chk['recommend'] in {'behind', 'front'}

    matte = tmp_path / 'matte.mp4'
    m_res = subtle_vision.generate_matte(src, matte, quality='fast')
    assert matte.is_file() and m_res['frames'] >= 20
    matte_probe = media.probe(matte)
    assert (matte_probe['width'], matte_probe['height']) == (320, 480)

    packed = tmp_path / 'packed.mp4'
    subtle_vision.generate_packed_preview(src, packed, start=0.0, end=1.5, width=320, height=480, quality='fast')
    packed_probe = media.probe(packed)
    assert packed_probe['width'] == 640 and packed_probe['height'] == 480


def test_api_segmentation_and_matte_preview_endpoints(tmp_path):
    app = create_app(tmp_path / 'jobs')
    with TestClient(app) as client:
        h = client.get('/api/health').json()
        assert 'segmentation' in h
        assert h['segmentation']['bundled_ready'] is True
        seg = client.get('/api/models/segmentation')
        assert seg.status_code == 200 and seg.json()['bundled_ready'] is True
