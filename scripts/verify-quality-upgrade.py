"""Quality build QA through the same native Job Object and authenticated Next API."""
from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

import httpx

spec = importlib.util.spec_from_file_location('phase3a_qa', Path(__file__).with_name('verify-phase3a-package.py'))
baseline_qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline_qa)

DEV = Path(r'D:\PODCUT-Labs\Installer-Dev')
QUALITY = DEV / 'quality-upgrade'
checked, state = baseline_qa.checked, baseline_qa.state


def wait_for(read, complete, *, timeout=480):
    deadline = time.monotonic() + timeout
    states = []
    while time.monotonic() < deadline:
        value = read()
        name = value.get('status') or value.get('state')
        if not states or name != states[-1]:
            states.append(name)
            print(name, flush=True)
        if name in {'failed', 'blocked', 'cancelled'}:
            error = value.get('source_error') or {}
            raise RuntimeError(f'Workflow {name}: {error.get("code") or value.get("error") or value.get("message")}')
        if complete(value):
            return value, states
        time.sleep(.35)
    raise TimeoutError(f'Workflow timed out after stages {states}')


def prove_peer(host, port, header, token):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=6)
        try:
            connection.connect()
            client_port = connection.sock.getsockname()[1]
            nonce = uuid.uuid4().hex
            host.send({'op': 'status', 'nonce': nonce, 'connection': {'port': port, 'client_port': client_port}})
            proof = host.wait('status', timeout=10)
            if proof.get('nonce') != nonce or proof.get('connection', {}).get('owned') is not True:
                raise RuntimeError('Refused to give credentials to unowned listener.')
            connection.request('GET', '/api/health', headers={header:token, 'Origin': baseline_qa.FRONTEND})
            response = connection.getresponse()
            data = response.read(1024*1024)
            if response.status != 200:
                raise RuntimeError(f'Health returned {response.status}.')
            return json.loads(data)
        except (ConnectionRefusedError, TimeoutError, http.client.RemoteDisconnected):
            time.sleep(.25)
        finally:
            connection.close()
    raise TimeoutError(f'Private service at port {port} did not start.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=uuid.UUID)
    parser.add_argument('--resume-profile', type=uuid.UUID)
    args = parser.parse_args()
    run = checked(QUALITY/'runs'/str(args.build), QUALITY)
    desktop = checked(run/'dist/PODCUTAI-InstallerDev-win32-x64', run)
    package = desktop/'resources/podcut'
    exe = checked(package/'backend/PODCUTBackend.exe', package)
    app_manifest = checked(run/'desktop-package-manifest.json', run)
    before = json.loads(app_manifest.read_text(encoding='utf-8-sig'))
    output = checked(run/('qa-'+uuid.uuid4().hex[:10]), run)
    output.mkdir()
    temp = output/'temp'
    temp.mkdir()
    instance = str(args.resume_profile or uuid.uuid4())
    profile = checked(run/'PODCUTAI-InstallerDev/profiles'/instance, run)
    baseline_qa.PROFILE = run
    baseline_qa._old.PROFILE = run
    baseline_qa._old.free_dev_ports()
    windows = baseline_qa.windows_directory()
    env = baseline_qa.minimal_environment(windows, package)
    env.update(TEMP=str(temp), TMP=str(temp))
    record = json.loads(subprocess.check_output([exe, '--bootstrap-info', '--isolated-profile', instance], env=env, cwd=output))
    if Path(record['profile_root']) != profile or Path(record['package_root']) != package or profile.exists() != bool(args.resume_profile):
        raise RuntimeError('Frozen bootstrap did not resolve the expected in-workspace profile.')
    run_id = str(uuid.uuid4())
    backend_token, ui_token = secrets.token_hex(32), secrets.token_hex(32)
    report = {'status':'running','host_scope':'development-host-only','build':str(args.build),
              'profile':instance,'resumed':bool(args.resume_profile),
              'source_url':'https://www.youtube.com/watch?v=X9t-u87df3o','checks':{},'sources':{}}
    host = None

    def save():
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def check(name, condition):
        report['checks'][name] = bool(condition)
        save()
        if not condition:
            raise RuntimeError(name)
        print('PASS', name, flush=True)

    def request(client, method, path, status=200, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.status_code != status:
            raise RuntimeError(f'{method} {path}: HTTP {response.status_code}.')
        return response

    try:
        host = baseline_qa.JobHost(package, profile, instance, run_id, env, output)
        host.wait('ready')
        children = []
        for role, executable, arguments, cwd, port, header, token in [
            ('backend', exe, ['--serve','--run-id',run_id,'--isolated-profile',instance],
             profile/'temp'/('desktop-'+run_id)/'backend', 8300, 'x-podcut-run-token', backend_token),
            ('frontend', package/'node/node.exe', [str(package/'frontend/server.js')],
             package/'frontend', 3300, 'x-podcut-ui-token', ui_token),
        ]:
            host.send({'op':'start','id':role,'executable':str(executable),'args':arguments,'cwd':str(cwd),
                       'env':{'PODCUT_RUN_ID':run_id,'PODCUT_RUN_TOKEN':backend_token,
                              **({'PODCUT_UI_TOKEN':ui_token} if role == 'frontend' else {})}})
            identity = baseline_qa.process_identity(host.wait('started', role=role)['pid'])
            check(role+' uses its bundled executable', identity and Path(identity['executable']) == executable)
            children.append(identity)
            health = prove_peer(host, port, header, token)
        check('private frontend reaches the expected Quality profile', Path(health['instance']['profile_root']) == profile
              and health['instance']['run_id'] == run_id and health['reframe']['auto_face'] is True)

        with httpx.Client(base_url=baseline_qa.FRONTEND, trust_env=False, follow_redirects=False, timeout=120,
                          headers={'Origin':baseline_qa.FRONTEND,'x-podcut-ui-token':ui_token}) as client:
            if not args.resume_profile:
                archive = checked(QUALITY/'fixtures/whisper-small-verified.zip', QUALITY)
                with archive.open('rb') as stream:
                    request(client,'POST','/api/models/whisper/offline',files={'file':(archive.name,stream,'application/zip')})
            model,_ = wait_for(lambda: request(client,'GET','/api/models/whisper').json(), lambda value: value['state']=='ready')
            check('Whisper verified from separate ZIP', model['verified_bytes']==486212372)
            settings = {'duration_preset':'20','clip_count':3,'language':'en'}
            if args.resume_profile:
                projects = request(client,'GET','/api/jobs').json()
                matches = [job for job in projects if job['youtube_url']==report['source_url']]
                check('resume targets the sole ready MIT project',len(matches)==1 and matches[0]['status']=='ready')
                path = '/api/jobs/'+matches[0]['id']
            else:
                identity = request(client,'POST','/api/jobs',201,json={'title':'MIT OCW Introduction — Gilbert Strang',
                                                                       'youtube_url':report['source_url']}).json()['id']
                path = '/api/jobs/'+identity
                request(client,'POST',path+'/ingest',json={**settings,'auto_export':True,
                                                            'output':{'aspect':'vertical','caption_style':'poppins_highlight'}})
            fetched,steps = wait_for(lambda: request(client,'GET',path).json(),
                                     lambda value: value['status']=='ready' and value.get('auto_export',{}).get('status') in {'ready','skipped','failed'})
            report['sources']['mit'] = {'states':steps,'source':fetched['media'],'ingestion':fetched['ingestion']['metadata'],
                                        'analysis':fetched['analysis_diagnostics'],'candidates':fetched['candidates'],
                                        'auto_export':fetched['auto_export'],'cache_hit':fetched['source_cache_hit']}
            save()
            check('cold URL download measured by FFprobe', fetched['source_cache_hit'] is False and fetched['media']['has_audio']
                  and fetched['ingestion']['metadata'].get('quality'))
            check('Whisper and 20-second heuristic yield a verified automatic export',
                  fetched['transcript_ready'] is True and fetched['analysis_mode']=='heuristic'
                  and fetched['auto_export']['status']=='ready' and fetched['candidates'])
            candidate = fetched['candidates'][0]
            interval = {name:candidate[name] for name in ('start','end')}
            check('candidate fits 20 seconds', 8 <= interval['end']-interval['start'] <= 20)
            request(client,'POST',path+'/reframe/analyze',json={**interval,'mode':'auto_face'})
            wait_for(lambda: request(client,'GET',path).json(), lambda value: value['status']=='ready' and value.get('reframe',{}).get('status')=='ready')
            query = f'?start={interval["start"]}&end={interval["end"]}'
            plan = request(client,'GET',path+'/reframe'+query).json()
            check('local M1 produced a per-shot plan for the URL clip', plan.get('shots') and plan['mode']=='auto_face')
            queued = request(client,'POST',path+'/exports',202,json={**interval,'duration_preset':'20','aspect':'vertical',
                                                        'caption_style':'poppins_highlight','reframe_id':plan['id']}).json()
            export_id = queued['exports'][-1]['id']
            ready,_ = wait_for(lambda: request(client,'GET',path).json(),lambda value: value['status']=='ready')
            row = next(item for item in ready['exports'] if item['id']==export_id)
            export_url = path+f'/exports/{export_id}'
            file = output/'mit-strang-20s-m1-poppins.mp4'
            file.write_bytes(request(client,'GET',export_url+'/file').content)
            ass = request(client,'GET',export_url+'/captions.ass').text
            report['sources']['mit'].update(reframe={'mode':plan['mode'],'shot_count':len(plan['shots']),
                'face_observations':sum(len(sample['faces']) for shot in plan['shots'] for sample in shot['face_samples'])},
                export=row['media'],file=str(file))
            check('measured export is vertical H.264/AAC with Poppins and <=20 s',row['media']['width']==1080 and row['media']['height']==1920
                  and row['media']['duration']<=20.05 and row['media']['video_codec']=='h264' and row['media']['audio_codec']=='aac'
                  and 'Poppins SemiBold' in ass and r'\c&H5CF3D7&' in ass)
            subprocess.run([package/'tools/ffmpeg.exe','-v','error','-nostdin','-i',file,'-f','null','-'],
                           check=True,timeout=90,cwd=output,env=env)
            check('complete MP4 decodes with bundled FFmpeg', True)
            podcast = QUALITY/'fixtures/chalk-radio-kaiser-6min.mp4'
            if podcast.is_file():
                podcast_id = request(client,'POST','/api/jobs',201,json={'title':'MIT Chalk Radio — The Lumpy Universe'}).json()['id']
                podcast_path = '/api/jobs/'+podcast_id
                with podcast.open('rb') as stream:
                    request(client,'POST',podcast_path+'/source',files={'file':(podcast.name,stream,'video/mp4')})
                request(client,'POST',podcast_path+'/analyze',json=settings)
                result,steps = wait_for(lambda: request(client,'GET',podcast_path).json(),lambda value: value['status']=='ready')
                transcript = request(client,'GET',podcast_path+'/transcript').json()
                (output/'chalk-radio-transcript.json').write_text(json.dumps(transcript,ensure_ascii=False),encoding='utf-8')
                report['sources']['chalk_radio'] = {'states':steps,'source':result['media'],'candidates':result['candidates'],
                                                    'analysis':result['analysis_diagnostics']}
                check('conversation podcast analyzed through packaged Whisper and heuristics', result['transcript_ready']
                      and result['analysis_mode']=='heuristic' and result['analysis_diagnostics']['windows_evaluated']>0)
        report['status']='passed'
    except Exception as exc:
        report['status']='failed'
        report['failure']=str(exc).replace(backend_token,'[REDACTED]').replace(ui_token,'[REDACTED]')
        print(report['failure'],flush=True)
    finally:
        if host is not None:
            closed = host.close()
            report['checks']['native host closed its owned children'] = (closed['exit_code']==0 and not closed['forced_host_termination']
                and closed['closed_event'] is not None and closed['closed_event'].get('active_processes')==0)
            if not report['checks']['native host closed its owned children']:
                report['status']='failed'
        current = {path.relative_to(desktop).as_posix():state(path) for path in desktop.rglob('*') if path.is_file()}
        expected = {row['path']:(row['bytes'],row['sha256']) for row in before['files']}
        report['checks']['package unchanged'] = expected == {name:(row['size'],row['sha256']) for name,row in current.items()}
        if not report['checks']['package unchanged']:
            report['status']='failed'
        save()
        print(report['status'],output/'report.json',flush=True)
    return 0 if report['status']=='passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
