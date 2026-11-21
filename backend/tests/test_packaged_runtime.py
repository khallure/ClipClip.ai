"""Phase 2B fail-closed frozen boundary and dispatch tests; no model downloads."""
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, process_runtime, runtime_profile
from app.main import create_app


@pytest.fixture
def package_layout(tmp_path, monkeypatch):
    project = tmp_path / 'independent-project'
    package = project / 'desktop' / 'dist' / 'test-app' / 'resources' / 'podcut'
    executable = package / 'backend' / 'PODCUTBackend.exe'
    resources = executable.parent / '_internal'
    resources.mkdir(parents=True)
    executable.write_bytes(b'non-executable test fixture')
    marker = {'schema': 1, 'profile_id': 'installer-dev', 'build': runtime_profile.PACKAGE_BUILD,
              'backend': 'backend/PODCUTBackend.exe'}
    (package / 'podcut-package.json').write_text(json.dumps(marker), encoding='utf-8')
    monkeypatch.setattr(runtime_profile, 'APPROVED_PROJECT', project)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(sys, '_MEIPASS', str(resources), raising=False)
    profile = tmp_path / 'independent-user-profile'
    monkeypatch.setattr(runtime_profile, 'packaged_profile', lambda _instance='': profile)
    for name in list(os.environ):
        if name in {'PODCUT_PROJECT_ROOT', 'PODCUT_PROFILE_ROOT', 'DATA_DIR', 'MODELS_DIR', 'CACHE_DIR',
                    'LOG_DIR', 'TEMP_DIR', 'RUNTIME_DIR', 'FFMPEG_PATH', 'FFPROBE_PATH', 'NODE_PATH'} or (name.startswith('PODCUT_') and name.endswith('_DIR')):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('PODCUT_PROFILE_ROOT', str(profile))
    monkeypatch.setenv('PODCUT_PACKAGED_ROOT', str(package))
    for name in ('_PYI_APPLICATION_HOME_DIR', '_PYI_ARCHIVE_FILE', '_PYI_PARENT_PROCESS_LEVEL'):
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(project=project, package=package, executable=executable,
                           resources=resources, marker=marker)


def test_frozen_layout_comes_from_exact_executable_and_marker(package_layout):
    assert runtime_profile.packaged_layout() == (package_layout.package, package_layout.resources)


@pytest.mark.parametrize('failure', ['env-root', 'marker-build', 'marker-backend', 'resource-root', 'bootloader-home', 'bootloader-archive'])
def test_frozen_redirects_fail_before_directory_writes(package_layout, monkeypatch, failure):
    layout = package_layout
    if failure == 'env-root':
        monkeypatch.setenv('PODCUT_PACKAGED_ROOT', str(layout.project))
    elif failure.startswith('marker-'):
        marker = dict(layout.marker)
        marker['build' if failure == 'marker-build' else 'backend'] = 'foreign'
        (layout.package / 'podcut-package.json').write_text(json.dumps(marker), encoding='utf-8')
    elif failure == 'resource-root':
        monkeypatch.setattr(sys, '_MEIPASS', str(layout.package / 'backend'))
    elif failure == 'bootloader-home':
        monkeypatch.setenv('_PYI_APPLICATION_HOME_DIR', str(layout.project))
    else:
        monkeypatch.setenv('_PYI_ARCHIVE_FILE', str(layout.package / 'backend' / 'foreign.exe'))
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: pytest.fail('A frozen redirect caused a write before rejection.'))
    with pytest.raises(runtime_profile.IsolationError):
        runtime_profile.packaged_layout()


def test_source_cannot_claim_a_packaged_root(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    monkeypatch.setenv('PODCUT_PACKAGED_ROOT', str(config.PROJECT / 'desktop'))
    with pytest.raises(runtime_profile.IsolationError):
        runtime_profile.packaged_layout()


def test_packaged_server_requires_launch_identity_before_writes(package_layout, monkeypatch):
    monkeypatch.delenv('PODCUT_PROJECT_ROOT', raising=False)
    monkeypatch.delenv('PODCUT_RUN_ID', raising=False)
    monkeypatch.delenv('PODCUT_RUN_TOKEN', raising=False)
    monkeypatch.setattr(sys, 'argv', [str(package_layout.executable), '--serve', '--run-id', str(uuid.uuid4())])
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: pytest.fail('An unauthenticated server wrote files.'))
    with pytest.raises(runtime_profile.IsolationError, match='launcher identity'):
        runtime_profile.load_profile()


def test_worker_manifest_redirect_rejected_before_profile_writes(package_layout, monkeypatch):
    monkeypatch.delenv('PODCUT_PROJECT_ROOT', raising=False)
    monkeypatch.delenv('PODCUT_RUN_ID', raising=False)
    monkeypatch.delenv('PODCUT_RUN_TOKEN', raising=False)
    monkeypatch.setattr(sys, 'argv', [str(package_layout.executable), '--worker', 'ingestion', 'D:/Unapproved-Phase2B/request.json'])
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: pytest.fail('An escaped worker request caused profile writes.'))
    with pytest.raises(runtime_profile.IsolationError, match='Worker request'):
        runtime_profile.load_profile()


def test_frozen_worker_uses_explicit_mode_and_no_python_module_fallback(package_layout, tmp_path, monkeypatch):
    runtime = replace(config.RUNTIME, package=package_layout.package, resources=package_layout.resources)
    monkeypatch.setattr(config, 'RUNTIME', runtime)
    request = tmp_path / '.worker.json'
    request.write_text('{}', encoding='utf-8')
    command = config.worker_command('ingestion', request)
    assert command == [str(package_layout.executable), '--worker', 'ingestion', str(request)]
    assert '-m' not in command
    with pytest.raises(runtime_profile.IsolationError):
        config.worker_command('arbitrary-module', request)
    with pytest.raises(runtime_profile.IsolationError):
        config.worker_command('ingestion', Path('D:/Unapproved-Phase2B/request.json'))


def test_frozen_child_environment_has_bundled_tools_and_validated_bootloader_state(package_layout, monkeypatch):
    runtime = replace(config.RUNTIME, package=package_layout.package, resources=package_layout.resources)
    monkeypatch.setenv('PODCUT_RUN_TOKEN', 'b' * 64)
    monkeypatch.setenv('PRIVATE_SECRET', 'synthetic-not-a-real-secret')
    monkeypatch.setenv('_PYI_APPLICATION_HOME_DIR', str(package_layout.resources))
    monkeypatch.setenv('_PYI_ARCHIVE_FILE', str(package_layout.executable))
    monkeypatch.setenv('_PYI_PARENT_PROCESS_LEVEL', '1')
    monkeypatch.setenv('_PYI_PRIVATE_SECRET', 'synthetic-not-a-real-secret')
    environment = runtime.environment()
    assert environment['_PYI_APPLICATION_HOME_DIR'] == str(package_layout.resources)
    assert environment['_PYI_ARCHIVE_FILE'] == str(package_layout.executable)
    assert environment['_PYI_PARENT_PROCESS_LEVEL'] == '1'
    assert not {'PODCUT_RUN_TOKEN', 'PRIVATE_SECRET', '_PYI_PRIVATE_SECRET', 'PYTHONHOME', 'PYTHONPATH'} & environment.keys()
    assert all(Path(part).is_relative_to(package_layout.package) for part in environment['PATH'].split(os.pathsep))
    assert '.venv' not in environment['PATH'] and '.tools' not in environment['PATH']
    monkeypatch.setenv('_PYI_APPLICATION_HOME_DIR', str(package_layout.project))
    with pytest.raises(runtime_profile.IsolationError):
        runtime.environment()


def test_packaged_binary_lookup_does_not_fall_back_to_development(package_layout, monkeypatch):
    runtime = replace(config.RUNTIME, package=package_layout.package, resources=package_layout.resources)
    monkeypatch.setattr(config, 'RUNTIME', runtime)
    for name in ('ffmpeg', 'ffprobe', 'node'):
        assert config.binary(name) is None
    executable = package_layout.package / 'tools' / 'ffmpeg.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'test fixture')
    assert config.binary('ffmpeg') == str(executable)
    monkeypatch.setattr(config, 'RUNTIME', replace(runtime, settings={**runtime.settings, 'FFMPEG_PATH': str(config.PROJECT / '.tools' / 'ffmpeg.exe')}))
    with pytest.raises(runtime_profile.IsolationError):
        config.binary('ffmpeg')


def test_packaged_api_requires_token_for_read_media_and_write(tmp_path, monkeypatch):
    token = 'a' * 64
    runtime = replace(config.RUNTIME, run_id=str(uuid.uuid4()), run_token=token,
                      package=config.PROJECT / 'desktop' / 'dist' / 'test-package')
    monkeypatch.setattr(config, 'RUNTIME', runtime)
    with TestClient(create_app(tmp_path / 'data')) as client:
        for path in ('/', '/docs', '/openapi.json', '/api/jobs', '/api/health', '/api/runtime-identity', '/api/jobs/' + 'a' * 32 + '/media'):
            assert client.get(path).status_code == 403
            assert client.get(path, headers={'x-podcut-run-token': 'wrong'}).status_code == 403
        assert client.post('/api/jobs', json={}).status_code == 403
        headers = {'x-podcut-run-token': token, 'Origin': 'http://127.0.0.1:3300'}
        response = client.post('/api/jobs', json={'title': 'Independent desktop fixture'}, headers=headers)
        assert response.status_code == 201
        assert token not in response.text
        assert client.get('/api/jobs', headers=headers).status_code == 200
        assert client.get('/api/jobs', headers={**headers, 'Origin': 'http://127.0.0.1:3000'}).status_code == 403
        identity = client.get('/api/runtime-identity', headers=headers).json()
        assert identity['build'] == runtime_profile.PACKAGE_BUILD and identity['packaged'] is True
        assert identity['packaged_root'] == str(runtime.package)


def test_face_seed_is_independent_and_never_replaces_existing_user_model(package_layout, tmp_path, monkeypatch):
    source = package_layout.resources / 'backend' / 'assets' / 'models' / runtime_profile.YUNET_NAME
    source.parent.mkdir(parents=True)
    source.write_bytes(b'independent synthetic model bytes - not used for inference')
    monkeypatch.setattr(runtime_profile, 'YUNET_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    model_root = tmp_path / 'models'
    paths = {**config.RUNTIME.paths, 'PODCUT_MODELS_DIR': model_root}
    runtime = replace(config.RUNTIME, paths=paths, package=package_layout.package, resources=package_layout.resources)
    target = model_root / 'reframe' / runtime_profile.YUNET_NAME
    runtime.seed_yunet()
    assert target.read_bytes() == source.read_bytes()
    assert not os.path.samefile(source, target)
    target.write_bytes(b'user-selected-model-must-not-be-overwritten')
    runtime.seed_yunet()
    assert target.read_bytes() == b'user-selected-model-must-not-be-overwritten'
    assert not list(target.parent.glob('*.tmp'))


def test_external_dll_directory_restored_even_if_process_creation_fails(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    class FakeAPI:
        directory = 'D:\\bundled-native-runtime'
        calls = []
        def GetDllDirectoryW(self, size, buffer):
            if buffer is not None:
                buffer.value = self.directory
            return len(self.directory)
        def SetDllDirectoryW(self, value):
            self.calls.append(value)
            self.directory = value or ''
            return 1
    api = FakeAPI()
    monkeypatch.setattr(process_runtime, '_windows_dll_api', lambda: api)
    def fail(*args, **kwargs):
        assert api.directory == ''
        raise OSError('controlled process failure')
    monkeypatch.setattr(process_runtime.subprocess, 'Popen', fail)
    with pytest.raises(OSError, match='controlled'):
        process_runtime.external_popen(['not-a-real-program'])
    assert api.calls == [None, 'D:\\bundled-native-runtime']
    assert api.directory == 'D:\\bundled-native-runtime'


def test_packaged_entry_rejects_python_module_and_unknown_worker_modes(monkeypatch):
    entry_file = config.PROJECT / 'desktop' / 'backend' / 'backend_entry.py'
    spec = importlib.util.spec_from_file_location('phase2b_entry_for_test', entry_file)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    for arguments in (['-m', 'app.main'], ['--worker', 'arbitrary-module', 'request.json'], ['--serve']):
        with pytest.raises(RuntimeError, match='Expected'):
            entry.main(arguments)


def test_independent_whisper_model_path_is_validated_without_download(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'MODELS_DIR', tmp_path / 'models')
    monkeypatch.setattr(config, 'WHISPER_MODEL', 'tiny.en')
    assert config.whisper_model_source() == 'tiny.en'
    directory = config.MODELS_DIR / 'whisper' / 'tiny.en'
    directory.mkdir(parents=True)
    for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'):
        (directory / name).write_bytes(b'independent test fixture')
    assert config.whisper_model_source() == str(directory)
    (directory / 'model.bin').write_bytes(b'')
    with pytest.raises(runtime_profile.IsolationError, match='incomplete'):
        config.whisper_model_source()
