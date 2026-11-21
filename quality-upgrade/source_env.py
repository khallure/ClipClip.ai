"""Source regression runner scoped to this Quality workspace."""
from pathlib import Path
import hashlib
import importlib
import os
import sys

QUALITY = Path(__file__).resolve().parent
PROJECT = QUALITY.parent
PROFILE = QUALITY/'tests/profile'
TEMP = PROFILE/'temp'
for path in (PROFILE/'config', TEMP, PROFILE/'home'):
    if path.resolve() != path or not path.is_relative_to(PROJECT):
        raise RuntimeError('Unsafe Quality test path')
    path.mkdir(exist_ok=True, parents=True)
config = PROFILE/'config/backend.env'
if not config.exists():
    config.write_text('AI_PROVIDER=heuristic\nWHISPER_DEVICE=cpu\nWHISPER_COMPUTE_TYPE=int8\n'
                      'WHISPER_MODEL=small\nYOUTUBE_INGEST_ENABLED=false\n',encoding='utf-8')

for key in tuple(os.environ):
    if key.upper().startswith(('PODCUT_', 'OPENAI_', 'GEMINI_', 'GOOGLE_', 'HF_', 'HUGGINGFACE_', 'TORCH_', 'PYTEST_', 'PYTHONPATH')):
        del os.environ[key]
    elif key.upper() in {'DATA_DIR','MODELS_DIR','CACHE_DIR','LOG_DIR','TEMP_DIR','RUNTIME_DIR','AI_PROVIDER',
                          'FFMPEG_PATH','FFPROBE_PATH','NODE_PATH'}:
        del os.environ[key]
os.environ.update(TEMP=str(TEMP),TMP=str(TEMP),USERPROFILE=str(PROFILE/'home'),LOCALAPPDATA=str(PROFILE/'home'),
                  APPDATA=str(PROFILE/'home'), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
sys.path.insert(0,str(PROJECT/'backend'))
lab = importlib.import_module('app.lab_profile')
lab.APPROVED_PROFILE = PROFILE

face_name = 'face_detection_yunet_2023mar.onnx'
face_asset = PROJECT/'backend/assets/models'/face_name
face_model = PROFILE/'models/reframe'/face_name
if not face_model.exists():
    content = face_asset.read_bytes()
    if hashlib.sha256(content).hexdigest() != '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4':
        raise RuntimeError('Bundled YuNet asset checksum mismatch')
    if face_model.resolve() != face_model:
        raise RuntimeError('Unsafe Quality model path')
    face_model.parent.mkdir(parents=True, exist_ok=True)
    with face_model.open('xb') as target:
        target.write(content)

def run_tests(args):
    import pytest
    output = PROFILE/'temp'/('pytest-'+os.urandom(4).hex())
    return pytest.main(['-p','no:cacheprovider','--basetemp',str(output),*args])

if __name__ == '__main__':
    raise SystemExit(run_tests(sys.argv[1:]))
