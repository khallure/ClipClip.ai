"""Provider selection, credential privacy and real queue/cache integration.

All credentials and transcripts below are authored fixtures. No live AI provider
is contacted and these tests do not read local configuration files themselves.
"""
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import analysis, config, media
import app.main as main_module
from app.main import create_app


GEMINI_KEY = 'dummy-gemini-key-for-offline-test'
OPENAI_KEY = 'dummy-openai-key-for-offline-test'
MEDIA = {'duration': 60, 'width': 640, 'height': 360, 'fps': 25,
         'video_codec': 'h264', 'audio_codec': 'aac', 'has_audio': True}
TRANSCRIPT = {'language': 'id', 'segments': [
    {'start': 0, 'end': 9, 'text': 'Ternyata kita tidak harus menunggu motivasi karena langkah kecil membuat pekerjaan lebih mudah dimulai.'},
    {'start': 9, 'end': 18, 'text': 'Jadi tulis satu kalimat terlebih dahulu lalu periksa hasilnya agar langkah berikutnya terlihat jelas.'},
]}
SETTINGS = {'duration_preset': '20', 'clip_count': 3}
TEST_HEADERS = {'x-podcut-run-token': 'owned-provider-test-session'}


@pytest.fixture(autouse=True)
def isolated_ai_environment(monkeypatch):
    # Phase 3A accepts cloud settings only from the private packaged profile.
    packaged = replace(config.RUNTIME, package=config.PROJECT, resources=config.PROJECT,
                       settings={'AI_PROVIDER':'heuristic'}, run_token=TEST_HEADERS['x-podcut-run-token'])
    monkeypatch.setattr(config, 'RUNTIME', packaged)
    monkeypatch.setattr(main_module, 'create_provisioner', lambda: SimpleNamespace(
        status=lambda: {'state':'missing'}, close=lambda: None))


@pytest.mark.parametrize('provider,gemini,openai,expected,configured', [
    ('gemini', GEMINI_KEY, OPENAI_KEY, 'gemini', True),
    ('gemini', GEMINI_KEY, '', 'gemini', True),
    ('openai', '', OPENAI_KEY, 'openai', True),
    ('auto', GEMINI_KEY, OPENAI_KEY, 'heuristic', False),
    ('gemini', '', OPENAI_KEY, 'gemini', False),
    ('openai', GEMINI_KEY, '', 'openai', False),
    ('heuristic', GEMINI_KEY, OPENAI_KEY, 'heuristic', False),
])
def test_provider_choice_is_explicit_and_never_uses_another_cloud_silently(
        monkeypatch, provider, gemini, openai, expected, configured):
    monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', provider)
    monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_API_KEY', gemini)
    monkeypatch.setitem(config.RUNTIME.settings, 'OPENAI_API_KEY', openai)
    selected = config.ai_configuration()
    assert selected.provider == expected
    assert selected.public()['configured'] is configured
    assert selected.public()['status'] == ('not_tested' if configured else 'missing_key')
    assert GEMINI_KEY not in repr(selected) and OPENAI_KEY not in repr(selected)
    encoded = json.dumps(selected.public())
    assert GEMINI_KEY not in encoded and OPENAI_KEY not in encoded
    assert set(selected.public()) == {'provider', 'model', 'configured', 'status'}


def test_gemini_model_default_and_runtime_configuration_changes(monkeypatch):
    monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_API_KEY', GEMINI_KEY)
    monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', 'gemini')
    first = config.ai_configuration()
    assert first.provider == 'gemini' and first.model == 'gemini-3.5-flash-lite'
    monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_MODEL', 'owned-config-fixture-model')
    assert config.ai_configuration().model == 'owned-config-fixture-model'
    monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', 'heuristic')
    assert config.ai_configuration().provider == 'heuristic'
    assert first.provider == 'gemini'  # An in-flight configuration stays stable.


def test_blank_credential_is_not_reported_as_configured(monkeypatch):
    monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', ' GEMINI ')
    monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_API_KEY', '  \t ')
    monkeypatch.setitem(config.RUNTIME.settings, 'OPENAI_API_KEY', OPENAI_KEY)
    selected = config.ai_configuration()
    assert selected.provider == 'gemini' and not selected.api_key
    assert selected.public()['status'] == 'missing_key'


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setenv('YOUTUBE_INGEST_ENABLED', 'false')
    monkeypatch.setattr(media, 'probe', Mock(return_value=dict(MEDIA)))
    monkeypatch.setattr(media, 'make_poster', Mock())
    monkeypatch.setattr(media, 'available_whisper', lambda: False)
    monkeypatch.setattr(media, 'transcribe', Mock(side_effect=RuntimeError('Unexpected transcription')))
    return create_app(tmp_path / 'data')


def prepared(client):
    response = client.post('/api/jobs', json={'title': 'Owned provider configuration fixture'})
    assert response.status_code == 201
    ident = response.json()['id']
    assert client.post(f'/api/jobs/{ident}/source', files={
        'file': ('owned.mp4', b'controlled-video-placeholder', 'video/mp4')}).status_code == 200
    assert client.post(f'/api/jobs/{ident}/transcript', files={
        'file': ('owned.json', json.dumps(TRANSCRIPT).encode(), 'application/json')}).status_code == 200
    return ident


def run_analysis(client, ident):
    response = client.post(f'/api/jobs/{ident}/analyze', json=SETTINGS)
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{ident}').json()
        if job['status'] in {'ready', 'blocked', 'failed', 'cancelled'}:
            assert job['status'] == 'ready', job.get('error')
            return job
        time.sleep(.01)
    pytest.fail('Provider analysis did not complete locally')


def response(mode='ai', provider='gemini'):
    return {'mode': mode, 'provider': provider, 'warnings': [], 'candidates': [],
            'coverage': [{'start': 0, 'end': 18, 'segment_count': 2}]}


def test_health_configuration_is_not_claimed_as_success_or_exposed_as_a_key(application, monkeypatch):
    monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_API_KEY', GEMINI_KEY)
    monkeypatch.setitem(config.RUNTIME.settings, 'OPENAI_API_KEY', OPENAI_KEY)
    monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', 'gemini')
    with TestClient(application, headers=TEST_HEADERS) as client:
        health = client.get('/api/health')
        assert health.status_code == 200
        assert health.json()['gemini'] is True and health.json()['openai'] is False
        assert health.json()['ai'] == {'provider': 'gemini', 'model': 'gemini-3.5-flash-lite',
                                       'configured': True, 'status': 'not_tested'}
        assert GEMINI_KEY not in health.text and OPENAI_KEY not in health.text


def test_gemini_pipeline_uses_selected_model_and_caches_by_provider_and_model(application, monkeypatch):
    def review(*args, **kwargs):
        return response(provider=kwargs.get('provider', 'openai'))
    reviewer = Mock(side_effect=review)
    monkeypatch.setattr(analysis, 'analyze_transcript', reviewer)
    monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_API_KEY', GEMINI_KEY)
    monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', 'gemini')
    monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_MODEL', 'owned-gemini-model')
    monkeypatch.setitem(config.RUNTIME.settings, 'OPENAI_API_KEY', OPENAI_KEY)
    monkeypatch.setitem(config.RUNTIME.settings, 'OPENAI_MODEL', 'owned-openai-model')
    with TestClient(application, headers=TEST_HEADERS) as client:
        ident = prepared(client)
        first = run_analysis(client, ident)
        assert first['analysis_mode'] == 'ai' and first['analysis_provider'] == 'gemini'
        assert reviewer.call_count == 1
        assert reviewer.call_args.kwargs['provider'] == 'gemini'
        assert reviewer.call_args.kwargs['model'] == 'owned-gemini-model'
        assert reviewer.call_args.kwargs['api_key'] == GEMINI_KEY
        run_analysis(client, ident)
        assert reviewer.call_count == 1

        monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', 'openai')
        assert run_analysis(client, ident)['analysis_provider'] == 'openai'
        assert reviewer.call_count == 2
        assert reviewer.call_args.kwargs.get('provider', 'openai') == 'openai'
        assert reviewer.call_args.kwargs['model'] == 'owned-openai-model'
        assert reviewer.call_args.kwargs['api_key'] == OPENAI_KEY

        monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', 'gemini')
        assert run_analysis(client, ident)['analysis_provider'] == 'gemini'
        assert reviewer.call_count == 2
        monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_MODEL', 'owned-gemini-model-2')
        run_analysis(client, ident)
        assert reviewer.call_count == 3
        assert reviewer.call_args.kwargs['model'] == 'owned-gemini-model-2'

        directory = application.state.store.directory(ident)
        assert len(list(directory.glob('analysis-*.json'))) == 3
        saved = '\n'.join(path.read_text(encoding='utf-8') for path in directory.glob('*.json'))
        assert GEMINI_KEY not in saved and OPENAI_KEY not in saved
        media.transcribe.assert_not_called()


def test_gemini_failure_is_labelled_heuristic_and_not_cached_as_a_success(application, monkeypatch):
    reviewer = Mock(return_value=response(mode='heuristic', provider='heuristic'))
    monkeypatch.setattr(analysis, 'analyze_transcript', reviewer)
    monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', 'gemini')
    monkeypatch.setitem(config.RUNTIME.settings, 'GEMINI_API_KEY', GEMINI_KEY)
    with TestClient(application, headers=TEST_HEADERS) as client:
        ident = prepared(client)
        for _ in range(2):
            result = run_analysis(client, ident)
            assert result['analysis_mode'] == 'heuristic' and result['analysis_provider'] == 'heuristic'
        assert reviewer.call_count == 2
        assert not list(application.state.store.directory(ident).glob('analysis-*.json'))


def test_explicit_gemini_without_key_passes_no_other_cloud_credential(application, monkeypatch):
    reviewer = Mock(return_value=response(mode='heuristic', provider='heuristic'))
    monkeypatch.setattr(analysis, 'analyze_transcript', reviewer)
    monkeypatch.setitem(config.RUNTIME.settings, 'AI_PROVIDER', 'gemini')
    monkeypatch.setitem(config.RUNTIME.settings, 'OPENAI_API_KEY', OPENAI_KEY)
    with TestClient(application, headers=TEST_HEADERS) as client:
        ident = prepared(client)
        result = run_analysis(client, ident)
        assert result['analysis_mode'] == 'heuristic'
        assert reviewer.call_args.kwargs['provider'] == 'gemini'
        assert reviewer.call_args.kwargs['api_key'] is None
        assert client.get('/api/health').json()['ai']['status'] == 'missing_key'
