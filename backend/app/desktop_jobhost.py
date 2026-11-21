"""Windows job owner for the packaged DEV services; no app/model imports.

The parent communicates over stdin/stdout NDJSON. Only two exact packaged
service commands are allowed. Child creation is suspended until assignment to
our non-breakaway job succeeds; closing the host kills the entire owned job.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import hmac
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

from .runtime_profile import (APPROVED_PROFILE, APPROVED_PROJECT, IsolationError, canonical_instance, checked_path, packaged_layout,
                              packaged_profile)

PROTOCOL = 'podcut-job-host-v1'
MAX_COMMAND_BYTES = 256 * 1024
MAX_ENV_CHARS = 65535
INITIAL_COMMAND_TIMEOUT = 180.0
SHUTDOWN_TIMEOUT = 15.0


class JobHostError(RuntimeError):
    """A safe, credential-free protocol or native process error."""


def canonical_uuid(value: str) -> str:
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise JobHostError('Invalid desktop run identity.') from None
    return value.lower()


def text(value, label: str, maximum: int = 32767) -> str:
    if not isinstance(value, str) or len(value) > maximum or '\0' in value or '\r' in value or '\n' in value:
        raise JobHostError('Invalid ' + label + '.')
    return value


@dataclass(frozen=True)
class Layout:
    package: Path
    profile: Path
    run_id: str
    backend: Path
    node: Path
    frontend: Path
    instance: str = ''

    @classmethod
    def from_frozen(cls, package_root: str, profile_root: str, run_id: str, instance: str = '') -> 'Layout':
        """Derive authority from the actual executable, not a caller-chosen root."""
        run_id = canonical_uuid(run_id)
        if not getattr(sys, 'frozen', False):
            raise JobHostError('The job host must run from the packaged backend.')
        try:
            instance = canonical_instance(instance)
            actual_package, _resources = packaged_layout(require_inherited=False)
            package = checked_path(package_root, actual_package, 'Job package root')
            expected_profile = packaged_profile(instance)
            profile = checked_path(profile_root, expected_profile, 'Job profile root')
        except IsolationError as error:
            raise JobHostError(str(error)) from None
        if package != actual_package or profile != expected_profile:
            raise JobHostError('The job package/profile assertions do not match the native runtime.')
        if package.is_relative_to(profile) or profile.is_relative_to(package):
            raise JobHostError('The package and writable profile must be separate.')
        backend = checked_path(sys.executable, package, 'Job host executable')
        node = checked_path(package / 'node' / 'node.exe', package, 'Packaged Node')
        frontend = checked_path(package / 'frontend' / 'server.js', package, 'Packaged frontend')
        if not node.is_file() or not frontend.is_file():
            raise JobHostError('A packaged service file is missing.')
        return cls(package, profile, run_id, backend, node, frontend, instance)

    def writable(self, value: str | Path, label: str = 'Job writable path') -> Path:
        return checked_path(value, self.profile, label)

    @property
    def temporary(self) -> Path:
        return self.writable(self.profile / 'temp' / ('desktop-' + self.run_id))

    @property
    def logs(self) -> Path:
        return self.writable(self.profile / 'logs' / ('desktop-' + self.run_id))


@dataclass(frozen=True)
class Service:
    role: str
    executable: Path
    arguments: tuple[str, ...]
    cwd: Path
    environment: dict[str, str] = field(repr=False)
    stdout: Path
    stderr: Path


class ServicePolicy:
    """Strict command/environment allowlist, evaluated before any writes."""

    def __init__(self, layout: Layout, windows: Path):
        self.layout = layout
        self.windows = windows
        self.run_token = ''
        self.started: set[str] = set()

    def prepare(self, command: dict) -> Service:
        if not isinstance(command, dict) or set(command) != {'op', 'id', 'executable', 'args', 'cwd', 'env'} or command.get('op') != 'start':
            raise JobHostError('Invalid start command fields.')
        role = command.get('id')
        if role not in {'backend', 'frontend'} or role in self.started:
            raise JobHostError('A service role is invalid or already started.')
        layout = self.layout
        executable = checked_path(text(command['executable'], 'service executable'), layout.package, 'Service executable')
        expected_executable = layout.backend if role == 'backend' else layout.node
        arguments = command['args']
        expected_arguments = (('--serve', '--run-id', layout.run_id)
                              + (('--isolated-profile', layout.instance) if layout.instance else ())) if role == 'backend' else (str(layout.frontend),)
        if not isinstance(arguments, list) or tuple(text(arg, 'service argument') for arg in arguments) != expected_arguments or executable != expected_executable:
            raise JobHostError('The service executable or arguments are not approved.')
        if not executable.is_file():
            raise JobHostError('The service executable is missing.')
        cwd = Path(text(command['cwd'], 'service working directory'))
        expected_cwd = layout.temporary / 'backend' if role == 'backend' else layout.frontend.parent
        cwd = checked_path(cwd, layout.profile if role == 'backend' else layout.package, 'Service working directory')
        if cwd != expected_cwd:
            raise JobHostError('The service working directory is not approved.')
        environment = self.environment(command['env'], role)
        return Service(role, executable, expected_arguments, cwd, environment,
                       layout.writable(layout.logs / (role + '.stdout.log')),
                       layout.writable(layout.logs / (role + '.stderr.log')))

    def environment(self, supplied, role: str) -> dict[str, str]:
        if not isinstance(supplied, dict) or len(supplied) > 160:
            raise JobHostError('Invalid service environment.')
        incoming = {}
        for key, value in supplied.items():
            key = text(key, 'environment name', 100).upper()
            if not re.fullmatch(r'[A-Z_][A-Z0-9_]*', key) or key in incoming:
                raise JobHostError('Duplicate or invalid environment name.')
            incoming[key] = text(value, 'environment value')
        layout = self.layout
        paths = {
            'DATA_DIR': layout.profile / 'data', 'MODELS_DIR': layout.profile / 'models',
            'CACHE_DIR': layout.profile / 'cache', 'LOG_DIR': layout.profile / 'logs',
            'TEMP_DIR': layout.profile / 'temp', 'RUNTIME_DIR': layout.profile / 'runtime',
            'PODCUT_CONFIG_DIR': layout.profile / 'config', 'PODCUT_MODELS_DIR': layout.profile / 'models',
            'PODCUT_CACHE_DIR': layout.profile / 'cache', 'PODCUT_LOG_DIR': layout.profile / 'logs',
            'PODCUT_TEMP_DIR': layout.profile / 'temp', 'PODCUT_RUNTIME_DIR': layout.profile / 'runtime',
            'PODCUT_ENV_FILE': layout.profile / 'config' / 'backend.env',
            'PODCUT_BACKEND_ENV_FILE': layout.profile / 'config' / 'backend.env',
            'TEMP': layout.profile / 'temp', 'TMP': layout.profile / 'temp',
            'HF_HOME': layout.profile / 'cache' / 'huggingface',
            'HF_HUB_CACHE': layout.profile / 'cache' / 'huggingface' / 'hub',
            'HUGGINGFACE_HUB_CACHE': layout.profile / 'cache' / 'huggingface' / 'hub',
            'TRANSFORMERS_CACHE': layout.profile / 'cache' / 'huggingface' / 'transformers',
            'TORCH_HOME': layout.profile / 'cache' / 'torch',
            'TORCH_EXTENSIONS_DIR': layout.profile / 'cache' / 'torch' / 'extensions',
            'XDG_CACHE_HOME': layout.profile / 'cache' / 'xdg',
            'NUMBA_CACHE_DIR': layout.profile / 'cache' / 'numba',
            'MPLCONFIGDIR': layout.profile / 'cache' / 'matplotlib',
            'PODCUT_NEXT_CACHE_DIR': layout.profile / 'cache' / 'next',
            'USERPROFILE': layout.profile, 'LOCALAPPDATA': layout.profile / 'cache' / 'appdata',
            'APPDATA': layout.profile / 'config' / 'appdata',
        }
        fixed = {
            'PODCUT_PROJECT_ROOT': str(layout.package), 'PODCUT_PROFILE_ROOT': str(layout.profile),
            'PODCUT_PACKAGED_ROOT': str(layout.package), 'PODCUT_PROFILE': 'installer-dev',
            'PODCUT_PROFILE_ID': 'installer-dev', 'PODCUT_RUN_ID': layout.run_id, 'PODCUT_PROFILE_INSTANCE': layout.instance,
            'PODCUT_FRONTEND_PORT': '3300', 'PODCUT_BACKEND_PORT': '8300',
            'PODCUT_API_ORIGIN': 'http://127.0.0.1:8300', 'UVICORN_HOST': '127.0.0.1',
            'UVICORN_PORT': '8300', 'HOSTNAME': '127.0.0.1', 'PORT': '3300',
            'NODE_ENV': 'production', 'NEXT_TELEMETRY_DISABLED': '1', 'AI_PROVIDER': 'heuristic',
            'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONNOUSERSITE': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
            'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1',
        }
        tools = {'FFMPEG_PATH': layout.package / 'tools' / 'ffmpeg.exe',
                 'FFPROBE_PATH': layout.package / 'tools' / 'ffprobe.exe', 'NODE_PATH': layout.node}
        os_values = {'SYSTEMROOT': str(self.windows), 'WINDIR': str(self.windows),
                     'COMSPEC': str(self.windows / 'System32' / 'cmd.exe'), 'OS': 'Windows_NT',
                     'PATHEXT': '.COM;.EXE;.BAT;.CMD'}
        passthrough = {'LANG', 'LC_ALL', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE',
                       'PROCESSOR_IDENTIFIER', 'MAX_UPLOAD_MB', 'STORAGE_LIMIT_GB', 'MAX_DURATION_HOURS',
                       'WHISPER_MODEL', 'WHISPER_DEVICE', 'WHISPER_COMPUTE_TYPE', 'ASD_DEVICE',
                       'YOUTUBE_INGEST_ENABLED', 'YOUTUBE_SOURCE_MAX_HEIGHT',
                       'YOUTUBE_INGEST_TIMEOUT_SEC', 'YOUTUBE_CACHE_RETENTION_DAYS'}
        token_names = {'PODCUT_RUN_TOKEN'} | ({'PODCUT_UI_TOKEN'} if role == 'frontend' else set())
        allowed = set(paths) | set(fixed) | set(tools) | set(os_values) | passthrough | token_names | {'PATH'}
        if set(incoming) - allowed:
            raise JobHostError('An unsupported service environment variable was refused.')
        for name, value in incoming.items():
            if name in paths and value:
                layout.writable(value, 'Inherited service path')
            elif name in fixed and value != fixed[name]:
                raise JobHostError('A service identity or fixed setting differs from the desktop run.')
            elif name in tools and value and checked_path(value, layout.package, 'Service tool') != tools[name]:
                raise JobHostError('A service tool path differs from the package.')
            elif name in os_values and name != 'PATHEXT' and value.casefold() != os_values[name].casefold():
                raise JobHostError('A Windows runtime identity override was refused.')
            elif name == 'PATH':
                approved = {layout.package / 'backend', layout.package / 'node', layout.package / 'tools',
                            self.windows, self.windows / 'System32'}
                if any(Path(part) not in approved for part in value.split(os.pathsep) if part):
                    raise JobHostError('An inherited executable search path was refused.')
        token = incoming.get('PODCUT_RUN_TOKEN', '')
        if not re.fullmatch(r'[a-fA-F0-9]{64}', token) or incoming.get('PODCUT_RUN_ID') != layout.run_id:
            raise JobHostError('The service run credential is missing or invalid.')
        if self.run_token and not hmac.compare_digest(token, self.run_token):
            raise JobHostError('The services do not belong to the same authenticated run.')
        if role == 'frontend':
            ui_token = incoming.get('PODCUT_UI_TOKEN', '')
            if not re.fullmatch(r'[a-fA-F0-9]{64}', ui_token) or hmac.compare_digest(ui_token, token):
                raise JobHostError('The frontend session credential is missing or not independent.')
        result = dict(os_values)
        result.update({name: value for name, value in incoming.items() if name in passthrough})
        result.update({name: str(layout.writable(value)) for name, value in paths.items()})
        result.update(fixed)
        result.update({'PATH': os.pathsep.join(map(str, (layout.package / 'backend', layout.package / 'node',
                                                       layout.package / 'tools', self.windows / 'System32', self.windows)))})
        # Tool values are optional configuration overrides. NODE_PATH must stay
        # empty for the frontend Node module loader; its binary is an exact argv.
        result.update({'FFMPEG_PATH': str(tools['FFMPEG_PATH']), 'FFPROBE_PATH': str(tools['FFPROBE_PATH'])})
        result['PODCUT_RUN_TOKEN'] = token
        if role == 'frontend':
            result['PODCUT_UI_TOKEN'] = incoming['PODCUT_UI_TOKEN']
        else:
            for name, value in os.environ.items():
                if name.startswith('_PYI_'):
                    if name == '_PYI_APPLICATION_HOME_DIR' and Path(value) != layout.backend.parent / '_internal':
                        raise JobHostError('Frozen worker resource state differs from the package.')
                    if name == '_PYI_ARCHIVE_FILE' and Path(value) != layout.backend:
                        raise JobHostError('Frozen worker archive state differs from the package.')
                    result[name] = value
        if sum(len(key) + len(value) + 2 for key, value in result.items()) > MAX_ENV_CHARS:
            raise JobHostError('The service environment is too large.')
        return result

    def commit(self, service: Service) -> None:
        self.started.add(service.role)
        self.run_token = service.environment['PODCUT_RUN_TOKEN']


# Fixed-width Win32 definitions are kept local so importing this module does not
# load a DLL or initialize a job. Tests can replace the native boundary directly.
SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', wintypes.DWORD), ('min_working', SIZE_T), ('max_working', SIZE_T),
                ('active_limit', wintypes.DWORD), ('affinity', ULONG_PTR),
                ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', _IOCounters), ('process_memory', SIZE_T),
                ('job_memory', SIZE_T), ('peak_process_memory', SIZE_T), ('peak_job_memory', SIZE_T)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in ('user_time', 'kernel_time', 'period_user_time', 'period_kernel_time')] + [
        ('page_faults', wintypes.DWORD), ('total_processes', wintypes.DWORD),
        ('active_processes', wintypes.DWORD), ('terminated_processes', wintypes.DWORD)]


class _Security(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('descriptor', wintypes.LPVOID), ('inherit', wintypes.BOOL)]


class _Startup(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('reserved', wintypes.LPWSTR), ('desktop', wintypes.LPWSTR),
                ('title', wintypes.LPWSTR)] + [(name, wintypes.DWORD) for name in (
                    'x', 'y', 'width', 'height', 'xchars', 'ychars', 'fill', 'flags')] + [
                ('show', wintypes.WORD), ('reserved_size', wintypes.WORD), ('reserved_bytes', wintypes.LPVOID),
                ('stdin', wintypes.HANDLE), ('stdout', wintypes.HANDLE), ('stderr', wintypes.HANDLE)]


class _StartupEx(ctypes.Structure):
    _fields_ = [('startup', _Startup), ('attributes', wintypes.LPVOID)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [('process', wintypes.HANDLE), ('thread', wintypes.HANDLE),
                ('pid', wintypes.DWORD), ('tid', wintypes.DWORD)]


class _TCP4Row(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ('state', 'local_address', 'local_port',
                                                  'remote_address', 'remote_port', 'pid')]


class _TCP6Row(ctypes.Structure):
    _fields_ = [('local_address', ctypes.c_ubyte * 16), ('local_scope', ctypes.c_uint32),
                ('local_port', ctypes.c_uint32), ('remote_address', ctypes.c_ubyte * 16),
                ('remote_scope', ctypes.c_uint32), ('remote_port', ctypes.c_uint32),
                ('state', ctypes.c_uint32), ('pid', ctypes.c_uint32)]


class WindowsAPI:
    CREATE_FLAGS = 0x00000004 | 0x00000400 | 0x00080000 | 0x08000000
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    def __init__(self):
        if os.name != 'nt':
            raise JobHostError('Desktop process jobs require Windows.')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True, winmode=0x00000800)
        self.iphelper = ctypes.WinDLL('iphlpapi', use_last_error=True, winmode=0x00000800)
        self.iphelper.GetExtendedTcpTable.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD),
                                                      wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
        self.iphelper.GetExtendedTcpTable.restype = wintypes.DWORD
        signatures = {
            'CreateJobObjectW': ([wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'CreateFileW': ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Security), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'SetHandleInformation': ([wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD], wintypes.BOOL),
            'InitializeProcThreadAttributeList': ([wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SIZE_T)], wintypes.BOOL),
            'UpdateProcThreadAttribute': ([wintypes.LPVOID, wintypes.DWORD, SIZE_T, wintypes.LPVOID, SIZE_T, wintypes.LPVOID, wintypes.LPVOID], wintypes.BOOL),
            'DeleteProcThreadAttributeList': ([wintypes.LPVOID], None),
            'CreateProcessW': ([wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL,
                                wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, wintypes.LPVOID, ctypes.POINTER(_ProcessInformation)], wintypes.BOOL),
            'ResumeThread': ([wintypes.HANDLE], wintypes.DWORD),
            'TerminateProcess': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            'GetExitCodeProcess': ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'IsProcessInJob': ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            'GetWindowsDirectoryW': ([wintypes.LPWSTR, wintypes.UINT], wintypes.UINT),
            'SetDllDirectoryW': ([wintypes.LPCWSTR], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result

    def require(self, result, label: str):
        if not result:
            # Never include argv/environment or the raw protocol command.
            raise JobHostError(label + ' failed (Win32 ' + str(ctypes.get_last_error()) + ').')
        return result

    def windows_directory(self) -> Path:
        buffer = ctypes.create_unicode_buffer(32768)
        length = self.kernel.GetWindowsDirectoryW(buffer, len(buffer))
        if not length or length >= len(buffer):
            raise JobHostError('The Windows runtime directory could not be resolved.')
        return Path(buffer.value)

    def clear_dll_directory(self):
        # This host loads no optional model/native package. Each frozen backend
        # child initializes its own bootloader DLL directory independently.
        self.require(self.kernel.SetDllDirectoryW(None), 'Restoring external DLL search')

    def create_job(self):
        job = self.require(self.kernel.CreateJobObjectW(None, None), 'Creating the desktop job')
        try:
            self.require(self.kernel.SetHandleInformation(job, 1, 0), 'Protecting the job handle')
            limits = _ExtendedLimits()
            limits.basic.flags = 0x00002000  # KILL_ON_JOB_CLOSE, deliberately no BREAKAWAY.
            self.require(self.kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)), 'Configuring the desktop job')
            return job
        except BaseException:
            self.kernel.CloseHandle(job)
            raise

    def active(self, job) -> int:
        accounting = _Accounting()
        self.require(self.kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None), 'Querying the desktop job')
        return int(accounting.active_processes)

    def in_job(self, process, job) -> bool:
        result = wintypes.BOOL()
        self.require(self.kernel.IsProcessInJob(process, job, ctypes.byref(result)), 'Checking owned job membership')
        return bool(result.value)

    def tcp_rows(self) -> list[dict]:
        """Return only DEV-port rows, including an already connected peer.

        The OS supplies a global table, but other ports are discarded before
        their addresses/PIDs are inspected or serialized. IPv6 includes mapped
        loopback connections so a dual-stack foreign socket cannot hide.
        """
        result = []
        for family, row_type in ((2, _TCP4Row), (23, _TCP6Row)):
            size = wintypes.DWORD()
            code = self.iphelper.GetExtendedTcpTable(None, ctypes.byref(size), False, family, 5, 0)
            if code not in (0, 122):
                raise JobHostError('Reading a DEV socket table failed (Win32 ' + str(code) + ').')
            for _attempt in range(4):
                if size.value < 4 or size.value > 8 * 1024 * 1024:
                    raise JobHostError('The DEV socket table size is invalid.')
                storage = ctypes.create_string_buffer(size.value)
                code = self.iphelper.GetExtendedTcpTable(storage, ctypes.byref(size), False, family, 5, 0)
                if code != 122:
                    break
            if code != 0:
                raise JobHostError('The DEV socket table could not be captured consistently.')
            count = ctypes.c_uint32.from_buffer(storage).value
            row_size = ctypes.sizeof(row_type)
            if 4 + count * row_size > len(storage):
                raise JobHostError('The DEV socket table contains invalid row bounds.')
            for index in range(count):
                row = row_type.from_buffer(storage, 4 + index * row_size)
                port = socket.ntohs(int(row.local_port) & 0xffff)
                if port not in (3300, 8300):
                    continue
                if family == 2:
                    local_loopback = int(row.local_address).to_bytes(4, 'little') == b'\x7f\0\0\1'
                    remote_loopback = int(row.remote_address).to_bytes(4, 'little') == b'\x7f\0\0\1'
                else:
                    def loopback(address):
                        value = bytes(address)
                        # The parent pins an IPv4 127.0.0.1 connection, so ::1
                        # and other 127/8 addresses are not the same endpoint.
                        return value == b'\0' * 10 + b'\xff\xff\x7f\0\0\1'
                    local_loopback, remote_loopback = loopback(row.local_address), loopback(row.remote_address)
                result.append({'port': port, 'pid': int(row.pid), 'state': int(row.state),
                               'remote_port': socket.ntohs(int(row.remote_port) & 0xffff),
                               'local_loopback': local_loopback, 'remote_loopback': remote_loopback})
        return result

    def spawn(self, job, service: Service):
        security = _Security(ctypes.sizeof(_Security), None, True)
        handles, attributes = [], None
        attributes_initialized = False
        information = _ProcessInformation()
        retained = False
        try:
            # CREATE_NEW prevents following/replacing an existing linked log.
            for filename in (service.stdout, service.stderr):
                handle = self.kernel.CreateFileW(str(filename), 0x40000000, 3, ctypes.byref(security), 1, 0x80, None)
                if handle == self.INVALID_HANDLE:
                    raise JobHostError('Opening an independent service log failed (Win32 ' + str(ctypes.get_last_error()) + ').')
                handles.append(handle)
            stdin = self.kernel.CreateFileW('NUL', 0x80000000, 3, ctypes.byref(security), 3, 0x80, None)
            if stdin == self.INVALID_HANDLE:
                raise JobHostError('Opening service input failed.')
            handles.append(stdin)
            size = SIZE_T()
            self.kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            if not size.value:
                raise JobHostError('The service handle allowlist is unavailable.')
            storage = ctypes.create_string_buffer(size.value)
            attributes = ctypes.cast(storage, wintypes.LPVOID)
            self.require(self.kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)), 'Creating the service handle allowlist')
            attributes_initialized = True
            inherited = (wintypes.HANDLE * 3)(stdin, handles[0], handles[1])
            self.require(self.kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020002, inherited, ctypes.sizeof(inherited), None, None), 'Restricting inherited service handles')
            startup = _StartupEx()
            startup.startup.cb = ctypes.sizeof(_StartupEx)
            startup.startup.flags = 0x00000100  # STARTF_USESTDHANDLES.
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = stdin, handles[0], handles[1]
            startup.attributes = attributes
            argv = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(service.executable), *service.arguments]))
            environment = ctypes.create_unicode_buffer('\0'.join(key + '=' + value for key, value in sorted(service.environment.items(), key=lambda item: item[0].upper())) + '\0\0')
            self.require(self.kernel.CreateProcessW(str(service.executable), argv, None, None, True, self.CREATE_FLAGS,
                                                    environment, str(service.cwd), ctypes.byref(startup), ctypes.byref(information)), 'Creating the suspended service')
            self.require(self.kernel.AssignProcessToJobObject(job, information.process), 'Assigning the suspended service to its job')
            child = NativeProcess(int(information.pid), information.process)
            if self.kernel.ResumeThread(information.thread) == 0xFFFFFFFF:
                raise JobHostError('Resuming the assigned service failed.')
            retained = True
            return child
        finally:
            if information.process and not retained:
                # This handle came only from our own successful CreateProcessW.
                self.kernel.TerminateProcess(information.process, 1)
                self.kernel.WaitForSingleObject(information.process, 10000)
                self.kernel.CloseHandle(information.process)
            if information.thread:
                self.kernel.CloseHandle(information.thread)
            if attributes_initialized:
                self.kernel.DeleteProcThreadAttributeList(attributes)
            for handle in handles:
                self.kernel.CloseHandle(handle)

    def exit_code(self, process) -> int | None:
        wait = self.kernel.WaitForSingleObject(process, 0)
        if wait == 258:  # WAIT_TIMEOUT.
            return None
        if wait != 0:
            raise JobHostError('Waiting for an owned service failed.')
        result = wintypes.DWORD()
        self.require(self.kernel.GetExitCodeProcess(process, ctypes.byref(result)), 'Reading owned service exit status')
        return int(result.value)

    def terminate(self, job):
        self.require(self.kernel.TerminateJobObject(job, 0), 'Stopping the owned desktop job')

    def close(self, handle):
        self.kernel.CloseHandle(handle)


@dataclass
class NativeProcess:
    pid: int
    handle: object


class NativeJob:
    def __init__(self, api: WindowsAPI):
        self.api = api
        self.handle = api.create_job()
        self.processes: dict[str, NativeProcess] = {}

    def start(self, service: Service) -> int:
        if self.handle is None or service.role in self.processes:
            raise JobHostError('The job is closed or the service role already exists.')
        self.processes[service.role] = self.api.spawn(self.handle, service)
        return self.processes[service.role].pid

    def exits(self) -> list[tuple[str, int]]:
        finished = []
        for role, process in list(self.processes.items()):
            code = self.api.exit_code(process.handle)
            if code is not None:
                finished.append((role, code))
                self.api.close(process.handle)
                del self.processes[role]
        return finished

    def status(self, connection: dict | None = None) -> dict:
        listeners = {'3300': [], '8300': []}
        result = {'pids': {role: process.pid for role, process in self.processes.items()},
                  'active_processes': self.api.active(self.handle) if self.handle is not None else 0,
                  'listeners': listeners}
        rows = self.api.tcp_rows() if self.handle is not None else []
        proofs = {}
        def ownership(row):
            role = 'backend' if row['port'] == 8300 else 'frontend'
            key = (role, row['pid'])
            if key not in proofs:
                process = self.processes.get(role)
                proofs[key] = bool(process and row['pid'] == process.pid
                                   and self.api.exit_code(process.handle) is None
                                   and self.api.in_job(process.handle, self.handle))
            return {'port': row['port'], 'pid': row['pid'], 'owned': proofs[key],
                    'role': role if proofs[key] else None}
        for row in rows:
            if row['state'] == 2:  # MIB_TCP_STATE_LISTEN.
                listeners[str(row['port'])].append(ownership(row))
        if connection is not None:
            matches = [ownership(row) for row in rows if row['port'] == connection['port']
                       and row['remote_port'] == connection['client_port'] and row['state'] == 5
                       and row['local_loopback'] and row['remote_loopback']]
            owned = bool(matches) and all(item['owned'] for item in matches)
            result['connection'] = {**connection, 'pid': matches[0]['pid'] if len(matches) == 1 or owned else None,
                                    'owned': owned, 'role': matches[0]['role'] if owned else None} if matches else {
                                        **connection, 'pid': None, 'owned': False, 'role': None}
        return result

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT) -> int:
        if self.handle is None:
            return 0
        last_active = -1
        try:
            self.api.terminate(self.handle)
            deadline = time.monotonic() + timeout
            while True:
                last_active = self.api.active(self.handle)
                if last_active == 0 or time.monotonic() >= deadline:
                    break
                time.sleep(.025)
        finally:
            # KILL_ON_JOB_CLOSE is the fallback even on query/termination error.
            self.api.close(self.handle)
            self.handle = None
            for process in self.processes.values():
                self.api.close(process.handle)
            self.processes.clear()
        return last_active


def input_commands(stream, inbox: queue.Queue) -> None:
    """Bound each request and queue; never echo data containing credentials."""
    try:
        try:
            descriptor = stream.fileno()
        except (AttributeError, OSError):
            descriptor = None
        pending = bytearray()
        while True:
            if descriptor is None:
                raw = stream.readline(MAX_COMMAND_BYTES + 1)
            else:
                # Do not hold sys.stdin.buffer's lock in a daemon reader. The
                # frozen interpreter must be able to finalize after shutdown.
                while b'\n' not in pending and len(pending) <= MAX_COMMAND_BYTES:
                    chunk = os.read(descriptor, min(8192, MAX_COMMAND_BYTES + 1 - len(pending)))
                    if not chunk:
                        break
                    pending.extend(chunk)
                boundary = pending.find(b'\n')
                if boundary >= 0:
                    raw = bytes(pending[:boundary + 1])
                    del pending[:boundary + 1]
                else:
                    raw = bytes(pending)
                    pending.clear()
            if not raw:
                inbox.put(('eof', None))
                return
            if len(raw) > MAX_COMMAND_BYTES or not raw.endswith(b'\n'):
                inbox.put(('error', 'A desktop command is too large or incomplete.'))
                return
            try:
                command = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeError):
                inbox.put(('error', 'A desktop command is not valid NDJSON.'))
                return
            inbox.put(('command', command))
            if isinstance(command, dict) and command == {'op': 'shutdown'}:
                return
    except Exception:
        inbox.put(('error', 'The desktop control input failed.'))


def emit(stream, event: str, **fields) -> None:
    stream.write(json.dumps({'event': event, **fields}, separators=(',', ':'), allow_nan=False) + '\n')
    stream.flush()


def serve(layout: Layout, api: WindowsAPI, stream_in=None, stream_out=None) -> int:
    stream_in = stream_in if stream_in is not None else sys.stdin.buffer
    stream_out = stream_out if stream_out is not None else sys.stdout
    api.clear_dll_directory()
    policy = ServicePolicy(layout, api.windows_directory())
    inbox = queue.Queue(maxsize=16)
    reader = threading.Thread(target=input_commands, args=(stream_in, inbox), daemon=True, name='desktop-control-input')
    reader.start()
    job = NativeJob(api)
    failed = False
    deadline = time.monotonic() + INITIAL_COMMAND_TIMEOUT
    try:
        emit(stream_out, 'ready', protocol=PROTOCOL, run_id=layout.run_id, pid=os.getpid())
        while True:
            finished = job.exits()
            for role, code in finished:
                emit(stream_out, 'exit', id=role, code=code)
            if finished:
                # A service disappearing makes the other service untrusted/stale.
                failed = True
                break
            if not policy.started and time.monotonic() > deadline:
                raise JobHostError('No desktop service command arrived before startup timeout.')
            try:
                kind, command = inbox.get(timeout=.1)
            except queue.Empty:
                continue
            if kind == 'eof':
                break
            if kind == 'error':
                raise JobHostError(command)
            if not isinstance(command, dict):
                raise JobHostError('The desktop command must be an object.')
            operation = command.get('op')
            if operation == 'shutdown' and set(command) == {'op'}:
                break
            if operation == 'status' and set(command) <= {'op', 'nonce', 'connection'}:
                nonce = command.get('nonce')
                if nonce is not None and (not isinstance(nonce, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', nonce)):
                    raise JobHostError('The desktop status nonce is invalid.')
                connection = command.get('connection')
                if connection is not None and (not isinstance(connection, dict) or set(connection) != {'port', 'client_port'}
                        or type(connection['port']) is not int or connection['port'] not in (3300, 8300)
                        or type(connection['client_port']) is not int or not 1 <= connection['client_port'] <= 65535):
                    raise JobHostError('The desktop socket proof request is invalid.')
                emit(stream_out, 'status', **({'nonce': nonce} if nonce is not None else {}), **job.status(connection))
                continue
            service = policy.prepare(command)
            # All command/env/path checks above precede these scoped writes.
            checked_path(service.cwd, layout.profile if service.role == 'backend' else layout.package, 'Service cwd')
            if service.role == 'backend':
                service.cwd.mkdir(parents=True, exist_ok=True)
            layout.logs.mkdir(parents=True, exist_ok=True)
            for name in ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA'):
                layout.writable(service.environment[name]).mkdir(parents=True, exist_ok=True)
            pid = job.start(service)
            policy.commit(service)
            emit(stream_out, 'started', id=service.role, pid=pid)
    except BaseException as error:
        failed = True
        # Our own exceptions contain no credentials. Other exception text might.
        message = str(error) if isinstance(error, JobHostError) else type(error).__name__
        try:
            emit(stream_out, 'error', code='job-host-failed', message=message)
        except Exception:
            pass
    finally:
        # A normal shutdown command/EOF ends the input reader before interpreter
        # teardown. Error-path raw os.read never locks a buffered Python stream.
        reader.join(timeout=.5)
        try:
            remaining = job.shutdown()
            if remaining != 0:
                failed = True
            try:
                emit(stream_out, 'closed', active_processes=remaining, clean=(remaining == 0))
            except Exception:
                pass
        except BaseException:
            failed = True
    return 1 if failed else 0


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Installer DEV owned process host')
    parser.add_argument('--package-root', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--profile-root', required=True)
    parser.add_argument('--isolated-profile', default='')
    args = parser.parse_args(arguments)
    try:
        layout = Layout.from_frozen(args.package_root, args.profile_root, args.run_id, args.isolated_profile)
        return serve(layout, WindowsAPI())
    except BaseException as error:
        message = str(error) if isinstance(error, JobHostError) else type(error).__name__
        try:
            emit(sys.stdout, 'error', code='job-host-preflight', message=message)
        except Exception:
            pass
        return 1
