"""Fail-closed filesystem and environment boundary for Installer-Dev Phase 2A.

This module does no filesystem writes. All paths are checked before activation.
"""
from __future__ import annotations

import os
import ctypes
from ctypes import wintypes
import hashlib
import json
import re
import shutil
import stat
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

if not getattr(sys, 'frozen', False):
    from .lab_profile import APPROVED_PROJECT, APPROVED_PROFILE
else:
    # Frozen authority comes from the current executable and native Known Folder,
    # never these source-only aliases or inherited profile environment variables.
    APPROVED_PROJECT = Path(sys.executable).parent.parent
    APPROVED_PROFILE = None
PROFILE_ID = 'installer-dev'
PROTOCOL = 'podcut-installer-dev-v1'
BUILD = 'phase2a'
PACKAGE_BUILD = 'phase3a'
APP_DIRECTORY = 'ClipClip.ai'
YUNET_NAME = 'face_detection_yunet_2023mar.onnx'
YUNET_SHA256 = '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'
SETTING_NAMES = frozenset({'MAX_UPLOAD_MB', 'STORAGE_LIMIT_GB', 'MAX_DURATION_HOURS', 'WHISPER_MODEL',
    'WHISPER_DEVICE', 'WHISPER_COMPUTE_TYPE', 'ASD_DEVICE', 'YOUTUBE_INGEST_ENABLED',
    'YOUTUBE_SOURCE_MAX_HEIGHT', 'YOUTUBE_INGEST_TIMEOUT_SEC', 'YOUTUBE_CACHE_RETENTION_DAYS',
    'FFMPEG_PATH', 'FFPROBE_PATH', 'NODE_PATH'})
PRIVATE_SETTING_NAMES = frozenset({'AI_PROVIDER', 'GEMINI_API_KEY', 'GEMINI_MODEL', 'OPENAI_API_KEY', 'OPENAI_MODEL'})
DEFAULT_SETTINGS = {'MAX_UPLOAD_MB': '8192', 'STORAGE_LIMIT_GB': '30', 'MAX_DURATION_HOURS': '12',
    'WHISPER_MODEL': 'small', 'WHISPER_DEVICE': 'cpu', 'WHISPER_COMPUTE_TYPE': 'int8', 'ASD_DEVICE': 'cpu',
    'YOUTUBE_INGEST_ENABLED': 'true', 'AI_PROVIDER': 'heuristic', 'GEMINI_API_KEY': '',
    'GEMINI_MODEL': 'gemini-3.5-flash-lite', 'OPENAI_API_KEY': '', 'OPENAI_MODEL': 'gpt-4o-mini'}


class IsolationError(RuntimeError):
    """The runtime cannot stay inside the approved isolated roots."""


def packaged_layout(*, require_inherited: bool = True) -> tuple[Path | None, Path | None]:
    """Derive a frozen layout from the executable, never an arbitrary env root."""
    if not getattr(sys, 'frozen', False):
        if os.environ.get('PODCUT_PACKAGED_ROOT'):
            raise IsolationError('A source runtime cannot impersonate a packaged runtime.')
        return None, None
    executable_path = Path(sys.executable)
    if not executable_path.is_absolute() or '..' in executable_path.parts:
        raise IsolationError('The packaged executable must be an absolute native path.')
    package = checked_path(executable_path.parent.parent, executable_path.parent.parent, 'Packaged root')
    executable = checked_path(executable_path, package, 'Packaged executable')
    if executable != package / 'backend' / 'PODCUTBackend.exe' or not executable.is_file():
        raise IsolationError('Unexpected packaged backend executable layout.')
    inherited = os.environ.get('PODCUT_PACKAGED_ROOT', '')
    if ((require_inherited and not inherited)
            or (inherited and checked_path(inherited, package, 'Inherited packaged root') != package)):
        raise IsolationError('The packaged root does not match the running executable.')
    marker = checked_path(package / 'podcut-package.json', package, 'Package marker')
    if not marker.is_file() or marker.stat().st_size > 16384:
        raise IsolationError('The desktop package marker is missing or invalid.')
    try:
        manifest = json.loads(marker.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise IsolationError('The desktop package marker could not be read.') from None
    if (not isinstance(manifest, dict) or manifest.get('schema') != 1
            or manifest.get('profile_id') != PROFILE_ID or manifest.get('build') != PACKAGE_BUILD
            or manifest.get('backend') != 'backend/PODCUTBackend.exe'):
        raise IsolationError('The desktop package marker does not identify this DEV package.')
    resources = checked_path(getattr(sys, '_MEIPASS', ''), package / 'backend', 'Frozen resources')
    if resources != executable.parent / '_internal' or not resources.is_dir():
        raise IsolationError('The frozen resource directory does not match the onedir executable.')
    for name, expected in (('_PYI_APPLICATION_HOME_DIR', resources), ('_PYI_ARCHIVE_FILE', executable)):
        if os.environ.get(name) and checked_path(os.environ[name], package, name) != expected:
            raise IsolationError('The PyInstaller child state does not match this package.')
    if os.environ.get('_PYI_PARENT_PROCESS_LEVEL') and not re.fullmatch(r'[0-9]', os.environ['_PYI_PARENT_PROCESS_LEVEL']):
        raise IsolationError('Invalid PyInstaller parent process level.')
    return package, resources


def checked_path(value: str | Path, root: Path, label: str) -> Path:
    """Check containment without allowing resolution to hide a reparse ancestor."""
    candidate = Path(value)
    if not candidate.is_absolute() or '..' in candidate.parts:
        raise IsolationError(f'{label} must be an absolute isolated path.')
    candidate = Path(os.path.abspath(candidate))
    boundary = Path(os.path.abspath(root))
    try:
        candidate.relative_to(boundary)
    except ValueError:
        raise IsolationError(f'{label} is outside its approved isolated root.') from None
    for ancestor in (candidate, *candidate.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise IsolationError(f'{label} contains a link, junction or reparse point.')
        if stat.S_ISREG(info.st_mode) and getattr(info, 'st_nlink', 1) > 1:
            raise IsolationError(f'{label} contains a hardlinked file.')
    if candidate.resolve() != candidate:
        raise IsolationError(f'{label} does not resolve to its approved isolated path.')
    return candidate


def canonical_instance(value: str = '') -> str:
    if value == '':
        return ''
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise IsolationError('The isolated profile identifier must be a canonical UUID.') from None
    return value


def windows_local_appdata() -> Path:
    """Resolve LocalAppData for the process token, ignoring env spoofing; no writes."""
    if os.name != 'nt':
        raise IsolationError('The packaged desktop requires Windows Known Folder support.')
    class GUID(ctypes.Structure):
        _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD), ('data3', wintypes.WORD),
                    ('data4', ctypes.c_ubyte * 8)]
    identifier = GUID.from_buffer_copy(uuid.UUID('f1b32785-6fba-4fcf-9d55-7b8e7f157091').bytes_le)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                          ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    result = ctypes.c_void_p()
    # A null token expands USERPROFILE from the caller's process environment.
    # An explicit, query-only token uses the actual logged-in user's folder.
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(wintypes.HANDLE(-1), 0x0008, ctypes.byref(token)):
        raise IsolationError('Windows could not identify the current user.')
    try:
        status = shell.SHGetKnownFolderPath(ctypes.byref(identifier), 0, token, ctypes.byref(result))
        if status != 0 or not result.value:
            raise IsolationError('Windows could not resolve this user LocalAppData folder.')
        folder = Path(ctypes.wstring_at(result.value))
    finally:
        if result.value:
            ole.CoTaskMemFree(result)
        kernel.CloseHandle(token)
    return checked_path(folder, folder, 'Windows LocalAppData')


def windows_directory() -> Path:
    if os.name != 'nt':
        raise IsolationError('Windows runtime support is unavailable.')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    api.GetWindowsDirectoryW.restype = wintypes.UINT
    value = ctypes.create_unicode_buffer(32768)
    length = api.GetWindowsDirectoryW(value, len(value))
    if not 0 < length < len(value):
        raise IsolationError('Windows could not resolve its native system directory.')
    folder = Path(value.value)
    return checked_path(folder, folder, 'Windows directory')


def packaged_profile(instance: str = '') -> Path:
    instance = canonical_instance(instance)
    # Ordinary frozen builds exclude this module. A Quality Upgrade artifact
    # uses it only at the owned development run path for local QA.
    if getattr(sys, 'frozen', False):
        try:
            from .quality_dev_profile import quality_profile
        except ModuleNotFoundError as error:
            if error.name != 'app.quality_dev_profile':
                raise
        else:
            local_profile = quality_profile(Path(sys.executable), instance)
            if local_profile is not None:
                return local_profile
    local_appdata = windows_local_appdata()
    base = checked_path(local_appdata / APP_DIRECTORY, local_appdata, 'User profile base')
    return checked_path(base / 'profiles' / instance, base, 'Isolated user profile') if instance else base


def bootstrap_info(instance: str = '') -> dict:
    """Readonly native bootstrap; consumed and independently rechecked by Electron."""
    package, _resources = packaged_layout(require_inherited=False)
    if package is None:
        raise IsolationError('Bootstrap must run from the packaged executable.')
    instance = canonical_instance(instance)
    profile = packaged_profile(instance)
    if package.is_relative_to(profile) or profile.is_relative_to(package):
        raise IsolationError('The program and writable user profile must be separate.')
    return {'schema': 1, 'profile_id': PROFILE_ID, 'build': PACKAGE_BUILD, 'package_root': str(package),
            'project_root': str(package), 'profile_root': str(profile), 'profile_instance': instance or None,
            'windows_root': str(windows_directory())}


def _secret(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'PROXY')) or upper in {
        'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID', 'AWS_ACCESS_KEY_ID', 'AWS_PROFILE', 'AZURE_CLIENT_ID', 'AZURE_TENANT_ID'}


@dataclass(frozen=True)
class RuntimeProfile:
    project: Path
    root: Path
    paths: dict[str, Path]
    settings: dict[str, str] = field(repr=False)
    run_id: str
    run_token: str = field(repr=False)
    package: Path | None = None
    resources: Path | None = None
    instance: str = ''
    needs_config_seed: bool = False

    @property
    def packaged(self) -> bool:
        return self.package is not None

    def validate_writable(self, value: str | Path, label: str = 'Runtime path') -> Path:
        return checked_path(value, self.root, label)

    def validate_project(self, value: str | Path, label: str = 'Project path') -> Path:
        return checked_path(value, self.package if self.packaged else self.project, label)

    def validate_tree(self, value: str | Path) -> Path:
        """Reject existing linked data before store recovery can inspect or change it."""
        root = self.validate_writable(value, 'Data tree')
        pending = [root] if root.is_dir() else []
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = self.validate_writable(entry.path, 'Stored data path')
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        return root

    def identity(self) -> dict:
        result = {'profile_id': PROFILE_ID, 'project_root': str(self.project), 'profile_root': str(self.root),
                'run_id': self.run_id, 'port': 8300, 'frontend_port': 3300,
                'backend_host': '127.0.0.1', 'protocol': PROTOCOL, 'build': PACKAGE_BUILD if self.packaged else BUILD}
        if self.packaged:
            result.update(packaged=True, packaged_root=str(self.package), profile_instance=self.instance or None)
        return result

    def environment(self) -> dict[str, str]:
        """Allowlisted child environment: no inherited project paths or secrets."""
        allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'OS', 'PATHEXT', 'PROCESSOR_ARCHITECTURE',
                   'PROCESSOR_IDENTIFIER', 'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL'}
        result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        tool_paths = (self.package / 'backend', self.package / 'node', self.package / 'tools') if self.packaged else (
            self.project / '.tools' / 'python', self.project / '.tools' / 'node', self.root / 'tools')
        result['PATH'] = os.pathsep.join(str(path) for path in tool_paths)
        # Private provider credentials are used directly by AIConfiguration and
        # never forwarded to FFmpeg, ingestion or model worker environments.
        result.update({key: value for key, value in self.settings.items() if key in SETTING_NAMES})
        result.update({key: str(value) for key, value in self.paths.items()})
        result.update({'PODCUT_PROJECT_ROOT': str(self.project), 'PODCUT_PROFILE_ROOT': str(self.root),
            'PODCUT_PROFILE': PROFILE_ID, 'PODCUT_PROFILE_ID': PROFILE_ID,
            'PODCUT_FRONTEND_PORT': '3300', 'PODCUT_BACKEND_PORT': '8300',
            'UVICORN_HOST': '127.0.0.1', 'UVICORN_PORT': '8300',
            'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
            'AI_PROVIDER': 'heuristic',
            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1'})
        for name in ('FFMPEG_PATH', 'FFPROBE_PATH', 'NODE_PATH'):
            result[name] = self.settings.get(name, '')
        if self.packaged:
            result['PODCUT_PACKAGED_ROOT'] = str(self.package)
            result['PODCUT_PROFILE_INSTANCE'] = self.instance
            # Same onedir worker children reuse only already validated bootloader
            # state. Never forward arbitrary Python/Node paths or the API token.
            expected_state = {'_PYI_APPLICATION_HOME_DIR': self.resources,
                              '_PYI_ARCHIVE_FILE': self.package / 'backend' / 'PODCUTBackend.exe'}
            for name, expected in expected_state.items():
                if os.environ.get(name):
                    if checked_path(os.environ[name], self.package, name) != expected:
                        raise IsolationError('The worker bootloader state points outside this package.')
                    result[name] = os.environ[name]
            if os.environ.get('_PYI_PARENT_PROCESS_LEVEL'):
                level = os.environ['_PYI_PARENT_PROCESS_LEVEL']
                if not re.fullmatch(r'[0-9]', level):
                    raise IsolationError('Invalid worker bootloader process level.')
                result['_PYI_PARENT_PROCESS_LEVEL'] = level
        return result

    def activate(self) -> None:
        environment = self.environment()
        for path in dict.fromkeys(self.paths.values()):
            self.validate_writable(path).mkdir(parents=True, exist_ok=True)
        for key in list(os.environ):
            upper = key.upper()
            if _secret(key) or upper in SETTING_NAMES or upper.startswith(('PODCUT_', 'HF_', 'HUGGINGFACE_', 'TRANSFORMERS_', 'TORCH_')) or upper in {
                'DATA_DIR', 'MODELS_DIR', 'CACHE_DIR', 'LOG_DIR', 'TEMP_DIR', 'RUNTIME_DIR',
                'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'}:
                os.environ.pop(key, None)
        os.environ.update(environment)
        tempfile.tempdir = str(self.paths['TEMP'])
        if self.packaged:
            self.seed_configuration()
            self.seed_yunet()

    def seed_configuration(self) -> None:
        """Create a secret-free canonical first-run template, never overwrite."""
        if not self.needs_config_seed:
            return
        target = self.validate_writable(self.root / 'config' / 'backend.env', 'First-run configuration')
        if target.exists():
            return
        temporary = self.validate_writable(target.with_name('backend.env.seed-' + uuid.uuid4().hex + '.tmp'))
        content = '# ClipClip.ai private user settings. API keys are optional.\n' + ''.join(
            key + '=' + value + '\n' for key, value in DEFAULT_SETTINGS.items())
        try:
            with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Windows rename refuses replacing an existing destination.
                temporary.rename(target)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)

    def seed_yunet(self) -> None:
        """Seed the small licensed model once; never replace an existing model."""
        source = self.validate_project(self.resources / 'backend' / 'assets' / 'models' / YUNET_NAME, 'Bundled face model')
        target = self.validate_writable(self.paths['PODCUT_MODELS_DIR'] / 'reframe' / YUNET_NAME, 'Local face model')
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != YUNET_SHA256:
            raise IsolationError('The bundled YuNet model is missing or its checksum is invalid.')
        if target.exists():
            # Existing user models are never replaced. The unchanged M1
            # capability check will report a checksum mismatch when applicable.
            return
        self.validate_writable(target.parent).mkdir(parents=True, exist_ok=True)
        temporary = self.validate_writable(target.with_name(target.name + '.seed-' + uuid.uuid4().hex + '.tmp'))
        try:
            shutil.copy2(source, temporary)
            # On Windows rename refuses an existing destination. It cannot
            # overwrite a model created by another instance during this seed.
            try:
                temporary.rename(target)
            except FileExistsError:
                # Another instance/user created a model; preserve that file too.
                pass
        finally:
            temporary.unlink(missing_ok=True)


def load_profile() -> RuntimeProfile:
    package, resources = packaged_layout()
    project = package if package else checked_path(Path(__file__).absolute().parents[2], APPROVED_PROJECT, 'Project root')
    if not package and project != APPROVED_PROJECT:
        raise IsolationError('Backend must run from the approved Installer-Dev project root.')
    instance = canonical_instance(os.environ.get('PODCUT_PROFILE_INSTANCE', '')) if package else ''
    root = packaged_profile(instance) if package else checked_path(APPROVED_PROFILE, APPROVED_PROFILE, 'Profile root')
    if package and (package.is_relative_to(root) or root.is_relative_to(package)):
        raise IsolationError('The program and writable user profile must be separate.')
    # Application-specific inherited redirects fail closed before canonical values
    # replace them. Ordinary host TEMP/cache defaults are safely replaced below.
    for name, value in os.environ.items():
        upper = name.upper()
        if not value:
            continue
        if upper == 'PODCUT_PROJECT_ROOT':
            if checked_path(value, project, upper) != project:
                raise IsolationError('Inherited project identity does not match Installer-Dev.')
        elif upper == 'PODCUT_PROFILE_ROOT':
            if checked_path(value, root, upper) != root:
                raise IsolationError('Inherited profile identity does not match Installer-Dev.')
        elif upper in {'DATA_DIR', 'MODELS_DIR', 'CACHE_DIR', 'LOG_DIR', 'TEMP_DIR', 'RUNTIME_DIR'} or (upper.startswith('PODCUT_') and upper.endswith('_DIR')):
            checked_path(value, root, upper)
        elif upper in {'FFMPEG_PATH', 'FFPROBE_PATH', 'NODE_PATH'}:
            candidate = Path(value)
            boundary = package if package else project / '.tools' if candidate.is_relative_to(project / '.tools') else root / 'tools'
            checked_path(candidate, boundary, upper)
    settings_file = checked_path(root / 'config' / 'backend.env', root, 'Backend configuration')
    if not settings_file.is_file() and not package:
        raise IsolationError('The isolated profile backend configuration is missing.')
    # No interpolation: inherited values cannot redirect paths or inject credentials.
    needs_config_seed = not settings_file.is_file()
    loaded = dotenv_values(settings_file, interpolate=False, encoding='utf-8') if not needs_config_seed else dict(DEFAULT_SETTINGS)
    settings = {key: value or '' for key, value in loaded.items()}
    defaults = {
        'DATA_DIR': root / 'data', 'PODCUT_CONFIG_DIR': root / 'config',
        'PODCUT_MODELS_DIR': root / 'models', 'PODCUT_CACHE_DIR': root / 'cache',
        'PODCUT_LOG_DIR': root / 'logs', 'PODCUT_RUNTIME_DIR': root / 'runtime',
        'TEMP': root / 'temp', 'TMP': root / 'temp',
        'HF_HOME': root / 'cache' / 'huggingface', 'HF_HUB_CACHE': root / 'cache' / 'huggingface' / 'hub',
        'HUGGINGFACE_HUB_CACHE': root / 'cache' / 'huggingface' / 'hub',
        'TRANSFORMERS_CACHE': root / 'cache' / 'huggingface' / 'transformers',
        'TORCH_HOME': root / 'cache' / 'torch', 'TORCH_EXTENSIONS_DIR': root / 'cache' / 'torch' / 'extensions',
        'XDG_CACHE_HOME': root / 'cache' / 'xdg', 'NUMBA_CACHE_DIR': root / 'cache' / 'numba',
        'MPLCONFIGDIR': root / 'cache' / 'matplotlib',
    }
    paths = {name: checked_path(settings.get(name) or default, root, name) for name, default in defaults.items()}
    for alias in ('MODELS_DIR', 'CACHE_DIR', 'LOG_DIR', 'TEMP_DIR', 'RUNTIME_DIR'):
        if settings.get(alias):
            checked_path(settings[alias], root, alias)
    for name in ('FFMPEG_PATH', 'FFPROBE_PATH', 'NODE_PATH'):
        value = settings.get(name, '')
        if value:
            candidate = Path(value)
            boundary = package if package else project / '.tools' if candidate.is_relative_to(project / '.tools') else root / 'tools'
            checked_path(candidate, boundary, name)
    allowed_settings = SETTING_NAMES | PRIVATE_SETTING_NAMES if package else SETTING_NAMES
    settings = {key: value for key, value in settings.items() if key in allowed_settings}
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', settings.get('WHISPER_MODEL', 'small')):
        raise IsolationError('WHISPER_MODEL must be a model name, not a filesystem path.')
    run_id, token = os.environ.get('PODCUT_RUN_ID', ''), os.environ.get('PODCUT_RUN_TOKEN', '')
    worker = package is not None and len(sys.argv) == 4 and sys.argv[1:3] == ['--worker', 'ingestion']
    if worker:
        checked_path(sys.argv[3], root, 'Worker request')
    if package and not worker and (not run_id or not token):
        raise IsolationError('A packaged server requires the desktop launcher identity.')
    if bool(run_id) != bool(token):
        raise IsolationError('The launcher identity must include both run ID and run token.')
    if run_id:
        try:
            uuid.UUID(run_id)
        except ValueError:
            raise IsolationError('The launcher run ID is invalid.') from None
        if not re.fullmatch(r'[0-9a-fA-F]{64}', token):
            raise IsolationError('The launcher run token is invalid.')
    profile = RuntimeProfile(project, root, paths, settings, run_id, token, package, resources, instance, needs_config_seed)
    # Validate model seed and any existing user model before directory writes.
    if package:
        seed = profile.validate_project(resources / 'backend' / 'assets' / 'models' / YUNET_NAME, 'Bundled face model')
        profile.validate_writable(paths['PODCUT_MODELS_DIR'] / 'reframe' / YUNET_NAME, 'Local face model')
        if not seed.is_file() or hashlib.sha256(seed.read_bytes()).hexdigest() != YUNET_SHA256:
            raise IsolationError('The bundled YuNet model is missing or invalid.')
    return profile
