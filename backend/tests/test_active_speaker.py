"""AV evidence, cache isolation, manual priority and V1 regression protections."""
import copy
import json

import pytest

from app import active_speaker as asd
from app.reframe_models import ReframePlan
from app.reframe_render import pose_at, crop_at
from app.reframe_store import manual_plan, preserve_manual


def sample_raw(margin=5,offset=2,volume=-20):
    return {'light_asd':{'tracks':[{'track_id':1,'frames':[{'valid':True,'speaking_score':.98,
        'raw_positive_logit':3} for _ in range(30)]}], 'media':{'audio_rms_dbfs':[volume]*30}},
        'syncnet':{'tracks':[{'track_id':1,'experiments':{'aligned':{'windows':[
            {'index':i,'valid':True,'confidence':margin,'offset_frames':offset} for i in range(0,30,5)]}}}]}}


@pytest.mark.parametrize('margin,offset,volume,valid,audio',[(5,2,-20,True,True),(1,2,-20,False,True),
    (5,15,-20,False,True),(.1,-9,-120,False,False)])
def test_visual_only_high_asd_is_rejected_without_av_sync(margin,offset,volume,valid,audio):
    events=asd.evidence_events(sample_raw(margin,offset,volume),{'start':10,'end':11.2})
    assert all(e['valid'] is valid and e['audio_present'] is audio for e in events)
    if valid:
        assert events[0]['scores'][0]['confidence']==.98  # genuine model score; no fabricated fusion probability


@pytest.mark.parametrize('second_asd,second_sync,valid',[(.9,1,False),(.2,1,True),(.2,5,False)])
def test_unverified_competing_candidate_does_not_create_false_unique_speaker(second_asd,second_sync,valid):
    raw=sample_raw()
    other=copy.deepcopy(raw['light_asd']['tracks'][0]);other['track_id']=2
    for frame in other['frames']:frame['speaking_score']=second_asd
    raw['light_asd']['tracks'].append(other)
    other_sync=copy.deepcopy(raw['syncnet']['tracks'][0]);other_sync['track_id']=2
    for window in other_sync['experiments']['aligned']['windows']:window['confidence']=second_sync
    raw['syncnet']['tracks'].append(other_sync)
    events=asd.evidence_events(raw,{'start':10,'end':11.2})
    assert all(e['valid'] is valid for e in events)
    if not valid:
        from app.active_speaker_policy import decide_speaker
        result=decide_speaker(events,10,11.2,max_gap=.4)
        assert all(i['source']=='fallback' and i['confidence'] is None for i in result['intervals'])


def test_child_environment_excludes_credentials_and_proxy(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','private-value')
    monkeypatch.setenv('HTTP_PROXY','private-proxy')
    monkeypatch.setenv('HF_TOKEN','private-token')
    assert not {'GEMINI_API_KEY','HTTP_PROXY','HF_TOKEN'} & asd._env().keys()


def test_result_cache_reuses_models_and_invalidates_track_edits(tmp_path,monkeypatch):
    source=tmp_path/'source.mp4';source.write_bytes(b'validated-by-ingestion')
    shot={'start':1.,'end':3.,'face_samples':[{'time':1.,'faces':[]}]}
    monkeypatch.setattr(asd,'capabilities',lambda:{'available':True,'device':'cpu'})
    monkeypatch.setattr(asd.media,'tool',lambda name:'ffmpeg.exe')
    calls=[]
    def infer(module,request,*args):
        calls.append(module)
        return {'tracks':[],'runtime':{}}
    monkeypatch.setattr(asd,'_run',infer)
    args=(source,{'width':1920},shot,tmp_path/'cache')
    first=asd.infer_shot(*args,source_signature='a'*64)
    second=asd.infer_shot(*args,source_signature='a'*64)
    assert not first['cache_hit'] and second['cache_hit'] and len(calls)==2
    changed=copy.deepcopy(shot);changed['face_samples'][0]['time']=1.1
    asd.infer_shot(source,{'width':1920},changed,tmp_path/'cache',source_signature='a'*64)
    assert len(calls)==4


def test_manual_interval_wins_over_manual_keys_and_model_then_releases(tmp_path):
    source=tmp_path/'s.mp4';source.write_bytes(b'x')
    plan=manual_plan(source,{'width':1920,'height':1080,'duration':20,'fps':25},0,10)
    shot=plan['shots'][0]
    shot.update(focus='active',decision='active_speaker',keyframes=[{'time':0,'x':.5,'y':.5,'zoom':1,'manual':True}],
        speaker_intervals=[{'start':0,'end':10,'track_id':1,'focus':'auto','confidence':.98,
                            'score':3,'source':'model','reason':'measured','review_required':False}],
        speaker_overrides=[{'start':3,'end':5,'focus':'right'}])
    ReframePlan.model_validate(plan)
    assert pose_at(plan,2)['x']==.5
    assert pose_at(plan,4)['x']==.75
    assert pose_at(plan,6)['x']==.5
    old=copy.deepcopy(plan);fresh=copy.deepcopy(plan);fresh['shots'][0]['speaker_overrides']=[]
    assert preserve_manual(old,fresh)['shots'][0]['speaker_overrides']==old['shots'][0]['speaker_overrides']
    shot['speaker_overrides'].append({'start':4,'end':6,'focus':'left'})
    with pytest.raises(ValueError):ReframePlan.model_validate(plan)


def test_model_cut_resets_without_cross_camera_pan():
    def shot(start,end,track,x):
        return {'id':f'shot-{track}','start':start,'end':end,'focus':'active',
            'keyframes':[{'time':start,'x':.5,'y':.5,'zoom':1,'manual':False}],
            'face_samples':[{'time':start,'faces':[{'track_id':track,'bbox':[x,.2,.1,.2]}]},
                            {'time':end-.01,'faces':[{'track_id':track,'bbox':[x,.2,.1,.2]}]}],
            'speaker_intervals':[{'start':start,'end':end,'focus':'auto','track_id':track,'source':'model'}]}
    plan={'source_width':1920,'source_height':1080,'smoothing':1,'shots':[shot(0,1,1,.1),shot(1,2,2,.8)]}
    assert pose_at(plan,.999)['x']==pytest.approx(.15)
    assert pose_at(plan,1)['x']==pytest.approx(.85)
    for at in [.999,1]:
        box=crop_at(plan,at)
        assert 0<=box['x']<=1920-box['width'] and 0<=box['y']<=1080-box['height']


def test_missing_audio_abstains_without_running_models(tmp_path,monkeypatch):
    source=tmp_path/'source.mp4';source.write_bytes(b'x')
    info={'width':1920,'height':1080,'duration':10,'fps':25,'has_audio':False}
    plan=manual_plan(source,info,0,5)
    plan['shots'][0]['face_samples']=[{'time':0,'faces':[{'track_id':i,'bbox':[x,.2,.1,.2],'confidence':.9} for i,x in [(1,.1),(2,.7)]]}]
    def unexpected(*args,**kwargs):raise AssertionError('Audio-free source must not run audiovisual model.')
    monkeypatch.setattr(asd,'infer_shot',unexpected)
    result=asd.analyze_active_speaker(source,info,plan,tmp_path)
    shot=result['shots'][0]
    assert shot['review_required'] and shot['speaker_confidence'] is None
    assert all(i['source']=='fallback' and i['confidence'] is None for i in shot['speaker_intervals'])
    assert pose_at(result,2)['focus']=='both'


def test_cancelled_request_stops_before_starting_model():
    with pytest.raises(asd.media.MediaCancelled):
        asd._run('app.asd_runtime.runner',{},lambda:True,None)
