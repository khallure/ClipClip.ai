"""Composition regressions from policy fixtures, not an ASD accuracy benchmark."""
import copy
import json
import subprocess

import pytest

from app import config, media
from app.active_speaker_policy import decide_speaker
from app.reframe_render import crop_at, pose_at, render_filter


def event(time, first=.95, second=.1):
    return {'time': time, 'scores': [
        {'track_id': 1, 'score': first, 'confidence': first},
        {'track_id': 2, 'score': second, 'confidence': second}]}


def transition_plan(case, *, smoothing=.65, width=1920, height=1080):
    if case == 'interrupted_switch':
        values = [event(i/5) if i < 10 else event(i/5, .1, .95) if i < 16
                  else event(i/5, .9, .9) for i in range(26)]
    else:
        values = [event(i/5) for i in range(8)] + [event(1.6, .9, .9)]
        if case == 'reason_change':
            values += [event(1.8, .1, .1), event(2, .1, .1)]
            values += [event(i/5) for i in range(11, 26)]
        else:
            values += [event(1.8, .9, .9)] + [event(i/5) for i in range(10, 26)]
    intervals = decide_speaker(values, 0, 5, max_gap=.4)['intervals']
    moving = case != 'interrupted_switch'
    return {'source_width': width, 'source_height': height, 'source_fps': 25,
            'source_duration': 5, 'start': 0, 'end': 5, 'smoothing': smoothing,
            'shots': [{'id': case, 'start': 0, 'end': 5, 'focus': 'active',
                       'keyframes': [{'time': 0, 'x': .5, 'y': .5, 'zoom': 1}],
                       'speaker_intervals': intervals, 'speaker_overrides': [],
                       'face_samples': [{'time': i/5, 'faces': [
                           {'track_id': 1, 'bbox': [.1+(.08*i/5 if moving else 0), .2, .1, .2]},
                           {'track_id': 2, 'bbox': [.8, .2, .1, .2]}]} for i in range(26)]}]}


def coordinates(plan, time):
    pose = pose_at(plan, time)
    return tuple(pose[name] for name in ('x', 'y', 'zoom'))


@pytest.mark.parametrize('smoothing', [0, .65, 1])
def test_same_track_recovery_eases_from_held_composition(smoothing):
    plan = transition_plan('same_track', smoothing=smoothing)
    held = coordinates(plan, 1.6)
    assert coordinates(plan, 1.999999) == held
    assert coordinates(plan, 2) == held
    duration = .2+.6*smoothing
    assert held[0] < coordinates(plan, 2+duration/2)[0] < .15+.08*(2+duration/2)
    tracking = copy.deepcopy(plan)
    tracking['shots'][0]['speaker_intervals'] = [
        {'start': 0, 'end': 5, 'track_id': 1, 'focus': 'auto', 'source': 'model'}]
    assert coordinates(plan, 2+duration) == coordinates(tracking, 2+duration)


@pytest.mark.parametrize('smoothing', [0, .65, 1])
def test_locked_reason_changes_do_not_reset_composition(smoothing):
    plan = transition_plan('reason_change', smoothing=smoothing)
    held = coordinates(plan, 1.6)
    assert coordinates(plan, 1.799999) == held
    assert coordinates(plan, 1.8) == held
    assert coordinates(plan, 2.199999) == held
    assert coordinates(plan, 2.2) == held


@pytest.mark.parametrize('smoothing', [.65, 1])
def test_interrupted_pan_locks_actual_composed_pose(smoothing):
    plan = transition_plan('interrupted_switch', smoothing=smoothing)
    duration = .2+.6*smoothing
    ratio = .2/duration
    expected = .15+(.85-.15)*ratio*ratio*(3-2*ratio)
    assert coordinates(plan, 3.199999)[0] == pytest.approx(expected, abs=2e-6)
    assert coordinates(plan, 3.2)[0] == pytest.approx(expected)
    assert coordinates(plan, 3.8)[0] == pytest.approx(expected)
    assert pose_at(plan, 4.21)['focus'] == 'both'


def test_hold_retains_zoom_and_vertical_composition_during_face_motion():
    plan = transition_plan('reason_change')
    shot = plan['shots'][0]
    shot['keyframes'].append({'time': 5, 'x': .5, 'y': .5, 'zoom': 2})
    for sample in shot['face_samples']:
        sample['faces'][0]['bbox'][1] += sample['time']*.02
    held = coordinates(plan, 1.6)
    assert coordinates(plan, 1.8) == held
    assert coordinates(plan, 2.2) == held
    assert coordinates(plan, 2.8) != held


def test_nearest_shot_composition_is_held_outside_plan():
    plan = transition_plan('interrupted_switch')
    assert pose_at(plan, -.1) == pose_at(plan, 0)
    assert pose_at(plan, 5.1) == pose_at(plan, 5)
    # A leading model interval also holds its boundary pose, rather than using
    # the future/final interval or expiring its face evidence before the shot.
    plan['shots'][0]['speaker_intervals'][0].update(track_id=1, focus='auto', source='model')
    assert pose_at(plan, -10) == pose_at(plan, 0)


def test_thousands_of_hold_reason_segments_do_not_recurse_or_drift():
    plan = transition_plan('reason_change')
    shot = plan['shots'][0]
    shot['end'] = plan['end'] = plan['source_duration'] = 81
    shot['speaker_intervals'] = [{'start': 0, 'end': 1, 'track_id': 1, 'focus': 'auto', 'source': 'model'}]
    shot['speaker_intervals'] += [
        {'start': 1+i*.02, 'end': 1+(i+1)*.02, 'track_id': 1, 'focus': 'locked',
         'source': 'fallback', 'reason': str(i)} for i in range(3999)]
    shot['speaker_intervals'][-1]['end'] = 81
    assert coordinates(plan, 80.99) == coordinates(plan, 1)


@pytest.mark.parametrize('case', ['same_track', 'reason_change', 'interrupted_switch'])
def test_typescript_matches_transition_semantics_in_forward_and_reverse_seeks(tmp_path, case):
    node = config.binary('node')
    if not node:
        pytest.skip('Node required for preview geometry verification.')
    plan = transition_plan(case)
    times = [-10, 0, .999999, 1, 1.599999, 1.6, 1.799999, 1.8,
             1.999999, 2, 2.199999, 2.2, 2.4, 2.8, 2.999999, 3,
             3.199999, 3.2, 3.24, 3.8, 4.199999, 4.2, 5, 10]
    times += list(reversed(times))
    request = tmp_path/'request.json'
    request.write_text(json.dumps({'plan': plan, 'times': times}), encoding='utf-8')
    script = """import {readFileSync} from 'node:fs';import {pathToFileURL} from 'node:url';
const m=await import(pathToFileURL(process.argv[1]).href);
const {plan,times}=JSON.parse(readFileSync(process.argv[2],'utf8'));
process.stdout.write(JSON.stringify(times.map(t=>({pose:m.poseAt(plan,t),crop:m.cropAt(plan,t)}))));"""
    result = subprocess.run([node, '--input-type=module', '-e', script,
                             str(config.PROJECT/'frontend/lib/reframe.ts'), str(request)],
                            check=True, capture_output=True, text=True, encoding='utf-8', timeout=20)
    for time, actual in zip(times, json.loads(result.stdout)):
        expected = crop_at(plan, time)
        assert {name: actual['crop'][name] for name in ('x', 'y', 'width', 'height')} == {
            name: expected[name] for name in ('x', 'y', 'width', 'height')}
        assert actual['pose']['focus'] == expected['pose']['focus']
        for name in ('x', 'y', 'zoom'):
            assert actual['pose'][name] == pytest.approx(expected['pose'][name], abs=1e-10)


def ffmpeg(*args, cwd=None):
    return subprocess.run([media.tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin',
                           *map(str, args)], check=True, capture_output=True, cwd=cwd, timeout=90).stdout


@pytest.fixture(scope='module')
def gradient_source(tmp_path_factory):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('Real FFmpeg/FFprobe required.')
    directory = tmp_path_factory.mktemp('transition_gradient')
    target = directory/'source.mp4'
    ffmpeg('-y', '-f', 'lavfi', '-i', 'nullsrc=s=640x360:r=25:d=5,geq=lum=16+219*X/W:cb=128:cr=128',
           '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=5',
           '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', 12, '-threads', 2,
           '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', target)
    return target


@pytest.mark.parametrize('case,boundaries', [
    ('same_track', [1.6, 2]), ('reason_change', [1.6, 1.8, 2.2]),
    ('interrupted_switch', [3, 3.2])])
def test_real_mp4_boundary_frames_match_continuous_crop(gradient_source, tmp_path, case, boundaries):
    plan = transition_plan(case, width=640, height=360)
    filters = render_filter(plan, 0, 5, tmp_path)
    target = tmp_path/f'{case}.mp4'
    ffmpeg('-y', '-i', gradient_source, '-t', 5, '-map', '0:v:0', '-map', '0:a:0',
           '-vf', ','.join(filters), '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', 12,
           '-threads', 2, '-pix_fmt', 'yuv420p', '-c:a', 'aac', target, cwd=tmp_path)
    info = media.probe(target)
    assert (info['width'], info['height'], info['fps']) == (1080, 1920, 25)
    assert info['duration'] == pytest.approx(5, abs=.04) and info['has_audio']
    # A horizontal luminance ramp identifies the actual encoded source center.
    # Decode all center pixels once, so exact frame numbers do not depend on -ss.
    pixels = ffmpeg('-i', target, '-vf', 'crop=2:2:540:960', '-pix_fmt', 'gray', '-f', 'rawvideo', '-')
    assert len(pixels) == 125*4
    report = []
    for boundary in boundaries:
        for index in (round(boundary*25)-1, round(boundary*25), round(boundary*25)+1):
            time = index/25
            crop = crop_at(plan, time)
            expected = (crop['x']+crop['width']/2)/640*255
            actual = sum(pixels[index*4:index*4+4])/4
            assert actual == pytest.approx(expected, abs=3)
            report.append({'frame': index, 'time': time, 'crop_x': crop['x'],
                           'expected_luma': expected, 'decoded_luma': actual})
    if case == 'reason_change':
        # The metadata boundary in the middle of a lock preserves encoded pixels.
        assert pixels[44*4:45*4] == pixels[45*4:46*4] == pixels[46*4:47*4]
    if case == 'interrupted_switch':
        assert pixels[80*4:81*4] == pixels[95*4:96*4]
        assert sum(pixels[80*4:81*4])/4 < 120  # Not the right track's center (~217).
    (tmp_path/'plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    (tmp_path/'decoded-boundaries.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
