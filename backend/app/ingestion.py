"""Opt-in acquisition of one accessible YouTube source.

The application never equates a user's ownership checkbox with platform approval.
The enabled switch controls the feature. Legacy APPROVAL_REFERENCE and video ID
allowlist fields are ignored compatibility data, not platform proof or gates.
No approval reference, provider diagnostics or signed CDN URLs leave
this module. The optional downloader runs in a bounded child process; importing
the application does not import yt-dlp or start network requests.

``progress(fraction, message, details=None)`` uses fractions 0..1 (or None for indeterminate
work), with message prefixes validating/getting_metadata/retrieving_source/
verifying_media. An injected worker has the same keyword arguments as
``_run_worker`` and returns {filename, title, video_id}; it is intended for
controlled provider tests, never selected by a request parameter.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import config, media
from .process_runtime import external_popen
from .models import youtube_reference
from .source_progress import source_quality

YT_DLP_VERSION = '2026.10.26'
_ID = re.compile(r'[A-Za-z0-9_-]{11}\Z')
_CACHE_VERSION = 'v2'
_ENTRY = re.compile(rf'youtube-[A-Za-z0-9_-]{{11}}-h\d+-{_CACHE_VERSION}\Z')
_STAGE = re.compile(r'\.partial-[0-9a-f]{32}\Z')
_FILE = re.compile(r'source\.(mp4|mkv|webm)\Z')
_LOCK = threading.RLock()
_MESSAGES = {
    'disabled': 'Pengambilan YouTube belum diaktifkan. Unggah file video untuk melanjutkan.',
    'dependency': 'Paket pengambilan opsional belum siap. Jalankan setup pengambilan yang diizinkan atau unggah video.',
    'tools': 'FFmpeg, FFprobe, dan Node.js diperlukan untuk pengambilan otomatis. Unggah video atau periksa setup.',
    'network': 'Sumber belum dapat diambil karena koneksi atau layanan sumber bermasalah. Coba lagi atau unggah video.',
    'access': 'Sumber memerlukan akses khusus atau dibatasi platform. Pembatasan tidak dilewati; silakan unggah file yang diizinkan.',
    'unavailable': 'Video tidak tersedia untuk diambil. URL dan pengaturan tetap tersimpan; unggah file untuk melanjutkan.',
    'formats': 'Metadata video dapat dibaca, tetapi tidak ada format audio/video yang dapat diambil oleh adapter saat ini. Gunakan unggah file sumber; ini tidak berarti videonya dihapus.',
    'quota': 'Pengambilan dihentikan karena batas ukuran atau ruang penyimpanan. Kosongkan ruang atau unggah file yang lebih kecil.',
    'duration': 'Durasi sumber melebihi batas pengambilan. Unggah sumber yang lebih pendek.',
    'live': 'Siaran langsung atau video yang belum selesai belum didukung. Gunakan rekaman yang sudah selesai.',
    'invalid': 'Sumber tidak memiliki video dan audio yang dapat diverifikasi. Unggah file MP4 dengan video dan audio.',
    'policy': 'Alamat atau format stream sumber tidak didukung adapter ini. Gunakan satu video dari sumber yang didukung, atau unggah file sumber yang sah.',
    'timeout': 'Pengambilan sumber melewati batas waktu. Coba lagi atau unggah file video.',
    'worker': 'Pengambilan sumber belum berhasil. URL dan pengaturan tetap tersimpan; gunakan unggah video.',
}


class IngestionBlocked(media.MediaError):
    """Expected source/access failure: the job should retain its upload fallback."""
    def __init__(self, message: str, *, code: str = 'worker', retryable: bool = False,
                 recovery: str = 'upload'):
        self.code = code if code in _MESSAGES else 'worker'
        self.retryable = bool(retryable)
        self.recovery = recovery if recovery in {'upload', 'retry', 'settings'} else 'upload'
        super().__init__(message)


class IngestionCancelled(media.MediaCancelled):
    pass


def _blocked(code: str) -> IngestionBlocked:
    code = code if code in _MESSAGES else 'worker'
    transient = code in {'network', 'timeout'}
    recovery = 'retry' if transient else 'settings' if code in {
        'disabled', 'dependency', 'tools', 'quota'} else 'upload'
    return IngestionBlocked(_MESSAGES[code], code=code, retryable=transient, recovery=recovery)


def _number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return max(low, min(high, value)) if math.isfinite(value) else default
    except ValueError:
        return default


@dataclass(frozen=True)
class IngestPolicy:
    enabled: bool = False
    approval_reference: str = field(default='', repr=False)
    approved_video_ids: frozenset[str] = field(default_factory=frozenset, repr=False)
    timeout_sec: float = 1800
    max_bytes: int = 8192 * 1024 * 1024
    max_duration: float = 12 * 3600
    max_height: int = 2160
    cache_retention_days: float = 7

    @classmethod
    def from_env(cls) -> 'IngestPolicy':
        # Read at call time: disabling the adapter also applies to cached sources.
        # Legacy approval/allowlist text is deliberately not interpreted as proof.
        return cls(
            enabled=os.getenv('YOUTUBE_INGEST_ENABLED', '').strip().lower() in {'1', 'true', 'yes'},
            timeout_sec=_number('YOUTUBE_INGEST_TIMEOUT_SEC', 1800, 30, 7200),
            max_bytes=config.MAX_UPLOAD_MB * 1024 * 1024,
            max_duration=config.MAX_DURATION,
            max_height=int(_number('YOUTUBE_SOURCE_MAX_HEIGHT', 2160, 144, 4320)),
            cache_retention_days=_number('YOUTUBE_CACHE_RETENTION_DAYS', 7, 0, 365),
        )

    def gate(self, video_id: str | None = None) -> str | None:
        if not self.enabled:
            return 'disabled'
        return None


def _dependency_ready() -> bool:
    try:
        return (importlib.util.find_spec('yt_dlp') is not None
                and importlib.metadata.version('yt-dlp') == YT_DLP_VERSION
                and importlib.util.find_spec('yt_dlp_ejs') is not None)
    except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
        return False


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _regular_child(path: Path, directory: Path) -> bool:
    return not _linked(path) and path.is_file() and path.resolve().parent == directory.resolve()


def _remove_cache_entry(path: Path, root: Path) -> None:
    """Only remove a verified direct child owned by this cache, including on Windows."""
    if (not (_ENTRY.fullmatch(path.name) or _STAGE.fullmatch(path.name))
            or _linked(path) or path.resolve().parent != root.resolve()):
        return
    if path.is_dir():
        shutil.rmtree(path)


def _stage_size(directory: Path) -> int:
    # Downloader output is intentionally flat. Reject links/subdirectories outright.
    total = 0
    for path in directory.iterdir():
        if _linked(path) or not path.is_file():
            raise _blocked('policy')
        if path.name != '.worker.json':
            total += path.stat().st_size
    return total


def _check_cancel(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise IngestionCancelled('Pengambilan dibatalkan. URL, pengaturan, dan hasil sebelumnya tetap tersimpan.')


def _safe_numeric(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or value > 2**63-1:
        return None
    return int(value) if integer else float(value)


def _safe_text(value, limit=240):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'(?:https?://|www\.)\S+', '', value)
    return ' '.join(re.sub(r'[\x00-\x1f\x7f]', '', value).split())[:limit]


def sanitize_metadata(value: dict | None) -> dict:
    """Provider display metadata only; media locations and raw diagnostics never leave."""
    value = value if isinstance(value, dict) else {}
    result = {'title': _safe_text(value.get('title')), 'duration': _safe_numeric(value.get('duration')),
              'width': _safe_numeric(value.get('width'), integer=True),
              'height': _safe_numeric(value.get('height'), integer=True),
              'fps': _safe_numeric(value.get('fps')), 'formats': []}
    if quality := source_quality(value.get('quality')):
        result['quality'] = quality
    for raw in (value.get('formats') or [])[:80] if isinstance(value.get('formats'), list) else []:
        if not isinstance(raw, dict):
            continue
        ident = _safe_text(raw.get('id'), 80)
        if not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,80}', ident):
            continue
        result['formats'].append({
            'id': ident, 'width': _safe_numeric(raw.get('width'), integer=True),
            'height': _safe_numeric(raw.get('height'), integer=True), 'fps': _safe_numeric(raw.get('fps')),
            'video_codec': _safe_text(raw.get('video_codec'), 40),
            'audio_codec': _safe_text(raw.get('audio_codec'), 40), 'ext': _safe_text(raw.get('ext'), 12),
            'size_bytes': _safe_numeric(raw.get('size_bytes'), integer=True),
            'has_video': raw.get('has_video') is True, 'has_audio': raw.get('has_audio') is True})
    return result


def sanitize_progress(value: dict | None) -> dict:
    value = value if isinstance(value, dict) else {}
    result = {name: _safe_numeric(value.get(name), integer=name in {
        'downloaded_bytes', 'total_bytes', 'total_bytes_estimate'}) for name in (
        'downloaded_bytes', 'total_bytes', 'total_bytes_estimate', 'speed_bps', 'eta_seconds', 'percent')}
    if result['percent'] is not None:
        result['percent'] = min(100.0, result['percent'])
    if value.get('phase') in {'validating', 'metadata', 'download', 'merge', 'verify', 'cache'}:
        result['phase'] = value['phase']
    if isinstance(value.get('cache_hit'), bool):
        result['cache_hit'] = value['cache_hit']
    if isinstance(value.get('metadata'), dict):
        result['metadata'] = sanitize_metadata(value['metadata'])
    for name in ('format_id', 'resolution', 'video_codec', 'audio_codec'):
        if isinstance(value.get(name), str):
            result[name] = _safe_text(value[name], 80)
    return result


def _emit(progress, fraction: float | None, message: str, details: dict | None = None) -> None:
    if progress:
        if details is None:
            progress(fraction, message)
            return
        # Adapt the legacy two-argument callback without catching TypeError from
        # inside a callback (which would otherwise duplicate mutations).
        try:
            inspect.signature(progress).bind(fraction, message, details)
        except (TypeError, ValueError):
            progress(fraction, message)
        else:
            progress(fraction, message, sanitize_progress(details))


def _capacity(ensure_capacity, extra: int = 0) -> None:
    if ensure_capacity:
        try:
            ensure_capacity(extra)
        except ValueError:
            raise _blocked('quota') from None


def _free_space(directory: Path, extra: int = 0) -> None:
    if shutil.disk_usage(directory).free < extra + 100 * 1024 * 1024:
        raise _blocked('quota')


def _stop_process(proc) -> None:
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        killer = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'taskkill.exe'
        try:
            with external_popen([str(killer), '/PID', str(proc.pid), '/T', '/F'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as stopper:
                try:
                    stopper.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    stopper.kill()
                    stopper.wait()
                    raise
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _run_worker(manifest: dict, stage: Path, *, cancelled=None, progress=None,
                ensure_capacity=None) -> dict:
    """Bounded JSONL protocol; provider diagnostics never reach the API or disk logs."""
    stage = config.RUNTIME.validate_writable(stage, 'Ingestion worker stage')
    config.RUNTIME.validate_project(sys.executable, 'Ingestion interpreter')
    request = config.RUNTIME.validate_writable(stage / '.worker.json', 'Ingestion request')
    request.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    environment = config.child_environment()
    environment.update({'PYTHONUTF8': '1', 'YTDLP_NO_PLUGINS': '1'})
    kwargs = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)} if os.name == 'nt' else {'start_new_session': True}
    try:
        proc = subprocess.Popen(config.worker_command('ingestion', request),
                                cwd=config.TEMP_DIR if config.RUNTIME.packaged else config.BACKEND,
                                env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kwargs)
    except OSError:
        raise _blocked('worker') from None
    events: queue.Queue = queue.Queue(maxsize=128)
    final: dict = {}

    def read_events():
        try:
            while line := proc.stdout.readline(65537):
                if len(line) > 65536:
                    final['error'] = 'worker'
                    return
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        continue
                    if event.get('event') == 'result':
                        final['result'] = event
                    elif event.get('event') == 'error':
                        final['error'] = event.get('code', 'worker')
                    elif event.get('event') in {'progress', 'metadata'}:
                        if event.get('event') == 'metadata':
                            final['metadata'] = sanitize_metadata(event.get('metadata'))
                            event = {'stage': 'getting_metadata', 'fraction': None,
                                     'details': {'phase': 'metadata', 'metadata': final['metadata']}}
                        try:
                            events.put_nowait(event)
                        except queue.Full:
                            pass
                except (UnicodeDecodeError, ValueError):
                    # Ignore library banners rather than exposing unsanitized diagnostics.
                    continue
        finally:
            proc.stdout.close()

    reader = threading.Thread(target=read_events, daemon=True)
    reader.start()
    began, checked = time.monotonic(), 0.0
    try:
        while True:
            _check_cancel(cancelled)
            now = time.monotonic()
            if now - began > manifest['timeout_sec']:
                raise _blocked('timeout')
            # Temporary mux output can coexist with input tracks; reserve up to 2x.
            if now - checked >= .2:
                if _stage_size(stage) > manifest['max_bytes'] * 2 + 1024 * 1024:
                    raise _blocked('quota')
                _free_space(stage)
                _capacity(ensure_capacity)
                checked = now
            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                phase = event.get('stage')
                if phase not in {'getting_metadata', 'retrieving_source', 'merging_media'}:
                    continue
                value = event.get('fraction')
                value = max(0, min(1, float(value))) if isinstance(value, (int, float)) and math.isfinite(value) else None
                message = {'getting_metadata': 'getting_metadata: Membaca metadata dan kualitas sumber.',
                           'retrieving_source': 'retrieving_source: Mengambil video dan audio sumber.',
                           'merging_media': 'merging_media: Menggabungkan stream audio dan video dengan FFmpeg.'}[phase]
                _emit(progress, value, message, event.get('details'))
            if final.get('error'):
                raise _blocked(final['error'])
            if proc.poll() is not None:
                reader.join(timeout=2)
                if final.get('error'):
                    raise _blocked(final['error'])
                result = final.get('result')
                if proc.returncode or not isinstance(result, dict):
                    raise _blocked('worker')
                if final.get('metadata') and not result.get('metadata'):
                    result['metadata'] = final['metadata']
                return result
            time.sleep(.05)
    finally:
        _stop_process(proc)
        reader.join(timeout=2)
        request.unlink(missing_ok=True)


class VideoSourceIngestor:
    def __init__(self, cache_dir: Path | None = None, *, policy: IngestPolicy | None = None,
                 worker: Callable | None = None, probe: Callable | None = None):
        self.cache_dir = config.RUNTIME.validate_writable(cache_dir if cache_dir is not None else config.CACHE_DIR / 'source_cache', 'Source cache directory')
        self._policy = policy
        self._worker = worker
        self._probe = probe or media.probe

    def capabilities(self) -> dict:
        policy = self._policy or IngestPolicy.from_env()
        reason = policy.gate()
        if not reason and not self._worker and not _dependency_ready():
            reason = 'dependency'
        if not reason and not self._worker and not all(config.binary(name) for name in ('ffmpeg', 'ffprobe', 'node')):
            reason = 'tools'
        return {'enabled': policy.enabled, 'available': reason is None,
                'reason': _MESSAGES[reason] if reason else 'Adapter sumber siap. Ketersediaan dan pembatasan akses diperiksa saat pengambilan.'}

    def _verified(self, path: Path, directory: Path, policy: IngestPolicy) -> dict:
        if not _FILE.fullmatch(path.name) or not _regular_child(path, directory):
            raise _blocked('policy')
        if not 0 < path.stat().st_size <= policy.max_bytes:
            raise _blocked('quota')
        info = self._probe(path)
        try:
            valid = (info.get('has_audio') and info.get('audio_codec') and info.get('video_codec')
                     and math.isfinite(float(info['duration'])) and 0 < float(info['duration']) <= policy.max_duration
                     and 2 <= int(info['width']) <= 16384 and 2 <= int(info['height']) <= policy.max_height
                     and math.isfinite(float(info['fps'])) and 0 < float(info['fps']) <= 1000)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise _blocked('invalid')
        # Return only actual probe fields, not arbitrary provider metadata.
        return {key: info[key] for key in ('duration', 'width', 'height', 'fps', 'video_codec', 'audio_codec', 'has_audio')}

    def _cached(self, entry: Path, video_id: str, policy: IngestPolicy, cancelled=None) -> tuple[Path, dict, dict] | None:
        try:
            if _linked(entry) or entry.resolve().parent != self.cache_dir or not entry.is_dir():
                return None
            descriptor = entry / 'metadata.json'
            if not _regular_child(descriptor, entry) or descriptor.stat().st_size > 65536:
                return None
            meta = json.loads(descriptor.read_text(encoding='utf-8'))
            if (meta.get('video_id') != video_id or meta.get('profile') != f'best-av-{policy.max_height}-{_CACHE_VERSION}'
                    or time.time() - float(meta['completed_at']) > policy.cache_retention_days * 86400):
                return None
            path = entry / meta['filename']
            info = self._verified(path, entry, policy)
            if _sha256(path, cancelled) != meta.get('sha256'):
                return None
            return path, info, meta
        except media.MediaCancelled:
            raise
        except (OSError, ValueError, KeyError, TypeError, media.MediaError):
            return None

    def _prune(self, policy: IngestPolicy) -> None:
        now = time.time()
        for path in self.cache_dir.iterdir():
            try:
                if _linked(path):
                    continue
                if _STAGE.fullmatch(path.name) and now - path.stat().st_mtime > 86400:
                    _remove_cache_entry(path, self.cache_dir)
                elif _ENTRY.fullmatch(path.name):
                    descriptor = path / 'metadata.json'
                    if not _regular_child(descriptor, path) or descriptor.stat().st_size > 65536:
                        continue
                    metadata = json.loads(descriptor.read_text(encoding='utf-8'))
                    if now - float(metadata['completed_at']) > policy.cache_retention_days * 86400:
                        _remove_cache_entry(path, self.cache_dir)
            except (OSError, ValueError, TypeError, KeyError):
                continue

    def ingest(self, url: str, job_directory: Path, *, cancelled=None, progress=None,
               ensure_capacity=None) -> dict:
        _check_cancel(cancelled)
        _emit(progress, 0, 'validating: Memeriksa URL dan pengaturan pengambilan.', {'phase': 'validating'})
        try:
            canonical = youtube_reference(url)
            video_id = parse_qs(urlsplit(canonical).query)['v'][0]
        except (ValueError, KeyError, TypeError):
            raise IngestionBlocked('Gunakan URL HTTPS YouTube untuk satu video yang valid, atau unggah file video.', code='policy') from None
        policy = self._policy or IngestPolicy.from_env()
        if reason := policy.gate(video_id):
            raise _blocked(reason)
        capability = self.capabilities()
        if not capability['available']:
            raise _blocked('dependency' if not _dependency_ready() else 'tools')
        if policy.max_bytes <= 0 or policy.max_duration <= 0 or policy.timeout_sec <= 0:
            raise _blocked('policy')
        job = config.RUNTIME.validate_writable(job_directory, 'Ingestion job directory')
        if _linked(job) or not job.is_dir():
            raise _blocked('policy')
        job = job.resolve()
        if job == self.cache_dir or self.cache_dir in job.parents:
            raise _blocked('policy')
        with _LOCK:
            _check_cancel(cancelled)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            _free_space(self.cache_dir)
            self._prune(policy)
            _capacity(ensure_capacity)
            entry = self.cache_dir / f'youtube-{video_id}-h{policy.max_height}-{_CACHE_VERSION}'
            cached = self._cached(entry, video_id, policy, cancelled)
            cache_hit = cached is not None
            if not cached:
                _remove_cache_entry(entry, self.cache_dir)
                stage = self.cache_dir / f'.partial-{uuid.uuid4().hex}'
                stage.mkdir()
                try:
                    manifest = {'url': canonical, 'video_id': video_id, 'directory': str(stage),
                                'max_bytes': policy.max_bytes, 'max_duration': policy.max_duration,
                                'max_height': policy.max_height, 'timeout_sec': policy.timeout_sec,
                                'ffmpeg': config.binary('ffmpeg'), 'node': config.binary('node')}
                    _emit(progress, None, 'getting_metadata: Membaca metadata dan kualitas sumber.', {'phase': 'metadata'})
                    def forward_progress(fraction, message, details=None):
                        _emit(progress, fraction, message, details)
                    result = (self._worker or _run_worker)(manifest, stage, cancelled=cancelled,
                                                           progress=forward_progress, ensure_capacity=ensure_capacity)
                    _check_cancel(cancelled)
                    if not isinstance(result, dict) or result.get('video_id') != video_id:
                        raise _blocked('policy')
                    filename = result.get('filename', '')
                    if not isinstance(filename, str) or not _FILE.fullmatch(filename):
                        raise _blocked('policy')
                    path = stage / filename
                    _emit(progress, None, 'verifying_media: Memeriksa video, audio, durasi, dan integritas sumber.', {'phase': 'verify'})
                    info = self._verified(path, stage, policy)
                    if _stage_size(stage) > policy.max_bytes * 2 + 1024 * 1024:
                        raise _blocked('quota')
                    title = _safe_text(result.get('title')) or f'YouTube {video_id}'
                    raw_metadata = result.get('metadata') if isinstance(result.get('metadata'), dict) else {}
                    source_metadata = sanitize_metadata({**raw_metadata, 'title': title,
                                                         'duration': info['duration'], 'width': info['width'],
                                                         'height': info['height'], 'fps': info['fps']})
                    metadata = {'video_id': video_id, 'filename': filename, 'title': title,
                                'completed_at': time.time(), 'profile': f'best-av-{policy.max_height}-{_CACHE_VERSION}',
                                'sha256': _sha256(path, cancelled), 'media': info,
                                'source_metadata': source_metadata, 'source_size_bytes': path.stat().st_size}
                    # Retain only the verified source and minimal local metadata.
                    for extra in stage.iterdir():
                        if extra != path:
                            if not _regular_child(extra, stage):
                                raise _blocked('policy')
                            extra.unlink()
                    (stage / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
                    _check_cancel(cancelled)
                    _capacity(ensure_capacity)
                    stage.rename(entry)
                    cached = entry / filename, info, metadata
                except (IngestionBlocked, IngestionCancelled):
                    raise
                except media.MediaCancelled:
                    raise IngestionCancelled('Pengambilan dibatalkan. URL dan pengaturan tetap tersimpan.') from None
                except (OSError, ValueError, media.MediaError):
                    raise _blocked('worker') from None
                finally:
                    _remove_cache_entry(stage, self.cache_dir)
            source, info, metadata = cached
            _check_cancel(cancelled)
            source_metadata = sanitize_metadata(metadata.get('source_metadata') or {
                'title': metadata['title'], **info})
            if cache_hit:
                _emit(progress, 1, 'verifying_media: Sumber terverifikasi digunakan kembali dari cache lokal.',
                      {'phase': 'cache', 'cache_hit': True, 'metadata': source_metadata,
                       'downloaded_bytes': source.stat().st_size, 'total_bytes': source.stat().st_size, 'percent': 100})
            _emit(progress, None, 'verifying_media: Menyiapkan sumber lokal terverifikasi untuk analisis.',
                  {'phase': 'verify', 'cache_hit': cache_hit, 'metadata': source_metadata})
            target = job / f'source-{video_id}{source.suffix}'
            temporary = job / f'.ingest-{uuid.uuid4().hex}{source.suffix}'
            try:
                if target.exists() or _linked(target):
                    # Never replace a prior local source, even on a repeated request.
                    if not _regular_child(target, job) or _sha256(target, cancelled) != metadata['sha256']:
                        raise _blocked('policy')
                else:
                    _capacity(ensure_capacity, source.stat().st_size)
                    _free_space(job, source.stat().st_size)
                    # Copy in chunks: a job source stays independent of cache retention,
                    # and cancellation is observed even while copying a large episode.
                    with source.open('rb') as incoming, temporary.open('xb') as outgoing:
                        while chunk := incoming.read(1024 * 1024):
                            _check_cancel(cancelled)
                            outgoing.write(chunk)
                    if _sha256(temporary, cancelled) != metadata['sha256']:
                        raise _blocked('invalid')
                    _check_cancel(cancelled)
                    os.replace(temporary, target)
                _emit(progress, 1, 'verifying_media: Sumber video dan audio siap dianalisis.',
                      {'phase': 'verify', 'cache_hit': cache_hit, 'metadata': source_metadata,
                       'downloaded_bytes': source.stat().st_size, 'total_bytes': source.stat().st_size, 'percent': 100})
                warnings = []
                if (info['video_codec'] != 'h264' or info['audio_codec'] != 'aac'
                        or target.suffix not in {'.mp4', '.webm'}):
                    warnings.append(f"Sumber terbaik dipertahankan: {info['width']}×{info['height']}, "
                                    f"{info['video_codec']}/{info['audio_codec']}. Kompatibilitas preview bergantung pada browser.")
                return {'source_path': target, 'media': info, 'title': metadata['title'],
                        'video_id': video_id, 'cache_hit': cache_hit, 'warnings': warnings,
                        'metadata': source_metadata, 'source_size_bytes': source.stat().st_size}
            except (OSError, ValueError):
                raise _blocked('quota') from None
            finally:
                temporary.unlink(missing_ok=True)


def _sha256(path: Path, cancelled=None) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            _check_cancel(cancelled)
            digest.update(chunk)
    return digest.hexdigest()
