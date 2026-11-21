"""Real encodes prove numeric duration caps and media timing across frame rates.

These fixtures are generated locally; no network, speech model or API key is used.
"""
from array import array
import json
import math
import subprocess

import pytest

from app import config, media


START = .125
CAP = 20.0


def test_container_cannot_exceed_cap_by_more_than_one_frame(tmp_path, monkeypatch):
    info = {'duration': 20 + 1/30 + .0005, 'fps':30, 'width':320, 'height':180,
            'has_audio':True, 'video_codec':'h264', 'audio_codec':'aac'}
    monkeypatch.setattr(media, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(media, 'tool', lambda name: name)
    monkeypatch.setattr(media, 'probe', lambda path: info)
    with pytest.raises(media.MediaError, match='batas 20 detik'):
        media.export_clip(tmp_path/'source.mp4', tmp_path/'export',
            {'start':0, 'end':20, 'aspect':'original', 'crop_x':.5, 'caption_style':'none', 'duration_preset':'20'},
            None, info)
    assert not (tmp_path/'export/clip.mp4').exists()


def ffmpeg(*arguments):
    return subprocess.run([config.binary('ffmpeg'), '-hide_banner', '-loglevel', 'error',
                           '-nostdin', *map(str, arguments)], check=True,
                          capture_output=True, timeout=90).stdout


def inspect(path, *, frames=False):
    command = [config.binary('ffprobe'), '-v', 'error', '-of', 'json']
    if frames:
        command += ['-select_streams', 'v:0', '-show_frames',
                    '-show_entries', 'frame=best_effort_timestamp_time']
    else:
        command += ['-show_format', '-show_streams']
    result = subprocess.run([*command, str(path)], check=True, capture_output=True,
                            text=True, timeout=30)
    return json.loads(result.stdout)


def frame_times(path):
    return [float(frame['best_effort_timestamp_time'])
            for frame in inspect(path, frames=True)['frames']]


def decoded_frame(path, at):
    return ffmpeg('-ss', f'{at:.9f}', '-i', path, '-frames:v', '1', '-f', 'rawvideo',
                  '-pix_fmt', 'rgb24', '-')


def mean_error(a, b):
    assert len(a) == len(b) == 320 * 180 * 3
    return sum(abs(x-y) for x, y in zip(a, b)) / len(a)


def decoded_audio(path, start=0, duration=CAP):
    samples = array('h')
    samples.frombytes(ffmpeg('-ss', f'{start:.9f}', '-i', path, '-t', duration,
                             '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'))
    return samples


def rms(samples):
    return math.sqrt(sum(x*x for x in samples) / len(samples))


def correlation(a, b):
    assert len(a) == len(b) and a
    product = sum(x*y for x, y in zip(a, b))
    return product / math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))


@pytest.fixture(scope='module', params=[24, 25, 30, 60, 'vfr'])
def duration_source(request, tmp_path_factory):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('Real FFmpeg and FFprobe are required for duration integration tests.')
    kind = request.param
    directory = tmp_path_factory.mktemp(f'duration_source_{kind}')
    path = directory / 'owned_synthetic.mp4'
    rate = 60 if kind == 'vfr' else kind
    # Frame brightness changes with time, exposing an accidental seek to zero.
    video_filter = "eq=brightness='0.25*sin(2*PI*t)':eval=frame"
    if kind == 'vfr':
        # Preserve actual source PTS: 30 fps, then 20 fps, then 60 fps.
        # A frame timestamp assertion below verifies that this is genuinely VFR.
        video_filter += ",select='if(lt(t,7),not(mod(n,2)),if(lt(t,14),not(mod(n,3)),1))'"
    ffmpeg('-y', '-f', 'lavfi', '-i', f'testsrc2=size=320x180:rate={rate}:duration=21',
           '-f', 'lavfi', '-i', 'aevalsrc=0.2*sin(2*PI*(330*t+15*t*t)):s=48000:d=21',
           '-vf', video_filter, '-fps_mode', 'vfr' if kind == 'vfr' else 'cfr',
           '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p',
           '-threads', '2', '-c:a', 'aac', '-b:a', '192k', '-shortest', path)
    times = frame_times(path)
    differences = [round(b-a, 5) for a, b in zip(times, times[1:])]
    if kind == 'vfr':
        assert max(differences) - min(differences) > .025, differences[:20]
        assert any(abs(step-1/20) < .0001 for step in differences)
        assert any(abs(step-1/60) < .0001 for step in differences)
    else:
        assert all(abs(step-1/kind) < .0001 for step in differences)
    return path, times, kind


def test_real_twenty_second_cap_frames_and_audio(duration_source, tmp_path):
    source, source_times, kind = duration_source
    settings = {'start': START, 'end': START + CAP, 'duration_preset': '20',
                'aspect': 'original', 'crop_x': .5, 'caption_style': 'none'}
    result = media.export_clip(source, tmp_path, settings, None, media.probe(source))
    target = tmp_path / 'clip.mp4'
    details = inspect(target)
    video = next(stream for stream in details['streams'] if stream['codec_type'] == 'video')
    audio = next(stream for stream in details['streams'] if stream['codec_type'] == 'audio')
    numerator, denominator = map(int, video['avg_frame_rate'].split('/'))
    output_frame = denominator / numerator
    actual_duration = float(details['format']['duration'])
    assert CAP - output_frame <= actual_duration <= CAP + output_frame + .000001, (
        kind, actual_duration, output_frame)
    assert float(video['duration']) <= CAP + output_frame + .000001
    assert float(audio['duration']) <= CAP + output_frame + .000001
    assert (video['width'], video['height']) == (320, 180)
    assert video['codec_name'] == 'h264' and audio['codec_name'] == 'aac'
    assert result['has_audio'] and not (tmp_path / 'clip.partial.mp4').exists()
    assert abs(float(video['start_time'])) <= output_frame + .000001
    assert abs(float(audio['start_time'])) <= 1/48000

    # Compare both ends to real source frames within one source frame interval.
    # No synthetic transcript timestamps are involved in these assertions.
    output_times = frame_times(target)
    largest_source_frame = max(b-a for a, b in zip(source_times, source_times[1:]))
    for output_at in (output_times[0], output_times[-1]):
        expected_at = START + output_at
        nearby = [time for time in source_times
                  if abs(time-expected_at) <= largest_source_frame + .000001]
        actual = decoded_frame(target, output_at)
        errors = [mean_error(decoded_frame(source, time), actual) for time in nearby]
        assert min(errors) < 8, (kind, output_at, expected_at, list(zip(nearby, errors)))
    actual_first = decoded_frame(target, 0)
    assert mean_error(decoded_frame(source, 0), actual_first) > 12, kind

    # AAC is decoded, not merely detected in metadata. The chirped sine lets the
    # first/last audio windows prove source alignment as well as audible content.
    output_audio = decoded_audio(target)
    reference_audio = decoded_audio(source, START)
    assert len(output_audio) >= int((CAP-1/8000) * 8000), (kind, len(output_audio))
    assert len(reference_audio) == int(CAP*8000)
    for at in (0, int((CAP-.02)*8000)):
        actual = output_audio[at:at+160]
        reference = reference_audio[at:at+160]
        assert len(actual) == len(reference) == 160
        assert rms(actual) > 1000, (kind, at, rms(actual))
        assert correlation(actual, reference) > .95, (kind, at, correlation(actual, reference))
