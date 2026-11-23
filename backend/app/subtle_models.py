"""Dual-tier Apache-2.0 ONNX segmentation model verification and on-demand MODNet downloader."""
from __future__ import annotations

import copy
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import re
import ssl
import stat
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import uuid

from fastapi import FastAPI, HTTPException

from . import config
from .runtime_profile import IsolationError, checked_path
from .updater import UpdateError, validate_https_url

MANIFEST_NAME = 'segmentation-manifest.json'
CHUNK_BYTES = 256 * 1024
_verify_lock = threading.RLock()
_verified_cache: dict[tuple, tuple[bool, str]] = {}


class SegmentationModelError(RuntimeError):
    pass


def load_manifest(path: Path | None = None) -> dict:
    if path is None:
        path = config.RUNTIME.validate_project(
            config.BACKEND / 'assets' / 'models' / 'segmentation' / MANIFEST_NAME,
            'Segmentation model manifest',
        )
    if path.stat().st_size > 16384:
        raise SegmentationModelError('Manifest model segmentasi tidak valid.')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('schema') != 1 or not isinstance(data.get('bundled'), dict) or not isinstance(data.get('modnet'), dict):
        raise SegmentationModelError('Schema manifest segmentasi tidak valid.')
    return data


def _plain_file(path: Path, root: Path, label: str = 'Segmentation model file') -> os.stat_result:
    checked_path(path, root, label)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SegmentationModelError('Berkas model harus berupa file biasa, bukan symlink atau hardlink.')
    return info


def _hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_bundled_selfie(path: Path | None = None) -> Path:
    manifest = load_manifest()['bundled']
    if path is None:
        root = config.BACKEND / 'assets' / 'models' / 'segmentation'
        target = config.RUNTIME.validate_project(root / manifest['file'], 'Bundled segmentation model')
        boundary = config.RUNTIME.resources if config.RUNTIME.packaged else config.RUNTIME.project
    else:
        target = Path(path)
        boundary = target.parent
    info = _plain_file(target, boundary, 'Bundled segmentation model')
    if info.st_size != manifest['bytes']:
        raise SegmentationModelError('Ukuran model Selfie Segmentation bawaan tidak sesuai manifest.')
    key = (str(target), manifest['sha256'], info.st_size, info.st_mtime_ns, info.st_ino)
    with _verify_lock:
        cached = _verified_cache.get(key)
        if cached:
            if cached[0]:
                return target
            raise SegmentationModelError(cached[1])
        digest = _hash(target)
        if digest != manifest['sha256']:
            msg = 'Checksum model Selfie Segmentation bawaan tidak cocok.'
            _verified_cache[key] = (False, msg)
            raise SegmentationModelError(msg)
        _verified_cache[key] = (True, '')
    return target


def modnet_target_dir(models_dir: Path | None = None) -> Path:
    root = models_dir or config.MODELS_DIR
    return checked_path(root / 'segmentation' / 'modnet', root, 'MODNet model directory')


def verify_modnet(models_dir: Path | None = None) -> Path:
    root = models_dir or config.MODELS_DIR
    m = load_manifest()['modnet']
    directory = modnet_target_dir(root)
    target = checked_path(directory / m['filename'], root, 'MODNet ONNX model')
    if not target.exists():
        raise SegmentationModelError('Model MODNet (Akurat) belum diunduh.')
    info = _plain_file(target, root, 'MODNet ONNX model')
    if info.st_size != m['bytes']:
        raise SegmentationModelError('Ukuran berkas MODNet tidak sesuai manifest.')
    key = (str(target), m['sha256'], info.st_size, info.st_mtime_ns, info.st_ino)
    with _verify_lock:
        cached = _verified_cache.get(key)
        if cached:
            if cached[0]:
                return target
            raise SegmentationModelError(cached[1])
        if _hash(target) != m['sha256']:
            msg = 'Checksum model MODNet gagal verifikasi SHA-256.'
            _verified_cache[key] = (False, msg)
            raise SegmentationModelError(msg)
        _verified_cache[key] = (True, '')
    return target


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            validated = validate_https_url(newurl, label='URL pengalihan model MODNet')
        except UpdateError as exc:
            raise SegmentationModelError(str(exc)) from None
        return super().redirect_request(req, fp, code, msg, headers, validated)


def _default_urlopen(url: str, timeout: float):
    try:
        validated = validate_https_url(url, label='URL unduhan model MODNet')
    except UpdateError as exc:
        raise SegmentationModelError(str(exc)) from None
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ctx), _SafeRedirectHandler())
    return opener.open(Request(validated, headers={'User-Agent': 'ClipClip-Segmentation/1.0'}), timeout=timeout)


class SegmentationModelManager:
    """Thread-safe on-demand downloader for Tier-2 Apache-2.0 MODNet ONNX weights."""

    def __init__(self, *, models_dir: Path | None = None, urlopen=None):
        self.models_dir = models_dir or config.MODELS_DIR
        self.manifest = load_manifest()
        self._urlopen = urlopen or _default_urlopen
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = self._initial_state()

    def _initial_state(self) -> dict:
        bundled_ok = False
        try:
            verify_bundled_selfie()
            bundled_ok = True
        except (SegmentationModelError, IsolationError, OSError):
            pass
        modnet_ok = False
        try:
            verify_modnet(self.models_dir)
            modnet_ok = True
        except (SegmentationModelError, IsolationError, OSError):
            pass
        m = self.manifest['modnet']
        return {
            'bundled_ready': bundled_ok,
            'bundled_name': self.manifest['bundled']['name'],
            'modnet_state': 'ready' if modnet_ok else 'missing',
            'modnet_name': m['name'],
            'modnet_bytes': m['bytes'],
            'received_bytes': m['bytes'] if modnet_ok else 0,
            'percent': 100.0 if modnet_ok else 0.0,
            'message': 'MODNet (kualitas tinggi) siap digunakan.' if modnet_ok else 'Mode cepat (MediaPipe) siap; unduh MODNet (~25 MB) opsional untuk tepi rambut lebih halus.',
        }

    def status(self) -> dict:
        with self._lock:
            if self._state['modnet_state'] not in {'downloading', 'verifying'}:
                try:
                    verify_modnet(self.models_dir)
                    self._state.update(modnet_state='ready', received_bytes=self.manifest['modnet']['bytes'], percent=100.0,
                                       message='MODNet (kualitas tinggi) siap digunakan.')
                except (SegmentationModelError, IsolationError, OSError):
                    if self._state['modnet_state'] == 'ready':
                        self._state.update(modnet_state='missing', received_bytes=0, percent=0.0,
                                           message='Model MODNet belum terpasang.')
            return copy.deepcopy(self._state)

    def start_download(self) -> dict:
        with self._lock:
            if self._state['modnet_state'] in {'downloading', 'verifying'}:
                return copy.deepcopy(self._state)
            try:
                verify_modnet(self.models_dir)
                self._state.update(modnet_state='ready', percent=100.0, received_bytes=self.manifest['modnet']['bytes'])
                return copy.deepcopy(self._state)
            except (SegmentationModelError, IsolationError, OSError):
                pass
            self._cancel.clear()
            self._state.update(
                modnet_state='downloading',
                received_bytes=0,
                percent=0.0,
                message='Mengunduh model MODNet (~25 MB, Apache-2.0)...',
            )
            self._thread = threading.Thread(target=self._run_download, daemon=True, name='clipclip-modnet-dl')
            self._thread.start()
            return copy.deepcopy(self._state)

    def cancel(self) -> dict:
        with self._lock:
            self._cancel.set()
            if self._state['modnet_state'] == 'downloading':
                self._state.update(modnet_state='missing', message='Unduhan MODNet dibatalkan.')
            return copy.deepcopy(self._state)

    def close(self) -> None:
        self._cancel.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run_download(self) -> None:
        m = self.manifest['modnet']
        expected_bytes = int(m['bytes'])
        expected_sha = m['sha256']
        target_dir = modnet_target_dir(self.models_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        temp = checked_path(target_dir / f"{m['filename']}.partial-{uuid.uuid4().hex}", self.models_dir, 'MODNet partial download')
        final = checked_path(target_dir / m['filename'], self.models_dir, 'MODNet final target')
        hasher = hashlib.sha256()
        received = 0
        try:
            with self._urlopen(m['url'], 60.0) as response, temp.open('wb') as out:
                while not self._cancel.is_set():
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_bytes:
                        raise SegmentationModelError('Ukuran unduhan MODNet melebihi batas manifest.')
                    hasher.update(chunk)
                    out.write(chunk)
                    with self._lock:
                        self._state.update(
                            received_bytes=received,
                            percent=round(min(99.5, received / max(1, expected_bytes) * 100.0), 1),
                        )
            if self._cancel.is_set():
                raise SegmentationModelError('Unduhan MODNet dibatalkan.')
            if received != expected_bytes:
                raise SegmentationModelError('Unduhan MODNet terputus sebelum selesai.')
            with self._lock:
                self._state.update(modnet_state='verifying', message='Memverifikasi checksum SHA-256 MODNet...')
            if hasher.hexdigest() != expected_sha:
                raise SegmentationModelError('Checksum SHA-256 MODNet tidak cocok; berkas dihapus demi keamanan.')
            os.replace(temp, final)
            verify_modnet(self.models_dir)
            with self._lock:
                self._state.update(
                    modnet_state='ready',
                    received_bytes=expected_bytes,
                    percent=100.0,
                    message='MODNet (kualitas tinggi) terverifikasi SHA-256 dan siap dipakai.',
                )
        except (SegmentationModelError, HTTPError, URLError, IncompleteRead, OSError, IsolationError) as exc:
            msg = str(exc) if isinstance(exc, SegmentationModelError) else 'Gagal mengunduh MODNet. Periksa koneksi internet lalu coba lagi.'
            with self._lock:
                self._state.update(modnet_state='failed' if not self._cancel.is_set() else 'missing', message=msg)
        finally:
            temp.unlink(missing_ok=True)


def create_segmentation_manager() -> SegmentationModelManager:
    return SegmentationModelManager()


def register_segmentation_routes(app: FastAPI, get_manager) -> None:
    @app.get('/api/models/segmentation')
    def segmentation_status():
        return get_manager().status()

    @app.post('/api/models/segmentation/download', status_code=202)
    def segmentation_download():
        try:
            return get_manager().start_download()
        except SegmentationModelError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post('/api/models/segmentation/cancel')
    def segmentation_cancel():
        return get_manager().cancel()
