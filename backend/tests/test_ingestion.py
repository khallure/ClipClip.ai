"""No live provider calls. Controlled workers use generated, owned local media."""
import dataclasses
import json
import shutil
import socket
import subprocess
import sys
import time
import types
import urllib.request
from pathlib import Path

import pytest

from app import config, ingestion, ingestion_worker, media
from app.ingestion import IngestionBlocked, IngestionCancelled, IngestPolicy, VideoSourceIngestor
from app.ingestion_worker import (SourceRejected, _PinnedHTTPSConnection, _classify,
                                  downloader_options, public_addresses, validate_metadata,
                                  validate_provider_url)

VIDEO_ID = 'ownedTST_01'
OTHER_ID = 'ownedTST_02'
URL = f'https://www.youtube.com/watch?v={VIDEO_ID}'
MEDIA = {'duration': 2, 'width': 320, 'height': 180, 'fps': 24,
         'video_codec': 'h264', 'audio_codec': 'aac', 'has_audio': True}


def policy(**changes):
    return dataclasses.replace(IngestPolicy(enabled=True, approval_reference='private written approval reference',
                                            approved_video_ids=frozenset({VIDEO_ID})), **changes)


def local_worker(calls, contents=b'owned media test data', **result_changes):
    def worker(manifest, stage, **kwargs):
        calls.append(manifest)
        (stage / 'source.mp4').write_bytes(contents)
        if kwargs.get('progress'):
            kwargs['progress'](.5, 'retrieving_source: Controlled owned source.')
        return {'filename': 'source.mp4', 'title': 'Owned source', 'video_id': manifest['video_id'], **result_changes}
    return worker


def make_job(tmp_path, name='job'):
    result = tmp_path / name
    result.mkdir()
    return result


@pytest.mark.parametrize('changes,expected', [
    ({'enabled': False}, 'belum diaktifkan'),
])
def test_disabled_adapter_stops_before_any_provider_or_cache_access(tmp_path, changes, expected):
    calls = []
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(**changes), worker=local_worker(calls), probe=lambda _: MEDIA)
    with pytest.raises(IngestionBlocked, match=expected):
        adapter.ingest(URL, make_job(tmp_path))
    assert calls == []
    assert not (tmp_path / 'cache').exists()


@pytest.mark.parametrize('changes', [
    {'approval_reference': '', 'approved_video_ids': frozenset()},
    {'approval_reference': 'arbitrary text is not platform proof', 'approved_video_ids': frozenset({OTHER_ID})},
])
def test_legacy_reference_and_whitelist_are_not_download_gates_or_permission_proof(tmp_path, changes):
    calls = []
    adapter = VideoSourceIngestor(tmp_path/'cache', policy=policy(**changes), worker=local_worker(calls), probe=lambda _: MEDIA)
    result = adapter.ingest(URL, make_job(tmp_path))
    assert result['source_path'].is_file() and len(calls) == 1
    assert 'approval_reference' not in result and 'approved_video_ids' not in result


@pytest.mark.parametrize('url', [
    'http://www.youtube.com/watch?v=ownedTST_01',
    'https://youtube.com.attacker.invalid/watch?v=ownedTST_01',
    'https://user:secret@youtube.com/watch?v=ownedTST_01',
    'https://youtube.com:8443/watch?v=ownedTST_01',
    'https://127.0.0.1/watch?v=ownedTST_01',
    'file:///source.mp4', 'https://youtube.com/playlist?list=ownedTST_01',
    'https://youtu.be/invalid', 'https://youtu.be/ownedTST_01/second',
])
def test_invalid_url_is_not_resolved_or_fetched(tmp_path, url, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_a, **_k: pytest.fail('Input validation must not resolve DNS'))
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=lambda *_a, **_k: pytest.fail('No provider call'))
    with pytest.raises(IngestionBlocked, match='URL HTTPS'):
        adapter.ingest(url, make_job(tmp_path))


def test_environment_is_default_off_and_read_on_each_call(monkeypatch):
    for name in ('YOUTUBE_INGEST_ENABLED', 'YOUTUBE_APPROVAL_REFERENCE', 'YOUTUBE_APPROVED_VIDEO_IDS'):
        monkeypatch.delenv(name, raising=False)
    assert IngestPolicy.from_env().gate(VIDEO_ID) == 'disabled'
    adapter = VideoSourceIngestor(worker=lambda *_a, **_k: None)
    assert adapter.capabilities()['available'] is False
    monkeypatch.setenv('YOUTUBE_INGEST_ENABLED', 'true')
    monkeypatch.setenv('YOUTUBE_APPROVAL_REFERENCE', 'sensitive-reference-do-not-expose')
    monkeypatch.setenv('YOUTUBE_APPROVED_VIDEO_IDS', f'{VIDEO_ID}, {OTHER_ID}; * https://evil.invalid')
    assert IngestPolicy.from_env().approved_video_ids == frozenset()
    assert IngestPolicy.from_env().approval_reference == ''
    capability = adapter.capabilities()
    assert capability['enabled'] is True and capability['available'] is True
    assert 'sensitive-reference' not in json.dumps(capability) and VIDEO_ID not in json.dumps(capability)
    monkeypatch.delenv('YOUTUBE_APPROVAL_REFERENCE')
    monkeypatch.delenv('YOUTUBE_APPROVED_VIDEO_IDS')
    assert adapter.capabilities()['available'] is True
    monkeypatch.setenv('YOUTUBE_INGEST_ENABLED', 'false')
    assert adapter.capabilities()['available'] is False


def test_capabilities_reports_missing_optional_dependency_without_import_or_secrets(monkeypatch):
    monkeypatch.setattr(ingestion, '_dependency_ready', lambda: False)
    adapter = VideoSourceIngestor(policy=policy())
    assert adapter.capabilities() == {'enabled': True, 'available': False, 'reason': ingestion._MESSAGES['dependency']}
    assert policy().approval_reference not in repr(policy())
    monkeypatch.setattr(ingestion, '_dependency_ready', lambda: True)
    monkeypatch.setattr(config, 'binary', lambda _name: None)
    assert adapter.capabilities()['reason'] == ingestion._MESSAGES['tools']


def test_verified_local_source_cache_and_independent_job_copy(tmp_path):
    calls, events, reservations = [], [], []
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=local_worker(calls), probe=lambda _: dict(MEDIA))
    first_job = make_job(tmp_path, 'first')
    first = adapter.ingest(f'https://youtu.be/{VIDEO_ID}?list=ignored', first_job,
                           progress=lambda *event: events.append(event), ensure_capacity=reservations.append)
    assert first['source_path'].parent == first_job
    assert first['source_path'].read_bytes() == b'owned media test data'
    assert first['media'] == MEDIA and not first['cache_hit']
    assert first['video_id'] == VIDEO_ID and first['title'] == 'Owned source'
    second_job = make_job(tmp_path, 'second')
    second = adapter.ingest(URL, second_job)
    assert second['cache_hit'] and len(calls) == 1
    assert calls[0]['url'] == URL
    assert 'approval' not in json.dumps(calls[0])
    assert any(event[1].startswith('getting_metadata:') for event in events)
    assert any(event[1].startswith('verifying_media:') for event in events)
    assert events[-1][0] == 1 and reservations[-1] == len(b'owned media test data')
    cache_entry = next((tmp_path / 'cache').glob('youtube-*'))
    shutil.rmtree(cache_entry)
    assert first['source_path'].is_file() and second['source_path'].is_file()


def test_cached_file_is_reprobed_and_hash_checked(tmp_path):
    calls, probes = [], []
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=local_worker(calls),
                                 probe=lambda path: probes.append(path) or dict(MEDIA))
    adapter.ingest(URL, make_job(tmp_path, 'first'))
    cached_source = next((tmp_path / 'cache').glob('youtube-*/source.mp4'))
    cached_source.write_bytes(b'tampered cached media')
    result = adapter.ingest(URL, make_job(tmp_path, 'second'))
    assert not result['cache_hit'] and len(calls) == 2
    assert len(probes) >= 3


def test_disabling_feature_does_not_reuse_source_cache(tmp_path, monkeypatch):
    for name, value in [('YOUTUBE_INGEST_ENABLED', 'true'), ('YOUTUBE_APPROVAL_REFERENCE', 'approval'),
                        ('YOUTUBE_APPROVED_VIDEO_IDS', VIDEO_ID)]:
        monkeypatch.setenv(name, value)
    calls = []
    adapter = VideoSourceIngestor(tmp_path / 'cache', worker=local_worker(calls), probe=lambda _: dict(MEDIA))
    adapter.ingest(URL, make_job(tmp_path, 'first'))
    monkeypatch.setenv('YOUTUBE_INGEST_ENABLED', 'false')
    with pytest.raises(IngestionBlocked, match='belum diaktifkan'):
        adapter.ingest(URL, make_job(tmp_path, 'second'))
    assert len(calls) == 1


@pytest.mark.parametrize('result', [{'filename': '../outside.mp4'}, {'filename': 'source.f123.mp4'},
                                  {'video_id': OTHER_ID}, {'filename': 'source.m3u8'}])
def test_provider_result_cannot_escape_staging_or_switch_identity(tmp_path, result):
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=local_worker([], **result), probe=lambda _: dict(MEDIA))
    job = make_job(tmp_path)
    with pytest.raises(IngestionBlocked):
        adapter.ingest(URL, job)
    assert list(job.iterdir()) == []
    assert list((tmp_path / 'cache').iterdir()) == []


@pytest.mark.parametrize('change', [{'has_audio': False}, {'audio_codec': None}, {'duration': 999999},
                                  {'fps': 0}, {'fps': float('nan')}, {'width': 1}, {'height': 4320}])
def test_actual_probe_rejects_missing_audio_and_bad_media_before_cache_publish(tmp_path, change):
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=local_worker([]),
                                 probe=lambda _: {**MEDIA, **change})
    with pytest.raises(IngestionBlocked, match='video dan audio'):
        adapter.ingest(URL, make_job(tmp_path))
    assert list((tmp_path / 'cache').iterdir()) == []


def test_quota_rejected_before_publish_and_capacity_error_preserves_job(tmp_path):
    job = make_job(tmp_path)
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(max_bytes=3), worker=local_worker([]), probe=lambda _: MEDIA)
    with pytest.raises(IngestionBlocked, match='batas ukuran'):
        adapter.ingest(URL, job)
    assert not list(job.iterdir()) and not list((tmp_path / 'cache').iterdir())
    def full(_extra):
        raise ValueError('internal disk usage')
    with pytest.raises(IngestionBlocked, match='batas ukuran'):
        adapter.ingest(URL, job, ensure_capacity=full)


def test_existing_job_source_never_overwritten(tmp_path):
    job = make_job(tmp_path)
    target = job / f'source-{VIDEO_ID}.mp4'
    target.write_bytes(b'previous project source')
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=local_worker([]), probe=lambda _: MEDIA)
    with pytest.raises(IngestionBlocked):
        adapter.ingest(URL, job)
    assert target.read_bytes() == b'previous project source'


def test_cancel_removes_only_partial_output_and_preserves_prior_job(tmp_path):
    job = make_job(tmp_path)
    previous = job / 'previous.txt'
    previous.write_text('preserved')
    state = {'cancel': False}
    def worker(manifest, stage, **_kwargs):
        (stage / 'source.mp4').write_bytes(b'owned')
        state['cancel'] = True
        return {'filename': 'source.mp4', 'video_id': manifest['video_id']}
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=worker, probe=lambda _: MEDIA)
    with pytest.raises(IngestionCancelled):
        adapter.ingest(URL, job, cancelled=lambda: state['cancel'])
    assert previous.read_text() == 'preserved'
    assert list((tmp_path / 'cache').iterdir()) == []


def test_cache_expiry_evicts_only_owned_entries_and_no_job_source(tmp_path):
    calls = []
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=local_worker(calls), probe=lambda _: MEDIA)
    first = adapter.ingest(URL, make_job(tmp_path, 'first'))
    descriptor = next((tmp_path / 'cache').glob('youtube-*/metadata.json'))
    metadata = json.loads(descriptor.read_text())
    metadata['completed_at'] = time.time() - 8 * 86400
    descriptor.write_text(json.dumps(metadata))
    unrelated = tmp_path / 'cache' / 'unrelated'
    unrelated.mkdir()
    (unrelated / 'keep.txt').write_text('keep')
    result = adapter.ingest(URL, make_job(tmp_path, 'second'))
    assert not result['cache_hit'] and len(calls) == 2
    assert first['source_path'].is_file() and (unrelated / 'keep.txt').read_text() == 'keep'


def metadata():
    return {'id': VIDEO_ID, 'extractor_key': 'Youtube', 'duration': 2, 'availability': 'public',
            'requested_formats': [
                {'url': 'https://rr1.googlevideo.com/videoplayback?secret=signed', 'protocol': 'https',
                 'height': 2160, 'vcodec': 'av01', 'acodec': 'none', 'filesize': 100},
                {'url': 'https://rr1.googlevideo.com/videoplayback?secret=audio', 'protocol': 'https',
                 'vcodec': 'none', 'acodec': 'opus', 'filesize': 20}]}


def manifest(tmp_path):
    return {'url': URL, 'video_id': VIDEO_ID, 'directory': str(tmp_path), 'max_duration': 3600,
            'max_height': 2160, 'max_bytes': 1024 * 1024, 'timeout_sec': 10,
            'ffmpeg': 'trusted-ffmpeg', 'node': 'trusted-node'}


def direct_format(ident, *, height=None, width=None, video='none', audio='none',
                  ext='mp4', size=100, **extras):
    return {'format_id': ident, 'height': height, 'width': width, 'vcodec': video,
            'acodec': audio, 'ext': ext, 'filesize': size,
            'url': 'https://rr1.googlevideo.com/videoplayback?secret=signed',
            'protocol': 'https', **extras}


def test_best_source_selection_preserves_4k_av1_and_audio_without_avc_downgrade(tmp_path):
    options = downloader_options(manifest(tmp_path), lambda _: None, object())
    # Model a higher SDK preference for the lower progressive variant.
    catalog = [direct_format('opus', audio='opus', ext='webm'),
               direct_format('av1-4k', height=2160, width=3840, video='av01'),
               direct_format('avc-1080', height=1080, width=1920, video='avc1', audio='aac')]
    selected, = options['format']({'formats': catalog})
    assert selected['format_id'] == 'av1-4k+opus'
    assert selected['vcodec'] == 'av01' and selected['ext'] == options['merge_output_format'] == 'mp4'
    assert options['proxy'] == '' and options['geo_bypass'] is False
    assert options['cookiesfrombrowser'] is None and options['cookiefile'] is None
    assert options['remote_components'] == [] and options['enable_file_urls'] is False
    assert options['allow_unplayable_formats'] is False and options['impersonate'] is None
    assert not options.get('extractor_args', {}).get('youtube', {}).get('player_client')
    validate_metadata({**metadata(), **selected}, manifest(tmp_path))


def test_progressive_1080p_beats_pair_at_360p(tmp_path):
    options = downloader_options(manifest(tmp_path), lambda _: None, object())
    high = direct_format('1080-av', height=1080, width=1920, video='h264', audio='aac')
    catalog = [high, direct_format('360-v', height=360, width=640, video='av01'),
               direct_format('a', audio='opus')]
    selected, = options['format']({'formats': catalog})
    assert selected is high and 'requested_formats' not in selected


def test_quality_ranking_preserves_cap_transport_drm_and_aggregate_size_guards(tmp_path):
    job = {**manifest(tmp_path), 'max_bytes': 1000}
    video_360 = direct_format('360', height=360, width=640, video='av01')
    catalogue = [video_360, direct_format('opus', audio='opus', size=300),
                 direct_format('1080-big', height=1080, width=1920, video='h264', size=800),
                 direct_format('1080-hls', height=1080, width=1920, video='h264', audio='aac',
                               fragments=[{'url': 'https://rr1.googlevideo.com/segment'}]),
                 direct_format('2160-drm', height=2160, width=3840, video='h264', audio='aac',
                               has_drm=True),
                 direct_format('4320', height=4320, width=7680, video='h264', audio='aac')]
    options = downloader_options(job, lambda _: None, object())
    selected, = options['format']({'formats': catalogue})
    assert selected['format_id'] == '360+opus'
    public = ingestion_worker.display_metadata({**metadata(), **selected, 'formats': catalogue}, job)
    assert public['quality'] == {
        'eligible_max_height': 360, 'max_height': 2160, 'observed_max_height': 4320,
        'selected_format_ids': ['360', 'opus'],
        'limits': ['drm', 'resolution_cap', 'storage_quota', 'unsupported_transport']}
    assert 'secret=' not in json.dumps(public)
    assert '2160-drm' not in [item['id'] for item in public['formats']]


def test_quality_metadata_discards_untrusted_summary_fields():
    raw = {'quality': {'selected_format_ids': ['selected', 'https://provider.invalid/?token=secret'],
                       'limits': ['missing_audio', 'secret', {'token': 'hidden'}],
                       'observed_max_height': 2160, 'eligible_max_height': 1080,
                       'max_height': 2160, 'access_token': 'hidden'}}
    expected = {'selected_format_ids': ['selected'], 'limits': ['missing_audio'],
                'observed_max_height': 2160, 'eligible_max_height': 1080, 'max_height': 2160}
    assert ingestion.sanitize_metadata(raw)['quality'] == expected
    from app.source_progress import metadata as progress_metadata
    assert progress_metadata(raw)['quality'] == expected


def test_legacy_cache_is_preserved_but_does_not_pin_old_quality(tmp_path):
    calls = []
    cache = tmp_path / 'cache'
    legacy = cache / f'youtube-{VIDEO_ID}-h2160-v1'
    legacy.mkdir(parents=True)
    old = legacy / 'metadata.json'
    previous = json.dumps({'completed_at': 0, 'video_id': VIDEO_ID, 'profile': 'best-av-2160'})
    old.write_text(previous, encoding='utf-8')
    adapter = VideoSourceIngestor(cache, policy=policy(), worker=local_worker(calls), probe=lambda _: dict(MEDIA))
    result = adapter.ingest(URL, make_job(tmp_path))
    assert not result['cache_hit'] and len(calls) == 1
    assert old.read_text(encoding='utf-8') == previous
    assert (cache / f'youtube-{VIDEO_ID}-h2160-v2' / 'metadata.json').is_file()


def test_metadata_transient_failure_retries_same_url_once(monkeypatch):
    from unittest.mock import Mock
    ydl = Mock()
    ydl.extract_info.side_effect = [RuntimeError('connection timed out'), metadata()]
    pause = Mock()
    monkeypatch.setattr(ingestion_worker.time, 'sleep', pause)
    events = []
    assert ingestion_worker.extract_metadata(ydl, URL, events.append)['id'] == VIDEO_ID
    assert ydl.extract_info.call_count == 2
    assert all(call.args == (URL,) and call.kwargs == {'download': False}
               for call in ydl.extract_info.call_args_list)
    pause.assert_called_once_with(1)
    assert events[0]['stage'] == 'getting_metadata'


@pytest.mark.parametrize('message,code', [
    ('HTTP 403 access denied', 'access'),
    ('Requested format is not available. Use --list-formats', 'formats'),
    ('No video formats found', 'formats'),
    ('Video removed', 'unavailable'),
])
def test_access_and_format_failures_do_not_retry_or_rotate_clients(message, code, monkeypatch):
    from unittest.mock import Mock
    ydl = Mock()
    ydl.extract_info.side_effect = RuntimeError(message)
    pause = Mock()
    monkeypatch.setattr(ingestion_worker.time, 'sleep', pause)
    with pytest.raises(RuntimeError):
        ingestion_worker.extract_metadata(ydl, URL, lambda _event: None)
    ydl.extract_info.assert_called_once_with(URL, download=False)
    pause.assert_not_called()
    assert _classify(RuntimeError(message)) == code


@pytest.mark.parametrize('change,code', [
    ({'id': OTHER_ID}, 'policy'), ({'_type': 'playlist', 'entries': []}, 'policy'),
    ({'extractor_key': 'Generic'}, 'policy'), ({'is_live': True}, 'live'),
    ({'live_status': 'is_upcoming'}, 'live'), ({'live_status': 'post_live'}, 'live'),
    ({'availability': 'private'}, 'access'), ({'availability': 'needs_auth'}, 'access'),
    ({'age_limit': 18}, 'access'), ({'has_drm': True}, 'access'),
    ({'duration': None}, 'invalid'), ({'duration': float('nan')}, 'invalid'),
    ({'duration': 7200}, 'duration'),
])
def test_blocked_metadata_is_never_downloaded(tmp_path, change, code):
    with pytest.raises(SourceRejected) as error:
        validate_metadata({**metadata(), **change}, manifest(tmp_path))
    assert error.value.code == code


def test_metadata_rejects_aggregate_video_plus_audio_size_and_remote_manifests(tmp_path):
    info = metadata()
    info['requested_formats'][0]['filesize'] = 900_000
    info['requested_formats'][1]['filesize'] = 900_000
    with pytest.raises(SourceRejected, match='quota'):
        validate_metadata(info, manifest(tmp_path))
    info = metadata()
    info['requested_formats'][0]['fragments'] = [{'url': 'https://rr1.googlevideo.com/part'}]
    with pytest.raises(SourceRejected, match='policy'):
        validate_metadata(info, manifest(tmp_path))


@pytest.mark.parametrize('url', [
    'http://rr1.googlevideo.com/source', 'https://127.0.0.1/source',
    'https://googlevideo.com.attacker.invalid/source', 'https://attacker.invalid/source',
    'https://user:secret@www.youtube.com/watch', 'https://rr1.googlevideo.com:444/source',
    'file:///source.mp4', 'data:video/mp4;anything', 'https://[::1]/source',
    'https://www.youtube.com\\@127.0.0.1/source', 'https://www.youtube.com/\nsource',
])
def test_provider_egress_rejects_arbitrary_hosts_protocols_and_credentials(url):
    with pytest.raises(SourceRejected, match='policy'):
        validate_provider_url(url)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1', '192.0.2.1'])
def test_provider_dns_rejects_private_reserved_or_loopback_addresses(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))])
    with pytest.raises(SourceRejected, match='policy'):
        public_addresses('rr1.googlevideo.com')


def test_provider_dns_mixed_public_private_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_a, **_k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(SourceRejected, match='policy'):
        public_addresses('rr1.googlevideo.com')


def test_provider_connection_uses_validated_ip_and_original_tls_hostname(monkeypatch):
    connected, wrapped = [], []
    class FakeSocket:
        def settimeout(self, _timeout):
            pass
        def connect(self, address):
            connected.append(address)
        def close(self):
            pass
    class FakeContext:
        def wrap_socket(self, sock, server_hostname):
            wrapped.append(server_hostname)
            return sock
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])
    monkeypatch.setattr(socket, 'socket', lambda *_a, **_k: FakeSocket())
    connection = _PinnedHTTPSConnection('rr1.googlevideo.com', timeout=1)
    connection._context = FakeContext()
    connection.connect()
    assert connected == [('8.8.8.8', 443)] and wrapped == ['rr1.googlevideo.com']


def launch_fake_worker(monkeypatch, code):
    original = subprocess.Popen
    def launch(command, **kwargs):
        if command[:3] == [sys.executable, '-m', 'app.ingestion_worker']:
            return original([sys.executable, '-c', code], **kwargs)
        return original(command, **kwargs)
    monkeypatch.setattr(ingestion.subprocess, 'Popen', launch)


def test_child_jsonl_progress_and_result_protocol(tmp_path, monkeypatch):
    stage = make_job(tmp_path, 'stage')
    launch_fake_worker(monkeypatch, "import json; print(json.dumps({'event':'progress','stage':'retrieving_source','fraction':0.4,'message':'https://secret.invalid/token'}),flush=True); print(json.dumps({'event':'result','filename':'source.mp4','video_id':'ownedTST_01','title':'Owned'}),flush=True)")
    events = []
    result = ingestion._run_worker(manifest(stage), stage, progress=lambda *event: events.append(event))
    assert result['filename'] == 'source.mp4'
    assert events and events[0][0] == .4
    assert 'secret.invalid' not in json.dumps(events)
    assert not (stage / '.worker.json').exists()


@pytest.mark.parametrize('cancel', [False, True])
def test_child_has_hard_timeout_and_cancellation(tmp_path, monkeypatch, cancel):
    stage = make_job(tmp_path, 'stage')
    launch_fake_worker(monkeypatch, 'import time; time.sleep(30)')
    request = {**manifest(stage), 'timeout_sec': .15}
    began = time.monotonic()
    with pytest.raises(IngestionCancelled if cancel else IngestionBlocked):
        ingestion._run_worker(request, stage, cancelled=lambda: cancel)
    assert time.monotonic() - began < 5
    assert not (stage / '.worker.json').exists()


def test_child_access_failure_is_safe_and_never_exposes_provider_diagnostics(tmp_path, monkeypatch):
    stage = make_job(tmp_path, 'stage')
    launch_fake_worker(monkeypatch, "import json; print(json.dumps({'event':'error','code':'access','detail':'https://cdn.invalid/signed?token=secret'}),flush=True); raise SystemExit(1)")
    with pytest.raises(IngestionBlocked) as error:
        ingestion._run_worker(manifest(stage), stage)
    assert 'secret' not in str(error.value) and 'dibatasi platform' in str(error.value)
    assert _classify(RuntimeError('HTTP 403 https://signed.invalid?token=secret')) == 'access'


def test_owned_synthetic_video_full_ingest_probe_cache_flow(tmp_path):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('Real FFmpeg and FFprobe required for owned source integration test.')
    source = tmp_path / 'owned.mp4'
    subprocess.run([config.binary('ffmpeg'), '-v', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=2',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(source)],
                   check=True, capture_output=True, timeout=30)
    calls = []
    def own_provider(request, stage, **_kwargs):
        calls.append(request['video_id'])
        shutil.copyfile(source, stage / 'source.mp4')
        return {'filename': 'source.mp4', 'title': 'Owned synthetic source', 'video_id': request['video_id']}
    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=own_provider)
    first = adapter.ingest(URL, make_job(tmp_path, 'first'))
    second = adapter.ingest(URL, make_job(tmp_path, 'second'))
    assert first['media']['has_audio'] and first['media']['audio_codec'] == 'aac'
    assert first['media']['video_codec'] == 'h264' and first['media']['width'] == 320
    assert second['cache_hit'] and calls == [VIDEO_ID]
    assert media.probe(second['source_path']) == first['media']
    assert first['source_path'].read_bytes() == second['source_path'].read_bytes() == source.read_bytes()


@pytest.mark.parametrize('width,height', [(640, 360), (1920, 1080), (3840, 2160)])
def test_quality_selection_reports_measured_owned_media_resolution(tmp_path, width, height):
    """A fake inventory models provider offerings; FFmpeg and FFprobe use real A/V."""
    ffmpeg = config.binary('ffmpeg')
    if not (ffmpeg and config.binary('ffprobe')):
        pytest.skip('Real FFmpeg and FFprobe required for resolution verification.')
    fixture = tmp_path / 'fixture.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-f', 'lavfi',
                    '-i', f'color=c=blue:size={width}x{height}:rate=12:duration=0.5',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=0.5',
                    '-c:v', 'libx264', '-threads:v', '2', '-preset', 'ultrafast',
                    '-c:a', 'aac', '-shortest', str(fixture)],
                   check=True, capture_output=True, timeout=30)

    def worker(request, stage, **_kwargs):
        available = [direct_format('owned', width=width, height=height, video='avc1',
                                   audio='aac', size=fixture.stat().st_size)]
        if height > 360:
            available.append(direct_format('lower-sdk-preferred', width=640, height=360, video='av01'))
            available.append(direct_format('a', audio='opus'))
        selected, = ingestion_worker.select_format({'formats': available}, request)
        assert selected['format_id'] == 'owned'
        info = {'id': VIDEO_ID, 'title': 'Owned resolution fixture', 'duration': .5, 'formats': available, **selected}
        shutil.copyfile(fixture, stage / 'source.mp4')
        return {'filename': 'source.mp4', 'video_id': VIDEO_ID, 'metadata': ingestion_worker.display_metadata(info, request)}

    adapter = VideoSourceIngestor(tmp_path / 'cache', policy=policy(), worker=worker)
    result = adapter.ingest(URL, make_job(tmp_path))
    assert (result['media']['width'], result['media']['height']) == (width, height)
    assert result['media']['has_audio']
    assert result['metadata']['quality']['eligible_max_height'] == height
    assert result['metadata']['quality']['selected_format_ids'] == ['owned']


@pytest.mark.parametrize('private', [False, True])
def test_worker_checks_metadata_before_download_and_handles_single_download_limit(tmp_path, monkeypatch, private):
    """Model the pinned SDK's normal max-downloads exception, without importing it."""
    for name, value in [('YOUTUBE_INGEST_ENABLED', 'true'), ('YOUTUBE_APPROVAL_REFERENCE', 'approval'),
                        ('YOUTUBE_APPROVED_VIDEO_IDS', VIDEO_ID), ('GEMINI_API_KEY', 'test-private-key'),
                        ('GOOGLE_API_KEY', 'test-google-key'), ('OPENAI_API_KEY', 'test-openai-key')]:
        monkeypatch.setenv(name, value)
    calls, events = [], []
    class DownloadLimit(Exception):
        pass
    class FakeYoutubeDL:
        def __init__(self, options, auto_init):
            assert options['max_downloads'] == 1 and auto_init is False
            assert not any(name in ingestion_worker.os.environ for name in (
                'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY',
                'YOUTUBE_APPROVAL_REFERENCE', 'YOUTUBE_APPROVED_VIDEO_IDS'))
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def add_info_extractor(self, _extractor):
            pass
        def build_request_director(self, handlers):
            assert handlers == [object]
            return object()
        def extract_info(self, url, download):
            assert url == URL and download is False
            return {**metadata(), 'availability': 'private' if private else 'public'}
        def process_ie_result(self, info, download):
            calls.append(info['id'])
            assert info['availability'] == 'public' and download is True
            (tmp_path / 'source.mp4').write_bytes(b'owned synthetic provider bytes')
            raise DownloadLimit()
    fake = types.ModuleType('yt_dlp')
    fake.YoutubeDL = FakeYoutubeDL
    youtube = types.ModuleType('yt_dlp.extractor.youtube')
    youtube.YoutubeIE = object
    globals_module = types.ModuleType('yt_dlp.globals')
    globals_module.plugin_dirs = types.SimpleNamespace(value=['default'])
    utils = types.ModuleType('yt_dlp.utils')
    utils.MaxDownloadsReached = DownloadLimit
    for name, module in [('yt_dlp', fake), ('yt_dlp.extractor.youtube', youtube),
                         ('yt_dlp.globals', globals_module), ('yt_dlp.utils', utils)]:
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(ingestion_worker, '_dependency_ready', lambda: True)
    monkeypatch.setattr(ingestion_worker, '_provider_handler_class', lambda: object)
    monkeypatch.setattr(config, 'binary', lambda name: manifest(tmp_path)[name])
    if private:
        with pytest.raises(SourceRejected, match='access'):
            ingestion_worker.execute(manifest(tmp_path), events.append)
        assert calls == [] and not (tmp_path / 'source.mp4').exists()
    else:
        ingestion_worker.execute(manifest(tmp_path), events.append)
        assert calls == [VIDEO_ID]
        assert {key: events[-1][key] for key in ('event','filename','video_id','title')} == {
            'event': 'result', 'filename': 'source.mp4', 'video_id': VIDEO_ID, 'title': f'YouTube {VIDEO_ID}'}
        assert events[-1]['metadata']['duration'] == 2
    assert globals_module.plugin_dirs.value == []


def test_optional_sdk_https_handler_contract_without_any_network(monkeypatch):
    """Runs when optional SDK is installed, but never opens a socket or downloads."""
    monkeypatch.setenv('YTDLP_NO_PLUGINS', '1')
    pytest.importorskip('yt_dlp', reason='Optional adapter dependency is not installed.')
    from yt_dlp.networking import Request
    from yt_dlp.networking.exceptions import UnsupportedRequest
    cls = ingestion_worker._provider_handler_class()
    handler = cls(logger=object(), proxies={'all': ''})
    handler.validate(Request('https://rr1.googlevideo.com/owned'))
    with pytest.raises(UnsupportedRequest, match='Proxies are disabled'):
        handler.validate(Request('https://rr1.googlevideo.com/owned', proxies={'https': 'http://127.0.0.1:8888'}))
    opener = handler._create_instance({'all': None}, handler.cookiejar)
    assert not any(isinstance(item, (urllib.request.ProxyHandler, urllib.request.HTTPCookieProcessor,
                                    urllib.request.FileHandler, urllib.request.FTPHandler, urllib.request.DataHandler))
                   for item in opener.handlers)
    redirect = next(item for item in opener.handlers if item.__class__.__name__ == 'ProviderRedirectHandler')
    with pytest.raises(SourceRejected, match='policy'):
        redirect.redirect_request(urllib.request.Request('https://www.youtube.com/'), None, 302, '', {}, 'https://127.0.0.1/')
    http = next(item for item in opener.handlers if item.__class__.__name__ == 'ProviderHTTPHandler')
    request = urllib.request.Request('https://www.youtube.com/', headers={
        'Cookie': 'private=secret', 'Authorization': 'secret', 'Host': '127.0.0.1', 'X-Forwarded-For': '8.8.8.8'})
    clean = http.https_request(request)
    assert not any(name.lower() in {'cookie', 'authorization', 'x-forwarded-for'} for name, _ in clean.header_items())
    assert clean.get_header('Host') == 'www.youtube.com'
    handler.close()


def test_download_progress_aggregates_both_tracks_and_reports_real_current_speed():
    tracker = ingestion_worker.DownloadProgress({'requested_formats': [
        {'format_id':'video', 'filesize':1000}, {'format_id':'audio', 'filesize':200}]}, 2000)
    first = tracker.update({'status':'downloading', 'filename':'private/source.fvideo.mp4',
        'info_dict':{'format_id':'video','width':1920,'height':1080,'vcodec':'avc1','acodec':'none'},
        'downloaded_bytes':200, 'total_bytes':1000, 'speed':80, 'eta':10,
        'url':'https://signed.invalid/?token=secret'})
    assert first['downloaded_bytes'] == 200 and first['total_bytes'] == 1200
    assert first['percent'] == pytest.approx(100/6)
    assert first['speed_bps'] == 80 and first['eta_seconds'] == 10
    assert first['resolution'] == '1920×1080'
    tracker.update({'status':'finished','info_dict':{'format_id':'video'},'downloaded_bytes':1000})
    audio = tracker.update({'status':'downloading','info_dict':{'format_id':'audio'},'downloaded_bytes':100,
                            'total_bytes':200,'speed':20,'eta':5})
    assert audio['downloaded_bytes'] == 1100 and audio['total_bytes'] == 1200
    assert audio['percent'] == pytest.approx(1100/12)
    assert audio['speed_bps'] == 20 and audio['eta_seconds'] == 5
    assert 'private/source' not in json.dumps(first) and 'signed.invalid' not in json.dumps(first)


def test_unknown_download_totals_stay_unknown_until_each_track_finishes():
    tracker = ingestion_worker.DownloadProgress({'requested_formats':[{'format_id':'v'},{'format_id':'a'}]},10000)
    first = tracker.update({'status':'finished','info_dict':{'format_id':'v'},'downloaded_bytes':900})
    assert first['total_bytes'] is None and first['percent'] is None
    second = tracker.update({'status':'downloading','info_dict':{'format_id':'a'},'downloaded_bytes':50})
    assert second['downloaded_bytes'] == 950 and second['total_bytes_estimate'] is None
    assert second['speed_bps'] is None and second['eta_seconds'] is None
    finished = tracker.update({'status':'finished','info_dict':{'format_id':'a'},'downloaded_bytes':100})
    assert finished['total_bytes'] == 1000 and finished['percent'] == 100


def test_download_estimates_are_separate_from_actual_size_and_aggregate_quota():
    tracker = ingestion_worker.DownloadProgress({'requested_formats':[
        {'format_id':'v','filesize_approx':1000},{'format_id':'a','filesize_approx':200}]},1300)
    event=tracker.update({'status':'downloading','info_dict':{'format_id':'v'},'downloaded_bytes':500})
    assert event['total_bytes'] is None and event['total_bytes_estimate'] == 1200
    assert event['percent'] == pytest.approx(500/12)
    with pytest.raises(SourceRejected, match='quota'):
        tracker.update({'status':'downloading','info_dict':{'format_id':'a'},'downloaded_bytes':900})


def test_progress_boundary_rejects_nonfinite_values_and_does_not_leak_raw_metadata():
    details = ingestion.sanitize_progress({'phase':'download','speed_bps':float('nan'),
        'eta_seconds':-1,'downloaded_bytes':True,'total_bytes':float('inf'),'percent':101,
        'filename':'private/path.mp4','url':'https://signed.invalid/?token=secret',
        'metadata': {'title':'Owned https://signed.invalid/token', 'width':640,'height':360,
            'formats':[{'id':'137','url':'https://signed.invalid/?token=secret','video_codec':'h264',
                        'audio_codec':'none','has_video':True,'has_audio':False,'size_bytes':100}]}})
    assert details['speed_bps'] is None and details['eta_seconds'] is None
    assert details['downloaded_bytes'] is None and details['total_bytes'] is None
    assert details['percent'] == 100 and details['metadata']['title'] == 'Owned'
    assert details['metadata']['formats'][0]['size_bytes'] == 100
    assert 'secret' not in json.dumps(details) and 'private/path' not in json.dumps(details)


def test_progress_supports_legacy_callback_without_hiding_callback_type_error():
    received=[]
    ingestion._emit(lambda value,message:received.append((value,message)),.5,'retrieving_source: owned',{'phase':'download'})
    assert received == [(.5,'retrieving_source: owned')]
    calls=[]
    def fails(_fraction,_message,_details):
        calls.append(True)
        raise TypeError('intentional callback failure')
    with pytest.raises(TypeError,match='intentional'):
        ingestion._emit(fails,.5,'owned',{'phase':'download'})
    assert len(calls) == 1


def test_child_protocol_sanitizes_quality_stats_and_reports_real_merge_phase(tmp_path,monkeypatch):
    stage=make_job(tmp_path,'stage')
    code="""import json
events=[{'event':'metadata','metadata':{'title':'Owned','duration':2,'width':320,'height':180,'fps':24,
 'url':'https://secret.invalid/source','formats':[{'id':'v','video_codec':'h264','has_video':True,'url':'https://secret.invalid/token'}]}},
 {'event':'progress','stage':'retrieving_source','fraction':.5,'details':{'phase':'download','downloaded_bytes':100,'total_bytes':200,'speed_bps':50,'eta_seconds':2,'percent':50,'filename':'secret.mp4'}},
 {'event':'progress','stage':'merging_media','fraction':None,'details':{'phase':'merge','message':'https://secret.invalid/token'}},
 {'event':'result','filename':'source.mp4','video_id':'ownedTST_01','title':'Owned'}]
for event in events: print(json.dumps(event),flush=True)
"""
    launch_fake_worker(monkeypatch,code)
    events=[]
    result=ingestion._run_worker(manifest(stage),stage,progress=lambda *event:events.append(event))
    download=next(event for event in events if event[1].startswith('retrieving_source:'))
    merge=next(event for event in events if event[1].startswith('merging_media:'))
    assert download[2]['downloaded_bytes']==100 and download[2]['speed_bps']==50
    assert merge[0] is None and merge[2]['phase']=='merge' and merge[2]['speed_bps'] is None
    assert result['metadata']['title']=='Owned' and result['metadata']['width']==320
    assert 'secret' not in json.dumps(events) and 'secret' not in json.dumps(result)


def test_cache_hit_reuses_safe_quality_metadata_and_reports_actual_size_without_speed(tmp_path):
    calls=[]
    raw={'title':'Owned','width':1920,'height':1080,'fps':30,'duration':2,
         'formats':[{'id':'v','width':1920,'height':1080,'fps':30,'video_codec':'h264','audio_codec':'none',
                     'ext':'mp4','size_bytes':200,'has_video':True,'has_audio':False,'url':'https://secret.invalid'}]}
    adapter=VideoSourceIngestor(tmp_path/'cache',policy=policy(),worker=local_worker(calls,metadata=raw),probe=lambda _:MEDIA)
    first=adapter.ingest(URL,make_job(tmp_path,'first'))
    events=[]
    second=adapter.ingest(URL,make_job(tmp_path,'second'),progress=lambda *event:events.append(event))
    assert second['cache_hit'] and len(calls)==1
    assert second['metadata']==first['metadata'] and second['metadata']['width']==MEDIA['width']
    assert second['metadata']['formats'][0]['height']==1080
    assert second['source_size_bytes']==len(b'owned media test data')
    cached=next(event[2] for event in events if len(event)>2 and event[2].get('phase')=='cache')
    assert cached['cache_hit'] and cached['total_bytes']==second['source_size_bytes']
    assert cached['speed_bps'] is None and cached['percent']==100
    assert 'secret.invalid' not in json.dumps(second['metadata'])


@pytest.mark.parametrize('code,retryable,recovery',[
    ('network',True,'retry'),('timeout',True,'retry'),('access',False,'upload'),
    ('unavailable',False,'upload'),('disabled',False,'settings'),('quota',False,'settings')])
def test_errors_identify_recovery_without_provider_details(code,retryable,recovery):
    error=ingestion._blocked(code)
    assert error.code==code and error.retryable is retryable and error.recovery==recovery


def test_quality_metadata_excludes_unsafe_or_restricted_formats():
    info=metadata()
    info.update(title='Owned quality options',width=3840,height=2160,fps=60)
    info['requested_formats'][0].update(format_id='v',width=3840,fps=60,ext='mp4')
    info['requested_formats'][1].update(format_id='a',ext='m4a')
    info['formats']=[{'format_id':'bad','url':'https://127.0.0.1/private','protocol':'https','vcodec':'h264'},
                     {'format_id':'drm','url':'https://rr1.googlevideo.com/drm','protocol':'https','has_drm':True}]
    public=ingestion_worker.display_metadata(info)
    assert public['width']==3840 and public['height']==2160
    assert [f['id'] for f in public['formats']]==['v','a']
    assert public['formats'][0]['video_codec']=='av01'
    assert 'signed' not in json.dumps(public) and '127.0.0.1' not in json.dumps(public)
