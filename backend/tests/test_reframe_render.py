"""Real FFmpeg verifies moving crops, shot resets, containment and audio timing."""
from array import array
import copy
import math
from pathlib import Path
import subprocess

import pytest

from app import config, media
from app.reframe_render import crop_at, frame_geometry, pose_at, render_filter


def key(time, x=.5, y=.5, zoom=1, manual=False):
    return dict(time=time, x=x, y=y, zoom=zoom, manual=manual)


def shot(ident, start, end, keys, focus='auto', faces=None):
    return {'id': ident, 'start': start, 'end': end, 'focus': focus,
            'keyframes': keys, 'face_samples': faces or []}


def plan(shots, **extra):
    return {'source_width': 640, 'source_height': 360, 'source_fps': 12,
            'source_duration': 3, 'smoothing': .65, 'shots': shots, **extra}


def test_manual_override_easing_and_hard_camera_cut():
    value = plan([
        shot('a', 0, 1, [key(0, .1), key(.2, .9), key(0, .25, manual=True), key(1, .75, manual=True)]),
        shot('b', 1, 3, [key(1, .1), key(3, .3)]),
    ], smoothing=1)
    assert pose_at(value, 0)['x'] == .25
    assert pose_at(value, .25)['x'] == pytest.approx(.25+.5*.15625)
    assert pose_at(value, .999)['x'] > .74
    assert pose_at(value, 1)['x'] == .1
    assert pose_at(value, 1)['shot_id'] == 'b'
    assert pose_at(value, 3.001)['x'] == .3


@pytest.mark.parametrize('dimensions', [(1920,1080),(641,361),(300,800),(2,2)])
@pytest.mark.parametrize('x,y,zoom', [(0,0,1),(1,1,3),(.5,.5,1.1),(.99,.01,2.4)])
def test_crop_always_has_even_valid_dimensions_and_bounded_source_coordinates(dimensions,x,y,zoom):
    value=plan([shot('a',0,3,[key(0,x,y,zoom)])],source_width=dimensions[0],source_height=dimensions[1])
    crop=crop_at(value,1)
    assert crop == frame_geometry(value,1)
    assert all(crop[name]%2 == 0 for name in ('x','y','width','height'))
    assert crop['x'] >= 0 and crop['y'] >= 0
    assert crop['x']+crop['width'] <= dimensions[0]
    assert crop['y']+crop['height'] <= dimensions[1]


def test_focus_selected_track_does_not_swap_to_an_occluder_and_manual_keys_win():
    faces=[
        {'time':0,'faces':[{'track_id':7,'bbox':[.1,.2,.15,.2]}, {'track_id':9,'bbox':[.7,.2,.15,.2]}]},
        {'time':1,'faces':[{'track_id':9,'bbox':[.4,.2,.15,.2]}]},
        {'time':2,'faces':[{'track_id':7,'bbox':[.2,.2,.15,.2]}, {'track_id':9,'bbox':[.7,.2,.15,.2]}]},
    ]
    value=plan([shot('a',0,3,[key(0)],'left',faces)],smoothing=0)
    assert pose_at(value,1)['x'] == pytest.approx(.225)
    value['shots'][0]['keyframes'].append(key(0,.9,.4,2,True))
    assert pose_at(value,1)['x'] == .9
    assert pose_at(value,1)['zoom'] == 2
    value['shots'][0]['focus']='locked'
    assert pose_at(value,2.5)['x'] == .9


def test_both_contains_the_entire_source_and_interval_must_be_covered(tmp_path):
    value=plan([shot('a',0,3,[key(0,.2,.8,3)],'both')])
    assert crop_at(value,1)['width'] == 640 and crop_at(value,1)['height'] == 360
    assert crop_at(value,1)['fit_mode'] == 'contain'
    with pytest.raises(ValueError):
        render_filter(value,0,4,tmp_path)
    bad=copy.deepcopy(value)
    bad['shots'][0]['keyframes'][0]['zoom']=float('nan')
    with pytest.raises(ValueError):
        render_filter(bad,0,2,tmp_path)


def _ffmpeg(*arguments, cwd=None):
    return subprocess.run([media.tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
                           *map(str,arguments)],cwd=cwd,capture_output=True,check=True,timeout=90).stdout


@pytest.fixture(scope='module')
def owned_source(tmp_path_factory):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('Real FFmpeg/FFprobe required for reframing integration.')
    directory=tmp_path_factory.mktemp('owned_reframe_render')
    target=directory/'source.mp4'
    _ffmpeg('-y','-f','lavfi','-i','testsrc2=size=640x360:rate=12:duration=3',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=3',
            '-vf','drawbox=x=0:y=0:w=320:h=130:color=red:t=fill,drawbox=x=320:y=0:w=320:h=130:color=blue:t=fill',
            '-c:v','libx264','-preset','ultrafast','-threads','2','-pix_fmt','yuv420p',
            '-c:a','aac','-shortest',target)
    return target


def _render(source,directory,value,start=0,end=3):
    directory.mkdir()
    filters=render_filter(value,start,end,directory)
    target=directory/'clip.mp4'
    _ffmpeg('-y','-ss',start,'-i',source,'-t',end-start,'-map','0:v:0','-map','0:a:0',
            '-vf',','.join(filters),'-c:v','libx264','-preset','ultrafast','-threads','2',
            '-pix_fmt','yuv420p','-c:a','aac',target,cwd=directory)
    return target


def _pixel(source,time,x=540,y=50):
    data=_ffmpeg('-ss',time,'-i',source,'-frames:v','1','-vf',f'crop=2:2:{x}:{y}',
                 '-pix_fmt','rgb24','-f','rawvideo','-')
    return tuple(data[:3])


def test_real_mixed_both_composition_and_hard_cuts_preserve_timing_and_audio(owned_source,tmp_path):
    value=plan([shot('left',0,1,[key(0,.25)]),
                shot('both',1,2,[key(1)],'both'),
                shot('right',2,3,[key(2,.75)])])
    target=_render(owned_source,tmp_path/'mixed',value)
    info=media.probe(target)
    assert (info['width'],info['height'],info['fps']) == (1080,1920,12)
    assert info['duration'] == pytest.approx(3,abs=1/12)
    assert info['has_audio'] and info['audio_codec']=='aac'
    red=_pixel(target,.5)
    black=_pixel(target,1)
    blue=_pixel(target,2)
    assert red[0]>200 and red[1]<40 and red[2]<40
    assert max(black)<20  # Both uses letterboxing rather than stretching/cropping.
    assert blue[2]>150 and blue[0]<40 and blue[1]<40
    assert _pixel(target,1.2,100,700)[0]>200
    assert _pixel(target,1.2,900,700)[2]>150


def test_real_moving_xy_zoom_and_subrange_do_not_resample_frames(owned_source,tmp_path):
    value=plan([shot('moving',0,3,[key(0,.2,.1,1,True),key(3,.8,.9,2.5,True)])])
    target=_render(owned_source,tmp_path/'moving',value,start=.5,end=2.5)
    info=media.probe(target)
    assert info['fps']==12 and info['duration']==pytest.approx(2,abs=1/12)
    assert (info['width'],info['height'])==(1080,1920)
    frames=subprocess.run([media.tool('ffprobe'),'-v','error','-count_frames','-select_streams','v:0',
                           '-show_entries','stream=nb_read_frames','-of','csv=p=0',str(target)],
                          check=True,capture_output=True,timeout=30).stdout.decode().strip()
    assert int(frames)==24
    commands=(target.parent/'reframe-commands.txt').read_text(encoding='ascii')
    assert commands.count('crop@podcut w')>10 and 'nan' not in commands.lower()
    # Real audio remains aligned with the caller's unchanged -ss/-t interval.
    def audio(path,start):
        values=array('h')
        values.frombytes(_ffmpeg('-ss',start,'-i',path,'-t',1.5,'-vn','-ac','1','-ar','8000','-f','s16le','-'))
        return values[800:10000]
    baseline=tmp_path/'baseline.mp4'
    _ffmpeg('-y','-ss',.5,'-i',owned_source,'-t',2,'-map','0:v:0','-map','0:a:0',
            '-vf','scale=1080:1920','-c:v','libx264','-preset','ultrafast','-threads','2',
            '-pix_fmt','yuv420p','-c:a','aac',baseline)
    a,b=audio(baseline,0),audio(target,0)
    correlation=sum(x*y for x,y in zip(a,b))/math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
    assert correlation>.99999


def test_fractional_inpoint_preserves_original_video_and_audio_stream_timestamps(owned_source,tmp_path):
    value=plan([shot('moving',0,3,[key(0,.2,.2,1),key(3,.8,.8,2)])])
    target=_render(owned_source,tmp_path/'fractional',value,start=.53,end=2.53)
    baseline=tmp_path/'fractional-baseline.mp4'
    _ffmpeg('-y','-ss',.53,'-i',owned_source,'-t',2,'-map','0:v:0','-map','0:a:0',
            '-vf','scale=1080:1920','-c:v','libx264','-preset','ultrafast','-threads','2',
            '-pix_fmt','yuv420p','-c:a','aac',baseline)
    def timeline(path):
        import json
        return json.loads(subprocess.run([media.tool('ffprobe'),'-v','error','-show_entries',
            'stream=index,start_time,duration,avg_frame_rate','-of','json',str(path)],
            check=True,capture_output=True,timeout=30).stdout)['streams']
    assert timeline(target)==timeline(baseline)


def test_rapid_shot_changes_never_pan_between_compositions(owned_source,tmp_path):
    shots=[shot(str(i),i/3,(i+1)/3,[key(i/3,.25 if i%2==0 else .75)]) for i in range(9)]
    target=_render(owned_source,tmp_path/'rapid',plan(shots,smoothing=1))
    for index in range(9):
        rgb=_pixel(target,index/3)
        assert (rgb[0]>200 and rgb[2]<40) if index%2==0 else (rgb[2]>150 and rgb[0]<40)
