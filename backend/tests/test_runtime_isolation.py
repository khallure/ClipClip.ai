"""Phase 2A and Stage 2A Multi-PC workspace boundaries; run with --basetemp inside the isolated profile/temp."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import os
import stat
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, lab_profile, quality_dev_profile, runtime_profile
from app.main import create_app
from app.store import JobStore


def _write_workspace_marker(checkout: Path, payload: object = None) -> Path:
    checkout.mkdir(parents=True, exist_ok=True)
    marker = checkout / lab_profile.WORKSPACE_MARKER
    data = {'schema': 1, 'kind': lab_profile.WORKSPACE_KIND} if payload is None else payload
    marker.write_text(json.dumps(data), encoding='utf-8')
    return marker


def _write_owner_marker(checkout: Path, profile: Path | None = None, **overrides: object) -> Path:
    target_profile = (checkout / '.dev' / 'profile') if profile is None else profile
    owner_file = target_profile / lab_profile.OWNER_MARKER
    owner_file.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        'schema': 1,
        'workspace_id': str(uuid.uuid4()),
        'project_root': str(checkout),
        'profile_root': str(target_profile),
    }
    payload.update(overrides)
    owner_file.write_text(json.dumps(payload), encoding='utf-8')
    return owner_file


def test_profile_identity_and_paths():
    expected_project = Path(__file__).resolve().parents[2]
    assert config.PROJECT == expected_project == lab_profile.APPROVED_PROJECT
    assert (config.PROJECT / lab_profile.WORKSPACE_MARKER).is_file()
    assert lab_profile.resolve_workspace(expected_project) == (
        expected_project,
        expected_project / '.dev' / 'profile',
    )
    assert config.RUNTIME.root == runtime_profile.APPROVED_PROFILE
    assert config.RUNTIME.root.is_relative_to(config.PROJECT)
    if os.name == 'nt':
        local_appdata = runtime_profile.windows_local_appdata()
        assert not config.RUNTIME.root.is_relative_to(local_appdata)
        assert not local_appdata.is_relative_to(config.RUNTIME.root)
    assert config.DATA_DIR == config.RUNTIME.root / 'data'
    assert config.MODELS_DIR == config.RUNTIME.root / 'models'
    assert Path(tempfile.gettempdir()).is_relative_to(config.RUNTIME.root)
    assert config.ai_configuration().provider == 'heuristic'
    assert not config.ai_configuration().api_key
    assert all(path.is_relative_to(config.RUNTIME.root) for path in config.RUNTIME.paths.values())


def test_multipc_workspace_accepts_spaced_paths_and_valid_owner_marker(tmp_path):
    checkout = tmp_path / 'PODCUT-Stage 1' / 'PODCUT MultiPC Clone'
    _write_workspace_marker(checkout)
    expected_profile = checkout / '.dev' / 'profile'
    assert lab_profile.resolve_workspace(checkout) == (checkout, expected_profile)
    assert not expected_profile.exists()

    (expected_profile / 'config').mkdir(parents=True)
    assert lab_profile.resolve_workspace(checkout) == (checkout, expected_profile)

    _write_owner_marker(checkout)
    (expected_profile / 'config' / 'backend.env').write_text('AI_PROVIDER=heuristic\n', encoding='utf-8')
    assert lab_profile.resolve_workspace(checkout) == (checkout, expected_profile)


@pytest.mark.parametrize(
    'invalid_marker',
    [
        'missing',
        'not-json',
        [],
        {'schema': 2, 'kind': 'podcut-multipc-dev'},
        {'schema': True, 'kind': 'podcut-multipc-dev'},
        {'schema': '1', 'kind': 'podcut-multipc-dev'},
        {'schema': 1, 'kind': 'podcut-installer-dev'},
        {'schema': 1, 'kind': 'phase3a'},
        'oversized',
        'reparse',
        'hardlink',
    ],
)
def test_workspace_without_valid_multipc_marker_is_rejected(tmp_path, monkeypatch, invalid_marker):
    checkout = tmp_path / 'Unmarked Or Invalid Workspace'
    checkout.mkdir(parents=True)
    marker = checkout / lab_profile.WORKSPACE_MARKER
    if invalid_marker == 'not-json':
        marker.write_text('{invalid', encoding='utf-8')
    elif invalid_marker == 'oversized':
        marker.write_text('{"schema":1,"kind":"podcut-multipc-dev","pad":"' + ('x' * 17000) + '"}', encoding='utf-8')
    elif invalid_marker in ('reparse', 'hardlink'):
        _write_workspace_marker(checkout)
        original_lstat = Path.lstat

        def fake_lstat(path, *args, **kwargs):
            if path == marker:
                attrs = stat.FILE_ATTRIBUTE_REPARSE_POINT if invalid_marker == 'reparse' else 0
                links = 2 if invalid_marker == 'hardlink' else 1
                return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=attrs, st_nlink=links, st_size=46)
            return original_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'lstat', fake_lstat)
    elif invalid_marker != 'missing':
        _write_workspace_marker(checkout, invalid_marker)

    with pytest.raises(lab_profile.WorkspaceError):
        lab_profile.resolve_workspace(checkout)


def test_clone_profiles_and_installed_profiles_never_mix(tmp_path, monkeypatch):
    clone_a = tmp_path / 'PC A Clone'
    clone_b = tmp_path / 'PC B Clone'
    outside_profile = config.PROJECT / 'synthetic-outside-profile' / runtime_profile.APP_DIRECTORY
    assert outside_profile.is_relative_to(config.PROJECT)
    assert not outside_profile.is_relative_to(config.RUNTIME.root)
    assert not outside_profile.exists()
    _write_workspace_marker(clone_a)
    _write_workspace_marker(clone_b)
    _write_owner_marker(clone_a)
    _write_owner_marker(clone_b)

    project_a, profile_a = lab_profile.resolve_workspace(clone_a)
    project_b, profile_b = lab_profile.resolve_workspace(clone_b)
    assert project_a != project_b and profile_a != profile_b
    assert not profile_a.is_relative_to(project_b) and not profile_b.is_relative_to(project_a)

    unowned = tmp_path / 'Unowned Profile Clone'
    _write_workspace_marker(unowned)
    unowned_env = unowned / '.dev' / 'profile' / 'config' / 'backend.env'
    unowned_env.parent.mkdir(parents=True)
    unowned_env.write_text('AI_PROVIDER=heuristic\n', encoding='utf-8')
    with pytest.raises(lab_profile.WorkspaceError, match='ownership marker'):
        lab_profile.resolve_workspace(unowned)

    for bad_overrides in (
        {'project_root': str(clone_b)},
        {'profile_root': str(profile_b)},
        {'profile_root': str(outside_profile)},
        {'workspace_id': 'NOT-A-LOWERCASE-UUID'},
        {'schema': 2},
    ):
        _write_owner_marker(clone_a, **bad_overrides)
        with pytest.raises(lab_profile.WorkspaceError):
            lab_profile.resolve_workspace(clone_a)

    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: pytest.fail('mkdir occurred before foreign profile rejection'))
    valid_data_dir = tmp_path / 'data'
    assert valid_data_dir.is_relative_to(config.RUNTIME.root)
    monkeypatch.setenv('DATA_DIR', str(valid_data_dir))
    loaded = runtime_profile.load_profile()
    assert loaded.root == config.RUNTIME.root
    assert loaded.paths['DATA_DIR'] == config.RUNTIME.root / 'data'
    monkeypatch.delenv('DATA_DIR', raising=False)

    for env_name, foreign_target in (
        ('PODCUT_PROJECT_ROOT', clone_b),
        ('PODCUT_PROFILE_ROOT', profile_b),
        ('PODCUT_PROFILE_ROOT', outside_profile),
        ('DATA_DIR', outside_profile / 'data'),
        ('DATA_DIR', config.PROJECT / 'backend' / 'data'),
    ):
        monkeypatch.setenv(env_name, str(foreign_target))
        with pytest.raises(runtime_profile.IsolationError):
            runtime_profile.load_profile()
        monkeypatch.delenv(env_name, raising=False)
    assert not outside_profile.exists()


def test_quality_upgrade_qa_profile_is_dynamic_and_isolated_from_installed_profiles(tmp_path):
    build_id = str(uuid.uuid4())
    instance_id = str(uuid.uuid4())
    installed_exe = (
        tmp_path
        / 'Installed Phase 3A'
        / 'PODCUTAI-InstallerDev-win32-x64'
        / 'resources'
        / 'podcut'
        / 'backend'
        / 'PODCUTBackend.exe'
    )
    assert quality_dev_profile.quality_profile(installed_exe, instance_id) is None

    checkout = tmp_path / 'PODCUT-Stage 1' / 'PODCUT-MultiPC-Dev'
    _write_workspace_marker(checkout)
    run_dir = checkout / 'quality-upgrade' / 'runs' / build_id
    qa_exe = run_dir.joinpath(*quality_dev_profile.EXECUTABLE_LAYOUT)
    qa_profile = quality_dev_profile.quality_profile(qa_exe, instance_id)
    assert qa_profile == run_dir / runtime_profile.APP_DIRECTORY / 'profiles' / instance_id
    assert qa_profile.is_relative_to(run_dir)
    assert not qa_profile.is_relative_to(checkout / '.dev' / 'profile')

    with pytest.raises(runtime_profile.IsolationError):
        quality_dev_profile.quality_profile(qa_exe, '')
    with pytest.raises(runtime_profile.IsolationError):
        quality_dev_profile.quality_profile(run_dir / 'unexpected' / 'PODCUTBackend.exe', instance_id)

    unmarked = tmp_path / 'Unmarked Quality Root'
    unmarked_exe = (
        unmarked / 'quality-upgrade' / 'runs' / build_id
    ).joinpath(*quality_dev_profile.EXECUTABLE_LAYOUT)
    with pytest.raises(runtime_profile.IsolationError, match='workspace marker'):
        quality_dev_profile.quality_profile(unmarked_exe, instance_id)


@pytest.mark.parametrize('name', ['DATA_DIR', 'MODELS_DIR', 'PODCUT_CACHE_DIR', 'PODCUT_RUNTIME_DIR', 'FFMPEG_PATH', 'FFPROBE_PATH', 'NODE_PATH'])
def test_inherited_application_path_escape_rejected_before_writes(monkeypatch, name):
    monkeypatch.setenv(name, 'D:/Unapproved-Phase2A/runtime')
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: pytest.fail('mkdir occurred before validation'))
    with pytest.raises(runtime_profile.IsolationError):
        runtime_profile.load_profile()


@pytest.mark.parametrize('name', ['DATA_DIR', 'PODCUT_MODELS_DIR', 'PODCUT_CACHE_DIR', 'TEMP', 'TORCH_HOME', 'HF_HOME', 'FFMPEG_PATH'])
def test_canonical_config_escape_rejected_before_writes(monkeypatch, name):
    monkeypatch.setattr(runtime_profile, 'dotenv_values', lambda *a, **k: {name: 'D:/Unapproved-Phase2A/runtime'})
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: pytest.fail('mkdir occurred before validation'))
    with pytest.raises(runtime_profile.IsolationError):
        runtime_profile.load_profile()


def test_host_defaults_and_cloud_credentials_are_overridden(monkeypatch):
    monkeypatch.setenv('TEMP', 'D:/Unapproved-Phase2A/temp')
    monkeypatch.setenv('HF_HOME', 'D:/Unapproved-Phase2A/cache')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-not-a-real-key')
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-not-a-real-key')
    monkeypatch.setenv('AI_PROVIDER', 'openai')
    profile = runtime_profile.load_profile()
    environment = profile.environment()
    assert environment['TEMP'] == str(profile.root / 'temp')
    assert Path(environment['HF_HOME']).is_relative_to(profile.root)
    assert environment['AI_PROVIDER'] == 'heuristic'
    assert not {'OPENAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'} & environment.keys()


def test_child_environment_has_only_isolated_paths_and_no_secrets(monkeypatch):
    monkeypatch.setenv('PYTHONPATH', 'D:/Unapproved-Phase2A/python')
    monkeypatch.setenv('PRIVATE_SECRET', 'synthetic-value')
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'synthetic-value')
    monkeypatch.setenv('PATH', 'D:/Unapproved-Phase2A/tools')
    monkeypatch.setenv('PODCUT_RUN_TOKEN', 'a' * 64)
    environment = config.child_environment()
    assert not {'PYTHONPATH', 'PYTHONHOME', 'PRIVATE_SECRET', 'AWS_ACCESS_KEY_ID', 'PODCUT_RUN_TOKEN'} & environment.keys()
    assert all(Path(value).is_relative_to(config.PROJECT) or Path(value).is_relative_to(config.RUNTIME.root)
               for value in environment['PATH'].split(os.pathsep))
    assert environment['HF_HUB_OFFLINE'] == '1'


def test_no_global_tool_lookup(monkeypatch):
    monkeypatch.setenv('PATH', 'D:/Unapproved-Phase2A/tools')
    monkeypatch.setenv('FFMPEG_PATH', 'D:/Unapproved-Phase2A/ffmpeg.exe')
    found = config.binary('ffmpeg')
    assert found is None or Path(found).is_relative_to(config.PROJECT / '.tools') or Path(found).is_relative_to(config.RUNTIME.root / 'tools')
    assert config.binary('unexpected-program') is None


def test_outside_data_rejected_before_jobstore_or_create_app_write(monkeypatch):
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: pytest.fail('mkdir occurred before validation'))
    for operation in (JobStore, create_app):
        with pytest.raises(runtime_profile.IsolationError):
            operation(Path('D:/Unapproved-Phase2A/data'))


def test_reparse_ancestor_is_rejected(tmp_path, monkeypatch):
    suspect = tmp_path / 'linked'
    original_lstat = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == suspect:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(runtime_profile.IsolationError, match='reparse'):
        config.RUNTIME.validate_writable(suspect / 'data')


def test_existing_linked_job_record_blocks_store(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    directory = root / ('a' * 32)
    directory.mkdir(parents=True)
    record = directory / 'job.json'
    record.write_text('{}', encoding='utf-8')
    original_lstat = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == record:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(runtime_profile.IsolationError, match='reparse'):
        JobStore(root)


def test_identity_token_handshake_and_local_origin_allowlist(tmp_path, monkeypatch):
    token = 'a' * 64
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(config, 'RUNTIME', replace(config.RUNTIME, run_id=run_id, run_token=token))
    with TestClient(create_app(tmp_path / 'data')) as client:
        assert client.get('/api/runtime-identity').status_code == 403
        assert client.get('/api/runtime-identity', headers={'x-podcut-run-token': 'wrong'}).status_code == 403
        response = client.get('/api/runtime-identity', headers={'x-podcut-run-token': token})
        assert response.status_code == 200
        assert response.json()['run_id'] == run_id
        assert response.json()['profile_id'] == 'installer-dev'
        assert response.json()['port'] == 8300
        assert token not in response.text
        assert client.post('/api/jobs', json={}, headers={'Origin': 'http://127.0.0.1:3300'}).status_code == 201
        assert client.post('/api/jobs', json={}, headers={'Origin': 'http://localhost:8300'}).status_code == 201
        assert client.post('/api/jobs', json={}, headers={'Origin': 'http://127.0.0.1:3000'}).status_code == 403
        assert client.post('/api/jobs', json={}, headers={'Origin': 'http://127.0.0.1:3300.example.invalid'}).status_code == 403


@pytest.mark.parametrize('model', ['..', '.', 'D:/Unapproved-Phase2A/model', '../model'])
def test_whisper_model_cannot_be_a_path(monkeypatch, model):
    monkeypatch.setattr(runtime_profile, 'dotenv_values', lambda *a, **k: {'WHISPER_MODEL': model})
    with pytest.raises(runtime_profile.IsolationError):
        runtime_profile.load_profile()
