"""Real Installer-Dev HTTP/export check; authored timing fixture, not an ASR test.

Run only after the isolated launcher has started frontend 3300/backend 8300.
All HTTP goes through the frontend proxy. No speech/face weights are downloaded,
no live provider is called, and the evidence project is retained for inspection.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone


def _resolve_workspace() -> tuple[Path, Path]:
    project = Path(__file__).resolve().parents[1]
    marker = project / 'podcut-dev-workspace.json'
    if not marker.is_file() or marker.stat().st_size > 16384:
        raise RuntimeError('Multi-PC workspace marker is missing or invalid.')
    data = json.loads(marker.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema') != 1 or data.get('kind') != 'podcut-multipc-dev':
        raise RuntimeError('Verification requires the Multi-PC source checkout.')
    return project, project / '.dev' / 'profile'


PROJECT, PROFILE = _resolve_workspace()
BASE_URL = 'http://127.0.0.1:3300'


def bootstrap_path(value: str | Path, root: Path) -> Path:
    """Read-only preflight before importing the DEV backend configuration."""
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(root):
        raise RuntimeError('Verification requires the exact approved DEV roots.')
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise RuntimeError('Verification cannot traverse a reparse point.')
    if path.resolve() != path:
        raise RuntimeError('Verification path resolution did not match its approved root.')
    return path


class VerificationFailure(RuntimeError):
    pass


def authored_transcript() -> dict:
    # These authored fixture clocks deliberately do not describe the sine audio.
    # They exercise import, source timing provenance, captions and editorial flow.
    thoughts = [
        (2.0, 19.5, 'Ternyata kita tidak perlu menunggu motivasi karena langkah kecil membuat pekerjaan lebih mudah, jadi mulailah dengan satu kalimat dan periksa hasilnya sebelum menambah target.'),
        (23.0, 40.0, 'Pelajarannya bukan bekerja lebih lama, karena kita justru lebih mudah melihat kesalahan setelah istirahat singkat, jadi berhenti sebentar lalu periksa ulang pekerjaan sebelum mengirim hasilnya.'),
    ]
    segments = []
    for start, end, text in thoughts:
        tokens = text.split()
        words = [{'start': start + (end - start) * index / len(tokens),
                  'end': start + (end - start) * (index + 1) / len(tokens), 'word': token}
                 for index, token in enumerate(tokens)]
        segments.append({'start': start, 'end': end, 'text': text, 'words': words})
    return {'language': 'id', 'segments': segments}


def main() -> int:
    script_path = bootstrap_path(Path(__file__).absolute(), PROJECT)
    if script_path.parents[1] != PROJECT:
        raise RuntimeError('Verification script must live in the approved DEV scripts directory.')
    bootstrap_path(PROFILE, PROFILE)
    bootstrap_path(Path(sys.executable).absolute(), PROJECT)
    backend = bootstrap_path(PROJECT / 'backend', PROJECT)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(backend))
    from app import config
    import httpx

    if config.PROJECT != PROJECT or config.RUNTIME.root != PROFILE:
        raise RuntimeError('Backend profile identity did not match Installer-Dev.')
    run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:10]
    output = config.RUNTIME.validate_writable(PROFILE / 'runtime' / 'phase2a' / run, 'Verification report directory')
    fixture_root = config.RUNTIME.validate_writable(PROFILE / 'temp' / 'phase2a-fixtures' / run, 'Verification fixture directory')
    output.mkdir(parents=True, exist_ok=False)
    fixture_root.mkdir(parents=True, exist_ok=False)
    report_path = output / 'report.json'
    report = {'schema': 'podcut-phase2a-verification-v1', 'verification_id': run,
              'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'base_url': BASE_URL, 'project_root': str(PROJECT), 'profile_root': str(PROFILE),
              'fixture_notice': 'Authored transcript and scheduled word clocks over synthetic testsrc2/sine audio. This does not measure speech recognition accuracy or real podcast editorial quality.',
              'output_directory': str(output), 'fixture_directory': str(fixture_root),
              'checks': [], 'requests': [], 'tools': [], 'artifacts': {}}
    stage = 'preflight'
    expected_run_id = ''

    def save_report():
        target = config.RUNTIME.validate_writable(report_path, 'Verification report')
        temporary = config.RUNTIME.validate_writable(output / 'report.json.tmp', 'Verification report staging')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(target)

    def check(name: str, condition: bool, **details):
        report['checks'].append({'name': name, 'passed': bool(condition), **details})
        save_report()
        if not condition:
            raise VerificationFailure(name)
        print('PASS ' + name, flush=True)

    def disk(value: str | Path) -> Path:
        return config.RUNTIME.validate_writable(value, 'Verification artifact')

    def write_bytes(value: str | Path, content: bytes) -> Path:
        path = disk(value)
        path.write_bytes(content)
        return path

    def digest(value: str | Path) -> str:
        return hashlib.sha256(disk(value).read_bytes()).hexdigest()

    def file_state(value: str | Path) -> dict:
        path = disk(value)
        info = path.stat()
        return {'sha256': digest(path), 'size': info.st_size, 'mtime_ns': info.st_mtime_ns}

    def snapshot_model_caches() -> dict:
        result = {}
        roots = [config.MODELS_DIR, config.RUNTIME.paths['HF_HOME'], config.RUNTIME.paths['TORCH_HOME']]
        for root in roots:
            config.RUNTIME.validate_tree(root)
            for path in sorted(root.rglob('*')):
                disk(path)
                if path.is_file():
                    info = path.stat()
                    result[str(path.relative_to(PROFILE))] = {'size': info.st_size, 'mtime_ns': info.st_mtime_ns}
        return result

    def execute(tool_name: str, arguments: list[str | Path], *, timeout=90) -> bytes:
        executable = config.binary(tool_name)
        if not executable:
            raise VerificationFailure('Required isolated media tool is unavailable: ' + tool_name)
        command = [executable, *map(str, arguments)]
        began = time.monotonic()
        completed = subprocess.run(command, cwd=fixture_root, env=config.child_environment(),
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        report['tools'].append({'tool': tool_name, 'arguments': list(map(str, arguments)),
                                'returncode': completed.returncode, 'seconds': round(time.monotonic() - began, 3)})
        save_report()
        if completed.returncode:
            # Fixed fixture inputs only; never print inherited environment or credentials.
            write_bytes(output / f'{tool_name}-failure.txt', completed.stderr[-12000:])
            raise VerificationFailure('Isolated media command failed: ' + tool_name)
        return completed.stdout

    def probe(value: Path) -> dict:
        path = disk(value)
        info = json.loads(execute('ffprobe', ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]))
        video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
        audio = next((stream for stream in info['streams'] if stream['codec_type'] == 'audio'), {})
        rate = video.get('avg_frame_rate', '0/1').split('/')
        return {'width': int(video['width']), 'height': int(video['height']),
                'video_codec': video['codec_name'], 'audio_codec': audio.get('codec_name'),
                'duration': float(info['format']['duration']), 'fps': float(rate[0]) / max(float(rate[1]), 1),
                'size': int(info['format']['size'])}

    def request(client, method: str, path: str, expected=200, **kwargs):
        if not path.startswith('/') or path.startswith('//'):
            raise VerificationFailure('A verification request attempted to leave the local frontend.')
        began = time.monotonic()
        response = client.request(method, path, **kwargs)
        report['requests'].append({'method': method, 'path': path, 'status': response.status_code,
                                   'seconds': round(time.monotonic() - began, 3)})
        if response.status_code != expected:
            raise VerificationFailure(f'Unexpected HTTP status {response.status_code} for {method} {path}')
        if path.startswith('/api/'):
            if response.headers.get('x-podcut-proxy-profile') != 'installer-dev':
                raise VerificationFailure('The frontend response did not verify the Installer-Dev proxy.')
            if expected_run_id and response.headers.get('x-podcut-proxy-run-id') != expected_run_id:
                raise VerificationFailure('The frontend response changed its verified backend run identity.')
        return response

    def wait_job(client, ident: str, *, export_id: str | None = None) -> tuple[dict, list[str]]:
        deadline = time.monotonic() + 180
        states = []
        while time.monotonic() < deadline:
            job = request(client, 'GET', f'/api/jobs/{ident}').json()
            states.append(job.get('status', 'unknown'))
            if job['status'] in {'failed', 'blocked', 'cancelled'}:
                report['last_job'] = job
                raise VerificationFailure('The local processing job entered ' + job['status'])
            if job['status'] == 'ready':
                if export_id is None or next((item['status'] for item in job['exports'] if item['id'] == export_id), '') == 'ready':
                    return job, list(dict.fromkeys(states))
            time.sleep(.1)
        raise VerificationFailure('The local processing job did not finish within 180 seconds.')

    def check_candidates(job: dict, transcript: dict):
        candidates = job.get('candidates') or []
        allowed = [(segment['start'], segment['end']) for segment in transcript['segments']]
        complete = all(any(abs(candidate['start'] - first) < .002 and abs(candidate['end'] - last) < .002
                           for first, last in allowed) for candidate in candidates)
        check('heuristic candidates retain complete authored thoughts within the 20 second cap',
              job.get('analysis_mode') == 'heuristic' and job.get('analysis_provider') == 'heuristic'
              and bool(candidates) and complete
              and all(0 <= candidate['start'] < candidate['end'] <= 45
                      and candidate['end'] - candidate['start'] <= 20.000001
                      and candidate['duration'] <= 20.000001 and candidate.get('review_mode') == 'heuristic'
                      for candidate in candidates), candidate_count=len(candidates))

    try:
        check('all writable runtime directories are inside the approved external profile',
              all(path.is_relative_to(PROFILE) for path in config.RUNTIME.paths.values())
              and config.DATA_DIR == PROFILE / 'data' and config.MODELS_DIR == PROFILE / 'models'
              and config.CACHE_DIR == PROFILE / 'cache' and Path(tempfile.gettempdir()).is_relative_to(PROFILE))
        check('local heuristic analysis and offline model loading are enforced',
              config.ai_configuration().provider == 'heuristic' and not config.ai_configuration().api_key
              and os.environ.get('HF_HUB_OFFLINE') == '1' and os.environ.get('TRANSFORMERS_OFFLINE') == '1')
        model_snapshot = snapshot_model_caches()
        report['model_cache_before'] = model_snapshot
        with tempfile.NamedTemporaryFile(prefix='phase2a-proof-', dir=config.TEMP_DIR) as temporary:
            check('actual temporary file stays inside the isolated profile', disk(temporary.name).is_relative_to(PROFILE))

        stage = 'frontend identity'
        with httpx.Client(base_url=BASE_URL, trust_env=False, follow_redirects=False,
                          headers={'Origin': BASE_URL}, timeout=httpx.Timeout(90, connect=5)) as client:
            home = request(client, 'GET', '/')
            check('real frontend is served on port 3300', 'text/html' in home.headers.get('content-type', '') and 'PODCUT' in home.text.upper())
            health_response = request(client, 'GET', '/api/health')
            health = health_response.json()
            identity = health.get('instance', {})
            expected_run_id = identity.get('run_id', '')
            try:
                uuid.UUID(expected_run_id)
                valid_run_id = bool(expected_run_id)
            except (ValueError, TypeError, AttributeError):
                valid_run_id = False
            check('frontend proxy connects to the exact isolated backend instance',
                  identity.get('profile_id') == 'installer-dev' and Path(identity.get('project_root', '')) == PROJECT
                  and Path(identity.get('profile_root', '')) == PROFILE and identity.get('port') == 8300
                  and identity.get('frontend_port') == 3300 and identity.get('backend_host') == '127.0.0.1'
                  and identity.get('protocol') == 'podcut-installer-dev-v1' and identity.get('build') == 'phase2a'
                  and valid_run_id and health_response.headers.get('x-podcut-proxy-run-id') == expected_run_id)
            check('backend exposes isolated media tools and heuristic-only provider readiness',
                  health.get('ffmpeg') is True and health.get('ffprobe') is True
                  and health.get('openai') is False and health.get('gemini') is False
                  and health.get('ai', {}).get('provider') == 'heuristic' and not health.get('ai', {}).get('configured'))
            report['health'] = health

            stage = 'synthetic fixture'
            source = disk(fixture_root / 'owned-synthetic-45s.mp4')
            execute('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                               '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=45',
                               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=45',
                               '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20', '-threads', '2',
                               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', source])
            source_info = probe(source)
            check('owned synthetic source contains 45 seconds of H264/AAC media',
                  (source_info['width'], source_info['height']) == (640, 360)
                  and source_info['video_codec'] == 'h264' and source_info['audio_codec'] == 'aac'
                  and abs(source_info['duration'] - 45) <= .04, media=source_info)
            transcript = authored_transcript()
            transcript_bytes = json.dumps(transcript, ensure_ascii=False).encode('utf-8')
            write_bytes(fixture_root / 'authored-fixture-transcript.json', transcript_bytes)

            stage = 'source and transcript import'
            job = request(client, 'POST', '/api/jobs', expected=201,
                          json={'title': 'Phase 2A synthetic fixture - ' + run}).json()
            ident = job['id']
            check('new evidence project has an isolated identifier', bool(re.fullmatch(r'[a-f0-9]{32}', ident)))
            report['job_id'] = ident
            directory = disk(config.DATA_DIR / ident)
            with source.open('rb') as stream:
                uploaded = request(client, 'POST', f'/api/jobs/{ident}/source', files={'file': (source.name, stream, 'video/mp4')}).json()
            check('frontend upload preserves source metadata', uploaded['media']['has_audio'] and uploaded['media']['width'] == 640 and uploaded['media']['height'] == 360)
            imported = request(client, 'POST', f'/api/jobs/{ident}/transcript',
                               files={'file': ('authored-fixture.json', transcript_bytes, 'application/json')}).json()
            transcript_path = disk(directory / 'transcript.json')
            original_transcript = file_state(transcript_path)
            record = json.loads(disk(directory / 'job.json').read_text(encoding='utf-8'))
            stored_source = disk(directory / record['_source'])
            check('source and imported word clocks are stored only in external profile data',
                  imported['transcript_ready'] is True and record.get('_transcript_imported') is True
                  and stored_source.parent == directory and digest(stored_source) == digest(source)
                  and json.loads(transcript_path.read_text(encoding='utf-8'))['word_timestamps'] is True)
            source_range = request(client, 'GET', f'/api/jobs/{ident}/media', expected=206, headers={'Range': 'bytes=0-127'})
            check('source range playback crosses the frontend proxy correctly',
                  len(source_range.content) == 128 and source_range.content == source.read_bytes()[:128]
                  and source_range.headers.get('content-range', '').startswith('bytes 0-127/'))

            stage = 'heuristic analysis'
            settings = {'duration_preset': '20', 'clip_count': 3, 'language': 'auto'}
            request(client, 'POST', f'/api/jobs/{ident}/analyze', json=settings)
            analyzed, analysis_states = wait_job(client, ident)
            check_candidates(analyzed, transcript)
            check('analysis reuses the authored import without entering transcription',
                  'transcribing' not in analysis_states and file_state(transcript_path) == original_transcript
                  and not (directory / 'audio.wav').exists() and not (directory / 'caption_work').exists())
            analysis_files = {path.name: file_state(path) for path in directory.glob('analysis-*.json')}
            check('heuristic analysis produced a reusable local cache', bool(analysis_files))
            report['analysis'] = {'settings': settings, 'states': analysis_states, 'candidates': analyzed['candidates'], 'cache': analysis_files}

            stage = 'vertical Poppins caption preview and export'
            preview_payload = {'start': 2.0, 'end': 5.0, 'aspect': 'vertical',
                               'style': {'font_weight': 600, 'active_color': '#D7F35C'}}
            preview = request(client, 'POST', f'/api/jobs/{ident}/caption-preview', json=preview_payload).json()
            check('caption preview retains real fixture word clocks in a vertical document',
                  preview['width'] == 1080 and preview['height'] == 1920 and preview['duration'] == 3
                  and bool(preview['words']) and bool(preview['groups'])
                  and all(0 <= word['start'] < word['end'] <= 3.000001 for word in preview['words'])
                  and preview['style']['active_color'] == '#D7F35C' and preview['render_info']['ass_font_family'] == 'Poppins SemiBold')
            write_bytes(output / 'caption-preview.json', json.dumps(preview, ensure_ascii=False, indent=2).encode('utf-8'))

            export_results = {}
            report['exports'] = export_results
            for label, payload, dimensions in [
                ('vertical-poppins', {'start': 2.0, 'end': 5.0, 'aspect': 'vertical', 'caption_style': 'poppins_highlight',
                                      'subtitle_style': {'font_weight': 600, 'active_color': '#D7F35C'}}, (1080, 1920)),
                ('original', {'start': 30.0, 'end': 33.0, 'aspect': 'original', 'caption_style': 'none'}, (640, 360)),
            ]:
                stage = label + ' export'
                queued = request(client, 'POST', f'/api/jobs/{ident}/exports', expected=202, json=payload).json()
                export_id = queued['exports'][-1]['id']
                check(label + ' export received a valid local identifier', bool(re.fullmatch(r'[a-f0-9]{32}', export_id)))
                ready, states = wait_job(client, ident, export_id=export_id)
                exported = next(item for item in ready['exports'] if item['id'] == export_id)
                export_directory = disk(directory / 'exports' / export_id)
                local_clip = disk(export_directory / 'clip.mp4')
                downloaded = request(client, 'GET', f'/api/jobs/{ident}/exports/{export_id}/file')
                target = write_bytes(output / (label + '.mp4'), downloaded.content)
                metadata = probe(target)
                check(label + ' actual MP4 has verified dimensions, duration and H264/AAC audio',
                      exported['status'] == 'ready' and (metadata['width'], metadata['height']) == dimensions
                      and metadata['video_codec'] == 'h264' and metadata['audio_codec'] == 'aac'
                      and abs(metadata['duration'] - 3) <= 1 / 30 + .000001
                      and digest(target) == digest(local_clip), media=metadata)
                ranged = request(client, 'GET', f'/api/jobs/{ident}/exports/{export_id}/file', expected=206, headers={'Range': 'bytes=0-127'})
                check(label + ' MP4 range playback is preserved by the frontend proxy',
                      len(ranged.content) == 128 and ranged.content == downloaded.content[:128]
                      and ranged.headers.get('content-range', '').startswith('bytes 0-127/'))
                export_results[label] = {'id': export_id, 'path': str(target), 'data_directory': str(export_directory),
                                         'sha256': digest(target), 'media': metadata, 'states': states}
                if label == 'vertical-poppins':
                    stored_document = json.loads(disk(export_directory / 'caption_document.json').read_text(encoding='utf-8'))
                    check('Poppins preview and saved export use the same caption document', stored_document == preview)
                    sidecars = {}
                    for extension in ('ass', 'srt', 'vtt'):
                        response = request(client, 'GET', f'/api/jobs/{ident}/exports/{export_id}/captions.{extension}')
                        sidecars[extension] = response.text
                        write_bytes(output / f'vertical-poppins.captions.{extension}', response.content)
                        check(extension.upper() + ' sidecar survives the frontend proxy unchanged',
                              bool(response.content) and response.content == disk(export_directory / f'captions.{extension}').read_bytes())
                    check('ASS contains Poppins SemiBold and the requested lime active word',
                          'Poppins SemiBold' in sidecars['ass'] and r'\c&H5CF3D7&' in sidecars['ass']
                          and 'PlayResX: 1080' in sidecars['ass'] and 'PlayResY: 1920' in sidecars['ass'])
                    check('SRT and VTT sidecars have valid cue headers', '-->' in sidecars['srt'] and sidecars['vtt'].startswith('WEBVTT'))
                    font_copies = []
                    for font in ('Poppins-SemiBold.ttf', 'Poppins-Bold.ttf'):
                        staged_font = disk(export_directory / 'fonts' / font)
                        source_font = config.RUNTIME.validate_project(config.BACKEND / 'assets' / 'fonts' / font, 'Poppins source font')
                        font_copies.append(staged_font.is_file() and not os.path.samefile(staged_font, source_font)
                                           and staged_font.read_bytes() == source_font.read_bytes())
                    check('export fonts are independent copies of the approved DEV fonts', all(font_copies))
            report['exports'] = export_results

            stage = 'analysis cache reuse'
            request(client, 'POST', f'/api/jobs/{ident}/analyze', json=settings)
            reused, reuse_states = wait_job(client, ident)
            after_cache = {path.name: file_state(path) for path in directory.glob('analysis-*.json')}
            check('identical analysis reuses cache and imported timings without ASR',
                  after_cache == analysis_files and file_state(transcript_path) == original_transcript
                  and reused['candidates'] == analyzed['candidates'] and 'transcribing' not in reuse_states
                  and not (directory / 'audio.wav').exists() and not (directory / 'caption_work').exists()
                  and not (directory / 'caption_alignment.json').exists()
                  and len(reused['exports']) == 2 and all(item['status'] == 'ready' for item in reused['exports']))
            report['reuse_states'] = reuse_states

        stage = 'frame evidence'
        vertical_path = disk(export_results['vertical-poppins']['path'])
        original_path = disk(export_results['original']['path'])
        frames = []
        for label, path, at, scale in [('source-at-3s', source, 3, '320:180'),
                                       ('vertical-at-1s', vertical_path, 1, '320:568'),
                                       ('original-at-1s', original_path, 1, '320:180')]:
            target = disk(output / (label + '.png'))
            execute('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', str(at), '-i', path,
                               '-frames:v', '1', '-vf', 'scale=' + scale, target])
            check(label + ' evidence frame was extracted', target.is_file() and target.stat().st_size > 0)
            frames.append(target)
        contact = disk(output / 'contact-sheet.png')
        execute('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                           '-i', frames[0], '-i', frames[1], '-i', frames[2],
                           '-filter_complex', '[0:v]pad=320:568:0:194:color=0x111111[left];[2:v]pad=320:568:0:194:color=0x111111[right];[left][1:v][right]hstack=inputs=3[out]',
                           '-map', '[out]', '-frames:v', '1', contact])
        check('source, vertical Poppins and original export contact sheet was saved', contact.is_file() and contact.stat().st_size > 0)
        report['artifacts'] = {'report': str(report_path), 'source_fixture': str(source),
                               'vertical_mp4': str(vertical_path), 'original_mp4': str(original_path),
                               'contact_sheet': str(contact), 'frames': list(map(str, frames)),
                               'contact_sheet_columns': ['Synthetic source at global 3s', 'Vertical Poppins export at relative 1s (global 3s)', 'Original export at relative 1s (global 31s)']}
        stage = 'final filesystem check'
        config.RUNTIME.validate_tree(directory)
        config.RUNTIME.validate_tree(output)
        check('source, exports and evidence remain inside the profile with no linked paths',
              stored_source.is_relative_to(PROFILE) and all(Path(value['data_directory']).is_relative_to(PROFILE) for value in export_results.values())
              and digest(stored_source) == digest(source))
        after_models = snapshot_model_caches()
        report['model_cache_after'] = after_models
        check('no model weights or Hugging Face/Torch cache files were downloaded or changed', after_models == model_snapshot)
        report['status'] = 'passed'
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
        save_report()
        print('PASS Phase 2A integration evidence: ' + str(report_path), flush=True)
        return 0
    except Exception as error:
        report['status'] = 'failed'
        report['failed_stage'] = stage
        report['failure'] = str(error) if isinstance(error, VerificationFailure) else type(error).__name__
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
        save_report()
        print('FAIL Phase 2A integration at ' + stage + '; report: ' + str(report_path), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
