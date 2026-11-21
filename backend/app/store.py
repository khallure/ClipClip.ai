"""Atomic local job records and conservative disposable-cache cleanup."""
import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config

ACTIVE = {'queued', 'probing', 'validating', 'getting_metadata', 'retrieving_source', 'merging_media', 'verifying_media', 'transcribing', 'analyzing', 'exporting', 'reframing'}


def now():
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, root: Path | None = None):
        self.root = config.RUNTIME.validate_writable(root if root is not None else config.DATA_DIR, 'Job data directory')
        config.RUNTIME.validate_tree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def directory(self, ident: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise KeyError('Unknown project.')
        return config.RUNTIME.validate_writable(self.root / ident, 'Job directory')

    def get(self, ident: str) -> dict:
        with self.lock:
            try:
                return json.loads(config.RUNTIME.validate_writable(self.directory(ident) / 'job.json', 'Job record').read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                raise KeyError('Unknown or unreadable project.') from None

    def save(self, job: dict):
        with self.lock:
            directory = self.directory(job['id'])
            directory.mkdir(parents=True, exist_ok=True)
            job['updated_at'] = now()
            temp = config.RUNTIME.validate_writable(directory / 'job.json.tmp', 'Temporary job record')
            config.RUNTIME.validate_writable(directory / 'job.json', 'Job record')
            temp.write_text(json.dumps(job, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
            temp.replace(directory / 'job.json')

    def update(self, ident: str, **fields) -> dict:
        with self.lock:
            job = self.get(ident)
            job.update(fields)
            self.save(job)
            return job

    def create(self, title: str, youtube_url: str | None):
        job = {'id': uuid.uuid4().hex, 'title': title, 'youtube_url': youtube_url, 'source_name': None,
               'status': 'awaiting_upload', 'stage': 'input', 'progress': None,
               'message': 'Tautan tersimpan. Mulai pengambilan otomatis untuk memeriksa akses dan memproses video.' if youtube_url else 'Attach an owned or licensed source video to begin.',
               'error': None, 'warnings': [], 'created_at': now(), 'updated_at': now(), 'media': None,
               'candidates': [], 'exports': [], 'analysis_mode': None, 'transcript_ready': False, 'settings': None}
        self.save(job)
        return job

    def list(self):
        jobs = []
        for directory in self.root.iterdir():
            if directory.is_dir() and re.fullmatch(r'[a-f0-9]{32}', directory.name):
                config.RUNTIME.validate_writable(directory, 'Stored job directory')
                try:
                    jobs.append(self.get(directory.name))
                except KeyError:
                    pass
        return sorted(jobs, key=lambda j: j['updated_at'], reverse=True)

    @staticmethod
    def public(job: dict):
        return {k: v for k, v in job.items() if not k.startswith('_')}

    def recover(self):
        """A restart does not pretend interrupted work finished or discard earlier results."""
        config.RUNTIME.validate_tree(self.root)
        for job in self.list():
            if job['status'] in ACTIVE:
                for export in job['exports']:
                    if export['status'] in {'queued', 'processing'}:
                        export.update(status='failed', error='Server restarted during export. Retry this project.')
                if (job.get('auto_export') or {}).get('status') in {'queued', 'processing'}:
                    job['auto_export'].update(status='failed', message='Pemrosesan terhenti saat server dimulai ulang. Coba lagi untuk melanjutkan dari sumber yang tersimpan.')
                if (job.get('reframe') or {}).get('status') in {'queued', 'processing'}:
                    job['reframe'].update(status='failed', message='Analisis framing terputus. Metadata yang sudah tersimpan tetap tersedia; coba lagi bila diperlukan.')
                job.update(status='blocked', stage='interrupted', progress=None,
                           error='Server restarted during processing. Retry to resume using cached files.',
                           message='Processing interrupted; source, transcripts and completed exports are preserved.')
                self.save(job)
        for path in self.root.glob('*/**/*.partial*'):
            config.RUNTIME.validate_writable(path, 'Recovery path')
            if path.is_file():
                path.unlink(missing_ok=True)

    def usage(self):
        size = 0
        with self.lock:
            for path in self.root.rglob('*'):
                try:
                    if path.is_file():
                        size += path.stat().st_size
                except FileNotFoundError:
                    pass  # A worker may have just removed a disposable audio/partial file.
        return size

    def ensure_capacity(self, extra: int = 0):
        limit = config.STORAGE_LIMIT_GB * 1024 ** 3
        usage = self.usage()
        if usage + extra > limit:
            # These are reproducible caches. Never silently delete sources, transcripts or completed exports.
            for job in sorted(self.list(), key=lambda j: j['updated_at']):
                if job['status'] in ACTIVE:
                    continue
                path = self.directory(job['id']) / 'audio.wav'
                if path.exists():
                    usage -= path.stat().st_size
                    path.unlink(missing_ok=True)
                if usage + extra <= limit:
                    break
        if usage + extra > limit or shutil.disk_usage(self.root).free < extra + 100 * 1024 ** 2:
            raise ValueError('Local storage limit reached or disk is nearly full. Delete unused projects from History, or increase STORAGE_LIMIT_GB in backend/.env.')

    def delete(self, ident: str):
        with self.lock:
            job = self.get(ident)
            if job['status'] in ACTIVE:
                raise ValueError('Cancel processing and wait for it to stop before deleting this project.')
            path = self.directory(ident).resolve()
            # Verify the final absolute target immediately before recursive deletion.
            if path.parent != self.root or not re.fullmatch(r'[a-f0-9]{32}', path.name):
                raise ValueError('Invalid project directory.')
            shutil.rmtree(path)
