"""Relocatable frozen boundaries; synthetic fixtures, no downloads or outside writes."""
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import uuid

import pytest

from app import config, desktop_jobhost as host, runtime_profile as runtime


RUN = '79786678-8479-4b6d-9e49-35bcf40cb971'
INSTANCE = '13712a45-d9ee-40a7-8f9c-a1f60fd96507'


@pytest.fixture
def portable(tmp_path, monkeypatch):
    package = tmp_path / 'relocated 🩰 folder' / 'resources' / 'podcut'
    executable = package / 'backend' / 'PODCUTBackend.exe'
    resources = executable.parent / '_internal'
    resources.mkdir(parents=True)
    executable.write_bytes(b'fixture executable identity; never launched')
    for relative in ('node/node.exe', 'frontend/server.js', 'tools/ffmpeg.exe', 'tools/ffprobe.exe'):
        file = package / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'independent fixture')
    (package / 'podcut-package.json').write_text(json.dumps({'schema': 1, 'profile_id': runtime.PROFILE_ID,
        'build': runtime.PACKAGE_BUILD, 'backend': 'backend/PODCUTBackend.exe'}), encoding='utf-8')
    seed = resources / 'backend' / 'assets' / 'models' / runtime.YUNET_NAME
    seed.parent.mkdir(parents=True)
    seed.write_bytes(b'synthetic face-model seed for boundary testing only')
    local = tmp_path / 'another user 🧁' / 'Local'
    local.mkdir(parents=True)
    profile = local / runtime.APP_DIRECTORY
    monkeypatch.setattr(runtime, 'windows_local_appdata', lambda: local)
    monkeypatch.setattr(runtime, 'windows_directory', lambda: Path('C:/Windows'))
    monkeypatch.setattr(runtime, 'YUNET_SHA256', hashlib.sha256(seed.read_bytes()).hexdigest())
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(sys, '_MEIPASS', str(resources), raising=False)
    monkeypatch.setattr(sys, 'argv', [str(executable), '--serve', '--run-id', RUN])
    for name in list(os.environ):
        if name.startswith(('PODCUT_', '_PYI_')) or name in {'DATA_DIR','MODELS_DIR','CACHE_DIR','LOG_DIR','TEMP_DIR','RUNTIME_DIR','FFMPEG_PATH','FFPROBE_PATH','NODE_PATH'}:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('PODCUT_PACKAGED_ROOT', str(package))
    monkeypatch.setenv('PODCUT_PROJECT_ROOT', str(package))
    monkeypatch.setenv('PODCUT_PROFILE_ROOT', str(profile))
    monkeypatch.setenv('PODCUT_RUN_ID', RUN)
    monkeypatch.setenv('PODCUT_RUN_TOKEN', 'a' * 64)
    return SimpleNamespace(package=package, executable=executable, resources=resources, local=local, profile=profile)


def test_native_known_folder_ignores_environment_user_redirects(monkeypatch):
    original = runtime.windows_local_appdata()
    monkeypatch.setenv('LOCALAPPDATA', 'D:/Unapproved-Portable/Local')
    monkeypatch.setenv('USERPROFILE', 'D:/Unapproved-Portable/User')
    monkeypatch.setenv('APPDATA', 'D:/Unapproved-Portable/Roaming')
    assert runtime.windows_local_appdata() == original
    assert original.is_absolute()


@pytest.mark.parametrize('value', ['../escape', 'name', INSTANCE.upper(), '{'+INSTANCE+'}', ' '+INSTANCE, None, 123])
def test_isolated_profile_is_only_a_canonical_uuid(value):
    with pytest.raises(runtime.IsolationError):
        runtime.canonical_instance(value)


def test_bootstrap_is_readonly_and_uses_actual_relocated_resources(portable, monkeypatch):
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Read-only bootstrap wrote a directory.'))
    value = runtime.bootstrap_info()
    assert value == {'schema': 1, 'profile_id': runtime.PROFILE_ID, 'build': runtime.PACKAGE_BUILD,
                     'package_root': str(portable.package), 'project_root': str(portable.package),
                     'profile_root': str(portable.profile), 'profile_instance': None, 'windows_root': 'C:\\Windows'}
    assert not portable.profile.exists()


def test_bootstrap_isolated_profile_stays_inside_native_user_root(portable, monkeypatch):
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Read-only bootstrap wrote a directory.'))
    value = runtime.bootstrap_info(INSTANCE)
    assert value['profile_root'] == str(portable.profile/'profiles'/INSTANCE)
    assert value['profile_instance'] == INSTANCE
    assert not portable.profile.exists()


def test_frozen_roots_do_not_depend_on_source_lab_constants(portable, monkeypatch):
    monkeypatch.setattr(runtime, 'APPROVED_PROJECT', portable.local/'unrelated-source-lab')
    monkeypatch.setattr(runtime, 'APPROVED_PROFILE', portable.local/'unrelated-lab-profile')
    loaded = runtime.load_profile()
    assert loaded.project == portable.package
    assert loaded.root == portable.profile
    assert loaded.needs_config_seed
    assert loaded.settings['AI_PROVIDER'] == 'heuristic'
    assert not portable.profile.exists()


def test_first_run_config_is_secret_free_and_never_overwrites(portable, monkeypatch):
    loaded = runtime.load_profile()
    parent = loaded.root/'config'
    parent.mkdir(parents=True)
    loaded.seed_configuration()
    target = parent/'backend.env'
    content = target.read_bytes()
    assert b'AI_PROVIDER=heuristic' in content and b'GEMINI_API_KEY=\n' in content
    assert b'OPENAI_API_KEY=\n' in content and b'WHISPER_MODEL=small' in content
    assert not list(parent.glob('*.tmp'))
    target.write_bytes(b'# user-owned private configuration; preserve exactly\n')
    loaded.seed_configuration()
    assert target.read_bytes() == b'# user-owned private configuration; preserve exactly\n'


def test_isolated_profile_cannot_claim_default_or_foreign_profile(portable, monkeypatch):
    monkeypatch.setenv('PODCUT_PROFILE_INSTANCE', INSTANCE)
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Profile redirect wrote a directory.'))
    with pytest.raises(runtime.IsolationError):
        runtime.load_profile()  # Inherited root still names default, so mismatch.
    monkeypatch.setenv('PODCUT_PROFILE_ROOT', str(portable.profile/'profiles'/INSTANCE))
    loaded = runtime.load_profile()
    assert loaded.root == portable.profile/'profiles'/INSTANCE
    assert loaded.environment()['PODCUT_PROFILE_INSTANCE'] == INSTANCE
    assert loaded.root != portable.profile


def test_bootstrap_rejects_program_data_overlap(portable, monkeypatch):
    monkeypatch.setattr(runtime, 'packaged_profile', lambda _instance='': portable.package/'userdata')
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Program/data overlap wrote a directory.'))
    with pytest.raises(runtime.IsolationError, match='separate'):
        runtime.bootstrap_info()


def test_packaged_cloud_key_comes_only_from_private_file_and_never_child_env(portable, monkeypatch):
    parent = portable.profile/'config'
    parent.mkdir(parents=True)
    key = 'synthetic-user-owned-key-not-a-real-secret'
    (parent/'backend.env').write_text('AI_PROVIDER=gemini\nGEMINI_API_KEY='+key+'\nGEMINI_MODEL=owned-model\n', encoding='utf-8')
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-inherited-key-must-be-ignored')
    loaded = runtime.load_profile()
    assert loaded.settings['GEMINI_API_KEY'] == key
    assert key not in repr(loaded)
    assert 'GEMINI_API_KEY' not in loaded.environment()
    monkeypatch.setattr(config, 'RUNTIME', loaded)
    selected = config.ai_configuration()
    assert selected.provider == 'gemini' and selected.model == 'owned-model' and selected.api_key == key
    assert key not in repr(selected) and key not in json.dumps(selected.public())


def test_inherited_cloud_key_is_ignored_on_new_profile(portable, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-host-key-not-a-real-secret')
    monkeypatch.setenv('AI_PROVIDER', 'gemini')
    loaded = runtime.load_profile()
    monkeypatch.setattr(config, 'RUNTIME', loaded)
    assert config.ai_configuration().provider == 'heuristic'
    assert not config.ai_configuration().api_key


def test_jobhost_uses_relocated_package_and_native_profile(portable, monkeypatch):
    monkeypatch.setattr(host, 'packaged_profile', runtime.packaged_profile)
    layout = host.Layout.from_frozen(str(portable.package), str(portable.profile), RUN)
    assert layout.package == portable.package and layout.profile == portable.profile
    assert not portable.profile.exists()
    with pytest.raises(host.JobHostError):
        host.Layout.from_frozen(str(portable.package), str(portable.local/'foreign-profile'), RUN)


def test_jobhost_named_profile_requires_same_cli_and_child_identity(portable, monkeypatch):
    monkeypatch.setattr(host, 'packaged_profile', runtime.packaged_profile)
    target = portable.profile/'profiles'/INSTANCE
    layout = host.Layout.from_frozen(str(portable.package), str(target), RUN, INSTANCE)
    policy = host.ServicePolicy(layout, Path('C:/Windows'))
    command = {'op': 'start', 'id': 'backend', 'executable': str(portable.executable),
               'args': ['--serve', '--run-id', RUN, '--isolated-profile', INSTANCE],
               'cwd': str(layout.temporary/'backend'),
               'env': {'PODCUT_RUN_ID': RUN, 'PODCUT_RUN_TOKEN': 'a'*64, 'PODCUT_PROFILE_INSTANCE': INSTANCE}}
    service = policy.prepare(command)
    assert service.environment['PODCUT_PROJECT_ROOT'] == str(portable.package)
    assert service.environment['PODCUT_PROFILE_INSTANCE'] == INSTANCE
    assert service.arguments[-2:] == ('--isolated-profile', INSTANCE)
    command['env']['PODCUT_PROFILE_INSTANCE'] = str(uuid.uuid4())
    with pytest.raises(host.JobHostError, match='identity'):
        policy.prepare(command)


def test_checked_path_rejects_simulated_hardlink_without_creating_one(tmp_path, monkeypatch):
    file = tmp_path/'synthetic-file'
    file.write_text('fixture', encoding='utf-8')
    original = Path.lstat
    def fake(path, *args, **kwargs):
        if path == file:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0, st_nlink=2)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', fake)
    with pytest.raises(runtime.IsolationError, match='hardlink'):
        runtime.checked_path(file, tmp_path, 'Synthetic linked file')


def test_entry_rejects_profile_cli_env_mismatch_before_config_writes(portable, monkeypatch):
    entry = config.PROJECT/'desktop'/'backend'/'backend_entry.py'
    spec = importlib.util.spec_from_file_location('portable_entry_boundary_test', entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('PODCUT_PROFILE_INSTANCE', INSTANCE)
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Mismatched profile caused a write.'))
    with pytest.raises(RuntimeError, match='profile identities differ'):
        module.main(['--serve', '--run-id', RUN])
