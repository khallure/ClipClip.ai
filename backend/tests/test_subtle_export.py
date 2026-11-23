"""End-to-end tests for Subtle Remotion rendering and FFmpeg compositing."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

from app import config, media
from app.main import create_app
from app.subtle import renderer as subtle_renderer


def _make_synth_video(ffmpeg: str, path: Path, *, width: int = 540, height: int = 960, duration: float = 2.0) -> None:
    subprocess.run([
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', f'color=c=#141822:s={width}x{height}:r=24:d={duration}',
        '-f', 'lavfi', '-i', f'sine=frequency=220:sample_rate=16000:d={duration}',
        '-vf', (
            f'drawbox=x={width // 3}:y={int(height * 0.16)}:w={width // 3}:h={int(height * 0.20)}:color=#E2B397@1:t=fill,'
            f'drawbox=x={width // 5}:y={int(height * 0.36)}:w={int(width * 0.6)}:h={int(height * 0.50)}:color=#274B7A@1:t=fill'
        ),
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', str(path),
    ], check=True, capture_output=True, timeout=45)


def _make_synth_audio(ffmpeg: str, path: Path, *, duration: float = 2.0) -> None:
    subprocess.run([
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=44100:d={duration}',
        '-c:a', 'pcm_s16le', str(path),
    ], check=True, capture_output=True, timeout=30)


def _sample_transcript() -> dict:
    return {
        'language': 'en',
        'word_timestamps': True,
        'segments': [
            {
                'start': 0.1,
                'end': 1.9,
                'text': 'We built hundred percent local video magic',
                'words': [
                    {'word': 'We', 'start': 0.12, 'end': 0.32},
                    {'word': 'built', 'start': 0.34, 'end': 0.62},
                    {'word': '100%', 'start': 0.65, 'end': 1.05},
                    {'word': 'local', 'start': 1.08, 'end': 1.38},
                    {'word': 'magic', 'start': 1.40, 'end': 1.85},
                ],
            }
        ],
    }


def _require_toolchain():
    ffmpeg = config.binary('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg required for Subtle export tests')
    caps = subtle_renderer.renderer_capabilities()
    if not caps['available']:
        pytest.skip(f"Subtle renderer unavailable: {caps.get('reason')}")
    return ffmpeg


def test_renderer_capabilities_and_health(tmp_path):
    caps = subtle_renderer.renderer_capabilities()
    assert caps['node'] is True
    assert caps['script'] is True
    assert caps['bundle'] is True
    assert caps['browser'] is True
    assert caps['available'] is True

    app = create_app(tmp_path / 'jobs')
    with TestClient(app) as client:
        health = client.get('/api/health').json()
        assert health['renderer']['available'] is True
        styles_res = client.get('/api/subtle/styles').json()
        assert styles_res['renderer']['available'] is True
        assert len(styles_res['styles']) == 39


def test_subtle_export_glide_standard_and_cleanup(tmp_path):
    ffmpeg = _require_toolchain()
    src = tmp_path / 'src.mp4'
    _make_synth_video(ffmpeg, src, width=540, height=960, duration=2.0)
    src_info = media.probe(src)
    out_dir = tmp_path / 'export_glide'

    info = media.export_clip(
        src,
        out_dir,
        {
            'start': 0.0,
            'end': 1.6,
            'aspect': 'original',
            'crop_x': 0.5,
            'caption_style': 'subtle',
            'subtle': {'style': 'glide'},
        },
        _sample_transcript(),
        src_info,
        cache_dir=tmp_path / 'cache',
    )

    assert (out_dir / 'clip.mp4').is_file()
    assert (out_dir / 'captions.json').is_file()
    assert (out_dir / 'captions.srt').is_file()
    assert (out_dir / 'captions.vtt').is_file()
    assert (out_dir / 'preview.jpg').is_file()
    assert not any(p.name.startswith('_subtle_') for p in out_dir.iterdir())

    assert (info['width'], info['height']) == (540, 960)
    assert info['video_codec'] == 'h264'
    assert info['has_audio'] is True
    qa = info['subtle_qa']
    assert qa['has_preview'] is True
    assert any(fr.get('drawn') for fr in qa['frames'])

    cap_doc = json.loads((out_dir / 'captions.json').read_text(encoding='utf-8'))
    assert cap_doc['style'] == 'glide'
    assert len(cap_doc['pages']) >= 1


def test_subtle_export_glass_backdrop_blur(tmp_path):
    ffmpeg = _require_toolchain()
    src = tmp_path / 'glass.mp4'
    _make_synth_video(ffmpeg, src, width=540, height=960, duration=1.6)
    src_info = media.probe(src)
    out_dir = tmp_path / 'export_glass'

    info = media.export_clip(
        src,
        out_dir,
        {
            'start': 0.0,
            'end': 1.4,
            'aspect': 'original',
            'crop_x': 0.5,
            'caption_style': 'subtle',
            'subtle': {'style': 'glass'},
        },
        _sample_transcript(),
        src_info,
    )

    assert (out_dir / 'clip.mp4').is_file()
    assert (out_dir / 'preview.jpg').is_file()
    assert info['video_codec'] == 'h264'
    assert any(fr.get('drawn') for fr in info['subtle_qa']['frames'])


def test_subtle_export_keyword_behind_speaker(tmp_path):
    ffmpeg = _require_toolchain()
    src = tmp_path / 'speaker.mp4'
    _make_synth_video(ffmpeg, src, width=540, height=960, duration=1.8)
    src_info = media.probe(src)
    out_dir = tmp_path / 'export_keyword'

    info = media.export_clip(
        src,
        out_dir,
        {
            'start': 0.0,
            'end': 1.5,
            'aspect': 'original',
            'crop_x': 0.5,
            'caption_style': 'subtle',
            'subtle': {
                'style': 'keyword',
                'behind': 'on',
                'matte_quality': 'fast',
                'emphasis_words': ['magic'],
            },
        },
        _sample_transcript(),
        src_info,
    )

    assert (out_dir / 'clip.mp4').is_file()
    assert (out_dir / 'preview.jpg').is_file()
    assert info['video_codec'] == 'h264'
    assert not (out_dir / '_subtle_matte.mp4').exists()


def test_subtle_export_audio_only_downbeat_canvas(tmp_path):
    ffmpeg = _require_toolchain()
    src = tmp_path / 'beat.wav'
    _make_synth_audio(ffmpeg, src, duration=1.6)
    src_info = media.probe(src, allow_audio_only=True)
    assert src_info['audio_only'] is True
    out_dir = tmp_path / 'export_audio'

    info = media.export_clip(
        src,
        out_dir,
        {
            'start': 0.0,
            'end': 1.4,
            'aspect': 'original',
            'crop_x': 0.5,
            'caption_style': 'subtle',
            'subtle': {
                'style': 'downbeat',
                'canvas': '9:16',
                'canvas_bg': '#141824',
                'lyrics': 'We built magic',
            },
        },
        _sample_transcript(),
        src_info,
    )

    assert (out_dir / 'clip.mp4').is_file()
    assert (out_dir / 'preview.jpg').is_file()
    assert (info['width'], info['height']) == (1080, 1920)
    assert info['has_audio'] is True


def test_api_subtle_export_and_artifacts_download(tmp_path):
    ffmpeg = _require_toolchain()
    app = create_app(tmp_path / 'jobs')
    with TestClient(app) as client:
        created = client.post('/api/jobs', json={'title': 'Subtle API Export'}).json()
        jid = created['id']

        vid = tmp_path / 'api_src.mp4'
        _make_synth_video(ffmpeg, vid, width=540, height=960, duration=2.0)
        with vid.open('rb') as fh:
            up = client.post(f'/api/jobs/{jid}/source', files={'file': ('api_src.mp4', fh, 'video/mp4')})
        assert up.status_code == 200

        tr = client.post(
            f'/api/jobs/{jid}/transcript',
            files={'file': ('transcript.json', json.dumps(_sample_transcript()).encode('utf-8'), 'application/json')},
        )
        assert tr.status_code == 200

        exp = client.post(
            f'/api/jobs/{jid}/exports',
            json={
                'start': 0.0,
                'end': 1.4,
                'aspect': 'original',
                'caption_style': 'subtle',
                'subtle': {'style': 'glide'},
            },
        )
        assert exp.status_code == 202
        eid = exp.json()['exports'][-1]['id']

        deadline = time.monotonic() + 90
        export_rec = None
        while time.monotonic() < deadline:
            cur = client.get(f'/api/jobs/{jid}').json()
            export_rec = next(e for e in cur['exports'] if e['id'] == eid)
            if export_rec['status'] in {'ready', 'failed', 'cancelled'}:
                break
            time.sleep(0.25)

        assert export_rec is not None and export_rec['status'] == 'ready', export_rec
        assert export_rec.get('has_preview') is True
        assert export_rec.get('has_subtle_json') is True
        assert client.get(f'/api/jobs/{jid}/exports/{eid}/captions.json').status_code == 200
        assert client.get(f'/api/jobs/{jid}/exports/{eid}/preview.jpg').status_code == 200
        assert client.get(f'/api/jobs/{jid}/exports/{eid}/file').status_code == 200
