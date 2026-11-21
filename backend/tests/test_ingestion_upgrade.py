"""Owned-media integration for URL intake through the unchanged V1 clipper.

Provider transport and ASR are controlled fixtures. FFprobe, source cache/copy,
the queue, editorial selection, Poppins renderer, FFmpeg encode and HTTP range
responses are real. No external URL or live credential is used by this suite.
"""
from array import array
import copy
import hashlib
import json
import math
import shutil
import subprocess
import threading
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import config, media
from app.ingestion import IngestPolicy, IngestionBlocked, IngestionCancelled, VideoSourceIngestor
from app.main import create_app
from app.models import validate_transcript


VIDEO_ID = 'ownedTST_02'
URL = f'https://www.youtube.com/watch?v={VIDEO_ID}'
SETTINGS = {'duration_preset': '20', 'clip_count': 3}


def _run(*arguments):
    return subprocess.run([media.tool('ffmpeg'), '-hide_banner', '-loglevel', 'error',
                           '-nostdin', *map(str, arguments)], capture_output=True,
                          check=True, timeout=90).stdout


@pytest.fixture(scope='module')
def owned_media(tmp_path_factory):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('Real FFmpeg/FFprobe are required for the ingestion export integration.')
    path = tmp_path_factory.mktemp('owned_ingestion_upgrade') / 'owned.mp4'
    _run('-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=42',
         '-f', 'lavfi', '-i', 'aevalsrc=0.2*sin(2*PI*(330*t+15*t*t)):s=48000:d=42',
         '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-threads', '2',
         '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-shortest', path)
    return path


def _authored_transcript():
    """Explicit fixture word boundaries, not inferred timestamps or live ASR."""
    authored = [
        (2.0, 19.5, 'Ternyata kita tidak perlu menunggu motivasi karena langkah kecil membuat pekerjaan lebih mudah, jadi mulailah dengan satu kalimat dan periksa hasilnya sebelum menambah target.'),
        (23.0, 40.0, 'Pelajarannya bukan bekerja lebih lama, karena kita justru lebih mudah melihat kesalahan setelah istirahat singkat, jadi berhenti sebentar lalu periksa ulang pekerjaan sebelum mengirim hasilnya.'),
    ]
    segments = []
    for start, end, text in authored:
        tokens = text.split()
        words = [{'start': start+(end-start)*i/len(tokens),
                  'end': start+(end-start)*(i+1)/len(tokens), 'word': token}
                 for i, token in enumerate(tokens)]
        segments.append({'start': start, 'end': end, 'text': text, 'words': words})
    return validate_transcript({'language': 'id', 'segments': segments}, 42)


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'heuristic')
    monkeypatch.setattr(media, 'transcribe', Mock(side_effect=lambda *args, **kwargs: copy.deepcopy(_authored_transcript())))
    return create_app(tmp_path / 'data')


def _project(client, url=URL):
    result = client.post('/api/jobs', json={'youtube_url': url})
    assert result.status_code == 201, result.text
    return result.json()['id']


def _wait(client, ident, *, automatic_export=False):
    deadline = time.monotonic()+35
    job = None
    while time.monotonic() < deadline:
        response = client.get(f'/api/jobs/{ident}')
        assert response.status_code == 200
        job = response.json()
        if job['status'] in {'blocked', 'failed', 'cancelled'}:
            return job
        if job['status'] == 'ready' and (not automatic_export or (job.get('auto_export') or {}).get('status') == 'ready'):
            return job
        time.sleep(.01)
    pytest.fail(f'Intake did not complete: {job}')


def _metadata(path):
    return {'title': 'Owned ingestion fixture', 'duration': 42, 'width': 320, 'height': 180, 'fps': 30,
            'formats': [{'id': 'owned-30', 'height': 180, 'width': 320, 'fps': 30,
                         'video_codec': 'h264', 'audio_codec': 'aac', 'ext': 'mp4',
                         'size_bytes': path.stat().st_size, 'has_video': True, 'has_audio': True}]}


def _provider(path, calls):
    def retrieve(manifest, directory, *, progress, **kwargs):
        calls.append(manifest['video_id'])
        size = path.stat().st_size
        progress(.4, 'retrieving_source: Mengambil sumber uji.',
                 {'phase': 'download', 'downloaded_bytes': size//2, 'total_bytes': size,
                  'total_bytes_estimate': None, 'speed_bps': 4096, 'eta_seconds': 2,
                  'percent': 50, 'metadata': _metadata(path),
                  'signed_url': 'https://cdn.invalid/private?token=never-public'})
        progress(None, 'merging_media: Menggabungkan stream sumber.',
                 {'phase': 'merge', 'downloaded_bytes': size, 'total_bytes': size,
                  'speed_bps': None, 'eta_seconds': None, 'percent': None})
        shutil.copy2(path, directory/'source.mp4')
        return {'filename': 'source.mp4', 'title': 'Owned ingestion fixture',
                'video_id': manifest['video_id'], 'metadata': _metadata(path)}
    return retrieve


def _ingestor(application, worker, *, enabled=True):
    # Empty legacy notes/lists are deliberate: valid URL access is decided by the
    # configured provider, never by an arbitrary unverified approval string.
    return VideoSourceIngestor(application.state.store.root/'source_cache',
                               policy=IngestPolicy(enabled=enabled), worker=worker)


def _frame(path, moment):
    return _run('-ss', f'{moment:.9f}', '-i', path, '-frames:v', '1',
                '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-')


def _audio(path, start, duration):
    values = array('h')
    values.frombytes(_run('-ss', f'{start:.9f}', '-i', path, '-t', f'{duration:.9f}',
                         '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'))
    return values


def _correlation(a, b):
    return sum(x*y for x, y in zip(a, b))/math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))


def test_link_only_to_automatic_poppins_mp4_and_reused_source(application, owned_media):
    calls, snapshots = [], []
    application.state.pipeline.ingestor = _ingestor(application, _provider(owned_media, calls))
    original_update = application.state.store.update

    def capture(ident, **fields):
        snapshots.append(copy.deepcopy(fields))
        return original_update(ident, **fields)

    application.state.store.update = capture
    with TestClient(application) as client:
        ident = _project(client)
        response = client.post(f'/api/jobs/{ident}/ingest', json={**SETTINGS, 'auto_export': True,
            'output': {'aspect': 'original', 'caption_style': 'poppins_highlight'}})
        assert response.status_code == 200, response.text
        ready = _wait(client, ident, automatic_export=True)
        assert ready['status'] == 'ready', ready
        assert ready['auto_export']['status'] == 'ready'
        assert ready['candidates'] and all(c['duration'] <= 20 for c in ready['candidates'])
        assert ready['source_cache_hit'] is False and not ready.get('source_error')
        assert (ready['media']['width'], ready['media']['height']) == (320, 180)
        assert ready['ingestion']['phase'] == 'ready'
        assert ready['ingestion']['cache_hit'] is False
        transfers = [s['ingestion'] for s in snapshots if (s.get('ingestion') or {}).get('phase') == 'download']
        assert any(s['downloaded_bytes'] == owned_media.stat().st_size//2 and
                   s['total_bytes'] == owned_media.stat().st_size and s['speed_bps'] == 4096 and
                   s['eta_seconds'] == 2 for s in transfers)
        assert any((s.get('ingestion') or {}).get('phase') == 'merge' for s in snapshots)
        public = json.dumps(ready)
        assert 'never-public' not in public and 'signed_url' not in public

        directory = application.state.store.directory(ident)
        stored = application.state.store.get(ident)
        source = directory/stored['_source']
        assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(owned_media.read_bytes()).digest()
        preview_response = client.get(f'/api/jobs/{ident}/media', headers={'Range': 'bytes=0-99'})
        assert preview_response.status_code == 206 and len(preview_response.content) == 100
        export_id = ready['auto_export']['export_id']
        exported = next(item for item in ready['exports'] if item['id'] == export_id)
        assert exported['status'] == 'ready' and exported['caption_style'] == 'poppins_highlight'
        assert exported['start'] == ready['candidates'][0]['start']
        assert exported['end'] == ready['candidates'][0]['end']
        output_directory = directory/'exports'/export_id
        target = output_directory/'clip.mp4'
        info = media.probe(target)
        assert info['video_codec'] == 'h264' and info['audio_codec'] == 'aac' and info['has_audio']
        assert (info['width'], info['height'], info['fps']) == (320, 180, 30)
        duration = exported['end']-exported['start']
        assert abs(info['duration']-duration) <= 1/30+.000001
        assert info['duration'] <= 20+1/30+.000001
        assert client.get(f'/api/jobs/{ident}/exports/{export_id}/file', headers={'Range':'bytes=0-127'}).status_code == 206
        caption = client.post(f'/api/jobs/{ident}/caption-preview', json={
            'start': exported['start'], 'end': exported['end'], 'aspect': 'original'})
        assert caption.status_code == 200, caption.text
        assert caption.json() == json.loads((output_directory/'caption_document.json').read_text(encoding='utf-8'))
        assert 'Poppins' in client.get(f'/api/jobs/{ident}/exports/{export_id}/captions.ass').text
        assert client.get(f'/api/jobs/{ident}/exports/{export_id}/captions.srt').status_code == 200
        assert client.get(f'/api/jobs/{ident}/exports/{export_id}/captions.vtt').status_code == 200

        # The top half excludes caption pixels: actual source framing is retained.
        actual_frame = _frame(target, 0)
        top = 320*90*3
        errors = [sum(abs(a-b) for a,b in zip(actual_frame[:top], _frame(source, moment)[:top]))/top
                  for moment in (exported['start'], exported['start']+1/30)]
        assert min(errors) < 8
        assert sum(abs(a-b) for a,b in zip(actual_frame[:top], _frame(source, 0)[:top]))/top > 10
        actual_audio = _audio(target, 0, duration)
        source_audio = _audio(source, exported['start'], duration)
        assert len(actual_audio) >= round(duration*8000)-2
        assert math.sqrt(sum(v*v for v in actual_audio[-160:])/160) > 1000
        for offset in (800, int((duration-.12)*8000)):
            assert _correlation(actual_audio[offset:offset+160], source_audio[offset:offset+160]) > .95

        transcriptions = media.transcribe.call_count
        assert client.post(f'/api/jobs/{ident}/analyze', json={**SETTINGS, 'duration_preset':'30'}).status_code == 200
        assert _wait(client, ident)['status'] == 'ready'
        assert media.transcribe.call_count == transcriptions and calls == [VIDEO_ID]
        assert source.exists()

        second_id = _project(client)
        assert client.post(f'/api/jobs/{second_id}/ingest', json=SETTINGS).status_code == 200
        second = _wait(client, second_id)
        assert second['status'] == 'ready', second
        assert second['source_cache_hit'] is True and second['ingestion']['cache_hit'] is True
        assert calls == [VIDEO_ID]


def test_actual_provider_denial_keeps_url_and_upload_recovers(application, owned_media):
    def denied(*args, **kwargs):
        raise IngestionBlocked('Sumber memerlukan autentikasi. Gunakan unggah video.',
                               code='access', retryable=False, recovery='upload')
    application.state.pipeline.ingestor = _ingestor(application, denied)
    with TestClient(application) as client:
        ident = _project(client)
        assert client.post(f'/api/jobs/{ident}/ingest', json=SETTINGS).status_code == 200
        failed = _wait(client, ident)
        assert failed['status'] == 'blocked' and failed['youtube_url'] == URL
        assert failed['settings']['max_duration'] == 20 and failed['media'] is None
        assert failed['source_error'] == {'code':'access', 'retryable':False, 'recovery':'upload'}
        assert not list((application.state.store.root/'source_cache').glob('.partial-*'))
        with owned_media.open('rb') as source:
            response = client.post(f'/api/jobs/{ident}/source', files={'file':('owned.mp4', source, 'video/mp4')})
        assert response.status_code == 200, response.text
        uploaded = response.json()
        assert not uploaded.get('source_error') and uploaded.get('ingestion') is None
        assert uploaded['source_cache_hit'] is False and uploaded['youtube_url'] == URL
        assert client.post(f'/api/jobs/{ident}/analyze',json=SETTINGS).status_code == 200
        assert _wait(client,ident)['candidates']


def test_cancel_stops_partial_copy_without_discarding_project(application):
    entered = threading.Event()
    def blocked_worker(manifest, directory, *, progress, cancelled, **kwargs):
        (directory/'source.mp4.part').write_bytes(b'owned incomplete fixture')
        progress(None, 'retrieving_source: Pengambilan belum selesai.', {'phase':'download',
            'downloaded_bytes':24, 'total_bytes':None, 'speed_bps':None, 'eta_seconds':None, 'percent':None})
        entered.set()
        while not cancelled():
            time.sleep(.01)
        raise IngestionCancelled('Pengambilan dibatalkan.')
    application.state.pipeline.ingestor = _ingestor(application, blocked_worker)
    with TestClient(application) as client:
        ident = _project(client)
        client.post(f'/api/jobs/{ident}/ingest', json=SETTINGS)
        assert entered.wait(3)
        assert client.delete(f'/api/jobs/{ident}').status_code == 409
        assert client.post(f'/api/jobs/{ident}/cancel').status_code == 200
        cancelled = _wait(client,ident)
        assert cancelled['status'] == 'cancelled' and cancelled['youtube_url'] == URL
        assert cancelled['media'] is None and cancelled['ingestion']['phase'] == 'cancelled'
        assert not list((application.state.store.root/'source_cache').glob('.partial-*'))


def test_disabled_feature_does_not_reuse_cache_or_call_transport(application, owned_media):
    calls = []
    worker = _provider(owned_media,calls)
    application.state.pipeline.ingestor = _ingestor(application,worker)
    with TestClient(application) as client:
        first = _project(client)
        client.post(f'/api/jobs/{first}/ingest',json=SETTINGS)
        assert _wait(client,first)['status'] == 'ready'
        application.state.pipeline.ingestor = _ingestor(application,worker,enabled=False)
        second = _project(client)
        client.post(f'/api/jobs/{second}/ingest',json=SETTINGS)
        failed = _wait(client,second)
        assert failed['status'] == 'blocked' and failed['media'] is None
        assert failed['source_error']['code'] == 'disabled'
        assert calls == [VIDEO_ID]


@pytest.mark.parametrize('url', ['file:///C:/Windows/win.ini', 'https://127.0.0.1/video.mp4',
                                 'https://www.youtube.com.evil.example/watch?v=ownedTST_02'])
def test_untrusted_provider_urls_never_create_or_queue_a_job(application,url):
    application.state.pipeline.ingestor = _ingestor(application,Mock(side_effect=AssertionError('No transport expected')))
    with TestClient(application) as client:
        response = client.post('/api/jobs',json={'youtube_url':url})
        assert response.status_code == 422
        assert client.get('/api/jobs').json() == []
