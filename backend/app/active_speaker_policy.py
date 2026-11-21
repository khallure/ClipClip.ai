"""Conservative decisions from *joint audio-visual model* observations.

This module does not detect speech, measure mouths, compute diarization, or
invent model confidence. Call it separately for each M1 shot so camera cuts reset
all hold/hysteresis state. ``confidence`` is an input model score in [0, 1], not a
calibrated probability of correctness. Compact model intervals retain the minimum
observed selected confidence; manual/fallback intervals always use ``None``.
"""
from __future__ import annotations

import math
from bisect import bisect_right


def _number(value, name, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number.')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{name} is outside the supported range.')
    return float(value)


def _track(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('track_id must be a nonnegative integer.')
    return value


def _events(events, start, end):
    if not isinstance(events, (list, tuple)) or len(events) > 100000:
        raise ValueError('A bounded list of model events is required.')
    clean, previous = [], -math.inf
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('Each model event must be an object.')
        time = _number(event.get('time'), 'Event time', minimum=start, maximum=end)
        if time <= previous:
            raise ValueError('Model events must have unique increasing source times.')
        previous = time
        scores = event.get('scores', [])
        if not isinstance(scores, list) or len(scores) > 12:
            raise ValueError('At most twelve face scores are supported per event.')
        rows, ids = [], set()
        for row in scores:
            if not isinstance(row, dict):
                raise ValueError('Each model score must be an object.')
            ident = _track(row.get('track_id'))
            if ident in ids:
                raise ValueError('Duplicate track_id in a model event.')
            ids.add(ident)
            confidence = _number(row.get('confidence'), 'Model confidence', minimum=0, maximum=1)
            score = _number(row.get('score'), 'Raw model score')
            face = row.get('face_confidence')
            if face is not None:
                face = _number(face, 'Face confidence', minimum=0, maximum=1)
            rows.append({'track_id': ident, 'confidence': confidence, 'score': score,
                         'face_confidence': face})
        for flag in ('valid', 'audio_present'):
            if flag in event and not isinstance(event[flag], bool):
                raise ValueError(f'{flag} must be a boolean when provided.')
        clean.append({'time': time, 'scores': sorted(rows, key=lambda row: (-row['confidence'], row['track_id'])),
                      'valid': event.get('valid', True), 'audio_present': event.get('audio_present', True)})
    return clean


def _manual(overrides, start, end):
    if overrides is None:
        return []
    if not isinstance(overrides, (list, tuple)) or len(overrides) > 4000:
        raise ValueError('A bounded list of manual intervals is required.')
    result = []
    for value in overrides:
        if not isinstance(value, dict):
            raise ValueError('Manual intervals must be objects.')
        first = _number(value.get('start'), 'Manual start', minimum=start, maximum=end)
        last = _number(value.get('end'), 'Manual end', minimum=start, maximum=end)
        if last <= first:
            raise ValueError('Manual end must be after start.')
        ident = value.get('track_id')
        ident = None if ident is None else _track(ident)
        focus = value.get('focus', 'both' if ident is None else 'auto')
        if focus not in {'auto', 'left', 'right', 'both', 'locked'}:
            raise ValueError('Unsupported manual focus.')
        if focus == 'auto' and ident is None:
            raise ValueError('Manual auto focus requires a face track.')
        if focus == 'both':
            ident = None
        result.append({'start': first, 'end': last, 'track_id': ident, 'focus': focus})
    result.sort(key=lambda item: item['start'])
    if any(current['start'] < prior['end']-1e-9 for prior, current in zip(result, result[1:])):
        raise ValueError('Manual intervals must not overlap.')
    return result


def _append(intervals, first, last, decision):
    if last <= first:
        return
    current = {'start': first, 'end': last, **decision}
    keys = ('track_id', 'focus', 'source', 'review_required', 'reason')
    if intervals and abs(intervals[-1]['end']-first) < 1e-8 and all(intervals[-1][k] == current[k] for k in keys):
        previous = intervals[-1]
        previous['end'] = last
        if current['source'] == 'model':
            # This is a conservative aggregate of supplied evidence, not a
            # made-up confidence for manual choices or held/stale predictions.
            previous['confidence'] = min(previous['confidence'], current['confidence'])
            previous['score'] = min(previous['score'], current['score'])
    else:
        intervals.append(current)


def _overlay(intervals, overrides):
    if not overrides:
        return intervals
    boundaries = sorted({number for item in intervals+overrides for number in (item['start'], item['end'])})
    result, index, override_index = [], 0, 0
    for first, last in zip(boundaries, boundaries[1:]):
        middle = (first+last)/2
        while index+1 < len(intervals) and middle >= intervals[index]['end']:
            index += 1
        while override_index < len(overrides) and middle >= overrides[override_index]['end']:
            override_index += 1
        current = overrides[override_index] if override_index < len(overrides) else None
        forced = current if current and current['start'] <= middle < current['end'] else None
        if forced:
            decision = {'track_id': forced['track_id'], 'focus': forced['focus'],
                        'confidence': None, 'score': None, 'source': 'manual',
                        'reason': 'manual_override', 'review_required': False}
        else:
            decision = {k: v for k, v in intervals[index].items() if k not in {'start', 'end'}}
        _append(result, first, last, decision)
    return result


def decide_speaker(events, start, end, *, threshold=.75, margin=.2, min_hold=1.0,
                   hysteresis=.1, overrides=None, max_gap=1.0, face_threshold=.6):
    """Resolve one shot into source-time intervals with auditable abstentions.

    Acquisition/switches require a unique winner repeated at actual observation
    times for ``min_hold`` seconds. A one-frame high score never selects someone.
    The current track can remain selected down to threshold-hysteresis when it
    still wins clearly. Uncertain/overlapping/stale evidence briefly holds the
    previous framing (with confidence=None and review), then uses Focus Both.

    ``valid=False`` must be supplied when joint AV evidence is unusable; no
    audio/face-only fallback is manufactured here. ``max_gap`` bounds how long a
    sampled model observation is valid. Manual intervals overlay the automatic
    result and never acquire model confidence. No state is shared across calls.
    """
    start = _number(start, 'Shot start', minimum=0)
    end = _number(end, 'Shot end', minimum=start)
    if end <= start:
        raise ValueError('Shot end must be after start.')
    threshold = _number(threshold, 'Threshold', minimum=0, maximum=1)
    margin = _number(margin, 'Margin', minimum=0, maximum=1)
    hysteresis = _number(hysteresis, 'Hysteresis', minimum=0, maximum=threshold)
    min_hold = _number(min_hold, 'Minimum hold', minimum=0)
    max_gap = _number(max_gap, 'Maximum evidence gap', minimum=.000001)
    face_threshold = _number(face_threshold, 'Face threshold', minimum=0, maximum=1)
    values = _events(events, start, end)
    manual = _manual(overrides, start, end)
    times = [item['time'] for item in values]
    actual = set(times)
    boundaries = {start, end}
    for time in times:
        boundaries.update(t for t in (time, time+min_hold, time+max_gap, time+max_gap+min_hold) if start <= t <= end)
    boundaries = sorted(boundaries)
    active = pending = None
    active_since = pending_since = uncertain_since = None
    intervals = []

    def fallback(time, reason):
        nonlocal active, active_since, uncertain_since
        if uncertain_since is None:
            uncertain_since = time
        if active is not None and time < uncertain_since+min_hold-1e-9:
            return {'track_id': active, 'focus': 'locked', 'confidence': None, 'score': None,
                    'source': 'fallback', 'reason': reason+'_hold', 'review_required': True}
        active, active_since = None, None
        return {'track_id': None, 'focus': 'both', 'confidence': None, 'score': None,
                'source': 'fallback', 'reason': reason, 'review_required': True}

    for time, last in zip(boundaries, boundaries[1:]):
        index = bisect_right(times, time)-1
        event = values[index] if index >= 0 else None
        reason, winner = None, None
        if event is None or time >= event['time']+max_gap-1e-9:
            reason = 'missing_evidence'
        elif not event['audio_present']:
            reason = 'audio_unavailable'
        elif not event['valid']:
            reason = 'invalid_av_evidence'
        elif not event['scores']:
            reason = 'missing_face_tracks'
        else:
            rows = event['scores']
            top = rows[0]
            second = rows[1]['confidence'] if len(rows) > 1 else 0.0
            is_current = top['track_id'] == active
            cutoff = threshold-hysteresis if is_current else threshold
            required_margin = max(0, margin-hysteresis) if is_current else margin
            if len(rows) > 1 and top['confidence'] >= threshold and second >= threshold:
                reason = 'overlapping_or_ambiguous_speakers'
            elif top['face_confidence'] is not None and top['face_confidence'] < face_threshold:
                reason = 'low_face_confidence'
            elif top['confidence'] < cutoff:
                reason = 'low_model_confidence'
            elif top['confidence']-second < required_margin:
                reason = 'insufficient_speaker_margin'
            else:
                winner = top

        if winner is None:
            pending = pending_since = None
            decision = fallback(time, reason)
        elif winner['track_id'] == active:
            pending = pending_since = uncertain_since = None
            decision = {'track_id': active, 'focus': 'auto', 'confidence': winner['confidence'],
                        'score': winner['score'], 'source': 'model', 'reason': 'selected_av_model',
                        'review_required': False}
        else:
            if pending != winner['track_id']:
                pending, pending_since = winner['track_id'], time
            confirmed = (time in actual and time >= pending_since+min_hold-1e-9
                         and (active_since is None or time >= active_since+min_hold-1e-9))
            if confirmed:
                active, active_since = pending, time
                pending = pending_since = uncertain_since = None
                decision = {'track_id': active, 'focus': 'auto', 'confidence': winner['confidence'],
                            'score': winner['score'], 'source': 'model', 'reason': 'selected_av_model',
                            'review_required': False}
            else:
                decision = fallback(time, 'awaiting_stable_av_evidence')
        _append(intervals, time, last, decision)

    intervals = _overlay(intervals, manual)
    selected = sum(item['end']-item['start'] for item in intervals if item['source'] == 'model')
    manual_duration = sum(item['end']-item['start'] for item in intervals if item['source'] == 'manual')
    abstained = sum(item['end']-item['start'] for item in intervals if item['source'] == 'fallback')
    return {'intervals': intervals, 'review_required': any(item['review_required'] for item in intervals),
            'selected_duration': selected, 'manual_duration':manual_duration, 'abstained_duration':abstained,
            'confidence_semantics': 'minimum supplied joint-model confidence per model interval; uncalibrated',
            'settings': {'threshold': threshold, 'margin': margin, 'min_hold': min_hold,
                         'hysteresis': hysteresis, 'max_gap': max_gap, 'face_threshold': face_threshold}}
