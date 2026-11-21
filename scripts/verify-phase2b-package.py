"""Real frozen-package QA. No original inputs, cloud calls or installer actions.

The Python interpreter running this harness is a test driver only. All app
services are launched by the bundled PODCUTBackend.exe job host. Credentials
travel over its stdin pipe and remain in memory, never in the report or argv.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

PROJECT = Path('D:/PODCUT-Labs/Installer-Dev')
PROFILE = Path('C:/Users/video/AppData/Local/PODCUTAI-InstallerDev')
DEFAULT_PACKAGE = PROJECT / 'desktop/dist/PODCUTAI-InstallerDev-win32-x64/resources/podcut'
FRONTEND = 'http://127.0.0.1:3300'
BACKEND = 'http://127.0.0.1:8300'
ACTIVE = {'queued', 'probing', 'analyzing', 'transcribing', 'exporting', 'reframing', 'ingesting'}


class VerificationFailure(RuntimeError):
    pass


def checked(value: str | Path, root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(root):
        raise VerificationFailure('A QA path left its approved DEV boundary.')
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise VerificationFailure('A QA path traverses a symlink, junction or reparse point.')
    if path.resolve() != path:
        raise VerificationFailure('A QA path does not resolve to its approved location.')
    return path


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def state(path: Path) -> dict:
    info = path.stat()
    return {'sha256': sha(path), 'size': info.st_size, 'mtime_ns': info.st_mtime_ns}


def inventory(root: Path) -> dict:
    result, pending = {}, [root]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            checked(path, root)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = state(path)
            else:
                raise VerificationFailure('An unsupported filesystem entry appeared in the package.')
    return result


def process_identity(pid: int) -> dict | None:
    """Observe exact child identity; never stop a process discovered by PID."""
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    api.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                   ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    api.GetProcessTimes.restype = wintypes.BOOL
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() in (87, 1168):
            return None
        raise VerificationFailure('Could not observe an owned child process identity.')
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise VerificationFailure('Could not observe an owned process creation time.')
        return {'pid': pid, 'executable': str(Path(buffer.value)),
                'created_ticks': (created.dwHighDateTime << 32) | created.dwLowDateTime}
    finally:
        api.CloseHandle(handle)


def service_descendants(roots: dict[str, dict]) -> list[dict]:
    """Observe children of already verified roots; never use this list to kill."""
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('name', wintypes.WCHAR * 260)]
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32FirstW.restype = wintypes.BOOL
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise VerificationFailure('Could not observe owned service descendants.')
    rows, entry = [], Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        valid = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            rows.append((int(entry.pid), int(entry.parent)))
            valid = api.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    known = {value['pid']: value for value in roots.values()}
    found, changed = [], True
    while changed:
        changed = False
        for pid, parent in rows:
            if pid in known or parent not in known:
                continue
            observed = process_identity(pid)
            if observed and observed['created_ticks'] >= known[parent]['created_ticks']:
                known[pid] = observed
                found.append({**observed, 'parent_pid': parent})
                changed = True
    return found


def free_dev_ports() -> None:
    for port in (3300, 8300):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise VerificationFailure(f'DEV port {port} is occupied; no process was stopped.') from None


def authored_transcript() -> dict:
    # Deliberately authored clocks: these test timing import/editorial mechanics,
    # and are kept separate from the second, genuine ASR project below.
    thoughts = [
        (2.0, 19.5, 'Ternyata kita tidak perlu menunggu motivasi karena langkah kecil membuat pekerjaan lebih mudah, jadi mulailah dengan satu kalimat dan periksa hasilnya sebelum menambah target.'),
        (23.0, 40.0, 'Pelajarannya bukan bekerja lebih lama, karena kita justru lebih mudah melihat kesalahan setelah istirahat singkat, jadi berhenti sebentar lalu periksa ulang pekerjaan sebelum mengirim hasilnya.'),
    ]
    result = []
    for first, last, text in thoughts:
        tokens = text.split()
        result.append({'start': first, 'end': last, 'text': text,
                       'words': [{'word': word, 'start': first + (last-first)*index/len(tokens),
                                  'end': first + (last-first)*(index+1)/len(tokens)} for index, word in enumerate(tokens)]})
    return {'language': 'id', 'segments': result}


class JobHost:
    def __init__(self, package: Path, run_id: str, environment: dict, output: Path):
        self.package, self.run_id = package, run_id
        self.events = []
        self.inbox = queue.Queue()
        self.stderr = checked(output / 'job-host.stderr.log', PROFILE).open('wb')
        temporary = checked(PROFILE / 'temp' / ('desktop-' + run_id), PROFILE)
        temporary.mkdir(parents=True, exist_ok=False)
        self.process = subprocess.Popen([str(package/'backend/PODCUTBackend.exe'), '--job-host',
                                         '--package-root', str(package), '--run-id', run_id,
                                         '--profile-root', str(PROFILE)],
                                        cwd=temporary, env=environment, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.stderr,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.forced = False
        self.closed_event = None
        threading.Thread(target=self._read, daemon=True, name='package-QA-job-events').start()

    def _read(self):
        try:
            while line := self.process.stdout.readline(65537):
                if len(line) > 65536:
                    self.inbox.put({'event': 'driver-error', 'message': 'Oversized job host event.'})
                    return
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    self.inbox.put({'event': 'driver-error', 'message': 'Invalid job host event.'})
                    return
                self.inbox.put(event)
        finally:
            self.inbox.put({'event': 'driver-eof'})

    def send(self, command: dict):
        if self.process.poll() is not None:
            raise VerificationFailure('The packaged job host exited before a control command.')
        self.process.stdin.write(json.dumps(command, separators=(',', ':')).encode('utf-8') + b'\n')
        self.process.stdin.flush()

    def wait(self, expected: str, *, role: str | None = None, timeout=45) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.inbox.get(timeout=min(.25, max(.01, deadline-time.monotonic())))
            except queue.Empty:
                continue
            self.events.append(event)
            if event.get('event') == 'closed':
                self.closed_event = event
            if event.get('event') == expected and (role is None or event.get('id') == role):
                return event
            if event.get('event') in {'error', 'driver-error', 'driver-eof', 'exit', 'closed'}:
                raise VerificationFailure('The job host did not reach the expected service state: ' + event.get('event', 'unknown'))
        raise VerificationFailure('Timed out waiting for a packaged job host event: ' + expected)

    def close(self) -> dict:
        try:
            if self.process.poll() is None:
                self.send({'op': 'shutdown'})
                try:
                    self.closed_event = self.wait('closed', timeout=25)
                finally:
                    self.process.stdin.close()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.forced = True
                # This is the exact Popen child handle, never a discovered PID.
                # The host's KILL_ON_JOB_CLOSE then owns descendant cleanup.
                self.process.kill()
                self.process.wait(timeout=10)
        finally:
            if self.process.poll() is None:
                self.forced = True
                self.process.kill()
                self.process.wait(timeout=10)
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
            self.stderr.close()
        return {'exit_code': self.process.returncode, 'forced_host_termination': self.forced,
                'closed_event': self.closed_event, 'events': self.events}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root', default=str(DEFAULT_PACKAGE))
    parser.add_argument('--whisper-model', choices=('tiny.en', 'small'), default='small')
    arguments = parser.parse_args()
    checked(Path(__file__).absolute(), PROJECT)
    checked(Path(sys.executable).absolute(), PROJECT)
    package = checked(Path(arguments.package_root), PROJECT / 'desktop')
    app_root = checked(package.parents[1], PROJECT / 'desktop')
    for path in (package/'backend/PODCUTBackend.exe', package/'node/node.exe', package/'frontend/server.js',
                 package/'tools/ffmpeg.exe', package/'tools/ffprobe.exe', package/'podcut-package.json'):
        if not checked(path, package).is_file():
            raise VerificationFailure('A required packaged application file is missing.')
    fixture = checked(PROFILE/'temp/phase2b-fixtures/owned-face-speech.mp4', PROFILE)
    reference_path = checked(fixture.parent/'speech-reference.txt', PROFILE)
    if not fixture.is_file() or not reference_path.is_file():
        raise VerificationFailure('The independently prepared speech/face fixture is missing.')
    model = checked(PROFILE/'models/whisper'/arguments.whisper_model, PROFILE)
    for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'):
        if not checked(model/name, PROFILE).is_file() or (model/name).stat().st_size == 0:
            raise VerificationFailure('The independently prepared Whisper test model is incomplete.')
    free_dev_ports()
    import httpx

    run_id = str(uuid.uuid4())
    backend_token, ui_token = secrets.token_hex(32), secrets.token_hex(32)
    output = checked(PROFILE/'runtime/phase2b'/('package-' + run_id), PROFILE)
    output.mkdir(parents=True, exist_ok=False)
    report_path = output/'report.json'
    report = {'schema': 'podcut-phase2b-package-QA-v1', 'run_id': run_id, 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(), 'package': str(package),
              'evidence': str(output), 'checks': [], 'requests': [], 'exports': {}, 'asr_model': arguments.whisper_model,
              'fixture_notice': 'NASA public-domain still-face composition and local Windows TTS. Authored timing import is tested in one project; a separate project uses actual packaged Whisper ASR. This is not a real-podcast or active-speaker accuracy evaluation.'}
    host = None
    child_identities = {}
    config_original = None
    config_expected_hash = None
    config_path = checked(PROFILE/'config/backend.env', PROFILE)
    package_before = None
    failure = None
    stage = 'preflight'
    environment = {}

    def redact(value):
        if isinstance(value, str):
            return value.replace(backend_token, '[REDACTED]').replace(ui_token, '[REDACTED]')
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    def save():
        temporary = checked(report_path.with_suffix('.json.tmp'), PROFILE)
        temporary.write_text(json.dumps(redact(report), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(report_path)

    def write_json(path: Path, value):
        checked(path, PROFILE).write_text(json.dumps(redact(value), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def check(name: str, condition: bool, **details):
        report['checks'].append({'name': name, 'passed': bool(condition), **details})
        save()
        if not condition:
            raise VerificationFailure(name)
        print('PASS ' + name, flush=True)

    def request(client, method: str, path: str, *, expected=200, **kwargs):
        if not path.startswith('/') or path.startswith('//'):
            raise VerificationFailure('An HTTP request attempted to leave the fixed local service.')
        response = client.request(method, path, **kwargs)
        report['requests'].append({'method': method, 'path': path, 'status': response.status_code})
        if backend_token.encode() in response.content or ui_token.encode() in response.content:
            raise VerificationFailure('A desktop credential appeared in an HTTP response.')
        if response.status_code != expected:
            raise VerificationFailure(f'Unexpected HTTP {response.status_code} for {method} {path}')
        return response

    def wait_ready(client, *, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if host.process.poll() is not None:
                raise VerificationFailure('The packaged job host exited during HTTP readiness.')
            try:
                response = client.get('/api/health')
                if response.status_code == 200:
                    return response.json()
            except httpx.RequestError:
                pass
            time.sleep(.25)
        raise VerificationFailure('The packaged services did not become ready.')

    def wait_job(client, ident: str, *, timeout=480):
        deadline, states = time.monotonic() + timeout, []
        while time.monotonic() < deadline:
            job = request(client, 'GET', f'/api/jobs/{ident}').json()
            states.append(job['status'])
            if job['status'] in {'blocked', 'failed', 'cancelled'}:
                report['failed_job'] = job
                raise VerificationFailure('The packaged processing job entered ' + job['status'])
            if job['status'] == 'ready':
                return job, list(dict.fromkeys(states))
            time.sleep(.25)
        raise VerificationFailure('The packaged job did not finish before the bounded timeout.')

    def tool(name: str, args: list, *, timeout=90) -> bytes:
        executable = checked(package/'tools'/(name + '.exe'), package)
        result = subprocess.run([str(executable), *map(str, args)], cwd=output, env=environment,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            checked(output/(name + '-failure.txt'), PROFILE).write_bytes(result.stderr[-20000:])
            raise VerificationFailure('A bundled QA media tool failed: ' + name)
        return result.stdout

    def probe(path: Path) -> dict:
        raw = json.loads(tool('ffprobe', ['-v', 'error', '-show_format', '-show_streams', '-of', 'json', checked(path, PROFILE)]))
        video = next(item for item in raw['streams'] if item['codec_type'] == 'video')
        audio = next(item for item in raw['streams'] if item['codec_type'] == 'audio')
        rate = video['avg_frame_rate'].split('/')
        return {'width': int(video['width']), 'height': int(video['height']), 'video_codec': video['codec_name'],
                'audio_codec': audio['codec_name'], 'fps': float(rate[0])/max(1, float(rate[1])),
                'duration': float(raw['format']['duration']), 'size': int(raw['format']['size']),
                'video_start': float(video.get('start_time', 0)), 'audio_start': float(audio.get('start_time', 0)),
                'video_duration': float(video.get('duration', raw['format']['duration'])),
                'audio_duration': float(audio.get('duration', raw['format']['duration']))}

    def create_upload(client, title: str) -> tuple[str, dict]:
        created = request(client, 'POST', '/api/jobs', expected=201, json={'title': title}).json()
        ident = created['id']
        if not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise VerificationFailure('The app returned an invalid project identifier.')
        with fixture.open('rb') as stream:
            job = request(client, 'POST', f'/api/jobs/{ident}/source', files={'file': (fixture.name, stream, 'video/mp4')}).json()
        check(title + ' uploaded through the packaged frontend', job['media']['has_audio'] and job['media']['duration'] > 42)
        directory = checked(PROFILE/'data'/ident, PROFILE)
        record = json.loads((directory/'job.json').read_text(encoding='utf-8'))
        check(title + ' source is an independent profile file', sha(checked(directory/record['_source'], PROFILE)) == sha(fixture))
        return ident, job

    def export(client, ident: str, label: str, payload: dict, dimensions: tuple[int, int], *, timeout=480) -> Path:
        queued = request(client, 'POST', f'/api/jobs/{ident}/exports', expected=202, json=payload).json()
        export_id = queued['exports'][-1]['id']
        ready, states = wait_job(client, ident, timeout=timeout)
        record = next(item for item in ready['exports'] if item['id'] == export_id)
        response = request(client, 'GET', f'/api/jobs/{ident}/exports/{export_id}/file')
        target = checked(output/(label + '.mp4'), PROFILE)
        target.write_bytes(response.content)
        metadata = probe(target)
        check(label + ' MP4 is verified H264/AAC at the requested dimensions and duration',
              record['status'] == 'ready' and (metadata['width'], metadata['height']) == dimensions
              and metadata['video_codec'] == 'h264' and metadata['audio_codec'] == 'aac'
              and abs(metadata['duration']-(payload['end']-payload['start'])) <= .12, media=metadata)
        check(label + ' audio/video stream clocks remain aligned',
              abs(metadata['video_start']-metadata['audio_start']) <= .05
              and abs(metadata['video_duration']-metadata['audio_duration']) <= .15)
        part = request(client, 'GET', f'/api/jobs/{ident}/exports/{export_id}/file', expected=206, headers={'Range': 'bytes=0-127'})
        check(label + ' range playback works through the protected proxy', len(part.content) == 128 and part.content == response.content[:128])
        export_dir = checked(PROFILE/'data'/ident/'exports'/export_id, PROFILE)
        check(label + ' export and downloaded evidence match', sha(target) == sha(export_dir/'clip.mp4'))
        report['exports'][label] = {'project_id': ident, 'export_id': export_id, 'path': str(target), 'media': metadata, 'states': states}
        if payload.get('caption_style') == 'poppins_highlight':
            for extension in ('ass', 'srt', 'vtt'):
                sidecar = request(client, 'GET', f'/api/jobs/{ident}/exports/{export_id}/captions.{extension}')
                checked(output/(label + '.' + extension), PROFILE).write_bytes(sidecar.content)
                check(label + ' ' + extension.upper() + ' matches its stored caption artifact',
                      sidecar.content == (export_dir/('captions.' + extension)).read_bytes())
            ass = (export_dir/'captions.ass').read_text(encoding='utf-8')
            check(label + ' subtitle uses Poppins and lime active-word markup', 'Poppins SemiBold' in ass and r'\c&H5CF3D7&' in ass)
        return target

    try:
        stage = 'package immutability baseline'
        package_before = inventory(app_root)
        write_json(output/'package-before.json', package_before)
        check('desktop package inventory was captured before execution', bool(package_before), files=len(package_before))
        stage = 'isolated test configuration'
        config_original = config_path.read_bytes()
        checked(output/'backend-env.original', PROFILE).write_bytes(config_original)
        bom = config_original.startswith(b'\xef\xbb\xbf')
        text = config_original.decode('utf-8-sig')
        lines, replaced = [], 0
        for line in text.splitlines(keepends=True):
            if re.match(r'^\s*WHISPER_MODEL\s*=', line):
                ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
                line, replaced = 'WHISPER_MODEL=' + arguments.whisper_model + ending, replaced + 1
            lines.append(line)
        if replaced != 1:
            raise VerificationFailure('The DEV configuration must contain one unambiguous Whisper model setting.')
        new_bytes = (b'\xef\xbb\xbf' if bom else b'') + ''.join(lines).encode('utf-8')
        staging = checked(config_path.with_name('backend.env.QA-' + run_id + '.tmp'), PROFILE)
        staging.write_bytes(new_bytes)
        staging.replace(config_path)
        config_expected_hash = hashlib.sha256(new_bytes).hexdigest()
        check('only the external DEV model selection was temporarily changed', sha(config_path) == config_expected_hash)
        windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
        if not windows.is_absolute() or not (windows/'System32/kernel32.dll').is_file():
            raise VerificationFailure('The Windows system runtime could not be identified.')
        environment = {'SystemRoot': str(windows), 'WINDIR': str(windows), 'COMSPEC': str(windows/'System32/cmd.exe'),
                       'OS': 'Windows_NT', 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
                       'PATH': os.pathsep.join(map(str, (package/'backend', package/'node', package/'tools', windows/'System32', windows))),
                       'TEMP': str(PROFILE/'temp'), 'TMP': str(PROFILE/'temp'), 'USERPROFILE': str(PROFILE),
                       'LOCALAPPDATA': str(PROFILE/'cache/appdata'), 'APPDATA': str(PROFILE/'config/appdata'),
                       'XDG_CACHE_HOME': str(PROFILE/'cache/xdg'), 'PYTHONDONTWRITEBYTECODE': '1',
                       'PODCUT_PACKAGED_ROOT': str(package), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        stage = 'frozen host and services'
        host = JobHost(package, run_id, environment, output)
        ready = host.wait('ready')
        check('actual frozen job host announced the expected run', ready.get('protocol') == 'podcut-job-host-v1' and ready.get('run_id') == run_id)
        for role, executable, args, cwd in (
            ('backend', package/'backend/PODCUTBackend.exe', ['--serve', '--run-id', run_id], PROFILE/'temp'/('desktop-' + run_id)/'backend'),
            ('frontend', package/'node/node.exe', [str(package/'frontend/server.js')], package/'frontend'),
        ):
            credentials = {'PODCUT_RUN_ID': run_id, 'PODCUT_RUN_TOKEN': backend_token}
            if role == 'frontend':
                credentials['PODCUT_UI_TOKEN'] = ui_token
            host.send({'op': 'start', 'id': role, 'executable': str(executable), 'args': args,
                       'cwd': str(cwd), 'env': credentials})
            event = host.wait('started', role=role)
            identity = process_identity(event['pid'])
            check(role + ' runs the bundled executable with an independently observed PID',
                  identity is not None and Path(identity['executable']) == executable)
            child_identities[role] = identity
        report['owned_children'] = child_identities
        with httpx.Client(base_url=FRONTEND, headers={'x-podcut-ui-token': ui_token, 'Origin': FRONTEND},
                          trust_env=False, follow_redirects=False, timeout=httpx.Timeout(120, connect=4)) as client, \
             httpx.Client(base_url=BACKEND, headers={'x-podcut-run-token': backend_token},
                          trust_env=False, follow_redirects=False, timeout=10) as backend_client, \
             httpx.Client(base_url=FRONTEND, trust_env=False, follow_redirects=False, timeout=10) as anonymous:
            health = wait_ready(client)
            report['health'] = health
            identity = health['instance']
            check('protected standalone frontend reaches the exact frozen backend',
                  identity.get('packaged') is True and identity.get('build') == 'phase2b'
                  and identity.get('run_id') == run_id and Path(identity.get('packaged_root', '')) == package
                  and Path(identity.get('profile_root', '')) == PROFILE and identity.get('port') == 8300)
            check('packaged FFmpeg, Whisper and M1 are ready without active-speaker claims',
                  health.get('ffmpeg') is True and health.get('ffprobe') is True and health.get('whisper') is True
                  and health.get('reframe', {}).get('auto_face') is True
                  and health.get('reframe', {}).get('active_speaker') is False
                  and health.get('ai', {}).get('provider') == 'heuristic'
                  and health.get('openai') is False and health.get('gemini') is False)
            stage = 'HTTP security'
            home = request(client, 'GET', '/')
            check('bundled frontend serves its production UI', 'PODCUT' in home.text.upper())
            static_match = re.search(r'(?:src|href)="(/_next/static/[^"<>]+)"', home.text)
            check('production UI references a bundled static asset', static_match is not None)
            static_path = static_match.group(1).replace('&amp;', '&')
            request(client, 'GET', static_path)
            for path in ('/', '/api/health', static_path):
                request(anonymous, 'GET', path, expected=403)
            check('anonymous frontend page, API and static asset access is denied', True)
            request(client, 'GET', '/api/health', expected=403, headers={'Host': 'localhost:3300'})
            request(client, 'GET', '/api/health', expected=403, headers={'Origin': 'http://127.0.0.1:3000'})
            check('wrong frontend Host and Origin are denied even with the UI token', True)
            for path in ('/api/health', '/docs', '/openapi.json', '/api/jobs/' + 'a'*32 + '/media'):
                response = httpx.get(BACKEND + path, trust_env=False, timeout=5)
                check('anonymous backend ' + path + ' is denied', response.status_code == 403)
            request(backend_client, 'GET', '/api/health', expected=403, headers={'x-podcut-run-token': 'wrong'})
            request(client, 'GET', '/api/runtime-identity', headers={'x-podcut-run-token': 'wrong'})
            check('browser cannot replace the private backend token', True)

            stage = 'authored transcript and heuristic analysis'
            ident, source_job = create_upload(client, 'Packaged deterministic editorial fixture')
            report['authored_project_id'] = ident
            transcript = authored_transcript()
            request(client, 'POST', f'/api/jobs/{ident}/transcript', files={'file': ('authored-clocks.json', json.dumps(transcript), 'application/json')})
            directory = checked(PROFILE/'data'/ident, PROFILE)
            transcript_state = state(directory/'transcript.json')
            settings = {'duration_preset': '20', 'clip_count': 3}
            request(client, 'POST', f'/api/jobs/{ident}/analyze', json=settings)
            analyzed, analysis_states = wait_job(client, ident)
            candidates = analyzed.get('candidates') or []
            complete = all(any(abs(item['start']-segment['start']) < .002 and abs(item['end']-segment['end']) < .002
                               for segment in transcript['segments']) for item in candidates)
            check('heuristic 20 second suggestions preserve complete authored thoughts',
                  analyzed.get('analysis_mode') == 'heuristic' and bool(candidates) and complete
                  and all(item['end']-item['start'] <= 20.000001 for item in candidates))
            check('imported timing analysis does not invoke Whisper', state(directory/'transcript.json') == transcript_state
                  and not (directory/'audio.wav').exists() and 'transcribing' not in analysis_states)
            cache_before = {path.name: state(path) for path in directory.glob('analysis-*.json')}
            request(client, 'POST', f'/api/jobs/{ident}/analyze', json=settings)
            reused, _ = wait_job(client, ident)
            check('unchanged analysis reuses its cache and timing import', bool(cache_before)
                  and cache_before == {path.name: state(path) for path in directory.glob('analysis-*.json')}
                  and reused['candidates'] == candidates and state(directory/'transcript.json') == transcript_state)
            source_dimensions = (source_job['media']['width'], source_job['media']['height'])
            poppins = export(client, ident, 'authored-poppins-native',
                             {'start': 2.0, 'end': 5.0, 'aspect': 'original', 'caption_style': 'poppins_highlight'}, source_dimensions)

            stage = 'actual packaged M1 detection'
            request(client, 'POST', f'/api/jobs/{ident}/reframe/analyze', json={'start': 0, 'end': 18, 'mode': 'auto_face'})
            reframe_ready, _ = wait_job(client, ident)
            plan = request(client, 'GET', f'/api/jobs/{ident}/reframe?start=0&end=18').json()
            write_json(output/'m1-auto-plan.json', plan)
            cuts = [shot['start'] for shot in plan['shots'][1:]]
            check('M1 detects the two fixture camera cuts near 6 and 12 seconds',
                  all(any(abs(cut-target) <= .25 for cut in cuts) for target in (6, 12)), cuts=cuts)
            def shot_at(at):
                return next(shot for shot in plan['shots'] if shot['start'] <= at < shot['end'])
            left, right, master = (shot_at(at) for at in (3, 9, 15))
            left_faces = [face for sample in left['face_samples'] for face in sample['faces']]
            right_faces = [face for sample in right['face_samples'] for face in sample['faces']]
            centers = lambda faces: [face['bbox'][0]+face['bbox'][2]/2 for face in faces]
            check('real YuNet tracks the single face on each camera side', bool(left_faces) and bool(right_faces)
                  and max(centers(left_faces)) < .45 and min(centers(right_faces)) > .55
                  and left['decision'] == 'single_face' and right['decision'] == 'single_face')
            check('master shot keeps both faces and requests review without invented speaker confidence',
                  any(len(sample['faces']) >= 2 for sample in master['face_samples'])
                  and master['focus'] == 'both' and master['decision'] == 'ambiguous'
                  and master['review_required'] is True and master['speaker_confidence'] is None)
            pose_left = request(client, 'GET', f'/api/jobs/{ident}/reframe/pose?plan_id={plan["id"]}&time=5.8').json()['pose']
            pose_right = request(client, 'GET', f'/api/jobs/{ident}/reframe/pose?plan_id={plan["id"]}&time=6.2').json()['pose']
            check('camera cut changes composition directly without a cross-camera pan', pose_left['x'] < .45 and pose_right['x'] > .55)
            auto_video = export(client, ident, 'm1-auto-vertical-18s',
                                {'start': 0, 'end': 18, 'aspect': 'vertical', 'caption_style': 'none', 'reframe_id': plan['id']}, (1080, 1920))

            stage = 'manual M1 override persistence'
            immutable_evidence = hashlib.sha256(json.dumps([plan['analysis'], [(shot['face_samples'], shot['auto_keyframes']) for shot in plan['shots']]], sort_keys=True).encode()).hexdigest()
            edited = copy.deepcopy(plan)
            edited['shots'][0].update(focus='locked', keyframes=[
                {'time': left['start'], 'x': .35, 'y': .5, 'zoom': 1.12, 'manual': True},
                {'time': left['end'], 'x': .35, 'y': .5, 'zoom': 1.12, 'manual': True}])
            saved = request(client, 'PUT', f'/api/jobs/{ident}/reframe', json=edited).json()
            loaded = request(client, 'GET', f'/api/jobs/{ident}/reframe?start=0&end=18').json()
            after_evidence = hashlib.sha256(json.dumps([loaded['analysis'], [(shot['face_samples'], shot['auto_keyframes']) for shot in loaded['shots']]], sort_keys=True).encode()).hexdigest()
            pose = request(client, 'GET', f'/api/jobs/{ident}/reframe/pose?plan_id={plan["id"]}&time=1').json()['pose']
            check('manual per-shot framing persists and overrides automatic composition', saved == loaded
                  and loaded['shots'][0]['focus'] == 'locked' and loaded['shots'][0]['decision'] == 'manual'
                  and all(key['manual'] for key in loaded['shots'][0]['keyframes'])
                  and abs(pose['x']-.35) < .00001 and abs(pose['zoom']-1.12) < .00001)
            check('manual edit preserves face analysis, transcript and highlight cache', after_evidence == immutable_evidence
                  and state(directory/'transcript.json') == transcript_state
                  and cache_before == {path.name: state(path) for path in directory.glob('analysis-*.json')})
            write_json(output/'m1-manual-plan.json', loaded)
            manual_video = export(client, ident, 'm1-manual-vertical-3s',
                                  {'start': 0, 'end': 3, 'aspect': 'vertical', 'caption_style': 'none', 'reframe_id': plan['id']}, (1080, 1920))
            check('manual export does not retranscribe or regenerate face analysis', state(directory/'transcript.json') == transcript_state
                  and not (directory/'audio.wav').exists() and not (directory/'caption_alignment.json').exists())

            stage = 'genuine packaged Whisper transcription'
            asr_ident, asr_source = create_upload(client, 'Packaged actual local Whisper fixture')
            report['asr_project_id'] = asr_ident
            asr_directory = checked(PROFILE/'data'/asr_ident, PROFILE)
            check('actual ASR project begins without an imported transcript', not (asr_directory/'transcript.json').exists())
            began = time.monotonic()
            request(client, 'POST', f'/api/jobs/{asr_ident}/analyze', json={'duration_preset': '20', 'clip_count': 3, 'language': 'en'})
            asr_job, asr_states = wait_job(client, asr_ident, timeout=600)
            elapsed = time.monotonic()-began
            actual = request(client, 'GET', f'/api/jobs/{asr_ident}/transcript').json()
            words = [word for segment in actual['segments'] for word in segment.get('words', [])]
            text = ' '.join(segment['text'] for segment in actual['segments'])
            tokens = set(re.findall(r'[a-z]+', text.lower()))
            reference_tokens = set(re.findall(r'[a-z]+', reference_path.read_text(encoding='utf-8').lower()))
            recall = len(tokens & reference_tokens)/max(1, len(reference_tokens))
            check('packaged Whisper produces nonempty actual speech and bounded word timestamps', bool(words)
                  and all(0 <= word['start'] < word['end'] <= asr_source['media']['duration']+.02 for word in words)
                  and actual.get('word_timestamps') is True
                  and not json.loads((asr_directory/'job.json').read_text(encoding='utf-8')).get('_transcript_imported'))
            check('actual ASR shares substantial vocabulary with the independent TTS reference', recall >= .5,
                  reference_distinct_word_recall=round(recall, 4), recognized_words=len(words), seconds=round(elapsed, 3))
            check('actual ASR continues through the unchanged local heuristic path', asr_job.get('analysis_mode') == 'heuristic')
            write_json(output/'actual-asr-transcript.json', actual)
            report['asr'] = {'seconds': round(elapsed, 3), 'states': asr_states, 'recognized_words': len(words),
                             'reference_distinct_word_recall': recall,
                             'metric_notice': 'Distinct-word overlap with synthesized reference, not WER, natural speech accuracy, speaker identification or lip-sync accuracy.',
                             'candidate_count': len(asr_job.get('candidates') or [])}
            first = max(0, float(words[0]['start']))
            asr_video = export(client, asr_ident, 'actual-asr-poppins-native-3s',
                               {'start': first, 'end': first+3, 'aspect': 'original', 'caption_style': 'poppins_highlight'},
                               (asr_source['media']['width'], asr_source['media']['height']))

            stage = 'owned runtime process status'
            host.send({'op': 'status'})
            status = host.wait('status')
            observed = {role: process_identity(value['pid']) for role, value in child_identities.items()}
            descendants = service_descendants(child_identities)
            report['owned_runtime_status'] = status
            report['service_descendants'] = descendants
            listeners_owned = all(any(row.get('owned') is True and row.get('role') == role and row.get('pid') == value['pid']
                                       for row in status.get('listeners', {}).get(str(8300 if role == 'backend' else 3300), []))
                                  for role, value in child_identities.items())
            check('owned app processes retain the same bundled PIDs after processing',
                  status.get('pids') == {role: value['pid'] for role, value in child_identities.items()}
                  and observed == child_identities and listeners_owned
                  and status.get('active_processes', 0) >= len(child_identities))
            # Windows creates a private headless conhost for each console image.
            # It is an OS component, not a Python/Node development dependency.
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
            kernel.GetWindowsDirectoryW.restype = wintypes.UINT
            windows_buffer = ctypes.create_unicode_buffer(32768)
            count = kernel.GetWindowsDirectoryW(windows_buffer, len(windows_buffer))
            if not count or count >= len(windows_buffer):
                raise VerificationFailure('Could not identify the trusted Windows console host.')
            console_host = Path(windows_buffer.value)/'System32/conhost.exe'
            check('service descendants use packaged executables or the Windows console host',
                  all(Path(value['executable']).is_relative_to(package) or Path(value['executable']) == console_host
                      for value in descendants),
                  native_job_members=status['active_processes'], observed_descendants=len(descendants))

        stage = 'private frozen worker dispatch'
        worker_root = checked(PROFILE/'temp'/('desktop-' + run_id)/'worker-dispatch', PROFILE)
        worker_root.mkdir()
        worker_request = worker_root/'.worker.json'
        worker_request.write_text(json.dumps({'directory': str(worker_root), 'ffmpeg': str(package/'tools/ffmpeg.exe'),
                                             'node': str(package/'node/node.exe'), 'video_id': 'invalid'}), encoding='utf-8')
        worker = subprocess.run([str(package/'backend/PODCUTBackend.exe'), '--worker', 'ingestion', str(worker_request)],
                                cwd=worker_root, env=environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        worker_events = [json.loads(line) for line in worker.stdout.splitlines() if line.strip()]
        check('frozen ingestion dispatcher returns a controlled policy failure without recursive Python/server launch',
              worker.returncode == 1 and worker_events == [{'event': 'error', 'code': 'policy'}])

        stage = 'visual evidence'
        frames = []
        for label, video, at in [('m1-left', auto_video, 3), ('m1-right', auto_video, 9), ('m1-both', auto_video, 15),
                                 ('manual', manual_video, 1), ('poppins-import', poppins, 1), ('poppins-asr', asr_video, 1)]:
            target = checked(output/(label+'.png'), PROFILE)
            tool('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', at, '-i', video,
                            '-frames:v', '1', '-vf', 'scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2:color=0x151515', target])
            check(label + ' frame extracted from its actual MP4', target.is_file() and target.stat().st_size > 0)
            frames.append(target)
        sheet = checked(output/'contact-sheet.png', PROFILE)
        inputs = [item for path in frames for item in ('-i', path)]
        tool('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *inputs,
                        '-filter_complex', 'hstack=inputs=6[out]', '-map', '[out]', '-frames:v', '1', sheet])
        report['artifacts'] = {'contact_sheet': str(sheet), 'columns': ['M1 left', 'M1 right', 'M1 both', 'manual override', 'authored Poppins', 'actual ASR Poppins'],
                               'frames': list(map(str, frames))}
        check('visual contact sheet was created for human QA', sheet.is_file())
    except BaseException as error:
        failure = {'stage': stage, 'type': type(error).__name__, 'message': str(error)}
        report['failure'] = failure
        print('FAIL ' + stage + ': ' + redact(str(error)), flush=True)
    finally:
        cleanup = []
        if host is not None:
            try:
                report['host_shutdown'] = host.close()
                for role, original in child_identities.items():
                    observed = process_identity(original['pid'])
                    gone = observed is None or observed['created_ticks'] != original['created_ticks']
                    if not gone:
                        raise VerificationFailure('An owned packaged process remains after job shutdown: ' + role)
                closed = report['host_shutdown']
                if closed['exit_code'] != 0 or closed['forced_host_termination'] or not closed.get('closed_event', {}).get('clean'):
                    raise VerificationFailure('The packaged host did not complete a clean normal shutdown.')
                report['owned_children_gone'] = True
            except BaseException as error:
                cleanup.append({'stage': 'owned shutdown', 'type': type(error).__name__, 'message': str(error)})
        if config_expected_hash is not None:
            try:
                if sha(config_path) != config_expected_hash:
                    raise VerificationFailure('DEV config changed during QA; user changes were preserved and backup retained.')
                temporary = checked(config_path.with_name('backend.env.QA-restore-' + run_id + '.tmp'), PROFILE)
                temporary.write_bytes(config_original)
                temporary.replace(config_path)
                report['config_restored'] = sha(config_path) == hashlib.sha256(config_original).hexdigest()
                if not report['config_restored']:
                    raise VerificationFailure('The DEV config restoration checksum differs.')
            except BaseException as error:
                cleanup.append({'stage': 'config restoration', 'type': type(error).__name__, 'message': str(error)})
        if package_before is not None:
            try:
                package_after = inventory(app_root)
                write_json(output/'package-after.json', package_after)
                changed = sorted(path for path in set(package_before)|set(package_after) if package_before.get(path) != package_after.get(path))
                report['package_unchanged'] = not changed
                report['package_changed_paths'] = changed
                if changed:
                    raise VerificationFailure('The packaged runtime changed files inside its executable directory.')
            except BaseException as error:
                cleanup.append({'stage': 'package immutability', 'type': type(error).__name__, 'message': str(error)})
        try:
            free_dev_ports()
            report['dev_ports_released'] = True
        except BaseException as error:
            cleanup.append({'stage': 'DEV port release', 'type': type(error).__name__, 'message': str(error)})
        report['cleanup_errors'] = cleanup
        report['status'] = 'passed' if failure is None and not cleanup else 'failed'
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
        save()
        print(report['status'].upper() + ' packaged QA report: ' + str(report_path), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except VerificationFailure as error:
        print('FAIL preflight: ' + str(error), flush=True)
        raise SystemExit(1)
