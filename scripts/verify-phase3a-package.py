"""Actual relocated-package QA with a fresh native-selected UUID profile.

This is a lab-only test driver, not shipped application code. Its interpreter
controls bundled services only. Cleared PATH and an empty profile on this host
are deliberately NOT reported as a clean Windows / another-user test.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
import zipfile

_spec = importlib.util.spec_from_file_location('podcut_phase2b_qa_helpers', Path(__file__).with_name('verify-phase2b-package.py'))
_old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_old)
PROJECT, PROFILE = _old.PROJECT, _old.PROFILE
checked, sha, state, inventory = _old.checked, _old.sha, _old.state, _old.inventory
process_identity, service_descendants = _old.process_identity, _old.service_descendants
VerificationFailure = _old.VerificationFailure
FRONTEND, BACKEND = _old.FRONTEND, _old.BACKEND
MODEL_REVISION = '536b0662742c02347bc0e980a01041f333bce120'
MODEL_BYTES = 486212372


def windows_directory() -> Path:
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    api.GetWindowsDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    count = api.GetWindowsDirectoryW(buffer, len(buffer))
    if not 0 < count < len(buffer):
        raise VerificationFailure('The native Windows directory could not be observed.')
    return Path(buffer.value)


def minimal_environment(windows: Path, package: Path) -> dict:
    # No inherited secrets, Python/Node configuration or development tool PATH.
    return {'SystemRoot': str(windows), 'WINDIR': str(windows), 'OS': 'Windows_NT',
            'COMSPEC': str(windows/'System32/cmd.exe'), 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
            'PATH': os.pathsep.join(map(str, (package/'backend', package/'node', package/'tools', windows/'System32', windows))),
            'TEMP': str(PROFILE/'temp'), 'TMP': str(PROFILE/'temp'),
            'USERPROFILE': str(PROFILE), 'LOCALAPPDATA': str(PROFILE/'cache/bootstrap-env-spoof'),
            'APPDATA': str(PROFILE/'config/bootstrap-env-spoof'), 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}


class JobHost(_old.JobHost):
    def __init__(self, package: Path, profile: Path, instance: str, run_id: str, environment: dict, output: Path):
        self.package, self.run_id = package, run_id
        self.events, self.inbox = [], queue.Queue()
        self.stderr = checked(output/'job-host.stderr.log', PROFILE).open('wb')
        self.process = subprocess.Popen([str(package/'backend/PODCUTBackend.exe'), '--job-host',
                                        '--package-root', str(package), '--run-id', run_id,
                                        '--profile-root', str(profile), '--isolated-profile', instance],
                                       cwd=output, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=self.stderr,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.forced, self.closed_event = False, None
        threading.Thread(target=self._read, daemon=True, name='phase3a-QA-host-events').start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root', required=True, help='Actual payload resources/podcut inside an approved DEV copy.')
    parser.add_argument('--isolated-profile', required=True, help='New canonical UUID; native bootstrap chooses its location.')
    parser.add_argument('--offline-model-zip', required=True, help='Independently prepared model ZIP under the canonical DEV profile.')
    youtube = parser.add_mutually_exclusive_group()
    youtube.add_argument('--youtube-followup', type=Path, help='Successful test report that owns this existing UUID profile.')
    youtube.add_argument('--youtube-cold', action='store_true', help='Prove one-link ingestion in a never-used profile with an empty media cache.')
    arguments = parser.parse_args()
    if str(uuid.UUID(arguments.isolated_profile)) != arguments.isolated_profile:
        raise VerificationFailure('The isolated profile must be a canonical UUID.')
    checked(Path(__file__).absolute(), PROJECT)
    checked(Path(sys.executable).absolute(), PROJECT)
    package = checked(Path(arguments.package_root), PROJECT)
    app_root = checked(package.parent.parent, PROJECT)
    if package.name != 'podcut' or package.parent.name != 'resources':
        raise VerificationFailure('The QA payload must retain its resources/podcut layout.')
    executable = checked(package/'backend/PODCUTBackend.exe', package)
    for relative in ('backend/PODCUTBackend.exe', 'frontend/server.js', 'node/node.exe',
                     'tools/ffmpeg.exe', 'tools/ffprobe.exe', 'podcut-package.json'):
        if not checked(package/relative, package).is_file():
            raise VerificationFailure('A required relocated package resource is missing.')
    archive = checked(Path(arguments.offline_model_zip), PROFILE)
    fixture = checked(PROFILE/'temp/phase2b-fixtures/owned-face-speech.mp4', PROFILE)
    reference_path = checked(fixture.parent/'speech-reference.txt', PROFILE)
    if not archive.is_file() or not fixture.is_file() or not reference_path.is_file():
        raise VerificationFailure('An independently prepared offline model or media fixture is missing.')
    if archive.stat().st_size <= 10*1024*1024:
        raise VerificationFailure('The real offline ZIP must exceed 10 MiB to test the frontend upload path.')
    profile = checked(PROFILE/'profiles'/arguments.isolated_profile, PROFILE)
    if arguments.youtube_followup:
        previous = json.loads(checked(arguments.youtube_followup, PROFILE/'runtime/phase3a').read_text(encoding='utf8'))
        if previous.get('status') != 'passed' or Path(previous['profile']) != profile or Path(previous['package']) != package:
            raise VerificationFailure('Follow-up test must use the same proved QA profile and package.')
    elif profile.exists():
        raise VerificationFailure('This test requires a never-used isolated UUID profile; nothing was deleted.')
    _old.free_dev_ports()
    package_before = inventory(app_root)
    windows = windows_directory()
    environment = minimal_environment(windows, package)
    bootstrap = subprocess.run([str(executable), '--bootstrap-info', '--isolated-profile', arguments.isolated_profile],
                               cwd=app_root, env=environment, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if bootstrap.returncode != 0:
        raise VerificationFailure('The actual frozen native bootstrap failed before any profile was created.')
    try:
        boot = json.loads(bootstrap.stdout)
    except ValueError:
        raise VerificationFailure('The native bootstrap did not return one valid JSON record.') from None
    if (boot.get('schema') != 1 or boot.get('build') != 'phase3a' or boot.get('profile_id') != 'installer-dev'
            or Path(boot.get('package_root', '')) != package or Path(boot.get('project_root', '')) != package
            or Path(boot.get('profile_root', '')) != profile or boot.get('profile_instance') != arguments.isolated_profile
            or Path(boot.get('windows_root', '')) != windows or (not arguments.youtube_followup and profile.exists())):
        raise VerificationFailure('Native bootstrap identity/readonly behavior disagrees with the approved fresh-profile boundary.')

    import httpx
    run = str(uuid.uuid4())
    output = checked(PROFILE/'runtime/phase3a'/('package-' + run), PROFILE)
    output.mkdir(parents=True, exist_ok=False)
    report_path = output/'report.json'
    report = {'schema': 'podcut-phase3a-relocated-QA-v1', 'run_id': run, 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(), 'package': str(package), 'profile': str(profile),
              'evidence': str(output), 'bootstrap': boot, 'checks': [], 'requests': [], 'exports': {}, 'sessions': [],
              'clean_windows': 'NOT_VERIFIED', 'different_windows_account': 'NOT_VERIFIED',
              'environment_notice': 'Actual packaged services on the development Windows host with minimal PATH and a fresh native UUID profile; not a clean Windows VM.',
              'fixture_notice': 'Independent NASA public-domain still-face composition and Windows TTS, not a natural podcast or active-speaker benchmark.'}
    sessions, secrets_in_memory = [], []
    failure, stage = None, 'native first-run bootstrap'

    def redact(value):
        if isinstance(value, str):
            for secret in secrets_in_memory:
                value = value.replace(secret, '[REDACTED]')
            return value
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    def write_json(path, value):
        checked(path, PROFILE).write_text(json.dumps(redact(value), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def save():
        temporary = report_path.with_suffix('.json.tmp')
        write_json(temporary, report)
        temporary.replace(report_path)

    def check(name, condition, **details):
        report['checks'].append({'name': name, 'passed': bool(condition), **details})
        save()
        if not condition:
            raise VerificationFailure(name)
        print('PASS ' + name, flush=True)

    def request(client, method, path, *, expected=200, **kwargs):
        if not path.startswith('/') or path.startswith('//'):
            raise VerificationFailure('A QA HTTP request tried to leave the fixed local origin.')
        response = client.request(method, path, **kwargs)
        report['requests'].append({'method': method, 'path': path, 'status': response.status_code})
        if any(secret.encode() in response.content for secret in secrets_in_memory):
            raise VerificationFailure('An authentication credential leaked into an HTTP response.')
        if response.status_code != expected:
            raise VerificationFailure(f'Unexpected HTTP {response.status_code} for {method} {path}')
        return response

    def prove_http_ready(host, port, token, *, frontend):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if host.process.poll() is not None:
                raise VerificationFailure('The frozen supervisor exited before readiness.')
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
            try:
                connection.connect()
                client_port = connection.sock.getsockname()[1]
                nonce = uuid.uuid4().hex
                host.send({'op': 'status', 'nonce': nonce, 'connection': {'port': port, 'client_port': client_port}})
                proof = host.wait('status', timeout=10)
                peer = proof.get('connection', {})
                if proof.get('nonce') != nonce or peer.get('owned') is not True:
                    raise VerificationFailure('Native ownership proof failed before an HTTP credential was sent.')
                headers = {'Host': f'127.0.0.1:{port}', 'x-podcut-ui-token' if frontend else 'x-podcut-run-token': token}
                if frontend:
                    headers['Origin'] = FRONTEND
                connection.request('GET', '/api/health', headers=headers)
                response = connection.getresponse()
                body = response.read(1024*1024+1)
                if response.status == 200 and len(body) <= 1024*1024:
                    return json.loads(body)
                if response.status in (301,302,303,307,308,401,403):
                    raise VerificationFailure('The proven service refused its credential or redirected readiness.')
            except (ConnectionRefusedError, ConnectionResetError, TimeoutError, http.client.RemoteDisconnected):
                pass
            finally:
                connection.close()
            time.sleep(.2)
        raise VerificationFailure('The proven packaged service did not become ready.')

    def start_session(label):
        _old.free_dev_ports()
        session_dir = checked(output/label, PROFILE)
        session_dir.mkdir()
        run_id = str(uuid.uuid4())
        backend_token, ui_token = secrets.token_hex(32), secrets.token_hex(32)
        secrets_in_memory.extend((backend_token, ui_token))
        host = JobHost(package, profile, arguments.isolated_profile, run_id, environment, session_dir)
        value = {'host': host, 'children': {}, 'closed': False, 'clients': [], 'run_id': run_id, 'label': label,
                 'backend_token': backend_token, 'ui_token': ui_token}
        sessions.append(value)
        ready = host.wait('ready')
        check(label + ' native Job Object host is ready', ready.get('protocol') == 'podcut-job-host-v1' and ready.get('run_id') == run_id)
        for role in ('backend', 'frontend'):
            executable = package/('backend/PODCUTBackend.exe' if role == 'backend' else 'node/node.exe')
            args = ['--serve','--run-id',run_id,'--isolated-profile',arguments.isolated_profile] if role == 'backend' else [str(package/'frontend/server.js')]
            cwd = profile/'temp'/('desktop-'+run_id)/'backend' if role == 'backend' else package/'frontend'
            credentials = {'PODCUT_RUN_ID': run_id, 'PODCUT_RUN_TOKEN': backend_token}
            if role == 'frontend':
                credentials['PODCUT_UI_TOKEN'] = ui_token
            host.send({'op':'start','id':role,'executable':str(executable),'args':args,'cwd':str(cwd),'env':credentials})
            event = host.wait('started', role=role)
            identity = process_identity(event['pid'])
            check(label + ' ' + role + ' uses its bundled executable', identity is not None and Path(identity['executable']) == executable)
            value['children'][role] = identity
            value['health'] = prove_http_ready(host, 8300 if role == 'backend' else 3300,
                                              backend_token if role == 'backend' else ui_token, frontend=role == 'frontend')
        identity = value['health'].get('instance', {})
        check(label + ' frontend reaches the exact relocated frozen profile', identity.get('packaged') is True
              and identity.get('build') == 'phase3a' and identity.get('run_id') == run_id
              and Path(identity.get('packaged_root', '')) == package and Path(identity.get('profile_root', '')) == profile)
        client = httpx.Client(base_url=FRONTEND, headers={'x-podcut-ui-token':ui_token,'Origin':FRONTEND},
                              trust_env=False, follow_redirects=False, timeout=httpx.Timeout(180, connect=4))
        backend = httpx.Client(base_url=BACKEND, headers={'x-podcut-run-token':backend_token},
                               trust_env=False, follow_redirects=False, timeout=30)
        value['client'], value['backend'] = client, backend
        value['clients'] = [client, backend]
        report['sessions'].append({'label':label,'run_id':run_id,'children':value['children'],'health':value['health']})
        return value

    def close_session(value):
        if value['closed']:
            return
        for client in value['clients']:
            client.close()
        closed = value['host'].close()
        value['closed'] = True
        report.setdefault('shutdowns', []).append({'label':value['label'], **closed})
        gone = all(process_identity(row['pid']) != row for row in value['children'].values())
        _old.free_dev_ports()
        check(value['label'] + ' clean shutdown releases owned children and ports', gone and closed['exit_code'] == 0
              and not closed['forced_host_termination'] and closed.get('closed_event',{}).get('clean') is True
              and closed.get('closed_event',{}).get('active_processes') == 0)

    def wait_model(client, *, accept_failed=False):
        deadline, samples = time.monotonic()+180, []
        while time.monotonic() < deadline:
            value = request(client,'GET','/api/models/whisper').json()
            samples.append(value)
            if value.get('state') == 'ready' or accept_failed and value.get('state') == 'failed':
                return value, samples
            if value.get('state') in {'failed','cancelled'}:
                report['model_failure'] = value
                raise VerificationFailure('Actual model provisioning entered ' + value['state'])
            time.sleep(.1)
        raise VerificationFailure('Actual model setup did not complete within its bounded timeout.')

    def wait_job(client, ident):
        deadline, states = time.monotonic()+600, []
        while time.monotonic() < deadline:
            value = request(client,'GET',f'/api/jobs/{ident}').json()
            states.append(value['status'])
            if value['status'] == 'ready':
                return value, list(dict.fromkeys(states))
            if value['status'] in {'failed','blocked','cancelled'}:
                report['failed_job'] = value
                raise VerificationFailure('Actual processing entered ' + value['status'])
            time.sleep(.2)
        raise VerificationFailure('Actual processing did not finish within its bounded timeout.')

    def tool(name, args, *, timeout=120):
        result = subprocess.run([str(checked(package/'tools'/(name+'.exe'),package)), *map(str,args)],
                                cwd=output, env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            checked(output/(name+'-failure.txt'),PROFILE).write_bytes(result.stderr[-20000:])
            raise VerificationFailure('A bundled verification media tool failed: '+name)
        return result.stdout

    def export(client, ident, label, payload, dimensions):
        value=request(client,'POST',f'/api/jobs/{ident}/exports',expected=202,json=payload).json()
        export_id=value['exports'][-1]['id']
        ready, states=wait_job(client,ident)
        row=next(item for item in ready['exports'] if item['id']==export_id)
        response=request(client,'GET',f'/api/jobs/{ident}/exports/{export_id}/file')
        file=checked(output/(label+'.mp4'),PROFILE)
        file.write_bytes(response.content)
        raw=json.loads(tool('ffprobe',['-v','error','-show_format','-show_streams','-of','json',file]))
        video=next(item for item in raw['streams'] if item['codec_type']=='video')
        audio=next(item for item in raw['streams'] if item['codec_type']=='audio')
        metadata={'width':int(video['width']),'height':int(video['height']),'duration':float(raw['format']['duration']),
                  'video_codec':video['codec_name'],'audio_codec':audio['codec_name'],
                  'video_start':float(video.get('start_time',0)),'audio_start':float(audio.get('start_time',0)),
                  'video_duration':float(video.get('duration',raw['format']['duration'])),
                  'audio_duration':float(audio.get('duration',raw['format']['duration']))}
        check(label+' produces verified H264/AAC MP4 at requested size and duration',row['status']=='ready'
              and (metadata['width'],metadata['height'])==dimensions and metadata['video_codec']=='h264'
              and metadata['audio_codec']=='aac' and abs(metadata['duration']-(payload['end']-payload['start']))<=.12,media=metadata)
        check(label+' audio and video clocks are aligned',abs(metadata['video_start']-metadata['audio_start'])<=.05
              and abs(metadata['video_duration']-metadata['audio_duration'])<=.15)
        part=request(client,'GET',f'/api/jobs/{ident}/exports/{export_id}/file',expected=206,headers={'Range':'bytes=0-127'})
        check(label+' authenticated range playback and stored export agree',part.content==response.content[:128]
              and sha(file)==sha(checked(profile/'data'/ident/'exports'/export_id/'clip.mp4',profile)))
        if payload.get('caption_style')=='poppins_highlight':
            for extension in ('ass','srt','vtt'):
                captions=request(client,'GET',f'/api/jobs/{ident}/exports/{export_id}/captions.{extension}')
                checked(output/(label+'.'+extension),PROFILE).write_bytes(captions.content)
            ass=(output/(label+'.ass')).read_text(encoding='utf-8')
            check(label+' renders Poppins white/lime active-word captions','Poppins SemiBold' in ass and r'\c&H5CF3D7&' in ass)
        report['exports'][label]={'project_id':ident,'export_id':export_id,'file':str(file),'media':metadata,'states':states}
        return file

    class FollowupComplete(Exception):
        pass

    try:
        write_json(output/'package-before.json',package_before)
        if arguments.youtube_followup or arguments.youtube_cold:
            stage='live YouTube to MP4'
            session=start_session('youtube')
            client=session['client']
            if arguments.youtube_cold:
                check('new profile starts with no projects', request(client,'GET','/api/jobs').json()==[])
                with archive.open('rb') as stream:
                    request(client,'POST','/api/models/whisper/offline',files={'file':(archive.name,stream,'application/zip')})
                model,_=wait_model(client)
                check('new profile independently verifies the offline Whisper model',model['state']=='ready' and model['verified_bytes']==MODEL_BYTES)
            source_url='https://www.youtube.com/watch?v=X9t-u87df3o'
            yt=request(client,'POST','/api/jobs',expected=201,json={'title':'Highlights of Calculus introduction — Gilbert Strang, MIT OpenCourseWare','youtube_url':source_url}).json()
            path=f"/api/jobs/{yt['id']}"
            # Let V1 find a complete passage in this lecture using its normal Auto preset.
            request(client,'POST',path+'/ingest',json={'duration_preset':'auto','clip_count':3,'auto_export':True,'output':{'aspect':'vertical','caption_style':'poppins_highlight'}})
            states=[]
            until=time.monotonic()+600
            while time.monotonic()<until:
                yt=request(client,'GET',path).json()
                states.append(yt['status'])
                if (yt['status'] in ('failed','blocked','cancelled') or
                    yt['status']=='ready' and (yt.get('auto_export') or {}).get('status') in ('ready','failed','skipped')):
                    break
                time.sleep(.35)
            report['youtube']={'source':source_url,'preset':'auto','attribution':'Gilbert Strang, MIT OpenCourseWare, Highlights of Calculus, Spring 2010, Introduction. Excerpt cropped and captioned for private QA.','license':'https://creativecommons.org/licenses/by-nc-sa/4.0/',
                               'auto_export':yt.get('auto_export'),'media':yt.get('media'),'states':list(dict.fromkeys(states)),
                               'candidates':len(yt.get('candidates',[])),'status':yt['status'],'source_error':yt.get('source_error'),'message':yt.get('message'),
                               'source_cache_hit':yt.get('source_cache_hit'),'cold_profile':arguments.youtube_cold}
            save()
            check('YouTube source downloaded and supplied audio and video',bool(yt.get('media') and yt['media']['has_audio']))
            if arguments.youtube_cold:
                check('adapter fetched the source without a local cache hit',yt.get('source_cache_hit') is False)
            check('public YouTube source produced a heuristic highlight',yt.get('analysis_mode')=='heuristic' and bool(yt.get('candidates')))
            finished=[value for value in yt.get('exports',[]) if value['status']=='ready']
            check('automatic export reached ready without manual upload',bool(finished))
            url=path+'/exports/'+finished[-1]['id']+'/file'
            clip=output/'mit-ocw-introduction-clip.mp4'
            clip.write_bytes(request(client,'GET',url).content)
            meta=json.loads(tool('ffprobe',['-v','error','-show_format','-show_streams','-of','json',clip]))
            check('URL result is a playable vertical H264/AAC MP4 within the Auto preset limit',
                  any(s.get('codec_name')=='h264' and s.get('width')==1080 and s.get('height')==1920 for s in meta['streams']) and
                  any(s.get('codec_name')=='aac' for s in meta['streams']) and 0<float(meta['format']['duration'])<=90.12)
            tool('ffmpeg',['-v','error','-i',clip,'-f','null','-'])
            report['youtube'].update(clip=str(clip),duration=float(meta['format']['duration']),bytes=clip.stat().st_size)
            close_session(session)
            raise FollowupComplete()
        check('native bootstrap is readonly and ignores spoofed LocalAppData environment',not profile.exists()
              and Path(boot['profile_root'])==profile and PROFILE/'cache/bootstrap-env-spoof' not in profile.parents)
        first=start_session('first-run')
        client=first['client']
        stage='fresh profile and private setup API'
        config_path=checked(profile/'config/backend.env',profile)
        config_bytes=config_path.read_bytes()
        check('native service creates an independent default config without API keys',config_path.is_file()
              and b'AI_PROVIDER=heuristic' in config_bytes and b'GEMINI_API_KEY=\n' in config_bytes.replace(b'\r\n',b'\n')
              and b'OPENAI_API_KEY=\n' in config_bytes.replace(b'\r\n',b'\n'))
        fresh=request(client,'GET','/api/models/whisper').json()
        check('fresh profile reports model missing with truthful size and location',fresh.get('state')=='missing'
              and fresh.get('model')=='small' and fresh.get('revision')==MODEL_REVISION and fresh.get('total_bytes')==MODEL_BYTES
              and Path(fresh.get('location',''))==profile/'models/whisper/small' and first['health'].get('whisper') is False)
        check('fresh profile has no projects',request(client,'GET','/api/jobs').json()==[])
        with httpx.Client(base_url=FRONTEND,trust_env=False,follow_redirects=False,timeout=20) as anonymous:
            for path in ('/','/api/models/whisper','/api/health'):
                request(anonymous,'GET',path,expected=403)
        check('model setup, home and API reject anonymous callers',True)
        request(client,'GET','/api/health',expected=403,headers={'Origin':'http://127.0.0.1:3000'})
        request(client,'GET','/api/health',expected=403,headers={'Host':'localhost:3300'})
        request(first['backend'],'GET','/api/models/whisper',expected=403,headers={'x-podcut-run-token':'wrong'})
        check('new setup retains Host Origin and backend token protection',True)

        stage='invalid offline model recovery'
        bad=checked(output/'invalid-model.zip',PROFILE)
        with zipfile.ZipFile(bad,'w') as target:
            target.writestr('podcut-model.json',json.dumps({'schema':1,'model':'small','revision':'invalid'}))
        with bad.open('rb') as stream:
            request(client,'POST','/api/models/whisper/offline',files={'file':('invalid-model.zip',stream,'application/zip')})
        failed,bad_samples=wait_model(client,accept_failed=True)
        check('invalid offline model is rejected without enabling transcription',failed['state']=='failed'
              and not (profile/'models/whisper/small/model.bin').exists()
              and request(client,'GET','/api/health').json().get('whisper') is False)
        report['rejected_model']=failed

        stage='large actual offline model provisioning'
        began=time.monotonic()
        with archive.open('rb') as stream:
            accepted=request(client,'POST','/api/models/whisper/offline',files={'file':(archive.name,stream,'application/zip')}).json()
        model,samples=wait_model(client)
        report['model_provisioning']={'zip_bytes':archive.stat().st_size,'seconds':round(time.monotonic()-began,3),
                                      'accepted':accepted,'samples':samples,'result':model}
        check('large streamed offline ZIP passes frontend and reaches fully verified ready state',model['state']=='ready'
              and model['verified_bytes']==MODEL_BYTES and model['completed_bytes']==MODEL_BYTES and model['percent']==100
              and model['revision']==MODEL_REVISION and archive.stat().st_size>10*1024*1024)
        check('model progress stays within real byte and percent bounds',all(0<=item.get('percent',0)<=100
              and 0<=item.get('verified_bytes',0)<=MODEL_BYTES and 0<=item.get('completed_bytes',0)<=MODEL_BYTES
              for item in [accepted,*samples]))
        model_dir=checked(profile/'models/whisper/small',profile)
        model_files={path.name:state(path) for path in model_dir.iterdir() if path.is_file()}
        check('provisioning retains the trusted Whisper license',(model_dir/'LICENSE.OpenAI-Whisper.txt').is_file())
        reused=request(client,'POST','/api/models/whisper/download').json()
        check('already validated model returns ready without rewriting or downloading files',reused['state']=='ready'
              and model_files=={path.name:state(path) for path in model_dir.iterdir() if path.is_file()})
        health=request(client,'GET','/api/health').json()
        check('local Whisper M1 and heuristic are ready without cloud or active-speaker claims',health.get('whisper') is True
              and health.get('ffmpeg') is True and health.get('ffprobe') is True and health.get('reframe',{}).get('auto_face') is True
              and health.get('reframe',{}).get('active_speaker') is False and health.get('ai',{}).get('provider')=='heuristic'
              and health.get('gemini') is False and health.get('openai') is False)

        stage='actual Whisper and heuristic clipping'
        created=request(client,'POST','/api/jobs',expected=201,json={'title':'Phase3A relocated independent actual ASR fixture'}).json()
        ident=created['id']
        if not re.fullmatch('[a-f0-9]{32}',ident):
            raise VerificationFailure('The package returned an invalid project ID.')
        with fixture.open('rb') as stream:
            source=request(client,'POST',f'/api/jobs/{ident}/source',files={'file':(fixture.name,stream,'video/mp4')}).json()
        directory=checked(profile/'data'/ident,profile)
        record=json.loads((directory/'job.json').read_text(encoding='utf-8'))
        check('upload is copied into only the fresh native profile',sha(checked(directory/record['_source'],profile))==sha(fixture)
              and source['media']['has_audio'] and source['media']['duration']>60)
        began=time.monotonic()
        settings={'duration_preset':'20','clip_count':3,'language':'en'}
        request(client,'POST',f'/api/jobs/{ident}/analyze',json=settings)
        job,states=wait_job(client,ident)
        transcript=request(client,'GET',f'/api/jobs/{ident}/transcript').json()
        words=[word for segment in transcript['segments'] for word in segment.get('words',[])]
        actual_tokens=set(re.findall('[a-z]+',' '.join(segment['text'] for segment in transcript['segments']).lower()))
        expected_tokens=set(re.findall('[a-z]+',reference_path.read_text(encoding='utf-8').lower()))
        recall=len(actual_tokens&expected_tokens)/max(1,len(expected_tokens))
        check('packaged Whisper actually transcribes speech with bounded word timing',bool(words) and transcript.get('word_timestamps') is True
              and all(0<=word['start']<word['end']<=source['media']['duration']+.02 for word in words)
              and not json.loads((directory/'job.json').read_text(encoding='utf-8')).get('_transcript_imported'))
        check('actual speech reaches heuristic suggestions capped at 20 seconds',job.get('analysis_mode')=='heuristic'
              and bool(job.get('candidates')) and all(0<item['end']-item['start']<=20.000001 for item in job['candidates']) and recall>=.5)
        report['asr']={'seconds':round(time.monotonic()-began,3),'words':len(words),'candidate_count':len(job['candidates']),
                       'reference_distinct_word_recall':recall,'states':states,'metric_notice':'TTS distinct vocabulary overlap, not WER or natural-podcast accuracy.'}
        report['project_id']=ident
        write_json(output/'actual-transcript.json',transcript)
        transcript_state=state(directory/'transcript.json')
        analysis_cache={path.name:state(path) for path in directory.glob('analysis-*.json')}
        request(client,'POST',f'/api/jobs/{ident}/analyze',json=settings)
        repeated,_=wait_job(client,ident)
        check('repeat analysis preserves transcript and heuristic cache',repeated['candidates']==job['candidates']
              and state(directory/'transcript.json')==transcript_state
              and analysis_cache=={path.name:state(path) for path in directory.glob('analysis-*.json')})

        stage='actual M1 and manual composition'
        request(client,'POST',f'/api/jobs/{ident}/reframe/analyze',json={'start':0,'end':18,'mode':'auto_face'})
        wait_job(client,ident)
        plan=request(client,'GET',f'/api/jobs/{ident}/reframe?start=0&end=18').json()
        shots=[next(shot for shot in plan['shots'] if shot['start']<=at<shot['end']) for at in (3,9,15)]
        cuts=[shot['start'] for shot in plan['shots'][1:]]
        centers=lambda shot:[face['bbox'][0]+face['bbox'][2]/2 for sample in shot['face_samples'] for face in sample['faces']]
        check('M1 detects real left right faces and the 6/12 second hard cuts',bool(centers(shots[0])) and bool(centers(shots[1]))
              and max(centers(shots[0]))<.45 and min(centers(shots[1]))>.55
              and all(any(abs(cut-target)<=.25 for cut in cuts) for target in (6,12)))
        check('two-face fallback preserves both and honest unknown speaker confidence',shots[2]['focus']=='both'
              and shots[2]['review_required'] is True and shots[2]['speaker_confidence'] is None)
        before_pose=request(client,'GET',f'/api/jobs/{ident}/reframe/pose?plan_id={plan["id"]}&time=5.8').json()['pose']
        after_pose=request(client,'GET',f'/api/jobs/{ident}/reframe/pose?plan_id={plan["id"]}&time=6.2').json()['pose']
        check('hard camera cut resets framing without an across-camera pan',before_pose['x']<.45 and after_pose['x']>.55)
        auto_video=export(client,ident,'m1-auto-vertical-18s',{'start':0,'end':18,'aspect':'vertical','caption_style':'none','reframe_id':plan['id']},(1080,1920))
        evidence=lambda value:hashlib.sha256(json.dumps([value['analysis'],[(s['face_samples'],s['auto_keyframes']) for s in value['shots']]],sort_keys=True).encode()).hexdigest()
        original_evidence=evidence(plan)
        edited=copy.deepcopy(plan)
        edited['shots'][0].update(focus='locked',keyframes=[{'time':t,'x':.35,'y':.5,'zoom':1.12,'manual':True}
                                                         for t in (shots[0]['start'],shots[0]['end'])])
        request(client,'PUT',f'/api/jobs/{ident}/reframe',json=edited)
        saved=request(client,'GET',f'/api/jobs/{ident}/reframe?start=0&end=18').json()
        pose=request(client,'GET',f'/api/jobs/{ident}/reframe/pose?plan_id={plan["id"]}&time=1').json()['pose']
        check('manual X Y Zoom keyframes persist without reanalysis',saved['shots'][0]['decision']=='manual'
              and abs(pose['x']-.35)<.00001 and abs(pose['zoom']-1.12)<.00001 and evidence(saved)==original_evidence
              and state(directory/'transcript.json')==transcript_state
              and analysis_cache=={path.name:state(path) for path in directory.glob('analysis-*.json')})
        write_json(output/'manual-plan.json',saved)
        manual_video=export(client,ident,'manual-poppins-vertical-18s',{'start':0,'end':18,'aspect':'vertical','caption_style':'poppins_highlight','reframe_id':plan['id']},(1080,1920))
        first_word=float(words[0]['start'])
        native_video=export(client,ident,'actual-poppins-native-3s',{'start':first_word,'end':first_word+3,'aspect':'original','caption_style':'poppins_highlight'},(source['media']['width'],source['media']['height']))

        stage='visual output evidence'
        frames=[]
        for label,file,at in [('left',auto_video,3),('right',auto_video,9),('both',auto_video,15),('manual-poppins',manual_video,3),('native-poppins',native_video,1)]:
            target=checked(output/(label+'.png'),PROFILE)
            tool('ffmpeg',['-hide_banner','-loglevel','error','-nostdin','-y','-ss',at,'-i',file,'-frames:v','1','-vf','scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2:color=0x151515',target])
            frames.append(target)
        sheet=checked(output/'contact-sheet.png',PROFILE)
        inputs=[item for file in frames for item in ('-i',file)]
        tool('ffmpeg',['-hide_banner','-loglevel','error','-nostdin','-y',*inputs,'-filter_complex','hstack=inputs=5[out]','-map','[out]','-frames:v','1',sheet])
        check('actual MP4 frames and contact sheet decode successfully',sheet.is_file() and all(path.stat().st_size>0 for path in frames))
        report['artifacts']={'contact_sheet':str(sheet),'frames':list(map(str,frames)),'visual_review':'PENDING_HUMAN_QA'}

        stage='restart persistence'
        persistent=inventory(directory)
        write_json(output/'project-before-restart.json',persistent)
        old_ui_token=first['ui_token']
        close_session(first)
        second=start_session('restart')
        client=second['client']
        model2=request(client,'GET','/api/models/whisper').json()
        loaded=request(client,'GET',f'/api/jobs/{ident}').json()
        loaded_plan=request(client,'GET',f'/api/jobs/{ident}/reframe?start=0&end=18').json()
        request(client,'GET','/api/health',expected=403,headers={'x-podcut-ui-token':old_ui_token})
        check('restart rotates credentials and preserves verified model without download',model2['state']=='ready'
              and second['health'].get('whisper') is True
              and model_files=={path.name:state(path) for path in model_dir.iterdir() if path.is_file()})
        check('restart preserves project source transcript captions exports and manual keyframes',loaded['id']==ident
              and loaded['status']=='ready' and loaded_plan==saved and persistent==inventory(directory))
        report['restart']={'verified':True,'project_files':len(persistent),'fresh_profile':str(profile)}
        # Public fixture published by the youtube-dl author for downloader tests.
        # The regular app handles provider restrictions and exposes upload recovery.
        source_url = 'https://www.youtube.com/watch?v=BaW_jenozKc'
        youtube_job = request(client,'POST','/api/jobs',expected=201,
                             json={'title':'Public youtube-dl adapter fixture','youtube_url':source_url}).json()
        yt_id = youtube_job['id']
        result = request(client,'POST',f'/api/jobs/{yt_id}/ingest',json={'duration_preset':'20'}).json()
        until = time.monotonic() + 75
        while time.monotonic() < until and not result.get('media') and result['status'] not in ('failed','blocked','cancelled','ready'):
            time.sleep(.5)
            result = request(client,'GET',f'/api/jobs/{yt_id}').json()
        report['youtube']={'source':source_url,'status':result['status'],'media':bool(result.get('media')),
                           'source_error':result.get('source_error'),'message':result.get('message')}
        save()
        close_session(second)
    except FollowupComplete:
        pass
    except BaseException as error:
        failure={'stage':stage,'type':type(error).__name__,'message':str(error)}
        report['failure']=failure
        print('FAIL '+stage+': '+redact(str(error)),flush=True)
    finally:
        cleanup=[]
        for session in reversed(sessions):
            if not session['closed']:
                try:
                    close_session(session)
                except BaseException as error:
                    cleanup.append({'stage':'owned shutdown','type':type(error).__name__,'message':str(error)})
        try:
            after=inventory(app_root)
            write_json(output/'package-after.json',after)
            changes=sorted(name for name in package_before.keys()|after.keys() if package_before.get(name)!=after.get(name))
            report['package_unchanged']=not changes
            report['package_changed_paths']=changes
            if changes:
                raise VerificationFailure('Actual runtime changed its executable/package directory.')
        except BaseException as error:
            cleanup.append({'stage':'package immutability','type':type(error).__name__,'message':str(error)})
        try:
            _old.free_dev_ports()
            report['dev_ports_released']=True
        except BaseException as error:
            cleanup.append({'stage':'port release','type':type(error).__name__,'message':str(error)})
        report['cleanup_errors']=cleanup
        report['status']='passed' if failure is None and not cleanup else 'failed'
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        save()
        print(report['status'].upper()+' Phase3A packaged report: '+str(report_path),flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (VerificationFailure,ValueError) as error:
        print('FAIL preflight: '+str(error),flush=True)
        raise SystemExit(1)
