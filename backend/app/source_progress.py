"""Public source progress is a small allowlist, never raw downloader metadata."""
import math
import re


_QUALITY_LIMITS = frozenset({
    'resolution_cap', 'unsupported_transport', 'unsupported_container', 'drm',
    'storage_quota', 'missing_audio', 'unknown_dimensions',
})


def fresh_progress():
    return {'phase': 'validating', 'downloaded_bytes': None, 'total_bytes': None,
            'total_bytes_estimate': None, 'speed_bps': None, 'eta_seconds': None,
            'percent': None, 'cache_hit': False, 'metadata': None}


def number(value, maximum=10**15, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
        return None
    return int(value) if integer else value


def source_quality(value):
    """Sanitize the adapter's selection summary at both worker and API boundaries."""
    if not isinstance(value, dict):
        return None
    ids = value.get('selected_format_ids')
    limits = value.get('limits')
    return {
        **{key: number(value.get(key), 16384, True)
           for key in ('observed_max_height', 'eligible_max_height', 'max_height')},
        'selected_format_ids': [item for item in (ids[:2] if isinstance(ids, list) else [])
                                if isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9_.+\-]{1,80}', item)],
        'limits': sorted({item for item in (limits[:32] if isinstance(limits, list) else [])
                          if isinstance(item, str) and item in _QUALITY_LIMITS}),
    }


def metadata(value):
    if not isinstance(value, dict):
        return None
    clean = {'title': str(value.get('title') or '')[:240],
             'duration': number(value.get('duration')), 'width': number(value.get('width'), 16384, True),
             'height': number(value.get('height'), 16384, True), 'fps': number(value.get('fps'), 1000), 'formats': []}
    if quality := source_quality(value.get('quality')):
        clean['quality'] = quality
    formats = value.get('formats')
    for item in (formats if isinstance(formats, list) else [])[:80]:
        if not isinstance(item, dict):
            continue
        row = {}
        for key in ('id', 'video_codec', 'audio_codec', 'ext'):
            text = str(item.get(key) or '')[:80]
            row[key] = text if re.fullmatch(r'[A-Za-z0-9_.+-]*', text) else ''
        row.update({key: number(item.get(key), 16384, True) for key in ('width', 'height')})
        row.update(fps=number(item.get('fps'), 1000), size_bytes=number(item.get('size_bytes'), integer=True),
                   has_video=item.get('has_video') is True, has_audio=item.get('has_audio') is True)
        clean['formats'].append(row)
    return clean


def update_progress(previous, details=None, phase=None):
    defaults = fresh_progress()
    result = {**defaults, **{k: v for k, v in (previous if isinstance(previous, dict) else {}).items() if k in defaults}}
    details = details if isinstance(details, dict) else {}
    next_phase = details.get('phase', phase)
    if next_phase in {'validating', 'metadata', 'download', 'merge', 'verify', 'cache', 'ready', 'failed', 'cancelled'}:
        result['phase'] = next_phase
    for key in ('downloaded_bytes', 'total_bytes', 'total_bytes_estimate', 'speed_bps', 'eta_seconds', 'percent'):
        if key in details:
            result[key] = number(details[key], 100 if key == 'percent' else 10**15,
                                 key in {'downloaded_bytes', 'total_bytes', 'total_bytes_estimate'})
    if result['phase'] != 'download':
        result['speed_bps'], result['eta_seconds'] = None, None
    if 'metadata' in details:
        result['metadata'] = metadata(details['metadata'])
    if isinstance(details.get('cache_hit'), bool):
        result['cache_hit'] = details['cache_hit']
    return result
