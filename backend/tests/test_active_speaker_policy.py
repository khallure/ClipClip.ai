"""Synthetic policy fixtures, not an accuracy evaluation of any AV model."""
import math

import pytest

from app.active_speaker_policy import decide_speaker


def sample(time, first=.95, second=.1, **flags):
    return {'time':time,'scores':[{'track_id':1,'score':2.5,'confidence':first,'face_confidence':.95},
                                 {'track_id':2,'score':-1.,'confidence':second,'face_confidence':.95}],**flags}


def at(result,time):
    return next(item for item in result['intervals'] if item['start']<=time<item['end'])


def test_acquisition_needs_repeated_model_evidence_and_keeps_actual_confidence():
    result=decide_speaker([sample(10),sample(10.5,.93),sample(11,.91),sample(11.5,.89)],10,12)
    assert at(result,10.9)['track_id'] is None
    chosen=at(result,11.1)
    assert chosen['track_id']==1 and chosen['source']=='model'
    assert chosen['confidence']==.89  # Compact interval minimum of real inputs.
    assert chosen['score']==2.5 and chosen['review_required'] is False


def test_one_frame_challenger_does_not_switch_and_true_switch_is_delayed():
    values=[sample(0),sample(.5),sample(1),sample(1.5,.1,.96),sample(1.7),
            sample(2,.1,.95),sample(2.5,.1,.94),sample(3,.1,.92),sample(3.5,.1,.91)]
    result=decide_speaker(values,0,4)
    assert at(result,1.6)['track_id']==1
    assert at(result,1.6)['confidence'] is None and at(result,1.6)['review_required']
    assert at(result,1.8)['track_id']==1 and at(result,1.8)['source']=='model'
    assert at(result,2.8)['track_id']==1
    assert at(result,3.2)['track_id']==2 and at(result,3.2)['source']=='model'


def test_hysteresis_retains_current_track_below_acquisition_threshold():
    values=[sample(0),sample(.5),sample(1),sample(1.5,.68,.1),sample(2,.67,.1)]
    result=decide_speaker(values,0,2.5)
    assert at(result,1.6)['track_id']==1 and at(result,1.6)['confidence']==.67
    # The same weak score cannot select a fresh track after a camera cut.
    fresh=decide_speaker([sample(10,.68),sample(10.5,.68),sample(11,.68)],10,12)
    assert not any(item['source']=='model' for item in fresh['intervals'])


def test_overlapping_high_scores_hold_briefly_then_focus_both_without_confidence():
    values=[sample(0),sample(.5),sample(1),sample(1.5,.94,.9),sample(2,.92,.91),sample(2.5,.95,.9)]
    result=decide_speaker(values,0,3)
    assert at(result,1.8)['track_id']==1 and at(result,1.8)['focus']=='locked'
    assert at(result,2.6)['track_id'] is None and at(result,2.6)['focus']=='both'
    assert at(result,2.6)['confidence'] is None and at(result,2.6)['review_required']


@pytest.mark.parametrize('flags,reason',[({'audio_present':False},'audio_unavailable'),
                                        ({'valid':False},'invalid_av_evidence')])
def test_unusable_av_evidence_never_uses_high_model_scores(flags,reason):
    result=decide_speaker([sample(0,**flags),sample(.5,**flags),sample(1,**flags)],0,2)
    assert all(item['source']=='fallback' and item['confidence'] is None for item in result['intervals'])
    assert at(result,.5)['reason']==reason


def test_low_face_confidence_abstains_instead_of_selecting_other_face():
    values=[sample(0),sample(.5),sample(1)]
    for event in values:
        event['scores'][0]['face_confidence']=.2
    result=decide_speaker(values,0,2)
    assert at(result,1.2)['track_id'] is None
    assert at(result,1.2)['reason']=='low_face_confidence'


def test_sparse_model_events_expire_instead_of_claiming_confidence_through_gap():
    result=decide_speaker([sample(0),sample(.5),sample(1),sample(4)],0,5)
    assert at(result,1.2)['source']=='model'
    assert at(result,2.2)['source']=='fallback' and at(result,2.2)['confidence'] is None
    assert at(result,3.2)['track_id'] is None
    assert at(result,4.2)['source']=='fallback'


def test_manual_override_is_highest_priority_with_no_model_confidence():
    values=[sample(0),sample(.5),sample(1),sample(1.5),sample(2),sample(2.5)]
    result=decide_speaker(values,0,3,overrides=[{'start':1.2,'end':1.8,'track_id':2},
        {'start':2,'end':2.4,'track_id':None,'focus':'both'}])
    forced=at(result,1.5)
    assert forced['track_id']==2 and forced['source']=='manual'
    assert forced['confidence'] is None and forced['score'] is None
    assert forced['review_required'] is False
    assert at(result,1.9)['track_id']==1 and at(result,1.9)['source']=='model'
    assert at(result,2.2)['focus']=='both' and at(result,2.2)['source']=='manual'


def test_empty_evidence_still_covers_entire_shot_and_allows_manual_correction():
    result=decide_speaker([],3,7,overrides=[{'start':4,'end':6,'track_id':2}])
    assert [(i['start'],i['end']) for i in result['intervals']]==[(3,4),(4,6),(6,7)]
    assert at(result,5)['source']=='manual' and result['selected_duration']==0
    assert result['manual_duration']==2 and result['abstained_duration']==2


def test_reset_per_call_does_not_pan_identity_across_hard_cut():
    left=decide_speaker([sample(0),sample(.5),sample(1)],0,1.5)
    right=decide_speaker([sample(1.5,.1,.95),sample(2,.1,.95)],1.5,2.5)
    assert at(left,1.1)['track_id']==1
    assert at(right,1.6)['track_id'] is None


@pytest.mark.parametrize('events',[
    [sample(math.nan)], [sample(0),sample(0)], [sample(2),sample(1)],
    [{'time':0,'scores':[{'track_id':1,'score':.9}]}],
    [{'time':0,'scores':[{'track_id':True,'score':1,'confidence':.9}]}],
    [{'time':0,'scores':[{'track_id':1,'score':1,'confidence':1.1}]}],
    [{'time':0,'scores':[{'track_id':1,'score':math.inf,'confidence':.9}]}],
])
def test_invalid_model_evidence_is_rejected(events):
    with pytest.raises(ValueError):
        decide_speaker(events,0,3)


def test_overlapping_manual_intervals_are_not_silently_prioritized():
    with pytest.raises(ValueError,match='overlap'):
        decide_speaker([],0,3,overrides=[{'start':0,'end':2,'track_id':1},{'start':1,'end':3,'track_id':2}])


def test_confirmations_use_real_samples_not_synthetic_hold_deadline():
    # A single event can describe a window, but no repeated actual observation
    # has confirmed a persistent winner. Never create a confident selection.
    result=decide_speaker([sample(0)],0,3,min_hold=.5,max_gap=2)
    assert not any(item['source']=='model' for item in result['intervals'])
