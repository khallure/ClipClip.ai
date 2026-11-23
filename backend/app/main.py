import hashlib
import hmac
import json
import logging
import math
import os
import queue
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from . import config

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from . import media
from .models import (
    AlignCaptionsRequest, AnalysisSettings, IngestSettings, CreateJob,
    ExportSettings, CaptionPreviewSettings, CaptionCorrections, SubtlePreviewSettings,
    validate_transcript,
)
from .caption_state import caption_transcript, corrections, has_words
from .captions import build_caption_document
from .subtle import styles as subtle_styles, pipeline as subtle_pipeline, renderer as subtle_renderer
from .subtle.group import to_srt as subtle_to_srt, to_vtt as subtle_to_vtt
from .runtime_profile import checked_path
from .ingestion import VideoSourceIngestor
from .source_progress import fresh_progress, update_progress
from .model_provisioning import create_provisioner, register_model_routes, load_manifest
from .subtle_models import create_segmentation_manager, register_segmentation_routes
from .updater import create_updater, register_update_routes
from .version import APP_VERSION
from .reframe_models import ReframeInterval, ReframeRequest, ReframePlan
from . import reframe_store
from .store import ACTIVE, JobStore, now

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
for noisy_logger in ('httpx', 'httpx2', 'httpcore', 'httpcore2'):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)
log = logging.getLogger('podcut.pipeline')
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.mpeg', '.mpg', '.ts', '.flv', '.ogg'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac'}


class BodyLimitExceeded(Exception):
    pass


class UploadLimitMiddleware:
    """Bound multipart data before FastAPI spools it, including chunked uploads."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or scope.get('method') != 'POST' or not path.endswith(('/source', '/transcript', '/models/whisper/offline')):
            return await self.app(scope, receive, send)
        if path.endswith('/models/whisper/offline'):
            limit = sum(item['bytes'] for item in load_manifest()['files']) + 16 * 1024**2
        else:
            limit = (config.MAX_UPLOAD_MB * 1024**2 if path.endswith('/source') else 10 * 1024**2) + 65536
        received = 0
        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get('body', b''))
            if received > limit:
                raise BodyLimitExceeded()
            return message
        try:
            return await self.app(scope, bounded_receive, send)
        except BodyLimitExceeded:
            from fastapi.responses import JSONResponse
            return await JSONResponse({'detail': 'Upload exceeds the configured size limit.'}, status_code=413)(scope, receive, send)


class Pipeline:
    def __init__(self, store: JobStore):
        self.store = store
        self.ingestor = VideoSourceIngestor(config.CACHE_DIR / 'source_cache')
        self.tasks = queue.Queue(maxsize=20)
        self.events: dict[str, threading.Event] = {}
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.work, daemon=True, name='podcut-worker')

    def start(self):
        self.store.recover()
        self.thread.start()

    def close(self):
        self.stopping.set()
        for event in list(self.events.values()):
            event.set()
        self.thread.join(timeout=6)

    def enqueue(self, ident: str, task: dict):
        with self.store.lock:
            job = self.store.get(ident)
            if job['status'] in ACTIVE:
                raise HTTPException(409, 'This project is already processing. Cancel it before starting another task.')
            if task['type'] != 'ingest' and (not job.get('_source') or not job['media']):
                raise HTTPException(409, 'Attach a source video before processing.')
            if self.tasks.full():
                raise HTTPException(503, 'Local queue is full. Wait for another job to finish, then retry.')
            self.events[ident] = threading.Event()
            job.update(status='queued', stage='queued', progress=None, error=None,
                       source_error=None, message='Queued for the local worker.', _last_task=task)
            if task['type'] in {'analysis', 'ingest'}:
                job['settings'] = {k: v for k, v in task['settings'].items() if k in AnalysisSettings.model_fields}
                if task['type'] == 'ingest':
                    job['ingestion'] = fresh_progress() if not job.get('_source') else update_progress(job.get('ingestion'), phase='ready')
                    job['auto_export'] = {'status': 'queued'} if task['settings'].get('auto_export') else None
            elif task['type'] == 'export':
                if (job.get('auto_export') or {}).get('export_id') == task['export_id']:
                    job['auto_export'].update(status='queued')
                for export in job['exports']:
                    if export['id'] == task['export_id']:
                        export.update(status='queued', error=None)
            elif task['type'] == 'reframe':
                job['reframe'] = {'status':'queued', 'start':task['settings']['start'],
                                  'end':task['settings']['end'], 'message':'Analisis shot dan wajah masuk antrean lokal.'}
            self.store.save(job)
            self.tasks.put_nowait((ident, task))
            return job

    def cancel(self, ident):
        with self.store.lock:
            job = self.store.get(ident)
            if job['status'] not in ACTIVE:
                return job
            event = self.events.get(ident)
            if event:
                event.set()
            else:
                # Upload/probe is synchronous and cannot be raced with another operation.
                raise HTTPException(409, 'Source upload is still being validated. Wait for validation to finish.')
            return self.store.update(ident, message='Cancellation requested. Waiting for the current operation to stop.')

    def work(self):
        while not self.stopping.is_set():
            try:
                ident, task = self.tasks.get(timeout=.25)
            except queue.Empty:
                continue
            event = self.events[ident]
            cancelled = lambda: event.is_set() or self.stopping.is_set()
            try:
                if cancelled():
                    raise media.MediaCancelled('Processing cancelled. Previous results are preserved.')
                log.info('job=%s stage=%s started', ident, task['type'])
                if task['type'] == 'analysis':
                    self.analyze(ident, task['settings'], cancelled)
                elif task['type'] == 'export':
                    self.export(ident, task, cancelled)
                elif task['type'] == 'align_captions':
                    self.align_captions(ident, cancelled, settings=task.get('settings'))
                elif task['type'] == 'ingest':
                    self.ingest(ident, task['settings'], cancelled)
                elif task['type'] == 'reframe':
                    self.reframe(ident, task['settings'], cancelled)
                else:
                    raise ValueError('Operasi pemrosesan tidak dikenal.')
            except Exception as exc:
                # Never print API exception bodies/headers, which could include sensitive service details.
                is_cancelled = cancelled() or isinstance(exc, media.MediaCancelled) or type(exc).__name__ == 'AnalysisCancelled'
                known = isinstance(exc, (media.MediaError, ValueError))
                message = 'Processing cancelled. Previous results are preserved.' if is_cancelled else str(exc) if known else 'Processing failed. Check the backend log and retry; source and previous results are preserved.'
                log.warning('job=%s stage=%s failed type=%s', ident, task['type'], type(exc).__name__)
                with self.store.lock:
                    job = self.store.get(ident)
                    job.update(status='cancelled' if is_cancelled else 'blocked' if known else 'failed',
                               stage='cancelled' if is_cancelled else 'error', progress=None,
                               error=None if is_cancelled else message, message=message)
                    if task['type'] == 'ingest' and not job.get('_source'):
                        job['ingestion'] = update_progress(job.get('ingestion'), phase='cancelled' if is_cancelled else 'failed')
                        if not is_cancelled:
                            recovery = getattr(exc, 'recovery', 'retry')
                            job['source_error'] = {'code': str(getattr(exc, 'code', 'worker'))[:40],
                                                   'retryable': bool(getattr(exc, 'retryable', True)),
                                                   'recovery': recovery if recovery in {'retry', 'upload', 'settings'} else 'upload'}
                    if task['type'] == 'reframe' and job.get('reframe'):
                        job['reframe'].update(status='cancelled' if is_cancelled else 'failed', message=message)
                    automatic = job.get('auto_export') or {}
                    active_automatic = automatic.get('status') in {'queued', 'processing'} and (
                        task['type'] == 'ingest' or (task['type'] == 'export' and task.get('export_id') == automatic.get('export_id')))
                    if active_automatic:
                        job['auto_export'].update(status='failed', message=message)
                    failed_export_id = task.get('export_id') if task['type'] == 'export' else automatic.get('export_id') if active_automatic else None
                    if failed_export_id:
                        for export in job['exports']:
                            if export['id'] == failed_export_id:
                                export.update(status='cancelled' if is_cancelled else 'failed', error=message)
                    self.store.save(job)
            finally:
                self.tasks.task_done()

    def reframe(self, ident: str, settings: dict, cancelled):
        from .reframe_detection import analyze_reframe
        job = self.store.get(ident)
        directory = self.store.directory(ident)
        source = directory/job['_source']
        start, end = settings['start'], settings['end']
        plan = reframe_store.manual_plan(source,job['media'],start,end)
        try:
            previous = reframe_store.load_plan(directory,plan['id'],source,job['media'])
        except (FileNotFoundError,ValueError):
            previous = None
        def progress(value, message):
            value = value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None
            current = self.store.get(ident).get('reframe') or {}
            self.store.update(ident, status='reframing', stage='reframing',
                              progress=None if value is None else round(max(0,min(1,value))*100,1), message=message,
                              reframe={**current,'status':'processing','message':message})
        active = settings.get('mode') == 'active_speaker'
        cached_faces = active and previous and previous.get('analysis',{}).get('scene_detector')
        if cached_faces:
            plan = previous
            progress(0,'Memakai shot dan face tracks tersimpan; transkripsi dan deteksi wajah tidak diulang.')
        else:
            progress(0,'Mendeteksi shot dan wajah pada klip secara lokal.')
            result = analyze_reframe(source, job['media'], start,end,cancelled=cancelled,
                                    progress=(lambda v,m:progress(v*.45,m)) if active else progress)
            analysis = result.get('analysis',{})
            plan.update(mode='auto_face',shots=result['shots'],analysis=analysis,warnings=result.get('warnings',analysis.get('warnings',[])))
        if active:
            from .active_speaker import analyze_active_speaker
            plan = analyze_active_speaker(source,job['media'],plan,directory,cancelled=cancelled,
                                          progress=lambda v,m:progress(.45+v*.55,m))
        if cancelled():
            raise media.MediaCancelled('Analisis framing dibatalkan; metadata sebelumnya tetap tersedia.')
        plan = reframe_store.preserve_manual(previous,plan)
        plan = reframe_store.save_plan(directory,plan)
        self.store.update(ident,status='ready',stage='reframe_ready',progress=100,error=None,
            reframe={'status':'ready','id':plan['id'],'start':start,'end':end,
                     'message':f"Framing tersimpan untuk {len(plan['shots'])} shot. Periksa shot yang ditandai review."},
            message='Analisis framing selesai. Transkrip dan rekomendasi V1 tidak diubah.')

    def ingest(self, ident: str, settings: dict, cancelled):
        job = self.store.get(ident)
        directory = self.store.directory(ident)
        if not job.get('_source'):
            def progress(fraction, message, details=None):
                stage = message.partition(':')[0]
                if stage not in {'validating', 'getting_metadata', 'retrieving_source', 'merging_media', 'verifying_media'}:
                    stage = 'retrieving_source'
                phase = {'validating':'validating', 'getting_metadata':'metadata', 'retrieving_source':'download',
                         'merging_media':'merge', 'verifying_media':'verify'}[stage]
                previous = self.store.get(ident).get('ingestion')
                safe_fraction = fraction if isinstance(fraction, (int, float)) and not isinstance(fraction, bool) and math.isfinite(fraction) else None
                self.store.update(ident, status=stage, stage=stage, progress=None if safe_fraction is None else round(max(0, min(1, safe_fraction))*100, 1),
                                  ingestion=update_progress(previous, details, phase), source_error=None,
                                  message=message.partition(':')[2].strip() or message)
            result = self.ingestor.ingest(job['youtube_url'], directory, cancelled=cancelled,
                                          progress=progress, ensure_capacity=self.store.ensure_capacity)
            if cancelled():
                raise media.MediaCancelled('Pengambilan dibatalkan. URL dan sumber yang sudah lengkap tetap tersimpan.')
            source = Path(result['source_path']).resolve()
            if not source.is_file() or source.parent != directory.resolve():
                raise media.MediaError('Sumber terverifikasi tidak ditemukan di folder proyek. Unggah file video untuk melanjutkan.')
            warnings = result.get('warnings', [])
            with self.store.lock:
                job = self.store.get(ident)
                job.update(_source=source.name, source_name=source.name, media=result['media'],
                           _source_warnings=warnings, warnings=warnings, source_cache_hit=bool(result['cache_hit']),
                           ingestion=update_progress(job.get('ingestion'), {'phase':'ready', 'cache_hit':bool(result['cache_hit']),
                                                       'metadata':result.get('metadata') or {**result['media'], 'title':result['title']},
                                                       'downloaded_bytes':result.get('source_size_bytes', source.stat().st_size)}), source_error=None,
                           status='verifying_media', stage='verifying_media', progress=100, error=None,
                           message='Sumber siap; transkripsi dan pencarian klip akan dimulai otomatis.')
                if job['title'] == 'Untitled episode':
                    job['title'] = result['title'][:160]
                self.store.save(job)
            try:
                media.make_poster(source, directory / 'poster.jpg')
            except media.MediaError:
                log.info('job=%s poster unavailable; source retained', ident)
        else:
            job = self.store.get(ident)
        if settings.get('manual_end') and settings['manual_end'] > job['media']['duration'] + .01:
            raise media.MediaError('Rentang pencarian berakhir setelah video sumber. Sesuaikan rentang lalu analisis ulang; sumber tetap tersimpan.')
        analysis_settings = {k: v for k, v in settings.items() if k in AnalysisSettings.model_fields}
        self.analyze(ident, analysis_settings, cancelled, finish_ready=not settings.get('auto_export'))
        if settings.get('auto_export'):
            self.auto_export(ident, settings, cancelled)

    def auto_export(self, ident: str, settings: dict, cancelled):
        job = self.store.get(ident)
        if not job['candidates']:
            self.store.update(ident, status='ready', stage='complete', progress=100,
                              auto_export={'status':'skipped', 'message':'Belum ada kandidat utuh yang lolos pemeriksaan. Coba durasi lain atau rentang manual.'})
            return
        candidate = job['candidates'][0]
        output = settings.get('output') or {}
        payload = ExportSettings(start=candidate['start'], end=candidate['end'], duration_preset=settings.get('duration_preset'), **output)
        directory = self.store.directory(ident)
        if payload.caption_style == 'poppins_highlight' and not has_words(caption_transcript(directory)):
            self.align_captions(ident, cancelled, finish_ready=False)
        export_id = uuid.uuid4().hex
        export_settings = payload.model_dump()
        if payload.reframe_id:
            export_settings['_reframe_plan'] = checked_reframe(self.store,ident,payload)
        task = {'type':'export', 'settings':export_settings, 'export_id':export_id}
        with self.store.lock:
            job = self.store.get(ident)
            job['exports'].append({'id':export_id, **payload.model_dump(),
                                   'width':1080 if payload.aspect == 'vertical' else job['media']['width'],
                                   'height':1920 if payload.aspect == 'vertical' else job['media']['height'],
                                   'status':'queued', 'error':None, 'created_at':now(), 'filename':None})
            job.update(auto_export={'status':'processing', 'export_id':export_id}, _last_task=task)
            self.store.save(job)
        self.export(ident, task, cancelled)

    def analyze(self, ident: str, settings: dict, cancelled, *, finish_ready=True):
        from .analysis import analyze_transcript
        job = self.store.get(ident)
        directory = self.store.directory(ident)
        transcript_path = directory / 'transcript.json'
        transcript = json.loads(transcript_path.read_text(encoding='utf-8')) if transcript_path.is_file() else None
        requested_language = settings['language']
        if transcript and not job.get('_transcript_imported') and requested_language != 'auto' and transcript['language'] != requested_language:
            transcript = None
        if transcript is None:
            if not job['media']['has_audio']:
                raise media.MediaError('Source has no audio track. Import a timestamped transcript or attach a source containing speech.')
            self.store.ensure_capacity(int(job['media']['duration'] * 32000))
            self.store.update(ident, status='transcribing', stage='transcribing', progress=0,
                              message='Preparing local speech transcription.')
            def transcription_progress(p, message):
                self.store.update(ident, progress=round(p * .6, 1), message=message)
            transcript = media.transcribe(directory / job['_source'], directory, job['media']['duration'],
                                         requested_language, transcription_progress, cancelled,
                                         vocab=settings.get('vocab', ''))
            if cancelled():
                raise media.MediaCancelled('Processing cancelled.')
            write_json(transcript_path, transcript)
            self.store.update(ident, transcript_ready=True)
            (directory / 'audio.wav').unlink(missing_ok=True)
        self.store.update(ident, status='analyzing', stage='analyzing', progress=60,
                          message='Scanning transcript contexts across the complete episode.')
        ai = config.ai_configuration()
        api_key = ai.api_key or None
        key_data = {'transcript': transcript, 'settings': settings, 'ai': bool(api_key), 'provider': ai.provider, 'model': ai.model, 'version': 5}
        cache_key = hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cache_path = directory / f'analysis-{cache_key}.json'
        def analysis_progress(p, message):
            self.store.update(ident, progress=round(60 + min(1, max(0, p)) * 35, 1), message=message)
        if cache_path.is_file():
            result = json.loads(cache_path.read_text(encoding='utf-8'))
        else:
            extra = {'provider': 'gemini'} if ai.provider == 'gemini' else {}
            result = analyze_transcript(transcript['segments'], settings, api_key=api_key, model=ai.model or config.OPENAI_MODEL,
                                        progress=analysis_progress, cancelled=cancelled, **extra)
            if result['mode'] == 'ai' or not api_key:
                write_json(cache_path, result)
        if cancelled():
            raise media.MediaCancelled('Processing cancelled.')
        warnings = list(dict.fromkeys(job.get('_source_warnings', []) + result['warnings']))
        if not transcript.get('word_timestamps'):
            warnings.append('Imported segment timestamps are used. Word-level timing is unavailable; inspect boundaries before export.')
        self.store.update(ident, status='ready' if finish_ready else 'analyzing', stage='complete' if finish_ready else 'preparing_export',
                          progress=100 if finish_ready else 95, error=None,
                          message=f"{len(result['candidates'])} clip recommendations found across the transcript." if result['candidates'] else 'No self-contained moments met these settings. Try another duration or a wider manual range.',
                          candidates=result['candidates'], analysis_mode=result['mode'], warnings=warnings,
                          analysis_provider=result.get('provider', ai.provider if result['mode'] == 'ai' else 'heuristic'),
                          analysis_coverage=result.get('coverage', []), analysis_diagnostics=result.get('diagnostics'),
                          transcript_ready=True)
        log.info('job=%s analysis completed mode=%s count=%d', ident, result['mode'], len(result['candidates']))

    def export(self, ident, task, cancelled):
        job = self.store.get(ident)
        directory = self.store.directory(ident)
        settings = task['settings']
        self.store.ensure_capacity(int((settings['end'] - settings['start']) * 3_000_000))
        for export in job['exports']:
            if export['id'] == task['export_id']:
                export['status'] = 'processing'
        job.update(status='exporting', stage='exporting', progress=None,
                   message='Encoding H.264/AAC MP4. Download will unlock after FFprobe verifies the finished file.')
        self.store.save(job)
        transcript_path = directory / 'transcript.json'
        transcript = caption_transcript(directory)
        caption_edits = corrections(directory)
        extra = {}
        if caption_edits or settings['caption_style'] in {'poppins_highlight', 'subtle'}:
            extra['corrections'] = caption_edits
        if settings['caption_style'] == 'subtle':
            extra['cache_dir'] = directory / 'subtle_cache'
        if settings.get('_reframe_plan'):
            write_json(directory/'exports'/task['export_id']/'reframe_plan.json',settings['_reframe_plan'])
        info = media.export_clip(directory / job['_source'], directory / 'exports' / task['export_id'], settings,
                                 transcript, job['media'], cancelled, **extra)
        export_directory = directory / 'exports' / task['export_id']
        try:
            self.store.ensure_capacity()
        except ValueError:
            (export_directory / 'clip.mp4').unlink(missing_ok=True)
            raise
        has_captions = bool((export_directory / 'captions.srt').read_text(encoding='utf-8').strip())
        has_preview = (export_directory / 'preview.jpg').is_file()
        has_subtle_json = (export_directory / 'captions.json').is_file()
        with self.store.lock:
            job = self.store.get(ident)
            for export in job['exports']:
                if export['id'] == task['export_id']:
                    export.update(status='ready', error=None, width=info['width'], height=info['height'],
                                  media=info, filename='clip.mp4', has_captions=has_captions,
                                  has_preview=has_preview, has_subtle_json=has_subtle_json,
                                  subtle_qa=info.get('subtle_qa'))
            job.update(status='ready', stage='complete', progress=100, error=None,
                       message='Export verified. Your MP4' + (' and subtitles are' if has_captions else ' is') + ' ready to download.')
            if (job.get('auto_export') or {}).get('export_id') == task['export_id']:
                job['auto_export'].update(status='ready', message='Kandidat terbaik sudah diekspor dan siap diunduh.')
            self.store.save(job)
        log.info('job=%s export=%s verified %dx%d', ident, task['export_id'], info['width'], info['height'])

    def align_captions(self, ident, cancelled, *, finish_ready=True, settings: dict | None = None):
        job = self.store.get(ident)
        directory = self.store.directory(ident)
        opts = settings or {}
        if has_words(caption_transcript(directory)) and not opts.get('vocab') and not opts.get('hinglish'):
            self.store.update(ident, status='ready' if finish_ready else 'analyzing',
                              stage='captions_ready' if finish_ready else 'preparing_export', progress=100 if finish_ready else 95, error=None,
                              message='Waktu subtitle per kata sudah tersedia dan digunakan kembali.')
            return
        if not job['media']['has_audio']:
            raise media.MediaError('Video tidak memiliki audio untuk menyelaraskan subtitle.')
        self.store.ensure_capacity(int(job['media']['duration'] * 32000))
        working = directory / 'caption_work'
        working.mkdir(exist_ok=True)
        self.store.update(ident, status='transcribing', stage='caption_alignment', progress=None,
                          message='Menyelaraskan waktu kata dari audio asli secara lokal.')
        try:
            def progress(value, message):
                self.store.update(ident, progress=value, message=message)
            lang = opts.get('language') or 'auto'
            transcript = media.transcribe(
                directory / job['_source'], working, job['media']['duration'],
                lang, progress, cancelled,
                vocab=opts.get('vocab', ''), hinglish=bool(opts.get('hinglish')),
            )
            if cancelled():
                raise media.MediaCancelled('Penyelarasan subtitle dibatalkan.')
            if not has_words(transcript):
                raise media.MediaError('Tidak ditemukan waktu kata dari ucapan. Periksa audio sumber dan coba lagi.')
            write_json(directory / 'caption_alignment.json', transcript)
            self.store.update(ident, status='ready' if finish_ready else 'analyzing',
                              stage='captions_ready' if finish_ready else 'preparing_export', progress=100 if finish_ready else 95, error=None,
                              message='Subtitle per kata siap. Transkrip analisis asli tetap dipertahankan.')
        finally:
            (working / 'audio.wav').unlink(missing_ok=True)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def checked_reframe(store: JobStore, ident: str, payload: ExportSettings):
    if payload.aspect != 'vertical':
        raise ValueError('Metadata framing hanya dipakai untuk ekspor vertikal.')
    job = store.get(ident)
    directory = store.directory(ident)
    plan = reframe_store.load_plan(directory,payload.reframe_id,directory/job['_source'],job['media'])
    if payload.start < plan['start']-.00001 or payload.end > plan['end']+.00001:
        raise ValueError('Rentang klip berada di luar metadata framing. Siapkan framing untuk rentang yang dipilih.')
    return plan


def reframe_capabilities():
    try:
        from .reframe_detection import capabilities
        status = capabilities()
    except ImportError:
        status = {'available':False,'auto_face':False,'reason':'Dependensi deteksi lokal belum dipasang.'}
    from .active_speaker import capabilities as speaker_capabilities
    speaker = speaker_capabilities()
    if speaker['available'] and status.get('available'):
        status['reason']='Deteksi shot/wajah dan model active speaker lokal eksperimental siap.'
    return {**status,'manual':True,'active_speaker':speaker['available'] and status.get('available',False),'device':'cpu',
            'active_speaker_details':speaker}


def create_app(data_dir: Path | None = None):
    config.RUNTIME.validate_writable(data_dir if data_dir is not None else config.DATA_DIR, 'Application data directory')
    store = JobStore(data_dir)
    pipeline = Pipeline(store)
    model_manager = None
    model_lock = threading.Lock()
    seg_manager = None
    seg_lock = threading.Lock()
    update_manager = None
    update_lock = threading.Lock()

    def models():
        nonlocal model_manager
        with model_lock:
            if model_manager is None:
                model_manager = create_provisioner()
            return model_manager

    def segmentation_models():
        nonlocal seg_manager
        with seg_lock:
            if seg_manager is None:
                seg_manager = create_segmentation_manager()
            return seg_manager

    def updater():
        nonlocal update_manager
        with update_lock:
            if update_manager is None:
                update_manager = create_updater()
            return update_manager

    @asynccontextmanager
    async def lifespan(app):
        pipeline.start()
        yield
        if update_manager is not None:
            update_manager.close()
        if seg_manager is not None:
            seg_manager.close()
        if model_manager is not None:
            model_manager.close()
        pipeline.close()

    app = FastAPI(title='ClipClip.ai', version=APP_VERSION, lifespan=lifespan)
    app.state.store, app.state.pipeline = store, pipeline
    register_model_routes(app, models)
    register_segmentation_routes(app, segmentation_models)
    register_update_routes(app, updater)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        if config.RUNTIME.packaged:
            expected = config.RUNTIME.run_token
            supplied = request.headers.get('x-podcut-run-token', '')
            if not expected or not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('ascii')):
                return PlainTextResponse('The desktop backend credential did not match.', status_code=403)
        origin = request.headers.get('origin')
        if config.RUNTIME.packaged and origin and origin != 'http://127.0.0.1:3300':
            return PlainTextResponse('Only the isolated desktop frontend may access this service.', status_code=403)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and origin and origin not in config.LOCAL_ORIGINS:
            return PlainTextResponse('Only the local PODCUT studio may change projects.', status_code=403)
        if request.method == 'POST' and '/source' in request.url.path:
            try:
                if int(request.headers.get('content-length', '0')) > config.MAX_UPLOAD_MB * 1024**2 + 65536:
                    return PlainTextResponse('Source exceeds the configured upload limit.', status_code=413)
            except ValueError:
                return PlainTextResponse('Invalid content length.', status_code=400)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Project or file was not found.'}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        from fastapi.responses import JSONResponse
        # Pydantic's raw `input` can contain NaN/Infinity; never reserialize it.
        errors = [{'loc': list(error['loc']), 'msg': error['msg'], 'type': error['type']} for error in exc.errors()]
        return JSONResponse({'detail': errors}, status_code=422)

    def idle(ident):
        job = store.get(ident)
        if job['status'] in ACTIVE:
            raise HTTPException(409, 'Project is processing. Wait or cancel the current task.')
        return job

    @app.get('/api/runtime-identity')
    def runtime_identity(request: Request):
        token = config.RUNTIME.run_token
        supplied = request.headers.get('x-podcut-run-token', '')
        if token and not hmac.compare_digest(supplied.encode('utf-8'), token.encode('ascii')):
            raise HTTPException(403, 'Installer-Dev launcher identity did not match.')
        return config.RUNTIME.identity()

    @app.get('/api/health')
    def health():
        ai = config.ai_configuration().public()
        model_status = models().status() if config.RUNTIME.packaged else None
        return {'status': 'ok', 'ffmpeg': bool(config.binary('ffmpeg')), 'ffprobe': bool(config.binary('ffprobe')),
                'instance': config.RUNTIME.identity(),
                'whisper': media.available_whisper() and (model_status is None or model_status['state'] == 'ready'),
                'whisper_setup': model_status,
                'openai': ai['provider'] == 'openai' and ai['configured'],
                'whisper_model': config.WHISPER_MODEL, 'max_upload_mb': config.MAX_UPLOAD_MB,
                'storage_limit_gb': config.STORAGE_LIMIT_GB, 'youtube_ingest': pipeline.ingestor.capabilities(),
                'reframe': reframe_capabilities(),
                'segmentation': segmentation_models().status(),
                'renderer': subtle_renderer.renderer_capabilities(),
                'app_version': APP_VERSION,
                'updates': updater().status(),
                'gemini': ai['provider'] == 'gemini' and ai['configured'], 'ai': ai}

    @app.get('/api/jobs')
    def jobs():
        return [store.public(j) for j in store.list()]

    @app.post('/api/jobs', status_code=201)
    def new_job(payload: CreateJob):
        try:
            store.ensure_capacity()
        except ValueError as exc:
            raise HTTPException(507, str(exc)) from None
        return store.public(store.create(payload.title.strip(), payload.youtube_url))

    @app.get('/api/jobs/{ident}')
    def job_status(ident: str):
        return store.public(store.get(ident))

    @app.post('/api/jobs/{ident}/source')
    async def source(ident: str, file: UploadFile = File(...)):
        with store.lock:
            job = idle(ident)
            if job.get('_source'):
                raise HTTPException(409, 'This project already has a source. Create a new project for a different file.')
            suffix = Path(file.filename or '').suffix.lower()
            if suffix not in (VIDEO_EXTENSIONS | AUDIO_EXTENSIONS):
                raise HTTPException(422, 'Unsupported source type. Attach MP4, MOV, MKV, WEBM, MP3, WAV, M4A, FLAC atau container media didukung.')
            store.update(ident, status='probing', stage='uploading', progress=None, error=None, source_error=None,
                         ingestion=None, source_cache_hit=False, auto_export=None, message='Receiving and validating source media.')
        directory = store.directory(ident)
        temp = directory / f'source.partial{suffix}'
        count, checked = 0, 0
        try:
            store.ensure_capacity()
            with temp.open('wb') as out:
                while chunk := await file.read(1024 * 1024):
                    count += len(chunk)
                    if count > config.MAX_UPLOAD_MB * 1024**2:
                        raise HTTPException(413, f'Source exceeds the {config.MAX_UPLOAD_MB} MB upload limit.')
                    out.write(chunk)
                    if count - checked > 64 * 1024**2:
                        store.ensure_capacity()
                        checked = count
            store.ensure_capacity()
            allow_audio = suffix in AUDIO_EXTENSIONS or suffix == '.ogg'
            info = await run_in_threadpool(lambda: media.probe(temp, allow_audio_only=allow_audio))
            source_name = f'source{suffix}'
            temp.replace(directory / source_name)
            warnings = []
            if not info.get('audio_only') and (info['video_codec'] not in {'h264', 'vp8', 'vp9', 'av1'} or info['audio_codec'] not in {None, 'aac', 'opus', 'vorbis', 'mp3'}):
                warnings.append('Source codec may not play in this browser. Analysis/export remain available; H.264/AAC MP4 is recommended for preview.')
            if not info['has_audio']:
                warnings.append('Source has no audio. Import a timestamped transcript for recommendations.')
            job.update(_source=source_name, _source_warnings=warnings, source_name=Path((file.filename or 'video').replace('\\', '/')).name[:200],
                       media=info, status='uploaded', stage='input', progress=None, error=None,
                       source_error=None, ingestion=None, source_cache_hit=False, auto_export=None,
                       message='Source validated. Choose settings and analyze the episode.', warnings=warnings)
            if job['title'] == 'Untitled episode':
                job['title'] = Path(job['source_name']).stem[:160]
            store.save(job)
            if not info.get('audio_only'):
                try:
                    await run_in_threadpool(media.make_poster, directory / source_name, directory / 'poster.jpg')
                except media.MediaError:
                    log.info('job=%s poster unavailable; video retained', ident)
            return store.public(job)
        except (media.MediaError, ValueError, HTTPException, OSError) as exc:
            message = ('Could not save the source. Check available disk space and folder permissions, then attach it again.'
                       if isinstance(exc, OSError) else exc.detail if isinstance(exc, HTTPException) else str(exc))
            store.update(ident, status='blocked', stage='input', error=message, message=message)
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(422, message) from None
        finally:
            temp.unlink(missing_ok=True)
            await file.close()

    @app.post('/api/jobs/{ident}/transcript')
    async def import_transcript(ident: str, file: UploadFile = File(...)):
        job = idle(ident)
        if not job['media']:
            raise HTTPException(409, 'Attach the source video first, so transcript timestamps can be verified.')
        try:
            raw = await file.read(10 * 1024**2 + 1)
            if len(raw) > 10 * 1024**2:
                raise ValueError('Transcript exceeds 10 MB.')
            text = raw.decode('utf-8-sig')
            payload = media.parse_srt(text) if Path(file.filename or '').suffix.lower() == '.srt' else json.loads(text)
            transcript = validate_transcript(payload, job['media']['duration'])
            with store.lock:
                job = idle(ident)
                write_json(store.directory(ident) / 'transcript.json', transcript)
                for name in ('caption_alignment.json', 'caption_corrections.json'):
                    old = store.directory(ident) / name
                    if old.exists():
                        archive = store.directory(ident) / 'caption_history'
                        archive.mkdir(exist_ok=True)
                        old.replace(archive / f'{uuid.uuid4().hex}-{name}')
                job.update(transcript_ready=True, _transcript_imported=True, status='uploaded', error=None,
                           stage='input', message='Timestamped transcript imported. Analyze to update recommendations.',
                           candidates=[], analysis_mode=None, analysis_diagnostics=None)
                store.save(job)
            return store.public(job)
        except (ValueError, UnicodeError, TypeError) as exc:
            raise HTTPException(422, f'Invalid transcript: {str(exc)[:300]}') from None
        finally:
            await file.close()

    @app.get('/api/jobs/{ident}/transcript')
    def transcript(ident: str):
        store.get(ident)
        path = store.directory(ident) / 'transcript.json'
        if not path.exists():
            raise HTTPException(404, 'Transcript is not available yet.')
        return json.loads(path.read_text(encoding='utf-8'))

    @app.post('/api/jobs/{ident}/caption-preview')
    def caption_preview(ident: str, payload: CaptionPreviewSettings):
        with store.lock:
            job = store.get(ident)
            if not job['media'] or payload.end > job['media']['duration'] + .01:
                raise HTTPException(422, 'Rentang subtitle harus berada di dalam video sumber.')
            source = caption_transcript(store.directory(ident))
            if not has_words(source):
                raise HTTPException(409, 'Waktu per kata belum tersedia. Gunakan Siapkan waktu per kata; timestamp tidak akan dibuat-buat.')
            width, height = (1080, 1920) if payload.aspect == 'vertical' else (math.ceil(job['media']['width']/2)*2, math.ceil(job['media']['height']/2)*2)
            try:
                return build_caption_document(source['segments'], payload.start, payload.end, width, height,
                                              payload.style, corrections(store.directory(ident)))
            except ValueError as exc:
                raise HTTPException(422, f'Subtitle belum dapat disiapkan: {str(exc)[:350]}') from None

    @app.patch('/api/jobs/{ident}/caption-words')
    def caption_words(ident: str, payload: CaptionCorrections):
        with store.lock:
            idle(ident)
            source = caption_transcript(store.directory(ident))
            if not has_words(source):
                raise HTTPException(409, 'Siapkan waktu per kata sebelum mengoreksi subtitle.')
            valid_ids = set(range(sum(len(s.get('words') or []) for s in source['segments'])))
            if any(change.word_id not in valid_ids for change in payload.changes):
                raise HTTPException(422, 'ID kata tidak ada dalam transkrip sumber.')
            values = corrections(store.directory(ident))
            values.update({str(change.word_id): change.text for change in payload.changes})
            write_json(store.directory(ident) / 'caption_corrections.json', values)
            return {'saved': True}

    @app.get('/api/subtle/styles')
    def get_subtle_styles():
        return {
            'styles': subtle_styles.list_styles(),
            'segmentation': segmentation_models().status(),
            'renderer': subtle_renderer.renderer_capabilities(),
        }

    @app.post('/api/jobs/{ident}/subtle-matte-preview')
    def subtle_matte_preview(ident: str, payload: SubtlePreviewSettings):
        job = store.get(ident)
        if not job['media'] or payload.end > job['media']['duration'] + .01:
            raise HTTPException(422, 'Rentang pratinjau matte berada di luar sumber.')
        if job['media'].get('audio_only') or not job.get('_source'):
            raise HTTPException(409, 'Pratinjau matte membutuhkan sumber video.')
        directory = store.directory(ident)
        source_file = checked_path(directory / job['_source'], directory, 'Source video')
        crop_box = None
        if payload.aspect == 'vertical':
            sw, sh = int(job['media']['width']), int(job['media']['height'])
            cw = max(2, min(sw, int(sh * 9 / 16)) // 2 * 2)
            ch = max(2, min(sh, int(sw * 16 / 9)) // 2 * 2)
            cx = int((sw - cw) * payload.crop_x) // 2 * 2
            cy = (sh - ch) // 4 * 2
            crop_box = (cw, ch, cx, cy)
            out_w, out_h = 1080, 1920
        else:
            out_w = int(job['media']['width'])
            out_h = int(job['media']['height'])
        quality = payload.options.matte_quality
        key_payload = json.dumps({
            'src': job['_source'], 'start': round(payload.start, 3), 'end': round(payload.end, 3),
            'aspect': payload.aspect, 'crop_x': round(payload.crop_x, 4), 'quality': quality,
        }, sort_keys=True)
        cache_key = hashlib.sha256(key_payload.encode()).hexdigest()[:24]
        matte_dir = checked_path(directory / 'subtle_cache' / 'mattes', directory, 'Matte preview cache')
        matte_dir.mkdir(parents=True, exist_ok=True)
        out_path = checked_path(matte_dir / f'{cache_key}.mp4', directory, 'Packed matte preview')
        if not out_path.is_file():
            from . import subtle_vision
            subtle_vision.generate_packed_preview(
                source_file, out_path,
                start=payload.start, end=payload.end,
                width=out_w, height=out_h,
                crop_box=crop_box, quality=quality,
            )
        return {'key': cache_key, 'url': f'/api/jobs/{ident}/subtle-matte-preview/{cache_key}'}

    @app.get('/api/jobs/{ident}/subtle-matte-preview/{key}')
    def get_subtle_matte_preview(ident: str, key: str):
        import re as _re
        if not _re.fullmatch(r'[a-f0-9]{24}', key):
            raise HTTPException(404, 'Preview matte tidak ditemukan.')
        directory = store.directory(ident)
        path = checked_path(directory / 'subtle_cache' / 'mattes' / f'{key}.mp4', directory, 'Packed matte preview')
        if not path.is_file():
            raise HTTPException(404, 'Preview matte belum dibuat.')
        return FileResponse(path, media_type='video/mp4')

    @app.get('/api/caption-fonts/{filename}')
    def get_caption_font(filename: str):
        import re as _re
        if not _re.fullmatch(r'[A-Za-z0-9_-]+\.ttf', filename):
            raise HTTPException(404, 'Font tidak ditemukan.')
        fonts_dir = subtle_styles.subtle_fonts_dir()
        path = checked_path(fonts_dir / filename, fonts_dir, 'Subtle font asset')
        if not path.is_file():
            raise HTTPException(404, 'Berkas font tidak ditemukan.')
        return FileResponse(path, media_type='font/ttf', filename=filename)

    @app.post('/api/jobs/{ident}/subtle-preview')
    def subtle_preview(ident: str, payload: SubtlePreviewSettings):
        job = store.get(ident)
        if not job['media'] or payload.end > job['media']['duration'] + .01:
            raise HTTPException(422, 'Rentang subtitle harus berada di dalam sumber media.')
        directory = store.directory(ident)
        source_transcript = caption_transcript(directory)
        if not has_words(source_transcript):
            raise HTTPException(409, 'Waktu per kata belum tersedia. Gunakan Siapkan waktu per kata terlebih dahulu.')
        source_file = directory / job['_source'] if job.get('_source') else None
        crop_box = None
        if not job['media'].get('audio_only') and payload.aspect == 'vertical':
            sw, sh = int(job['media']['width']), int(job['media']['height'])
            cw = max(2, min(sw, int(sh * 9 / 16)) // 2 * 2)
            ch = max(2, min(sh, int(sw * 16 / 9)) // 2 * 2)
            cx = int((sw - cw) * payload.crop_x) // 2 * 2
            cy = (sh - ch) // 4 * 2
            crop_box = (cw, ch, cx, cy)
        try:
            res = subtle_pipeline.prepare_subtle_captions(
                video_path=source_file,
                source_info=job['media'],
                transcript=source_transcript,
                start=payload.start,
                end=payload.end,
                aspect=payload.aspect,
                options=payload.options.model_dump(),
                corrections=corrections(directory),
                crop_box=crop_box,
                cache_dir=directory / 'subtle_cache',
            )
            return res
        except ValueError as exc:
            raise HTTPException(422, f'Subtitle Subtle belum dapat disiapkan: {str(exc)[:350]}') from None

    @app.post('/api/jobs/{ident}/captions/align')
    def align_caption_words(ident: str, payload: AlignCaptionsRequest | None = Body(default=None)):
        job = idle(ident)
        if not job['media']:
            raise HTTPException(409, 'Unggah video sumber terlebih dahulu.')
        opts = payload.model_dump() if payload else {}
        if has_words(caption_transcript(store.directory(ident))) and not opts.get('vocab') and not opts.get('hinglish'):
            return store.public(store.update(ident, message='Waktu per kata sudah tersedia; transkripsi tidak diulang.'))
        return store.public(pipeline.enqueue(ident, {'type': 'align_captions', 'settings': opts}))

    @app.post('/api/jobs/{ident}/analyze')
    def analyze(ident: str, payload: AnalysisSettings):
        job = idle(ident)
        if job['media'] and payload.manual_end and payload.manual_end > job['media']['duration'] + .01:
            raise HTTPException(422, 'Manual range extends past the source video.')
        return store.public(pipeline.enqueue(ident, {'type': 'analysis', 'settings': payload.model_dump()}))

    @app.post('/api/jobs/{ident}/ingest')
    def ingest_source(ident: str, payload: IngestSettings):
        with store.lock:
            job = idle(ident)
            if not job.get('youtube_url'):
                raise HTTPException(422, 'Simpan tautan YouTube pada proyek, atau unggah video sumber.')
            return store.public(pipeline.enqueue(ident, {'type': 'ingest', 'settings': payload.model_dump()}))

    @app.post('/api/jobs/{ident}/retry')
    def retry(ident: str):
        job = idle(ident)
        if not job.get('_last_task'):
            raise HTTPException(409, 'Attach a source and choose Analyze to begin.')
        return store.public(pipeline.enqueue(ident, job['_last_task']))

    @app.post('/api/jobs/{ident}/cancel')
    def cancel(ident: str):
        return store.public(pipeline.cancel(ident))

    @app.delete('/api/jobs/{ident}')
    def delete(ident: str):
        try:
            store.delete(ident)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {'deleted': True}

    @app.get('/api/jobs/{ident}/media')
    def source_media(ident: str):
        job = store.get(ident)
        if not job.get('_source'):
            raise HTTPException(404, 'Attach a source video first.')
        return FileResponse(store.directory(ident) / job['_source'])

    @app.get('/api/jobs/{ident}/poster')
    def poster(ident: str):
        store.get(ident)
        path = store.directory(ident) / 'poster.jpg'
        if not path.is_file():
            raise HTTPException(404, 'Poster unavailable.')
        return FileResponse(path, media_type='image/jpeg')

    @app.get('/api/jobs/{ident}/captions.vtt')
    def vtt(ident: str):
        return PlainTextResponse(media.subtitles(transcript(ident)['segments'], vtt=True), media_type='text/vtt')

    def reframe_source(ident):
        job = store.get(ident)
        if not job.get('_source') or not job.get('media'):
            raise HTTPException(409,'Siapkan video sumber sebelum mengatur framing.')
        directory = store.directory(ident)
        source = (directory/job['_source']).resolve()
        if source.parent != directory.resolve() or not source.is_file():
            raise HTTPException(409,'Video sumber proyek tidak tersedia.')
        return job,directory,source

    def reframe_interval(ident,start,end):
        job,directory,source = reframe_source(ident)
        try:
            interval = ReframeInterval(start=start,end=end)
            if interval.end > job['media']['duration']+.00001:
                raise ValueError('Rentang framing melewati video sumber.')
        except ValueError:
            raise HTTPException(422,'Pilih rentang framing di dalam sumber, maksimal 10 menit.') from None
        return job,directory,source,interval

    @app.get('/api/jobs/{ident}/reframe')
    def get_reframe(ident:str,start:float,end:float):
        job,directory,source,interval = reframe_interval(ident,start,end)
        signature = reframe_store.source_signature(source,job['media'])
        try:
            return reframe_store.load_plan(directory,reframe_store.plan_id(signature,interval.start,interval.end),source,job['media'])
        except FileNotFoundError:
            raise HTTPException(404,'Belum ada metadata framing untuk rentang ini.') from None
        except ValueError as error:
            raise HTTPException(422,str(error)) from None

    @app.post('/api/jobs/{ident}/reframe/manual')
    def create_manual_reframe(ident:str,payload:ReframeInterval):
        with store.lock:
            idle(ident)
            job,directory,source,interval = reframe_interval(ident,payload.start,payload.end)
            plan = reframe_store.manual_plan(source,job['media'],interval.start,interval.end)
            try:
                return reframe_store.load_plan(directory,plan['id'],source,job['media'])
            except FileNotFoundError:
                return reframe_store.save_plan(directory,plan)
            except ValueError as error:
                raise HTTPException(422,str(error)) from None

    @app.post('/api/jobs/{ident}/reframe/analyze')
    def analyze_reframe(ident:str,payload:ReframeRequest):
        with store.lock:
            idle(ident)
            reframe_interval(ident,payload.start,payload.end)
            if payload.mode == 'active_speaker':
                from .active_speaker import capabilities as speaker_capabilities
                speaker = speaker_capabilities()
                if not speaker['available']:
                    raise HTTPException(409,speaker['reason'])
            status = reframe_capabilities()
            if not status.get('auto_face',status.get('available')):
                raise HTTPException(409,status.get('reason') or 'Detektor lokal belum siap. Pasang kebutuhan reframing lalu mulai ulang aplikasi.')
            return store.public(pipeline.enqueue(ident,{'type':'reframe','settings':payload.model_dump()}))

    @app.put('/api/jobs/{ident}/reframe')
    def update_reframe(ident:str,payload:ReframePlan):
        with store.lock:
            idle(ident)
            job,directory,source = reframe_source(ident)
            try:
                original = reframe_store.load_plan(directory,payload.id,source,job['media'])
            except FileNotFoundError:
                raise HTTPException(404,'Buat atau analisis framing sebelum menyimpan keyframe.') from None
            except ValueError as error:
                raise HTTPException(422,str(error)) from None
            incoming = payload.model_dump()
            immutable = ('id','version','source_signature','source_width','source_height','source_fps','source_duration','start','end')
            if any(original[name] != incoming[name] for name in immutable):
                raise HTTPException(422,'Identitas sumber dan rentang framing tidak boleh diubah.')
            if [(s['id'],s['start'],s['end']) for s in original['shots']] != [(s['id'],s['start'],s['end']) for s in incoming['shots']]:
                raise HTTPException(422,'Batas shot hasil analisis tidak boleh diubah oleh keyframe.')
            for old,new in zip(original['shots'],incoming['shots']):
                keys = []
                for key in new['keyframes']:
                    # Changed/generated user coordinates are explicit manual overrides.
                    unchanged = any(all(previous[k] == key[k] for k in ('time','x','y','zoom'))
                                    for previous in old.get('auto_keyframes',[])+old['keyframes'] if not previous['manual'])
                    keys.append({**key,'manual':key['manual'] or not unchanged})
                if new['focus'] == 'active' and not old.get('speaker_intervals'):
                    raise HTTPException(422,'Analisis active speaker sebelum memilih Follow Active Speaker.')
                old.update(focus=new['focus'],keyframes=keys,speaker_overrides=new['speaker_overrides'])
                if any(k['manual'] for k in keys) or new['focus'] not in {'auto','active'}:
                    old.update(decision='manual',review_required=False,reason='Framing dipilih atau dikoreksi pengguna.')
                elif new['focus'] == 'active':
                    def uncovered(interval):
                        cursor=interval['start']
                        for override in old['speaker_overrides']:
                            if override['end']<=cursor:continue
                            if override['start']>cursor+.000001:return True
                            cursor=max(cursor,override['end'])
                            if cursor>=interval['end']-.000001:return False
                        return cursor<interval['end']-.000001
                    old.update(decision='active_speaker',review_required=any(i['review_required'] and uncovered(i) for i in old['speaker_intervals']),
                               reason='Mengikuti bukti audio-visual lokal; interval manual tetap diprioritaskan.')
            original['smoothing'] = incoming['smoothing']
            original['mode'] = incoming['mode']
            return reframe_store.save_plan(directory,original)

    @app.get('/api/jobs/{ident}/reframe/pose')
    def reframe_pose(ident:str,plan_id:str,time:float):
        job,directory,source = reframe_source(ident)
        try:
            plan = reframe_store.load_plan(directory,plan_id,source,job['media'])
            if not math.isfinite(time) or not plan['start'] <= time <= plan['end']:
                raise ValueError('Waktu berada di luar rentang framing.')
            from .reframe_render import frame_geometry,pose_at
            return {'pose':pose_at(plan,time),'crop':frame_geometry(plan,time)}
        except FileNotFoundError:
            raise HTTPException(404,'Metadata framing tidak tersedia.') from None
        except ValueError as error:
            raise HTTPException(422,str(error)) from None

    @app.post('/api/jobs/{ident}/exports', status_code=202)
    def export(ident: str, payload: ExportSettings):
        with store.lock:
            job = idle(ident)
            if not job['media']:
                raise HTTPException(409, 'Attach a source video first.')
            if payload.end > job['media']['duration'] + .01:
                raise HTTPException(422, 'Export ends after the source video.')
            preset = payload.duration_preset or (job.get('settings') or {}).get('duration_preset', 'auto')
            if preset in {'20', '30', '45', '60'} and payload.end - payload.start > float(preset) + .000001:
                raise HTTPException(422, f'Potongan melebihi batas maksimal {preset} detik. Sesuaikan in/out atau pilih mode lain.')
            if payload.caption_style == 'poppins_highlight' and not has_words(caption_transcript(store.directory(ident))):
                raise HTTPException(409, 'Subtitle Poppins memerlukan waktu per kata. Klik Siapkan waktu per kata, lalu ekspor kembali.')
            if payload.caption_style == 'subtle':
                if not has_words(caption_transcript(store.directory(ident))):
                    raise HTTPException(409, 'Subtitle animasi Subtle memerlukan waktu per kata. Klik Siapkan waktu per kata, lalu ekspor kembali.')
                rcaps = subtle_renderer.renderer_capabilities()
                if not rcaps['available']:
                    raise HTTPException(409, rcaps.get('reason') or 'Renderer Subtle belum siap.')
            if payload.caption_style in {'clean', 'bold'} and not job['transcript_ready']:
                raise HTTPException(409, 'Transcribe or import a transcript before burning captions.')
            if pipeline.tasks.full():
                raise HTTPException(503, 'Local queue is full. Wait for another job to finish, then retry.')
            settings = payload.model_dump()
            if payload.reframe_id:
                try:
                    settings['_reframe_plan'] = checked_reframe(store,ident,payload)
                except (ValueError,FileNotFoundError) as error:
                    raise HTTPException(422,str(error)) from None
            if payload.caption_style == 'subtle':
                subtle_opts = settings.get('subtle') or {}
                out_w, out_h = subtle_pipeline.compute_output_dimensions(
                    job['media'], aspect=payload.aspect, canvas=subtle_opts.get('canvas', '9:16'),
                )
            else:
                out_w = 1080 if payload.aspect == 'vertical' else job['media']['width']
                out_h = 1920 if payload.aspect == 'vertical' else job['media']['height']
            export_id = uuid.uuid4().hex
            job['exports'].append({'id': export_id, **payload.model_dump(), 'width': out_w,
                                   'height': out_h, 'status': 'queued', 'error': None,
                                   'created_at': now(), 'filename': None})
            store.save(job)
            settings['duration_preset'] = preset
            return store.public(pipeline.enqueue(ident, {'type': 'export', 'settings': settings, 'export_id': export_id}))

    def export_file(ident, export_id, filename):
        job = store.get(ident)
        export = next((e for e in job['exports'] if e['id'] == export_id), None)
        if not export or export['status'] != 'ready':
            raise HTTPException(409, 'This export has not passed verification yet.')
        path = store.directory(ident) / 'exports' / export['id'] / filename
        if not path.is_file():
            raise HTTPException(404, 'Export file is missing. Retry the export.')
        return path

    @app.get('/api/jobs/{ident}/exports/{export_id}/file')
    def download(ident: str, export_id: str):
        return FileResponse(export_file(ident, export_id, 'clip.mp4'), media_type='video/mp4', filename=f'clipclip-{ident[:8]}-{export_id[:8]}.mp4')

    @app.get('/api/jobs/{ident}/exports/{export_id}/captions.srt')
    def srt(ident: str, export_id: str):
        path = export_file(ident, export_id, 'captions.srt')
        if not path.read_text(encoding='utf-8').strip():
            raise HTTPException(409, 'This export interval has no transcript captions. Import or generate a transcript first.')
        return FileResponse(path, media_type='application/x-subrip', filename='captions.srt')

    @app.get('/api/jobs/{ident}/exports/{export_id}/captions.ass')
    def ass(ident: str, export_id: str):
        return FileResponse(export_file(ident, export_id, 'captions.ass'), media_type='text/x-ssa', filename='captions.ass')

    @app.get('/api/jobs/{ident}/exports/{export_id}/captions.vtt')
    def export_vtt(ident: str, export_id: str):
        return FileResponse(export_file(ident, export_id, 'captions.vtt'), media_type='text/vtt', filename='captions.vtt')

    @app.get('/api/jobs/{ident}/exports/{export_id}/captions.json')
    def export_captions_json(ident: str, export_id: str):
        return FileResponse(export_file(ident, export_id, 'captions.json'), media_type='application/json', filename='captions.json')

    @app.get('/api/jobs/{ident}/exports/{export_id}/preview.jpg')
    def export_preview_jpg(ident: str, export_id: str):
        return FileResponse(export_file(ident, export_id, 'preview.jpg'), media_type='image/jpeg', filename='preview.jpg')

    app.add_middleware(UploadLimitMiddleware)
    return app


app = create_app()
