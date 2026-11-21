"""Local YuNet/PySceneDetect tests; no cloud/API, speech model or network."""
import hashlib
import json
import math
from pathlib import Path

import pytest

from app import media, reframe_detection as vision


def test_capability_check_does_not_load_detector_or_start_network(monkeypatch):
    monkeypatch.setattr(vision, '_imports', lambda: pytest.fail('Readiness must not run inference.'))
    result = vision.capabilities()
    assert result['device'] == 'cpu' and result['model'] == 'YuNet 2023mar'
    assert result['available'] == result['auto_face']
    assert 'reason' in result


def test_model_is_pinned_and_loads_from_unicode_path_as_bytes():
    if not vision.capabilities()['available']:
        pytest.skip('Optional local reframing dependencies are not installed.')
    cv2, np, *_ = vision._imports()
    assert hashlib.sha256(vision.MODEL_PATH.read_bytes()).hexdigest() == vision.MODEL_SHA256
    detector = vision._face_detector(cv2, np)
    assert vision._detect_faces(detector, np.zeros((320, 320, 3), dtype=np.uint8)) == []


def face(x=.2, score=.95, track_id=1):
    return {'track_id': track_id, 'bbox': [x, .15, .16, .25], 'confidence': score}


def test_master_shot_never_converts_face_scores_into_speaker_confidence():
    samples = [{'time': t, 'faces': [face(.1), face(.7, .99, 2)]} for t in (2., 2.4, 2.8)]
    result = vision._compose_shot('shot-0001', 2, 3, samples, 1920, 1080)
    assert result['decision'] == 'ambiguous' and result['review_required']
    assert result['speaker_confidence'] is None
    assert result['focus'] == 'both'  # Stable safe fallback retains both faces.
    assert all(k['x'] == .5 and k['y'] == .5 and not k['manual'] for k in result['keyframes'])
    assert result['face_samples'][0]['faces'][1]['confidence'] == .99


def test_single_face_is_followed_with_smoothing_then_held_on_occlusion_identity_change():
    samples = [{'time': 5., 'faces': [face(.15)]},
               {'time': 5.3, 'faces': [face(.2)]},
               {'time': 5.6, 'faces': []},
               {'time': 5.9, 'faces': [face(.7, track_id=2)]}]
    result = vision._compose_shot('shot-0001', 5, 6, samples, 1920, 1080)
    keys = result['keyframes']
    assert result['decision'] == 'single_face' and result['review_required']
    assert keys[0]['x'] == pytest.approx(.23)
    assert .23 < keys[1]['x'] < .28  # Smoothing; no direct jump to detection center.
    assert keys[-1]['x'] == keys[-2]['x'] == keys[-3]['x']
    assert result['speaker_confidence'] is None


def test_tracks_are_short_local_ids_and_reacquisition_is_not_speaker_identity():
    a, tracks, next_id = vision._track_sample([{'bbox': [.1,.2,.2,.2], 'confidence': .9}], {}, 1, 0)
    b, tracks, next_id = vision._track_sample([{'bbox': [.11,.2,.2,.2], 'confidence': .91}], tracks, next_id, .3)
    c, _, _ = vision._track_sample([{'bbox': [.11,.2,.2,.2], 'confidence': .91}], tracks, next_id, 2.)
    assert a[0]['track_id'] == b[0]['track_id']
    assert c[0]['track_id'] != b[0]['track_id']


def test_invalid_intervals_or_cancel_do_not_launch_proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, 'capabilities', lambda: {'available': True})
    monkeypatch.setattr(media, 'run', lambda *_a, **_k: pytest.fail('No media process should start.'))
    source = tmp_path/'source.mp4'
    source.write_bytes(b'unchanged source')
    info = {'duration': 800, 'width':1920, 'height':1080, 'fps':30}
    for start,end in ((0,601),(-1,2),(2,1),(math.nan,3),(0,801)):
        with pytest.raises(media.MediaError):
            vision.analyze_reframe(source,info,start,end)
    with pytest.raises(media.MediaCancelled):
        vision.analyze_reframe(source,info,0,10,cancelled=lambda:True)
    assert source.read_bytes() == b'unchanged source'


@pytest.fixture
def cut_source(tmp_path):
    if not vision.capabilities()['available']:
        pytest.skip('Optional local reframing dependencies are not installed.')
    if not media.config.binary('ffmpeg'):
        pytest.skip('FFmpeg required.')
    source = tmp_path/'potongan wajah 🩰.mp4'
    media.run([media.tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-y',
               '-f','lavfi','-i','color=black:s=320x180:r=25:d=1',
               '-f','lavfi','-i','color=white:s=320x180:r=25:d=1',
               '-f','lavfi','-i','color=black:s=320x180:r=25:d=1',
               '-f','lavfi','-i','color=white:s=320x180:r=25:d=1',
               '-f','lavfi','-i','color=black:s=320x180:r=25:d=1',
               '-filter_complex','[0:v][1:v][2:v][3:v][4:v]concat=n=5:v=1:a=0[v]',
               '-map','[v]','-c:v','libx264','-preset','ultrafast','-threads','2','-pix_fmt','yuv420p',str(source)])
    return source


def test_real_proxy_scenedetect_yunet_resets_at_cuts_and_preserves_source(cut_source):
    original = hashlib.sha256(cut_source.read_bytes()).hexdigest()
    progress = []
    result = vision.analyze_reframe(cut_source, media.probe(cut_source), 1, 4,
                                    progress=lambda fraction,message: progress.append((fraction,message)))
    shots = result['shots']
    assert len(shots) == 3
    assert [s['start'] for s in shots] == pytest.approx([1,2,3],abs=.041)
    assert [s['end'] for s in shots] == pytest.approx([2,3,4],abs=.041)
    assert all(s['decision']=='no_face' and s['review_required'] and s['speaker_confidence'] is None for s in shots)
    assert all(s['keyframes'][0]['time']==s['start'] and s['keyframes'][-1]['time']==s['end'] for s in shots)
    assert all(s['start'] <= sample['time'] < s['end'] for s in shots for sample in s['face_samples'])
    assert result['analysis']['active_speaker_available'] is False
    assert progress[-1][0] == 1 and hashlib.sha256(cut_source.read_bytes()).hexdigest() == original
    assert 'https:' not in json.dumps(result)
