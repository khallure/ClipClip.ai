"""Pinned public Whisper provisioning, with no cloud key or interpreter install.

The embedded manifest is the trust root. User archives cannot substitute their
own URL, version, size or hash. Nothing is extracted by archive paths.
"""
from __future__ import annotations

import copy
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import struct
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid
import zipfile

from fastapi import UploadFile

from .runtime_profile import checked_path, IsolationError

MANIFEST_NAME = 'whisper-small-manifest.json'
OFFLINE_METADATA = 'podcut-model.json'
LICENSE_NAME = 'LICENSE.OpenAI-Whisper.txt'
CHUNK_BYTES = 256 * 1024
_verification_lock = threading.RLock()
_verified: dict[tuple, tuple[bool, str]] = {}


class ModelProvisioningError(RuntimeError):
    pass


class Cancelled(ModelProvisioningError):
    pass


class IncompleteDownload(ModelProvisioningError):
    pass


def load_manifest(path: Path | None = None) -> dict:
    if path is None:
        from . import config
        path = config.BACKEND / 'assets/models' / MANIFEST_NAME
    if path.stat().st_size > 16384:
        raise ModelProvisioningError('Manifest model bawaan tidak valid.')
    value = json.loads(path.read_text(encoding='utf-8'))
    files = value.get('files', [])
    if (value.get('schema') != 1 or value.get('model') != 'small'
            or value.get('repository') != 'Systran/faster-whisper-small'
            or not re.fullmatch('[a-f0-9]{40}', value.get('revision', ''))
            or len(files) != 4
            or {item.get('name') for item in files} != {'config.json','model.bin','tokenizer.json','vocabulary.txt'}
            or any(not isinstance(item.get('bytes'), int) or isinstance(item['bytes'], bool)
                   or item['bytes'] < 1 or not re.fullmatch('[a-f0-9]{64}', item.get('sha256', '')) for item in files)):
        raise ModelProvisioningError('Manifest model bawaan tidak valid.')
    return value


def _plain_file(path: Path, root: Path) -> os.stat_result:
    checked_path(path, root, 'Whisper model file')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ModelProvisioningError('Berkas model harus berupa file mandiri, bukan tautan.')
    return info


def _hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_model(directory: Path, manifest: dict, root: Path) -> Path:
    """Hash once per process/file identity; any stat change invalidates the cache."""
    directory = checked_path(directory, root, 'Whisper model directory')
    if directory.name != manifest['model']:
        raise ModelProvisioningError('Model ini belum memiliki manifest verifikasi yang didukung.')
    try:
        tokens = []
        for item in manifest['files']:
            info = _plain_file(directory / item['name'], root)
            if info.st_size != item['bytes']:
                raise ModelProvisioningError('Model Whisper belum lengkap atau ukurannya tidak cocok. Jalankan setup model.')
            tokens.append((item['name'], item['sha256'], info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev))
    except FileNotFoundError:
        raise ModelProvisioningError('Model Whisper belum tersedia. Jalankan setup model atau impor paket offline.') from None
    key = (str(directory), manifest['revision'], tuple(tokens))
    with _verification_lock:
        prior = _verified.get(key)
        if prior:
            if prior[0]:
                return directory
            raise ModelProvisioningError(prior[1])
        for item in manifest['files']:
            if _hash(directory / item['name']) != item['sha256']:
                message = 'Checksum model Whisper tidak cocok. Perbaiki melalui setup model; transkripsi belum diaktifkan.'
                _verified[key] = (False, message)
                raise ModelProvisioningError(message)
        # Re-check identities after hashing to reject a replacement during verification.
        after = []
        for item in manifest['files']:
            info = _plain_file(directory / item['name'], root)
            after.append((item['name'], item['sha256'], info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev))
        if tuple(after) != tuple(tokens):
            raise ModelProvisioningError('Model berubah saat verifikasi. Coba lagi setelah penyalinan selesai.')
        if len(_verified) > 32:
            _verified.clear()
        _verified[key] = (True, '')
    return directory


def require_verified_model(directory: Path) -> Path:
    from . import config
    return verify_model(directory, load_manifest(), config.MODELS_DIR)


def _trusted_url(url: str) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    domains = ('huggingface.co', 'hf.co', 'xethub.hf.co')
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(host == domain or host.endswith('.' + domain) for domain in domains)):
        raise ModelProvisioningError('Tujuan unduhan model tidak termasuk sumber HTTPS yang diizinkan.')


class ModelRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        _trusted_url(newurl)
        return super().redirect_request(request, file, code, message, headers, newurl)


def _open_model(url: str):
    import certifi
    from urllib.request import HTTPSHandler
    _trusted_url(url)
    context = ssl.create_default_context(cafile=certifi.where())
    opener = build_opener(ProxyHandler({}), ModelRedirects(), HTTPSHandler(context=context))
    return opener.open(Request(url, headers={'User-Agent': 'PODCUTAI-ModelSetup/1', 'Accept-Encoding': 'identity'}), timeout=15)


def _zip_directory_bounds(path: Path) -> None:
    """Reject huge central directories/ZIP64 before ZipFile allocates entries."""
    size = path.stat().st_size
    with path.open('rb') as stream:
        if stream.read(4) != b'PK\x03\x04':
            raise ModelProvisioningError('Paket offline harus berupa ZIP model PODCUT.')
        stream.seek(max(0, size - 65557))
        trailer = stream.read(65557)
    offset = trailer.rfind(b'PK\x05\x06')
    if offset < 0 or len(trailer) - offset < 22:
        raise ModelProvisioningError('Direktori paket ZIP tidak lengkap.')
    _, disk, central_disk, entries_disk, entries, central_size, central_offset, comment = struct.unpack_from('<4s4H2LH', trailer, offset)
    if (disk != 0 or central_disk != 0 or entries_disk != entries or not 5 <= entries <= 6
            or central_size > 65536 or central_offset + central_size > size
            or offset + 22 + comment != len(trailer)):
        raise ModelProvisioningError('Struktur paket ZIP tidak didukung atau melebihi batas.')


class ModelProvisioner:
    def __init__(self, models_root: Path, temp_root: Path, cache_root: Path, manifest: dict,
                 license_path: Path, opener=None):
        self.models_root, self.temp_root, self.cache_root = models_root, temp_root, cache_root
        self.manifest, self.license_path = manifest, license_path
        self.directory = checked_path(models_root/'whisper'/manifest['model'], models_root, 'Whisper model directory')
        self.total = sum(item['bytes'] for item in manifest['files'])
        self.max_archive_bytes = self.total + 16 * 1024**2
        self.opener = opener or _open_model
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.thread: threading.Thread | None = None
        self._state = self._initial('missing')

    def _initial(self, state):
        return {'state': state, 'model': self.manifest['model'], 'revision': self.manifest['revision'],
                'repository': self.manifest['repository'], 'license': 'MIT', 'location': str(self.directory),
                'total_bytes': self.total, 'completed_bytes': 0, 'verified_bytes': 0, 'received_bytes': 0,
                'speed_bps': None, 'percent': 0.0, 'filename': None, 'attempt': 0, 'operation': None,
                'message': 'Model lokal memerlukan setup satu kali.', 'error_code': None}

    def _update(self, **values):
        with self.lock:
            self._state.update(values)

    def status(self) -> dict:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return copy.deepcopy(self._state)
        try:
            verify_model(self.directory, self.manifest, self.models_root)
        except (ModelProvisioningError, IsolationError) as error:
            with self.lock:
                if self._state['state'] not in ('failed', 'cancelled'):
                    self._state.update(state='missing', message=str(error))
        else:
            self._update(state='ready', completed_bytes=self.total, verified_bytes=self.total,
                         percent=100.0, speed_bps=None, error_code=None,
                         message='Model lokal terverifikasi. Transkripsi siap tanpa API key.')
        with self.lock:
            return copy.deepcopy(self._state)

    def busy(self):
        with self.lock:
            return bool(self.thread and self.thread.is_alive())

    def start(self, archive: Path | None = None) -> dict:
        with self.lock:
            if self.busy():
                raise ModelProvisioningError('Setup model sedang berjalan.')
            if archive is None and self.status()['state'] == 'ready':
                return copy.deepcopy(self._state)
            if archive is not None:
                checked_path(archive, self.temp_root, 'Uploaded offline model archive')
            self.cancelled.clear()
            self._state = self._initial('verifying')
            self._state.update(operation='offline' if archive else 'download', message='Memeriksa file model yang sudah tersedia.')
            self.thread = threading.Thread(target=self._run, args=(archive,), daemon=True, name='podcut-model-setup')
            self.thread.start()
            return copy.deepcopy(self._state)

    def cancel(self) -> dict:
        self.cancelled.set()
        return self.status()

    def close(self):
        self.cancelled.set()
        if self.thread:
            self.thread.join(timeout=.5)

    def _check_cancel(self):
        if self.cancelled.is_set():
            raise Cancelled('Setup model dibatalkan. File model yang sudah valid tetap dipertahankan.')

    def _stage(self) -> Path:
        directory = checked_path(self.temp_root/('model-setup-' + str(uuid.uuid4())), self.temp_root, 'Model setup staging')
        directory.mkdir(parents=True, exist_ok=False)
        return directory

    def _copy_stream(self, stream, target: Path, item: dict, valid_bytes: int, started: float):
        count, digest = 0, hashlib.sha256()
        with target.open('xb') as file:
            while True:
                self._check_cancel()
                chunk = stream.read(min(CHUNK_BYTES, item['bytes'] - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                if count > item['bytes']:
                    raise ModelProvisioningError('Ukuran file model melebihi manifest; file ditolak.')
                file.write(chunk)
                digest.update(chunk)
                elapsed = max(.001, time.monotonic()-started)
                with self.lock:
                    received = self._state['received_bytes'] + len(chunk)
                self._update(received_bytes=received, completed_bytes=valid_bytes+count,
                             percent=100*(valid_bytes+count)/self.total, speed_bps=received/elapsed)
        if count != item['bytes']:
            raise IncompleteDownload('Ukuran model tidak lengkap. Periksa koneksi lalu coba lagi.')
        if digest.hexdigest() != item['sha256']:
            raise ModelProvisioningError('Checksum unduhan model tidak cocok; file tidak diaktifkan.')

    def _download(self, stage: Path) -> list[dict]:
        completed = 0
        started = time.monotonic()
        for item in self.manifest['files']:
            self._check_cancel()
            destination = checked_path(self.directory/item['name'], self.models_root, 'Whisper asset')
            if destination.is_file():
                info = _plain_file(destination, self.models_root)
                if info.st_size == item['bytes'] and _hash(destination) == item['sha256']:
                    completed += item['bytes']
                    self._update(verified_bytes=completed, completed_bytes=completed, percent=100*completed/self.total)
                    continue
            target = stage/item['name']
            url = f"https://huggingface.co/{self.manifest['repository']}/resolve/{self.manifest['revision']}/{item['name']}"
            for attempt in range(1, 4):
                self._update(state='downloading', filename=item['name'], attempt=attempt,
                             message=f"Mengunduh {item['name']} dari sumber model publik.")
                try:
                    with self.opener(url) as response:
                        self._copy_stream(response, target, item, completed, started)
                    break
                except HTTPError as error:
                    retry = error.code in (408, 429, 500, 502, 503, 504)
                    if not retry or attempt == 3:
                        raise ModelProvisioningError(f'Sumber model mengembalikan HTTP {error.code}. Coba lagi atau gunakan paket offline.') from None
                except (URLError, TimeoutError, ConnectionError, OSError, IncompleteDownload, IncompleteRead):
                    if attempt == 3:
                        raise ModelProvisioningError('Unduhan terputus atau internet tidak tersedia. Coba lagi atau impor paket offline.') from None
                if target.exists():
                    target.unlink()
                if self.cancelled.wait(min(attempt, 2)):
                    self._check_cancel()
            completed += item['bytes']
            # Retain a verified file across later-file failures. The activation
            # gate still checks every pinned hash before opening Whisper.
            checked_path(self.directory, self.models_root, 'Whisper destination').mkdir(parents=True, exist_ok=True)
            destination = checked_path(destination, self.models_root, 'Whisper destination file')
            os.replace(target, destination)
            self._update(verified_bytes=completed, completed_bytes=completed, percent=100*completed/self.total)
        return []

    def _archive_entries(self, archive: Path):
        if archive.stat().st_size > self.max_archive_bytes:
            raise ModelProvisioningError('Paket offline melebihi batas ukuran model.')
        _zip_directory_bounds(archive)
        file = zipfile.ZipFile(archive)
        try:
            expected = {item['name']: item for item in self.manifest['files']}
            allowed = {*expected, OFFLINE_METADATA, LICENSE_NAME}
            names = file.namelist()
            if len(names) != len(set(names)) or set(names)-allowed or not ({*expected, OFFLINE_METADATA} <= set(names)):
                raise ModelProvisioningError('Isi ZIP harus berisi empat file model dan podcut-model.json pada root, tanpa file tambahan.')
            for info in file.infolist():
                mode = info.external_attr >> 16
                expected_size = expected[info.filename]['bytes'] if info.filename in expected else None
                wrong_size = info.file_size != expected_size if expected_size is not None else info.file_size > 16384
                if (info.flag_bits & 1 or info.is_dir() or stat.S_ISLNK(mode)
                        or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or wrong_size):
                    raise ModelProvisioningError('File ZIP tidak sesuai ukuran atau tipe yang diizinkan.')
            metadata = json.loads(file.read(OFFLINE_METADATA))
            if (not isinstance(metadata, dict) or metadata.get('schema') != 1 or metadata.get('model') != self.manifest['model']
                    or metadata.get('revision') != self.manifest['revision']):
                raise ModelProvisioningError('Versi paket offline berbeda dari manifest model bawaan.')
        except BaseException:
            file.close()
            raise
        return file

    def _offline(self, stage: Path, archive: Path) -> list[dict]:
        completed = 0
        started = time.monotonic()
        with self._archive_entries(archive) as file:
            for item in self.manifest['files']:
                self._update(state='copying', filename=item['name'], message='Menyalin dan memverifikasi paket model offline.')
                with file.open(item['name']) as stream:
                    self._copy_stream(stream, stage/item['name'], item, completed, started)
                completed += item['bytes']
                self._update(verified_bytes=completed)
        return self.manifest['files']

    def _run(self, archive: Path | None):
        stage = None
        try:
            stage = self._stage()
            changed = self._offline(stage, archive) if archive else self._download(stage)
            self._check_cancel()
            self.directory.mkdir(parents=True, exist_ok=True)
            checked_path(self.directory, self.models_root, 'Whisper destination')
            self._update(state='verifying', message='Memastikan checksum dan versi model sebelum mengaktifkan transkripsi.')
            for item in changed:
                self._check_cancel()
                target = checked_path(self.directory/item['name'], self.models_root, 'Whisper model destination')
                if target.exists():
                    _plain_file(target, self.models_root)
                os.replace(stage/item['name'], target)
            license_target = checked_path(self.directory/LICENSE_NAME, self.models_root, 'Whisper notice')
            if license_target.exists():
                _plain_file(license_target, self.models_root)
            license_stage = stage/LICENSE_NAME
            shutil.copyfile(self.license_path, license_stage)
            os.replace(license_stage, license_target)
            verify_model(self.directory, self.manifest, self.models_root)
            self._update(state='ready', verified_bytes=self.total, completed_bytes=self.total, percent=100.0,
                         speed_bps=None, filename=None, error_code=None,
                         message='Model lokal terverifikasi. Transkripsi siap tanpa API key.')
        except Cancelled as error:
            self._update(state='cancelled', message=str(error), speed_bps=None, error_code='cancelled')
        except (ModelProvisioningError, IsolationError, zipfile.BadZipFile, ValueError, OSError) as error:
            message = str(error) if isinstance(error, ModelProvisioningError) else 'Paket model tidak valid atau penyimpanan tidak tersedia. File model belum diaktifkan.'
            self._update(state='failed', message=message, speed_bps=None, error_code='model_setup_failed')
        finally:
            if stage is not None:
                checked_path(stage, self.temp_root, 'Model staging cleanup')
                shutil.rmtree(stage)
            if archive is not None and archive.exists():
                checked_path(archive, self.temp_root, 'Offline upload cleanup')
                archive.unlink()

    def offline_metadata(self) -> dict:
        return {'schema': 1, 'model': self.manifest['model'], 'repository': self.manifest['repository'],
                'revision': self.manifest['revision'], 'license': 'MIT', 'files': copy.deepcopy(self.manifest['files'])}

    def export_offline(self) -> Path:
        if self.busy():
            raise ModelProvisioningError('Tunggu setup model selesai sebelum menyiapkan paket offline.')
        verify_model(self.directory, self.manifest, self.models_root)
        root = checked_path(self.cache_root/'offline-models', self.cache_root, 'Offline model export')
        root.mkdir(parents=True, exist_ok=True)
        output = checked_path(root/f"whisper-small-{self.manifest['revision'][:12]}.zip", self.cache_root, 'Offline model archive')
        stage = checked_path(root/(str(uuid.uuid4())+'.zip'), self.cache_root, 'Offline archive staging')
        try:
            with zipfile.ZipFile(stage, 'x', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(OFFLINE_METADATA, json.dumps(self.offline_metadata(), indent=2))
                archive.writestr(LICENSE_NAME, self.license_path.read_bytes())
                for item in self.manifest['files']:
                    source = checked_path(self.directory/item['name'], self.models_root, 'Offline export source')
                    digest = hashlib.sha256()
                    with source.open('rb') as stream, archive.open(item['name'], 'w') as target:
                        while chunk := stream.read(CHUNK_BYTES):
                            digest.update(chunk)
                            target.write(chunk)
                    if digest.hexdigest() != item['sha256']:
                        raise ModelProvisioningError('Model berubah saat membuat paket offline. Coba lagi.')
            if output.exists():
                _plain_file(output, self.cache_root)
            os.replace(stage, output)
            return output
        finally:
            if stage.exists():
                stage.unlink()


def create_provisioner() -> ModelProvisioner:
    from . import config
    return ModelProvisioner(config.MODELS_DIR, config.TEMP_DIR, config.CACHE_DIR, load_manifest(),
                            config.BACKEND/'assets/models'/LICENSE_NAME)


def register_model_routes(app, get_manager):
    """Routes inherit the same localhost/run-token middleware as every API."""
    from fastapi import File, HTTPException, UploadFile
    from fastapi.responses import FileResponse
    from . import config

    @app.get('/api/models/whisper')
    def model_status():
        return get_manager().status()

    @app.post('/api/models/whisper/download')
    def model_download():
        try:
            return get_manager().start()
        except ModelProvisioningError as error:
            raise HTTPException(409, str(error)) from None

    @app.post('/api/models/whisper/cancel')
    def model_cancel():
        return get_manager().cancel()

    @app.get('/api/models/whisper/offline-manifest')
    def model_manifest():
        return get_manager().offline_metadata()

    @app.get('/api/models/whisper/offline-package')
    def model_export():
        try:
            manager = get_manager()
            path = manager.export_offline()
        except ModelProvisioningError as error:
            raise HTTPException(409, str(error)) from None
        return FileResponse(path, media_type='application/zip',
                            filename=f"podcut-whisper-small-{manager.manifest['revision'][:12]}.zip")

    @app.post('/api/models/whisper/offline')
    async def model_offline(file: UploadFile = File(...)):
        manager = get_manager()
        if manager.busy():
            raise HTTPException(409, 'Setup model sedang berjalan. Tunggu atau batalkan lebih dahulu.')
        archive = checked_path(config.TEMP_DIR/('offline-upload-'+str(uuid.uuid4())+'.zip'), config.TEMP_DIR, 'Offline upload')
        started = False
        try:
            count = 0
            with archive.open('xb') as target:
                while chunk := await file.read(CHUNK_BYTES):
                    count += len(chunk)
                    if count > manager.max_archive_bytes:
                        raise HTTPException(413, 'Paket model offline melebihi batas ukuran.')
                    target.write(chunk)
            result = manager.start(archive)
            started = True
            return result
        except ModelProvisioningError as error:
            raise HTTPException(409, str(error)) from None
        finally:
            await file.close()
            if not started and archive.exists():
                checked_path(archive, config.TEMP_DIR, 'Offline upload cleanup')
                archive.unlink()
