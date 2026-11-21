"""Desktop job boundary tests; every fixture belongs in profile --basetemp.

NativeJob tests use a fake Win32 boundary. The Windows-only descendant test
starts the checked DEV Node executable, assigns it before resume, and cleans up
only its own native job. No PID scans, global tools, network, models or ports.
"""
import ctypes
import io
import json
import os
from pathlib import Path
import queue
import socket
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

from app import desktop_jobhost as host
from app.runtime_profile import IsolationError, PACKAGE_BUILD


RUN = 'b845245a-4279-4ebe-bf9a-85c753276f2e'
TOKEN = 'a' * 64
UI_TOKEN = 'b' * 64


@pytest.fixture
def layout(tmp_path):
    package, profile = tmp_path / 'desktop' / 'package', tmp_path / 'profile'
    for relative in ('backend/PODCUTBackend.exe', 'node/node.exe', 'frontend/server.js', 'tools/ffmpeg.exe', 'tools/ffprobe.exe'):
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'local fixture only')
    (package / 'backend' / '_internal').mkdir()
    (package / 'podcut-package.json').write_text(json.dumps({
        'schema': 1, 'profile_id': 'installer-dev', 'build': PACKAGE_BUILD, 'backend': 'backend/PODCUTBackend.exe'}), encoding='utf-8')
    return host.Layout(package, profile, RUN, package / 'backend/PODCUTBackend.exe',
                       package / 'node/node.exe', package / 'frontend/server.js')


def command(layout, role='backend'):
    environment = {'PODCUT_RUN_ID': RUN, 'PODCUT_RUN_TOKEN': TOKEN}
    if role == 'frontend':
        environment['PODCUT_UI_TOKEN'] = UI_TOKEN
    return {'op': 'start', 'id': role,
            'executable': str(layout.backend if role == 'backend' else layout.node),
            'args': ['--serve', '--run-id', RUN] if role == 'backend' else [str(layout.frontend)],
            'cwd': str(layout.temporary / 'backend' if role == 'backend' else layout.frontend.parent),
            'env': environment}


def test_layout_rejects_source_runtime_before_directory_creation(layout, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Preflight wrote a directory.'))
    with pytest.raises(host.JobHostError, match='packaged backend'):
        host.Layout.from_frozen(str(layout.package), str(layout.profile), RUN)


def test_layout_validates_actual_frozen_executable_and_marker(layout, monkeypatch):
    monkeypatch.setattr(host, 'APPROVED_PROJECT', layout.package.parents[1])
    monkeypatch.setattr(host, 'APPROVED_PROFILE', layout.profile)
    monkeypatch.setattr(host, 'packaged_profile', lambda _instance='': layout.profile)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(layout.backend))
    monkeypatch.setattr(sys, '_MEIPASS', str(layout.backend.parent / '_internal'), raising=False)
    assert not layout.profile.exists()
    assert host.Layout.from_frozen(str(layout.package), str(layout.profile), RUN) == layout
    assert not layout.profile.exists()
    marker = layout.package / 'podcut-package.json'
    marker.write_text('{"schema":1,"profile_id":"foreign"}', encoding='utf-8')
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Invalid marker wrote a directory.'))
    with pytest.raises(host.JobHostError, match='does not identify'):
        host.Layout.from_frozen(str(layout.package), str(layout.profile), RUN)


@pytest.mark.parametrize('field', ['executable', 'resources'])
def test_frozen_layout_cannot_point_at_a_different_native_file(layout, monkeypatch, field):
    monkeypatch.setattr(host, 'APPROVED_PROJECT', layout.package.parents[1])
    monkeypatch.setattr(host, 'APPROVED_PROFILE', layout.profile)
    monkeypatch.setattr(host, 'packaged_profile', lambda _instance='': layout.profile)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(layout.node if field == 'executable' else layout.backend))
    monkeypatch.setattr(sys, '_MEIPASS', str(layout.package / 'frontend' if field == 'resources' else layout.backend.parent / '_internal'), raising=False)
    with pytest.raises(host.JobHostError):
        host.Layout.from_frozen(str(layout.package), str(layout.profile), RUN)


@pytest.mark.parametrize('change', ['exe', 'args', 'cwd', 'role', 'extra-field'])
def test_unapproved_command_rejected_before_writes(layout, monkeypatch, change):
    policy = host.ServicePolicy(layout, Path('C:/Windows'))
    request = command(layout)
    if change == 'exe':
        request['executable'] = str(layout.node)
    elif change == 'args':
        request['args'] = ['--worker', 'anything']
    elif change == 'cwd':
        request['cwd'] = str(layout.profile / 'config')
    elif change == 'role':
        request['id'] = 'ffmpeg'
    else:
        request['debug'] = True
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Invalid command wrote a directory.'))
    with pytest.raises(host.JobHostError):
        policy.prepare(request)


@pytest.mark.parametrize('name,value', [
    ('NODE_OPTIONS', '--require=private-injection.cjs'), ('PYTHONPATH', 'C:/unapproved'),
    ('OPENAI_API_KEY', 'dummy-secret'), ('HTTP_PROXY', 'http://127.0.0.1:9'),
    ('DATA_DIR', 'C:/unapproved'), ('PATH', 'C:/unapproved'),
    ('PODCUT_PACKAGED_ROOT', 'C:/unapproved'), ('PODCUT_RUN_ID', str(uuid.uuid4())),
    ('_PYI_APPLICATION_HOME_DIR', 'C:/unapproved'),
])
def test_environment_injection_refused_without_creating_paths(layout, monkeypatch, name, value):
    request = command(layout)
    request['env'][name] = value
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Environment preflight wrote a directory.'))
    with pytest.raises((host.JobHostError, IsolationError)):
        host.ServicePolicy(layout, Path('C:/Windows')).prepare(request)


def test_environment_scope_credentials_and_independent_ui_token(layout):
    policy = host.ServicePolicy(layout, Path('C:/Windows'))
    backend = policy.prepare(command(layout))
    assert backend.environment['PODCUT_RUN_TOKEN'] == TOKEN
    assert 'PODCUT_UI_TOKEN' not in backend.environment
    assert TOKEN not in repr(backend)
    assert backend.environment['PATH'].split(os.pathsep) == [str(layout.package / 'backend'), str(layout.package / 'node'),
                                                            str(layout.package / 'tools'), str(Path('C:/Windows/System32')), str(Path('C:/Windows'))]
    assert Path(backend.environment['USERPROFILE']).is_relative_to(layout.profile)
    assert Path(backend.environment['APPDATA']).is_relative_to(layout.profile)
    assert Path(backend.environment['LOCALAPPDATA']).is_relative_to(layout.profile)
    policy.commit(backend)
    with pytest.raises(host.JobHostError, match='already started'):
        policy.prepare(command(layout))
    frontend = policy.prepare(command(layout, 'frontend'))
    assert frontend.environment['PODCUT_UI_TOKEN'] == UI_TOKEN
    assert 'NODE_PATH' not in frontend.environment
    assert all(Path(frontend.environment[key]).is_relative_to(layout.profile)
               for key in ('DATA_DIR', 'TEMP', 'TMP', 'HF_HOME', 'TORCH_HOME', 'PODCUT_NEXT_CACHE_DIR'))


def test_cross_run_token_or_reused_ui_credential_is_rejected(layout):
    policy = host.ServicePolicy(layout, Path('C:/Windows'))
    policy.commit(policy.prepare(command(layout)))
    request = command(layout, 'frontend')
    request['env']['PODCUT_RUN_TOKEN'] = 'c' * 64
    with pytest.raises(host.JobHostError, match='same authenticated run'):
        policy.prepare(request)
    request = command(layout, 'frontend')
    request['env']['PODCUT_UI_TOKEN'] = TOKEN
    with pytest.raises(host.JobHostError, match='not independent'):
        policy.prepare(request)


def test_default_windows_pathext_is_sanitized_not_executed(layout):
    request = command(layout)
    request['env']['PATHEXT'] = '.COM;.EXE;.BAT;.CMD;.VBS;.JS'
    prepared = host.ServicePolicy(layout, Path('C:/Windows')).prepare(request)
    assert prepared.environment['PATHEXT'] == '.COM;.EXE;.BAT;.CMD'


def test_host_bootloader_state_is_checked_and_never_forwarded_to_node(layout, monkeypatch):
    monkeypatch.setenv('_PYI_APPLICATION_HOME_DIR', str(layout.backend.parent / '_internal'))
    monkeypatch.setenv('_PYI_ARCHIVE_FILE', str(layout.backend))
    policy = host.ServicePolicy(layout, Path('C:/Windows'))
    backend = policy.prepare(command(layout))
    assert backend.environment['_PYI_ARCHIVE_FILE'] == str(layout.backend)
    frontend = policy.prepare(command(layout, 'frontend'))
    assert not any(key.startswith('_PYI_') for key in frontend.environment)
    monkeypatch.setenv('_PYI_APPLICATION_HOME_DIR', 'C:/unapproved')
    with pytest.raises(host.JobHostError, match='resource state'):
        policy.prepare(command(layout))


@pytest.mark.parametrize('raw', [b'{}', b'{broken}\n', b'\xff\n', b'x' * (host.MAX_COMMAND_BYTES + 1)])
def test_input_rejects_partial_malformed_and_oversized_records(raw):
    inbox = queue.Queue()
    host.input_commands(io.BytesIO(raw), inbox)
    kind, message = inbox.get_nowait()
    assert kind == 'error' and len(message) < 100
    assert TOKEN not in message


def test_input_only_yields_commands_then_eof():
    inbox = queue.Queue()
    host.input_commands(io.BytesIO(b'{"op":"status"}\n{"op":"status","nonce":"second"}\n'), inbox)
    assert [inbox.get_nowait() for _ in range(3)] == [
            ('command', {'op': 'status'}), ('command', {'op': 'status', 'nonce': 'second'}), ('eof', None)]


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.active_count = 0
        self.exit_codes = {}
        self.rows = []
        self.membership = True

    def clear_dll_directory(self):
        self.calls.append(('dll',))

    def windows_directory(self):
        return Path('C:/Windows')

    def create_job(self):
        self.calls.append(('job',))
        return 'only-created-job'

    def spawn(self, job, service):
        self.calls.append(('spawn', job, service.role))
        self.active_count += 1
        return host.NativeProcess(6000 + self.active_count, 'created-' + service.role)

    def exit_code(self, handle):
        return self.exit_codes.get(handle)

    def active(self, job):
        self.calls.append(('active', job))
        return self.active_count

    def tcp_rows(self):
        return self.rows

    def in_job(self, process, job):
        return self.membership

    def terminate(self, job):
        self.calls.append(('terminate', job))
        self.active_count = 0

    def close(self, handle):
        self.calls.append(('close', handle))


def test_job_shutdown_uses_only_owned_handles_and_cleans_descendants(layout):
    api = FakeAPI()
    job = host.NativeJob(api)
    policy = host.ServicePolicy(layout, Path('C:/Windows'))
    assert job.start(policy.prepare(command(layout))) == 6001
    assert job.start(policy.prepare(command(layout, 'frontend'))) == 6002
    api.active_count = 5  # Two service roots plus three owned descendants.
    assert job.status()['active_processes'] == 5
    assert job.shutdown() == 0
    assert api.calls[-5:] == [('terminate', 'only-created-job'), ('active', 'only-created-job'),
                             ('close', 'only-created-job'), ('close', 'created-backend'), ('close', 'created-frontend')]
    assert job.processes == {} and job.handle is None
    assert job.shutdown() == 0


def test_job_query_failure_still_closes_kill_on_close_job():
    api = FakeAPI()
    job = host.NativeJob(api)
    api.active = lambda _job: (_ for _ in ()).throw(host.JobHostError('query fixture failure'))
    with pytest.raises(host.JobHostError, match='query fixture'):
        job.shutdown()
    assert ('close', 'only-created-job') in api.calls
    assert job.handle is None


def tcp_row(port, pid, *, state=2, remote_port=0, loopback=True):
    return {'port': port, 'pid': pid, 'state': state, 'remote_port': remote_port,
            'local_loopback': loopback, 'remote_loopback': loopback}


def test_listener_and_pinned_connection_proofs_require_exact_live_role_and_job(layout):
    api = FakeAPI()
    job = host.NativeJob(api)
    policy = host.ServicePolicy(layout, Path('C:/Windows'))
    job.start(policy.prepare(command(layout)))
    job.start(policy.prepare(command(layout, 'frontend')))
    api.rows = [tcp_row(8300, 6001), tcp_row(3300, 6002), tcp_row(3300, 9999),
                tcp_row(8300, 6001, state=5, remote_port=51000)]
    try:
        status = job.status({'port': 8300, 'client_port': 51000})
        assert status['listeners']['8300'] == [{'port': 8300, 'pid': 6001, 'owned': True, 'role': 'backend'}]
        assert status['listeners']['3300'][1] == {'port': 3300, 'pid': 9999, 'owned': False, 'role': None}
        assert status['connection'] == {'port': 8300, 'client_port': 51000, 'pid': 6001, 'owned': True, 'role': 'backend'}
        api.membership = False
        assert not job.status({'port': 8300, 'client_port': 51000})['connection']['owned']
        api.membership = True
        api.exit_codes['created-backend'] = 0
        assert not job.status({'port': 8300, 'client_port': 51000})['connection']['owned']
    finally:
        job.shutdown()


@pytest.mark.parametrize('bad', ['foreign-socket', 'different-client-port', 'nonloopback', 'not-established'])
def test_owned_listener_does_not_authorize_a_different_established_connection(layout, bad):
    api = FakeAPI()
    job = host.NativeJob(api)
    job.start(host.ServicePolicy(layout, Path('C:/Windows')).prepare(command(layout)))
    row = tcp_row(8300, 6001, state=5, remote_port=51000)
    if bad == 'foreign-socket':
        row['pid'] = 9999
    elif bad == 'different-client-port':
        row['remote_port'] = 51001
    elif bad == 'nonloopback':
        row['remote_loopback'] = False
    else:
        row['state'] = 8
    api.rows = [tcp_row(8300, 6001), row]
    try:
        status = job.status({'port': 8300, 'client_port': 51000})
        assert status['listeners']['8300'][0]['owned']
        assert not status['connection']['owned']
    finally:
        job.shutdown()


def table_api(payloads):
    def capture(buffer, size, _ordered, family, table_class, _reserved):
        assert table_class == 5  # OWNER_PID_ALL, needed for an established socket proof.
        payload = payloads[family]
        size._obj.value = len(payload)
        if buffer is None:
            return 122
        ctypes.memmove(buffer, payload, len(payload))
        return 0
    api = host.WindowsAPI.__new__(host.WindowsAPI)
    api.iphelper = SimpleNamespace(GetExtendedTcpTable=capture)
    return api


def test_native_tcp_parser_filters_other_ports_and_reads_ipv4_ipv6_loopback():
    loop4 = int.from_bytes(b'\x7f\x00\x00\x01', 'little')
    rows4 = [host._TCP4Row(5, loop4, socket.htons(8300), loop4, socket.htons(51000), 6001),
             host._TCP4Row(2, loop4, socket.htons(3000), 0, 0, 9999),
             host._TCP4Row(2, loop4, socket.htons(8000), 0, 0, 9998)]
    row6 = host._TCP6Row()
    row6.local_address[:] = b'\0' * 10 + b'\xff\xff\x7f\x00\x00\x01'
    row6.remote_address[:] = b'\0' * 10 + b'\xff\xff\x7f\x00\x00\x01'
    row6.local_port, row6.remote_port, row6.state, row6.pid = socket.htons(3300), socket.htons(51001), 5, 6002
    api = table_api({2: len(rows4).to_bytes(4, 'little') + b''.join(bytes(row) for row in rows4),
                     23: (1).to_bytes(4, 'little') + bytes(row6)})
    assert api.tcp_rows() == [tcp_row(8300, 6001, state=5, remote_port=51000),
                              tcp_row(3300, 6002, state=5, remote_port=51001)]


def test_native_tcp_table_rejects_invalid_row_bounds():
    api = table_api({2: (100).to_bytes(4, 'little'), 23: (0).to_bytes(4, 'little')})
    with pytest.raises(host.JobHostError, match='row bounds'):
        api.tcp_rows()


def test_status_protocol_correlates_nonce_and_denies_unmatched_socket(layout):
    api, output = FakeAPI(), io.StringIO()
    messages = [command(layout), {'op': 'status', 'nonce': 'socket-proof-1', 'connection': {'port': 8300, 'client_port': 51000}}, {'op': 'shutdown'}]
    raw = b''.join(json.dumps(message).encode() + b'\n' for message in messages)
    assert host.serve(layout, api, io.BytesIO(raw), output) == 0
    status = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line)['event'] == 'status')
    assert status['nonce'] == 'socket-proof-1'
    assert status['listeners'] == {'3300': [], '8300': []}
    assert not status['connection']['owned']
    assert status['connection']['pid'] is None


@pytest.mark.parametrize('connection', [{'port': 3000, 'client_port': 51000}, {'port': 8300, 'client_port': 0},
                                       {'port': 8300, 'client_port': True}, {'port': 8300, 'client_port': 51000, 'pid': 9999}])
def test_status_cannot_query_other_ports_or_supply_process_authority(layout, connection):
    api, output = FakeAPI(), io.StringIO()
    raw = json.dumps({'op': 'status', 'nonce': 'bad-proof', 'connection': connection}).encode() + b'\n'
    assert host.serve(layout, api, io.BytesIO(raw), output) == 1
    assert any(json.loads(line)['event'] == 'error' for line in output.getvalue().splitlines())


def test_control_protocol_eof_stops_created_job_and_does_not_echo_token(layout):
    api, output = FakeAPI(), io.StringIO()
    data = json.dumps(command(layout)).encode('utf-8') + b'\n'
    assert host.serve(layout, api, io.BytesIO(data), output) == 0
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event['event'] for event in events] == ['ready', 'started', 'closed']
    assert events[-1]['clean'] and events[-1]['active_processes'] == 0
    assert TOKEN not in output.getvalue() and 'env' not in output.getvalue()
    assert ('terminate', 'only-created-job') in api.calls


def test_invalid_control_request_closes_job_without_starting_anything(layout):
    api, output = FakeAPI(), io.StringIO()
    raw = json.dumps({'op': 'start', 'env': {'private': TOKEN}}).encode() + b'\n'
    assert host.serve(layout, api, io.BytesIO(raw), output) == 1
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event['event'] for event in events] == ['ready', 'error', 'closed']
    assert not any(call[0] == 'spawn' for call in api.calls)
    assert TOKEN not in output.getvalue()


def test_shutdown_reader_returns_without_waiting_for_parent_eof():
    class StillOpenInput:
        def __init__(self):
            self.calls = 0

        def readline(self, _maximum):
            self.calls += 1
            assert self.calls == 1, 'Shutdown must not start another blocking read.'
            return b'{"op":"shutdown"}\n'

    stream, inbox = StillOpenInput(), queue.Queue()
    host.input_commands(stream, inbox)
    assert inbox.get_nowait() == ('command', {'op': 'shutdown'})
    assert stream.calls == 1


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Objects require Windows.')
def test_actual_scoped_node_descendants_are_stopped_by_their_job(tmp_path):
    node = host.checked_path(host.APPROVED_PROJECT / '.tools/node/node.exe', host.APPROVED_PROJECT, 'DEV test Node')
    if not node.is_file():
        pytest.skip('The independent DEV Node fixture is unavailable.')
    host.checked_path(tmp_path, host.APPROVED_PROFILE, 'Job test temporary directory')
    script = tmp_path / 'owned-job-descendants.cjs'
    script.write_text("const {spawn}=require('node:child_process');spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore',windowsHide:true});setInterval(()=>{},1000);", encoding='utf-8')
    api = host.WindowsAPI()
    job = host.NativeJob(api)
    environment = {'SYSTEMROOT': str(api.windows_directory()), 'WINDIR': str(api.windows_directory()),
                   'TEMP': str(tmp_path), 'TMP': str(tmp_path), 'PATH': str(node.parent)}
    service = host.Service('backend', node, (str(script),), tmp_path, environment,
                           tmp_path / 'owned.stdout.log', tmp_path / 'owned.stderr.log')
    try:
        pid = job.start(service)
        assert pid > 0
        deadline = time.monotonic() + 10
        while job.status()['active_processes'] < 2 and time.monotonic() < deadline:
            time.sleep(.025)
        assert job.status()['active_processes'] == 2
        assert job.shutdown() == 0
    finally:
        job.shutdown()


@pytest.mark.skipif(os.name != 'nt', reason='Windows TCP owner proofs require Windows.')
def test_actual_pinned_socket_proof_accepts_owned_node_and_rejects_foreign_job(tmp_path):
    """Only port 3300; both sentinels are independently owned cleanup fixtures.

    The first node keeps running after releasing its listener. A node belonging
    to a different job then occupies the same port. A live role record alone
    must not authorize that foreign established socket.
    """
    node = host.checked_path(host.APPROVED_PROJECT / '.tools/node/node.exe', host.APPROVED_PROJECT, 'DEV socket test Node')
    if not node.is_file():
        pytest.skip('The independent DEV Node fixture is unavailable.')
    host.checked_path(tmp_path, host.APPROVED_PROFILE, 'Socket proof test temporary directory')
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', 3300))
        except OSError:
            pytest.skip('DEV port 3300 is occupied; its owner was left untouched.')
    finally:
        probe.close()
    script = tmp_path / 'owned-socket-proof.cjs'
    script.write_text("""const fs=require('node:fs');const net=require('node:net');
const [ready,close,closed]=process.argv.slice(2);
const server=net.createServer(socket=>socket.on('error',()=>{}));
server.on('error',error=>{console.error(error.code);process.exit(2)});
server.listen({host:'127.0.0.1',port:3300,exclusive:true},()=>fs.writeFileSync(ready,'ready'));
let stopping=false;setInterval(()=>{if(!stopping&&fs.existsSync(close)){stopping=true;server.close(()=>fs.writeFileSync(closed,'closed'));}},20);
""", encoding='utf-8')
    api = host.WindowsAPI()
    own, foreign = host.NativeJob(api), host.NativeJob(api)
    connections = []
    environment = {'SYSTEMROOT': str(api.windows_directory()), 'WINDIR': str(api.windows_directory()),
                   'TEMP': str(tmp_path), 'TMP': str(tmp_path), 'PATH': str(node.parent)}

    def start(job, label):
        ready, close, closed = (tmp_path / (label + suffix) for suffix in ('.ready', '.close', '.closed'))
        service = host.Service('frontend', node, (str(script), str(ready), str(close), str(closed)), tmp_path,
                               environment, tmp_path / (label + '.stdout.log'), tmp_path / (label + '.stderr.log'))
        pid = job.start(service)
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert api.exit_code(job.processes['frontend'].handle) is None, 'The scoped sentinel exited before listening.'
            time.sleep(.02)
        assert ready.is_file(), 'The scoped listener did not start in time.'
        return pid, close, closed

    def connect_without_headers():
        connection = socket.create_connection(('127.0.0.1', 3300), timeout=3)
        connections.append(connection)
        return connection, {'port': 3300, 'client_port': connection.getsockname()[1]}

    try:
        own_pid, close_file, closed_file = start(own, 'owned')
        connection, query = connect_without_headers()
        proof = own.status(query)
        assert proof['connection'] == {**query, 'pid': own_pid, 'owned': True, 'role': 'frontend'}
        assert proof['listeners']['3300'] == [{'port': 3300, 'pid': own_pid, 'owned': True, 'role': 'frontend'}]
        connection.close()
        close_file.write_text('close the owned listener only', encoding='utf-8')
        deadline = time.monotonic() + 10
        while not closed_file.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert closed_file.is_file(), 'The owned sentinel did not release its listener.'
        assert api.exit_code(own.processes['frontend'].handle) is None
        foreign_pid, _, _ = start(foreign, 'foreign')
        _, query = connect_without_headers()
        proof = own.status(query)
        assert proof['connection'] == {**query, 'pid': foreign_pid, 'owned': False, 'role': None}
        assert proof['listeners']['3300'] == [{'port': 3300, 'pid': foreign_pid, 'owned': False, 'role': None}]
    finally:
        for connection in connections:
            connection.close()
        # No occupant PID is ever killed; only these two explicit job handles.
        try:
            assert foreign.shutdown() == 0
        finally:
            assert own.shutdown() == 0
