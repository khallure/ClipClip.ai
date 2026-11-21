# Build with scoped DEV .venv PyInstaller; all caches/work/dist paths are supplied
# by the parent build script under the two approved DEV roots.
from pathlib import Path
import importlib.util
import os
import uuid
from importlib.metadata import PackageNotFoundError

from PyInstaller.utils.hooks import collect_all, copy_metadata

project = Path(SPECPATH).resolve().parents[1]
backend = project / 'backend'
assets = backend / 'assets'
datas = [(str(assets), 'backend/assets')]
binaries = []
hiddenimports = ['app.main', 'app.ingestion_worker', 'app.desktop_jobhost',
                 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
                 'uvicorn.lifespan.on']
quality_build = os.environ.get('PODCUT_QUALITY_BUILD_ID')
if quality_build:
    if str(uuid.UUID(quality_build)) != quality_build:
        raise RuntimeError('Expected a canonical Quality Upgrade build UUID.')
    hiddenimports.append('app.quality_dev_profile')

# Native packages and their bundled assets/metadata must survive relocation.
# Absence is a build error for the Phase 2B CPU/M1 package, not a fake readiness.
for package, distribution in (
    ('cv2', 'opencv-python'), ('scenedetect', 'scenedetect'),
    ('faster_whisper', 'faster-whisper'), ('ctranslate2', 'ctranslate2'),
    ('av', 'av'), ('onnxruntime', 'onnxruntime'), ('tokenizers', 'tokenizers'),
    ('yt_dlp', 'yt-dlp'),
):
    if importlib.util.find_spec(package) is None:
        raise RuntimeError('Required Phase 2B package is missing: ' + package)
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
    datas += copy_metadata(distribution)

for distribution in ('numpy', 'huggingface-hub', 'python-dotenv', 'fastapi',
                     'starlette', 'pydantic', 'uvicorn', 'python-multipart', 'httpx',
                     'openai', 'certifi', 'tqdm', 'requests', 'urllib3', 'websockets',
                     'brotli', 'pycryptodomex'):
    try:
        datas += copy_metadata(distribution)
    except PackageNotFoundError:
        # Some optional yt-dlp transports are not installed. They are not enabled
        # by the source adapter, which uses its HTTPS-only urllib director.
        pass

analysis = Analysis(
    [str(Path(SPECPATH) / 'backend_entry.py')],
    pathex=[str(backend)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'torchaudio', 'tensorflow', 'app.lab_profile',
              'app.asd_runtime.runner', 'app.asd_runtime.syncnet_runner'] +
             ([] if quality_build else ['app.quality_dev_profile']),
    noarchive=False,
)
analysis.datas = [item for item in analysis.datas if '__pycache__' not in Path(item[0]).parts]
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='PODCUTBackend',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, disable_windowed_traceback=False, contents_directory='_internal')
coll = COLLECT(exe, analysis.binaries, analysis.datas,
               strip=False, upx=False, name='PODCUTBackend')
