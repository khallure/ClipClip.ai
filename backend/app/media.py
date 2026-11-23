"""Bounded, cancellable file-based media processing; no shell or URL inputs."""
import importlib.util
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from . import config
from .process_runtime import external_popen
from .models import validate_transcript

log = logging.getLogger(__name__)


class MediaError(RuntimeError):
    pass


class MediaCancelled(MediaError):
    pass


def available_whisper() -> bool:
    return importlib.util.find_spec('faster_whisper') is not None


def run(command: list[str], *, cwd: Path | None = None, cancelled: Callable[[], bool] | None = None,
        timeout: float = 14400, capture: bool = False) -> str:
    """Drain diagnostics to disk to avoid pipe deadlocks on long episodes."""
    with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as output:
        try:
            proc = external_popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                    cwd=cwd, env=config.child_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (FileNotFoundError, OSError):
            raise MediaError('Media tool could not start. Configure isolated FFmpeg and FFprobe in the Installer-Dev profile.') from None
        began = time.monotonic()
        try:
            while proc.poll() is None:
                if cancelled and cancelled():
                    raise MediaCancelled('Processing cancelled. Previous results are preserved.')
                if time.monotonic() - began > timeout:
                    raise MediaError('Media processing timed out. Try a shorter source or check the media file.')
                time.sleep(.08)
            if proc.returncode:
                errors.seek(0, 2)
                length = errors.tell()
                errors.seek(max(0, length - 5000))
                detail = errors.read().decode('utf-8', 'replace')
                log.warning('Media subprocess failed: %s', detail[-1800:])
                if 'No space left' in detail:
                    raise MediaError('Not enough disk space. Delete unused local projects and retry.')
                if 'subtitles' in detail or 'libass' in detail:
                    raise MediaError('This FFmpeg build cannot burn captions. Export without captions or install a build with libass.')
                raise MediaError('FFmpeg could not read or encode this media. Use an MP4 with H.264 video and AAC audio, or check backend logs.')
            if capture:
                output.seek(0)
                return output.read(2_000_000).decode('utf-8', 'replace')
            return ''
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def tool(name: str) -> str:
    path = config.binary(name)
    if not path:
        raise MediaError(f'{name.upper()} is unavailable. Install FFmpeg (including FFprobe), then retry this project.')
    return path


def input_args(path: Path) -> list[str]:
    # In particular, do not let uploaded playlists cause network or arbitrary local file access.
    return ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm,avi,mpegts,mpeg,flv,ogg,mp3,wav,flac,aac', '-i', str(path)]


def probe(path: Path, *, allow_audio_only: bool = False) -> dict:
    raw = run([tool('ffprobe'), '-v', 'error', *input_args(path), '-show_streams', '-show_format', '-of', 'json'],
              timeout=60, capture=True)
    try:
        info = json.loads(raw)
        video = next((s for s in info['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
        audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
        if video is None:
            if not allow_audio_only or audio is None:
                raise StopIteration()
            duration = float(info['format'].get('duration') or audio.get('duration') or 0)
            if not math.isfinite(duration) or not 0 < duration <= config.MAX_DURATION:
                raise MediaError(f'Audio duration must be between 0 and {config.MAX_DURATION/3600:g} hours.')
            return {
                'duration': round(duration, 3),
                'width': 1080,
                'height': 1920,
                'fps': 30.0,
                'video_codec': None,
                'audio_codec': audio['codec_name'],
                'has_audio': True,
                'audio_only': True,
            }
        duration = float(info['format'].get('duration') or video.get('duration') or 0)
        width, height = int(video['width']), int(video['height'])
        for data in video.get('side_data_list', []):
            if abs(int(data.get('rotation', 0))) % 180 == 90:
                width, height = height, width
        rate = video.get('avg_frame_rate', '0/1').split('/')
        fps = float(rate[0]) / (float(rate[1]) or 1)
    except (ValueError, KeyError, StopIteration, TypeError, IndexError):
        raise MediaError('No playable video track or valid duration was found. Attach a video file, not an audio-only file or playlist.') from None
    if not math.isfinite(duration) or not 0 < duration <= config.MAX_DURATION:
        raise MediaError(f'Video duration must be between 0 and {config.MAX_DURATION/3600:g} hours.')
    if not 2 <= width <= 16384 or not 2 <= height <= 16384:
        raise MediaError('Unsupported video dimensions.')
    return {'duration': round(duration, 3), 'width': width, 'height': height,
            'fps': round(fps, 3), 'video_codec': video['codec_name'],
            'audio_codec': audio['codec_name'] if audio else None, 'has_audio': audio is not None}


def make_audio_canvas(source: Path, target: Path, *, width: int = 1080, height: int = 1920,
                      fps: float = 30.0, start: float | None = None, end: float | None = None,
                      bg_hex: str = '#0E0E10', cancelled=None) -> Path:
    clean_hex = f'0x{bg_hex[1:]}' if isinstance(bg_hex, str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', bg_hex) else 'black'
    cmd = [tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
           '-f', 'lavfi', '-i', f'color=c={clean_hex}:s={int(width)}x{int(height)}:r={fps:g}']
    if start is not None and start > 0:
        cmd += ['-ss', f'{start:.6f}']
    if end is not None and end > (start or 0):
        cmd += ['-t', f'{end - (start or 0):.6f}']
    cmd += [
        *input_args(source),
        '-map', '0:v:0', '-map', '1:a:0', '-shortest',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target),
    ]
    run(cmd, cancelled=cancelled)
    return target


def make_poster(source: Path, target: Path):
    run([tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', '0',
         *input_args(source), '-frames:v', '1', '-vf', 'scale=640:-2', str(target)], timeout=90)


_model = None
_model_signature = None
_model_lock = threading.Lock()


def transcribe(source: Path, working: Path, duration: float, language: str = 'auto',
               progress=None, cancelled=None, *, vocab: str = '', hinglish: bool = False) -> dict:
    if not available_whisper():
        raise MediaError('Local Whisper transcription is unavailable in this runtime. Use an independently prepared local model/runtime, or import a timestamped JSON/SRT transcript, then retry.')
    audio = working / 'audio.wav'
    if not audio.is_file():
        if progress:
            progress(0, 'Extracting mono speech audio; source video remains unchanged.')
        try:
            run([tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *input_args(source),
                 '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)], cancelled=cancelled)
        except BaseException:
            audio.unlink(missing_ok=True)
            raise
    global _model, _model_signature
    try:
        from faster_whisper import WhisperModel
        model_source = config.whisper_model_source()
        signature = (model_source, config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE)
        with _model_lock:
            if _model is None or _model_signature != signature:
                if progress:
                    progress(0, f'Loading isolated Whisper {config.WHISPER_MODEL} from the Installer-Dev models directory.')
                _model = WhisperModel(model_source, device=config.WHISPER_DEVICE,
                                     compute_type=config.WHISPER_COMPUTE_TYPE, cpu_threads=4, num_workers=1,
                                     download_root=str(config.MODELS_DIR), local_files_only=True)
                _model_signature = signature
        if cancelled and cancelled():
            raise MediaCancelled('Transcription cancelled.')
        hints = []
        if hinglish and language == 'hi':
            from .subtle.hinglish import ROMAN_HINT
            hints.append(ROMAN_HINT)
        if vocab and vocab.strip():
            hints.append('Vocabulary: ' + vocab.strip().rstrip('.') + '.')
        transcribe_kwargs = {}
        if hints:
            transcribe_kwargs['initial_prompt'] = ' '.join(hints)[:400]
        iterator, info = _model.transcribe(str(audio), language=None if language == 'auto' else language,
                                           word_timestamps=True, vad_filter=True, beam_size=5,
                                           condition_on_previous_text=False, **transcribe_kwargs)
        segments = []
        for segment in iterator:
            if cancelled and cancelled():
                raise MediaCancelled('Transcription cancelled.')
            if segment.text.strip():
                words = [{'start': w.start, 'end': w.end, 'word': w.word} for w in segment.words or [] if w.end > w.start]
                segments.append({'start': segment.start, 'end': min(duration, segment.end), 'text': segment.text,
                                 'words': words})
            if progress:
                progress(min(99, segment.end / duration * 100), f'Transcribing speech at {segment.end/60:.1f} / {duration/60:.1f} minutes.')
        return validate_transcript({'segments': segments, 'language': info.language}, duration)
    except MediaCancelled:
        raise
    except Exception as exc:
        if isinstance(exc, ValueError) and 'Transcript' in str(exc):
            raise MediaError(str(exc)) from None
        # A failed CUDA installation is an actionable error, not a silent CPU downgrade.
        log.warning('Whisper failed (%s)', type(exc).__name__)
        raise MediaError('Whisper could not load or transcribe with the isolated local model. Check that the independent model is complete and disk space is available. For GPU errors set WHISPER_DEVICE=cpu and WHISPER_COMPUTE_TYPE=int8, or import a timestamped transcript.') from None


def timestamp(seconds: float, vtt=False) -> str:
    milliseconds = max(0, round(seconds * 1000))
    h, rem = divmod(milliseconds, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f'{h:02}:{m:02}:{s:02}{"." if vtt else ","}{ms:03}'


def subtitles(segments: list[dict], start=0.0, end=float('inf'), vtt=False) -> str:
    """Crop and rebase real transcript timestamps; never interpolate word timings."""
    cues = []
    for segment in segments:
        words = [w for w in segment.get('words', []) if float(w['end']) > start and float(w['start']) < end]
        if segment.get('words'):
            parts = [{'start': group[0]['start'], 'end': group[-1]['end'],
                      'text': ''.join(w['word'] if w['word'].startswith(' ') else ' ' + w['word'] for w in group).strip()}
                     for i in range(0, len(words), 8) if (group := words[i:i+8])]
        else:
            parts = [segment]
        for part in parts:
            a, b = max(start, part['start']), min(end, part['end'])
            if b <= a:
                continue
            text = re.sub(r'<[^>]*>', '', part['text']).replace('{', '').replace('}', '').replace('\\', '/').strip()
            text = re.sub(r'[\x00-\x08\x0b-\x1f]', '', text)
            cues.append(f'{len(cues)+1}\n{timestamp(a-start,vtt)} --> {timestamp(b-start,vtt)}\n{text}\n')
    return ('WEBVTT\n\n' if vtt else '') + '\n'.join(cues)


def parse_srt(text: str) -> dict:
    segments = []
    pattern = re.compile(r'(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)', re.S)
    for match in pattern.finditer(text.replace('\r\n', '\n')):
        groups = match.groups()
        def seconds(parts):
            h, m, s, ms = map(int, parts)
            if m > 59 or s > 59:
                raise ValueError('Invalid SRT timestamp.')
            return h * 3600 + m * 60 + s + ms / 1000
        segments.append({'start': seconds(groups[:4]), 'end': seconds(groups[4:8]),
                         'text': re.sub(r'<[^>]*>', '', groups[8]).strip()})
    return {'segments': segments, 'language': 'auto'}


def export_clip(source: Path, directory: Path, settings: dict, transcript: dict | None,
                source_info: dict, cancelled=None, *, corrections: dict | None = None,
                cache_dir: Path | None = None) -> dict:
    if settings.get('caption_style') == 'subtle':
        from .subtle.renderer import export_subtle_clip
        return export_subtle_clip(
            source,
            directory,
            settings,
            transcript,
            source_info,
            cancelled=cancelled,
            corrections=corrections,
            cache_dir=cache_dir,
        )
    directory.mkdir(parents=True, exist_ok=True)
    start, end = settings['start'], settings['end']
    target, partial = directory / 'clip.mp4', directory / 'clip.partial.mp4'
    audio_base: Path | None = None
    working_source = source
    working_start = start
    working_info = source_info
    if source_info.get('audio_only'):
        audio_base = directory / '_audio_canvas.mp4'
        out_w, out_h = (1080, 1920) if settings['aspect'] == 'vertical' else (1080, 1920)
        make_audio_canvas(source, audio_base, start=start, end=end, width=out_w, height=out_h, cancelled=cancelled)
        working_source = audio_base
        working_start = 0.0
        working_info = probe(audio_base)
    filters = []
    if settings['aspect'] == 'vertical' and settings.get('_reframe_plan') and not source_info.get('audio_only'):
        from .reframe_render import render_filter
        filters.extend(render_filter(settings['_reframe_plan'], start, end, directory))
    elif settings['aspect'] == 'vertical':
        # Manual crop position is previewed by the browser; no face tracking is claimed.
        crop_width = max(2, min(working_info['width'], int(working_info['height'] * 9 / 16)) // 2 * 2)
        crop_height = max(2, min(working_info['height'], int(working_info['width'] * 16 / 9)) // 2 * 2)
        x = int((working_info['width'] - crop_width) * settings['crop_x']) // 2 * 2
        y = (working_info['height'] - crop_height) // 4 * 2
        filters.extend([f'crop={crop_width}:{crop_height}:{x}:{y}', 'scale=1080:1920:flags=lanczos', 'setsar=1'])
    else:
        filters.extend(['pad=ceil(iw/2)*2:ceil(ih/2)*2', 'setsar=1'])
    segments = (transcript or {}).get('segments', [])
    caption_document = None
    if any(segment.get('words') for segment in segments) and (settings['caption_style'] == 'poppins_highlight' or settings.get('subtitle_style')):
        from .captions import build_caption_document, to_ass, to_srt, to_vtt, font_directory
        out_width, out_height = (1080, 1920) if settings['aspect'] == 'vertical' else (math.ceil(working_info['width']/2)*2, math.ceil(working_info['height']/2)*2)
        caption_document = build_caption_document(segments, start, end, out_width, out_height, settings.get('subtitle_style'), corrections)
        captions = to_srt(caption_document)
        (directory / 'captions.ass').write_text(to_ass(caption_document), encoding='utf-8')
        (directory / 'captions.vtt').write_text(to_vtt(caption_document), encoding='utf-8')
        (directory / 'caption_document.json').write_text(json.dumps(caption_document, ensure_ascii=False), encoding='utf-8')
        if settings['caption_style'] == 'poppins_highlight':
            fonts = directory / 'fonts'
            fonts.mkdir(exist_ok=True)
            for original in font_directory().iterdir():
                if original.suffix.lower() == '.ttf':
                    target_font = fonts / original.name
                    if not target_font.exists():
                        shutil.copy2(original, target_font)
    else:
        captions = subtitles(segments, start, end)
    (directory / 'captions.srt').write_text(captions, encoding='utf-8')
    if settings['caption_style'] == 'poppins_highlight':
        if not caption_document or not caption_document['words']:
            raise MediaError('Subtitle Poppins memerlukan waktu kata yang asli. Siapkan subtitle per kata terlebih dahulu.')
        filters.append('subtitles=filename=captions.ass:fontsdir=fonts')
    elif settings['caption_style'] != 'none':
        if not captions.strip():
            raise MediaError('No captions overlap this interval. Choose no captions or adjust the times.')
        vertical = settings['aspect'] == 'vertical'
        font_size = 20 if vertical else 22
        style = f'FontName=Arial,FontSize={font_size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,Outline=2,Shadow=0,MarginV=40,Alignment=2'
        if settings['caption_style'] == 'bold':
            style += ',Bold=1,PrimaryColour=&H007DFFCD,Outline=3'
        filters.append(f"subtitles=filename=captions.srt:force_style='{style}'")
    command = [tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', f'{working_start:.6f}',
               *input_args(working_source), '-t', f'{end-start:.6f}', '-map', '0:v:0', '-map', '0:a:0?',
               '-vf', ','.join(filters), '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-threads', '4', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-map_metadata', '-1', str(partial)]
    try:
        run(command, cwd=directory, cancelled=cancelled)
        info = probe(partial)
        preset = settings.get('duration_preset')
        if preset in {'20', '30', '45', '60'} and info['duration'] > float(preset) + 1 / max(1, info['fps']) + .000001:
            raise MediaError(f'Ekspor melewati batas {preset} detik dan tidak ditandai siap. Periksa batas kalimat atau pendekkan akhir klip.')
        expected = (1080, 1920) if settings['aspect'] == 'vertical' else (math.ceil(working_info['width']/2)*2, math.ceil(working_info['height']/2)*2)
        if ((info['width'], info['height']) != expected or info['video_codec'] != 'h264'
                or working_info['has_audio'] and not info['has_audio']
                or abs(info['duration'] - (end-start)) > .35):
            raise MediaError('Export verification failed. No download is marked ready; retry or check source timestamps.')
        if cancelled and cancelled():
            raise MediaCancelled('Export cancelled.')
        partial.replace(target)
        return info
    finally:
        partial.unlink(missing_ok=True)
        if audio_base is not None:
            audio_base.unlink(missing_ok=True)
