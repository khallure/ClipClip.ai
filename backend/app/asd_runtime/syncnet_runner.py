"""Research-only official SyncNet verifier, consuming existing M1 tracks.

The code is MIT. Official Oxford weights have separate research/CC Attribution
terms; they are intentionally not bundled. Confidence is an embedding-distance
margin, never a calibrated probability. No face detector or cloud service runs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from .. import config

import cv2
import numpy as np
import python_speech_features
from scipy.io import wavfile
from scipy.ndimage import median_filter
import torch
from torch.nn import functional as F

from .runner import FPS, _command, _tracks, dense_track, progress
from .syncnet_model import S

CHECKPOINT_SHA256 = "961e8696f888fce4f3f3a6c3d5b3267cf5b343100b238e79b2659bff2c605442"
VERSION = "syncnet-m2-research-1"


def crop_bgr(image, composition):
    cx, cy, size = composition
    pad = max(1, int(math.ceil(size * 1.8)))
    frame = np.pad(image, ((pad, pad), (pad, pad), (0, 0)), "constant", constant_values=110)
    cx, cy = cx + pad, cy + pad
    face = frame[int(cy-size):int(cy+size*1.8), int(cx-size*1.4):int(cx+size*1.4)]
    return cv2.resize(face, (224,224)) if face.size else np.full((224,224,3),110,np.uint8)


def prepare(manifest, work):
    source = Path(manifest['source']).resolve()
    stat = source.stat()
    fields = {k:manifest.get(k) for k in ('start','end','source_width','face_samples','tracks','max_track_gap','min_face_confidence')}
    fields.update(version=VERSION,source=str(source),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
    key = hashlib.sha256(json.dumps(fields,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    cache = Path(manifest['cache_dir']).resolve()/f'syncnet-prepared-{key}.npz' if manifest.get('cache_dir') else None
    if cache and cache.is_file():
        progress('syncnet_prepared_cache_hit')
        with np.load(cache,allow_pickle=False) as saved:
            tracks={int(k):saved[f'face_{k}'].copy() for k in saved['ids']}
            masks={int(k):saved[f'mask_{k}'].copy() for k in saved['ids']}
            audio,times=saved['audio'].copy(),saved['times'].copy()
        return tracks,masks,audio,times,True
    start,end=float(manifest['start']),float(manifest['end'])
    if not 0 <= start < end or end-start>600:
        raise ValueError('Selected shot must be 0–600 seconds')
    ffmpeg=str(manifest['ffmpeg']);video=work/'video.mp4';wave=work/'audio.wav'
    width=min(1920,int(manifest.get('source_width',1920)));width-=width%2
    progress('syncnet_prepare_media',start=start,end=end)
    _command([ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(start),'-i',str(source),'-t',str(end-start),'-map','0:v:0','-an','-vf',f'fps=25,scale={width}:-2','-c:v','libx264','-preset','ultrafast','-crf','16','-threads','4',str(video)])
    _command([ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(start),'-i',str(source),'-t',str(end-start),'-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wave)])
    sr,audio=wavfile.read(wave)
    cap=cv2.VideoCapture(str(video));count=min(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),len(audio)//640)
    width,height=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    times=start+np.arange(count)/25
    faces,masks,centers={},{},{}
    for tid,rows in _tracks(manifest).items():
        boxes,mask=dense_track(rows,times,max_gap=min(.5,float(manifest.get('max_track_gap',.5))),min_confidence=float(manifest.get('min_face_confidence',.75)))
        x,y,w,h=boxes.T*np.array([[width],[height],[width],[height]])
        center=np.column_stack((x+w/2,y+h/2,np.maximum(w,h)/2))
        edges=np.flatnonzero(np.diff(np.r_[False,mask,False]))
        for a,b in zip(edges[::2],edges[1::2]):
            center[a:b]=median_filter(center[a:b],size=(min(13,((b-a-1)//2)*2+1),1),mode='nearest')
        faces[tid]=np.full((count,224,224,3),110,np.uint8);masks[tid]=mask;centers[tid]=center
    for i in range(count):
        ok,image=cap.read()
        if not ok:
            for mask in masks.values():mask[i:]=False
            break
        for tid in faces:
            if masks[tid][i]:faces[tid][i]=crop_bgr(image,centers[tid][i])
    cap.release()
    if cache:
        cache.parent.mkdir(parents=True,exist_ok=True)
        payload={'ids':np.array(list(faces)),'audio':audio,'times':times}
        payload.update({f'face_{k}':v for k,v in faces.items()});payload.update({f'mask_{k}':v for k,v in masks.items()})
        temporary=cache.with_name(f'{cache.name}.{os.getpid()}.tmp')
        with temporary.open('wb') as handle:np.savez_compressed(handle,**payload)
        os.replace(temporary,cache)
    return faces,masks,audio,times,False


def distance_metrics(visual,audio,valid,vshift=15):
    """Official padded pairwise offset distances, restricted to observed windows."""
    padding=F.pad(audio,(0,0,vshift,vshift))
    distances=torch.stack([F.pairwise_distance(visual[[i]].repeat(vshift*2+1,1),padding[i:i+vshift*2+1]) for i in range(len(visual))])
    mask=torch.from_numpy(valid)
    if not mask.any():return {'valid_windows':0,'offset_frames':None,'confidence':None}
    means=distances[mask].mean(0)
    minval,minidx=means.min(0)
    median=means.median()
    frames=vshift-int(minidx)
    windows=[]
    for i in range(0,len(visual),5):
        first,last=max(0,i-25),min(len(visual),i+26)
        observed=mask[first:last]
        if int(observed.sum())<15 or float(observed.float().mean())<.8:
            windows.append({'index':i,'valid':False,'confidence':None,'offset_frames':None})
            continue
        local=distances[first:last][observed].mean(0)
        best,where=local.min(0)
        windows.append({'index':i,'valid':True,'confidence':round(float(local.median()-best),6),
                        'offset_frames':vshift-int(where),'min_distance':round(float(best),6)})
    return {'valid_windows':int(mask.sum()),'offset_frames':frames,'offset_seconds':frames/25,'min_distance':round(float(minval),6),'confidence':round(float(median-minval),6),'zero_offset_distance':round(float(means[vshift]),6),'zero_offset_margin':round(float(median-means[vshift]),6),'windows':windows,'distance_by_offset':[{'offset_frames':vshift-i,'distance':round(float(value),6)} for i,value in enumerate(means)],'window_zero_offset_distances':[round(float(x),6) if valid[i] else None for i,x in enumerate(distances[:,vshift])],'window_best_offset_confidence':[round(float(median-x),6) if valid[i] else None for i,x in enumerate(distances[:,int(minidx)])]}


def infer(manifest):
    config.validate_model_manifest(manifest)
    began=time.perf_counter();torch.set_num_threads(int(manifest.get('cpu_threads',4)));torch.set_num_interop_threads(1)
    path=Path(manifest['syncnet_weights']);sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if sha!=CHECKPOINT_SHA256:raise ValueError('Unrecognized Oxford SyncNet checkpoint checksum')
    device=manifest.get('device','cpu')
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
    network=S().eval();network.load_state_dict(torch.load(path,map_location='cpu',weights_only=True),strict=True);network.to(device)
    loaded=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='podcut_syncnet_') as directory:
        faces,masks,wave,times,cache_hit=prepare(manifest,Path(directory));prepared=time.perf_counter()
        count=len(times)-5
        if count<10:raise ValueError('SyncNet needs more than 0.6 seconds of valid video')
        batch=int(manifest.get('syncnet_batch_size',10))
        experiments={'aligned':wave,'shifted_0.2':np.roll(wave,3200),'shifted_2.0':np.roll(wave,32000),'silence':np.zeros_like(wave)} if manifest.get('syncnet_ablations',False) else {'aligned':wave}
        with torch.inference_mode():
            audio_embeddings={}
            for name,audio in experiments.items():
                mfcc=python_speech_features.mfcc(audio,16000).T.astype(np.float32)
                outputs=[]
                for i in range(0,count,batch):
                    data=np.stack([mfcc[:,j*4:j*4+20] for j in range(i,min(count,i+batch))])[:,None]
                    outputs.append(network.forward_aud(torch.from_numpy(data).to(device)).cpu())
                audio_embeddings[name]=torch.cat(outputs)
            audio_done=time.perf_counter();result=[]
            for tid,frames in sorted(faces.items()):
                progress('syncnet_visual_inference',track_id=tid)
                outputs=[]
                for i in range(0,count,batch):
                    data=np.stack([frames[j:j+5] for j in range(i,min(count,i+batch))]).transpose(0,4,1,2,3).astype(np.float32)
                    outputs.append(network.forward_lip(torch.from_numpy(data).to(device)).cpu())
                visual=torch.cat(outputs)
                valid=np.array([masks[tid][i:i+5].all() for i in range(count)])
                result.append({'track_id':tid,'valid_fraction':round(float(valid.mean()),6),'experiments':{name:distance_metrics(visual,audio,valid,int(manifest.get('syncnet_vshift',15))) for name,audio in audio_embeddings.items()}})
        if device=='cuda':torch.cuda.synchronize()
        finished=time.perf_counter()
    return {'version':VERSION,'model':'SyncNet-v2','checkpoint_sha256':sha,'code_license':'MIT','weights_terms':'Oxford: research purposes under Creative Commons Attribution','weights_terms_url':'https://www.robots.ox.ac.uk/~vgg/software/lipsync/','confidence_kind':'median_offset_embedding_distance_minus_minimum','confidence_is_probability':False,'start':manifest['start'],'end':manifest['end'],'fps':25,'window_start_times':np.round(times[:count],6).tolist(),'tracks':result,'runtime':{'device':device,'torch_version':torch.__version__,'cuda_available':torch.cuda.is_available(),'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'parameters':sum(p.numel() for p in network.parameters()),'load_seconds':round(loaded-began,4),'preprocessing_seconds':round(prepared-loaded,4),'audio_embedding_seconds':round(audio_done-prepared,4),'visual_and_metrics_seconds':round(finished-audio_done,4),'total_seconds':round(finished-began,4),'prepared_cache_hit':cache_hit}}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('manifest',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    try:
        config.RUNTIME.validate_writable(args.manifest, 'SyncNet request')
        if args.output:config.RUNTIME.validate_writable(args.output, 'SyncNet output')
        result=infer(json.loads(args.manifest.read_text(encoding='utf-8-sig')))
        serialized=json.dumps(result,allow_nan=False)
        if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(serialized,encoding='utf-8');print(json.dumps({'event':'complete','runtime':result['runtime']}),flush=True)
        else:print(serialized,flush=True)
        return 0
    except Exception as error:
        import sys
        print(json.dumps({'event':'error','type':type(error).__name__,'message':str(error)}),file=sys.stderr,flush=True);return 1


if __name__=='__main__':raise SystemExit(main())
