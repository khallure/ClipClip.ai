"""Backend configuration for the fixed, external Installer-Dev profile."""
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .runtime_profile import load_profile, checked_path, IsolationError

# Validate every configured path before creating directories or importing app state.
RUNTIME = load_profile()
RUNTIME.activate()
PROJECT = RUNTIME.project
BACKEND = RUNTIME.resources / 'backend' if RUNTIME.packaged else PROJECT / 'backend'
DATA_DIR = RUNTIME.paths['DATA_DIR']
MODELS_DIR = RUNTIME.paths['PODCUT_MODELS_DIR']
CACHE_DIR = RUNTIME.paths['PODCUT_CACHE_DIR']
LOG_DIR = RUNTIME.paths['PODCUT_LOG_DIR']
TEMP_DIR = RUNTIME.paths['TEMP']
RUNTIME_DIR = RUNTIME.paths['PODCUT_RUNTIME_DIR']
LOCAL_ORIGINS = frozenset(f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (3300, 8300))


def binary(name: str) -> str | None:
    """Use only validated DEV-local tools; never search the inherited PATH."""
    if name not in {'ffmpeg', 'ffprobe', 'node'}:
        return None
    if RUNTIME.packaged:
        relative = Path('node/node.exe') if name == 'node' else Path('tools') / f'{name}.exe'
        candidate = checked_path(RUNTIME.package / relative, RUNTIME.package, 'Bundled tool')
        configured = RUNTIME.settings.get(name.upper() + '_PATH', '')
        if configured and Path(configured) != candidate:
            raise IsolationError('A packaged runtime cannot redirect its bundled tools.')
        return str(candidate) if candidate.is_file() else None
    roots = (PROJECT / '.tools', RUNTIME.root / 'tools')
    configured = RUNTIME.settings.get(name.upper() + '_PATH', '')
    if configured:
        candidate = Path(configured)
        root = next((root for root in roots if candidate.is_relative_to(root)), None)
        if root is None:
            raise IsolationError('Configured media tool is outside the approved tool roots.')
        candidate = checked_path(candidate, root, 'Configured tool')
        return str(candidate) if candidate.is_file() else None
    for root in roots:
        checked_path(root, root, 'Tool root')
        candidates = [root / name / f'{name}.exe', root / f'{name}.exe', root / 'ffmpeg' / 'bin' / f'{name}.exe']
        package_root = checked_path(root / 'ffmpeg', root, 'FFmpeg tool directory')
        if package_root.is_dir():
            for variant in sorted(package_root.iterdir()):
                # Validate each directory before descending; glob may follow junctions.
                checked_path(variant, root, 'FFmpeg package')
                if variant.is_dir():
                    candidates.append(variant / 'bin' / f'{name}.exe')
        for candidate in candidates:
            candidate = checked_path(candidate, root, 'Local tool')
            if candidate.is_file():
                return str(candidate)
    return None


def child_environment() -> dict[str, str]:
    return RUNTIME.environment()


def worker_command(kind: str, request: Path) -> list[str]:
    """Frozen executables dispatch explicit worker modes; they are not Python."""
    if kind != 'ingestion':
        raise IsolationError('Unknown private worker type.')
    request = RUNTIME.validate_writable(request, 'Private worker request')
    executable = RUNTIME.validate_project(sys.executable, 'Worker executable')
    if RUNTIME.packaged:
        if executable != RUNTIME.package / 'backend' / 'PODCUTBackend.exe':
            raise IsolationError('The worker executable does not belong to this package.')
        return [str(executable), '--worker', kind, str(request)]
    return [str(executable), '-m', 'app.ingestion_worker', str(request)]


def validate_model_manifest(manifest: dict) -> None:
    """Path-only checks shared by optional isolated model subprocesses."""
    RUNTIME.validate_writable(manifest['source'], 'Model source')
    if manifest.get('cache_dir'):
        RUNTIME.validate_writable(manifest['cache_dir'], 'Model feature cache')
    for name in ('weights', 'syncnet_weights'):
        if manifest.get(name):
            checked_path(manifest[name], MODELS_DIR, 'Model checkpoint')
    ffmpeg = binary('ffmpeg')
    if not ffmpeg or Path(manifest['ffmpeg']) != Path(ffmpeg):
        raise IsolationError('The model subprocess must use the isolated FFmpeg tool.')


MAX_UPLOAD_MB = max(1, int(os.getenv('MAX_UPLOAD_MB', '8192')))
STORAGE_LIMIT_GB = max(0.05, float(os.getenv('STORAGE_LIMIT_GB', '30')))
MAX_DURATION = float(os.getenv('MAX_DURATION_HOURS', '12')) * 3600
WHISPER_MODEL = os.getenv('WHISPER_MODEL', 'small')
WHISPER_DEVICE = os.getenv('WHISPER_DEVICE', 'cpu')
WHISPER_COMPUTE_TYPE = os.getenv('WHISPER_COMPUTE_TYPE', 'int8')
OPENAI_MODEL = 'gpt-4o-mini'


def whisper_model_source() -> str:
    """Prefer an independently provisioned local model, with no download/link."""
    directory = checked_path(MODELS_DIR / 'whisper' / WHISPER_MODEL, MODELS_DIR, 'Local Whisper model')
    if RUNTIME.packaged:
        # The frozen distribution supports the pinned manifest model only. No
        # alias/download fallback may silently activate unverified weights.
        if WHISPER_MODEL != 'small':
            raise IsolationError('Paket ini memerlukan model Whisper small yang telah diverifikasi melalui setup.')
        from .model_provisioning import require_verified_model
        return str(require_verified_model(directory))
    if directory.exists():
        if not directory.is_dir():
            raise IsolationError('The local Whisper model is not a directory.')
        for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'):
            path = checked_path(directory / name, MODELS_DIR, 'Whisper model asset')
            if not path.is_file() or path.stat().st_size == 0:
                raise IsolationError('The independently provisioned Whisper model is incomplete.')
        return str(directory)
    # Backward-compatible local HF cache lookup, still local_files_only=True.
    return WHISPER_MODEL


@dataclass(frozen=True)
class AIConfiguration:
    provider: str
    model: str
    api_key: str = field(default='', repr=False)

    def public(self):
        return {'provider': self.provider, 'model': self.model, 'configured': bool(self.api_key),
                'status': 'not_tested' if self.api_key else 'missing_key'}


def ai_configuration() -> AIConfiguration:
    """Source lab stays local; frozen users may select their own private API key."""
    if not RUNTIME.packaged:
        return AIConfiguration('heuristic', '')
    provider = RUNTIME.settings.get('AI_PROVIDER', 'heuristic').strip().lower()
    if provider not in {'gemini', 'openai'}:
        return AIConfiguration('heuristic', '')
    prefix = provider.upper()
    key = RUNTIME.settings.get(prefix + '_API_KEY', '').strip()
    default_model = 'gemini-3.5-flash-lite' if provider == 'gemini' else OPENAI_MODEL
    model = RUNTIME.settings.get(prefix + '_MODEL', default_model).strip() or default_model
    if len(model) > 160 or any(character in model for character in '\r\n\0'):
        raise IsolationError('Nama model AI pada konfigurasi privat tidak valid.')
    if len(key) > 16384 or any(character in key for character in '\r\n\0'):
        raise IsolationError('API key pada konfigurasi privat tidak valid.')
    return AIConfiguration(provider, model, key)
