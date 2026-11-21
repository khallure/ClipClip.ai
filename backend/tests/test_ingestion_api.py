"""API orchestration with an approved injected provider and owned generated media.

No live platform URL is accessed. Source retrieval, cache, FFprobe and queue are
real; only provider transport and speech recognition use controlled fixtures.
"""
import json
import shutil
import threading
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import config, media
from app.ingestion import VideoSourceIngestor, IngestPolicy, IngestionBlocked, IngestionCancelled
from app.main import create_app

URL = 'https://www.youtube.com/watch?v=abcdefghijk'
SETTINGS = {'duration_preset': '20', 'clip_count': 3}
TEXT = 'Ternyata kita tidak perlu menunggu motivasi, karena langkah kecil membuat pekerjaan lebih mudah. Jadi mulai dari satu kalimat lalu periksa hasilnya.'
TRANSCRIPT = {'language': 'id', 'word_timestamps': False, 'segments': [
    {'start': 1, 'end': 18, 'text': TEXT}, {'start': 22, 'end': 39, 'text': TEXT.replace('motivasi', 'keberanian')}]}


def wait(client, ident, states=('ready','blocked','failed','cancelled')):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{ident}').json()
        if job['status'] in states:
            return job
        time.sleep(.01)
    pytest.fail(f'Job stuck: {job["status"]}')


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('YOUTUBE_INGEST_ENABLED', 'false')
    monkeypatch.setattr(media, 'transcribe', Mock(return_value=TRANSCRIPT))
    return create_app(tmp_path / 'data')


@pytest.fixture(scope='module')
def owned(tmp_path_factory):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('FFmpeg/FFprobe required')
    path = tmp_path_factory.mktemp('owned-ingest') / 'owned.mp4'
    media.run([media.tool('ffmpeg'), '-hide_banner','-loglevel','error','-y', '-f','lavfi','-i',
               'color=c=navy:s=320x180:r=25:d=42', '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=42',
               '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(path)])
    return path


def project(client, url=URL):
    response = client.post('/api/jobs', json={'youtube_url':url} if url else {})
    assert response.status_code == 201
    return response.json()['id']


def test_default_blocked_retains_url_settings_and_allows_upload_recovery(application, owned):
    with TestClient(application) as client:
        ident = project(client)
        assert client.post(f'/api/jobs/{ident}/ingest', json=SETTINGS).status_code == 200
        blocked = wait(client, ident)
        assert blocked['status'] == 'blocked' and 'Unggah' in blocked['error']
        assert blocked['youtube_url'] == URL and blocked['settings']['max_duration'] == 20
        assert blocked['media'] is None
        with owned.open('rb') as source:
            assert client.post(f'/api/jobs/{ident}/source', files={'file':('owned.mp4',source,'video/mp4')}).status_code == 200
        assert client.post(f'/api/jobs/{ident}/analyze', json=SETTINGS).status_code == 200
        ready = wait(client, ident)
        assert ready['status'] == 'ready' and ready['candidates']
        assert all(c['duration'] <= 20 for c in ready['candidates'])
        assert ready['youtube_url'] == URL


def test_approved_link_only_auto_analyzes_and_reuses_real_cache(application, owned):
    calls = []
    stages = []
    def worker(manifest, directory, **kwargs):
        calls.append(manifest['video_id'])
        kwargs['progress'](.5, 'retrieving_source: Mengambil sumber uji milik proyek.')
        shutil.copy2(owned, directory / 'source.mp4')
        return {'filename':'source.mp4','title':'Owned approved fixture','video_id':manifest['video_id']}
    ingestor = VideoSourceIngestor(application.state.store.root / 'source_cache', policy=IngestPolicy(
        enabled=True, approval_reference='injected-provider-only', approved_video_ids=frozenset({'abcdefghijk'})), worker=worker)
    application.state.pipeline.ingestor = ingestor
    update = application.state.store.update
    def record(ident, **fields):
        if 'status' in fields:
            stages.append(fields['status'])
        return update(ident, **fields)
    application.state.store.update = record
    with TestClient(application) as client:
        assert client.get('/api/health').json()['youtube_ingest']['available'] is True
        for cached in (False,True):
            ident = project(client)
            assert client.post(f'/api/jobs/{ident}/ingest',json=SETTINGS).status_code == 200
            ready = wait(client, ident)
            assert ready['status'] == 'ready', ready
            assert ready['source_cache_hit'] is cached
            assert ready['title'] == 'Owned approved fixture' and ready['candidates']
            assert client.get(f'/api/jobs/{ident}/media',headers={'Range':'bytes=0-99'}).status_code == 206
            source_path = application.state.store.directory(ident) / application.state.store.get(ident)['_source']
            assert media.probe(source_path)['has_audio']
            transcriptions = media.transcribe.call_count
            assert client.post(f'/api/jobs/{ident}/retry').status_code == 200
            assert wait(client,ident)['status'] == 'ready'
            assert media.transcribe.call_count == transcriptions
        assert calls == ['abcdefghijk']
        assert {'validating','getting_metadata','retrieving_source','verifying_media','transcribing','analyzing'} <= set(stages)
        public = client.get('/api/health').text
        assert 'injected-provider-only' not in public and 'abcdefghijk' not in public


def test_cancellation_and_retry_keep_identity_and_prevent_mutation(application):
    entered = threading.Event()
    class Controlled:
        def capabilities(self): return {'enabled':True,'available':True,'reason':'test'}
        def ingest(self, url, directory, *, cancelled, progress, **kwargs):
            progress(.2,'retrieving_source: controlled')
            entered.set()
            while not cancelled(): time.sleep(.01)
            raise IngestionCancelled('cancelled')
    application.state.pipeline.ingestor = Controlled()
    with TestClient(application) as client:
        ident = project(client)
        client.post(f'/api/jobs/{ident}/ingest',json=SETTINGS)
        assert entered.wait(3)
        assert client.delete(f'/api/jobs/{ident}').status_code == 409
        assert client.post(f'/api/jobs/{ident}/ingest',json=SETTINGS).status_code == 409
        client.post(f'/api/jobs/{ident}/cancel')
        assert wait(client,ident)['status'] == 'cancelled'
        application.state.pipeline.ingestor.ingest = Mock(side_effect=IngestionBlocked('Upload recovery'))
        assert client.post(f'/api/jobs/{ident}/retry').status_code == 200
        assert wait(client,ident)['status'] == 'blocked'
        assert client.get(f'/api/jobs/{ident}').json()['youtube_url'] == URL


def test_no_url_or_invalid_settings_do_not_queue(application):
    with TestClient(application) as client:
        ident = project(client,None)
        assert client.post(f'/api/jobs/{ident}/ingest',json=SETTINGS).status_code == 422
        assert client.post(f'/api/jobs/{ident}/ingest',json={'duration_preset':'19'}).status_code == 422
        assert client.get(f'/api/jobs/{ident}').json()['status'] == 'awaiting_upload'
