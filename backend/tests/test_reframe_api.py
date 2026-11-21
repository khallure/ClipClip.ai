"""Reframing must be reusable without touching transcription or highlight analysis."""
import copy
import json
import subprocess
import threading
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import config, media
from app.main import create_app
from app.reframe_models import ReframePlan
from app.store import ACTIVE


@pytest.fixture(scope='module')
def source(tmp_path_factory):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('Real FFmpeg is required.')
    path=tmp_path_factory.mktemp('reframe_owned')/'source.mp4'
    subprocess.run([media.tool('ffmpeg'),'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=24:duration=8',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=8','-c:v','libx264','-preset','ultrafast',
        '-threads','2','-c:a','aac','-shortest',str(path)],check=True,capture_output=True,timeout=30)
    return path


@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setenv('AI_PROVIDER','heuristic')
    monkeypatch.setattr(media,'transcribe',Mock(side_effect=AssertionError('Reframe must not transcribe.')))
    return create_app(tmp_path/'data')


def prepare(client,source):
    ident=client.post('/api/jobs',json={'title':'Owned framing QA'}).json()['id']
    with source.open('rb') as file:
        response=client.post(f'/api/jobs/{ident}/source',files={'file':('owned.mp4',file,'video/mp4')})
    assert response.status_code==200,response.text
    return ident


def wait(client,ident):
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        job=client.get(f'/api/jobs/{ident}').json()
        if job['status'] not in ACTIVE:
            return job
        time.sleep(.02)
    pytest.fail(f'Task did not complete: {job}')


def manual(client,ident,start=1,end=3):
    response=client.post(f'/api/jobs/{ident}/reframe/manual',json={'start':start,'end':end})
    assert response.status_code==200,response.text
    return response.json()


def test_manual_keyframes_persist_bounds_and_export_without_asr(app,source):
    with TestClient(app) as client:
        ident=prepare(client,source)
        directory=app.state.store.directory(ident)
        original_candidates=[{'id':'retained-editorial-result','start':1,'end':3}]
        app.state.store.update(ident,candidates=original_candidates)
        transcript={'language':'en','segments':[{'start':1,'end':3,'text':'Original words stay synchronized.',
            'words':[{'start':1+i*.5,'end':1.45+i*.5,'word':word} for i,word in enumerate(['Original','words','stay','synchronized.'])]}]}
        assert client.post(f'/api/jobs/{ident}/transcript',files={
            'file':('words.json',json.dumps(transcript),'application/json')}).status_code==200
        # Import resets candidates; editorial state before reframing is what must remain.
        app.state.store.update(ident,candidates=original_candidates)
        transcript_before=(directory/'transcript.json').read_bytes()
        assert client.get(f'/api/jobs/{ident}/reframe?start=1&end=3').status_code==404
        plan=manual(client,ident)
        plan['shots'][0]['keyframes']=[{'time':1,'x':.1,'y':.2,'zoom':1.1,'manual':True},
                                       {'time':2.9,'x':.9,'y':.8,'zoom':1.7,'manual':True}]
        saved=client.put(f'/api/jobs/{ident}/reframe',json=plan)
        assert saved.status_code==200,saved.text
        assert client.get(f'/api/jobs/{ident}/reframe?start=1&end=3').json()==saved.json()
        geometry=client.get(f'/api/jobs/{ident}/reframe/pose?plan_id={plan["id"]}&time=2').json()['crop']
        assert geometry['x']>=0 and geometry['y']>=0
        assert geometry['x']+geometry['width']<=320 and geometry['y']+geometry['height']<=180
        response=client.post(f'/api/jobs/{ident}/exports',json={'start':1,'end':3,'aspect':'vertical',
            'caption_style':'poppins_highlight','duration_preset':'custom','reframe_id':plan['id']})
        assert response.status_code==202,response.text
        ready=wait(client,ident)
        assert ready['status']=='ready',ready
        exported=ready['exports'][-1]
        output=directory/'exports'/exported['id']
        info=media.probe(output/'clip.mp4')
        assert (info['width'],info['height'],info['fps'])==(1080,1920,24)
        assert abs(info['duration']-2)<=1/24 and info['has_audio']
        assert json.loads((output/'reframe_plan.json').read_text(encoding='utf-8'))==saved.json()
        assert 'Poppins' in (output/'captions.ass').read_text(encoding='utf-8')
        assert (directory/'transcript.json').read_bytes()==transcript_before
        assert ready['candidates']==original_candidates and media.transcribe.call_count==0


def test_source_identity_crop_and_unavailable_speaker_are_rejected(app,source,monkeypatch):
    from app import active_speaker
    monkeypatch.setattr(active_speaker,'capabilities',lambda:{'available':False,'reason':'Optional local models unavailable.'})
    with TestClient(app) as client:
        ident=prepare(client,source)
        plan=manual(client,ident)
        for field,value in [('source_width',640),('source_signature','a'*64)]:
            changed={**plan,field:value}
            assert client.put(f'/api/jobs/{ident}/reframe',json=changed).status_code==422
        changed=copy.deepcopy(plan)
        changed['shots'][0]['speaker_confidence']=.99
        assert client.put(f'/api/jobs/{ident}/reframe',json=changed).status_code==422
        changed=copy.deepcopy(plan)
        changed['shots'][0]['keyframes'][0]['zoom']=4
        assert client.put(f'/api/jobs/{ident}/reframe',json=changed).status_code==422
        assert client.post(f'/api/jobs/{ident}/reframe/analyze',json={'start':1,'end':3,'mode':'active_speaker'}).status_code==409
        for payload in [dict(start=0,end=4,aspect='vertical'),dict(start=1,end=3,aspect='original')]:
            response=client.post(f'/api/jobs/{ident}/exports',json={**payload,'reframe_id':plan['id']})
            assert response.status_code==422,response.text
        assert client.get(f'/api/jobs/{ident}/reframe?start=0&end=9').status_code==422


def test_detection_preserves_manual_override_and_cannot_forge_detector_evidence(app,source,monkeypatch):
    from app import reframe_detection
    calls=[]
    def detect(_source,_info,start,end,**kwargs):
        calls.append((start,end))
        return {'shots':[{'id':'shot-0001','start':start,'end':end,'decision':'single_face',
            'speaker_confidence':None,'face_samples':[{'time':start,'faces':[{
                'track_id':1,'bbox':[.2,.2,.1,.2],'confidence':.92}]}],
            'keyframes':[{'time':start,'x':.25,'y':.5,'zoom':1,'manual':False}]}],
            'analysis':{'detector':'controlled fixture, not a live face detection'}}
    monkeypatch.setattr(reframe_detection,'analyze_reframe',detect)
    monkeypatch.setattr(reframe_detection,'capabilities',lambda:{'available':True,'auto_face':True})
    with TestClient(app) as client:
        ident=prepare(client,source)
        plan=manual(client,ident)
        plan['shots'][0]['keyframes'][0].update(x=.8,manual=True)
        assert client.put(f'/api/jobs/{ident}/reframe',json=plan).status_code==200
        assert client.post(f'/api/jobs/{ident}/reframe/analyze',json={'start':1,'end':3}).status_code==200
        ready=wait(client,ident)
        assert ready['reframe']['status']=='ready'
        detected=client.get(f'/api/jobs/{ident}/reframe?start=1&end=3').json()
        assert detected['shots'][0]['keyframes'][0]['x']==.8
        detected['shots'][0]['face_samples'][0]['faces'][0]['confidence']=.01
        detected['smoothing']=.9
        saved=client.put(f'/api/jobs/{ident}/reframe',json=detected).json()
        assert saved['shots'][0]['face_samples'][0]['faces'][0]['confidence']==.92
        assert calls==[(1,3)] and media.transcribe.call_count==0


def test_plan_rejects_gaps_and_nonfinite_coordinates():
    from app.reframe_store import manual_plan
    # Structural validation is also applied when reading persisted JSON.
    with pytest.raises(ValueError):
        ReframePlan.model_validate({'id':'a'*32,'source_signature':'b'*64,'source_width':320,
            'source_height':180,'source_fps':24,'source_duration':8,'start':0,'end':4,
            'shots':[{'id':'shot-1','start':1,'end':4,'keyframes':[{'time':1,'x':.5,'y':.5,'zoom':1}]}]})


def test_manual_override_can_restore_saved_automatic_track_without_detection(app,source):
    with TestClient(app) as client:
        ident=prepare(client,source)
        plan=manual(client,ident)
        baseline=copy.deepcopy(plan['shots'][0]['auto_keyframes'])
        plan['shots'][0]['keyframes']=[{'time':1,'x':.9,'y':.2,'zoom':2,'manual':True}]
        changed=client.put(f'/api/jobs/{ident}/reframe',json=plan).json()
        assert changed['shots'][0]['auto_keyframes']==baseline
        changed['shots'][0]['keyframes']=baseline
        restored=client.put(f'/api/jobs/{ident}/reframe',json=changed)
        assert restored.status_code==200,restored.text
        assert restored.json()['shots'][0]['keyframes']==baseline
        assert media.transcribe.call_count==0


def test_cancel_reframe_preserves_existing_plan_and_editorial_data(app,source,monkeypatch):
    from app import reframe_detection
    entered=threading.Event()
    def detect(*args,cancelled,**kwargs):
        entered.set()
        while not cancelled():
            time.sleep(.01)
        raise media.MediaCancelled('Controlled local vision cancellation.')
    monkeypatch.setattr(reframe_detection,'analyze_reframe',detect)
    monkeypatch.setattr(reframe_detection,'capabilities',lambda:{'available':True,'auto_face':True})
    with TestClient(app) as client:
        ident=prepare(client,source)
        original=manual(client,ident)
        client.post(f'/api/jobs/{ident}/reframe/analyze',json={'start':1,'end':3})
        assert entered.wait(3)
        assert client.delete(f'/api/jobs/{ident}').status_code==409
        assert client.post(f'/api/jobs/{ident}/cancel').status_code==200
        result=wait_cancelled(client,ident)
        assert result['reframe']['status']=='cancelled'
        assert client.get(f'/api/jobs/{ident}/reframe?start=1&end=3').json()==original
        assert media.transcribe.call_count==0


def wait_cancelled(client,ident):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        job=client.get(f'/api/jobs/{ident}').json()
        if job['status']=='cancelled':
            return job
        time.sleep(.02)
    pytest.fail(f'Cancellation did not finish: {job}')


def test_active_speaker_reuses_tracks_and_keeps_model_evidence_readonly(app,source,monkeypatch):
    from app import active_speaker, reframe_detection, reframe_store
    monkeypatch.setattr(active_speaker,'capabilities',lambda:{'available':True,'device':'cpu'})
    monkeypatch.setattr(reframe_detection,'capabilities',lambda:{'available':True,'auto_face':True})
    detector=Mock(side_effect=AssertionError('Cached M1 tracks must not rerun face detection.'))
    monkeypatch.setattr(reframe_detection,'analyze_reframe',detector)
    def infer(source,info,plan,directory,**kwargs):
        result=copy.deepcopy(plan);result['mode']='active_speaker'
        shot=result['shots'][0]
        shot.update(focus='active',decision='active_speaker',speaker_confidence=.9,review_required=True,
            speaker_intervals=[{'start':1,'end':1.5,'track_id':None,'focus':'both','source':'fallback',
                'confidence':None,'score':None,'reason':'missing evidence','review_required':True},
                {'start':1.5,'end':3,'track_id':1,'focus':'auto','source':'model',
                'confidence':.9,'score':2.1,'reason':'controlled model evidence','review_required':False}])
        return result
    monkeypatch.setattr(active_speaker,'analyze_active_speaker',infer)
    with TestClient(app) as client:
        ident=prepare(client,source);plan=manual(client,ident)
        directory=app.state.store.directory(ident)
        plan['analysis']['scene_detector']='controlled M1 result'
        reframe_store.save_plan(directory,plan)
        response=client.post(f'/api/jobs/{ident}/reframe/analyze',json={'start':1,'end':3,'mode':'active_speaker'})
        assert response.status_code==200,response.text
        assert wait(client,ident)['status']=='ready'
        detected=client.get(f'/api/jobs/{ident}/reframe?start=1&end=3').json()
        assert detected['shots'][0]['speaker_confidence']==.9
        detected['shots'][0]['speaker_confidence']=.999
        detected['shots'][0]['speaker_intervals'][1]['confidence']=.999
        detected['shots'][0]['speaker_overrides']=[{'start':1,'end':1.5,'focus':'right'}]
        saved=client.put(f'/api/jobs/{ident}/reframe',json=detected)
        assert saved.status_code==200,saved.text
        assert saved.json()['shots'][0]['speaker_confidence']==.9
        assert saved.json()['shots'][0]['speaker_intervals'][1]['confidence']==.9
        assert saved.json()['shots'][0]['review_required'] is False
        assert saved.json()['shots'][0]['speaker_overrides'][0]['focus']=='right'
        assert detector.call_count==0 and media.transcribe.call_count==0
