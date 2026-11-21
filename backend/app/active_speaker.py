"""Optional isolated local AV models. V1 never imports Torch or repeats ASR here."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from . import config, media
from .active_speaker_policy import decide_speaker

VERSION = 'light-asd-syncnet-m2-2'
EVIDENCE_GATE_VERSION = 'av-consensus-2'
LIGHT_SHA = 'efc375833887eefa9d209dc92810e18519b04c3c73ea35a549f2a7f40b7d94d5'
SYNC_SHA = '961e8696f888fce4f3f3a6c3d5b3267cf5b343100b238e79b2659bff2c605442'
LIGHT = config.MODELS_DIR/'active_speaker/light-asd-talkset.model'
SYNC = config.MODELS_DIR/'active_speaker/syncnet_v2.model'
_health = None
_health_time = 0
_health_lock = threading.Lock()


def _env():
    return config.child_environment()


def _interpreter(device):
    name = '.venv-asd-gpu' if device == 'cuda' else '.venv-asd'
    return config.RUNTIME.validate_project(config.PROJECT/name/('Scripts/python.exe' if os.name == 'nt' else 'bin/python'), 'ASD interpreter')


def capabilities(force_refresh=False):
    global _health, _health_time
    if config.RUNTIME.packaged:
        return {'available': False, 'active_speaker': False, 'experimental': True, 'device': 'cpu',
                'model': 'Light-ASD + SyncNet', 'score_is_calibrated': False,
                'reason': 'Model active speaker memerlukan helper CPU/GPU terpisah; helper tersebut belum dipaketkan pada Phase 2B.'}
    with _health_lock:
        if _health is not None and not force_refresh and time.monotonic()-_health_time < 60:
            return dict(_health)
        result = {'available':False,'active_speaker':False,'experimental':True,'device':'cpu',
                  'model':'Light-ASD + SyncNet','score_is_calibrated':False,
                  'reason':'Jalankan scripts/Setup-ActiveSpeaker.ps1 untuk model lokal opsional.'}
        if all(config.RUNTIME.validate_writable(p, 'ASD model').is_file() for p in (LIGHT,SYNC)) and config.binary('ffmpeg'):
            if hashlib.sha256(LIGHT.read_bytes()).hexdigest() != LIGHT_SHA or hashlib.sha256(SYNC.read_bytes()).hexdigest() != SYNC_SHA:
                result['reason'] = 'Checksum model active speaker tidak cocok. Pasang ulang model resmi.'
            else:
                requested = os.getenv('ASD_DEVICE','auto').lower()
                devices = ['cpu'] if requested == 'cpu' else ['cuda','cpu']
                for device in devices:
                    python = _interpreter(device)
                    if not python.is_file():
                        continue
                    try:
                        probe = subprocess.run([str(python),'-c',
                            'import torch,cv2,numpy,scipy,python_speech_features; '
                            'import json; print(json.dumps({"cuda":torch.cuda.is_available()}))'],
                            cwd=config.BACKEND,env=_env(),capture_output=True,text=True,timeout=20,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        ready = probe.returncode == 0 and (device != 'cuda' or json.loads(probe.stdout)['cuda'])
                    except (OSError,ValueError,subprocess.TimeoutExpired):
                        ready = False
                    if ready:
                        result.update(available=True,active_speaker=True,device=device,reason=None)
                        break
        _health, _health_time = result, time.monotonic()
        return dict(result)


def _stop(proc):
    if proc.poll() is None:
        if os.name == 'nt':
            try:
                subprocess.run([str(Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/taskkill.exe'),'/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=15,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except (OSError,subprocess.TimeoutExpired):
                proc.kill()
            if proc.poll() is None:proc.kill()
        else:
            proc.kill()
    proc.wait(timeout=20)


def _read(path):
    if path.stat().st_size > 32*1024*1024:
        raise ValueError('Hasil model melampaui batas metadata.')
    return json.loads(path.read_text(encoding='utf-8'),parse_constant=lambda v:(_ for _ in ()).throw(ValueError('Nonfinite model output')))


def _run(module, request, cancelled, progress):
    if cancelled and cancelled():
        raise media.MediaCancelled('Analisis speaker dibatalkan.')
    with tempfile.TemporaryDirectory(prefix='podcut_asd_') as tmp:
        root=Path(tmp); inp=root/'input.json'; out=root/'output.json'
        inp.write_text(json.dumps(request,allow_nan=False),encoding='utf-8')
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen([str(_interpreter(request['device'])),'-m',module,str(inp),'--output',str(out)],
                cwd=config.BACKEND,env=_env(),stdout=log,stderr=log,stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            began=time.monotonic()
            try:
                while proc.poll() is None:
                    if cancelled and cancelled():
                        raise media.MediaCancelled('Analisis speaker dibatalkan; hasil lama dipertahankan.')
                    if time.monotonic()-began > 900:
                        raise media.MediaError('Model speaker melewati batas waktu. Pilih klip lebih pendek atau gunakan GPU lokal.')
                    time.sleep(.1)
                if proc.returncode or not out.is_file():
                    # Do not expose child stderr, private paths, or inherited credentials.
                    raise media.MediaError('Model audio-visual lokal gagal. Coba CPU (ASD_DEVICE=cpu), klip lebih pendek, atau framing manual.')
                return _read(out)
            finally:
                _stop(proc)


def _identity(request):
    fields={k:request[k] for k in ('source_signature','start','end','face_samples')}
    fields.update(version=VERSION,light=LIGHT_SHA,sync=SYNC_SHA)
    return hashlib.sha256(json.dumps(fields,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def infer_shot(source, source_info, shot, cache_directory, *, source_signature, cancelled=None, progress=None):
    status=capabilities()
    if not status['available']:
        raise media.MediaError(status['reason'])
    source=Path(source).resolve()
    if not source.is_file() or source.suffix.lower() not in {'.mp4','.mkv','.webm','.mov','.m4v','.avi'}:
        raise media.MediaError('Model speaker memerlukan file video lokal yang sudah divalidasi.')
    request={'source':str(source),'ffmpeg':media.tool('ffmpeg'),'weights':str(LIGHT),
             'syncnet_weights':str(SYNC),'start':shot['start'],'end':shot['end'],
             'source_width':source_info['width'],'face_samples':shot['face_samples'],
             'source_signature':source_signature,'device':status['device'],'cpu_threads':4,
             'syncnet_ablations':False}
    if not 0 < shot['end']-shot['start'] <= 24.01:
        raise ValueError('Inference speaker memerlukan chunk maksimal 24 detik.')
    directory=config.RUNTIME.validate_writable(cache_directory, 'Speaker cache directory')
    if directory.is_symlink() or bool(getattr(directory,'is_junction',lambda:False)()):
        raise ValueError('Folder cache speaker tidak valid.')
    directory.mkdir(parents=True,exist_ok=True)
    key=_identity(request); cached=directory/f'{key}.json'
    if cached.is_file() and not cached.is_symlink():
        try:
            result=_read(cached)
            if result.get('key') == key and result.get('version') == VERSION and 'tracks' in result['light_asd'] and 'tracks' in result['syncnet']:
                return {**result,'cache_hit':True}
        except (ValueError,KeyError,TypeError):
            pass
    request['cache_dir']=str(directory/f'features-{key}')
    if progress: progress(0,'Menyiapkan audio/wajah dari face tracks yang tersimpan.')
    def run_models():
        light=_run('app.asd_runtime.runner',request,cancelled,progress)
        if progress: progress(.45,'Memeriksa sinkronisasi audio dengan setiap wajah secara lokal.')
        sync=_run('app.asd_runtime.syncnet_runner',request,cancelled,progress)
        return light,sync
    try:
        light,sync=run_models()
    except media.MediaCancelled:
        raise
    except media.MediaError:
        if request['device'] != 'cuda' or not _interpreter('cpu').is_file():
            raise
        request['device']='cpu'
        if progress: progress(0,'GPU belum berhasil; mencoba model lokal pada CPU.')
        light,sync=run_models()
    result={'version':VERSION,'key':key,'light_asd':light,'syncnet':sync,'cache_hit':False}
    temporary=cached.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result,allow_nan=False),encoding='utf-8');os.replace(temporary,cached)
    return result


def evidence_events(raw, shot):
    """Gate actual ASD scores with measured local SyncNet offset/margin.

    Thresholds are explicit engineering rules, NOT a calibrated speaker accuracy.
    RMS rejects silence only. SyncNet supplies the joint speech synchrony evidence.
    """
    light,sync=raw['light_asd'],raw['syncnet']
    sync_tracks={t['track_id']:t['experiments'].get('aligned',{}) for t in sync['tracks']}
    events=[]
    for i in range(0,max((len(t['frames']) for t in light['tracks']),default=0),5):
        at=shot['start']+i/25
        if at >= shot['end']:break
        rows=[]
        competing_unverified=False
        verified_tracks=0
        for track in light['tracks']:
            if i >= len(track['frames']):continue
            frame=track['frames'][i]
            windows=sync_tracks.get(track['track_id'],{}).get('windows',[])
            window=min(windows,key=lambda w:abs(w['index']-i),default=None)
            verified=window and abs(window['index']-i)<=5 and window['valid'] and window['confidence']>=3 and abs(window['offset_frames'])<=2
            if frame['valid']:
                if verified:
                    verified_tracks+=1
                    rows.append({'track_id':track['track_id'],'confidence':frame['speaking_score'],
                                 'score':frame['raw_positive_logit']})
                elif frame['speaking_score']>=.75:
                    # Discarding a second high ASD candidate used to manufacture
                    # certainty about the remaining face. Treat disagreement as
                    # unusable evidence; it does not prove the other face speaks.
                    competing_unverified=True
        rms=light.get('media',{}).get('audio_rms_dbfs',[])
        # Short syllabic gaps must not reset the one-second acquisition timer.
        volume=max(rms[max(0,i-5):i+6],default=None)
        ambiguity=competing_unverified or verified_tracks>1
        events.append({'time':round(at,6),'scores':rows,'valid':bool(rows) and not ambiguity,
                       'model_disagreement':competing_unverified,'multiple_synchronized_tracks':verified_tracks>1,
                       'audio_present':volume is not None and math.isfinite(volume) and volume > -45})
    return events


def analyze_active_speaker(source, source_info, plan, directory, *, cancelled=None, progress=None):
    result=deepcopy(plan);stats=[]
    shots=result['shots']
    for n,shot in enumerate(shots):
        if cancelled and cancelled():raise media.MediaCancelled('Analisis speaker dibatalkan.')
        if not any(len(s['faces'])>=2 for s in shot['face_samples']):
            continue  # M1 close-up composition remains intact.
        events=[];runs=[]
        chunks=math.ceil((shot['end']-shot['start'])/20) if source_info.get('has_audio',True) else 0
        if not chunks:
            events=[{'time':shot['start'],'scores':[],'valid':False,'audio_present':False}]
        for offset in range(chunks):
            first=shot['start']+offset*20;last=min(shot['end'],first+20)
            chunk={**shot,'start':max(shot['start'],first-1.2),'end':min(shot['end'],last+1.2)}
            chunk['face_samples']=[s for s in shot['face_samples'] if chunk['start']<=s['time']<=chunk['end']]
            def update(value,message):
                if progress:progress((n+(offset+value)/max(1,math.ceil((shot['end']-shot['start'])/20)))/len(shots),f'Shot {n+1}/{len(shots)} · {message}')
            if chunk['end']-chunk['start'] < .8:
                continue
            raw=infer_shot(source,source_info,chunk,Path(directory)/'reframes/asd-cache',
                           source_signature=plan['source_signature'],cancelled=cancelled,progress=update)
            events.extend(e for e in evidence_events(raw,chunk) if first<=e['time']<last)
            runs.append({'start':chunk['start'],'end':chunk['end'],'cache_hit':raw['cache_hit'],
                         'light_runtime':raw['light_asd']['runtime'],'sync_runtime':raw['syncnet']['runtime']})
        policy=decide_speaker(events,shot['start'],shot['end'],max_gap=.4)
        shot.update(focus='active',decision='active_speaker',speaker_intervals=policy['intervals'],
                    speaker_confidence=min((i['confidence'] for i in policy['intervals'] if i['source']=='model'),default=None),
                    review_required=policy['review_required'],reason='Model AV lokal + pemeriksaan sinkronisasi; bagian ambigu memakai framing stabil/Focus Both.')
        stats.append({'shot_id':shot['id'],'runs':runs,'selected_seconds':policy['selected_duration'],
                      'abstained_seconds':policy['abstained_duration'],
                      'ambiguous_events':sum(e.get('model_disagreement',False) or e.get('multiple_synchronized_tracks',False) for e in events)})
    result['mode']='active_speaker'
    result['analysis']={**result.get('analysis',{}),'active_speaker_available':True,'active_speaker':{'version':VERSION,'evidence_gate_version':EVIDENCE_GATE_VERSION,'model':'Light-ASD + SyncNet',
        'score_is_calibrated':False,'sync_margin_min':3,'sync_offset_max_frames':2,'audio_silence_dbfs':-45,
        'minimum_hold_seconds':1,'shots':stats}}
    warning='Active Speaker bersifat eksperimental. Skor ASD belum terkalibrasi; margin SyncNet bukan probabilitas. Periksa shot bertanda review.'
    result['warnings']=list(dict.fromkeys([*result.get('warnings',[]),warning]))
    return result
