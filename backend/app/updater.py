"""Fail-closed built-in update checker, SHA-256 verifier, and Electron handoff.

Supports both direct HTTPS ``latest.json`` feeds and GitHub Releases API feeds
(public or private using an optional per-PC ``UPDATE_GITHUB_TOKEN`` stored only
in the user's private ``backend.env``). Never embeds secrets, never downloads on
startup unless explicitly configured, rejects downgrades, strips ``Authorization``
headers across cross-host redirects, verifies exact size and SHA-256 before
marking an installer ready, and delegates detached installer execution to the
Electron parent process outside the backend Windows Job Object.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
from http.client import IncompleteRead
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import uuid

from fastapi import FastAPI, HTTPException

from .runtime_profile import IsolationError, checked_path
from .version import APP_VERSION

PRODUCT_NAME = 'ClipClip.ai'
PLATFORM_NAME = 'win32'
ARCH_NAME = 'x64'
MANIFEST_MAX_BYTES = 16 * 1024
GITHUB_RELEASE_MAX_BYTES = 512 * 1024
MIN_INSTALLER_BYTES = 1024 * 1024  # 1 MiB
MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
CHUNK_BYTES = 256 * 1024
MAX_REDIRECTS = 5
SEMVER_RE = re.compile(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')
SHA256_RE = re.compile(r'^[a-f0-9]{64}$')
INSTALLER_NAME_RE = re.compile(r'^ClipClip\.ai-Setup-(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-x64\.exe$')
GITHUB_API_RELEASE_RE = re.compile(
    r'^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/(?:latest|tags/[A-Za-z0-9._-]+)$'
)
GITHUB_API_ASSET_RE = re.compile(
    r'^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/assets/[0-9]+$'
)
FORBIDDEN_HOST_SUFFIXES = ('.localhost', '.local', '.internal', '.lan', '.home.arpa')


class UpdateError(RuntimeError):
    """Sanitized, user-facing update error with safe error code."""

    def __init__(self, message: str, *, code: str = 'update_error', status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class UpdateCancelled(UpdateError):
    def __init__(self, message: str = 'Pengunduhan update dibatalkan.'):
        super().__init__(message, code='cancelled', status_code=409)


def parse_semver(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise UpdateError('Format versi rilis tidak valid.', code='invalid_manifest')
    match = SEMVER_RE.fullmatch(value.strip())
    if not match:
        raise UpdateError('Format versi rilis harus x.y.z.', code='invalid_manifest')
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer_version(remote: str, current: str) -> bool:
    return parse_semver(remote) > parse_semver(current)


def validate_https_url(url: str, *, label: str = 'URL update') -> str:
    if not isinstance(url, str):
        raise UpdateError(f'{label} tidak valid.', code='invalid_url')
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 2048 or any(ch in cleaned for ch in ' \t\r\n\0'):
        raise UpdateError(f'{label} tidak valid atau mengandung karakter terlarang.', code='invalid_url')
    parsed = urlsplit(cleaned)
    if parsed.scheme.lower() != 'https':
        raise UpdateError(f'{label} wajib menggunakan HTTPS.', code='insecure_url')
    if parsed.username is not None or parsed.password is not None:
        raise UpdateError(f'{label} tidak boleh menyertakan kredensial inline.', code='invalid_url')
    host = (parsed.hostname or '').strip().lower()
    if not host or host == 'localhost' or host.endswith(FORBIDDEN_HOST_SUFFIXES):
        raise UpdateError(f'Host {label} tidak diizinkan.', code='blocked_host')
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host:
            raise UpdateError(f'Host {label} tidak valid.', code='blocked_host') from None
    else:
        if (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            raise UpdateError(f'Alamat IP privat/lokal pada {label} ditolak.', code='blocked_host')
    return cleaned


def is_github_release_feed(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme.lower() == 'https'
            and (parsed.hostname or '').lower() == 'api.github.com'
            and bool(GITHUB_API_RELEASE_RE.fullmatch(parsed.path)))


def is_github_asset_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme.lower() == 'https'
            and (parsed.hostname or '').lower() == 'api.github.com'
            and bool(GITHUB_API_ASSET_RE.fullmatch(parsed.path)))


def parse_release_manifest(
    raw: bytes,
    *,
    min_installer_bytes: int = MIN_INSTALLER_BYTES,
) -> dict[str, Any]:
    if not raw or len(raw) > MANIFEST_MAX_BYTES:
        raise UpdateError('Manifest update (latest.json) kosong atau terlalu besar.', code='invalid_manifest')
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError):
        raise UpdateError('Manifest update (latest.json) bukan JSON yang valid.', code='invalid_manifest') from None
    if not isinstance(data, dict):
        raise UpdateError('Format manifest update tidak valid.', code='invalid_manifest')
    if (data.get('schema') != 1 or isinstance(data.get('schema'), bool)
            or data.get('product') != PRODUCT_NAME
            or data.get('platform') != PLATFORM_NAME
            or data.get('arch') != ARCH_NAME):
        raise UpdateError('Manifest update tidak cocok dengan produk ClipClip.ai win32-x64.', code='invalid_manifest')

    version = data.get('version')
    parse_semver(version)

    installer = data.get('installer')
    if not isinstance(installer, dict):
        raise UpdateError('Bagian installer pada manifest update tidak lengkap.', code='invalid_manifest')
    filename = installer.get('filename')
    if not isinstance(filename, str) or not INSTALLER_NAME_RE.fullmatch(filename):
        raise UpdateError('Nama file installer pada manifest tidak valid.', code='invalid_manifest')
    expected_filename = f'ClipClip.ai-Setup-{version}-x64.exe'
    if filename != expected_filename:
        raise UpdateError('Nama file installer tidak cocok dengan versi manifest.', code='invalid_manifest')

    size = installer.get('bytes')
    if (not isinstance(size, int) or isinstance(size, bool)
            or size < min_installer_bytes or size > MAX_INSTALLER_BYTES):
        raise UpdateError('Ukuran installer pada manifest di luar batas aman.', code='invalid_manifest')

    sha256 = installer.get('sha256')
    if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256.strip().lower()):
        raise UpdateError('Checksum SHA-256 pada manifest tidak valid.', code='invalid_manifest')
    sha256 = sha256.strip().lower()

    url = validate_https_url(installer.get('url', ''), label='URL installer')
    notes = data.get('notes', '')
    if not isinstance(notes, str):
        notes = ''
    notes = notes.strip()[:4000]
    published_at = data.get('published_at', '')
    if not isinstance(published_at, str) or len(published_at) > 64:
        published_at = ''

    return {
        'schema': 1,
        'product': PRODUCT_NAME,
        'platform': PLATFORM_NAME,
        'arch': ARCH_NAME,
        'version': version.strip(),
        'published_at': published_at.strip(),
        'notes': notes,
        'installer': {
            'filename': filename,
            'bytes': size,
            'sha256': sha256,
            'url': url,
        },
    }


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Fail-closed HTTPS redirect handler that strips credentials across hosts."""

    def __init__(self, *, max_redirects: int = MAX_REDIRECTS):
        super().__init__()
        self._max_redirects = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        count = getattr(req, '_clipclip_redirect_count', 0) + 1
        if count > self._max_redirects:
            raise UpdateError('Terlalu banyak pengalihan (redirect) saat mengambil update.', code='too_many_redirects')
        validated = validate_https_url(newurl, label='URL pengalihan update')
        old_parts = urlsplit(req.full_url)
        new_parts = urlsplit(validated)
        same_origin = (
            (old_parts.hostname or '').lower() == (new_parts.hostname or '').lower()
            and (old_parts.port or 443) == (new_parts.port or 443)
        )
        new_req = super().redirect_request(req, fp, code, msg, headers, validated)
        if new_req is None:
            return None
        new_req._clipclip_redirect_count = count
        if not same_origin:
            for attr in ('headers', 'unredirected_hdrs'):
                bag = getattr(new_req, attr, None)
                if isinstance(bag, dict):
                    for key in list(bag):
                        if key.lower() in {'authorization', 'cookie', 'proxy-authorization'}:
                            bag.pop(key, None)
        return new_req


def _default_urlopen(request: Request, timeout: float):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=context),
        _SafeRedirectHandler(max_redirects=MAX_REDIRECTS),
    )
    return opener.open(request, timeout=timeout)


def _plain_file(path: Path, root: Path, label: str = 'Update file') -> os.stat_result:
    checked_path(path, root, label)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UpdateError('Berkas update harus berupa file biasa, bukan tautan/hardlink.', code='unsafe_path')
    return info


def _hash_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _read_bounded(stream, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UpdateError('Respons metadata update melebihi batas ukuran aman.', code='oversized_response')
        chunks.append(chunk)
    return b''.join(chunks)


def _write_json_atomic(path: Path, root: Path, payload: dict[str, Any], label: str) -> Path:
    target = checked_path(path, root, label)
    checked_path(target.parent, root, label).mkdir(parents=True, exist_ok=True)
    temp = checked_path(target.with_name(f'{target.name}.tmp-{uuid.uuid4().hex}'), root, label)
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    try:
        with temp.open('wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


class AppUpdater:
    """Thread-safe update checker, SHA-256 verifier, and handoff writer."""

    def __init__(
        self,
        *,
        current_version: str = APP_VERSION,
        feed_url: str = '',
        github_token: str = '',
        auto_check: bool = False,
        cache_dir: Path,
        runtime_dir: Path,
        profile_root: Path,
        packaged: bool,
        profile_instance: str = '',
        run_id: str = '',
        urlopen: Callable[[Request, float], Any] | None = None,
        min_installer_bytes: int = MIN_INSTALLER_BYTES,
    ):
        parse_semver(current_version)
        self.current_version = current_version
        self._raw_feed_url = (feed_url or '').strip()
        self._github_token = (github_token or '').strip()
        self._auto_check = bool(auto_check)
        self._profile_root = checked_path(profile_root, profile_root, 'Update profile root')
        self._cache_dir = checked_path(cache_dir, self._profile_root, 'Update cache directory')
        self._runtime_dir = checked_path(runtime_dir, self._profile_root, 'Update runtime directory')
        self._packaged = bool(packaged)
        self._profile_instance = (profile_instance or '').strip()
        self._run_id = (run_id or '').strip()
        self._urlopen = urlopen or _default_urlopen
        self._min_installer_bytes = min_installer_bytes

        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._download_url: str = ''
        self._download_is_github_asset: bool = False
        self._installer_path: Path | None = None

        self._feed_url: str = ''
        self._feed_kind: str = 'none'
        self._feed_host: str = ''
        self._config_error: str = ''
        if self._raw_feed_url:
            try:
                self._feed_url = validate_https_url(self._raw_feed_url, label='UPDATE_FEED_URL')
                self._feed_host = (urlsplit(self._feed_url).hostname or '').lower()
                self._feed_kind = 'github_releases' if is_github_release_feed(self._feed_url) else 'direct_manifest'
            except UpdateError as exc:
                self._config_error = str(exc)

        initial_state = 'unconfigured' if not self._feed_url else 'idle'
        initial_message = (
            self._config_error
            if self._config_error
            else 'Feed update belum dikonfigurasi. Isi UPDATE_FEED_URL di backend.env untuk mengaktifkan pemeriksaan update.'
            if not self._feed_url
            else 'Siap memeriksa update ClipClip.ai.'
        )
        self._state: dict[str, Any] = {
            'current_version': self.current_version,
            'configured': bool(self._feed_url),
            'feed_kind': self._feed_kind,
            'feed_host': self._feed_host,
            'auto_check': self._auto_check,
            'token_configured': bool(self._github_token),
            'packaged': self._packaged,
            'can_install': self._packaged and not self._profile_instance,
            'state': initial_state,
            'available_version': None,
            'published_at': None,
            'release_notes': '',
            'filename': None,
            'total_bytes': 0,
            'received_bytes': 0,
            'percent': 0.0,
            'speed_bps': None,
            'expected_sha256': None,
            'verified_sha256': None,
            'verified': False,
            'checked_at': None,
            'message': initial_message,
            'error_code': 'invalid_url' if self._config_error else None,
        }

    def close(self) -> None:
        self._cancel_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def _build_request(self, url: str, *, accept: str, allow_github_auth: bool = False) -> Request:
        validated = validate_https_url(url, label='URL update')
        headers = {
            'User-Agent': f'ClipClip.ai-Updater/{self.current_version}',
            'Accept': accept,
        }
        host = (urlsplit(validated).hostname or '').lower()
        if allow_github_auth and self._github_token and host == 'api.github.com':
            headers['Authorization'] = f'Bearer {self._github_token}'
            headers['X-GitHub-Api-Version'] = '2022-11-28'
        return Request(validated, headers=headers, method='GET')

    def _fetch_bytes(self, url: str, *, accept: str, max_bytes: int, allow_github_auth: bool = False) -> bytes:
        request = self._build_request(url, accept=accept, allow_github_auth=allow_github_auth)
        try:
            with self._urlopen(request, 20.0) as response:
                return _read_bounded(response, max_bytes)
        except UpdateError:
            raise
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise UpdateError(
                    'Akses ke feed update ditolak (401/403). Periksa UPDATE_GITHUB_TOKEN lokal jika repo bersifat privat.',
                    code='auth_failed',
                    status_code=502,
                ) from None
            if exc.code == 404:
                raise UpdateError('Rilis atau manifest latest.json belum ditemukan pada feed update.', code='not_found', status_code=502) from None
            raise UpdateError(f'Server update mengembalikan status HTTP {exc.code}.', code='http_error', status_code=502) from None
        except (URLError, TimeoutError, OSError, IncompleteRead):
            raise UpdateError('Tidak dapat terhubung ke server update. Periksa koneksi internet lalu coba lagi.', code='network_error', status_code=502) from None

    def _resolve_manifest(self) -> tuple[dict[str, Any], str, bool]:
        if self._feed_kind == 'github_releases':
            release_raw = self._fetch_bytes(
                self._feed_url,
                accept='application/vnd.github+json',
                max_bytes=GITHUB_RELEASE_MAX_BYTES,
                allow_github_auth=True,
            )
            try:
                release = json.loads(release_raw.decode('utf-8-sig'))
            except (UnicodeDecodeError, ValueError):
                raise UpdateError('Respons GitHub Releases tidak valid.', code='invalid_manifest', status_code=502) from None
            assets = release.get('assets') if isinstance(release, dict) else None
            if not isinstance(assets, list):
                raise UpdateError('Rilis GitHub tidak memiliki daftar asset.', code='invalid_manifest', status_code=502)
            manifest_asset = next((a for a in assets if isinstance(a, dict) and a.get('name') == 'latest.json'), None)
            if manifest_asset is None:
                raise UpdateError('Rilis GitHub terbaru belum menyertakan file latest.json.', code='manifest_missing', status_code=502)
            manifest_url = manifest_asset.get('url') if self._github_token else (manifest_asset.get('browser_download_url') or manifest_asset.get('url'))
            if not manifest_url:
                raise UpdateError('URL asset latest.json pada rilis GitHub tidak lengkap.', code='invalid_manifest', status_code=502)
            use_api_manifest = is_github_asset_url(manifest_url)
            manifest_raw = self._fetch_bytes(
                manifest_url,
                accept='application/octet-stream' if use_api_manifest else 'application/json',
                max_bytes=MANIFEST_MAX_BYTES,
                allow_github_auth=use_api_manifest,
            )
            manifest = parse_release_manifest(manifest_raw, min_installer_bytes=self._min_installer_bytes)
            filename = manifest['installer']['filename']
            installer_asset = next((a for a in assets if isinstance(a, dict) and a.get('name') == filename), None)
            if installer_asset is None:
                raise UpdateError(f'File installer {filename} tidak ditemukan pada rilis GitHub.', code='asset_missing', status_code=502)
            gh_digest = installer_asset.get('digest')
            if isinstance(gh_digest, str) and gh_digest.startswith('sha256:'):
                gh_hex = gh_digest.split(':', 1)[1].strip().lower()
                if gh_hex != manifest['installer']['sha256']:
                    raise UpdateError('Checksum SHA-256 pada latest.json berbeda dari digest rilis GitHub.', code='checksum_mismatch', status_code=502)
            if self._github_token and isinstance(installer_asset.get('url'), str) and is_github_asset_url(installer_asset['url']):
                return manifest, validate_https_url(installer_asset['url'], label='URL asset installer'), True
            download_url = installer_asset.get('browser_download_url') or manifest['installer']['url']
            return manifest, validate_https_url(download_url, label='URL installer'), False

        manifest_raw = self._fetch_bytes(
            self._feed_url,
            accept='application/json',
            max_bytes=MANIFEST_MAX_BYTES,
            allow_github_auth=False,
        )
        manifest = parse_release_manifest(manifest_raw, min_installer_bytes=self._min_installer_bytes)
        return manifest, manifest['installer']['url'], False

    def _updates_dir(self, version: str) -> Path:
        parse_semver(version)
        return checked_path(self._cache_dir / 'updates' / version, self._cache_dir, 'Version update directory')

    def _cached_verified_installer(self, version: str, filename: str, expected_bytes: int, expected_sha256: str) -> Path | None:
        update_dir = self._updates_dir(version)
        target = checked_path(update_dir / filename, self._cache_dir, 'Cached installer')
        if not target.exists():
            return None
        try:
            info = _plain_file(target, self._cache_dir, 'Cached installer')
            if info.st_size == expected_bytes and _hash_file(target) == expected_sha256:
                return target
        except (UpdateError, IsolationError, OSError):
            return None
        return None

    def check(self) -> dict[str, Any]:
        with self._lock:
            if self._state['state'] in {'downloading', 'verifying', 'installing'}:
                raise UpdateError('Proses unduhan atau pemasangan update sedang berjalan.', code='busy', status_code=409)
            if not self._feed_url:
                raise UpdateError(self._state['message'], code='unconfigured', status_code=409)
            self._state.update(state='checking', error_code=None, message='Memeriksa rilis terbaru ClipClip.ai...')

        try:
            manifest, download_url, is_gh_asset = self._resolve_manifest()
        except UpdateError as exc:
            with self._lock:
                self._state.update(
                    state='failed',
                    error_code=exc.code,
                    message=str(exc),
                    checked_at=datetime.now(timezone.utc).isoformat(),
                )
                return copy.deepcopy(self._state)

        checked_at = datetime.now(timezone.utc).isoformat()
        remote_version = manifest['version']
        installer = manifest['installer']

        with self._lock:
            if not is_newer_version(remote_version, self.current_version):
                self._download_url = ''
                self._download_is_github_asset = False
                self._installer_path = None
                self._state.update(
                    state='up_to_date',
                    available_version=None,
                    published_at=manifest['published_at'] or None,
                    release_notes='',
                    filename=None,
                    total_bytes=0,
                    received_bytes=0,
                    percent=0.0,
                    speed_bps=None,
                    expected_sha256=None,
                    verified_sha256=None,
                    verified=False,
                    checked_at=checked_at,
                    error_code=None,
                    message=f'ClipClip.ai v{self.current_version} sudah merupakan versi terbaru.',
                )
                return copy.deepcopy(self._state)

            self._download_url = download_url
            self._download_is_github_asset = is_gh_asset
            cached = self._cached_verified_installer(
                remote_version, installer['filename'], installer['bytes'], installer['sha256']
            )
            if cached is not None:
                self._installer_path = cached
                self._state.update(
                    state='ready_to_install',
                    available_version=remote_version,
                    published_at=manifest['published_at'] or None,
                    release_notes=manifest['notes'],
                    filename=installer['filename'],
                    total_bytes=installer['bytes'],
                    received_bytes=installer['bytes'],
                    percent=100.0,
                    speed_bps=None,
                    expected_sha256=installer['sha256'],
                    verified_sha256=installer['sha256'],
                    verified=True,
                    checked_at=checked_at,
                    error_code=None,
                    message=f'Update ClipClip.ai v{remote_version} sudah tersedia di cache dan SHA-256 terverifikasi.',
                )
            else:
                self._installer_path = None
                self._state.update(
                    state='available',
                    available_version=remote_version,
                    published_at=manifest['published_at'] or None,
                    release_notes=manifest['notes'],
                    filename=installer['filename'],
                    total_bytes=installer['bytes'],
                    received_bytes=0,
                    percent=0.0,
                    speed_bps=None,
                    expected_sha256=installer['sha256'],
                    verified_sha256=None,
                    verified=False,
                    checked_at=checked_at,
                    error_code=None,
                    message=f'Update ClipClip.ai v{remote_version} tersedia.',
                )
            return copy.deepcopy(self._state)

    def start_download(self) -> dict[str, Any]:
        with self._lock:
            if self._state['state'] in {'downloading', 'verifying'}:
                return copy.deepcopy(self._state)
            if self._state['state'] == 'ready_to_install':
                return copy.deepcopy(self._state)
            if self._state['state'] == 'installing':
                raise UpdateError('Pemasangan update sedang berlangsung.', code='busy', status_code=409)
            if not self._state.get('available_version') or not self._download_url or not self._state.get('expected_sha256'):
                raise UpdateError('Periksa update terlebih dahulu sebelum mengunduh.', code='no_update_selected', status_code=409)
            version = str(self._state['available_version'])
            filename = str(self._state['filename'])
            expected_bytes = int(self._state['total_bytes'])
            expected_sha256 = str(self._state['expected_sha256'])
            download_url = self._download_url
            is_gh_asset = self._download_is_github_asset
            if not is_newer_version(version, self.current_version):
                raise UpdateError('Downgrade versi ditolak.', code='downgrade_blocked', status_code=409)

            self._cancel_event.clear()
            self._state.update(
                state='downloading',
                received_bytes=0,
                percent=0.0,
                speed_bps=None,
                verified=False,
                verified_sha256=None,
                error_code=None,
                message=f'Mengunduh {filename}...',
            )
            thread = threading.Thread(
                target=self._download_worker,
                args=(version, filename, expected_bytes, expected_sha256, download_url, is_gh_asset),
                daemon=True,
                name='clipclip-update-download',
            )
            self._thread = thread
            thread.start()
            return copy.deepcopy(self._state)

    def _download_worker(
        self,
        version: str,
        filename: str,
        expected_bytes: int,
        expected_sha256: str,
        download_url: str,
        is_gh_asset: bool,
    ) -> None:
        update_dir = self._updates_dir(version)
        update_dir.mkdir(parents=True, exist_ok=True)
        target = checked_path(update_dir / filename, self._cache_dir, 'Installer target')
        part = checked_path(update_dir / f'{filename}.part-{uuid.uuid4().hex}', self._cache_dir, 'Partial installer')
        try:
            cached = self._cached_verified_installer(version, filename, expected_bytes, expected_sha256)
            if cached is not None:
                with self._lock:
                    self._installer_path = cached
                    self._state.update(
                        state='ready_to_install',
                        received_bytes=expected_bytes,
                        percent=100.0,
                        speed_bps=None,
                        verified=True,
                        verified_sha256=expected_sha256,
                        error_code=None,
                        message=f'Update ClipClip.ai v{version} terverifikasi SHA-256 dan siap dipasang.',
                    )
                return

            request = self._build_request(
                download_url,
                accept='application/octet-stream',
                allow_github_auth=is_gh_asset,
            )
            hasher = hashlib.sha256()
            received = 0
            started = time.monotonic()
            with self._urlopen(request, 60.0) as response, part.open('wb') as out:
                while True:
                    if self._cancel_event.is_set():
                        raise UpdateCancelled()
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_bytes:
                        raise UpdateError(
                            'Ukuran file installer melebihi batas manifest; unduhan dihentikan.',
                            code='size_mismatch',
                        )
                    out.write(chunk)
                    hasher.update(chunk)
                    elapsed = max(0.05, time.monotonic() - started)
                    with self._lock:
                        self._state.update(
                            received_bytes=received,
                            percent=round(min(99.9, (received / expected_bytes) * 100.0), 1),
                            speed_bps=round(received / elapsed),
                        )
                out.flush()
                os.fsync(out.fileno())

            if self._cancel_event.is_set():
                raise UpdateCancelled()

            with self._lock:
                self._state.update(
                    state='verifying',
                    received_bytes=received,
                    percent=100.0,
                    speed_bps=None,
                    message='Memverifikasi integritas SHA-256 installer...',
                )

            info = _plain_file(part, self._cache_dir, 'Downloaded installer')
            if info.st_size != expected_bytes:
                raise UpdateError(
                    'Ukuran file hasil unduhan tidak cocok dengan manifest. File dihapus.',
                    code='size_mismatch',
                )
            stream_digest = hasher.hexdigest()
            disk_digest = _hash_file(part)
            if stream_digest != expected_sha256 or disk_digest != expected_sha256:
                raise UpdateError(
                    'Verifikasi SHA-256 gagal! File installer tidak cocok dengan manifest resmi dan telah dihapus.',
                    code='checksum_mismatch',
                )

            os.replace(part, target)
            _plain_file(target, self._cache_dir, 'Verified installer')
            _write_json_atomic(
                update_dir / 'verified-update.json',
                self._cache_dir,
                {
                    'schema': 1,
                    'product': PRODUCT_NAME,
                    'version': version,
                    'filename': filename,
                    'bytes': expected_bytes,
                    'sha256': expected_sha256,
                    'verified_at': datetime.now(timezone.utc).isoformat(),
                },
                'Verified update metadata',
            )
            with self._lock:
                self._installer_path = target
                self._state.update(
                    state='ready_to_install',
                    received_bytes=expected_bytes,
                    percent=100.0,
                    speed_bps=None,
                    verified=True,
                    verified_sha256=expected_sha256,
                    error_code=None,
                    message=f'Update ClipClip.ai v{version} terverifikasi SHA-256 dan siap dipasang.',
                )
        except UpdateCancelled as exc:
            part.unlink(missing_ok=True)
            with self._lock:
                self._state.update(
                    state='cancelled',
                    speed_bps=None,
                    verified=False,
                    verified_sha256=None,
                    error_code=exc.code,
                    message=str(exc),
                )
        except (UpdateError, IsolationError) as exc:
            part.unlink(missing_ok=True)
            code = exc.code if isinstance(exc, UpdateError) else 'unsafe_path'
            with self._lock:
                self._state.update(
                    state='failed',
                    speed_bps=None,
                    verified=False,
                    verified_sha256=None,
                    error_code=code,
                    message=str(exc),
                )
        except (HTTPError, URLError, TimeoutError, OSError, IncompleteRead):
            part.unlink(missing_ok=True)
            with self._lock:
                self._state.update(
                    state='failed',
                    speed_bps=None,
                    verified=False,
                    verified_sha256=None,
                    error_code='network_error',
                    message='Pengunduhan installer terputus. Coba unduh ulang.',
                )
        finally:
            part.unlink(missing_ok=True)

    def cancel_download(self) -> dict[str, Any]:
        with self._lock:
            if self._state['state'] in {'downloading', 'verifying'}:
                self._cancel_event.set()
                self._state.update(message='Membatalkan pengunduhan update...')
            return copy.deepcopy(self._state)

    def request_install(self) -> dict[str, Any]:
        with self._lock:
            if not self._packaged:
                raise UpdateError(
                    'Instalasi update otomatis hanya aktif pada aplikasi desktop ClipClip.ai yang terpasang; pada mode DEV gunakan git pull atau scripts/build-installer.ps1.',
                    code='dev_mode_blocked',
                    status_code=409,
                )
            if self._profile_instance:
                raise UpdateError(
                    'Instalasi update otomatis dinonaktifkan pada profil QA terisolasi (--isolated-profile) agar instalasi utama Windows tidak tertimpa.',
                    code='isolated_profile_blocked',
                    status_code=409,
                )
            if self._state['state'] != 'ready_to_install' or not self._installer_path:
                raise UpdateError('Belum ada file update terverifikasi SHA-256 yang siap dipasang.', code='not_ready', status_code=409)
            version = str(self._state['available_version'])
            filename = str(self._state['filename'])
            expected_bytes = int(self._state['total_bytes'])
            expected_sha256 = str(self._state['expected_sha256'])
            if not is_newer_version(version, self.current_version):
                raise UpdateError('Downgrade versi ditolak.', code='downgrade_blocked', status_code=409)

            target = checked_path(self._updates_dir(version) / filename, self._cache_dir, 'Verified installer')
            info = _plain_file(target, self._cache_dir, 'Verified installer')
            if info.st_size != expected_bytes:
                raise UpdateError('Ukuran file installer berubah sebelum pemasangan; pemasangan dibatalkan.', code='size_mismatch', status_code=409)
            actual_sha256 = _hash_file(target)
            if actual_sha256 != expected_sha256:
                target.unlink(missing_ok=True)
                self._installer_path = None
                self._state.update(
                    state='failed',
                    verified=False,
                    verified_sha256=None,
                    error_code='checksum_mismatch',
                    message='Verifikasi ulang SHA-256 gagal tepat sebelum pemasangan! File dihapus.',
                )
                raise UpdateError(self._state['message'], code='checksum_mismatch', status_code=409)

            handoff = {
                'schema': 1,
                'product': PRODUCT_NAME,
                'run_id': self._run_id,
                'current_version': self.current_version,
                'version': version,
                'filename': filename,
                'installer_path': str(target),
                'bytes': expected_bytes,
                'sha256': expected_sha256,
                'requested_at': datetime.now(timezone.utc).isoformat(),
            }
            _write_json_atomic(
                self._runtime_dir / 'pending-update.json',
                self._runtime_dir,
                handoff,
                'Pending update handoff',
            )
            self._state.update(
                state='installing',
                error_code=None,
                message=f'Menutup ClipClip.ai dan memasang update v{version}...',
            )
            return copy.deepcopy(self._state)


def create_updater() -> AppUpdater:
    from . import config

    settings = config.RUNTIME.settings
    return AppUpdater(
        current_version=APP_VERSION,
        feed_url=settings.get('UPDATE_FEED_URL', ''),
        github_token=settings.get('UPDATE_GITHUB_TOKEN', ''),
        auto_check=settings.get('UPDATE_AUTO_CHECK', '').strip().lower() in {'1', 'true', 'yes'},
        cache_dir=config.CACHE_DIR,
        runtime_dir=config.RUNTIME_DIR,
        profile_root=config.RUNTIME.root,
        packaged=config.RUNTIME.packaged,
        profile_instance=config.RUNTIME.instance,
        run_id=config.RUNTIME.run_id,
    )


def register_update_routes(app: FastAPI, get_updater: Callable[[], AppUpdater]) -> None:
    @app.get('/api/updates')
    def update_status():
        return get_updater().status()

    @app.post('/api/updates/check')
    def update_check():
        try:
            return get_updater().check()
        except UpdateError as exc:
            raise HTTPException(exc.status_code, exc.args[0]) from None

    @app.post('/api/updates/download')
    def update_download():
        try:
            return get_updater().start_download()
        except UpdateError as exc:
            raise HTTPException(exc.status_code, exc.args[0]) from None

    @app.post('/api/updates/cancel')
    def update_cancel():
        return get_updater().cancel_download()

    @app.post('/api/updates/install')
    def update_install():
        try:
            return get_updater().request_install()
        except UpdateError as exc:
            raise HTTPException(exc.status_code, exc.args[0]) from None
