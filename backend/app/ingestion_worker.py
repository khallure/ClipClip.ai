"""Private subprocess entry point for the optional, approved source adapter.

Only JSONL events with fixed stages/error codes are emitted. yt-dlp is imported
inside execute(), so upload and clipping do not require the optional package.
The pinned embedding API is documented at github.com/yt-dlp/yt-dlp and its
networking/_urllib.py. No CLI config, plugins, remote components or credentials
are loaded. The network director contains a single HTTPS-only provider handler.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .ingestion import (IngestPolicy, _dependency_ready, _FILE, _ID, _linked,
                        sanitize_metadata, sanitize_progress, _safe_numeric)
from .models import youtube_reference

_HOSTS = ('youtube.com', 'googlevideo.com', 'ytimg.com')
_EXACT_HOSTS = {'youtubei.googleapis.com', 'jnn-pa.googleapis.com'}


class SourceRejected(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_provider_url(url: str) -> str:
    """Provider endpoints only; every redirect passes through this check too."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or '').lower()
        valid = (parsed.scheme == 'https' and parsed.username is None and parsed.password is None
                 and parsed.port in (None, 443) and '\\' not in url
                 and not any(ord(char) < 32 or ord(char) == 127 for char in url)
                 and (host in _EXACT_HOSTS or any(host == base or host.endswith('.' + base) for base in _HOSTS)))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SourceRejected('policy')
    return host


def public_addresses(host: str) -> list[tuple]:
    """Resolve once, reject all non-public answers, connect to these exact IPs."""
    validate_provider_url(f'https://{host}/')
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(answer[4][0]).is_global for answer in answers):
            raise SourceRejected('policy')
        return answers
    except (OSError, ValueError):
        raise SourceRejected('network') from None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self.port != 443 or self._tunnel_host:
            raise SourceRejected('policy')
        last_error = None
        for family, kind, protocol, _, address in public_addresses(self.host):
            sock = socket.socket(family, kind, protocol)
            try:
                sock.settimeout(self.timeout)
                sock.connect(address)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
                sock.close()
        raise last_error or OSError('Source connection failed')


def _provider_handler_class():
    # These imports remain confined to the optional child process.
    from yt_dlp.networking._urllib import HTTPHandler, RedirectHandler, UrllibRH
    from yt_dlp.networking.exceptions import UnsupportedRequest

    class ProviderHTTPHandler(HTTPHandler):
        def http_open(self, request):
            raise SourceRejected('policy')

        def https_open(self, request):
            validate_provider_url(request.full_url)
            return self.do_open(_PinnedHTTPSConnection, request, context=self._context)

        def https_request(self, request):
            validate_provider_url(request.full_url)
            # No authenticated cookies, authorization, host override or proxy headers.
            for headers in (request.headers, request.unredirected_hdrs):
                for name in list(headers):
                    if name.lower() in {'authorization', 'proxy-authorization', 'cookie', 'host',
                                        'x-forwarded-for', 'ytdl-socks-proxy'}:
                        headers.pop(name, None)
            return super().https_request(request)

    class ProviderRedirectHandler(RedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            validate_provider_url(newurl)
            return super().redirect_request(request, fp, code, message, headers, newurl)

    class ProviderRH(UrllibRH):
        _SUPPORTED_URL_SCHEMES = ('https',)
        _SUPPORTED_PROXY_SCHEMES = ()
        _SUPPORTED_FEATURES = ()

        def _check_proxies(self, proxies):
            # yt-dlp represents explicit "no proxy" as an empty/all/None entry.
            # Accept those sentinels while rejecting every actual proxy address.
            if any(value not in (None, '') for value in proxies.values()):
                raise UnsupportedRequest('Proxies are disabled for this source adapter')

        def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
            if any(value for value in proxies.values()) or legacy_ssl_support:
                raise SourceRejected('policy')
            opener = urllib.request.OpenerDirector()
            for handler in (
                ProviderHTTPHandler(context=self._make_sslcontext()),
                ProviderRedirectHandler(), urllib.request.UnknownHandler(),
                urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor(),
            ):
                opener.add_handler(handler)
            opener.addheaders = []
            return opener

        def _send(self, request):
            validate_provider_url(request.url)
            return super()._send(request)

    return ProviderRH


def validate_metadata(info: dict, manifest: dict) -> None:
    if (not isinstance(info, dict) or info.get('id') != manifest['video_id']
            or info.get('_type', 'video') != 'video'
            or info.get('extractor_key', '').lower() != 'youtube' or 'entries' in info):
        raise SourceRejected('policy')
    if info.get('is_live') or info.get('live_status') in {'is_live', 'is_upcoming', 'post_live'}:
        raise SourceRejected('live')
    if info.get('availability') not in (None, 'public', 'unlisted') or (info.get('age_limit') or 0) >= 18:
        raise SourceRejected('access')
    try:
        duration = float(info['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise SourceRejected('invalid')
        if duration > manifest['max_duration']:
            raise SourceRejected('duration')
    except (ValueError, KeyError, TypeError):
        raise SourceRejected('invalid') from None
    selected = info.get('requested_formats') or [info]
    if info.get('has_drm') or any(item.get('has_drm') for item in selected):
        raise SourceRejected('access')
    if (not any(item.get('vcodec') not in (None, 'none') for item in selected)
            or not any(item.get('acodec') not in (None, 'none') for item in selected)):
        raise SourceRejected('invalid')
    estimate = 0
    for item in selected:
        try:
            if item.get('height') and float(item['height']) > manifest['max_height']:
                raise SourceRejected('policy')
            estimate += float(item.get('filesize') or item.get('filesize_approx') or 0)
        except (ValueError, TypeError):
            raise SourceRejected('invalid') from None
        validate_provider_url(item.get('url', ''))
        if item.get('protocol') not in (None, 'https') or item.get('fragments'):
            # Use direct provider HTTPS streams, never delegate remote manifests to FFmpeg.
            raise SourceRejected('policy')
    if not math.isfinite(estimate) or estimate > manifest['max_bytes']:
        raise SourceRejected('quota')


def downloader_options(manifest: dict, progress_hook, logger, postprocessor_hook=None) -> dict:
    return {
        'format': lambda context: select_format(context, manifest),
        'merge_output_format': 'mp4',
        'outtmpl': str(Path(manifest['directory']) / 'source.%(ext)s'),
        'paths': {'home': manifest['directory'], 'temp': manifest['directory']},
        'noplaylist': True, 'max_downloads': 1, 'allowed_extractors': ['^youtube$'],
        'skip_unavailable_fragments': False, 'concurrent_fragment_downloads': 1,
        'socket_timeout': 20, 'retries': 1, 'fragment_retries': 1, 'extractor_retries': 0,
        'file_access_retries': 0, 'max_filesize': manifest['max_bytes'],
        'continuedl': False, 'overwrites': False, 'nopart': False,
        'external_downloader': {'default': 'native'},
        'ffmpeg_location': manifest['ffmpeg'], 'keepvideo': False,
        'writeinfojson': False, 'writethumbnail': False, 'writesubtitles': False,
        'writeautomaticsub': False, 'writedescription': False, 'writeannotations': False,
        'cachedir': False, 'quiet': True, 'noprogress': True, 'no_warnings': True,
        'logger': logger, 'progress_hooks': [progress_hook],
        'postprocessor_hooks': [postprocessor_hook] if postprocessor_hook else [],
        'geo_bypass': False, 'geo_verification_proxy': None, 'proxy': '',
        'usenetrc': False, 'cookiefile': None, 'cookiesfrombrowser': None,
        'username': None, 'password': None, 'videopassword': None,
        'allow_unplayable_formats': False, 'enable_file_urls': False,
        'nocheckcertificate': False, 'impersonate': None, 'mark_watched': False,
        'js_runtimes': {'node': {'path': manifest['node']}}, 'remote_components': [],
        # Use the pinned SDK's maintained public defaults. Do not force a client,
        # rotate clients after access denial, or supply authentication/custom tokens.
    }


def _classify(error: BaseException) -> str:
    if isinstance(error, SourceRejected):
        return error.code
    # Examine errors only in memory. Never emit/log their URLs, tokens or text.
    text = str(error).lower()
    if any(word in text for word in ('private', 'sign in', 'login', '403', '429', 'drm', 'premium', 'age-restricted',
                                     'not available in your country', 'bot', 'po token', 'geo-restricted')):
        return 'access'
    if any(word in text for word in ('no video formats', 'requested format', 'only images are available')):
        return 'formats'
    if any(word in text for word in ('unavailable', 'removed', '404', 'copyright')):
        return 'unavailable'
    if any(word in text for word in ('timed out', 'timeout', 'connection', 'network', '502', '503', '504', 'name resolution')):
        return 'network'
    if any(word in text for word in ('no space', 'file too large', 'max-filesize')):
        return 'quota'
    return 'worker'


def extract_metadata(ydl, url: str, emit):
    """One bounded retry for transient transport failure, never an access denial."""
    for attempt in range(2):
        try:
            return ydl.extract_info(url, download=False)
        except Exception as error:
            if attempt or _classify(error) != 'network':
                raise
            emit({'event': 'progress', 'stage': 'getting_metadata', 'fraction': None,
                  'details': {'phase': 'metadata'}})
            time.sleep(1)


class _QuietLogger:
    def debug(self, *_args, **_kwargs):
        pass

    info = warning = error = debug


def stream_size(item: dict) -> int:
    return (_safe_numeric(item.get('filesize'), integer=True)
            or _safe_numeric(item.get('filesize_approx'), integer=True) or 0)


def direct_stream_limit(item: dict) -> str | None:
    if item.get('has_drm'):
        return 'drm'
    if item.get('protocol') not in (None, 'https') or item.get('fragments'):
        return 'unsupported_transport'
    try:
        validate_provider_url(item.get('url', ''))
    except SourceRejected:
        return 'unsupported_transport'
    return None


def source_plans(formats: list[dict], max_height: int, max_bytes: int) -> tuple[list, dict]:
    """Rank eligible complete A/V plans by actual dimensions, then SDK preference."""
    formats = [item for item in formats if isinstance(item, dict) and item.get('format_id')]
    audio = [item for item in reversed(formats) if item.get('vcodec') in (None, 'none')
             and item.get('acodec') not in (None, 'none') and not direct_stream_limit(item)]
    plans, limits = [], set()
    observed, eligible = 0, 0
    for position, video in enumerate(formats):
        if video.get('vcodec') in (None, 'none', 'images'):
            continue
        height = _safe_numeric(video.get('height'), integer=True)
        width = _safe_numeric(video.get('width'), integer=True)
        if height and 2 <= height <= 16384:
            observed = max(observed, height)
        if reason := direct_stream_limit(video):
            limits.add(reason)
            continue
        if not height or not width or not 2 <= height <= 16384 or not 2 <= width <= 16384:
            limits.add('unknown_dimensions')
            continue
        if height > max_height:
            limits.add('resolution_cap')
            continue
        size = stream_size(video)
        if size > max_bytes:
            limits.add('storage_quota')
            continue
        if video.get('acodec') not in (None, 'none'):
            if video.get('ext') not in {'mp4', 'webm', 'mkv'}:
                limits.add('unsupported_container')
                continue
            tracks = (video,)
        else:
            if not audio:
                limits.add('missing_audio')
                continue
            partner = next((item for item in audio if size + stream_size(item) <= max_bytes), None)
            if partner is None:
                limits.add('storage_quota')
                continue
            tracks = (video, partner)
        eligible = max(eligible, height)
        plans.append(((width * height, position), tracks))
    return plans, {'observed_max_height': observed or None, 'eligible_max_height': eligible or None,
                   'max_height': max_height, 'limits': sorted(limits)}


def select_format(context: dict, manifest: dict):
    """yt-dlp custom format selector. Mux only the two chosen direct HTTPS tracks."""
    plans, diagnostics = source_plans(context['formats'], manifest['max_height'], manifest['max_bytes'])
    if not plans:
        if 'storage_quota' in diagnostics['limits']:
            raise SourceRejected('quota')
        if 'drm' in diagnostics['limits']:
            raise SourceRejected('access')
        return
    _, tracks = max(plans, key=lambda plan: plan[0])
    video = tracks[0]
    if len(tracks) == 1:
        yield video
        return
    audio = tracks[1]
    yield {
        'format_id': f"{video['format_id']}+{audio['format_id']}",
        'format': f"{video.get('format') or video['format_id']}+{audio.get('format') or audio['format_id']}",
        'requested_formats': tracks, 'ext': 'mp4', 'protocol': 'https+https',
        **{name: video.get(name) for name in ('width', 'height', 'fps', 'resolution', 'vcodec', 'dynamic_range')},
        **{name: audio.get(name) for name in ('acodec', 'abr', 'asr', 'audio_channels', 'language')},
        'filesize_approx': sum(map(stream_size, tracks)) or None,
    }


def display_metadata(info: dict, manifest: dict | None = None) -> dict:
    """Publish available quality descriptions without any download URL or token."""
    formats = []
    raw_formats = list(info.get('requested_formats') or []) + list(info.get('formats') or [])
    if not raw_formats:
        raw_formats = [info]
    seen = set()
    for item in raw_formats:
        if not isinstance(item, dict) or direct_stream_limit(item):
            continue
        ident = str(item.get('format_id') or '')
        if ident in seen:
            continue
        seen.add(ident)
        formats.append({'id': ident, 'width': item.get('width'), 'height': item.get('height'),
                        'fps': item.get('fps'), 'video_codec': item.get('vcodec'),
                        'audio_codec': item.get('acodec'), 'ext': item.get('ext'),
                        'size_bytes': item.get('filesize'),
                        'has_video': item.get('vcodec') not in (None, 'none'),
                        'has_audio': item.get('acodec') not in (None, 'none')})
    quality = None
    if manifest:
        _, quality = source_plans(info.get('formats') or raw_formats, manifest['max_height'], manifest['max_bytes'])
        quality['selected_format_ids'] = [item.get('format_id') for item in info.get('requested_formats') or [info]]
    return sanitize_metadata({'title': info.get('title'), 'duration': info.get('duration'), 'quality': quality,
                              'width': info.get('width'), 'height': info.get('height'),
                              'fps': info.get('fps'), 'formats': formats})


class DownloadProgress:
    """Accumulate sequential video/audio tracks while keeping unknown totals unknown."""
    def __init__(self, info: dict, max_bytes: int):
        self.max_bytes = max_bytes
        self.tracks = {}
        self.names = {}
        for index, item in enumerate(info.get('requested_formats') or [info]):
            key = str(item.get('format_id') or f'track-{index}')
            exact = _safe_numeric(item.get('filesize'), integer=True)
            estimate = _safe_numeric(item.get('filesize_approx'), integer=True)
            self.tracks[key] = {'done': 0, 'total': exact if exact else None,
                                'estimate': exact or estimate or None, 'seen': False}

    def update(self, event: dict) -> dict:
        info = event.get('info_dict') or {}
        key = str(info.get('format_id') or '')
        name = str(event.get('filename') or '')
        if key not in self.tracks:
            key = self.names.get(name) or next((k for k, track in self.tracks.items() if not track['seen']), key or 'track')
        self.names[name] = key
        track = self.tracks.setdefault(key, {'done': 0, 'total': None, 'estimate': None, 'seen': False})
        track['seen'] = True
        downloaded = _safe_numeric(event.get('downloaded_bytes'), integer=True)
        total = _safe_numeric(event.get('total_bytes'), integer=True)
        estimate = _safe_numeric(event.get('total_bytes_estimate'), integer=True)
        if downloaded is not None:
            track['done'] = max(track['done'], downloaded)
        if total:
            track['total'] = max(track['total'] or 0, total)
            track['estimate'] = max(track['estimate'] or 0, total)
        elif estimate:
            track['estimate'] = max(track['estimate'] or 0, estimate)
        if event.get('status') == 'finished' and track['done']:
            track['total'] = track['done']
            track['estimate'] = track['done']
        done = sum(t['done'] for t in self.tracks.values())
        exact = sum(t['total'] for t in self.tracks.values()) if all(t['total'] for t in self.tracks.values()) else None
        estimated = sum(t['estimate'] for t in self.tracks.values()) if all(t['estimate'] for t in self.tracks.values()) else None
        if done > self.max_bytes or (exact or estimated or 0) > self.max_bytes:
            raise SourceRejected('quota')
        denominator = exact or estimated
        return sanitize_progress({'phase': 'download', 'downloaded_bytes': done,
            'total_bytes': exact, 'total_bytes_estimate': estimated,
            'speed_bps': event.get('speed'), 'eta_seconds': event.get('eta'),
            'percent': min(100, done / denominator * 100) if denominator else None,
            'format_id': key, 'resolution': f"{info['width']}×{info['height']}" if info.get('width') and info.get('height') else '',
            'video_codec': info.get('vcodec'), 'audio_codec': info.get('acodec')})


def execute(manifest: dict, emit) -> None:
    config.RUNTIME.validate_writable(manifest['directory'], 'Ingestion output directory')
    for name in ('ffmpeg', 'node'):
        tool = config.binary(name)
        if not tool or Path(manifest[name]) != Path(tool):
            raise SourceRejected('policy')
    # Remove cloud credentials before loading yt-dlp or spawning its JS runtime.
    for name in list(os.environ):
        if name.upper() in {'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY',
                            'YOUTUBE_APPROVAL_REFERENCE', 'YOUTUBE_APPROVED_VIDEO_IDS'}:
            os.environ.pop(name, None)
    video_id = manifest.get('video_id', '')
    if not _ID.fullmatch(video_id) or IngestPolicy.from_env().gate(video_id):
        raise SourceRejected('policy')
    if youtube_reference(manifest.get('url', '')) != f'https://www.youtube.com/watch?v={video_id}':
        raise SourceRejected('policy')
    directory = Path(manifest['directory'])
    if _linked(directory) or not directory.is_dir() or not directory.is_absolute():
        raise SourceRejected('policy')
    if not _dependency_ready():
        raise SourceRejected('dependency')
    os.environ['YTDLP_NO_PLUGINS'] = '1'
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.youtube import YoutubeIE
    from yt_dlp.globals import plugin_dirs
    from yt_dlp.utils import MaxDownloadsReached
    plugin_dirs.value = []
    began = time.monotonic()
    tracker = None

    def progress_hook(event):
        if time.monotonic() - began > manifest['timeout_sec']:
            raise SourceRejected('timeout')
        if tracker is None:
            raise SourceRejected('worker')
        details = tracker.update(event)
        emit({'event': 'progress', 'stage': 'retrieving_source',
              'fraction': details['percent'] / 100 if details['percent'] is not None else None,
              'details': details})

    def postprocessor_hook(event):
        if 'merger' in str(event.get('postprocessor', '')).lower() and event.get('status') in {'started', 'processing', 'finished'}:
            emit({'event': 'progress', 'stage': 'merging_media', 'fraction': None,
                  'details': {'phase': 'merge', 'speed_bps': None, 'eta_seconds': None}})

    emit({'event': 'progress', 'stage': 'getting_metadata', 'fraction': None,
          'details': {'phase': 'metadata'}})
    with YoutubeDL(downloader_options(manifest, progress_hook, _QuietLogger(), postprocessor_hook), auto_init=False) as ydl:
        ydl.add_info_extractor(YoutubeIE())
        ydl._request_director = ydl.build_request_director([_provider_handler_class()])
        info = extract_metadata(ydl, manifest['url'], emit)
        validate_metadata(info, manifest)
        public_metadata = display_metadata(info, manifest)
        emit({'event': 'metadata', 'metadata': public_metadata})
        tracker = DownloadProgress(info, manifest['max_bytes'])
        emit({'event': 'progress', 'stage': 'retrieving_source', 'fraction': None,
              'details': {'phase': 'download', 'downloaded_bytes': 0}})
        # Already validated single-video metadata; no user-supplied second URL.
        try:
            ydl.process_ie_result(info, download=True)
        except MaxDownloadsReached:
            # yt-dlp deliberately raises this AFTER completing max_downloads=1.
            # The parent still requires exactly one real, verified A/V source.
            pass
    candidates = [path for path in directory.iterdir() if _FILE.fullmatch(path.name) and not _linked(path) and path.is_file()]
    if len(candidates) != 1 or candidates[0].stat().st_size > manifest['max_bytes']:
        raise SourceRejected('invalid')
    emit({'event': 'result', 'filename': candidates[0].name, 'video_id': video_id,
          'title': public_metadata['title'] or f'YouTube {video_id}', 'metadata': public_metadata})


def main(arguments: list[str] | None = None) -> int:
    def emit(event):
        print(json.dumps(event, ensure_ascii=True), flush=True)
    try:
        arguments = sys.argv[1:] if arguments is None else arguments
        if len(arguments) != 1:
            raise SourceRejected('policy')
        request = config.RUNTIME.validate_writable(arguments[0], 'Ingestion request')
        if not request.is_file() or _linked(request) or request.stat().st_size > 16000:
            raise SourceRejected('policy')
        manifest = json.loads(request.read_text(encoding='utf-8'))
        if request.resolve().parent != Path(manifest['directory']).resolve():
            raise SourceRejected('policy')
        execute(manifest, emit)
        return 0
    except Exception as error:
        emit({'event': 'error', 'code': _classify(error)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
