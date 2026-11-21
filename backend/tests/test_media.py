"""Real FFmpeg encodes: no YouTube, API credentials, downloaded test media or mocks."""
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import config, media
from app.main import create_app


@pytest.fixture(scope='module')
def source_video(tmp_path_factory):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('Real FFmpeg and FFprobe required for media integration tests.')
    directory = tmp_path_factory.mktemp('owned_synthetic')
    target = directory / 'synthetic.mp4'
    # Numbered frames change with time, making accurate seek/trim measurable.
    command = [config.binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
               '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=65',
               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=65',
               '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-shortest', str(target)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    return target


def decoded_frame(path, at):
    return subprocess.run([config.binary('ffmpeg'), '-v', 'error', '-ss', str(at), '-i', str(path),
                           '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                          capture_output=True, check=True, timeout=30).stdout


def test_real_native_precise_trim_and_audio(source_video, tmp_path):
    info = media.probe(source_video)
    settings = {'start': 12.25, 'end': 16.75, 'aspect': 'original', 'crop_x': .5, 'caption_style': 'none'}
    result = media.export_clip(source_video, tmp_path, settings, None, info)
    assert (result['width'], result['height']) == (320, 180)
    assert result['video_codec'] == 'h264' and result['audio_codec'] == 'aac'
    assert abs(result['duration'] - 4.5) < .1
    # Compare first encoded output frame to corresponding source frame.
    expected, actual = decoded_frame(source_video, 12.25), decoded_frame(tmp_path / 'clip.mp4', 0)
    mean_error = sum(abs(a-b) for a, b in zip(expected, actual)) / len(expected)
    assert len(expected) == len(actual) and mean_error < 8
    data = (tmp_path / 'clip.mp4').read_bytes()
    assert data.index(b'moov') < data.index(b'mdat')  # faststart
    audio = subprocess.run([config.binary('ffmpeg'), '-v', 'error', '-i', str(tmp_path / 'clip.mp4'),
                            '-vn', '-f', 's16le', '-ac', '1', '-ar', '8000', '-'],
                           capture_output=True, check=True, timeout=30).stdout
    assert len(audio) > 60000 and any(audio)


def test_real_vertical_and_burned_captions(source_video, tmp_path):
    transcript = {'segments': [{'start': 0, 'end': 3, 'text': 'Owned synthetic test. Uji subtitle Indonesia.'}]}
    result = media.export_clip(source_video, tmp_path,
                               {'start': .5, 'end': 2.5, 'aspect': 'vertical', 'crop_x': .7, 'caption_style': 'bold'},
                               transcript, media.probe(source_video))
    assert (result['width'], result['height']) == (1080, 1920)
    assert result['has_audio']
    assert abs(result['duration'] - 2) < .1
    assert '00:00:00,000 --> 00:00:02,000' in (tmp_path / 'captions.srt').read_text()


def wait_job(client, ident):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{ident}').json()
        if job['status'] not in {'queued', 'analyzing', 'transcribing', 'exporting'}:
            return job
        time.sleep(.05)
    raise AssertionError('Worker did not finish in 90 seconds')


def test_upload_transcript_analyze_preview_export_integration(source_video, tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    app = create_app(tmp_path / 'jobs')
    transcript = {'segments': [
        {'start': 0, 'end': 10, 'text': 'I remember my first project failed because I promised results before actually listening to the customer.'},
        {'start': 10, 'end': 25, 'text': 'For example, I started practicing every day and tracked my progress.'},
        {'start': 25, 'end': 40, 'text': 'The lesson is to start small and stay consistent. That is why practice matters.'},
        {'start': 40, 'end': 55, 'text': 'Terima kasih. Kita akan membahas cerita yang lain pada episode berikutnya.'},
        {'start': 55, 'end': 65, 'text': 'Semoga bermanfaat dan sampai jumpa.'},
    ]}
    with TestClient(app) as client:
        job = client.post('/api/jobs', json={'youtube_url': 'https://youtu.be/dQw4w9WgXcQ'}).json()
        ident = job['id']
        assert job['status'] == 'awaiting_upload' and job['media'] is None
        with source_video.open('rb') as data:
            uploaded = client.post(f'/api/jobs/{ident}/source', files={'file': ('synthetic.mp4', data, 'video/mp4')})
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()['media']['has_audio']
        assert '_source' not in uploaded.json()
        preview = client.get(f'/api/jobs/{ident}/media', headers={'Range': 'bytes=0-1023'})
        assert preview.status_code == 206 and len(preview.content) == 1024
        imported = client.post(f'/api/jobs/{ident}/transcript', files={'file': ('transcript.json', json.dumps(transcript), 'application/json')})
        assert imported.status_code == 200, imported.text
        queued = client.post(f'/api/jobs/{ident}/analyze', json={'clip_count': 3})
        assert queued.status_code == 200, queued.text
        ready = wait_job(client, ident)
        assert ready['status'] == 'ready', ready
        assert ready['analysis_mode'] == 'heuristic'
        assert ready['candidates'] and all(c['review_mode'] == 'heuristic' for c in ready['candidates'])
        assert 'WEBVTT' in client.get(f'/api/jobs/{ident}/captions.vtt').text
        exported = client.post(f'/api/jobs/{ident}/exports', json={'start': 1.25, 'end': 4.75, 'aspect': 'original'})
        assert exported.status_code == 202, exported.text
        ready = wait_job(client, ident)
        assert ready['status'] == 'ready', ready
        export_id = ready['exports'][-1]['id']
        assert ready['exports'][-1]['status'] == 'ready'
        response = client.get(f'/api/jobs/{ident}/exports/{export_id}/file')
        assert response.status_code == 200 and response.headers['content-type'] == 'video/mp4'
        assert b'ftyp' in response.content[:32]
        assert client.get(f'/api/jobs/{ident}/exports/{export_id}/captions.srt').status_code == 200
        # A settings change reuses the imported transcript and keeps the previous export.
        client.post(f'/api/jobs/{ident}/analyze', json={'clip_count': 4, 'target_duration': 30})
        assert wait_job(client, ident)['exports'][0]['status'] == 'ready'


def test_word_subtitles_do_not_invent_timing():
    segments = [{'start': 10, 'end': 12, 'text': 'Hello world.', 'words': [
        {'start': 10, 'end': 10.4, 'word': ' Hello'}, {'start': 11.1, 'end': 12, 'word': ' world.'}]}]
    out = media.subtitles(segments, start=11, end=11.5)
    assert '00:00:00,100 --> 00:00:00,500' in out
    assert 'world' in out and 'Hello' not in out


def test_cancelled_export_never_marks_file_ready(source_video, tmp_path):
    with pytest.raises(media.MediaCancelled):
        media.export_clip(source_video, tmp_path, {'start': 0, 'end': 5, 'aspect': 'original', 'crop_x': .5, 'caption_style': 'none'},
                          None, media.probe(source_video), lambda: True)
    assert not (tmp_path / 'clip.mp4').exists()


def test_srt_roundtrip_and_reject_invalid_minutes():
    segments = [{'start': .123, 'end': 2.456, 'text': 'Hello Indonesia.'}]
    assert media.parse_srt(media.subtitles(segments))['segments'] == segments
    with pytest.raises(ValueError):
        media.parse_srt('1\n00:90:00,000 --> 00:90:05,000\nBad\n')
