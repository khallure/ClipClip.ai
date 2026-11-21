"""The displayed crop must agree with the geometry used to encode the MP4."""
import copy
import json
import subprocess

import pytest

from app import config
from app.reframe_render import crop_at,pose_at


@pytest.mark.parametrize('focus', ['auto','left','right','both','locked','manual','active','override','active_manual'])
def test_typescript_preview_matches_python_render_geometry(tmp_path,focus):
    node=config.binary('node')
    if not node:
        pytest.skip('Node required for cross-language preview verification.')
    keys=[{'time':10,'x':.2,'y':.4,'zoom':1.1,'manual':False},
          {'time':12,'x':.8,'y':.7,'zoom':1.7,'manual':False}]
    shot={'id':'shot-1','start':10,'end':13,'focus':'auto' if focus=='manual' else focus,
        'keyframes':keys,'face_samples':[
            {'time':10,'faces':[{'track_id':1,'bbox':[.1,.2,.15,.2],'confidence':.9},
                                {'track_id':2,'bbox':[.7,.3,.15,.2],'confidence':.95}]},
            {'time':12,'faces':[{'track_id':1,'bbox':[.2,.22,.15,.2],'confidence':.9},
                                {'track_id':2,'bbox':[.6,.28,.15,.2],'confidence':.95}]}]}
    if focus in {'manual','active_manual'}:
        shot['keyframes'].append({'time':11.5,'x':.55,'y':.6,'zoom':2,'manual':True})
    if focus in {'active','override','active_manual'}:
        shot['focus']='active'
        shot['speaker_intervals']=[{'start':10,'end':11,'track_id':1,'focus':'auto','source':'model'},
            {'start':11,'end':12.8,'track_id':2,'focus':'auto','source':'model'},
            {'start':12.8,'end':13,'track_id':None,'focus':'both','source':'fallback'}]
    if focus=='override':
        shot['speaker_overrides']=[{'start':10.1,'end':11.5,'focus':'right'},
            {'start':11.6,'end':12.2,'focus':'both'},{'start':12.2,'end':12.8,'focus':'locked'}]
    other=copy.deepcopy(shot)
    other.update(id='shot-2',start=13,end=15,focus='auto',keyframes=[{
        'time':13,'x':.9,'y':.2,'zoom':1.2,'manual':False}],face_samples=[])
    plan={'source_width':1920,'source_height':1080,'start':10,'end':15,'smoothing':.75,'shots':[shot,other]}
    times=[10,10.2,11,11.5,12.9,12.999999,13,13.1,14.9]
    request=tmp_path/'input.json'
    request.write_text(json.dumps({'plan':plan,'times':times}),encoding='utf-8')
    script="""import {readFileSync} from 'node:fs';import {pathToFileURL} from 'node:url';
const m=await import(pathToFileURL(process.argv[1]).href);
const {plan,times}=JSON.parse(readFileSync(process.argv[2],'utf8'));
process.stdout.write(JSON.stringify(times.map(t=>({pose:m.poseAt(plan,t),crop:m.cropAt(plan,t)}))));"""
    result=subprocess.run([node,'--input-type=module','-e',script,
        str(config.PROJECT/'frontend/lib/reframe.ts'),str(request)],capture_output=True,text=True,encoding='utf-8',check=True,timeout=20)
    values=json.loads(result.stdout)
    for time,actual in zip(times,values):
        expected=crop_at(plan,time)
        assert {k:actual['crop'][k] for k in ('x','y','width','height')}=={
            k:expected[k] for k in ('x','y','width','height')}
        pose=pose_at(plan,time)
        assert actual['pose']['shot_id']==pose['shot_id']
        for name in ('x','y','zoom'):
            assert actual['pose'][name]==pytest.approx(pose[name],abs=1e-10)
