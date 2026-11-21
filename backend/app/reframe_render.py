"""Deterministic reframe geometry shared by preview and FFmpeg export.

Coordinates/times in a plan refer to the original source. Each shot is an
independent composition; interpolation never crosses a camera cut. Rendering
uses FFmpeg's documented crop commands rather than zoompan or a second video
timeline, preserving the caller's audio, frame timestamps and clip duration.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from pathlib import Path


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'Invalid reframe {name}.')
    return float(value)


def _clamp(value, low, high):
    return min(high, max(low, value))


def _even(value):
    return max(2, int(value) // 2 * 2)


def _dimensions(plan):
    width, height = int(_number(plan.get('source_width'), 'width')), int(_number(plan.get('source_height'), 'height'))
    if not 2 <= width <= 16384 or not 2 <= height <= 16384:
        raise ValueError('Unsupported reframe source dimensions.')
    return width, height


def _shot_at(plan, time):
    shots = plan.get('shots')
    if not isinstance(shots, list) or not shots:
        raise ValueError('Reframe plan has no shots.')
    for shot in shots:
        if _number(shot['start'], 'shot start') <= time < _number(shot['end'], 'shot end'):
            return shot
    # Seeking just outside the selected interval holds its nearest composition.
    if time < shots[0]['start']:
        return shots[0]
    if time >= shots[-1]['end']:
        return shots[-1]
    raise ValueError('Reframe shot timeline has a gap.')


def _keys(shot):
    keys = shot.get('keyframes') or [{'time': shot['start'], 'x': .5, 'y': .5, 'zoom': 1}]
    manual = [key for key in keys if key.get('manual') is True]
    selected = manual or keys
    clean = []
    for key in selected:
        t = _number(key['time'], 'keyframe time')
        x, y, zoom = (_number(key[name], f'keyframe {name}') for name in ('x', 'y', 'zoom'))
        if not 0 <= x <= 1 or not 0 <= y <= 1 or not 1 <= zoom <= 3:
            raise ValueError('Reframe keyframe is outside the supported bounds.')
        clean.append({'time': t, 'x': x, 'y': y, 'zoom': zoom})
    clean.sort(key=lambda key: key['time'])
    return clean, bool(manual)


def _interpolate(keys, time, smoothing, times=None):
    if time <= keys[0]['time']:
        return {key: keys[0][key] for key in ('x', 'y', 'zoom')}
    if time >= keys[-1]['time']:
        return {key: keys[-1][key] for key in ('x', 'y', 'zoom')}
    times = times if times is not None else [key['time'] for key in keys]
    index = max(0, min(len(keys)-2, bisect_right(times, time)-1))
    first, last = keys[index:index+2]
    span = last['time']-first['time']
    ratio = (time-first['time'])/span if span > 0 else 1
    ease = (1-smoothing)*ratio + smoothing*ratio*ratio*(3-2*ratio)
    return {key: first[key]+(last[key]-first[key])*ease for key in ('x', 'y', 'zoom')}


def _focus_keys(plan, shot, keys, focus):
    samples = shot.get('face_samples') or []
    candidates = [sample for sample in samples if isinstance(sample.get('faces'), list) and sample['faces']]
    if not candidates:
        return [{'time': shot['start'], 'x': .25 if focus == 'left' else .75, 'y': .5,
                 'zoom': keys[0]['zoom']}]
    initial = next((sample for sample in candidates if len(sample['faces']) >= 2), candidates[0])
    ordered = sorted(initial['faces'], key=lambda face: face['bbox'][0]+face['bbox'][2]/2)
    selected = ordered[0 if focus == 'left' else -1]['track_id']
    width, height = _dimensions(plan)
    base_height = min(height, width*16/9)
    result = []
    key_times = [key['time'] for key in keys]
    for sample in candidates:
        face = next((item for item in sample['faces'] if item['track_id'] == selected), None)
        if face is None:
            continue  # Keep the selected identity; don't follow an occluder's face.
        box = face['bbox']
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError('Invalid face bounding box in reframe plan.')
        x, y, w, h = (_number(v, 'face bounding box') for v in box)
        if min(x, y, w, h) < 0 or x+w > 1.000001 or y+h > 1.000001:
            raise ValueError('Face bounding box is outside the source.')
        sample_time = _number(sample['time'], 'face sample time')
        zoom = _interpolate(keys, sample_time, 0, key_times)['zoom']
        result.append({'time': sample_time, 'x': _clamp(x+w/2, 0, 1),
                       'y': _clamp(y-h*.5 + base_height/zoom/height*.5, 0, 1), 'zoom': zoom})
    return sorted(result, key=lambda key: key['time']) or keys


def _prepare_shot(plan, shot):
    """Compile face paths and entering compositions once, without shared state."""
    keys, manual = _keys(shot)
    focus = shot.get('focus', 'auto')
    if focus not in {'auto', 'left', 'right', 'both', 'locked', 'active'}:
        raise ValueError('Unknown reframe focus.')
    prepared = {'keys': keys, 'times': [key['time'] for key in keys], 'manual': manual,
                'focus': focus, 'paths': {}, 'focus_paths': {}, 'segments': [], 'starts': []}
    intervals = shot.get('speaker_intervals', []) if focus == 'active' and not manual else []
    choices = intervals + shot.get('speaker_overrides', [])
    tracks = {item['track_id'] for item in choices if item.get('track_id') is not None}
    grouped = {track: [] for track in tracks}
    for sample in shot.get('face_samples', []) if tracks else []:
        for face in sample['faces']:
            if face['track_id'] in grouped:
                grouped[face['track_id']].append({**sample, 'faces': [face]})
    for track, samples in grouped.items():
        path = _focus_keys(plan, {**shot, 'face_samples': samples}, keys, 'left') if samples else []
        prepared['paths'][track] = (path, [key['time'] for key in path])
    focuses = {item.get('focus') for item in choices}
    if not manual:
        focuses.add(focus)
    for selected in focuses & {'left', 'right'}:
        path = _focus_keys(plan, shot, keys, selected)
        prepared['focus_paths'][selected] = (path, [key['time'] for key in path])
    # Resolve the preceding segment at the shared boundary, including any pan
    # still in progress. A metadata/reason boundary must not reset a held crop.
    for item in intervals:
        previous = prepared['segments'][-1] if prepared['segments'] else None
        anchor = _segment_pose(plan, shot, prepared, previous, item['start']) if previous else None
        ease = bool(previous and item['source'] == 'model' and (
            previous['item'].get('track_id') != item.get('track_id')
            or previous['item'].get('focus') == 'locked'))
        prepared['segments'].append({'item': item, 'anchor': anchor, 'ease': ease})
        prepared['starts'].append(item['start'])
    return prepared


def _speaker_pose(plan, shot, prepared, interval, time, manual):
    focus = interval.get('focus', 'both')
    if manual and focus == 'locked' and interval.get('track_id') is None:
        return {**_pose_for_shot(plan, shot, prepared, interval['start'], overrides=False), 'manual': True}
    keys, key_times = prepared['keys'], prepared['times']
    if focus in {'left','right'}:
        path, times = prepared['focus_paths'][focus]
        pose = _interpolate(path, time, plan.get('smoothing', .65), times)
    elif interval.get('track_id') is not None:
        path, times = prepared['paths'][interval['track_id']]
        index = bisect_right(times, time)
        distance = min((abs(times[i]-time) for i in (index-1, index) if 0 <= i < len(times)), default=math.inf)
        if distance > 1:
            return {'x':.5,'y':.5,'zoom':1,'focus':'both','shot_id':str(shot['id']),'manual':manual}
        pose = _interpolate(path, interval['start'] if focus == 'locked' else time, plan.get('smoothing',.65), times)
    else:
        pose = _interpolate(keys, interval['start'] if focus == 'locked' else time, plan.get('smoothing',.65), key_times)
    return {**pose,'focus':'both' if focus == 'both' else 'auto','shot_id':str(shot['id']),'manual':manual}


def _segment_pose(plan, shot, prepared, segment, time):
    item, anchor = segment['item'], segment['anchor']
    if item.get('focus') == 'locked' and anchor is not None:
        return dict(anchor)
    pose = _speaker_pose(plan, shot, prepared, item, time, False)
    duration = .2+.6*plan.get('smoothing', .65)
    if (segment['ease'] and anchor is not None and anchor['focus'] != 'both'
            and pose['focus'] != 'both' and time < item['start']+duration):
        ratio = _clamp((time-item['start'])/duration, 0, 1)
        ease = ratio*ratio*(3-2*ratio)
        pose.update({key: anchor[key]+(pose[key]-anchor[key])*ease for key in ('x', 'y', 'zoom')})
    return pose


def _pose_for_shot(plan, shot, prepared, time, *, overrides=True):
    # Preview seeking outside the plan holds the nearest shot composition.
    time = _clamp(time, shot['start'], shot['end'])
    if overrides:
        override = next((item for item in shot.get('speaker_overrides', []) if item['start'] <= time < item['end']), None)
        if override is not None:
            return _speaker_pose(plan, shot, prepared, override, time, True)
    keys, times = prepared['keys'], prepared['times']
    manual, focus = prepared['manual'], prepared['focus']
    if focus == 'active' and not manual:
        if prepared['segments']:
            index = max(0, min(len(prepared['segments'])-1, bisect_right(prepared['starts'], time)-1))
            return _segment_pose(plan, shot, prepared, prepared['segments'][index], time)
        return {'x':.5,'y':.5,'zoom':1,'focus':'both','shot_id':str(shot['id']),'manual':False}
    if focus == 'active':
        focus = 'auto'
    if focus == 'locked':
        pose = {key: keys[0][key] for key in ('x', 'y', 'zoom')}
    else:
        if focus in {'left', 'right'} and not manual:
            keys, times = prepared['focus_paths'][focus]
        pose = _interpolate(keys, time, plan.get('smoothing', .65), times)
    return {**pose, 'focus': focus, 'shot_id': str(shot['id']), 'manual': manual}


def pose_at(plan: dict, time: float) -> dict:
    """Resolve normalized center/zoom; any manual keys replace the auto track."""
    time = _number(time, 'time')
    _dimensions(plan)
    smoothing = _number(plan.get('smoothing', .65), 'smoothing')
    if not 0 <= smoothing <= 1:
        raise ValueError('Reframe smoothing must be between zero and one.')
    shot = _shot_at(plan, time)
    return _pose_for_shot(plan, shot, _prepare_shot(plan, shot), time)


def crop_at(plan: dict, time: float) -> dict:
    """Return even pixel crop coordinates, bounded to the original source."""
    width, height = _dimensions(plan)
    pose = pose_at(plan, time)
    return _crop_from_pose(width, height, pose)


def _crop_from_pose(width, height, pose):
    if pose['focus'] == 'both':
        x, y, crop_width, crop_height = 0, 0, _even(width), _even(height)
        fit_mode = 'contain'
    else:
        crop_height = min(_even(height), _even(min(height, width*16/9)/pose['zoom']))
        crop_width = min(_even(width), _even(crop_height*9/16))
        x = max(0, int(_clamp(pose['x']*width-crop_width/2, 0, width-crop_width))//2*2)
        y = max(0, int(_clamp(pose['y']*height-crop_height/2, 0, height-crop_height))//2*2)
        fit_mode = 'crop'
    if x+crop_width > width or y+crop_height > height:
        raise ValueError('Reframe crop exceeds the source.')
    return {'x': x, 'y': y, 'width': crop_width, 'height': crop_height,
            'fit_mode': fit_mode, 'pose': pose, 'shot_id': pose['shot_id']}


frame_geometry = crop_at


def render_filter(plan: dict, start: float, end: float, directory: Path) -> list[str]:
    """Write numeric-only crop commands and return the existing -vf filter list.

    Runtime crop width/height commands are supported by FFmpeg's crop filter;
    sendcmd evaluates them on the input's timeline, without resampling frames.
    Aspect-preserving scale/pad also supports a per-shot Focus Both composition.
    The caller keeps its existing -ss/-t/audio/caption/encode pipeline unchanged.
    """
    start, end = _number(start, 'start'), _number(end, 'end')
    if start < 0 or not 0 < end-start <= 600:
        raise ValueError('Choose a reframe interval of at most ten minutes.')
    if end > _number(plan.get('source_duration'), 'source duration')+.001:
        raise ValueError('Reframe interval exceeds the source duration.')
    shots = plan.get('shots') or []
    if not shots or start < shots[0]['start']-.001 or end > shots[-1]['end']+.001:
        raise ValueError('Reframe plan does not cover the export interval.')
    fps = _number(plan.get('source_fps', 30), 'frame rate')
    if not 0 < fps <= 240:
        raise ValueError('Unsupported frame rate for reframing.')
    smoothing = _number(plan.get('smoothing', .65), 'smoothing')
    if not 0 <= smoothing <= 1:
        raise ValueError('Reframe smoothing must be between zero and one.')
    source_width, source_height = _dimensions(plan)
    prepared = [_prepare_shot(plan, shot) for shot in shots]
    # Include cut timestamps independently of the frame-rate estimate. VFR input
    # receives a new composition on the first actual frame at/after each cut.
    times = {round(start+index/fps, 9) for index in range(math.ceil((end-start)*fps))}
    times.update(_number(shot['start'], 'shot start') for shot in shots if start <= shot['start'] < end)
    rows = []
    previous = None
    shot_index = 0
    for time in sorted(times):
        while shot_index < len(shots)-1 and time >= shots[shot_index]['end']:
            shot_index += 1
        shot = shots[shot_index]
        if not shot['start']-.000001 <= time <= shot['end']+.000001:
            raise ValueError('Reframe shot timeline has a gap.')
        resolved = _pose_for_shot(plan, shot, prepared[shot_index], time)
        geometry = _crop_from_pose(source_width, source_height, resolved)
        box = tuple(geometry[key] for key in ('width', 'height', 'x', 'y'))
        if box == previous:
            continue
        width, height, x, y = box
        # Reset offsets before dimensions change, then apply the final offsets.
        # Intermediate command states never escape to the output frame.
        factor = min(1080/width, 1920/height)
        scaled_width, scaled_height = min(1080, _even(width*factor)), min(1920, _even(height*factor))
        commands = ', '.join(f'[enter] {target} {key} {value}' for target, key, value in (
            ('crop@podcut', 'x', 0), ('crop@podcut', 'y', 0),
            ('crop@podcut', 'w', width), ('crop@podcut', 'h', height),
            ('crop@podcut', 'x', x), ('crop@podcut', 'y', y),
            ('scale@podcutscale', 'w', scaled_width), ('scale@podcutscale', 'h', scaled_height)))
        rows.append(f'{max(0,time-start):.9f} {commands};')
        previous = box
    data = '\n'.join(rows)+'\n'
    if len(data) > 32*1024*1024:
        raise ValueError('Reframe command timeline is too large.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory/'reframe-commands.txt.tmp'
    temporary.write_text(data, encoding='ascii')
    temporary.replace(directory/'reframe-commands.txt')
    first_shot = next(i for i, shot in enumerate(shots) if shot['start'] <= start < shot['end'])
    first = _crop_from_pose(source_width, source_height, _pose_for_shot(plan, shots[first_shot], prepared[first_shot], start))
    return [
        'sendcmd=f=reframe-commands.txt',
        f"crop@podcut=w={first['width']}:h={first['height']}:x={first['x']}:y={first['y']}:exact=1",
        'scale@podcutscale=1080:1920:eval=frame:flags=lanczos',
        'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black:eval=frame',
        'setsar=1',
    ]
