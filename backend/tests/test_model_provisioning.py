"""Small independent fixtures test the real provisioning mechanics, never HF."""
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import shutil
import stat
from urllib.error import HTTPError, URLError
import zipfile

import pytest

from app import model_provisioning as models


@pytest.fixture
def fixture(tmp_path):
    content = {name: ('independent model fixture: '+name).encode() for name in
               ('config.json','model.bin','tokenizer.json','vocabulary.txt')}
    manifest = {'schema': 1, 'model': 'small', 'repository': 'Systran/faster-whisper-small',
                'revision': 'a'*40, 'license': 'MIT',
                'files': [{'name': name, 'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                          for name, value in content.items()]}
    root = tmp_path/'models'
    temporary, cache = tmp_path/'temp', tmp_path/'cache'
    for directory in (root, temporary, cache):
        directory.mkdir()
    notice = tmp_path/'MIT.txt'
    notice.write_text('MIT test notice', encoding='utf-8')
    calls = []
    def opener(url):
        calls.append(url)
        return io.BytesIO(content[url.rsplit('/', 1)[1]])
    manager = models.ModelProvisioner(root, temporary, cache, manifest, notice, opener)
    return manager, content, calls


def installed(manager, content):
    manager.directory.mkdir(parents=True)
    for name, value in content.items():
        (manager.directory/name).write_bytes(value)


def finished(manager):
    if manager.thread:
        manager.thread.join(timeout=5)
        assert not manager.thread.is_alive()
    return manager.status()


def offline(manager, content, path=None, transform=None, metadata=None):
    path = path or manager.temp_root/'test-model.zip'
    with zipfile.ZipFile(path, 'w') as file:
        for name, value in content.items():
            file.writestr(name, value)
        file.writestr(models.OFFLINE_METADATA, json.dumps(metadata or manager.offline_metadata()))
        if transform:
            transform(file)
    return path


def test_manifest_is_exact_pinned_public_small_model():
    path = Path(__file__).parents[1]/'assets/models'/models.MANIFEST_NAME
    manifest = models.load_manifest(path)
    assert manifest['revision'] == '536b0662742c02347bc0e980a01041f333bce120'
    assert sum(item['bytes'] for item in manifest['files']) == 486212372
    assert manifest['license'] == 'MIT'


def test_verified_files_hash_once_and_detect_same_size_modification(fixture, monkeypatch):
    manager, content, _ = fixture
    installed(manager, content)
    original = models._hash
    calls = []
    monkeypatch.setattr(models, '_hash', lambda path: (calls.append(path), original(path))[1])
    assert manager.status()['state'] == 'ready'
    assert len(calls) == 4
    assert manager.status()['state'] == 'ready'
    assert len(calls) == 4
    path = manager.directory/'model.bin'
    path.write_bytes(b'!'*len(content['model.bin']))
    assert manager.status()['state'] == 'missing'
    assert 'Checksum' in manager.status()['message']


def test_missing_model_does_not_fall_back_to_unverified_hf_cache(fixture):
    manager, _, _ = fixture
    with pytest.raises(models.ModelProvisioningError, match='belum tersedia'):
        models.verify_model(manager.directory, manager.manifest, manager.models_root)


def test_valid_model_is_never_downloaded_or_overwritten(fixture):
    manager, content, calls = fixture
    installed(manager, content)
    identities = {name: (manager.directory/name).stat().st_mtime_ns for name in content}
    assert manager.start()['state'] == 'ready'
    assert manager.thread is None and not calls
    assert identities == {name: (manager.directory/name).stat().st_mtime_ns for name in content}


def test_download_stream_verifies_all_bytes_and_reuses_valid_files(fixture):
    manager, content, calls = fixture
    manager.directory.mkdir(parents=True)
    (manager.directory/'config.json').write_bytes(content['config.json'])
    manager.start()
    result = finished(manager)
    assert result['state'] == 'ready'
    assert result['received_bytes'] == manager.total-len(content['config.json'])
    assert result['verified_bytes'] == manager.total and result['percent'] == 100
    assert len(calls) == 3 and all('/resolve/'+'a'*40+'/' in url for url in calls)
    assert (manager.directory/models.LICENSE_NAME).read_text() == 'MIT test notice'


@pytest.mark.parametrize('kind', ['http', 'disconnect', 'truncated'])
def test_transient_failure_retries_and_activates_only_verified_files(fixture, monkeypatch, kind):
    manager, content, _ = fixture
    original = manager.opener
    count = 0
    def opener(url):
        nonlocal count
        count += 1
        if count == 1:
            if kind == 'http':
                raise HTTPError(url, 503, 'temporary', None, None)
            if kind == 'disconnect':
                raise URLError('offline fixture')
            return io.BytesIO(b'partial')
        return original(url)
    monkeypatch.setattr(manager.cancelled, 'wait', lambda _: False)
    manager.opener = opener
    manager.start()
    result = finished(manager)
    assert result['state'] == 'ready' and count == 5
    assert result['received_bytes'] >= manager.total


def test_checksum_failure_never_activates_any_download(fixture):
    manager, content, _ = fixture
    manager.opener = lambda url: io.BytesIO(b'!'*len(content[url.rsplit('/', 1)[1]]))
    manager.start()
    result = finished(manager)
    assert result['state'] == 'failed' and 'Checksum' in result['message']
    assert not manager.directory.exists()
    assert not list(manager.temp_root.iterdir())


def test_retry_after_later_file_fails_keeps_previously_verified_downloads(fixture):
    manager, content, calls = fixture
    good = manager.opener
    def interrupted(url):
        if url.endswith('/tokenizer.json'):
            raise HTTPError(url, 404, 'unavailable', None, None)
        return good(url)
    manager.opener = interrupted
    manager.start()
    assert finished(manager)['state'] == 'failed'
    assert (manager.directory/'model.bin').read_bytes() == content['model.bin']
    manager.opener = good
    manager.start()
    assert finished(manager)['state'] == 'ready'
    assert sum(url.endswith('/model.bin') for url in calls) == 1


def test_no_internet_has_clear_offline_fallback(fixture, monkeypatch):
    manager, _, _ = fixture
    def unavailable(_):
        raise URLError('no network')
    manager.opener = unavailable
    monkeypatch.setattr(manager.cancelled, 'wait', lambda _: False)
    manager.start()
    result = finished(manager)
    assert result['state'] == 'failed'
    assert 'internet' in result['message'] and 'offline' in result['message']
    assert not manager.directory.exists()


def test_cancel_preserves_existing_valid_model_file(fixture):
    manager, content, _ = fixture
    manager.directory.mkdir(parents=True)
    (manager.directory/'config.json').write_bytes(content['config.json'])
    class CancelStream(io.BytesIO):
        def read(self, size):
            result = super().read(size)
            manager.cancelled.set()
            return result
    manager.opener = lambda url: CancelStream(content[url.rsplit('/', 1)[1]])
    manager.start()
    result = finished(manager)
    assert result['state'] == 'cancelled'
    assert (manager.directory/'config.json').read_bytes() == content['config.json']
    assert not (manager.directory/'model.bin').exists()


def test_offline_zip_roundtrip_and_no_network(fixture):
    manager, content, calls = fixture
    installed(manager, content)
    archive = manager.export_offline()
    target_root = manager.models_root/'fresh'
    target_root.mkdir()
    target = models.ModelProvisioner(target_root, manager.temp_root, manager.cache_root,
                                     manager.manifest, manager.license_path, manager.opener)
    upload = manager.temp_root/'uploaded.zip'
    shutil.copyfile(archive, upload)
    target.start(upload)
    assert finished(target)['state'] == 'ready'
    assert not calls and not upload.exists()
    assert (target.directory/models.LICENSE_NAME).is_file()


@pytest.mark.parametrize('bad_name', ['../escape', 'nested/model.bin', 'C:/bad/model.bin', '/absolute', 'model.bin\\evil'])
def test_offline_zip_rejects_unlisted_paths_without_extraction(fixture, bad_name):
    manager, content, _ = fixture
    archive = offline(manager, content, transform=lambda file: file.writestr(bad_name, b'bad'))
    manager.start(archive)
    assert finished(manager)['state'] == 'failed'
    assert not manager.directory.exists()


def test_offline_wrong_revision_is_rejected(fixture):
    manager, content, _ = fixture
    metadata = manager.offline_metadata() | {'revision': 'b'*40}
    manager.start(offline(manager, content, metadata=metadata))
    assert finished(manager)['state'] == 'failed'
    assert not manager.directory.exists()


def test_offline_duplicate_file_is_rejected(fixture):
    manager, content, _ = fixture
    with pytest.warns(UserWarning, match='Duplicate'):
        archive = offline(manager, content, transform=lambda file: file.writestr('model.bin', content['model.bin']))
    manager.start(archive)
    assert finished(manager)['state'] == 'failed'


def test_offline_symlink_notice_is_rejected(fixture):
    manager, content, _ = fixture
    info = zipfile.ZipInfo(models.LICENSE_NAME)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    manager.start(offline(manager, content, transform=lambda file: file.writestr(info, b'target')))
    assert finished(manager)['state'] == 'failed'


def test_offline_size_mismatch_and_corruption_not_activated(fixture):
    manager, content, _ = fixture
    manager.start(offline(manager, content | {'model.bin': b'corrupt'}))
    assert finished(manager)['state'] == 'failed'
    assert not manager.directory.exists()


@pytest.mark.parametrize('url', ['http://huggingface.co/x', 'https://evil.test/model',
                               'https://huggingface.co.evil.test/model', 'https://user:password@huggingface.co/x',
                               'https://huggingface.co:444/x'])
def test_untrusted_download_target_is_blocked(url):
    with pytest.raises(models.ModelProvisioningError):
        models._trusted_url(url)


def test_approved_https_redirect_targets():
    for host in ('huggingface.co', 'cdn-lfs.huggingface.co', 'cas-bridge.xethub.hf.co', 'us.aws.cdn.hf.co'):
        models._trusted_url('https://'+host+'/public')


def test_actual_model_routes_inherit_packaged_auth_and_import_offline(fixture, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config, main
    manager, content, _ = fixture
    monkeypatch.setattr(main, 'create_provisioner', lambda: manager)
    monkeypatch.setattr(config, 'RUNTIME', replace(config.RUNTIME, package=tmp_path/'package', run_token='e'*64))
    monkeypatch.setattr(config, 'TEMP_DIR', manager.temp_root)
    app = main.create_app(tmp_path/'jobs')
    token = {'x-podcut-run-token': 'e'*64}
    with TestClient(app) as client:
        assert client.get('/api/models/whisper').status_code == 403
        assert client.post('/api/models/whisper/download').status_code == 403
        assert client.post('/api/models/whisper/offline', files={'file': ('model.zip', b'bad')}).status_code == 403
        assert client.get('/api/models/whisper', headers=token).json()['state'] == 'missing'
        archive = offline(manager, content)
        # Source upload must remain until request completes; route owns a COPY.
        with archive.open('rb') as file:
            response = client.post('/api/models/whisper/offline', headers=token, files={'file': ('model.zip', file, 'application/zip')})
        assert response.status_code == 200
        assert finished(manager)['state'] == 'ready'
        assert client.get('/api/models/whisper', headers=token).json()['state'] == 'ready'
        exported = client.get('/api/models/whisper/offline-package', headers=token)
        assert exported.status_code == 200 and exported.headers['content-type'] == 'application/zip'
        assert client.get('/api/models/whisper/offline-package').status_code == 403
