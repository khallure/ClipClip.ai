from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import time
import uuid
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app import runtime_profile, updater
from app.version import APP_VERSION


class _FakeResponse:
    def __init__(self, payload: bytes, *, chunk_size: int = 16384):
        self._buf = io.BytesIO(payload)
        self._chunk_size = chunk_size

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._buf.read()
        return self._buf.read(min(size, self._chunk_size))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._buf.close()


def _make_manifest(
    version: str = '1.0.1',
    payload: bytes = b'MZ' + b'A' * 4096,
    *,
    url: str | None = None,
    sha256: str | None = None,
    size: int | None = None,
    notes: str = 'Perbaikan UI & fitur baru.',
) -> tuple[bytes, dict]:
    filename = f'ClipClip.ai-Setup-{version}-x64.exe'
    digest = sha256 or hashlib.sha256(payload).hexdigest()
    obj = {
        'schema': 1,
        'product': 'ClipClip.ai',
        'platform': 'win32',
        'arch': 'x64',
        'version': version,
        'published_at': '2026-11-22T12:00:00Z',
        'notes': notes,
        'installer': {
            'filename': filename,
            'bytes': len(payload) if size is None else size,
            'sha256': digest,
            'url': url or f'https://releases.example.com/clipclip/{filename}',
        },
    }
    return json.dumps(obj).encode('utf-8'), obj


def _wait_terminal(mgr: updater.AppUpdater, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = mgr.status()
        if st['state'] not in {'downloading', 'verifying', 'checking'}:
            return st
        time.sleep(0.02)
    raise AssertionError(f'Updater did not finish in time: {mgr.status()}')


def _profile_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    profile = tmp_path / 'ClipClip.ai'
    cache = profile / 'cache'
    runtime = profile / 'runtime'
    cache.mkdir(parents=True)
    runtime.mkdir(parents=True)
    return profile, cache, runtime


def test_unconfigured_updater_makes_zero_network_calls_and_blocks_check(tmp_path):
    profile, cache, runtime = _profile_dirs(tmp_path)

    def forbidden_net(req, timeout):
        raise AssertionError('No network call allowed when unconfigured')

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url='',
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=True,
        run_id=str(uuid.uuid4()),
        urlopen=forbidden_net,
        min_installer_bytes=128,
    )
    st = mgr.status()
    assert st['state'] == 'unconfigured'
    assert st['configured'] is False
    assert st['feed_kind'] == 'none'
    with pytest.raises(updater.UpdateError) as exc:
        mgr.check()
    assert exc.value.code == 'unconfigured'
    assert exc.value.status_code == 409


@pytest.mark.parametrize('bad_url', [
    'http://releases.example.com/latest.json',
    'file:///C:/Windows/win.ini',
    'https://user:secret@releases.example.com/latest.json',
    'https://localhost/latest.json',
    'https://sub.localhost/latest.json',
    'https://nas.local/latest.json',
    'https://127.0.0.1/latest.json',
    'https://192.168.1.10/latest.json',
    'https://10.0.0.5/latest.json',
    'https://169.254.169.254/latest.json',
    'https://[::1]/latest.json',
    'https://intranet/latest.json',
    'https://releases.example.com/latest.json\r\nX-Injected: 1',
])
def test_url_validation_rejects_http_credentials_localhost_and_private_ips(bad_url):
    with pytest.raises(updater.UpdateError):
        updater.validate_https_url(bad_url)


def test_safe_redirect_handler_strips_authorization_and_cookie_across_hosts():
    handler = updater._SafeRedirectHandler(max_redirects=5)
    req = Request(
        'https://api.github.com/repos/khallure/ClipClip.ai/releases/assets/999',
        headers={'Authorization': 'Bearer ghp_synthetic_secret_token', 'Cookie': 'sid=123', 'Accept': 'application/octet-stream'},
    )
    redirected = handler.redirect_request(
        req, None, 302, 'Found', {},
        'https://objects.githubusercontent.com/github-production-release-asset/file.exe?sig=abc',
    )
    assert redirected is not None
    all_headers = {k.lower(): v for k, v in redirected.header_items()}
    assert 'authorization' not in all_headers
    assert 'cookie' not in all_headers
    assert all_headers.get('accept') == 'application/octet-stream'

    same_host = handler.redirect_request(
        req, None, 302, 'Found', {},
        'https://api.github.com/repos/khallure/ClipClip.ai/releases/assets/1000',
    )
    assert same_host is not None
    same_headers = {k.lower(): v for k, v in same_host.header_items()}
    assert same_headers.get('authorization') == 'Bearer ghp_synthetic_secret_token'

    with pytest.raises(updater.UpdateError):
        handler.redirect_request(req, None, 302, 'Found', {}, 'http://objects.githubusercontent.com/file.exe')

    req._clipclip_redirect_count = 5
    with pytest.raises(updater.UpdateError) as exc:
        handler.redirect_request(req, None, 302, 'Found', {}, 'https://objects.githubusercontent.com/file.exe')
    assert exc.value.code == 'too_many_redirects'


def test_direct_manifest_check_download_sha256_verify_cache_reuse_and_handoff(tmp_path):
    profile, cache, runtime = _profile_dirs(tmp_path)
    run_id = str(uuid.uuid4())
    payload = b'MZ' + (b'CLIPCLIP_INSTALLER_PAYLOAD_' * 200)
    manifest_bytes, manifest_obj = _make_manifest('1.0.1', payload)
    feed_url = 'https://releases.example.com/clipclip/latest.json'
    installer_url = manifest_obj['installer']['url']
    download_hits = 0

    def fake_open(req: Request, timeout: float):
        nonlocal download_hits
        headers = {k.lower(): v for k, v in req.header_items()}
        assert 'authorization' not in headers
        if req.full_url == feed_url:
            return _FakeResponse(manifest_bytes)
        if req.full_url == installer_url:
            download_hits += 1
            return _FakeResponse(payload, chunk_size=1024)
        raise AssertionError(f'Unexpected URL: {req.full_url}')

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url=feed_url,
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=True,
        run_id=run_id,
        urlopen=fake_open,
        min_installer_bytes=128,
    )
    checked = mgr.check()
    assert checked['state'] == 'available'
    assert checked['available_version'] == '1.0.1'
    assert checked['verified'] is False

    mgr.start_download()
    done = _wait_terminal(mgr)
    assert done['state'] == 'ready_to_install'
    assert done['verified'] is True
    assert done['verified_sha256'] == hashlib.sha256(payload).hexdigest()
    assert download_hits == 1

    # Re-checking reuses the already-verified cached installer without re-downloading.
    rechecked = mgr.check()
    assert rechecked['state'] == 'ready_to_install'
    assert rechecked['verified'] is True
    assert download_hits == 1

    installing = mgr.request_install()
    assert installing['state'] == 'installing'
    pending_file = runtime / 'pending-update.json'
    assert pending_file.is_file()
    handoff = json.loads(pending_file.read_text(encoding='utf-8'))
    assert handoff['schema'] == 1
    assert handoff['product'] == 'ClipClip.ai'
    assert handoff['run_id'] == run_id
    assert handoff['version'] == '1.0.1'
    assert handoff['sha256'] == hashlib.sha256(payload).hexdigest()
    assert Path(handoff['installer_path']).is_relative_to(cache / 'updates' / '1.0.1')


def test_private_github_releases_feed_authenticates_only_api_github_and_never_leaks_token(tmp_path):
    profile, cache, runtime = _profile_dirs(tmp_path)
    secret_token = 'test-synthetic-readonly-token-1234567890'
    payload = b'MZ' + (b'PRIVATE_RELEASE_' * 256)
    digest = hashlib.sha256(payload).hexdigest()
    manifest_bytes, _ = _make_manifest('1.1.0', payload)
    feed_url = 'https://api.github.com/repos/khallure/ClipClip.ai/releases/latest'
    manifest_asset_url = 'https://api.github.com/repos/khallure/ClipClip.ai/releases/assets/101'
    installer_asset_url = 'https://api.github.com/repos/khallure/ClipClip.ai/releases/assets/102'

    release_doc = {
        'tag_name': 'v1.1.0',
        'assets': [
            {'name': 'latest.json', 'url': manifest_asset_url, 'browser_download_url': 'https://github.com/khallure/ClipClip.ai/releases/download/v1.1.0/latest.json'},
            {'name': 'ClipClip.ai-Setup-1.1.0-x64.exe', 'url': installer_asset_url, 'digest': f'sha256:{digest}',
             'browser_download_url': 'https://github.com/khallure/ClipClip.ai/releases/download/v1.1.0/ClipClip.ai-Setup-1.1.0-x64.exe'},
        ],
    }

    seen_auth: list[tuple[str, str | None]] = []

    def fake_open(req: Request, timeout: float):
        headers = {k.lower(): v for k, v in req.header_items()}
        seen_auth.append((req.full_url, headers.get('authorization')))
        if req.full_url == feed_url:
            assert headers.get('authorization') == f'Bearer {secret_token}'
            return _FakeResponse(json.dumps(release_doc).encode('utf-8'))
        if req.full_url == manifest_asset_url:
            assert headers.get('authorization') == f'Bearer {secret_token}'
            assert headers.get('accept') == 'application/octet-stream'
            return _FakeResponse(manifest_bytes)
        if req.full_url == installer_asset_url:
            assert headers.get('authorization') == f'Bearer {secret_token}'
            assert headers.get('accept') == 'application/octet-stream'
            return _FakeResponse(payload)
        raise AssertionError(f'Unexpected URL: {req.full_url}')

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url=feed_url,
        github_token=secret_token,
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=True,
        run_id=str(uuid.uuid4()),
        urlopen=fake_open,
        min_installer_bytes=128,
    )
    st = mgr.check()
    assert st['state'] == 'available'
    assert st['feed_kind'] == 'github_releases'
    assert st['token_configured'] is True
    assert secret_token not in json.dumps(st)

    mgr.start_download()
    done = _wait_terminal(mgr)
    assert done['state'] == 'ready_to_install'
    assert secret_token not in json.dumps(done)

    mgr.request_install()
    assert secret_token not in (runtime / 'pending-update.json').read_text(encoding='utf-8')
    assert len(seen_auth) == 3


def test_github_asset_digest_mismatch_fails_closed(tmp_path):
    profile, cache, runtime = _profile_dirs(tmp_path)
    payload = b'MZ' + b'X' * 2048
    manifest_bytes, _ = _make_manifest('1.0.2', payload)
    feed_url = 'https://api.github.com/repos/khallure/ClipClip.ai/releases/latest'
    manifest_asset_url = 'https://api.github.com/repos/khallure/ClipClip.ai/releases/assets/201'
    release_doc = {
        'tag_name': 'v1.0.2',
        'assets': [
            {'name': 'latest.json', 'url': manifest_asset_url},
            {'name': 'ClipClip.ai-Setup-1.0.2-x64.exe', 'url': 'https://api.github.com/repos/khallure/ClipClip.ai/releases/assets/202',
             'digest': 'sha256:' + ('0' * 64)},
        ],
    }

    def fake_open(req: Request, timeout: float):
        if req.full_url == feed_url:
            return _FakeResponse(json.dumps(release_doc).encode('utf-8'))
        return _FakeResponse(manifest_bytes)

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url=feed_url,
        github_token='synthetic_token',
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=True,
        run_id=str(uuid.uuid4()),
        urlopen=fake_open,
        min_installer_bytes=128,
    )
    res = mgr.check()
    assert res['state'] == 'failed'
    assert res['error_code'] == 'checksum_mismatch'


@pytest.mark.parametrize('remote_version', ['1.0.0', '0.9.9'])
def test_downgrade_and_equal_version_are_up_to_date_and_cannot_download(tmp_path, remote_version):
    profile, cache, runtime = _profile_dirs(tmp_path)
    payload = b'MZ' + b'B' * 2048
    manifest_bytes, _ = _make_manifest(remote_version, payload)
    feed_url = 'https://releases.example.com/clipclip/latest.json'

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url=feed_url,
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=True,
        run_id=str(uuid.uuid4()),
        urlopen=lambda req, timeout: _FakeResponse(manifest_bytes),
        min_installer_bytes=128,
    )
    st = mgr.check()
    assert st['state'] == 'up_to_date'
    assert st['available_version'] is None
    with pytest.raises(updater.UpdateError) as exc:
        mgr.start_download()
    assert exc.value.code == 'no_update_selected'


def test_checksum_mismatch_deletes_downloaded_file_and_blocks_install(tmp_path):
    profile, cache, runtime = _profile_dirs(tmp_path)
    expected_payload = b'MZ' + b'G' * 2048
    tampered_payload = b'MZ' + b'E' * 2048
    manifest_bytes, manifest_obj = _make_manifest('1.0.3', expected_payload)
    feed_url = 'https://releases.example.com/clipclip/latest.json'
    installer_url = manifest_obj['installer']['url']

    def fake_open(req: Request, timeout: float):
        if req.full_url == feed_url:
            return _FakeResponse(manifest_bytes)
        return _FakeResponse(tampered_payload)

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url=feed_url,
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=True,
        run_id=str(uuid.uuid4()),
        urlopen=fake_open,
        min_installer_bytes=128,
    )
    assert mgr.check()['state'] == 'available'
    mgr.start_download()
    done = _wait_terminal(mgr)
    assert done['state'] == 'failed'
    assert done['error_code'] == 'checksum_mismatch'
    assert done['verified'] is False
    assert not list((cache / 'updates' / '1.0.3').glob('*.exe*'))
    with pytest.raises(updater.UpdateError):
        mgr.request_install()


def test_oversized_download_aborts_and_deletes_partial_file(tmp_path):
    profile, cache, runtime = _profile_dirs(tmp_path)
    declared_payload = b'MZ' + b'O' * 1024
    oversized_payload = b'MZ' + b'O' * 4096
    manifest_bytes, _ = _make_manifest('1.0.4', declared_payload)
    feed_url = 'https://releases.example.com/clipclip/latest.json'

    def fake_open(req: Request, timeout: float):
        if req.full_url == feed_url:
            return _FakeResponse(manifest_bytes)
        return _FakeResponse(oversized_payload, chunk_size=512)

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url=feed_url,
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=True,
        run_id=str(uuid.uuid4()),
        urlopen=fake_open,
        min_installer_bytes=128,
    )
    assert mgr.check()['state'] == 'available'
    mgr.start_download()
    done = _wait_terminal(mgr)
    assert done['state'] == 'failed'
    assert done['error_code'] == 'size_mismatch'
    assert not list((cache / 'updates' / '1.0.4').glob('*.exe*'))


def test_toctou_tampering_after_download_is_caught_at_install_and_deleted(tmp_path):
    profile, cache, runtime = _profile_dirs(tmp_path)
    payload = b'MZ' + b'T' * 2048
    manifest_bytes, manifest_obj = _make_manifest('1.0.5', payload)
    feed_url = 'https://releases.example.com/clipclip/latest.json'

    def fake_open(req: Request, timeout: float):
        if req.full_url == feed_url:
            return _FakeResponse(manifest_bytes)
        return _FakeResponse(payload)

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url=feed_url,
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=True,
        run_id=str(uuid.uuid4()),
        urlopen=fake_open,
        min_installer_bytes=128,
    )
    mgr.check()
    mgr.start_download()
    assert _wait_terminal(mgr)['state'] == 'ready_to_install'

    # Tamper with file on disk after verification (same size, different byte).
    target = cache / 'updates' / '1.0.5' / manifest_obj['installer']['filename']
    target.write_bytes(b'MZ' + b'Z' * 2048)

    with pytest.raises(updater.UpdateError) as exc:
        mgr.request_install()
    assert exc.value.code == 'checksum_mismatch'
    assert not target.exists()
    assert not (runtime / 'pending-update.json').exists()


@pytest.mark.parametrize('packaged,instance,expected_code', [
    (False, '', 'dev_mode_blocked'),
    (True, str(uuid.uuid4()), 'isolated_profile_blocked'),
])
def test_dev_mode_and_isolated_qa_profile_block_install_handoff(tmp_path, packaged, instance, expected_code):
    profile, cache, runtime = _profile_dirs(tmp_path)
    payload = b'MZ' + b'D' * 2048
    manifest_bytes, _ = _make_manifest('1.0.6', payload)
    feed_url = 'https://releases.example.com/clipclip/latest.json'

    def fake_open(req: Request, timeout: float):
        if req.full_url == feed_url:
            return _FakeResponse(manifest_bytes)
        return _FakeResponse(payload)

    mgr = updater.AppUpdater(
        current_version='1.0.0',
        feed_url=feed_url,
        cache_dir=cache,
        runtime_dir=runtime,
        profile_root=profile,
        packaged=packaged,
        profile_instance=instance,
        run_id=str(uuid.uuid4()),
        urlopen=fake_open,
        min_installer_bytes=128,
    )
    assert mgr.status()['can_install'] is False
    mgr.check()
    mgr.start_download()
    assert _wait_terminal(mgr)['state'] == 'ready_to_install'
    with pytest.raises(updater.UpdateError) as exc:
        mgr.request_install()
    assert exc.value.code == expected_code
    assert not (runtime / 'pending-update.json').exists()


def test_update_github_token_stays_private_and_never_enters_child_environment(tmp_path):
    profile, cache, runtime = _profile_dirs(tmp_path)
    pkg = tmp_path / 'pkg'
    pkg.mkdir()
    rp = runtime_profile.RuntimeProfile(
        project=pkg,
        root=profile,
        paths={'DATA_DIR': profile / 'data', 'TEMP': profile / 'temp'},
        settings={
            'UPDATE_FEED_URL': 'https://api.github.com/repos/khallure/ClipClip.ai/releases/latest',
            'UPDATE_AUTO_CHECK': 'false',
            'UPDATE_GITHUB_TOKEN': 'test-synthetic-never-forward-to-children',
        },
        run_id=str(uuid.uuid4()),
        run_token='a' * 64,
        package=pkg,
        resources=pkg / 'backend' / '_internal',
    )
    child_env = rp.environment()
    assert 'UPDATE_GITHUB_TOKEN' not in child_env
    assert 'test-synthetic-never-forward-to-children' not in json.dumps(child_env)
    assert child_env.get('UPDATE_FEED_URL') == 'https://api.github.com/repos/khallure/ClipClip.ai/releases/latest'


def test_fastapi_update_routes_and_health_expose_app_version_without_network(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    app = app_main.create_app(data_dir=data_dir)
    with TestClient(app) as client:
        health = client.get('/api/health')
        assert health.status_code == 200
        body = health.json()
        assert body['app_version'] == APP_VERSION
        assert body['updates']['current_version'] == APP_VERSION
        assert body['updates']['state'] == 'unconfigured'

        status = client.get('/api/updates')
        assert status.status_code == 200
        assert status.json()['current_version'] == APP_VERSION

        check = client.post('/api/updates/check')
        assert check.status_code == 409
