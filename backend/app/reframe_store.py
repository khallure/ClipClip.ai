"""Reframing is a separate, reusable project artifact; it never edits transcripts."""
import hashlib
import json
import os
from pathlib import Path

from .reframe_models import ReframePlan


def source_signature(source: Path, info: dict) -> str:
    stat = source.stat()
    # Identity/invalidation fingerprint, not a substitute for ingestion SHA verification.
    value = [source.name, stat.st_size, stat.st_mtime_ns,
             info['width'], info['height'], info['duration']]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def plan_id(signature: str, start: float, end: float) -> str:
    return hashlib.sha256(f'{signature}:{start:.6f}:{end:.6f}:reframe-v1'.encode()).hexdigest()[:32]


def _path(directory: Path, ident: str) -> Path:
    import re
    if not re.fullmatch(r'[a-f0-9]{32}', ident):
        raise ValueError('ID framing tidak valid.')
    root = directory/'reframes'
    if root.is_symlink() or bool(getattr(root, 'is_junction', lambda: False)()):
        raise ValueError('Folder framing tidak valid.')
    path = root/f'{ident}.json'
    if path.is_symlink():
        raise ValueError('File framing tidak valid.')
    return path


def load_plan(directory: Path, ident: str, source: Path, info: dict) -> dict:
    path = _path(directory, ident)
    if not path.is_file():
        raise FileNotFoundError('Framing belum disiapkan untuk rentang ini.')
    if path.stat().st_size > 12*1024*1024:
        raise ValueError('Metadata framing melebihi batas.')
    result = ReframePlan.model_validate_json(path.read_text(encoding='utf-8')).model_dump()
    if (result['id'] != ident or result['source_signature'] != source_signature(source, info)
            or result['source_width'] != info['width'] or result['source_height'] != info['height']):
        raise ValueError('Framing berasal dari sumber berbeda. Analisis ulang framing video ini.')
    return result


def save_plan(directory: Path, plan: dict) -> dict:
    result = ReframePlan.model_validate(plan).model_dump()
    path = _path(directory, result['id'])
    path.parent.mkdir(exist_ok=True)
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > 12*1024*1024:
        raise ValueError('Metadata framing melebihi batas.')
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(raw, encoding='utf-8')
    os.replace(temporary, path)
    return result


def manual_plan(source: Path, info: dict, start: float, end: float, crop_x=.5) -> dict:
    signature = source_signature(source, info)
    crop_width = min(info['width'], info['height']*9/16)
    center = ((info['width']-crop_width)*crop_x+crop_width/2)/info['width']
    result = ReframePlan(id=plan_id(signature,start,end), source_signature=signature,
        source_width=info['width'], source_height=info['height'], source_fps=info['fps'] or 30,
        source_duration=info['duration'], start=start,end=end,
        shots=[{'id':'shot-0001','start':start,'end':end,'keyframes':[{
            'time':start,'x':center,'y':.5,'zoom':1,'manual':False}]}]).model_dump()
    result['shots'][0]['auto_keyframes'] = [dict(k) for k in result['shots'][0]['keyframes']]
    return result


def preserve_manual(previous: dict | None, detected: dict) -> dict:
    for shot in detected['shots']:
        baseline = [dict(key) for key in shot['keyframes'] if not key.get('manual')]
        if baseline:
            shot['auto_keyframes'] = baseline
    if not previous:
        return detected
    for shot in detected['shots']:
        manual = [key for old in previous['shots'] for key in old['keyframes']
                  if key.get('manual') and shot['start'] <= key['time'] < shot['end']]
        if manual:
            shot['keyframes'] = sorted(manual, key=lambda key:key['time'])
            prior = next((old for old in previous['shots'] if old['start'] <= manual[0]['time'] < old['end']), None)
            if prior:
                shot['focus'] = prior['focus']
            shot.update(decision='manual')
        prior = next((old for old in previous['shots'] if old['start'] <= (shot['start']+shot['end'])/2 < old['end']),None)
        shot['speaker_overrides'] = [{**item,'start':max(item['start'],shot['start']),'end':min(item['end'],shot['end'])}
            for old in previous['shots'] for item in old.get('speaker_overrides',[])
            if item['start'] < shot['end'] and item['end'] > shot['start']]
        if prior and prior['focus'] != 'auto' and prior['decision'] == 'manual':
            shot.update(focus=prior['focus'],decision='manual',review_required=False,reason='Pilihan fokus manual dipertahankan.')
    detected['smoothing'] = previous['smoothing']
    return detected
