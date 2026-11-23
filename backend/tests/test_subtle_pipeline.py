"""Comprehensive unit and API tests for Stage 1 of the Subtle integration."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
import numpy as np
import pytest

from app import config, media
from app.main import create_app
from app.subtle import beats as subtle_beats
from app.subtle import cuts as subtle_cuts
from app.subtle import emphasis as subtle_emphasis
from app.subtle import group as subtle_group
from app.subtle import hinglish as subtle_hinglish
from app.subtle import legibility as subtle_legibility
from app.subtle import lyrics as subtle_lyrics
from app.subtle import pipeline as subtle_pipeline
from app.subtle import placement as subtle_placement
from app.subtle import shots as subtle_shots
from app.subtle import spoken_numbers as subtle_numbers
from app.subtle import styles as subtle_styles
from app.subtle import verify as subtle_verify


def _words(line: str, step: int = 300) -> list[dict]:
    return [{'text': t, 'startMs': k * step, 'endMs': k * step + step - 20} for k, t in enumerate(line.split())]


def _norm(line: str, lang: str | None = None, grouping: str = 'auto') -> str:
    return ' | '.join(w['text'] for w in subtle_numbers.normalise_numbers(_words(line), lang, grouping))


def test_all_39_styles_fonts_and_variants_load_cleanly():
    items = subtle_styles.list_styles()
    assert len(items) == 39
    fonts_dir = subtle_styles.subtle_fonts_dir()
    for item in items:
        st = subtle_styles.load_style(item['name'])
        font_file = st['font']['file']
        assert (fonts_dir / font_file).is_file(), f'{item["name"]} missing font {font_file}'
        for var in item['variants']:
            merged = subtle_styles.load_style(f'{item["name"]}:{var}')
            assert merged['variant'] == var

    tweaked = subtle_styles.load_style(
        'glass',
        tweaks={'accent': '#11AAFF', 'size_pct': 120, 'y': 64, 'case': 'upper', 'shadow': False},
    )
    assert tweaked['emphasis']['color'] == '#11AAFF'
    assert tweaked['position']['y'] == 64
    assert tweaked['case'] == 'upper'
    assert tweaked['shadow'] is None
    assert tweaked['legibility']['mode'] == 'none'

    with pytest.raises(ValueError):
        subtle_styles.load_style('../etc')
    with pytest.raises(ValueError):
        subtle_styles.load_style('glide', tweaks=None, custom_recipe={'font': {'family': 'Bad', 'file': '../evil.ttf', 'weight': 400}})


@pytest.mark.parametrize('line,lang,grouping,expected', [
    ('gold price is five thousand dollars now', 'en', 'auto', 'gold | price | is | $5,000 | now'),
    ('it costs 3.50 dollars', 'en', 'auto', 'it | costs | $3.50'),
    ('up 40 percent since 1991.', 'en', 'auto', 'up | 40% | since | 1991.'),
    ('in nineteen ninety one, India', 'en', 'auto', 'in | 1991, | India'),
    ('paanch lakh rupaye lagte hain', 'hi', 'auto', '₹5 lakh | lagte | hain'),
    ('1500000 rupees', 'en', 'auto', '₹15,00,000'),
    ('el oro cuesta cinco mil dólares ahora', 'es', 'auto', 'el | oro | cuesta | $5,000 | ahora'),
    ('en dos mil veinticuatro nosotros', 'es', 'auto', 'en | 2024 | nosotros'),
    ('harganya lima juta rupiah sekarang', 'id', 'auto', 'harganya | Rp5 juta | sekarang'),
    ('naik empat puluh persen sejak 1991.', 'id', 'auto', 'naik | 40% | sejak | 1991.'),
    ('sekitar dua puluh lima ribu orang', 'id', 'auto', 'sekitar | 25.000 | orang'),
    ('pada dua ribu dua puluh empat kami', 'id', 'auto', 'pada | 2024 | kami'),
])
def test_spoken_numbers_multilingual_including_indonesian(line, lang, grouping, expected):
    assert _norm(line, lang, grouping) == expected


def test_hinglish_transliteration():
    out = subtle_hinglish.romanize_text('दोस्तों आज हम 5000 बात करेंगे')
    assert 'doston' in out
    assert 'aaj' in out
    assert '5000' in out


def test_emphasis_rules_manual_overrides_and_big_words():
    ws = subtle_numbers.normalise_numbers(_words('harganya lima juta rupiah sekarang dan Meera pernah bangkrut'), 'id')
    cap, _ = subtle_group.build_captions_document(
        ws, width=1080, height=1920, fps=30.0, duration_ms=6000,
        style=subtle_styles.load_style('keyword'), language='id',
        emphasis='auto', numbers='indonesian',
    )
    highlighted = [w['text'] for p in cap['pages'] for w in p['words'] if w['emphasis']]
    assert any('Rp5 juta' in t for t in highlighted)
    assert cap.get('big')
    assert any(b['text'] == 'Rp5 juta' and b.get('front') is True for b in cap['big'])

    cap2, _ = subtle_group.build_captions_document(
        ws, width=1080, height=1920, fps=30.0, duration_ms=6000,
        style=subtle_styles.load_style('glide'), language='id',
        emphasis='auto', clear_auto_emphasis=True,
        manual_picks=[{'page': 0, 'word_index': 0, 'reason': 'hook'}],
    )
    picks2 = [(pi, wi, w['text']) for pi, p in enumerate(cap2['pages']) for wi, w in enumerate(p['words']) if w['emphasis']]
    assert len(picks2) == 1 and picks2[0][:2] == (0, 0)


def test_group_all_39_styles_cuts_srt_and_vtt():
    words = _words('I spent five thousand dollars in 1991 with Meera at Mumbai and never looked back', step=280)
    cuts = [1700]
    for st_meta in subtle_styles.list_styles():
        st = subtle_styles.load_style(st_meta['name'])
        cap, _ = subtle_group.build_captions_document(
            words, width=1080, height=1920, fps=30.0, duration_ms=len(words) * 300 + 800,
            style=st, cuts=cuts,
        )
        assert cap['pages'], st_meta['name']
        assert all(p['endMs'] > p['startMs'] for p in cap['pages'])
        assert all(a['endMs'] <= b['startMs'] for a, b in zip(cap['pages'], cap['pages'][1:]))
    srt = subtle_group.to_srt(cap['pages'])
    vtt = subtle_group.to_vtt(cap['pages'])
    assert '-->' in srt
    assert vtt.startswith('WEBVTT')


def test_beats_analysis_and_lyrics_alignment():
    # Generate a 6-second 120 BPM synthetic pulse train at 22050 Hz
    sr = subtle_beats.SR
    y = np.zeros(sr * 6, dtype=np.float32)
    for beat in range(12):
        idx = int(round(beat * 0.5 * sr))
        env = np.exp(-np.arange(800) / 140.0).astype(np.float32)
        y[idx:idx + 800] = np.sin(2 * math.pi * 110 * np.arange(800) / sr).astype(np.float32) * env
    res = subtle_beats.analyse_audio_bytes(y)
    assert res['bpm'] is not None and 115 <= res['bpm'] <= 125
    assert res['clarity'] >= 0.25 and len(res['times']) >= 8

    heard = _words('hello world we sing tonight forever more')
    lyr = subtle_lyrics.read_lyrics_text('Hello world\nWe sing tonight\nForevermore')
    aligned, stats = subtle_lyrics.align_lyrics(heard, lyr)
    assert stats['matched'] >= 4
    assert any(w.get('lyricLine') == 0 for w in aligned)


def test_legibility_palette_placement_shots_and_cv2_verify(tmp_path):
    st = subtle_styles.load_style('glide')
    ws = _words('Check $5,000 bright white scene text')
    cap, _ = subtle_group.build_captions_document(
        ws, width=1080, height=1920, fps=30.0, duration_ms=3000, style=st,
    )
    # Bright white + textured synthetic frames should trigger boost > 0.15 and extract a valid accent
    rng = np.random.default_rng(42)

    def fake_sampler(_t, sw, sh):
        base = np.full((sh, sw, 3), 230, dtype=np.uint8)
        noise = rng.integers(-25, 25, size=(sh, sw, 3))
        return np.clip(base.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    subtle_legibility.analyse(cap, st, None, sampler=fake_sampler)
    assert cap.get('palette') and cap['palette']['accent'].startswith('#')
    assert cap['legibilitySummary']['boosted'] >= 1

    # Placement avoids face in center band
    frames = [{'faces': [[0.3, 0.45, 0.4, 0.2]], 'hands': [], 'person': [0.5] * 24}]
    y_chosen = subtle_placement.choose(frames, (0.6, 0.12), 0.5, 20.0, 82.0, 52.0)
    assert abs(y_chosen - 52.0) > 10.0

    # Shots wide vs close-up
    wide_frames = [{'person': [0.0]*3 + [0.28]*6 + [0.4]*15, 'faces': [[0.4, 0.12, 0.2, 0.2]]}] * 4
    close_frames = [{'person': [0.85]*24, 'faces': [[0.2, 0.1, 0.6, 0.6]]}] * 4
    s_wide = subtle_shots.analyse_shot((0, 2000), wide_frames, 0.19)
    s_close = subtle_shots.analyse_shot((2000, 4000), close_frames, 0.19)
    assert s_wide['behind'] is True
    assert s_close['behind'] is False

    # OpenCV verify_export & contact sheet without Pillow
    preview_jpg = tmp_path / 'preview.jpg'

    def fake_grab(path, t, pix, w, h):
        if pix == 'rgba':
            img = np.zeros((h, w, 4), dtype=np.uint8)
            img[int(h * 0.48):int(h * 0.56), int(w * 0.25):int(w * 0.75)] = [255, 255, 255, 255]
            return img
        if pix == 'gray':
            return np.zeros((h, w), dtype=np.uint8)
        return np.full((h, w, 3), 24, dtype=np.uint8)

    qa = subtle_verify.verify_export(
        video_path=tmp_path / 'out.mp4',
        source_path=tmp_path / 'src.mp4',
        cap=cap,
        style=st,
        overlay_path=tmp_path / 'ov.mov',
        preview_out=preview_jpg,
        grab_fn=fake_grab,
    )
    assert qa['ok'] is True
    assert preview_jpg.is_file() and preview_jpg.read_bytes()[:3] == b'\xff\xd8\xff'


def test_api_subtle_styles_fonts_preview_and_audio_only_upload(tmp_path):
    ffmpeg = config.binary('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg required for audio-only probe test')
    wav = tmp_path / 'song.wav'
    subprocess.run(
        [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=3', str(wav)],
        check=True, capture_output=True, timeout=30,
    )
    app = create_app(tmp_path / 'jobs')
    with TestClient(app) as client:
        st_res = client.get('/api/subtle/styles')
        assert st_res.status_code == 200
        assert len(st_res.json()['styles']) == 39

        f_res = client.get('/api/caption-fonts/Inter-Bold.ttf')
        assert f_res.status_code == 200 and len(f_res.content) > 50000
        assert client.get('/api/caption-fonts/..evil.ttf').status_code in {404, 422}

        job = client.post('/api/jobs', json={'title': 'Audio Song'}).json()
        with wav.open('rb') as fp:
            up = client.post(f"/api/jobs/{job['id']}/source", files={'file': ('song.wav', fp, 'audio/wav')})
        assert up.status_code == 200, up.text
        assert up.json()['media']['audio_only'] is True

        tr = {
            'language': 'en',
            'segments': [{
                'start': 0.0, 'end': 2.5, 'text': 'Gold costs five thousand dollars today',
                'words': [
                    {'start': 0.1, 'end': 0.5, 'word': 'Gold'},
                    {'start': 0.5, 'end': 0.9, 'word': 'costs'},
                    {'start': 0.9, 'end': 1.3, 'word': 'five'},
                    {'start': 1.3, 'end': 1.7, 'word': 'thousand'},
                    {'start': 1.7, 'end': 2.1, 'word': 'dollars'},
                    {'start': 2.1, 'end': 2.4, 'word': 'today'},
                ],
            }],
        }
        assert client.post(f"/api/jobs/{job['id']}/transcript", files={'file': ('tr.json', json.dumps(tr), 'application/json')}).status_code == 200
        prev = client.post(
            f"/api/jobs/{job['id']}/subtle-preview",
            json={'start': 0.0, 'end': 2.5, 'aspect': 'vertical', 'options': {'style': 'downbeat', 'canvas': '1:1'}},
        )
        assert prev.status_code == 200, prev.text
        data = prev.json()
        assert data['captions']['video']['width'] == 1080 and data['captions']['video']['height'] == 1080
        assert '$5,000' in json.dumps(data['captions'])
